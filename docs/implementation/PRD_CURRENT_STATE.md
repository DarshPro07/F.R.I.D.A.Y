# PRD Current State — machine and repository truth

Written 2026-09-23 at the start of the universal-PRD implementation program.
Everything here was measured on this host in this session; nothing is carried
forward from an earlier ledger without being re-measured or explicitly marked
as carried.

## 1. Requirements authority

| | |
|---|---|
| PRD | `C:\Users\admin\Downloads\PRD.md` |
| PRD sha256 | `1777465992d37628f8c1ec0cc44c9ccf8aada4f07357140b8bbb8687b231bf35` |
| PRD size | 1,491 lines / 68,487 bytes |
| PRD version | 1.0 — Universal Product Baseline |
| Also in tree | `docs/PRD_V3.1.md` (sha256 `3b518965…`) — a **different, earlier** document |

The two PRDs are not the same file. `docs/PRD_V3.1.md` is the v3.1 master
document the previous program implemented (its compliance matrix is
`docs/architecture/PRD_V31_COMPLIANCE_MATRIX.md`). The new `PRD.md` is the
universal product baseline: it re-scopes the product from "Friday for this
owner" to "a product any brand, agency or professional installs", and it
introduces requirement families that v3.1 never had — workspace isolation,
progressive onboarding, local intelligence routes, a free-model scout, a
technology scout, research artifact caching, a capability builder pipeline,
Telegram remote control and Android automation.

Both are authoritative for their own scope. Where a v3.1 requirement and a
new PRD requirement describe the same thing, the new PRD's acceptance
criteria govern, and the v3.1 evidence is carried only when it was produced
by a test that still exists and still runs on this tree.

The product owner's recorded direction of 2026-09-22 answers and in places
amends the PRD. It is captured in the ledger as `PRD-OWN-*` rows, including
the three places where it cannot be implemented as stated and why.

## 2. Repository

| | |
|---|---|
| Root | `F:\F.R.I.D.A.Y` |
| Branch | `main` |
| HEAD | `030795f5dcd9ccd9524c62aea7aef86d59312554` |
| HEAD subject | `e2e: commit the mic-race fix I reported earlier and never staged` |
| Commits on main | 38 |
| Remote | `https://github.com/DarshPro07/F.R.I.D.A.Y` |
| Worktrees | one (`F:/F.R.I.D.A.Y`, no linked worktrees) |
| Uncommitted paths | 41 at session start (19 modified, 22 untracked) |
| Submodules | `third_party/upstream/agency-agents` is a gitlink with **no** `.gitmodules` entry |

### Uncommitted work present at session start — preserved, not reset

The working tree carries a complete, unstaged work package from the previous
session (the Windows EXE and the Skill Intelligence layer, ledger at
`docs/product/EXE_AND_SKILL_SYSTEM_LEDGER.md`). It is the owner's to commit
(house rule: agents never commit). Nothing in it was discarded, reset or
overwritten:

- modified: `friday/config.py` (STATE_ROOT), `friday/ui_server.py`,
  `friday/launcher.py`-adjacent modules, `scripts/soak.py`, `pyproject.toml`,
  `uv.lock`, `.gitignore`, six other `friday/*.py`, three test files
- untracked: `friday/launcher.py`, `friday/skills/` (18 modules),
  `friday_exe.py`, `packaging/`, `skills/`, `scripts/build_windows.py`,
  `scripts/validate_exe*.py`, `docs/build/`, `docs/skills/`,
  `docs/product/`, seven test modules

A secret scan over every untracked text file (OpenAI/Google key shapes,
`API_SECRET=`, private-key headers) found nothing.

## 3. Import origin

```
.venv       3.11.15  ->  F:\F.R.I.D.A.Y\friday\__init__.py
.venv-build 3.11.15  ->  F:\F.R.I.D.A.Y\friday\__init__.py
```

Both venvs import **this** checkout. There is no `.venv-verify` on this
machine and no `D:` worktree, so the gate in `AGENTS.md` that names
`.venv-verify` is run here with `.venv-build`, which is the isolated build
venv and not the live agent's venv.

## 4. Host

| | |
|---|---|
| OS | Windows 11 Pro 10.0.26200 |
| CPU | AMD Ryzen 7 5700U, 16 logical cores |
| RAM | 16.0 GB total, **1.6 GB free** at session start |
| GPU | AMD Radeon integrated (no discrete GPU) |
| Free disk (F:) | 83.9 GB |
| Python / Node / uv / npm | 3.11.15 / v24.13.1 / 0.11.12 / 11.8.0 |
| Claude Code CLI | 2.1.278 at `C:\Users\admin\.local\bin\claude` |

Host pressure is a real constraint on evidence quality: at session start
Chrome, Edge, a ChatGPT desktop app and a Claude session held most of RAM.
The host also enters Modern Standby unattended (Kernel-Power 506/507), which
has previously turned long runs into hours of wall time and four spurious
test failures. Long runs in this program are therefore started detached, one
at a time, and their host conditions are recorded with the result.

## 5. Services and processes

- No FRIDAY process running: nothing listening on `:8000` (MCP), `:8770`
  (control room) or `:7880`.
- No Hermes process, and **no Hermes installation on this host**: `D:\hermes`
  does not exist and `.env` carries no `HERMES_DIR` / `HERMES_PYTHON`. The
  v3.1 ledger's live Hermes evidence was produced on a machine that had it.
