# Windows FRIDAY.exe — plan and status

Owner request (2026-09-14): "Create a exe for this so that I could run this code."

## Reality before this work

- The `Friday.exe` the docs mention (`Friday.exe`, `Friday.exe --stop`) was a
  13 KB native stub, tracked as a binary and untracked in `d1751bb` (audit
  A-005). It still needed the source tree and `.venv`; its source was never
  in the repository.
- The runtime is three processes started from `.venv`:
  `server.py` (MCP over SSE, :8000), `scripts/run_ui.py` (control room,
  :8770, opens Chrome), `agent_friday.py start` (LiveKit voice worker).
  `start.py` and `scripts/restart_friday.py` are the two launchers.
- Every module resolves its state from
  `Path(__file__).resolve().parent.parent` (`friday/config.py` explains why:
  detached processes must not depend on the working directory). `ADA_DB` and
  `FRIDAY_VAULT` are the only env overrides.
- Friday's own code re-invokes `sys.executable` as a Python interpreter
  (`-c`, `-m friday.objective_cli`, `scripts/jarvis_overlay.py`, and the
  Hermes gateway worker file, which Hermes's own Python reads from disk).

## Decision

PyInstaller **onedir** (`dist/FRIDAY/FRIDAY.exe` + `_internal/`), not onefile:

1. PyInstaller's own guidance: prove onedir first; onefile extracts to a
   temp dir on every start, which is the wrong place for a SQLite database,
   logs and a vault that must persist.
2. The `__file__`-anchored roots above land in `_internal/`, which is a real,
   writable, stable directory in onedir. In onefile they would land in a
   temp dir that is deleted at exit.
3. Nuitka was not chosen: no evidence it is needed, and a full compile of
   livekit + onnxruntime + av + transformers is hours of CPU on this host
   for no functional gain.

One console executable. A windowless variant is a follow-up, not v1: the
role children inherit the parent's console, and a console is how the
owner sees a failed start and stops it with Ctrl+C.

## Pieces

| Piece | File | Status |
|---|---|---|
| Launcher logic (roles, supervisor, stop, doctor, python shim) | `friday/launcher.py` | done |
| Frozen entry point | `friday_exe.py` | done |
| PyInstaller spec | `packaging/FRIDAY.spec` | done |
| Reproducible build command | `scripts/build_windows.py` | done |
| Build identity reads the bundled manifest when there is no git | `friday/build_identity.py` | done |
| Unit tests (run in `.venv-build`, never the live `.venv`) | `tests/test_launcher.py`, `tests/test_build_identity.py` | done |
| Golden journeys against the built exe | `scripts/validate_exe.py` | done |
| Docs | `docs/build/WINDOWS_EXE.md`, `docs/build/FRIDAY_EXE_VALIDATION.md` | done |

## Verification (evidence in docs/build/FRIDAY_EXE_VALIDATION.md)

Status 2026-09-15: **built, validated, state relocated**, one external blocker.

- State now lives under `%LOCALAPPDATA%\FRIDAY` (`friday/config.py`
  `STATE_ROOT`; `FRIDAY_HOME` and `PORTABLE_MODE` override), `.env` is
  read from `config\.env` there, and a build before this date migrates
  its `_internal` state on first start. Proven by EXE-12 (state root
  outside the bundle) and EXE-13 (legacy migration, shipped fixtures stay)
  in `scripts/validate_exe.py`, plus `tests/test_state_root.py` (21).
- Clean-machine run (`scripts/validate_exe_clean.py`): a copy of the
  bundle on C:, PATH without Python/uv/git/Hermes, empty profile, cwd
  `C:\` — 5/5 (version, paths, doctor, server + control room, stop).
- The 10 environmental test failures are gone: the orphaned `find.exe`
  (Git's, unsigned as all of Git's tools, 15 h CPU, 10.9 M handles) was
  inspected and ended; the same tests pass, and the full suite went
  3975 passed / 0 failed on the pre-relocation tree.

Status 2026-09-14: built and validated, one external blocker.

- `scripts/validate_exe.py --with-agent`: 13/13 journeys — EXE-01 version,
  EXE-02 database bootstrap, EXE-03 MCP server on :8000, EXE-05 control
  room on :8770, EXE-08 interpreter shim, EXE-10 loud failure, EXE-04
  persistence, EXE-07 voice worker boots inside the bundle (inference
  process, job runner, reaches LiveKit), EXE-09 stop leaves nothing behind.
- EXE-06 (worker registered with LiveKit): BLOCKED_EXTERNAL — the
  repository `.env` still carries the `.env.example` LiveKit placeholders
  (401). Same from source. Owner action: fill in real credentials in
  `dist\FRIDAY\.env`, rerun the validator.
- EXE-11 rebuild preserved `.env`, `_internal\data`, `_internal\logs`.
- Unit tests: 33 passed in `.venv-build`. Full deterministic suite in the
  same venv: 3965 passed, 73 skipped, 10 failed — the 10 fail identically at
  clean HEAD and come from psutil choking on a leftover `find.exe` holding
  10.46 M handles on this machine (details in the validation doc).
- Defects found and fixed on the way: undeclared `defusedxml` (RSS/YouTube
  tools would raise from source too), console hold firing in terminals,
  doctor counting defaulted settings as unset, shim crashing on an unknown
  bundled path, an inconsistent bundle from editing mid-build.
