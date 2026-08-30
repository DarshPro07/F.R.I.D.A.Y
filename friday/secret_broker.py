"""Secret broker - credentials exist; models see aliases (Phase 4).

The contract, from the north star (boss-decided):

- Zero-credential start. The model NEVER receives a raw secret: not in
  prompts, not in logs, not in memory, not in Hermes context, not in
  Paperclip prompts.
- Entry: the USER types the secret into a scratch file Friday opens for
  them (interim surface until the UI panel exists). A deterministic
  non-model mover encrypts/places it and shreds the scratch.
- Thereafter Friday knows only `alias` + capability metadata:
  provider, connection status, model availability. Never the value.

Storage: DPAPI would bind to the Windows user; for portability and
auditability this uses Fernet (cryptography package) with a key held in
the user's profile directory outside every model-readable workspace
jail. The threat model is "keep secrets out of MODEL context and logs",
not "defend against a root attacker on the user's own machine" - stated
honestly rather than implied otherwise.
"""

from __future__ import annotations

import base64
import json
import os
import re
import time
from pathlib import Path

from friday.browser_capability import redact_secrets



#: Lives OUTSIDE the workspace jail (user profile, not repo).
_DEFAULT_HOME = Path(os.environ.get("FRIDAY_SECRET_HOME",
                                    str(Path.home() / ".friday-secrets")))

_ALIAS_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")


def _safe_alias(alias: str) -> str:
    """Aliases become filenames: constrain the charset so no alias can
    escape the vault directory (adversarial finding: '../../evil')."""
    if not _ALIAS_RE.match(alias or ""):
        raise ValueError(
            f"invalid alias {alias!r}: letters, digits, dot, dash, "
            "underscore only (max 64)")
    return alias


class SecretBroker:
    """Alias -> encrypted secret, with model-facing metadata only."""

    def __init__(self, home: Path | str | None = None) -> None:
        self.home = Path(home or _DEFAULT_HOME)
        self.home.mkdir(parents=True, exist_ok=True)
        self._key_path = self.home / "broker.key"
        self._store_path = self.home / "secrets.enc.json"
        self._fernet = self._load_fernet()

    # -- crypto ------------------------------------------------------------

    def _load_fernet(self):
        from cryptography.fernet import Fernet
        if not self._key_path.exists():
            self._key_path.write_bytes(Fernet.generate_key())
            try:
                os.chmod(self._key_path, 0o600)
            except OSError:
                pass                       # Windows: ACLs, not chmod bits
        return Fernet(self._key_path.read_bytes())

    def _read_store(self) -> dict:
        if not self._store_path.exists():
            return {}
        return json.loads(self._store_path.read_text(encoding="utf-8"))

    def _write_store(self, store: dict) -> None:
        self._store_path.write_text(json.dumps(store, indent=1),
                                    encoding="utf-8")

    # -- the user-entry flow ----------------------------------------------

    def scratch_file(self, alias: str) -> Path:
        """A blank scratch file the USER types the secret into. Never a
        live .env, never inside the repo workspace."""
        path = self.home / f"enter-{_safe_alias(alias)}.txt"
        path.write_text("", encoding="utf-8")
        return path

    def ingest_scratch(self, alias: str, *, provider: str = "",
                       purpose: str = "") -> dict:
        """
        Deterministic mover: reads the scratch the user filled, encrypts,
        stores, SHREDS the scratch (overwrite then unlink). Returns
        metadata only - the value never appears in the return, and
        therefore never in a tool result or model context.
        """
        path = self.home / f"enter-{_safe_alias(alias)}.txt"
        raw = path.read_text(encoding="utf-8").strip()
        if not raw:
            return {"status": "empty", "alias": alias,
                    "note": "scratch file was empty; nothing stored"}
        store = self._read_store()
        store[alias] = {
            "value": base64.b64encode(
                self._fernet.encrypt(raw.encode())).decode(),
            "provider": provider,
            "purpose": purpose,
            "created_at": time.time(),
        }
        self._write_store(store)
        # shred: overwrite with zeros of the same length, then remove
        path.write_text("\0" * len(raw), encoding="utf-8")
        path.unlink()
        return {"status": "stored", "alias": alias, "provider": provider,
                "length": len(raw), "note": "value encrypted; scratch "
                "shredded; models see this alias only"}

    # -- model-facing surface (metadata only) ------------------------------

    def list_aliases(self) -> list[dict]:
        store = self._read_store()
        return [{"alias": alias,
                 "provider": meta.get("provider", ""),
                 "purpose": meta.get("purpose", ""),
                 "created_at": meta.get("created_at", 0)}
                for alias, meta in store.items()]

    def connection_metadata(self, alias: str) -> dict:
        """What a provider-connection flow may show: capability facts,
        no value."""
        store = self._read_store()
        if alias not in store:
            return {"alias": alias, "status": "absent"}
        meta = store[alias]
        return {"alias": alias, "status": "connected",
                "provider": meta.get("provider", ""),
                "purpose": meta.get("purpose", "")}

    # -- machine-facing surface (never returned to models) -----------------

    def resolve_for_process(self, alias: str) -> str:
        """
        The ONLY decrypt path. For deterministic consumers (writing a
        child process env, an SDK constructor) - callers must never put
        the return value into a tool result, log, or prompt. The name
        says the contract.
        """
        store = self._read_store()
        if alias not in store:
            raise KeyError(f"no secret under alias {alias!r}")
        token = base64.b64decode(store[alias]["value"])
        return self._fernet.decrypt(token).decode()

    def inject_env(self, alias: str, env_var: str,
                   env: dict | None = None) -> dict:
        """Place the decrypted value into a process-env mapping (for a
        child process spawn). Returns metadata, never the value."""
        target = env if env is not None else os.environ
        target[env_var] = self.resolve_for_process(alias)
        return {"status": "injected", "alias": alias, "env_var": env_var}

    def remove(self, alias: str) -> dict:
        store = self._read_store()
        if store.pop(alias, None) is None:
            return {"status": "absent", "alias": alias}
        self._write_store(store)
        return {"status": "removed", "alias": alias}


def clipboard_is_dead(text: str) -> bool:
    """The clipboard rule: key-shaped clipboard content is DEAD on
    arrival - detected here, refused for ingestion into memory or
    context. (redact_secrets provides the shape table.)"""
    _clean, count = redact_secrets(text or "")
    return count > 0
