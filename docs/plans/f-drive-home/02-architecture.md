# F-drive home — architecture, procedures, verification

Companion to `01-reality.md` (facts) and `00-status.md` (slices and gates).

## 1. Principles

1. **One tree.** `F:\home` holds every piece of state and every tool that can
   live there. If `F:\home` exists, `bootstrap.ps1` can rebuild the machine;
   if `F:\home` is backed up, a new laptop can be rebuilt.
2. **Classify every path**: STATE is backed up; CODE is pushed to git; CACHE
   is re-pointed and never backed up; TOOL is reinstalled by the bootstrap.
3. **One mechanism per tool.** Use the tool's documented seam (env var or
   installer flag) when it has one. Use an NTFS junction only where consumers
   hardcode `~` (Claude Code's `~/.claude`; Hermes's `~/.hermes` fallbacks).
   Never both for the same tool.
4. **No `C:\Users\admin` anywhere on F:.** The next laptop may have another
   user name. Everything derives from `$env:USERPROFILE` or `F:\home`.
5. **Rebuild, don't copy, derived things.** Venvs come from `uv.lock` and the
   Hermes installer; caches refill. Copying a venv across machines never works
   (`pyvenv.cfg` binds an absolute interpreter path).
6. **Replication is part of the goal, not an extra.** F: is an internal
   partition; without an off-machine copy a new laptop gets nothing.
7. **Reuse what exists** before writing anything: Hermes `install.ps1
   -HermesHome/-InstallDir`, `hermes backup/import/doctor`; Friday
   `config.default_state_root()`, `launcher.doctor()`, the
   `validate_exe_clean.py` clean-environment pattern; claude-mem
   `CLAUDE_MEM_DATA_DIR`; `claude mcp add-json`; restic for snapshots.

## 2. Layout

```
F:\
  F.R.I.D.A.Y\  ai-agent-nexi\  AI-Agent-Crunchy\  jarvis\     <- CODE, unchanged paths
  home\                                                          <- THE HOME (one tree)
    bootstrap\         git repo (private remote): bootstrap.ps1, doctor.ps1, backup.ps1,
                       restore.ps1, env.psd1 (manifest), pins.json, lib\, tests\, README.md,
                       state\ (gitignored: before-env.json, restic.password, last-backup.json)
    claude\            <- junction target of %USERPROFILE%\.claude (STATE + plugin cache)
    claude\_profile\   <- backup staging copy of %USERPROFILE%\.claude.json, .gitconfig
    claude-mem\        <- CLAUDE_MEM_DATA_DIR (STATE)
    hermes\            <- HERMES_HOME (STATE) ; hermes\hermes-agent\ = code + venv (installer default)
                          hermes\git\, hermes\node\ = installer-managed portable tools
    friday\            <- secrets\ (FRIDAY_SECRET_HOME), reminders\ (ADA_REMINDER_DIR),
                          browser-profile\ (FRIDAY_BROWSER_PROFILE); optional apps\FRIDAY\ (exe, PORTABLE_MODE)
    mcp\               <- memory.jsonl (MEMORY_FILE_PATH for the MCP memory server)
    git\               <- gitconfig (GIT_CONFIG_GLOBAL); PortableGit only if no system Git
    uv\                <- python\ (UV_PYTHON_INSTALL_DIR)  cache\ (UV_CACHE_DIR)  tools\  bin\   (CACHE)
    node\              <- portable Node LTS for Claude's npx MCP servers (TOOL, only if no system Node)
    npm\               <- NPM_CONFIG_PREFIX + cache\ (CACHE)
    bun\               <- BUN_INSTALL (TOOL/CACHE; claude-mem worker)
    models\            <- ollama\ (OLLAMA_MODELS)  hf\ (HF_HOME)   (CACHE)
    playwright\        <- PLAYWRIGHT_BROWSERS_PATH (CACHE)
    bin\               <- restic.exe (pinned), jarvis-* shims source
    archive\           <- ai-agent-workbench (May-23), other frozen material
    backups\           <- db\ (SQLite online-backup copies)  wip\ (git patches)  — restic reads from here
```

Repos stay where they are: moving `F:\F.R.I.D.A.Y` would change Claude's
project key (`F--F-R-I-D-A-Y`), the memory directory, `.claude.json` project
entries and claude-mem's project mapping for no gain. Volume label becomes
`FRIDAY-HOME`; the bootstrap refuses to run unless the tree is on `F:`.

## 3. The manifest (`env.psd1`) — single source of truth

