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
import shutil
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


def _migrate_legacy_secrets(target: Path) -> list[str]:
    """Move a pre-workspace install's key and store into `target`.

    Returns what was moved, for the doctor. Idempotent and conservative: it
    refuses to overwrite anything already in `target`, because a half-moved
    key pair would leave secrets that decrypt with neither key.
    """
    legacy_key = _DEFAULT_HOME / "broker.key"
    legacy_store = _DEFAULT_HOME / "secrets.enc.json"
    if not legacy_key.exists() and not legacy_store.exists():
        return []
    if (target / "broker.key").exists() or (target / "secrets.enc.json").exists():
        return []
    target.mkdir(parents=True, exist_ok=True)
    moved = []
    for source in (legacy_key, legacy_store):
        if source.exists():
            # shutil.move, not Path.replace: the state root and the user
            # profile can be on different drives, and rename across drives
            # is WinError 17 (the same defect the launcher hit migrating
            # .env - see docs/product/EXE_AND_SKILL_SYSTEM_LEDGER.md).
            shutil.move(str(source), str(target / source.name))
            moved.append(source.name)
    return moved


class SecretBroker:
    """Alias -> encrypted secret, with model-facing metadata only.

    Per workspace (FR-PRV-002: "secrets are namespaced per workspace/user").
    Each workspace gets its own directory holding its own Fernet key and its
    own encrypted store, so reading another workspace's secrets needs that
    workspace's key file and not merely its name. This is the one place the
    PRD's word "cryptographically" is literally true of the separation.
    """

    def __init__(self, home: Path | str | None = None,
                 workspace_id: str | None = None) -> None:
        self.home = Path(home) if home else self.home_for(workspace_id)
        self.home.mkdir(parents=True, exist_ok=True)
        self._key_path = self.home / "broker.key"
        self._store_path = self.home / "secrets.enc.json"
        self._fernet = self._load_fernet()

    @staticmethod
    def home_for(workspace_id: str | None = None) -> Path:
        """`<base>/workspaces/<id>`, migrating a pre-boundary install once.

        An install that stored secrets before workspaces existed has them at
        `<base>/broker.key` and `<base>/secrets.enc.json`. Simply changing
        the default path would make those secrets invisible - the owner's
        stored credentials would appear to have vanished, which is a
        regression dressed as a security improvement. So the legacy pair is
        moved into the `default` workspace the first time that workspace is
        opened, and only when the destination is still empty.
        """
        from friday.workspace import DEFAULT_ID, resolve
        owner = resolve(workspace_id)
        target = _DEFAULT_HOME / "workspaces" / owner
        if owner == DEFAULT_ID:
            _migrate_legacy_secrets(target)
        return target

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
