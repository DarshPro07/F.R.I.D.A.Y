"""
PyInstaller entry point for FRIDAY.exe.

Kept to three lines of behaviour on purpose: `multiprocessing.freeze_support()`
must run before anything else so LiveKit's job processes (started with the
spawn context, i.e. this executable re-run with `--multiprocessing-fork`)
work in a frozen build; everything else lives in `friday.launcher`, which is
importable and tested from source.
"""
import multiprocessing
import sys

if __name__ == "__main__":
    multiprocessing.freeze_support()

    from friday.launcher import hold_if_the_window_would_close, main

    code = main()
    hold_if_the_window_would_close(code)
    sys.exit(code)
