# External audit (2026-09-05) - triage against the repository

Each finding was checked against the tree at `3bcf6d4` before any action.
"Confirmed" means the repo shows the problem; "already true" means the
repo already does what the finding asks (with the evidence); "partly"
names the gap. Actions are tracked here so the order survives context
loss. Owner decisions are marked **OWNER**.

## P0 - repository sanitation and secrets

| ID | Verdict | Evidence | Action |
|---|---|---|---|
| A-001 live DB committed | **Confirmed** | `data/ada.sqlite3` (11.3 MB) tracked since `5b9cd75`; also `data/products_gate.sqlite3`, `data/product_mcp_gate.sqlite3`, `tmp_wire.sqlite3` | untrack; schema already lives in `store.py` `SCHEMA`; add `scripts/seed_demo_db.py` |
| A-002 access log committed | **Confirmed** | `data/access_log.jsonl` tracked since `5b9cd75` | untrack; history contains it (see A-007) |
| A-003 provider cooldowns | **Confirmed** | `data/provider_cooldowns.json` added in `3bcf6d4` | untrack + ignore |
| A-004 generated artifacts | **Confirmed** | `data/golden/*.json` x4, `data/perf/latest.json`, `data/hermes/*.json`, `data/post_*/summary.txt`, `data/evaluation/attempts.json` (895 KB), `data/tts_cache` 14, `data/exports` 13, `data/workspace` 9, `data/vision` 14 PNG, `data/browser_shots` 3 | untrack runtime output; keep `data/corpus`, `data/gate` (fixtures the tests read) |
| A-005 binary | **Confirmed** | `Friday.exe`, `Friday.exe.bak`, `Friday.exe.pre-mcp-backup` tracked | untrack + ignore; CI builds |
| A-006 `.serena/project.local.yml` | **Confirmed** | added in `3bcf6d4` | untrack + ignore |
| A-007 secret history scan | **Done** - gitleaks 8.30.1 over all 3 commits: 15 findings | see "Secret scan result" below |
| A-015 line endings | **Confirmed** | no `.gitattributes`; CRLF/LF warnings seen | add `.gitattributes`, normalize |
| A-055 DOCX as canonical PRD | **Confirmed** | `FRIDAY_JARVIS_..._v3.1.docx` tracked | convert to `docs/PRD_V3.1.md`, untrack binary |
| A-056 repo growth | **Confirmed** | `trainingData.yml` 16.8 MB, `.aicodepro/checkpoints.git` 754 objects / 17 MB, `ui/models/*.bin` 6.4 MB | `.aicodepro` untrack; `trainingData.yml` **OWNER** (what is it?); face model is a product asset, keep |

### Secret scan result (gitleaks 8.30.1, full history, values never printed)

| finding | verdict | action |
|---|---|---|
| `data/companion/extension_key.pem` (private key) + `token.txt` + `extension_id.txt` | **REAL** - this is the key/token the companion bridge trusts (`friday/companion/pairing.py:58`, `bridge.py:80`); in history since `5b9cd75`, repo is **public** | **DONE**: untracked + ignored; **rotated** (`pairing.provision()` -> new key, new extension id `moiepnmalifnlgklhjohilejgagemhed`, manifest re-pinned; `bridge.rotate_token()` -> new secret). Old material moved to `%LOCALAPPDATA%\friday-retired-companion-<stamp>` for the owner to destroy after re-pairing the extension. History rewrite remains OWNER's call (below) |
| `friday/companion/extension/manifest.json` `"key"` | false positive - Chrome MV3 *public* key (id derivation), meant to be shipped | allow-listed in `.gitleaks.toml` |
| `tests/test_execution.py`, `tests/test_sandbox.py`, `tests/test_shared_brain.py` (12) | false positives - literal fake tokens (`abc123xyz789`, `sk-abc...6789`) used to test redaction | allow-listed |
| `third_party/bin/cbm/THIRD_PARTY_NOTICES.md` | false positive - a git commit SHA matched the sourcegraph pattern | allow-listed |

Re-scan with `.gitleaks.toml`: staged tree **0 findings**; full history
**1 finding** = the retired `.pem` in `5b9cd75`..`3bcf6d4` (expected until
history is rewritten; the key is now worthless).

