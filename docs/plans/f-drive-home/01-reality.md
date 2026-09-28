# F-drive home — reality (verified 2026-09-18)

Everything below was read from this machine or its source trees on
2026-09-18. Nothing was changed. Evidence is `file:line` or the command that
produced it.

## 1. The hardware fact that shapes everything

| Fact | Evidence |
|---|---|
| One physical disk: WDC PC SN530 NVMe 512 GB, GPT, disk 0 | `Get-Disk` |
| C: 234 GB (boot), **F: 217 GB (partition 4, "New Volume")**, D: 9 GB, 3 recovery partitions | `Get-Partition -DiskNumber 0` |
| F: free 59 GB, C: free 49 GB, D: free 1 GB | `Get-Volume` |
| **Windows pages on F:** — `F:\pagefile.sys`, 23.5 GB allocated | `Win32_PageFileUsage` |
| F: owner SYSTEM; `Authenticated Users` have Modify | `Get-Acl F:\` |
| BitLocker state unknown (cmdlet needs admin); no evidence of encryption | `Get-BitLockerVolume` → Access denied |
| Session runs as standard user; `admin` is in Administrators (UAC elevation possible) | `WindowsPrincipal.IsInRole` = False; `Get-LocalGroupMember` |
| RAM 15.4 GB, Dell Inspiron 5515, Windows 11 Pro 26200 | `Win32_ComputerSystem` |
| WSL `Ubuntu-24.04` exists (vhdx 1.4 GB on C:) but is **empty**: no users in `/home`, no `.hermes`/`.claude`, no repos | `wsl -d Ubuntu-24.04 -u root -- ls /home; du -sh /root` |
| OneDrive: not signed in (process count 0, folder has only `desktop.ini`) | `Get-Process OneDrive` |
| No `gh`, `restic`, `rclone`, `7z`, `sqlite3`, `docker`; no `~/.ssh` | `Get-Command` |
| GitHub auth = Git Credential Manager entry `git:https://github.com` (machine-bound) | `cmdkey /list` |

**Consequence.** F: is an internal partition of the only disk. It survives a
Windows reinstall of C:, but it does not travel to a new laptop by itself.
"F: as the home" therefore has to be paired with an off-machine copy, or the
goal ("never lose memory on a new laptop") is not met. This is the central
design constraint; see `02-architecture.md` §5.

## 2. What "memory" actually is on this machine

Classes: **STATE** (irreplaceable, must be backed up), **CODE** (git; must be
pushed), **CACHE** (re-downloadable; re-point, never back up), **TOOL**
(reinstalled by a one-liner).