User-scope env vars (set with `[Environment]::SetEnvironmentVariable(…, "User")`,
the same call the Hermes installer uses, `install.ps1:1400-1405`):

| Var | Value | Owner |
|---|---|---|
| `HERMES_HOME` | `F:\home\hermes` | Hermes installer sets; bootstrap asserts |
| `HERMES_GIT_BASH_PATH` | system Git's `bin\bash.exe`, else `F:\home\git\bin\bash.exe` | bootstrap |
| `CLAUDE_MEM_DATA_DIR` | `F:\home\claude-mem` | bootstrap |
| `CLAUDE_CODE_USE_AUTH_TOKEN` | `true` (carried as-is) | existing |
| `FRIDAY_SECRET_HOME` / `ADA_REMINDER_DIR` / `FRIDAY_BROWSER_PROFILE` | `F:\home\friday\{secrets,reminders,browser-profile}` | bootstrap |
| `UV_PYTHON_INSTALL_DIR` / `UV_CACHE_DIR` / `UV_TOOL_DIR` / `UV_TOOL_BIN_DIR` | `F:\home\uv\{python,cache,tools,bin}` | bootstrap |
| `NPM_CONFIG_PREFIX` / `NPM_CONFIG_CACHE` | `F:\home\npm`, `F:\home\npm\cache` | bootstrap |
| `BUN_INSTALL` | `F:\home\bun` | bootstrap |
| `OLLAMA_MODELS` / `HF_HOME` / `PLAYWRIGHT_BROWSERS_PATH` | `F:\home\models\ollama`, `F:\home\models\hf`, `F:\home\playwright` | bootstrap |
| `GIT_CONFIG_GLOBAL` | `F:\home\git\gitconfig` | bootstrap |

Deliberately **not** set: `CLAUDE_CONFIG_DIR` (see `01-reality.md` §4.1) and
`FRIDAY_HOME` (it would hijack the source checkout's state root,
`friday/config.py:110-112`, and `launcher.migrate_legacy_state()` refuses when
it is explicit).

PATH: add `F:\home\uv\bin`, `F:\home\npm`, `F:\home\bun\bin`, `F:\home\bin`,
(`F:\home\node`, `F:\home\git\cmd` only when the portable copies are in use);
the installer adds `F:\home\hermes\hermes-agent\venv\Scripts`. Remove the old
`C:\Users\admin\AppData\Local\hermes\hermes-agent\venv\Scripts`.

Junctions (`New-Item -ItemType Junction`, no admin):
`%USERPROFILE%\.claude → F:\home\claude`, `%USERPROFILE%\.hermes → F:\home\hermes`.

Canaries (doctor reports DEGRADED if any re-appears as a real directory on C:):
`~\.claude-mem`, `%LOCALAPPDATA%\hermes`, `%LOCALAPPDATA%\FRIDAY`,
`~\.friday-secrets`, `~\.ada`, `%APPDATA%\uv\python`, `%LOCALAPPDATA%\uv\cache`.

Profile-root files that must stay real files on C: and are staged into
`F:\home\claude\_profile\` by every backup and put back by `restore.ps1`:
`%USERPROFILE%\.claude.json`. (`.gitconfig` moves via `GIT_CONFIG_GLOBAL`.)

## 4. Per-tool procedures

### 4.1 Claude Code (junction)
Migration (owner runs it from a plain PowerShell **with every Claude Code
window closed** — the directory holds open handles):
1. `robocopy %USERPROFILE%\.claude F:\home\claude /E /COPY:DAT /DCOPY:T`, then
   compare file counts and total bytes.
2. `Rename-Item %USERPROFILE%\.claude .claude.pre-f-home` (kept until Gate B
   passes; pruned by `bootstrap.ps1 -PruneOldState`).
3. `New-Item -ItemType Junction -Path %USERPROFILE%\.claude -Target F:\home\claude`.
4. Copy `%USERPROFILE%\.claude.json` to `F:\home\claude\_profile\` (staging).
New laptop: `restore.ps1` puts the tree back and creates the junction before
`claude` is first launched; `.credentials.json` comes with it, so the
subscription login normally survives. The binary is reinstalled with the
official one-liner (`irm https://claude.ai/install.ps1 | iex`).
Known risk: Claude Code normalises its config path (`ve()` in the binary);
a junction is resolved by Node transparently, but Gate B explicitly checks
hooks, plugins, MCP list, memory and `--resume` after the swap. Rollback is
the reverse of steps 2–3.