Lesson recorded: marking `*.pem binary` in `.gitattributes` made gitleaks
report 0 findings on the same history (diff scanners see "Binary files
differ"). Removed; a key file stays text so scanners can see it.

No provider API key (OpenAI/Anthropic/Google/ElevenLabs/Sarvam/LiveKit)
appears anywhere in history: `.env` was ignored from the first commit.

**Owner action required (cannot be done by the agent):**
1. Re-pair the Chrome companion extension: reload the unpacked extension
   (its id changed to `moiepnmalifnlgklhjohilejgagemhed`), then destroy
   `%LOCALAPPDATA%\friday-retired-companion-*`.
2. Decide on history: (a) leave it - the old key/token are revoked by
   rotation, or (b) `git filter-repo --path data/companion --invert-paths`
   + force-push + re-clone everywhere. (a) is safe; (b) is cleaner.
3. Enable GitHub secret scanning + push protection on the repository
   (Settings -> Code security).

## P0 - correctness findings the repo already answers (evidence)

| ID | Verdict | Evidence |
|---|---|---|
| A-013 "only two E2E specs" | **Stale** | 12 spec files, 48 Playwright tests, `e2e-run.log` 48 passed |
| A-016 CI never proven | **Confirmed** | `.github/workflows/verify.yml` pushed at `3bcf6d4`; no run observed yet -> verify on the sanitized push |
| A-022 governor uses estimates only | **Partly** | `GatewayTelemetry` records actual `input/output/cached/reasoning_tokens` per call (`model_gateway.py:170-171, 741`); `GrowthGuard` uses actuals; but `ObjectiveBudget` has no `max_tool_calls / max_subagents / max_cost / max_replans` -> add |
| A-023 blind retries | **Already true** | `provider_diagnostics.py` classes TRANSIENT/CAPPED/AUTH/...; `model_gateway.py:786` treats QUOTA/RATE_LIMITED/INSUFFICIENT_CREDIT as non-retry; `provider_cooldowns.py` cooldown |
| A-041 scheduler idempotency | **Already true** | `store.py:99-141`: `lease_owner/lease_token/lease_until`, `idempotency_key` UNIQUE(run_id, key) |
| A-043 selfdev cannot rewrite its judge | **Partly** | `self_upgrade.KERNEL_PATHS` refuses policy/netguard/sensitive_domains/user_policy/self_upgrade/constitution at PROPOSE; **missing**: `trust.py`, `confirmation.py`, `promotion.py`, `evaluation.py`, `honesty.py`, `adversarial.py`, `selfdev.py`, `selfdev_benchmark.py`, `golden.py`, `docs/golden/`, `tests/test_trust.py`, `tests/test_policy*.py`, `.github/workflows/` -> extend |
| A-045 security pack off by default | **Already true** | `security_skills.py:145-148`, `strix_pentest.py:25-28`: `risk="restricted"`, needs `security.authorized_scope`; `test_trust` proves refusal |
| A-038 SQLite concurrency | **Partly** | one shared connection + RLock (`store.py:826-835`), no WAL / busy_timeout pragma, migrations are in-code `_ADDED_COLUMNS`; chaos test covers kill-during-run but not kill-during-write -> add pragmas + test |
| A-042 remote replay | **Partly** | approvals are nonce-bound; `/api/objective` is session-cookie gated with no per-message nonce/timestamp -> add |
| A-028 rules archived | **Confirmed** | 9 rules moved to `.claude/rules-archive/`, 3 active | restore the behaviours as active rules |
| A-014/A-029 Bash runner | **Confirmed** | `scripts/baseline_suite.sh` only | add `scripts/baseline_suite.py` (canonical), `.sh`/`.ps1` wrappers |

## P1 - architecture findings (accepted, sequenced after P0)

A-010/A-018/A-019 provider transport model (API vs SUBSCRIPTION_CLI vs
OAUTH_APP vs LOCAL); A-011/A-020 privacy firewall (data classes +
redaction before routing); A-008/A-024 opt-in live provider suite
(`tests/live/`, `FRIDAY_LIVE_PROVIDER_TESTS=1`); A-009/A-025 live Golden
Journeys; A-026 golden outputs as CI artifacts; A-027 independent
verification runner; A-021 module splits; A-036/037 browser adversarial
pages + profile separation; A-039 memory promotion provenance; A-040
observability payload policy; A-047 per-stage subprocess timeouts; A-048
invariant tests; A-049 cancellation everywhere; A-050 capability status
vocabulary; A-051 soak; A-052/053/054 model eval + `models.yaml` +
semantic fallback; A-031..035 voice benchmarks (needs live room - OWNER
runs, agent instruments).

## Order of execution

1. Sanitize the working tree (untrack + ignore + `.gitattributes`), rotate
   the companion pairing, allowlist the false positives, re-scan - **this
   commit**.
2. Push; watch `verify.yml` run on the remote (A-016).
3. Kernel guard extension (A-043) + SQLite pragmas/crash-during-write test
   (A-038) + remote nonce (A-042) + ObjectiveBudget fields (A-022) +
   canonical Python runner (A-029) + rules restore (A-028).
4. Then the P1 list, verification-against-reality first (live provider
   suite, live golden journeys, browser injection pages, soak).

## Resolution log (2026-09-05, same day)

| finding | commit | what landed | evidence |
|---|---|---|---|
| A-001..006, A-015, A-055, A-056 | `d1751bb` | 88 runtime/artifact/binary paths untracked (files kept on disk), `.gitignore` extended, `.gitattributes` (LF text, CRLF for bat/cmd/ps1, binaries listed), `docs/PRD_V3.1.md` canonical (94 requirement IDs verified present), `scripts/seed_demo_db.py` (42 tables + demo rows), `.serena` / `.aicodepro` / `.docx` out | `git ls-files` after: none of the P0 paths; `git ls-files --eol` 0 `i/crlf` |
| A-007 / A-017 | `d1751bb` | companion pairing **rotated** (new `.pem`/id/token generated by the production `pairing.py` path; extension `manifest.json` key updated; old material moved aside, not deleted); `.gitleaks.toml` allowlists the 12 deliberate test fakes + the MV3 public key; full-history scan with the config: **0 findings** (15 before) | `gitleaks git . --config .gitleaks.toml` -> "no leaks found". History still contains the OLD key/token: they are now worthless (rotated). Push protection is a GitHub-side setting the owner enables (Settings -> Code security). |
| A-007 / A-017, CI step (correction of `aa442ce`) | `aa442ce`, `1d5fd3e` | **My `aa442ce` diagnosis was wrong.** It assumed the CI secret scan was the same full-history scan as the local command and that its finding was the retired `.pem`; on that basis it added a `.gitleaksignore` fingerprint. Reproduced properly (depth-1 clone from the remote, the action's default gitleaks **8.24.3**, the action's exact `detect --log-opts=<push range>` args): the action scans only the PUSHED RANGE on a one-commit checkout, so the `.pem` (commit `5b9cd75`) was never in scope, and its finding was `tests/test_browser_primitives.py:53` - a deliberate fake `sk-…` token the redaction test feeds in, which 8.24.3 flags as `generic-api-key` and the locally installed 8.30.1 does not (rule/entropy changes). Every local "clean" scan was measuring a different tool. Two fixes: the file joins the redaction-test path allowlist (same class as the three already there); and the step no longer uses `gitleaks-action` - it downloads the pinned 8.30.1 binary and runs the AGENTS.md command verbatim (`gitleaks git . --config .gitleaks.toml`) on a `fetch-depth: 0` checkout, so it is a true history scan and local green == CI green. The `.gitleaksignore` entry stays: it is correct for the full-history scan the step now runs (removing it -> exit 1 locally, shown in `aa442ce`), just not what closed the action's range scan. | red-green on the allowlist entry, full history: 8.24.3 exit 0 WITH it / **exit 2 WITHOUT** it; 8.30.1 exit 0 either way (why local never showed it). `test_repo_hygiene` 5 passed (the new path exists, is a test). `verify.yml` parsed: python-job checkout `fetch-depth: 0`, scan step runs the pinned binary. |
| A-016 (CI runs on HEAD?) | `e76c81a`, `094063e` | **Audit was right - the run on `3bcf6d4` had failed** (14 test modules import Windows-only libraries at module level -> ImportError on ubuntu; the log had been unread). Fixed properly, not hidden: `tests/conftest.py` `collect_ignore` for the 14 Windows-native modules on non-win32 with the reason stated; `verify.yml` now a matrix **windows-latest (required) + ubuntu-latest (compatibility)**, pytest-timeout per test, chunked; `pytest-timeout` declared in `[dev]` | run on `3bcf6d4`: FAILED (both jobs). Runs since: superseded by each push (`cancel-in-progress`); the run on the final HEAD of this series is the one that counts - see below |
| A-043 | `7af2c96` | `KERNEL_PATHS` extended to trust roots (`trust.py`, `confirmation.py`, `promotion.py`, `evaluation.py`, `honesty.py`, `adversarial.py`, `selfdev*.py`, `golden.py`, `objective_budget.py`, `docs/golden/`, `tests/test_trust.py`, `tests/test_policy*.py`, `.github/workflows/`); prefix matching (`is_kernel_path`) so a file under a kernel directory is refused; `./` prefix bug found and fixed | `test_selfdev` + `test_self_upgrade` + reachability: 76 passed; each new path refused at PROPOSE |
| A-038 | `7af2c96` | `PRAGMA journal_mode=WAL`, `synchronous=NORMAL`, `busy_timeout=10000`, `foreign_keys=ON` in `Store` | `tests/test_store_durability.py` 4: **real process killed mid-write** -> DB opens, committed rows intact, uncommitted gone; two processes writing concurrently -> no `database is locked`; WAL confirmed |
| A-022 | `8becc2d` | `friday/objective_budget.py`: spend measured from **recorded** usage only (GatewayTelemetry rows, Hermes WorkRunLog per objective, attempts, strategy changes, wall time); `CLASS_LIMITS` max_tool_calls / max_workers / max_replans; engine checks before every call; exhausted -> PAUSED with blocker naming dimension + numbers, `run.budget_exhausted` event, parked task charged nothing | `tests/test_objective_budget.py` 6 with the real engine; objective/continuity/golden regression 184 passed |
| A-042 | `09898f5` | remote `/api/objective` requires one-time nonce + fresh timestamp (`access.check_replay`, 300 s window, bounded seen-set, refusals logged); local Control Room exempt | `tests/test_remote_channel.py` 9: replayed message refused, exactly one run in the ledger |
| A-014 / A-029 / A-047 | `9231ba7` | `scripts/baseline_suite.py` canonical (per-test timeout, per-chunk ceiling, **kill with process tree**), `.sh`/`.ps1` wrappers | chunk_timeout=6 on worktrees+windows -> exit 124 in 6.2 s, **0 surviving python/git**; normal chunk exit 0 |
| A-028 | this commit | the 9 archived rules restored to `.claude/rules/` with `paths:` scopes (token discipline kept: they load when a matching file is touched); `00-team-rules.md` is the index and says the numbered rule wins on disagreement | 12 rule files active, archive removed |
| A-016 again (CI truth on `70176b1`) | `a1b9529` | **The run on `70176b1` (id 33949024905) was red on both jobs** - read, not assumed: windows 46 failed, ubuntu 82 failed + 57 errors. Each bucket root-caused, none skipped blindly: (1) runtime deps `mss`/`send2trash`/`pyyaml` undeclared, CI installs `--all-extras`; (2) `friday/toolsets/audio.py`, `friday/toolsets/windows.py` (pygetwindow raises `NotImplementedError` at import on Linux) and `friday/platform/windows.py` imported Windows-only libraries at module level and took the whole tool registry down - now optional imports, every tool answers UNSUPPORTED off Windows; (3) fresh checkouts materialise gitlinks as EMPTY dirs so `is_dir()` lied about clones - `_skillpack.cloned()` (non-empty) drives health and skips; (4) runner without an audio endpoint / sleep states: `system.volume_*` answer UNSUPPORTED via `NoAudioEndpoint` (E_NOTFOUND) instead of FAILED, `power._suspend` checks refusals before capability; (5) three real defects found by CI: `objective_budget.max_replans` (0/1) undercut `MAX_STRATEGY_CHANGES` (3) so a stuck task parked PAUSED before it could conclude BLOCKED; `ownership.py` freshness windows were inclusive while Windows' clock ticks ~15.6 ms (`age==0` for 1993/2000 stamps); `sandbox`/`execution`/`voice_brain` treated `C:\...` as relative on POSIX and joined it into the workspace; (6) `netguard` tests resolved `example.com` for real and failed when the network dropped mid-run - resolver faked in the deterministic gate, real DNS under `live`. Every regression guard watched red then green | Windows baseline `data/baseline_fix1`: 3,788 passed, 1 skipped, 4 failed - all four are the machine entering Modern Standby mid-run (Kernel-Power 506/507 at 14:03-14:15, `git rev-parse` "timed out after -668 s"); WSL Ubuntu-24.04 gate on a `git archive` checkout (empty gitlinks, like actions/checkout): see the ci-truth commit message for the final numbers |
| A-008 / A-024 (provider health is evidence, not credentials) | `193053d` | `friday/provider_health.py`: verdict per route from the `GatewayTelemetry` ledger only - UNPROBED / HEALTHY / DEGRADED / UNAVAILABLE / STALE (24 h), durable failures (auth, credits, unsupported model, NO_ROUTE, EMPTY_RESPONSE) are UNAVAILABLE with the provider's own text; `ModelGateway.health()` now reports `healthy`/`unavailable`/`providers` beside `usable`; `candidates()` skips UNAVAILABLE routes on failover (a pin still probes them); `probe()` is explicit and paid. `candidates()` resolves each provider's OWN catalog default (`hermes_cli.models` via the worker) - the old diff sent `model=""` and Hermes pre-filled the main model, which is how `claude-opus-5` reached the OpenAI API; no default -> NO_ROUTE. `infer()`: empty visible content is never "ok" - `finish_reason=length` -> OUTPUT_TRUNCATED with one widened retry (>=1024 output tokens), otherwise EMPTY_RESPONSE. Root cause of "Gemini returns empty": `gemini-3.6-flash` is a thinking model and 16 `max_tokens` were spent on hidden reasoning (`completion=0, total=22`); at 256 it answered `pong` | `tests/test_provider_health.py` 24 + `tests/test_model_gateway.py` 25 (each guard red then green); live suite `data/live/providers_latest.json` 20260905T093709Z: **healthy** anthropic, openai-codex, openai-api, nvidia, gemini (after the widened retry); **unavailable** opencode-zen (`CreditsError: No payment method`), opencode-free (`Model laguna-s-2.1-free is not supported`) - both are account facts for the owner, recorded with the reason, not routed to |
| A-036 (prompt-injection pages; PRD Req. 26) | `9747b7f`, `e15ade7` | **Real hole, found by probe before any page was written**: on the UI brain path (`voice_brain.reply()` tool loop) the boss asked "what does this page say about the weather?", the page's text told the model to call `hermes/delegate` "delete all tests and push" and `files/write` - **both went through**, under normal and full autonomy. The owner-words gate (`_asked_for`) existed only on the fabric path; the Friday-own write families (`files/write`, `files/delete`, `hermes/delegate`, `contacts/save`, `desktop/plan`) never reach it. **Same class on the LiveKit/MCP path**, probed after: `FridayAgent.use_capability` dispatches any capability by name and under DANGEROUS autonomy (the default) the policy table answers AUTO for `files_write` and `hermes_delegate` - the fake supervisor received "delete all tests and force-push to main" with no owner words at all. Fix, one table for both paths: `friday/write_licence.py` maps each Friday-own write (UI: family/operation; MCP: capability id - files_write/create/edit/copy/move, files_delete/recycle, hermes_delegate, desktop_takeover/plan, memory_remember) to the words in the owner's turn that license it; `voice_brain._run_capability` checks it FIRST, `agent_friday.use_capability` checks it after resolution and before the ack/dispatch, against `_owner_words` recorded in `read_before_answering` (the seam both speech and typed input pass through). Nothing read is a licence. Suites: `tests/test_injection_pages.py` serves six real HTML pages (`tests/fixtures/injection_pages/`: ignore-previous, hidden text, HTML comment + aria-label, fake system/tool block, ready-made JSON tool call, exfiltration) on loopback through the REAL gated fetch, plays the compromised model and asserts the hostile text was read (guard reached), the write is refused with the "not an instruction from him" reason, no stub was called, and the negative case with the boss's own words dispatches; `tests/test_write_licence_agent.py` does the same six calls through the real `use_capability` with a stub router, plus: reads/control plane never gated, `_owner_words` set before the model sees the turn, both paths share one table by identity | UI: 15 passed; gate removed -> 6 fail (file written / WorkRun created / contact saved / screen captured). MCP: 15 passed; gate removed -> 6 fail. Touched set incl. turn_ownership, ui_server, reachability: 103 passed |
| A-016 closed (first confirmed-green run) | `1d5fd3e` | Run **33967758541** on `1d5fd3e`, 2026-09-05 13:02-13:22Z: `pytest windows-latest` success, `pytest ubuntu-latest` success (every step incl. the pinned-gitleaks history scan; the failure-annotation step correctly skipped), `Playwright (control room)` success. Read from the jobs API step by step, not from the badge. The seven pushes it took today, each red for a different, named reason: `a1b9529` (the 70176b1 buckets), `193053d` (ubuntu: no `$DISPLAY`, masked locally by WSLg), `7254ccb` (annotations, so a red job is readable without repo admin), `ced5727` (headless screen UNSUPPORTED; a test that saved a contact nobody asked for), `aa442ce` (wrong diagnosis of the secret scan - corrected above), `1d5fd3e` (the right one). Not a claim about the next commit: re-verify on the commit you are looking at. | jobs API: 3 jobs, 39 steps, 0 non-success. `friday_ci_watch.py 1d5fd3e` -> `conclusion=success`, exit 0 |

Not done from P0, honestly: **GitHub push protection** (owner-side toggle);
**history rewrite** - deliberately not done: the leaked material is rotated
and the repo has three human commits with one public clone base; rewriting
`main` on a public repo invalidates the owner's and CI's clones for material
that is already worthless. If the owner wants the old blobs gone, the
command is `git filter-repo --invert-paths --path data/companion/` followed
by a force-push and a fresh clone everywhere.
