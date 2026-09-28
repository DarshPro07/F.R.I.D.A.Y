# Pitfalls seen while packaging FRIDAY.exe

Each entry is a lesson, not a log: the shape of the failure, why it
happens, what to do instead.

## An inconsistent bundle from editing mid-build

PyInstaller reads the entry script and compiles modules at different
moments of a build that runs for several minutes. A source edit in that
window can freeze a newer entry script against an older module (an
`ImportError: cannot import name ...` on every start). Rule: no edits to
bundled files between `[build] running PyInstaller` and
`[build] PyInstaller exit`; new files are safe, existing ones are not.

## `uv sync` removes PyInstaller from the build venv

`uv sync` makes the environment match the lock exactly; PyInstaller is a
build tool, not a project dependency, so every re-sync removes it. The
build preflight names it; reinstall with
`uv pip install --python .venv-build\Scripts\python.exe pyinstaller`.

## A file cannot be moved across drives with `Path.replace`

`os.replace` fails with WinError 17 when the source and target are on
different drives; the bundle typically lives on a data drive and the
profile under `C:\Users`. Use `shutil.move` (copy and delete) for any
migration step.

## An undeclared dependency only fails at call time

`friday/toolsets/web.py` imported `defusedxml` at call time; no venv had
it and the lock did not list it, so the RSS path would have raised
`ModuleNotFoundError` from source and from the exe. Declare every
runtime import in `pyproject.toml` and lock it, then re-sync.

## The console hold must be double-click-only

A `Press Enter to close` hold on every non-zero exit also fires when the
command was typed in a terminal. Hold only when the process is the sole
owner of its console (`GetConsoleProcessList` returns 1).

## The validator and the real profile

`scripts/validate_exe.py` sets `LOCALAPPDATA` to `build\validate\profile`
for every process it launches and clears `FRIDAY_HOME`, so it can test
the default path (including legacy migration) without touching the
owner's state. Running the exe by hand uses the real profile.