### 4.2 claude-mem (env var)
Stop the worker (`F:\…\claude-mem\worker.pid`, a `bun` process, port 37777)
and Claude Code; move `~\.claude-mem` to `F:\home\claude-mem`; set
`CLAUDE_MEM_DATA_DIR`; rewrite the two absolute values inside
`settings.json` (`CLAUDE_MEM_DATA_DIR`, `CLAUDE_MEM_TRANSCRIPTS_CONFIG_PATH`)
to the F: path. `chroma\` is a rebuildable index. Verification: observation
count in `claude-mem.db` before == after; the next SessionStart context shows
the same history; `logs\` on F: receives new lines; the C: canary stays
absent. Optional later: claude-mem cloud sync (cmem.ai) as a second copy of
this one database — not required because restic covers it.

### 4.3 Hermes + Jarvis (installer flags + junction)
1. Stop the gateway / any `hermes` process. `hermes backup -o
   F:\home\backups\hermes-pre-move.zip` (built-in, excludes the code tree).
2. Copy `%LOCALAPPDATA%\hermes` → `F:\home\hermes` (state, code checkout,
   `git\`, `node\` if present); rename the C: tree `hermes.pre-f-home`.
3. With `UV_PYTHON_INSTALL_DIR` already on F: and `uv python install 3.11`
   done, re-run the upstream installer:
   `scripts\install.ps1 -HermesHome F:\home\hermes -InstallDir F:\home\hermes\hermes-agent`
   — valid `.git` ⇒ update in place (`install.ps1:944-999`); the venv is
   recreated and now binds to `F:\home\uv\python\…` (`:1163-1169`);
   `HERMES_HOME` and PATH are set in User scope (`:1384-1405`); an existing
   `.env` is not overwritten (`:1429-1438`).
4. Junction `%USERPROFILE%\.hermes → F:\home\hermes` (covers the five
   hardcoded fallbacks and unpropagated children). Remove the stray
   `C:\Users\admin\.hermes\config.yaml` first (it is a 219-byte fallback
   artefact from 2026-05-24; kept in the archive).
5. Regenerate the seven `~\.local\bin\jarvis-*` shims from
   `F:\home\bin\jarvis-shims\` (one-line `exec hermes -p jarvis-<role>`).
Verification: `hermes doctor` PASS; `hermes profile list` shows the 16
profiles; `jarvis-oracle --help` resolves; Friday's `hermes_bridge.locate()`
(`friday/hermes_bridge.py:1043-1071`) reports the F: install via PATH; the
`hermes-agent\.plans\` files are present.

### 4.4 Friday
- Source checkout stays at `F:\F.R.I.D.A.Y`; state root stays the repo root
  (`friday/config.py:119`); `data/ada.sqlite3` is never written by any
  script in this plan (backup reads it through SQLite's online-backup API
  on a `mode=ro` connection — stated assumption for the house rule).
- Set the three per-user seams (§3). Nothing to move: all three are empty.
- Rebuild `.venv`, `.venv-build` (`UV_PROJECT_ENVIRONMENT=.venv-build`, per
  `scripts/build_windows.py:7-16`) and create the missing `.venv-verify`
  (`AGENTS.md:70-83`) with `uv sync --python 3.11` so every `pyvenv.cfg`
  `home=` points under `F:\home\uv\python`.
- Code fix (single owner: the connector's service): derive the `D:\hermes`
  defaults in `friday/connectors/plane.py:64,358,404` and the two scripts
  from `HERMES_HOME` (`Path(os.environ.get("HERMES_HOME", Path.home()/".hermes"))`),
  with a unit test that sets `HERMES_HOME` to a tmp path and asserts the
  resolved profile env path. Optionally honour `CLAUDE_CONFIG_DIR` in
  `friday/executors/runs.py:337` — not needed with the junction; skip.
- Optional: copy `dist\FRIDAY\` to `F:\home\friday\apps\FRIDAY\` with a
  `PORTABLE_MODE` marker so the exe keeps its state beside itself on F:
  (`friday/config.py:92-105`). `%LOCALAPPDATA%\FRIDAY` (1 MB) is disposable.

### 4.5 Python / uv, Node / npm / bun, Git, models, browsers
- uv and claude binaries stay in `~\.local\bin` (installers offer no location
  flag); bootstrap installs them when missing. `uv python install 3.11`
  lands under `F:\home\uv\python`. The 7 GB C: cache is not moved: after the
  venvs are rebuilt, `bootstrap.ps1 -PruneOldCaches` deletes it.
- Node: prefer system Node ≥ 20 if present (this laptop: 24 in Program
  Files); otherwise unpack the pinned portable zip to `F:\home\node`
  (sha256 in `pins.json`). Claude's 22 MCP servers run `cmd /c npx -y …`
  and only need `npx` on PATH; their cache moves via `NPM_CONFIG_CACHE`.
- bun: `BUN_INSTALL=F:\home\bun` (the claude-mem worker runtime).
- Git: prefer system Git; else PortableGit to `F:\home\git` (the Hermes
  installer ships the same package). `GIT_CONFIG_GLOBAL` moves the config;
  GitHub HTTPS auth is machine-bound (Credential Manager) — one interactive
  login per machine, documented in the runbook.
- MCP `memory` server: re-register with `MEMORY_FILE_PATH=F:\home\mcp\memory.jsonl`
  (`claude mcp add-json memory …`); today's graph is empty, so no move.
- `OLLAMA_MODELS`, `HF_HOME`, `PLAYWRIGHT_BROWSERS_PATH`: set; move the
  existing HF (534 MB) and Playwright (675 MB) trees to avoid re-downloads.

## 5. Replication — what actually makes a new laptop possible

**Tool: restic** (single pinned `restic.exe` in `F:\home\bin`; encrypted,
deduplicated, versioned; the same commands work against a USB drive, a NAS,
S3/B2 or an rclone remote, so the target can change later without changing
the scripts).

`backup.ps1` (idempotent, ~1 min, scheduled at logon + every 6 h via a
user-level scheduled task, plus `backup.ps1 -Now`):
1. `dbsnap.py` — for every known SQLite file (`claude-mem.db`, Hermes
   `state.db` and `profiles\*\state.db`, Friday `data\*.sqlite3`, Jarvis
   `*.db`) open read-only and `Connection.backup()` into
   `F:\home\backups\db\` — consistent copies while the apps keep running,
   without VSS/admin. Live `*.db`, `*-wal`, `*-shm` are excluded from restic.
2. `wip-snapshot` — for every repo in the manifest: `git diff HEAD` →
   `backups\wip\<repo>.patch` and `git ls-files --others --exclude-standard`
   zipped → `<repo>-untracked.zip`. Captures the 40/6/20 uncommitted files
   without touching the owner's commit discipline (agents never commit).
3. Stage `%USERPROFILE%\.claude.json` into `F:\home\claude\_profile\`.
4. `restic backup --files-from include.txt --exclude-file exclude.txt`
   over `F:\home` (excluding `uv\`, `npm\cache`, `bun\`, `node\`, `models\`,
   `playwright\`, `claude\plugins\cache`, `claude\plugins\marketplaces`,
   `hermes\hermes-agent\{venv,node_modules,.git}`, `hermes\{git,node}`, live
   DB files) plus the Friday checkout's gitignored state (`.env`, `data\`
   minus `tts_cache|browser_shots|workspace|workbench|exports`, `vault\`),
   `F:\jarvis\**\*.db`, and `F:\home\archive`.
5. `restic forget --keep-daily 14 --keep-weekly 8 --keep-monthly 12 --prune`;
   `restic check` weekly; write `state\last-backup.json` (doctor reads it).

Password custody: `F:\home\bootstrap\state\restic.password` (ACL: the user
only; gitignored) **and** the owner's password manager. Losing the password
loses every backup — the runbook says so in its first line.

Targets (owner decision, `00-status.md`): first an external USB SSD/HDD
labelled `FRIDAY-BACKUP` (`restic -r E:\friday-restic`); then a cloud bucket
(Backblaze B2 / S3) so a stolen bag does not take both copies. `restic copy`
mirrors one repo into the other.

## 6. New-laptop runbook (target: under one hour, mostly downloads)

0. Prerequisites the owner brings: the restic password, the GitHub login,
   a Windows user account, a volume with letter **F:** and ≥ 60 GB (Disk
   Management: shrink C: or use the second disk), the backup drive.
1. Install Git (`winget install Git.Git`) — or copy `F:\home\bootstrap` from
   the backup drive, where `backup.ps1` also keeps an unencrypted copy of
   the bootstrap repo — and `git clone` the private bootstrap repo to
   `F:\home\bootstrap`.
2. `restore.ps1 -Repo E:\friday-restic` → `restic restore latest --target F:\`
   (recreates `F:\home`), puts `.claude.json` back, clones every repo listed
   in the manifest into `F:\`, applies `wip\*.patch` + untracked zips.
3. `bootstrap.ps1` → installs uv, claude, node/git if needed, restic; sets
   env vars + PATH; creates the junctions; `uv python install 3.11`; Hermes
   installer with the F: flags; `uv sync` per repo; jarvis shims; scheduled
   backup task; volume label check.
4. `doctor.ps1` must print `PASS` for every line before the machine is used.
5. One-time interactive logins: GitHub (Credential Manager); Claude only if
   `.credentials.json` expired.

## 7. Verification gates and rollback

| Gate | Proves | How |
|---|---|---|
| A — dry run | The planner is right | `bootstrap.ps1 -Migrate -WhatIf` lists every move/env/junction; Pester tests over the manifest and the planner (fake "before" state → expected action list) |
| B — this laptop | Nothing lost, everything found on F: | After S2–S5: `doctor.ps1` all PASS; `claude --version`, `claude mcp list`, a session in `F:\F.R.I.D.A.Y` shows the memory dir and the same claude-mem observation count; `hermes doctor` PASS; Friday launcher doctor PASS; pytest gate from `.venv-verify` reproduces the last counts (A–F 1556, P–Z 1833, `docs/plans/windows-exe`) |
| C — restorable | The backup is real | `restic check`; `restic restore latest --target <scratch>`; byte/hash diff against `F:\home` for the included set; open the restored `claude-mem.db` and count rows |
| D — clean machine | A new laptop works | Windows Sandbox (`.wsb` maps the restic repo and bootstrap read-only; inside: `subst F: C:\F`, run steps 2–4 of the runbook) — different user name, no C: state. Fallback: a fresh local user account on this laptop |
| E — no drift | Nothing crept back to C: | doctor canaries after one week of normal use |

Rollback at every slice: the C: originals are renamed (`*.pre-f-home`), never
deleted, until Gate B passes; `state\before-env.json` restores env vars and
PATH exactly; junction removal is one command.

## 8. Scripts (all PowerShell 5.1 + one Python helper, no admin unless stated)

| File | Purpose |
|---|---|
| `bootstrap.ps1` | Converge the machine to the manifest (idempotent). Flags: `-Migrate` (first run on a machine that has C: state), `-WhatIf`, `-PruneOldState`, `-PruneOldCaches` |
| `doctor.ps1` | `PASS/DEGRADED/FAIL  name  detail` lines, exit 0/1/2 — same shape as `friday/launcher.py:897-910` and `validate_exe_clean.py:50-53` |
| `backup.ps1` / `restore.ps1` | §5 / §6 |
| `dbsnap.py` | SQLite online backups (argparse, docstring-first, matches `scripts/` style) |
| `env.psd1`, `pins.json`, `include.txt`, `exclude.txt` | Manifest, pinned tool versions + sha256, restic sets |
| `lib\*.ps1` | `Ensure-EnvVar`, `Ensure-PathEntry`, `Ensure-Junction`, `Stop-Workloads`, `Assert-HomeDrive`, `Write-Report` |
| `tests\*.Tests.ps1` | Pester (built-in 3.4 suffices): manifest validity, no `C:\Users\<name>` literals, planner output for fixture states, doctor formatting |
| `admin-steps.ps1` | The only elevated script: volume label, drive-letter pin (`Set-Partition -NewDriveLetter F`), pagefile move, BitLocker enable, Windows Sandbox feature — each a separate, opt-in switch |

## 9. Security notes (rule 12)

- Secrets never enter git: the bootstrap repo ignores `state\`; `.env`,
  `auth.json`, `.credentials.json`, claude-mem `settings.json` travel only
  inside the encrypted restic repo.
- F: at rest: recommend BitLocker with the recovery key in the Microsoft
  account and on paper. Without it, the laptop's disk holds every token in
  clear (it already does today on C:, so this is not a regression).
- Downloads are pinned and hash-checked (`pins.json`), mirroring
  `third_party/UPSTREAM_LOCK.json`; every HTTP call in the scripts has a
  timeout.
- Backup runs log to `F:\home\backups\log\` including auth failures against
  the target.

## 10. Not doing

No `CLAUDE_CONFIG_DIR`; no relocation of the Windows profile, Program Files
apps, VS Code/Cursor settings; no WSL work (the distro is empty); no
OneDrive/Drive sync of live SQLite files (corruption risk); no copying of
venvs or `node_modules`; no changes to `data/ada.sqlite3`; no automatic
commits or pushes by agents.