| Item | Where now | Size | Class | Documented relocation seam | Evidence |
|---|---|---|---|---|---|
| Claude Code config: `settings.json`, `CLAUDE.md`, `projects/*/memory/*.md` (7 projects), `history.jsonl`, `.credentials.json` (OAuth), transcripts (39 MB), `skills/`, `plans/`, `file-history/` | `C:\Users\admin\.claude` | 850 MB (807 MB is plugin cache) | STATE (+CACHE) | `CLAUDE_CONFIG_DIR` — **rejected**, see §4.1 | `du -sm .claude/*` |
| Claude Code global config: 22 user-scope MCP servers, `oauthAccount`, project trust list, onboarding | `C:\Users\admin\.claude.json` | 78 KB | STATE | moves only with `CLAUDE_CONFIG_DIR` (binary: `globalConfig: join(CLAUDE_CONFIG_DIR ‖ homedir(), ".claude.json")`) | strings of `~/.local/share/claude/versions/2.1.272` |
| claude-mem: `claude-mem.db` (+wal), `settings.json` (holds Gemini/OpenRouter keys), `backfill.json`, `transcript-watch.json` | `C:\Users\admin\.claude-mem` | 14 MB | STATE | `CLAUDE_MEM_DATA_DIR` — env var wins, then `settings.json`, then `~/.claude-mem` | plugin `13.24.0/skills/mode-creator/scripts/configure-telegram.mjs:38-44`; 215 refs |
| Hermes runtime state: `state.db`, `config.yaml`, `.env`, `auth.json`, `SOUL.md`, `profiles/` (16: `jarvis-*` ×8, `local-*` ×8), `skills/`, `memories/`, `sessions/`, `cron/`, `kanban/`, `hooks/` | `C:\Users\admin\AppData\Local\hermes` (= `HERMES_HOME`, User env var) | ~75 MB | STATE | `HERMES_HOME`; installer flags `-HermesHome`, `-InstallDir` | `hermes_constants.py:59-101`; `scripts/install.ps1:26-27` |
| Hermes code checkout + venv + node_modules + `.plans/` | `%LOCALAPPDATA%\hermes\hermes-agent` (nested inside HERMES_HOME) | 1.8 GB | CODE (upstream git) + CACHE (venv) + STATE (`.plans/`) | installer re-run updates in place when `.git` is valid, **wipes the dir if `.git` is broken**, always recreates `venv\` | `install.ps1:944-999`, `:1006-1014`, `:1163-1166` |
| Jarvis = the 8 Hermes profiles `jarvis-*` + shims `~/.local/bin/jarvis-*` (`exec hermes -p jarvis-<role>`) | HERMES_HOME + `C:\Users\admin\.local\bin` | 7 × 50 B | STATE (profiles) / TOOL (shims) | covered by HERMES_HOME | `cat ~/.local/bin/jarvis-oracle` |
| Jarvis project sources + `jarvis.db`, `jarvis_memory.db` | `C:\Users\admin\Downloads\jarvis-final` (**not git**) and `Downloads\jarvis-main` (**not git**) | 1,020 MB | CODE+STATE, unprotected | none — must move + `git init` | `git status` → not a repo |
| May-23 "AI-Agent Workbench" (souls, agent roster, prompts, scripts, docs, jarvis backups) | `C:\Users\admin\.ai-agent-workbench` | small | STATE (archive) | scripts hardcode `C:\Users\admin\…` | `scripts/backup-jarvis.ps1:7-8` |
| Friday live state in the source checkout: `data/ada.sqlite3`, `audit.sqlite3`, `gateway_calls.sqlite3`, `access_log.jsonl`, `data/skills/`, `vault/`, `.env` | `F:\F.R.I.D.A.Y` (gitignored, so **not on GitHub**) | 3 MB | STATE | state root = repo root in source mode (`FRIDAY_HOME` overrides everything — deliberately not used) | `friday/config.py:108-119`; `.gitignore` "Live state" block |
| Friday per-user dirs: secret broker, reminders, browser profile | `~/.friday-secrets` (0 MB), `~/.ada` (0 MB), `~/.friday` (absent) | 0 | STATE (future) | `FRIDAY_SECRET_HOME`, `ADA_REMINDER_DIR`, `FRIDAY_BROWSER_PROFILE` | `secret_broker.py:36-37`; `toolsets/reminders.py:65`; `ui_browser.py:35-36` |
| Friday packaged-exe state (from validation runs) | `%LOCALAPPDATA%\FRIDAY` | 1 MB | disposable | `FRIDAY_HOME` / `PORTABLE_MODE` marker beside the exe | `config.py:92-118` |
| MCP `memory` server knowledge graph | nowhere yet (graph is empty; no `memory.jsonl` on disk) | 0 | STATE (future) | `MEMORY_FILE_PATH` env on the server entry | `read_graph` → `{entities:[],relations:[]}` |
| Machine wiring: User env vars `HERMES_HOME`, `HERMES_GIT_BASH_PATH`, `CLAUDE_CODE_USE_AUTH_TOKEN`, PATH entries; `.gitconfig` (1 line); Ollama startup link | registry HKCU, `C:\Users\admin` | — | STATE (must be re-creatable) | none — needs a manifest | `Get-ItemProperty HKCU:\Environment` |

Caches (re-point only): uv package cache 7 GB (`%LOCALAPPDATA%\uv\cache`),
uv-managed CPythons 257 MB (`%APPDATA%\uv\python`: 3.11.15, 3.13, 3.14),
HuggingFace 534 MB, Playwright browsers 675 MB, npm cache, plugin caches,
`node_modules`, every venv.

Tools (reinstall): `uv 0.11.12` and `claude 2.1.272` in `~/.local/bin`
(installers have no location flag), Node 24 (`Program Files`), Git 2.53
(`Program Files`), python.org 3.12/3.13 (unused by any venv here), Ollama,
bun (`~/.bun`, used by the claude-mem worker), rg/ffmpeg (winget), VS Code,
Cursor.

## 3. Code repositories and their exposure

| Repo | Dirty files | Unpushed commits | Remote |
|---|---|---|---|
| `F:\F.R.I.D.A.Y` | 40 | 0 | github.com/DarshPro07/F.R.I.D.A.Y |
| `F:\ai-agent-nexi` | 6 | 0 | github.com/aicodepro/ai-agent-nexi |
| `F:\AI-Agent-Crunchy\crunchyfashion-cmo-sales-agent` | 20 | 0 | github.com/aicodepro/crunchyfashion-cmo-sales-agent (second copy on `D:\aicodepro\`) |
| `F:\AI-Agent-Crunchy\FlorabyS-ETSY`, `…\CrunchyFashionEtsy` | — | — | **not git** |
| `D:\aicodepro\aicodepro-agent\agent-code`, `D:\aicodepro\project` | — | — | **not git** (D: has 1 GB free) |
| `C:\…\Downloads\jarvis-final`, `jarvis-main` | — | — | **not git**, on C: |

Uncommitted work exists in exactly one place today. Cloud Claude Code
sessions and any Hermes sandbox start from what is pushed, which is the
likely meaning of "code gets removed when I switch a Claude/Hermes session".

## 4. Findings that change the design

### 4.1 `CLAUDE_CONFIG_DIR` is the wrong lever here
- Friday's CLI executor documents that moving it breaks subscription auth:
  `friday/executors/cli.py:44-47` ("CLAUDE_CONFIG_DIR must not be moved").
- Hermes reads Claude's credentials from fixed paths with no env support:
  `agent/anthropic_adapter.py:873, 895, 1021` (`~/.claude/.credentials.json`,
  `~/.claude.json`).
- Friday walks `~/.claude/projects` directly: `friday/executors/runs.py:337`.
- With the env var set, `.claude.json` also moves into the config dir
  (binary evidence above), so Hermes's reader would break.
- `CLAUDE_CODE_PROJECT_DIR_NAME` exists in the binary but is only honoured
  together with `CLAUDE_CONFIG_DIR` and is a single global name — unusable
  for a multi-repo user. Project keys stay path-derived (`F--F-R-I-D-A-Y`),
  so the drive letter must stay `F:`.
- Therefore: an NTFS **junction** `%USERPROFILE%\.claude → F:\home\claude`
  (no admin, transparent to every consumer), and `.claude.json` stays a real
  file on C: that the backup captures and the bootstrap restores.

### 4.2 Hermes: env var is supported, but with holes
- `get_hermes_home()` falls back to `~/.hermes` when a child process lacks
  the env var (`hermes_constants.py:101`, documented as upstream issue
  #18594); explicit propagation exists in only ~6 spawners.
- Five modules hardcode `~/.hermes` outright: `agent/file_safety.py:16,25`,
  `agent/nous_rate_guard.py:35`, `agent/lsp/install.py:117`,
  `gateway/platforms/telegram.py:3298`. A stray
  `C:\Users\admin\.hermes\config.yaml` (2026-05-24) already exists from this.
- Therefore: `HERMES_HOME=F:\home\hermes` **and** a junction
  `%USERPROFILE%\.hermes → F:\home\hermes`, so every fallback lands on F:.
- Built-ins to reuse, not rewrite: `hermes backup` / `hermes import`
  (`hermes_cli/backup.py:34-60`, `main.py:12043-12097`), `hermes doctor`
  (`hermes_cli/doctor.py`), `hermes profile export|import`.

### 4.3 Every venv is bound to a C: interpreter
`pyvenv.cfg` `home = C:\Users\admin\AppData\Roaming\uv\python\cpython-3.11-…`
for `F:\F.R.I.D.A.Y\.venv`, `.venv-build`, and the Hermes venv. Copying a
venv to another machine never works; the fix is `UV_PYTHON_INSTALL_DIR` on
F: plus `uv sync` from `uv.lock` (Friday) and the installer re-run (Hermes).
`.venv-verify` (the house-rule test venv, `AGENTS.md:70-83`) does not exist.

### 4.4 Friday still carries a previous laptop's layout
Hardcoded `D:\hermes\…` defaults: `friday/connectors/plane.py:64, 358, 404`,
`scripts/probe_model_gateway.py:16`, `scripts/promotion_1f.py:22-23`. No
`C:\Users\admin` in source. These are the fossil of the last migration and
should derive from `HERMES_HOME`.

### 4.5 Security
- Secrets that would live on F:: Claude OAuth token, claude-mem API keys in
  `settings.json`, Hermes `.env`/`auth.json`/`config.yaml`, Friday `.env`,
  broker key. F: is not known to be encrypted; backups must be encrypted
  regardless of target.
- F: root ACL grants `Authenticated Users` Modify: fine for a data volume,
  and it is what lets a differently-named user on another machine use the
  tree.

## 5. Root causes of "I lose everything"

1. Every tool defaults its state to `C:\Users\admin\…`, which is exactly what
   a new laptop (or reinstall) replaces.
2. Several projects are not in git, and every git repo has uncommitted work.
3. There is no off-machine copy of any state. The May-23 workbench backups
   were zips on C: itself.
4. Nothing records how the machine is wired (env vars, PATH, links,
   installs), so each rebuild is done by hand and differently.
