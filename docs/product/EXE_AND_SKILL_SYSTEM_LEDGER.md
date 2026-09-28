# EXE and Skill System — implementation ledger

Work package: Windows `FRIDAY.exe` (Objective A) and the Skill Intelligence
layer over Hermes skills (Objective B). Owner direction of 2026-09-15:
move persistent state out of `_internal`, clear the environmental test
blocker, validate LiveKit with real credentials, then build the skill
intelligence layer on Hermes's skill system.

Statuses: VERIFIED needs evidence in this file or the linked docs;
BLOCKED_EXTERNAL names what only the owner can supply.

Tree under test: `030795f` + this uncommitted work. Nothing was committed
(house rule: agents never commit; the owner reviews the diff).

## Objective A — Windows executable

| ID | Requirement | Status | Evidence |
|---|---|---|---|
| A-1 | One canonical entry point; dev and packaged share the bootstrap | VERIFIED | `friday/launcher.py` (roles, supervisor, stop, status, doctor, paths, interpreter shim); `friday_exe.py`; `python -m friday.launcher` from source |
| A-2 | Immutable core separated from mutable state | VERIFIED | `friday/config.py` `STATE_ROOT`: `FRIDAY_HOME` → portable (beside the exe) → `%LOCALAPPDATA%\FRIDAY` → repository from source; `tests/test_state_root.py` (21); EXE-12 |
| A-3 | `.env` order: OS environment, `STATE_ROOT/config/.env`, beside the exe only in portable mode, repository only from source | VERIFIED | `config.env_file_candidates`, `load_env_files`; tests in `test_state_root.py`; EXE-12 shows `config\.env` |
| A-4 | Older builds' state migrates on first start; shipped fixtures stay | VERIFIED | `launcher.migrate_legacy_state`, `config.adopt_legacy_env_file` (cross-drive via `shutil.move`); EXE-13; unit tests |
| A-5 | Secrets never embedded | VERIFIED | spec ships no `.env`; `.env.example` only; doctor prints names, never values (`test_doctor_never_prints_a_secret_value`) |
| A-6 | First-run bootstrap: dirs, database, diagnostics | VERIFIED | `ensure_state_dirs`, doctor bootstraps `ada.sqlite3` (43 tables); EXE-02 |
| A-7 | Runtime doctor with PASS/DEGRADED/FAIL | VERIFIED | `FRIDAY.exe --doctor`, exit 0/1/2; build smoke; clean-machine CLEAN-03 |
| A-8 | Onedir proven before onefile | VERIFIED (onedir chosen) | `docs/build/WINDOWS_EXE.md` "Why onedir": `__file__`-anchored roots and persistent state; onefile deliberately not produced |
| A-9 | Debug build | DEFERRED_WITH_REASON | one console build; `--doctor`, per-role logs and `--paths` give the diagnosability; a windowless variant is a follow-up |
| A-10 | Build metadata (version, SHA, time, channel) | VERIFIED | `_internal/build_manifest.json`; `--version` prints `FRIDAY 0.1.0 (commit 030795f+dirty, dev)`; `friday.build_identity` reads the manifest |
| A-11 | Code signing when a certificate exists, `NOT_CONFIGURED` otherwise | VERIFIED (not configured) | `scripts/build_windows.py::sign`; manifest `signing_status: NOT_CONFIGURED` |
| A-12 | Reproducible build command | VERIFIED | `.venv-build\Scripts\python.exe scripts\build_windows.py`; six builds this package; `SHA256SUMS.txt` |
| A-13 | EXE golden journeys on the artifact | VERIFIED | `scripts/validate_exe.py --with-agent`: 15/15 on builds 4 and 5 (EXE-00…13) |
| A-14 | Clean-machine run | VERIFIED (substitute) | Windows Sandbox not installed; `scripts/validate_exe_clean.py` copies the bundle to C:, strips PATH of Python/uv/git/Hermes, fresh profile, cwd `C:\`: 5/5 on builds 4 and 5 |
| A-15 | Upgrade preserves user state | VERIFIED | state lives outside the bundle (A-2); portable layout preserved by the build script; probes survived a rebuild |
| A-16 | Voice worker registers with LiveKit | BLOCKED_EXTERNAL | the owner's `.env` carries the `.env.example` LiveKit placeholders; LiveKit answers 401 (EXE-06); EXE-07 proves the worker boots in the bundle (inference process, job runner, connection attempts) |
| A-17 | Turn-detector model present | VERIFIED | `FRIDAY.exe --role agent download-files` fetched it (394 MB cache) |

## Objective B — Skill Intelligence over Hermes

| ID | Requirement | Status | Evidence |
|---|---|---|---|
| B-1 | Reuse Hermes's store, format, `skill_manage`, guard, usage, curator, taps | VERIFIED | audit in `docs/plans/skill-intelligence/00-status.md`; every FRIDAY skill is a valid Hermes skill (`metadata.friday` only) |
| B-2 | Lifecycle DISCOVER→…→ACTIVE→…→DEPRECATE, never generated→trusted directly | VERIFIED | `contracts.TRANSITIONS`, `lifecycle.py`; agent/community start QUARANTINED; SK-02, SK-10 |
| B-3 | Tiers built-in / user / project / shared private / public | VERIFIED | `files.tier_roots`; source checkout keeps the user tier under `data/skills/user` (no double discovery) |
| B-4 | Registry with provenance, versions, quality, usage, dependencies | VERIFIED | `registry.py`; in the per-call ledger handle-leak test (`tests/test_dbconn.py`) |
| B-5 | Codebase skills with a fingerprint of what they depend on | VERIFIED | `skills/friday-windows-exe/` (`depends_on`, `tests`); `dependencies.py` fingerprints; `docs/skills/CODEBASE_SKILLS.md` |
| B-6 | Code change → NEEDS_REVALIDATION → targeted test; unrelated change → nothing | VERIFIED | SK-03, SK-04 (README ignored), git-driven `revalidate --since` |
| B-7 | Candidate detector with the ladder's criteria; trivial and unverified work refused | VERIFIED | `candidates.py`; SK-01 |
| B-8 | Curator: noise out, imperative steps, dedupe, merge near-duplicates | VERIFIED | `curator.py`; SK-02; unit tests |
| B-9 | Deterministic linter and secret/privacy scanner | VERIFIED | `lint.py`; SK-05, SK-10, units; `docs/skills/SECURITY.md` |
| B-10 | Risk classification from content, never self-declared | VERIFIED | `lint.classify_risk`; `self_verification` FAIL on self-claims; SK-10 |
| B-11 | Evaluator: trigger / non-trigger / procedure / security / token tests | VERIFIED | `evaluator.py`; real pytest run for `friday-windows-exe` (42 passed); PUB-01 uses a real subprocess |
| B-12 | Quality from outcomes; versioning; rollback of a bad version | VERIFIED | `evaluator.quality`, `lifecycle.update/rollback/degrade_if_needed`; SK-06 |
| B-13 | Lazy routing with token accounting; no full-catalog loads | VERIFIED | `router.py`; SK-09 (index 73 tokens, body 1,358 only when selected, 0 otherwise) |
| B-14 | Conflict detection: specificity, version, else explicit | VERIFIED | SK-07; `friday skills doctor` reports competing tasks |
| B-15 | Publishing LOCAL / PRIVATE_GIT / PUBLIC_GIT in tap layout; public gated by policy; codebase never public | VERIFIED | `publisher.py`; PUB-01 (bare origin, push, fresh clone, discovery, re-activation); SK-08 |
| B-16 | Hermes link (`skills.external_dirs`) | VERIFIED (plan; apply on request) | `hermes_link.py`; unit tests preserve comments; `friday skills hermes-link --apply` not run on the owner's config |
| B-17 | Memory ↔ skill bridge | VERIFIED | `bridge.py`; `friday skills propose` / `facts` |
| B-18 | Owner console | VERIFIED | `friday.skills.cli`: doctor, sync, list, lint, activate, revalidate, rollback, publish, select, hints, propose, rescore, facts, hermes-link; works from the packaged exe via `-m` |
| B-19 | Skill Intelligence in the packaged runtime | VERIFIED | build 5: sync → activate (real procedure test through the shim) → select → doctor PASS from `dist\FRIDAY\FRIDAY.exe -m friday.skills.cli` |
| B-20 | Reachability ratchet honours the new package | VERIFIED | `friday/reachability.py` `DECLARED["friday/skills"]` (a kernel path; one entry with the reason) after removing three dead helpers and wiring the console commands; `tests/test_reachability.py` passes |
| B-21 | Voice/objective seams (`TaskBundle.skill_hints`, MCP tool) | DEFERRED_WITH_REASON | `router.hints_for` exists; the objective-engine and mcp-tools owners wire it (one-line seam each); documented in the plan |
| B-22 | LLM-assisted curation | DEFERRED_WITH_REASON | deterministic curation ships; a model pass is a hook, not required |

## Defects found and fixed on the way

1. Inconsistent first bundle from editing the tree mid-build (rebuild rule).
2. `defusedxml` imported at call time, never declared or locked (declared, locked).
3. Console hold on every non-zero exit even in a terminal (double-click only).
4. Doctor counted settings with defaults as unset (credentials only).
5. Interpreter shim raised on an unknown bundled path (`find_spec` first).
6. `.env` adoption used `Path.replace` across drives (WinError 17 → `shutil.move`).
7. Vault shipped inside the bundle showed as legacy state forever (not shipped).
8. Soak sampler: a psutil `RuntimeError` deleted the verdict (guarded; INCONCLUSIVE with the reason; 4 tests).
9. Risk classifier read trigger phrases and git's "checkout" as money (body only; narrowed patterns).
10. A proposed skill update evaluated the previous version's tests (record re-read from the file).
11. Rollback could not re-activate from DRAFT (restage first).
12. Glob dependencies let `*` cross `/` (own translation, no fnmatch).
13. Publisher commits had no git identity in a fresh checkout (FRIDAY identity, overridable).
14. Validators crashed on a suspended host's negative timeout (named FAIL instead).

## Environment facts that shaped the run

- A leftover `find.exe / -iname activate -path *venv*` from 2026-09-13 held
  10.9 M handles and broke psutil's enumeration (10 pre-existing test
  failures). Ended after inspection; the suite went green.
- The host enters Modern Standby unattended (Kernel-Power 506/507); long
  runs report hours of wall time. Two background jobs were reaped for low
  memory when the build and the full suite overlapped; everything heavy
  ran one at a time afterwards.
- Windows Sandbox is not installed; the clean-machine script is the
  substitute.

## Test runs on the final tree (2026-09-16)

| Run | Result |
|---|---|
| `tests/test_[a-f]*.py` | 1556 passed, 71 skipped |
| `tests/test_[g-o]*.py` | 664 passed, 1 skipped |
| `tests/test_[p-z]*.py` | 1833 passed, 1 skipped |
| total | **4053 passed, 73 skipped, 0 failed** |
| touched areas together (launcher, doctor, skill layer, control room, reachability ratchet, ledgers, soak) | 186 passed |
| skill layer alone | `test_skills_golden.py` 17, `test_skills_units.py` 24, `test_skills_extensions.py` 9 |
| launcher / doctor / state root / identity | 22 + 5 + 21 + 5 |
| soak harness | 20 passed (4 new) |
| artifact (final build) | golden journeys 15/15, clean-machine 6/6, packaged console and debug variant exercised (docs/build/FRIDAY_EXE_VALIDATION.md) |

An earlier pass on the tree before the second-pass additions had one
failure, the reachability ratchet over the new package; it was fixed at
the root (three dead helpers removed, the console wired to the bridge,
detector, curator and quality paths, one `DECLARED` entry) and stayed
green through the final run.

## Second pass (2026-09-16): the master prompt section by section

| Section | Requirement | Status | Evidence |
|---|---|---|---|
| §4 entry point initialises skills | VERIFIED | the doctor's `skills` section syncs nothing but reports tiers, registry and held skills; `sync` is the explicit step |
| §5 first run seeds default skills | VERIFIED | BUILT_IN tier `friday/skills/builtin/friday-operate` shipped in the bundle (package data); `adopt` copies it into the user tier |
| §6 doctor: APPLICATION, DATABASE, HERMES (version), SKILLS, BROWSER, VOICE, PROVIDERS, SECURITY | VERIFIED | `launcher.doctor_checks` + `skills_check`, `voice_check` (reuses `friday.health.report`), `security_check`, `hermes_version`; `tests/test_launcher_doctor.py` |
| §7 onedir installer instead of onefile | VERIFIED | `scripts/build_windows.py::make_distribution` → `dist/FRIDAY-<version>-win64.zip` + `.sha256`; onefile deliberately not produced (docs/build/WINDOWS_EXE.md) |
| §8 debug build | VERIFIED | `FRIDAY-Debug.exe` (bootloader debug) from the same analysis; `launcher.debug_mode` / `enable_diagnostics` (DEBUG logging, faulthandler); `FRIDAY_DEBUG=1` |
| §12 EXE-12 removing the core keeps user data | VERIFIED | `scripts/validate_exe_clean.py` CLEAN-06 |
| §17 registry: success/failure rate, average latency and cost | VERIFIED | `SkillRegistry.stats`; `usage` console command; `/api/skills` |
| §19 codebase fingerprint: repository identity and commit | VERIFIED | `dependencies.git_identity`; stamped at activation (`codebase_id`, `codebase_commit`); test `test_activation_stamps_the_codebase_fingerprint` |
| §30 self-improvement loop: outcomes feed quality | VERIFIED | `outcome NAME success|failure|corrected` → `record_usage` → `rescore`; `rescore` degrades and rolls back below the floor |
| §42 Python plugin system through entry points | VERIFIED | `friday/skills/plugins.py` (`friday.plugins` group; discovery without import; load only for `FRIDAY_PLUGINS_ALLOW`; shape validated); tests |
| §45 `ChangeType` classifier | VERIFIED | `friday/skills/change_type.py`; test |
| §46 Skills view | VERIFIED (read-only API) | `/api/skills` in `friday/ui_server.py` (state, scope, version, source, quality, usage, verification, dependencies, permissions, conflicts, unregistered); the visual panel in `ui/index.html` is left to the voice-brain owner with the endpoint ready |
| §48-B skill diff review | VERIFIED | `diff NAME`: verified snapshot vs on disk |
| §48-C canary | DEFERRED_WITH_REASON | a percentage rollout needs a live objective stream to sample; the lifecycle's rollback-on-failure and the quality floor cover the risk today |
| §48-G codebase learning event | VERIFIED (on request) | `revalidate --since <commit>`; a post-commit hook can call it |
| §48-H usage analytics | VERIFIED | `usage NAME`, `list` quality/usage columns, `/api/skills` |
| §54-55 upgrades keep user skills; built-in vs user-modified merge with rollback | VERIFIED | state outside the bundle; `adopt` / `builtins` / `upgrade-builtin` (replace untouched, merge edited, keep shipped frontmatter, evaluate, roll back on failure); test |
| §47 audit A–T | see below | |

### §47 — bugs and gaps searched for

| Item | Finding | Status |
|---|---|---|
| A skills loaded globally | never: the router returns one index line per skill and loads bodies only when selected | closed |
| B no provenance for agent-created skills | `source`, `trust`, `content_hash`, `codebase_commit` on every record; agent trust starts quarantined | closed |
| C stale codebase instructions | `NEEDS_REVALIDATION` on declared-dependency change; `revalidate` | closed |
| D skills duplicating AGENTS.md | the curator merges near-duplicates against registered skills; AGENTS.md itself is not indexed | open, low: a lint WARN against AGENTS.md lines is a follow-up |
| E skills containing secrets | lint FAIL, quarantine, publication refused | closed |
| F skills referencing deleted code | dependency `missing` state in `check`; `broken_reference` for files inside the skill | closed |
| G scripts depending on developer machine paths | lint `private_data` (WARN local, FAIL for publication); the curator replaces profile paths | closed |
| H skill creation with no test | evaluator WARN; `doctor` reports active skills without tests | closed (reported, not blocked) |
| I skill update with no rollback | `update` always keeps the last verified snapshot and restores it on failure | closed |
| J public/private confusion | scope + `publish` intent + policy gate; codebase scopes never public | closed |
| K no conflict resolution | specificity → version → explicit CONFLICT | closed |
| L whole-memory scans per routing decision | routing reads the registry index only | closed |
| M registry needing a full directory scan every turn | the registry is the ledger; `sync` scans on request | closed |
| N invalid skill silently accepted | lint FAIL → quarantine; malformed frontmatter refused | closed |
| O user-specific skill shared project-wide | tiers are directories; a user-tier skill never lands in the project tier unless published to it | closed |
| P codebase skill stale without detection | fingerprints + git-driven revalidation | closed |
| Q generated helper script executed without policy | FRIDAY never executes skill scripts; only declared tests run, bounded, from the evaluator | closed |
| R skills bypassing deterministic policy | permissions are informational; `self_verification` and `policy_bypass` lint FAILs | closed |
| S publisher exposing private data | publication lint (`private_data` FAIL), scope gate, standing authorization | closed |
| T plugin code imported from skill directories | no such path; plugins are entry points behind an allowlist | closed |

### §60 — final audit of the new system

Searched for: bugs (14 found and fixed, listed above), race conditions
(per-call ledger connections; atomic SKILL.md writes; the supervisor
terminates process trees), unsafe paths (`write_file` refuses `..` and
foreign subdirectories; the router refuses references outside the allowed
subdirectories), secret leakage (doctor and lint print names only; the
validators copy no `.env` into the bundle), wrong app-data paths
(`test_state_root.py`, EXE-12, CLEAN-02), hard-coded source-tree
assumptions (none in `friday/skills`; the validators' scratch skill uses an
absolute path to the exe on purpose), packaging-only failures (six builds,
validated each time), skill conflicts (SK-07), stale detection (SK-03/04),
unbounded scans (none; discovery is per tier root), token bloat (SK-09),
public/private mistakes (SK-08), missing rollback (SK-06), false
verification (SK-10, forged reports), wrong worktree imports (every run
asserts `friday.__file__` under this checkout), Windows-only assumptions
(the build refuses other platforms by design; `config.default_state_root`
falls back to a home directory elsewhere), broken clean-machine startup
(CLEAN-01…06).

## Final architecture (as shipped)

```
FRIDAY.exe / FRIDAY-Debug.exe               dist\FRIDAY\  (455 MB onedir; zip 237 MB)
   │  friday.launcher: roles (server, ui, agent) · supervisor · stop · status
   │  doctor (17 sections) · paths · interpreter shim · legacy migration
   ▼