- `.env` holds 9 keys; `LIVEKIT_URL`, `LIVEKIT_API_KEY` and
  `LIVEKIT_API_SECRET` are still the `.env.example` placeholders, so LiveKit
  answers 401 and no live voice measurement is possible here.
- Live database `data/ada.sqlite3` present and untouched (house rule).
- 19 of 24 configured MCP servers fail to connect in this session (wrong npm
  package names and a stale API key); none of them is required by FRIDAY's
  own gates.

## 6. Test baseline on this exact tree

Canonical runner, four chunks, `-m "not live and not slow"`, 224 test files,
`.venv-build`, `ADA_DB` pointed at a scratch database (never the live one):

```
python scripts/baseline_suite.py --out data/prd_baseline --chunks 4 \
       --python .venv-build\Scripts\python.exe
```

Result: see `data/prd_baseline/summary.txt` — it is regenerated by the run
and is the authority, not this paragraph. Chunk 0 finished
`1237 passed, 16 skipped, 5 deselected` in 256 s with exit 0.

## 7. Known blockers at session start

| blocker | blocks | owner action |
|---|---|---|
| Hermes not installed on this host | live model-gateway and execution-engine evidence (`FR-MDL-003`) | install Hermes, or authorise FRIDAY to provision it |
| LiveKit credentials are placeholders | voice latency targets (`FR-VOI-001`, `NFR-PERF-001`, `PRD-SPIKE-06`) | supply real LiveKit credentials |
| No provider credentials beyond placeholders | official model connections (`PRD-GATE-004`) | connect at least one provider |
| No code-signing certificate | signed update path (`NFR-SEC-001`) | supply a certificate |
| No authorised third-party test account | authenticated browser journey (`PRD-GATE-015`) | nominate a safe test account |
| Customer PII retention default undecided | `PRD-SPIKE-05` | a business decision |
| AI disclosure on customer-facing channels | `PRD-OWN-DISCLOSE` | a legal decision |

## 8. The gap the audit found that matters most

`PRD-INV-06` — "workspace scope is mandatory for private-state access" — has
no implementation. The word *workspace* in this codebase means the filesystem
jail root (`fsjail.DEFAULT_WORKSPACE`), not a tenant boundary. Memories carry
`scope` and `project_scope`; secrets, skills, research and browser profiles
are single-tenant. `FR-PRV-002` is therefore `MISSING`, and it carries
`PRD-GATE-008`, `PRD-MET-TRUST` and the workspace half of `FR-ONB-004`,
`FR-SKL-001`, `FR-CAP-003` and `FR-POL-004`.

That is the first implementation target of this program.
<!-- build-program:begin -->
## Build program (generated; do not edit by hand)

- HEAD: 4a20e8d on prd-v7/slice-7-adaptive-hardware, updated 2026-10-05T11:46:10+00:00
- Counts: {"DEFERRED_BY_PRD": 2, "EXISTING_UNVERIFIED": 9, "MISSING": 86, "PARKED_BY_OWNER": 1, "PARTIAL": 63, "VERIFIED": 36}
- Unresolved P0: 21
- Last full suite: 4,753 passed, 0 failed at 258e3c0
- Last wave (148): none
- Parked: CORE-001 (PARKED_BY_OWNER), DOMAIN-001 (partial x2), EVAL-001 (failed x2), ACT-001 (failed x2), CTX-001 (partial x2), BCON-001 (partial x2), CYBER-002 (partial x2), DUR-001 (partial x2), INTENT-001 (integration refused), JIT-001 (partial x2), MEM-003 (stale worktree), MEM-002 (partial x2), TRAJ-001 (partial x2), MCP-001 (NOT_RED), CTX-002 (VERIFIER_FAIL), CTX-003 (integration refused), RES-001 (VERIFIER_FAIL), BROW-004 (partial x2), CLI-001 (partial x2), ASSUME-001 (failed x2), BBOX-001 (partial x2), CAP-003 (integration refused), BRAIN-001 (cascade exhausted), BROW-003 (partial x2), CAP-004 (integration refused), CAP-FORGE-003 (TEST_TIMEOUT), APPROVAL-001 (stale worktree), BG-001 (partial x2), PKG-001 (partial x2), CAP-FORGE-002 (integration refused), CAPB-001 (TEST_TIMEOUT), CHAOS-001 (partial x2), CODE-001 (blocked), COG-003 (cascade exhausted)
- Open escalations: 101
  - CAP-FORGE-002 integration: refusing CAP-FORGE-002: run has no commit
  - CAPB-001 test_timeout: {"requirement": "CAPB-001", "result": "TEST_TIMEOUT", "red_green": {"result": "OK", "tests": ["tests
  - MODEL-012 resolved: controller unpark: integrated by the controller after a clean rebase
  - MODEL-012 blocked: Live FRIDAY.exe text and voice turn needs working LiveKit and TTS credentials, which this host does 
  - - docs_commit: ex.lock': File exists.

Another git process seems to be running in this repository, e.g.
an editor o
  - CHAOS-001 partial: integrated twice without closing its gaps
  - CODE-001 blocked: Owner decision needed: a live run budget. Defaults (5 tasks x 2 arms x 3 repeats = 30 runs) need at 
  - SEC-005 resolved: controller unpark: owner ruling 2026-10-05b: scoped delegations get their own gateway without Hermes
  - MODEL-012 resolved: controller unpark: owner ruling 2026-10-05b: slice 3, opencode free models and the signed-in Codex C
  - COG-003 cascade: 3 repairs spent
- Next ready: MODEL-012, SEC-005
<!-- build-program:end -->
