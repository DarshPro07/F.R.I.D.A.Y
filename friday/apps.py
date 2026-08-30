"""
Application discovery and resolution.

Independent implementation. Mark-L (CC BY-NC) resolves apps from a hardcoded
alias table of ~50 names x 3 operating systems; that table is both a licence
problem and a correctness one - on this machine Chrome lives under
"Program Files (x86)", which a table of standard paths would miss.

So apps are *discovered*, in this order:

    1. Windows "App Paths" registry  - what the OS itself uses for `start x`
    2. PATH (shutil.which)
    3. Start Menu .lnk shortcuts
    4. A small alias table, only to map human words ("browser", "vs code")
       onto executable stems that the steps above then locate

Every resolution carries ``expected_processes``: the process names that should
appear once the app is running. That is what makes launch *verifiable* rather
than merely attempted - see friday/toolsets/system.py.
"""

from __future__ import annotations

import os
import shutil
import sys
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

IS_WINDOWS = sys.platform == "win32"

#: Human word -> candidate executable stems, in preference order. This maps
#: language onto identifiers; it does NOT record where anything is installed.
ALIASES: dict[str, tuple[str, ...]] = {
    "browser": ("chrome", "msedge", "firefox", "brave"),
    "chrome": ("chrome",),
    "google chrome": ("chrome",),
    "edge": ("msedge",),
    "firefox": ("firefox",),
    "brave": ("brave",),
    "spotify": ("spotify",),
    "vs code": ("code",),
    "vscode": ("code",),
    "visual studio code": ("code",),
    "code": ("code",),
    "terminal": ("wt", "powershell", "cmd"),
    "powershell": ("powershell", "pwsh"),
    "command prompt": ("cmd",),
    "cmd": ("cmd",),
    "file explorer": ("explorer",),
    "explorer": ("explorer",),
    "files": ("explorer",),
    "calculator": ("calc",),
    "calc": ("calc",),
    "notepad": ("notepad",),
    "paint": ("mspaint",),
    "task manager": ("taskmgr",),
    "word": ("winword",),
    "excel": ("excel",),
    "powerpoint": ("powerpnt",),
    "discord": ("discord",),
    "slack": ("slack",),
    "obs": ("obs64", "obs"),
    "steam": ("steam",),
    "blender": ("blender",),
    "notion": ("notion",),
    "obsidian": ("obsidian",),
}

#: Apps whose launcher exits and hands off to a differently-named process.
#: Verification must look for the real process, not the stub.
PROCESS_ALIASES: dict[str, tuple[str, ...]] = {
    "calc": ("CalculatorApp.exe", "Calculator.exe", "calc.exe"),
    "wt": ("WindowsTerminal.exe", "wt.exe"),
    "code": ("Code.exe",),
    "explorer": ("explorer.exe",),
}


@dataclass(frozen=True)
class AppTarget:
    """A resolved application, with everything needed to launch and verify it."""

    query: str
    stem: str
    command: str
    source: str
    expected_processes: tuple[str, ...] = field(default=())

    @property
    def display_name(self) -> str:
        return self.query.strip().title()


# --- discovery -------------------------------------------------------------


@lru_cache(maxsize=1)
def app_paths_registry() -> dict[str, str]:
    """
    Executable stem -> full path, from the Windows App Paths registry.

    This is the same table `Start > Run` consults, so it finds apps that are
    not on PATH and whose install location is non-standard.
    """
    if not IS_WINDOWS:
        return {}
    import winreg

    found: dict[str, str] = {}
    subkey = r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths"
    for root in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
        try:
            with winreg.OpenKey(root, subkey) as key:
                for i in range(winreg.QueryInfoKey(key)[0]):
                    try:
                        name = winreg.EnumKey(key, i)
                        with winreg.OpenKey(key, name) as entry:
                            path, _ = winreg.QueryValueEx(entry, "")
                        if path and Path(path).exists():
                            found.setdefault(Path(name).stem.lower(), path)
                    except OSError:
                        continue
        except OSError:
            continue
    return found


@lru_cache(maxsize=1)
def start_menu_shortcuts() -> dict[str, str]:
    """Shortcut display name (lowercased) -> .lnk path."""
    if not IS_WINDOWS:
        return {}
    roots = [
        Path(os.environ.get("ProgramData", "")) / "Microsoft/Windows/Start Menu/Programs",
        Path(os.environ.get("APPDATA", "")) / "Microsoft/Windows/Start Menu/Programs",
    ]
    found: dict[str, str] = {}
    for root in roots:
        if not root.is_dir():
            continue
        try:
            for lnk in root.rglob("*.lnk"):
                found.setdefault(lnk.stem.lower(), str(lnk))
        except OSError:
            continue
    return found


def refresh_discovery() -> None:
    """Drop discovery caches (after installing something, say)."""
    app_paths_registry.cache_clear()
    start_menu_shortcuts.cache_clear()


# --- resolution ------------------------------------------------------------


def _expected_processes(stem: str) -> tuple[str, ...]:
    if stem in PROCESS_ALIASES:
        return PROCESS_ALIASES[stem]
    return (f"{stem}.exe",) if IS_WINDOWS else (stem,)


def _resolve_stem(stem: str) -> AppTarget | None:
    registry = app_paths_registry()
    if stem in registry:
        return AppTarget(stem, stem, registry[stem], "app_paths", _expected_processes(stem))

    on_path = shutil.which(stem)
    if on_path:
        return AppTarget(stem, stem, on_path, "which", _expected_processes(stem))

    return None


def resolve(query: str) -> AppTarget | None:
    """
    Resolve a human app name to something launchable, or None.

    None means "I could not find it", which callers must report honestly
    rather than launching a guess.
    """
    raw = (query or "").strip()
    if not raw:
        return None
    key = raw.lower()

    # 1-3: alias -> candidate stems -> registry / PATH
    for stem in ALIASES.get(key, (key,)):
        target = _resolve_stem(stem)
        if target:
            return AppTarget(raw, target.stem, target.command, target.source,
                             target.expected_processes)

    # 4: Start Menu, exact then prefix
    shortcuts = start_menu_shortcuts()
    if key in shortcuts:
        return AppTarget(raw, key, shortcuts[key], "start_menu", _expected_processes(key))
    for name, path in sorted(shortcuts.items()):
        if name.startswith(key) or key in name:
            return AppTarget(raw, name, path, "start_menu", _expected_processes(name))

    return None


def known_apps() -> list[dict]:
    """Everything discoverable on this machine, for the apps.list_known tool."""
    entries = [
        {"name": stem, "path": path, "source": "app_paths"}
        for stem, path in sorted(app_paths_registry().items())
    ]
    entries += [
        {"name": name, "path": path, "source": "start_menu"}
        for name, path in sorted(start_menu_shortcuts().items())
    ]
    return entries
