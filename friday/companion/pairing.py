"""
Which extension, exactly - and the secret it pairs with.

"Origin starts with chrome-extension://" was not good enough, and the reason
is worth stating plainly: **every** extension has such an origin. A second
extension the user installs for something unrelated - or one that is
compromised later - would satisfy that check and be handed a channel into
their logged-in browser.

So the bridge pins one id.

## Where the id comes from

Chrome derives an extension's id from its public key, deterministically:

    id = sha256(DER public key)[:16], each nibble mapped 0-f -> a-p

Without a `key` in the manifest, an unpacked extension is given an id derived
from its *path*, which changes if the folder moves and differs on every
machine - useless for an allow-list. With `key` pinned in the manifest the id
is stable everywhere, which is exactly the case Chrome documents it for.

So: generate a keypair once, embed the public half in the manifest, and let
the bridge accept that one origin.

## The token

Random, 256 bits, compared in constant time, sent as the first message rather
than in the URL because query strings end up in logs and history. It can be
rotated, which matters because the only recovery from a leaked token is a new
one.

On Windows the file is locked to the current user with `icacls`. Not a strong
boundary against a process already running as him - nothing at this layer is -
but it stops the casual case of another account on the machine reading it.
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
import subprocess
from pathlib import Path

from friday.config import DATA_DIR

logger = logging.getLogger("friday-agent.companion.pairing")

EXTENSION_DIR = Path(__file__).parent / "extension"
MANIFEST = EXTENSION_DIR / "manifest.json"

#: The private half. Never shipped, never needed at runtime - only to mint the
#: manifest key once.
KEY_PATH = Path(os.getenv("ADA_COMPANION_KEY")
                or DATA_DIR / "companion" / "extension_key.pem").resolve()

#: The id the bridge will accept, cached so the bridge does not have to parse
#: the manifest on every connection.
ID_PATH = Path(os.getenv("ADA_COMPANION_ID")
               or DATA_DIR / "companion" / "extension_id.txt").resolve()


def extension_id_from_key(public_der: bytes) -> str:
    """
    Chrome's own derivation, reimplemented.

    sha256 of the DER public key; the first 16 bytes; each nibble as a letter
    a-p. Reimplemented rather than guessed - the test pins it against a known
    vector so a subtle mistake cannot quietly produce an id nothing matches.
    """
    digest = hashlib.sha256(public_der).digest()[:16]
    return "".join(chr(ord("a") + (byte >> 4)) + chr(ord("a") + (byte & 0xF))
                   for byte in digest)


def generate_key() -> bytes:
    """A 2048-bit RSA key, written once and reused."""
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

    if KEY_PATH.is_file():
        return KEY_PATH.read_bytes()

    private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = private.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption())
    KEY_PATH.parent.mkdir(parents=True, exist_ok=True)
    KEY_PATH.write_bytes(pem)
    lock_down(KEY_PATH)
    logger.info("companion: extension key written to %s", KEY_PATH)
    return pem


def public_der(private_pem: bytes) -> bytes:
    from cryptography.hazmat.primitives import serialization

    private = serialization.load_pem_private_key(private_pem, password=None)
    return private.public_key().public_bytes(
        encoding=serialization.Encoding.DER,
        format=serialization.PublicFormat.SubjectPublicKeyInfo)


def lock_down(path: Path) -> bool:
    """
    Readable by this user only.

    Not a strong boundary against something already running as him - nothing
    at this layer is - but it stops another account on the machine reading it.
    """
    if os.name != "nt":
        try:
            path.chmod(0o600)
            return True
        except OSError:
            return False
    user = os.environ.get("USERNAME", "")
    if not user:
        return False
    try:
        subprocess.run(
            ["icacls", str(path), "/inheritance:r", "/grant:r", f"{user}:F"],
            capture_output=True, timeout=30, check=False)
        return True
    except Exception:
        return False


def provision() -> dict:
    """
    Mint the key, pin it into the manifest, and record the id.

    Idempotent: run it as often as you like, it settles on one identity.
    """
    pem = generate_key()
    der = public_der(pem)
    key_b64 = base64.b64encode(der).decode("ascii")
    extension_id = extension_id_from_key(der)

    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    changed = manifest.get("key") != key_b64
    manifest["key"] = key_b64
    if changed:
        MANIFEST.write_text(json.dumps(manifest, indent=2) + "\n",
                            encoding="utf-8")

    ID_PATH.parent.mkdir(parents=True, exist_ok=True)
    ID_PATH.write_text(extension_id, encoding="utf-8")

    return {"extension_id": extension_id,
            "origin": f"chrome-extension://{extension_id}",
            "manifest_updated": changed,
            "key_file": str(KEY_PATH),
            "id_file": str(ID_PATH)}


def allowed_origin() -> str:
    """
    The one origin the bridge accepts, or "" when nothing is pinned yet.

    Empty means the companion has not been provisioned. The bridge treats that
    as "refuse everything" rather than "allow anything" - an unconfigured
    security check must fail closed.
    """
    if ID_PATH.is_file():
        extension_id = ID_PATH.read_text(encoding="utf-8").strip()
        if extension_id:
            return f"chrome-extension://{extension_id}"
    try:
        manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return ""
    key = manifest.get("key")
    if not key:
        return ""
    try:
        return f"chrome-extension://{extension_id_from_key(base64.b64decode(key))}"
    except Exception:
        return ""
