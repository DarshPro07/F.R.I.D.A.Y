"""Keep this process's machine awake for the duration of an 8-hour soak.

`SetThreadExecutionState` is the documented, non-elevated way to say "do
not sleep while I am working" - the same call a video player makes. It
binds to THIS thread, so it lapses the moment the run ends; nothing to
restore, and no power-plan edit that outlives the soak.

ES_AWAYMODE_REQUIRED is deliberately NOT set: away mode is for media
playback and is refused on some systems. ES_SYSTEM_REQUIRED plus
ES_CONTINUOUS is what a long compute job wants. The display is allowed to
turn off.

Run the soak as a child of this script so the state is held for exactly
as long as the soak runs:

    python scripts/keep_awake.py -- .venv-verify/Scripts/python.exe scripts/soak.py --hours 8 ...
"""
from __future__ import annotations

import ctypes
import subprocess
import sys

ES_CONTINUOUS = 0x80000000
ES_SYSTEM_REQUIRED = 0x00000001


def hold() -> bool:
    """Ask Windows not to sleep. False when the call is refused, which the
    caller must report rather than assume - an 8-hour claim over a machine
    that slept is the failure this exists to prevent."""
    if not sys.platform.startswith("win"):
        return False
    prev = ctypes.windll.kernel32.SetThreadExecutionState(
        ES_CONTINUOUS | ES_SYSTEM_REQUIRED)
    return prev != 0


def release() -> None:
    if sys.platform.startswith("win"):
        ctypes.windll.kernel32.SetThreadExecutionState(ES_CONTINUOUS)


def main(argv: list[str]) -> int:
    if "--" not in argv:
        print(__doc__)
        return 2
    command = argv[argv.index("--") + 1:]
    if not command:
        print("nothing to run after --")
        return 2
    held = hold()
    print(f"keep_awake: system sleep suppressed = {held}", flush=True)
    if not held:
        print("keep_awake: WARNING - the request was refused; a long run may be "
              "interrupted by sleep. The soak's own sample-gap detector will "
              "mark that INCONCLUSIVE rather than PASS.", flush=True)
    try:
        return subprocess.call(command)
    finally:
        release()
        print("keep_awake: released", flush=True)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
