# FRIDAY.exe — validation evidence

Artifact: `dist\FRIDAY\FRIDAY.exe` and `dist\FRIDAY\FRIDAY-Debug.exe`,
FRIDAY 0.1.0, commit `030795f+dirty` (the working tree that adds the
launcher, the state relocation and the skill layer), built from
`.venv-build` with Python 3.11.15 and PyInstaller 6.22.3;
`_internal\build_manifest.json` carries the same facts. Onedir, 455 MB
with both executables (57 MB each); the shippable
`dist\FRIDAY-0.1.0-win64.zip` is 237 MB with its digest beside it.
`SIGNING_STATUS=NOT_CONFIGURED`.

## Final build (2026-09-16, detached from the harness)

The shipped artifact is the build that exited at 21:42 IST (PyInstaller
reported 16,568 s of wall time because the host spent most of that in
Modern Standby; the previous identical build ran in 409 s awake). Its
golden journeys were rerun after it finished: 15/15, EXE-04 with 43 →
49 tables, EXE-06 BLOCKED_EXTERNAL. Its in-build doctor reads the Hermes
probe honestly under load (`present; version not read within 45s`); run
outside the build the same probe returns `Hermes Agent v0.14.0`.

| Check | Result |
|---|---|
| golden journeys, `scripts/validate_exe.py --with-agent` | 15/15 (EXE-06 BLOCKED_EXTERNAL on placeholder credentials) |
| clean-machine journeys, `scripts/validate_exe_clean.py` | 6/6, CLEAN-06 proves removing the bundle keeps the profile's database |
| `FRIDAY-Debug.exe --version` | `diagnostics on: … frozen=True argv=['--version']` then the version line |
| doctor from the exe, new sections | `hermes … (Hermes Agent v0.14.0 (2026.5.16))`; `skills 1 on disk in 3 tier(s), 0 registered; unregistered: friday-operate` (DEGRADED until `sync`); `voice stt sarvam configured; tts openai configured; llm google/gemini-2.5-flash; 15 input device(s)`; `security 24/24 kernel modules present; secrets reported by name only` |
| skills console from the exe | `sync` registers the shipped `friday-operate`; `builtins` lists it; `activate friday-operate` runs its `scripts/check.py` through the exe's interpreter shim (`FRIDAY 0.1.0 (commit 030795f+dirty, dev)`) → ACTIVE; `adopt` copies it into the user tier; `upgrade-builtin` → `current`; `outcome … success` → quality 1.0; `doctor` PASS |
| EXE-04 | 43 tables at bootstrap, 49 after the roles ran (the skills ledger is created by the doctor's skills section after its database check); the file persisted |
| full deterministic suite on the final tree, three chunks | 1556 + 664 + 1833 = 4053 passed, 73 skipped, 0 failed |

Earlier builds of the same tree gave the same journey results (builds of
2026-09-15 08:16 and 15:0x: 15/15 and 5/5).

Everything below was produced by running the artifact, never the source
tree. Commands are repeatable:

```
.venv-build\Scripts\python.exe scripts\build_windows.py             build + in-build smoke
.venv-build\Scripts\python.exe scripts\validate_exe.py --with-agent  golden journeys
.venv-build\Scripts\python.exe scripts\validate_exe_clean.py         clean-machine journeys
.venv-build\Scripts\python.exe -m pytest tests\test_launcher.py tests\test_state_root.py tests\test_build_identity.py -q
```

## Golden journeys (build of 2026-09-15 15:0x IST; identical results on the 08:16 build)

The validator sets `LOCALAPPDATA` to `build\validate\profile` for every
launched process and clears `FRIDAY_HOME`, so the owner's real profile is
never touched and the default path, including migration, is what is
tested.

| Journey | Result | Evidence |
|---|---|---|
| EXE-00 preconditions | PASS | exe present, :8000 and :8770 free |
| EXE-01 `--version` | PASS | `FRIDAY 0.1.0 (commit 030795f+dirty, dev)` — the stamped commit; no git in the bundle |
| EXE-12 state root outside the bundle | PASS | `--paths`: `state_root=<profile>\FRIDAY`, database and logs under it, `.env` at `<profile>\FRIDAY\config\.env`; nothing under `_internal` |
| EXE-02 `--doctor` bootstraps | PASS | exit 1 (DEGRADED: signing), `data\ada.sqlite3` created in the state root, 43 tables |
| EXE-13 legacy state migrates | PASS | probes planted in `_internal\data`, `logs`, `vault` moved into the state root; `data\corpus` and `data\gate` stayed in the bundle |
| EXE-03 `--role server` | PASS | `GET /sse` → 200 `text/event-stream` on :8000 |
| EXE-05 `--role ui --no-browser` | PASS | `/health` 200, `GET /` → 200 `text/html` on :8770 |
| EXE-08 interpreter shim | PASS | `-c` reports `friday.__file__` under `_internal` and `sys.frozen=True`; `-m friday.launcher --version` answers; a bundled `.py` path runs as its module |
| EXE-10 unknown role fails loudly | PASS | exit 2, `FRIDAY: unknown role 'nope' (expected one of server, ui, agent)` |
| EXE-04 database persists | PASS | same file, same 43 tables after the server and ui processes ran and stopped |
| EXE-07 voice worker boots in the bundle | PASS | supervisor started server → ui → agent; LiveKit inference process initialised (turn-detector model found), `job runner initialized`, connection attempts reached LiveKit; launcher alive |
| EXE-06 voice worker registered with LiveKit | BLOCKED_EXTERNAL | the `.env` copied from the owner's profile still holds the `.env.example` LiveKit placeholders; LiveKit answers 401. Same from source. Needs real `LIVEKIT_URL`/`LIVEKIT_API_KEY`/`LIVEKIT_API_SECRET` in `%LOCALAPPDATA%\FRIDAY\config\.env` |
| EXE-09 `--stop` after server, ui and the full stack | PASS | ports free and `--status` reports no processes each time |

Result line: `15/15 journeys passed`. Role output under `build\validate\`.

## Clean-machine journeys

Windows Sandbox is not installed on this host, so
`scripts/validate_exe_clean.py` copies the bundle to `C:\Windows\Temp`
(another drive), sets PATH to the Windows system directories only (no
Python, uv, git, Hermes, Node), points `LOCALAPPDATA`, `APPDATA`,
`USERPROFILE` and `TEMP` at fresh empty directories, drops every
developer variable, and runs from `C:\`.

| Journey | Result | Evidence |
|---|---|---|
| CLEAN-01 `--version` | PASS | `FRIDAY 0.1.0 (commit 030795f+dirty, dev)` |
| CLEAN-02 `--paths` under the fresh profile | PASS | `state_root=<copy>\profile\LocalAppData\FRIDAY`, no `.env` |
| CLEAN-03 `--doctor` with no developer environment | PASS | only `livekit credentials` FAILs (no `.env`); `core imports` PASS; hermes and browser DEGRADED as expected |
| CLEAN-04 MCP server and control room start (`--no-agent --no-browser`) | PASS | :8000 and `/health` 200 with the launcher alive |
| CLEAN-05 `--stop` | PASS | ports free, no processes |

Result line: `5/5 clean-machine journeys passed` (twice: 08:16 and 15:0x
builds). A first attempt died on a negative subprocess timeout when the
host entered standby mid-run; the validators now report that as a named
FAIL instead of crashing.

## Skill layer through the packaged exe

With `LOCALAPPDATA` pointed at the scratch profile and a `say-ok` skill
written under `skills\user\` whose declared test runs the exe's own
interpreter shim:

```
FRIDAY.exe -m friday.skills.cli --db <scratch> sync       registered say-ok  LOCAL_USER  QUARANTINED
FRIDAY.exe -m friday.skills.cli --db <scratch> activate say-ok
  PASS risk READ_ONLY · PASS trigger 'say ok' · PASS non_trigger 'build the exe'
  PASS procedure 'FRIDAY.exe -c print(1)' exit 0 · PASS tokens ~218   → say-ok: ACTIVE (1.0.0, quality 0.75)
FRIDAY.exe -m friday.skills.cli --db <scratch> select "please say ok"   selected [say-ok], loaded_tokens 218
FRIDAY.exe -m friday.skills.cli --db <scratch> doctor                   1 on disk, 1 registered: ACTIVE=1 → PASS
FRIDAY.exe -m friday.skills.cli --db <scratch> hermes-link              plan only: would add the scratch user tier to skills.external_dirs
```

Repeated on the final (15:0x) build with the added console commands:
`hints "please say ok"` → `["say-ok"]`; `propose` with three remembered
steps wrote a QUARANTINED draft into the scratch user tier; `rescore`
kept `say-ok` ACTIVE at quality 0.75; `doctor` reported
`ACTIVE=1, QUARANTINED=1 → DEGRADED` (the quarantined draft, as designed).

## In-build smoke (scripts/build_windows.py)

`--version` answered; `--doctor` on the owner's real profile: state root
`C:\Users\admin\AppData\Local\FRIDAY`, `.env` adopted from beside the exe
into `config\.env` (LiveKit and provider credential names present),
database 45 tables, core imports 13, ui assets, browser, hermes, ports
free; DEGRADED only for `code signing NOT_CONFIGURED`.

## Defects found while packaging and relocating

See `docs/product/EXE_AND_SKILL_SYSTEM_LEDGER.md` for the full list; the
ones specific to the exe:

1. inconsistent bundle from editing the tree mid-build;
2. `defusedxml` never declared (RSS and caption tools would raise);
3. console hold firing in terminals;
4. doctor counting defaulted settings as unset;
5. shim crashing on an unknown bundled path;
6. `.env` adoption failing across drives (`Path.replace` → `shutil.move`);
7. the vault shipped inside `_internal` reported as legacy state on
   every rebuild (no longer shipped);
8. `uv sync` removing PyInstaller from the build venv (documented; the
   preflight names it).

## What is not proven here

- Registration with LiveKit and a spoken turn (EXE-06 onwards): blocked
  on real credentials.
- Code signing: no certificate on this machine.
- A true fresh-OS run: the clean-machine script removes the developer
  environment and the checkout's reachability, not the operating system's
  own installed runtimes (Visual C++ redistributables, WebView).
