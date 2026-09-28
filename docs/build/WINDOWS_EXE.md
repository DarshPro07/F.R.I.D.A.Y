# FRIDAY.exe — the Windows package

`dist\FRIDAY\FRIDAY.exe` starts Friday without Python, a virtual environment
or the source tree: the MCP server, the control room and the LiveKit voice
worker, in that order, the way `start.py` and `scripts\restart_friday.py`
do from a checkout.

## Run

```
dist\FRIDAY\FRIDAY.exe                 start everything and stay attached (Ctrl+C stops it)
dist\FRIDAY\FRIDAY.exe --no-browser    serve the control room without opening a window
dist\FRIDAY\FRIDAY.exe --no-agent      MCP server + control room only (no LiveKit credentials needed)
dist\FRIDAY\FRIDAY.exe --stop          stop every process running from this executable
dist\FRIDAY\FRIDAY.exe --status        ports 8000/8770 and the processes
dist\FRIDAY\FRIDAY.exe --doctor        diagnostics; exit 0 PASS, 1 DEGRADED, 2 FAIL
dist\FRIDAY\FRIDAY.exe --paths         every resolved location (state root, database, logs, .env) as JSON
dist\FRIDAY\FRIDAY.exe --version
```

First run: copy `dist\FRIDAY\.env.example` to
`%LOCALAPPDATA%\FRIDAY\config\.env` and fill it in (`FRIDAY.exe --paths`
prints the exact path). Credentials are looked up in this order:

1. the OS environment (always wins);
2. `%LOCALAPPDATA%\FRIDAY\config\.env` (or `FRIDAY_HOME\config\.env`);
3. `.env` beside the executable, **only** in portable mode;
4. the repository `.env`, **only** from a source checkout.

A packaged build never reads the repository, and a non-portable build never
reads a file lying beside the executable. Without LiveKit credentials the
MCP server and the control room still start; the voice worker cannot
register and `--doctor` says so by name. `--doctor` never prints a secret
value; it reports only whether a name is set (the same rule as
`friday.config`).

## Layout

```
dist\FRIDAY\                       the application - replaced wholesale by a rebuild
  FRIDAY.exe                       the launcher and every role (one binary, dispatched by --role)
  .env.example                     template
  SHA256SUMS.txt                   digests of the executable and the manifest
  _internal\                       the application core (PyInstaller onedir)
    build_manifest.json            version, commit, dirty, build time, signing status
    friday\ ui\ scripts\ ...       the package, the control room assets, shipped fixtures

%LOCALAPPDATA%\FRIDAY\             the user's state - a rebuild never touches it
  config\.env                      credentials
  data\                            the database (ada.sqlite3), artifacts, captures, data\logs\friday.log
  logs\                            one log per role: server.log, ui.log, agent.log
  vault\                           the knowledge vault
  skills\                          FRIDAY-managed skills (see docs/skills/)
  cache\  runtime\                 reserved
```

`friday/config.py` decides this once for every process: `STATE_ROOT` is
`FRIDAY_HOME` when set, the directory beside the executable in portable
mode, `%LOCALAPPDATA%\FRIDAY` (what Windows reserves for per-user,
machine-specific state, `FOLDERID_LocalAppData`) for a packaged build, and
the repository root from a source checkout. Nothing reads the working
directory (`tests/test_runtime_roots.py` launches four interpreters from
different directories and requires identical answers). `ADA_DB` and
`FRIDAY_VAULT` still override the database and vault individually.

