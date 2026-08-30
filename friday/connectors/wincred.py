"""
Windows Credential Manager store — provider secrets never touch a file.

Per Microsoft's guidance for locally persisted credentials, values live in
Windows Credential Manager (CRED_TYPE_GENERIC, CRED_PERSIST_LOCAL_MACHINE)
via ctypes against advapi32 - no extra dependency, no plaintext .env, no
Fernet file for provider keys. What the rest of Friday sees is an opaque
reference `wincred:<target>`; resolving a ref to a value happens ONLY at
the execution boundary (the Hermes secret-source plugin or the gateway
process), never in model context.

Target naming: `hermes/<ENV_NAME>` - e.g. `hermes/FIREWORKS_API_KEY` -
so the Hermes Secret Source plugin can enumerate exactly the credentials
meant for it by prefix, and nothing else in the user's vault.
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes as wintypes

_ADVAPI32 = ctypes.windll.advapi32 if hasattr(ctypes, "windll") else None

CRED_TYPE_GENERIC = 1
CRED_PERSIST_LOCAL_MACHINE = 2
_ERROR_NOT_FOUND = 1168

PREFIX = "hermes/"


class _CREDENTIAL(ctypes.Structure):
    _fields_ = [
        ("Flags", wintypes.DWORD),
        ("Type", wintypes.DWORD),
        ("TargetName", wintypes.LPWSTR),
        ("Comment", wintypes.LPWSTR),
        ("LastWritten", wintypes.FILETIME),
        ("CredentialBlobSize", wintypes.DWORD),
        ("CredentialBlob", ctypes.POINTER(ctypes.c_byte)),
        ("Persist", wintypes.DWORD),
        ("AttributeCount", wintypes.DWORD),
        ("Attributes", ctypes.c_void_p),
        ("TargetAlias", wintypes.LPWSTR),
        ("UserName", wintypes.LPWSTR),
    ]


def available() -> bool:
    return _ADVAPI32 is not None


def write(name: str, value: str, *, comment: str = "Stored by Friday "
          "ConnectorControlPlane; models see an opaque ref only.") -> str:
    """Store `value` under `hermes/<name>`. Returns the opaque ref.

    The VALUE parameter deliberately never appears in any log or raised
    error text.
    """
    if not available():                                     # pragma: no cover
        raise OSError("Windows Credential Manager unavailable on this host")
    target = f"{PREFIX}{name}"
    blob = value.encode("utf-16-le")
    credential = _CREDENTIAL()
    credential.Flags = 0
    credential.Type = CRED_TYPE_GENERIC
    credential.TargetName = target
    credential.Comment = comment
    credential.CredentialBlobSize = len(blob)
    credential.CredentialBlob = ctypes.cast(
        ctypes.create_string_buffer(blob, len(blob)),
        ctypes.POINTER(ctypes.c_byte))
    credential.Persist = CRED_PERSIST_LOCAL_MACHINE
    credential.UserName = "friday"
    if not _ADVAPI32.CredWriteW(ctypes.byref(credential), 0):
        raise OSError(f"CredWriteW failed for {target!r} "
                      f"(winerror {ctypes.GetLastError()})")
    return f"wincred:{target}"


def read(name: str) -> str | None:
    """Resolve `hermes/<name>` to its value. EXECUTION BOUNDARY ONLY."""
    if not available():                                     # pragma: no cover
        return None
    target = f"{PREFIX}{name}"
    pointer = ctypes.POINTER(_CREDENTIAL)()
    if not _ADVAPI32.CredReadW(target, CRED_TYPE_GENERIC, 0,
                               ctypes.byref(pointer)):
        if ctypes.GetLastError() == _ERROR_NOT_FOUND:
            return None
        return None
    try:
        size = pointer.contents.CredentialBlobSize
        raw = ctypes.string_at(pointer.contents.CredentialBlob, size)
        return raw.decode("utf-16-le")
    finally:
        _ADVAPI32.CredFree(pointer)


def delete(name: str) -> bool:
    if not available():                                     # pragma: no cover
        return False
    return bool(_ADVAPI32.CredDeleteW(f"{PREFIX}{name}",
                                      CRED_TYPE_GENERIC, 0))


def exists(name: str) -> bool:
    """Presence without the value - the model-safe question."""
    if not available():                                     # pragma: no cover
        return False
    pointer = ctypes.POINTER(_CREDENTIAL)()
    if not _ADVAPI32.CredReadW(f"{PREFIX}{name}", CRED_TYPE_GENERIC, 0,
                               ctypes.byref(pointer)):
        return False
    _ADVAPI32.CredFree(pointer)
    return True
