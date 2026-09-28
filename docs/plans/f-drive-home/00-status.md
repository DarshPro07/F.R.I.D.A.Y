# F-drive home — plan and status

Owner request (2026-09-18): "I have a F drive; make it the home for all the
code. My code gets removed when I switch laptop or Claude/Hermes session.
Set up Hermes, Python, Jarvis etc. so that I never lose my memory even with
a new laptop. Do not create a random plan — research first."

**Verdict: PLAN ONLY.** Nothing on the machine was changed. Every fact in
`01-reality.md` was read on 2026-09-18; the design is in `02-architecture.md`.

| Gate | State |
|---|---|
| 0 Reality | ✅ `01-reality.md` |
| 1 Decision | ✅ `02-architecture.md` §1–§5 |
| 2 Slices | ✅ below |
| 3 Verification design | ✅ `02-architecture.md` §7 |
| 4 Execution | ⬜ waits for the owner's five decisions below |
| 5 Rehearsal on a clean machine | ⬜ Gate D |

## The decision in one paragraph

F: is an internal partition of the laptop's only disk (`01-reality.md` §1),
so "put it on F:" alone cannot survive a laptop change. The plan therefore
has two halves that only work together: **(a)** `F:\home` becomes the single
tree that holds all state and F:-resident tools, wired by a manifest-driven
`bootstrap.ps1` (env vars for tools that document one — Hermes, claude-mem,
uv, npm, Ollama, HF, Playwright, Friday's seams; NTFS junctions only for
`~\.claude` and `~\.hermes`, whose consumers hardcode `~`); **(b)** an
encrypted, scheduled restic snapshot of that tree (plus SQLite online copies
and git patches of uncommitted work) to an off-machine target, with a tested
`restore.ps1` + `bootstrap.ps1` runbook that rebuilds a fresh Windows user
in under an hour. `CLAUDE_CONFIG_DIR` is deliberately not used
(`01-reality.md` §4.1); `FRIDAY_HOME` is deliberately not set globally.

## Decisions the owner must make before Gate 4

| # | Decision | Recommendation | Why it matters |
|---|---|---|---|
| 1 | Off-machine backup target | External USB SSD labelled `FRIDAY-BACKUP` now; add a cloud bucket (B2/S3) when available | Without a target, S7 cannot run and the "new laptop" goal is unmet |
| 2 | Move the pagefile from F: to C: (admin, reboot) | Yes | Frees 23.5 GB on F:; F: stops being a boot-critical volume |
| 3 | BitLocker on F: (admin; recovery key to Microsoft account + paper) | Yes | Every token and memory DB will live on F: |
| 4 | Move `Downloads\jarvis-final` (1.0 GB), `jarvis-main` (245 MB), `D:\aicodepro\*` to `F:\` and create private GitHub repos for the non-git projects; which GitHub account (DarshPro07 or aicodepro) | Yes, under the account that owns the related repos | These are the only code trees with no copy anywhere |
| 5 | Rehearsal method: enable Windows Sandbox (admin, reboot) or create a second local user | Windows Sandbox | Gate D is the proof that a new laptop works |

Assumptions if the owner does not answer: 1 = external drive, 2–3 = yes but
scheduled last, 4 = yes under DarshPro07, 5 = second local user.

## Slices

Each slice ends with a check that failed before and passes after; the C:
originals stay renamed until Gate B. Agents never commit; the orchestrator
asks the owner. Rough effort in brackets.

| # | Slice | Done when | Evidence to record |
|---|---|---|---|
| S1 | Bootstrap repo skeleton: `F:\home\bootstrap` (git, private remote), `env.psd1`, `pins.json`, `doctor.ps1`, Pester tests, `-WhatIf` planner [½ day] | Pester green; `doctor.ps1` runs and reports today's state as FAIL/DEGRADED for every not-yet-migrated item | doctor output before |
| S2 | Toolchain on F: (uv python 3.11, caches, npm/bun, models, playwright), rebuild `.venv`, `.venv-build`, create `.venv-verify` [½ day] | every `pyvenv.cfg` `home=` under `F:\home\uv\python`; pytest gate from `.venv-verify` reproduces A–F 1556 / P–Z 1833 | doctor "venv bound to F:" PASS; test counts |
| S3 | Claude Code (junction) + claude-mem (env var) — **owner runs the swap outside Claude Code** [1 h + owner] | `claude mcp list` works; a session in `F:\F.R.I.D.A.Y` shows the memory dir and the same claude-mem observation count as before; C: canaries absent | counts before/after; SessionStart header |
| S4 | Hermes + Jarvis: move tree, installer re-run with `-HermesHome F:\home\hermes -InstallDir F:\home\hermes\hermes-agent`, junction `~\.hermes`, shims [2 h] | `hermes doctor` PASS; 16 profiles listed; `jarvis-oracle --help`; Friday `hermes_bridge.locate()` finds it; `.plans\` intact | hermes doctor output |
| S5 | Friday seams (`FRIDAY_SECRET_HOME`, `ADA_REMINDER_DIR`, `FRIDAY_BROWSER_PROFILE`); code fix deriving `D:\hermes` defaults from `HERMES_HOME` (`friday/connectors/plane.py:64,358,404`, `scripts/probe_model_gateway.py:16`, `scripts/promotion_1f.py:22-23`) + unit test; optional portable exe copy [1 h] | new test fails on old code, passes after; launcher doctor PASS | test name; doctor output |
| S6 | Code hygiene: move Jarvis + D: trees to F:, `git init` + private remotes, owner commits/pushes the 40/6/20 dirty files, update the `filesystem` MCP server path [owner-driven] | every repo in the manifest has a remote and `unpushed=0`; no non-git project on C:/D: | `git remote -v` table |
| S7 | Replication: restic on the target, `dbsnap.py`, wip snapshot, `backup.ps1`, scheduled task, first backup, `restic check`, restore rehearsal to scratch + diff [½ day] | doctor "last backup < 26 h" PASS; restored `claude-mem.db` row count == live | restic snapshot id; diff summary |
| S8 | Clean-machine rehearsal (Gate D), runbook `README.md`, `AGENTS.md` operational notes, memory note, prune `*.pre-f-home` and old C: caches (~10 GB back on C:) [2 h] | `doctor.ps1` all PASS under a different user name with no C: state | sandbox/new-user doctor output |

## Not in scope

`CLAUDE_CONFIG_DIR`; moving the Windows profile or Program Files apps;
editor settings; WSL (empty); OneDrive/Drive sync of live databases; copying
venvs; any write to `data/ada.sqlite3` (backups read it via SQLite's
online-backup API on a read-only connection — stated assumption).

## Next command

Owner answers decisions 1–5, then: `S1` via the friday-orch dispatch recipe
(rule 15) with this directory as the plan doc.
