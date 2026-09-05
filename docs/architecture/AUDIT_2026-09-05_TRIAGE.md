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
