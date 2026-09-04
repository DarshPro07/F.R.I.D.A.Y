"""
Putting the arrow on the actual screen.

`screen.screen_point` always writes an annotated PNG - that is the guaranteed
answer. This module is the better one: a short-lived, click-through, always-on-
top child process that draws the same arrow over the live desktop.

It is deliberately best-effort. Every failure path here returns False and the
caller still has its image. An overlay that cannot be drawn is a missing
flourish; an overlay that crashes the UI server is an outage.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "jarvis_overlay.py"

#: A GUI child must never flash a console window - the same lesson as the bun
#: subprocesses that used to pop up in front of the boss.
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

#: Only one arrow at a time. A second point replaces the first rather than
#: stacking two claims on the screen.
_CURRENT: subprocess.Popen | None = None

DEFAULT_SECONDS = float(os.getenv("JARVIS_OVERLAY_SECONDS", "4"))


def dismiss() -> None:
    """Take down the current arrow, if any. Never raises."""
    global _CURRENT
    proc, _CURRENT = _CURRENT, None
    if proc is None:
        return
    try:
        if proc.poll() is None:
            proc.terminate()
    except Exception:  # noqa: BLE001
        pass


def show(x: int, y: int, label: str = "", seconds: float | None = None) -> bool:
    """Draw an arrow whose tip is at screen pixel (x, y). True if it started.

    Returns False - never raises - when there is no display, no Tk, or the
    window cannot be made click-through.
    """
    global _CURRENT
    if not SCRIPT.exists():
        return False
    if sys.platform != "win32":
        # The click-through/transparent-colour path is Windows-specific today.
        return False

    dismiss()
    try:
        _CURRENT = subprocess.Popen(
            [sys.executable, str(SCRIPT),
             "--x", str(int(x)), "--y", str(int(y)),
             "--label", str(label or "")[:42],
             "--seconds", str(seconds if seconds is not None else DEFAULT_SECONDS)],
            cwd=str(ROOT),
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL,
            creationflags=_NO_WINDOW,
        )
    except Exception:  # noqa: BLE001
        _CURRENT = None
        return False
    return True