friday.config STATE_ROOT ─► %LOCALAPPDATA%\FRIDAY  (config\.env, data\, logs\, vault\, skills\)
   │
   ├─ MEMORY        friday.store / memory_promotion          (facts)
   ├─ SKILLS        friday.skills (14 modules) over Hermes    (procedures)
   │     registry · lint · candidates · curator · dependencies · evaluator
   │     lifecycle · router · publisher · hermes_link · bridge · plugins
   │     change_type · cli   — tiers: built-in, user, project, private, public
   ├─ CAPABILITIES  friday.capabilities / fabric              (approved tools)
   └─ PLUGINS       friday.plugins entry points, allow-listed (installed code)
                     ▼
                  HERMES  (skills dir + external_dirs + taps; execution)
                     ▼
                  VERIFY  (evaluator reports = the only evidence for ACTIVE)
                     ▼
                  LEARN   (outcomes → quality → degrade/rollback; code change → revalidate)
```

## Prioritized remaining ideas and gaps

1. Wire `router.hints_for(query)` into `hermes_bridge.TaskBundle.skill_hints`
   (objective-engine owner; one line) and record `outcome` from the
   objective verifier so quality learns from real work, not the console.
2. Expose the skill layer to the voice brain as MCP tools (`skills_status`,
   `skills_learn`, `skills_test`) with capability records and ownership
   guard entries (mcp-tools owner).
3. A Skills panel in `ui/index.html` over the `/api/skills` endpoint, with a
   Playwright journey (voice-brain owner).
4. Real LiveKit credentials in `%LOCALAPPDATA%\FRIDAY\config\.env`, then
   EXE-06 and a spoken turn through the packaged worker.
5. Code signing once a certificate exists (`FRIDAY_SIGN_THUMBPRINT`).
6. Canary activation (a bounded share of eligible objectives) once the
   objective stream feeds outcomes.
7. Curator lint against `AGENTS.md` duplication; an optional model-assisted
   rewording pass behind the deterministic curation.
8. A post-commit hook that runs `friday skills revalidate --since HEAD~1`.
9. Windows Sandbox on the build host for a true fresh-OS run.

## Verdict

- EXE: VERIFIED except A-16 (BLOCKED_EXTERNAL on LiveKit credentials).
- Skill system: VERIFIED for the layer, its journeys and the packaged
  console; deferred with reasons: B-21 (voice/objective seams), B-22
  (LLM-assisted curation), §48-C (canary), §46 visual panel.