Portable mode: set `PORTABLE_MODE=true` in the environment or put an empty
file named `PORTABLE_MODE` beside `FRIDAY.exe`. State then lives beside the
executable (`config\ data\ logs\ vault\ skills\`), and the build script
preserves those directories across rebuilds.

Builds before 2026-09-15 kept state under `_internal\`. The first start of
a newer build moves `_internal\data`, `_internal\logs` and `_internal\vault`
into the state root entry by entry (never overwriting, shipped fixtures
stay) and adopts a `.env` lying beside the executable into `config\`.
`--doctor` reports leftovers as `legacy state`.

## Build

Once, create the build environment. Never install PyInstaller into the
live `.venv`; the running agent imports from it.

```
set UV_PROJECT_ENVIRONMENT=.venv-build
uv sync --extra dev --extra documents --extra web --python 3.11
uv pip install --python .venv-build\Scripts\python.exe pyinstaller
```

`uv sync` makes the environment match the lock exactly, so every re-sync
(after a dependency change) removes PyInstaller again; repeat the
`uv pip install` line afterwards. The build script's preflight says so
when it is missing.

Then, every time:

```
.venv-build\Scripts\python.exe scripts\build_windows.py
```

The script refuses the live venv, stamps `build_manifest.json` (version
from `pyproject.toml`, commit, dirty flag), sets aside existing user state,
runs PyInstaller on `packaging\FRIDAY.spec`, restores the state, copies
`.env.example`, signs when a certificate is configured, writes
`SHA256SUMS.txt`, and finally runs the artifact itself: `--version` must
answer and `--doctor` must not FAIL. `--clean` also clears PyInstaller's
cache; `--skip-smoke` skips the last step.

The golden journeys against the artifact itself (EXE-01..EXE-10, and EXE-06
with `--with-agent` when a `.env` sits beside the exe) are one command; role
output lands under `build\validate\`:

```
.venv-build\Scripts\python.exe scripts\validate_exe.py [--with-agent]
```

The clean-machine journeys copy the bundle to another drive, strip PATH of
Python, uv, git and Hermes, point every profile variable at empty
directories and run from `C:\`: the closest thing to a machine that never
saw this repository without Windows Sandbox (not installed on this host):

```
.venv-build\Scripts\python.exe scripts\validate_exe_clean.py
```

The unit tests for the launcher and the state roots run in the same venv:

```
.venv-build\Scripts\python.exe -m pytest tests\test_launcher.py tests\test_state_root.py tests\test_build_identity.py -q
```

The skill layer's console is reachable from the packaged build through
the interpreter shim (`docs/skills/ARCHITECTURE.md`):

```
dist\FRIDAY\FRIDAY.exe -m friday.skills.cli doctor
```

## Why onedir, why PyInstaller

- PyInstaller's own guidance is to prove a onedir build before a onefile
  one. A onefile executable unpacks to a temporary directory on every start
  and deletes it at exit; the database, logs and vault would go with it.
- `__file__`-anchored roots (above) need a stable directory. onedir gives
  one; onefile does not.
- Nuitka was not chosen: nothing in this codebase needs compilation, and a
  full compile of livekit, onnxruntime, av and transformers costs hours of
  CPU per build for no functional gain.
- `pyinstaller-hooks-contrib` already carries hooks for onnxruntime,
  transformers, tokenizers and av. The spec adds `collect_all` for the
  `livekit` namespace (the rtc ffi DLL, every plugin, the silero model),
  `mcp`, `trafilatura` (its data tables), `comtypes`/`pycaw`, and `friday`
  itself (its adapters and toolsets are found with `pkgutil`, which static
  analysis cannot see).

## How the pieces fit

- `friday_exe.py` — the frozen entry. Calls `multiprocessing.freeze_support()`
  first (LiveKit's job processes are spawned as this executable with
  `--multiprocessing-fork`) and hands over to `friday.launcher.main`.
- `friday/launcher.py` — roles, supervisor, stop, status, doctor, and the
  interpreter shim. Friday's own code re-invokes `sys.executable` as a
  Python (`-c`, `-m friday.objective_cli`, `scripts/jarvis_overlay.py`);
  in a bundle `sys.executable` is FRIDAY.exe, so the shim serves those
  shapes before parsing launcher arguments. A `.py` path that is not on
  disk but maps to a bundled module is run as that module.
- `friday/build_identity.py` — reads `build_manifest.json` when present,
  so the control-room footer and `server.py`'s `friday.build` line report
  the stamped commit instead of `unknown`.
- `friday/hermes_model_gateway_worker.py` and `scripts/jarvis_overlay.py`
  are shipped as files as well as modules: Hermes's own Python reads the
  worker from disk.

## Code signing

`scripts/build_windows.py` signs with SignTool (SHA-256, RFC 3161
timestamp) when `FRIDAY_SIGN_THUMBPRINT` (certificate store) or
`FRIDAY_SIGN_PFX` (+ `FRIDAY_SIGN_PFX_PASSWORD`) is set, and verifies the
signature afterwards. Otherwise it prints `SIGNING_STATUS=NOT_CONFIGURED`
and the manifest says the same. An unsigned executable triggers
SmartScreen on machines that have never seen it; that is expected until a
certificate exists. No self-signed certificate is created to hide that.

## Known limits

- Two console executables: `FRIDAY.exe` and `FRIDAY-Debug.exe` (the same
  program with the bootloader's import trace, DEBUG logging and fault
  handlers on; `FRIDAY_DEBUG=1` turns the same diagnostics on for
  `FRIDAY.exe`). No windowless variant: the console is how a failed start
  is seen and how Ctrl+C stops the three roles. `--stop` only stops
  processes started from the same executable file.
- The shippable artifact is `dist\FRIDAY-<version>-win64.zip` with its
  digest in `dist\FRIDAY-<version>-win64.zip.sha256` (`--no-zip` skips
  it). No installer framework is on this host and the onedir layout needs
  none: unpack, run. Removing the folder never touches the profile
  (clean-machine journey CLEAN-06).
- `--doctor` covers application, state root, legacy state, credentials
  (names only), writable directories, database, core imports, ui assets,
  browser, Hermes (with its version), ports, skills (tiers, registry,
  quarantined or stale), voice (STT/TTS/LLM selection and its credentials,
  input devices) and security (kernel modules present, credentials outside
  the application directory), then code signing.
- The turn-detector model (LiveKit `download-files`) is fetched into the
  Hugging Face cache on first use, as from source. Run
  `FRIDAY.exe --role agent download-files` once on a machine that has never
  run the agent.
- Hermes is not bundled. `--doctor` reports DEGRADED when `hermes` is not
  on PATH; execution delegation is unavailable until it is installed.
- Task Scheduler entries Friday creates (reminders, schedules) point at the
  executable's current path; moving `dist\FRIDAY` breaks them until they
  are re-created.
- Windows only. The build script refuses to run elsewhere.
