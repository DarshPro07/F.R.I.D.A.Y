---
name: friday-windows-exe
description: Build, validate and ship the Windows FRIDAY.exe package (PyInstaller onedir) without breaking the owner's state or the live venv.
version: 1.0.0
author: friday
license: MIT
platforms: [windows]
metadata:
  hermes:
    tags: [build, packaging, pyinstaller, windows, release]
    related_skills: []
  friday:
    scope: repository
    task: build-friday-exe
    publish: private
    depends_on:
      - friday/launcher.py
      - friday/config.py
      - friday_exe.py
      - packaging/FRIDAY.spec
      - scripts/build_windows.py
      - scripts/validate_exe.py
    tests:
      - pytest tests/test_launcher.py tests/test_state_root.py
    triggers:
      - build the exe
      - rebuild friday.exe
      - validate the exe
      - package friday for windows
      - make a windows build
    never_for:
      - book a table
      - what is the weather
---

# Build and validate FRIDAY.exe

## When to use

When asked to build, rebuild, package, validate or ship the Windows
executable, or when a change touched the launcher, the config roots, the
spec or the build/validate scripts.

## Quick reference

- Build venv: `.venv-build` (never the live `.venv`; PyInstaller is not installed there on purpose).
- Build: `.venv-build\Scripts\python.exe scripts\build_windows.py` (adds `--clean` for a fresh cache).
- Validate the artifact: `.venv-build\Scripts\python.exe scripts\validate_exe.py --with-agent`.
- Artifact: `dist\FRIDAY\FRIDAY.exe`; state: `%LOCALAPPDATA%\FRIDAY` (never inside `_internal`).
- Docs: `docs/build/WINDOWS_EXE.md`, evidence: `docs/build/FRIDAY_EXE_VALIDATION.md`.

## Procedure

1. Confirm the worktree and venv: `git rev-parse --show-toplevel`, then
   `.venv-build\Scripts\python.exe -c "import friday; print(friday.__file__)"`
   must point at this checkout.
2. If `uv sync` ran since the last build, reinstall PyInstaller:
   `uv pip install --python .venv-build\Scripts\python.exe pyinstaller`
   (a re-sync makes the venv match the lock and removes it).
3. Run the launcher and state-root tests first:
   `.venv-build\Scripts\python.exe -m pytest tests\test_launcher.py tests\test_state_root.py -q`.
4. Build: `.venv-build\Scripts\python.exe scripts\build_windows.py`. Do not
   edit any bundled file (anything under `friday/`, `scripts/`, the spec,
   `friday_exe.py`) between `[build] running PyInstaller` and
   `[build] PyInstaller exit`.
5. Read the tail of the build log: `PyInstaller exit 0`, `SIGNING_STATUS`,
   the in-build `--doctor` verdict (DEGRADED is normal without signing;
   FAIL is not).
6. Validate the artifact: `scripts\validate_exe.py --with-agent`. Every
   journey must PASS; EXE-06 reads BLOCKED_EXTERNAL while the `.env`
   holds the `.env.example` LiveKit placeholders.
7. Record the run in `docs/build/FRIDAY_EXE_VALIDATION.md` with the
   commit, the journey table and any defect found.

## Pitfalls

- Editing a bundled module mid-build produces an inconsistent bundle
  (new entry script, old module): every journey fails at import. Rebuild
  on a stable tree. See references/pitfalls.md.
- `Path.replace` cannot move a file across drives on Windows; the bundle
  is on F: and the profile on C:. Use `shutil.move`.
- The validator redirects `LOCALAPPDATA` to `build\validate\profile`;
  running the exe by hand uses the real profile and migrates state there.
- A doctor `legacy state` warning means an older build's `_internal\data`,
  `logs` or `vault` still exists; the next start moves it.

## Verification

- `scripts\validate_exe.py --with-agent` prints `N/N journeys passed`.
- `FRIDAY.exe --paths` shows `state_root` under `%LOCALAPPDATA%\FRIDAY`.
- `FRIDAY.exe --status` after `--stop` reports no processes and free ports.
