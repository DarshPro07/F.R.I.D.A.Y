"""
Where the owner's Desktop, Documents and Downloads really are.

`Path.home() / "Desktop"` is a guess. On Windows the shell decides where
those folders live, and OneDrive's "Known Folder Move" relocates Desktop
and Documents under `%USERPROFILE%\\OneDrive\\...` while the bare folder
under the profile may or may not still exist. A jail root or a target path
built from the guess is then wrong in the one case that matters - the
owner says "on my Desktop" and Friday writes (or refuses) somewhere else.

`SHGetKnownFolderPath` is the authoritative answer on Windows; elsewhere,
and if the shell call fails, the profile folder is the fallback. Nothing
here touches the filesystem beyond that one query.
"""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path

#: KNOWNFOLDERID values (shell32 KnownFolders.h).
_FOLDER_IDS = {
    "desktop": "{B4BFCC3A-DB2C-424C-B029-7FE99A87C641}",
    "documents": "{FDD39AD0-238F-46AF-ADB4-6C85480369C7}",
    "downloads": "{374DE290-123F-4565-9164-39C4925E467B}",
}

#: How the owner names them in speech -> canonical folder key.
_SPOKEN = {
    "desktop": "desktop",
    "documents": "documents", "my documents": "documents", "docs": "documents",
    "downloads": "downloads", "download folder": "downloads", "downloads folder": "downloads",
}

_PLACE_RE = re.compile(
    r"\b(?:on|in|into|to|under|from|inside)\s+(?:my\s+|the\s+)?"
    r"(desktop|my documents|documents|docs|downloads folder|download folder|downloads)\b(?:\s+folder)?",
    re.I)

_cache: dict[str, Path] = {}


def _shell_known_folder(key: str) -> Path | None:
    """The Windows shell's path for `key`, or None off Windows / on failure."""
    if sys.platform != "win32":
        return None
    try:
        import ctypes
        from ctypes import wintypes

        class GUID(ctypes.Structure):
            _fields_ = [("Data1", wintypes.DWORD), ("Data2", wintypes.WORD),
                        ("Data3", wintypes.WORD), ("Data4", ctypes.c_ubyte * 8)]

        text = _FOLDER_IDS[key].strip("{}")
        parts = text.split("-")
        guid = GUID()
        guid.Data1 = int(parts[0], 16)
        guid.Data2 = int(parts[1], 16)
        guid.Data3 = int(parts[2], 16)
        tail = bytes.fromhex(parts[3] + parts[4])
        guid.Data4 = (ctypes.c_ubyte * 8)(*tail)

        out = ctypes.c_wchar_p()
        shell32 = ctypes.windll.shell32
        ole32 = ctypes.windll.ole32
        result = shell32.SHGetKnownFolderPath(ctypes.byref(guid), 0, None, ctypes.byref(out))
        if result != 0 or not out.value:
            return None
        try:
            return Path(out.value)
        finally:
            ole32.CoTaskMemFree(out)
    except Exception:  # noqa: BLE001 - any failure means "use the fallback"
        return None


def known_folder(key: str) -> Path:
    """The real folder for 'desktop' / 'documents' / 'downloads'.

    Shell answer first, `~/<Name>` otherwise. Cached per process; an
    `ADA_KNOWN_FOLDER_<KEY>` variable overrides both (tests, odd hosts).
    """
    key = key.lower()
    if key not in _FOLDER_IDS:
        raise KeyError(key)
    override = os.getenv(f"ADA_KNOWN_FOLDER_{key.upper()}", "").strip()
    if override:
        return Path(override).expanduser()
    if key not in _cache:
        found = _shell_known_folder(key)
        _cache[key] = found if found is not None else Path.home() / key.capitalize()
    return _cache[key]


def place_in_request(text: str) -> Path | None:
    """The folder a request names as WHERE - "on my Desktop", "in Downloads"
    - or None when it names no place. Only the three the owner uses by
    name; any other location is a path he would have spoken in full."""
    m = _PLACE_RE.search(text or "")
    if not m:
        return None
    key = _SPOKEN.get(m.group(1).lower())
    return known_folder(key) if key else None


def reset_cache() -> None:
    """Forget resolved folders (tests that change the override env)."""
    _cache.clear()
