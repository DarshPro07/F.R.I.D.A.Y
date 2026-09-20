# FRIDAY Action-Truth Master Ledger

Program: ACTION TRUTH + E2E STABILIZATION (master autonomous PRD, 2026-09-20).
Rule: VERIFIED needs objective evidence (a test run with exit code, a read-back, a
ledger row, a CI run id). Code existing is not verification.

## 1. Repository truth at program start (2026-09-20 ~10:00 IST)

| item | value |
|---|---|
| root | `E:/friday-tony-stark-demo-main` (`git rev-parse --show-toplevel`) |
| branch | `main` |
| HEAD | `47d112af289ab34e0bc2a7bbbe482ec0e261383b` (2026-09-20 09:46 +0530) |
| dirty | M `agent_friday.py`, `friday/fsjail.py`, `friday/planner.py`, `friday/semantics.py`, `friday/voice_brain.py`; ?? `friday/direct_action.py`, `friday/known_folders.py`, `friday/literals.py`, `tests/test_direct_action.py` (Phase 0 work) plus untracked data/evidence dirs |
| worktrees | `D:/friday-product-phase1` (3a8091c, closed), `D:/friday-soak-fix` (903d2e1), `.claude/worktrees/agent-a04eb2e659394db57` (99dd904) |
| test interpreter | `.venv-verify/Scripts/python.exe` 3.11.15; `friday.__file__` = `E:\friday-tony-stark-demo-main\friday\__init__.py` (this checkout) |
| live interpreter | `.venv/Scripts/python.exe` 3.11.15 |
| live processes | MCP `server.py` pid 12608 (:8000, started 09-19 20:34); UI `run_ui.py` pid 16764 (:8770); voice worker `agent_friday.py start` pid 28172 (09-19 23:51, runs code OLDER than 47d112a); Hermes gateway (friday profile) pid 16624; `friday.tools.execution_bridge` pid 17120 |
| database | `data/ada.sqlite3` (live, never touched directly) |
| host | C: 36 GB free, D: 67 GB free, RAM 1.3 GB free of 16 GB |
| stale lock | `.git/index.lock` 0 bytes 09:58, no git.exe running -> stale |
| E2E harness | `scripts/e2e_master.py` (six verdict classes, self-test) committed in 47d112a |
| last full suite | `d75e972` 4,271 passed / 72 skipped / 0 failed (before 42c29fc..47d112a) |
| M1 evidence | `D:/friday-test-tmp/e2e_M1.json`; repo-root artefact `friday-jarvis-test-txt-on-my-desktop.txt` (59 bytes, 00:20, content "Created by Friday for: Create jarvis-test.txt on my Desktop") = planner invented name AND content AND the bare name resolved against cwd (the repo root), which is why step 5's Desktop path read "outside the permitted roots" |

## 2. Requirements

Statuses: NOT_AUDITED, EXISTING, PARTIAL, BROKEN, MISSING, IMPLEMENTING, TESTING,
VERIFIED, BLOCKED_EXTERNAL, REJECTED_WITH_REASON.

| ID | title | status | implementation | gap | tests | evidence | commit | next |
|---|---|---|---|---|---|---|---|---|
| AT-00 | Phase 0: full deterministic suite on the direct-action tree, then commit as one unit | TESTING | run 1 (`p0_baseline`): chunk0 17 failed = one cause (test stand-ins lacked `act_directly_first`; fixed with `getattr`), chunk1 987 passed, chunk2 killed by host reboot 14:29 (Kernel-Power 41). Run 2 (`p0_baseline2`): chunk0 1380 passed, chunk1 1009 passed, chunk2 exit 1073807364 at 15:31 = "Critical Battery Trigger Met" shutdown (Kernel-Power 524) with 555 MB free. Runner now records host state per chunk and has `--resume` (same tree only, digest-checked). Run 3 (`p0_baseline3`, proc_8e409143b65b) on the full Phase 0-11 tree in flight | run 3 verdict | `tests/test_baseline_suite_runner.py` 2 | summaries in `D:/friday-test-tmp/p0_baseline*/summary.txt` | - | read exit codes, commit |
| AT-01 | Harness: six verdict classes, step-6 pre-mutation proof, negative-control plants, self-test | VERIFIED | `scripts/e2e_master.py` | none known | `--self-test` exit 0 | self-test run 2026-09-20 | 47d112a | rerun on live steps 3-12 (AT-12) |
| AT-02 | ActionEvidenceLedger across executors + claim classifier + audit | VERIFIED (unit) / TESTING (live) | `friday/action_evidence.py`; writers in `agent_friday.use_capability`, `record_tool_evidence` (function_tools_executed), `voice_brain._run_capability`, `hermes_bridge` tool.complete + terminal, state snapshots from `skill_list`/`capability_families`/`self_model_snapshot` | live-run evidence not yet observed | `tests/test_action_evidence.py` 51 | 51 passed (basetemp ae3) | 47d112a | AT-05 correlation tightening; live rows in AT-12 |
| AT-03 | Fail-closed reply audit before the LiveKit fork | VERIFIED (unit) | `FridayAgent.llm_node` audits before tee; correction re-audited | live proof | `tests/test_reply_audit_fail_closed.py` | green in 135-test batch (basetemp ae8) | 47d112a | AT-12 voice artefacts |
| AT-04 | Literal preservation + Known Folders + direct action + admission decline + planner fidelity | TESTING | `friday/literals.py`, `friday/known_folders.py`, `friday/direct_action.py`, `agent_friday.act_directly_first`, `voice_brain._act_directly`, `planner._spoken_path`/`target_pinned`/`_ENDS_WITH_DETERMINER`, `semantics` verbs + 3rd-person, `fsjail.default_roots` + shell folders | full-suite verdict (AT-00) | `tests/test_direct_action.py` 30 + planner/semantics/routing/reachability batch 234 | 234 passed (basetemp da3); on-disk probe create->read "version one", overwrite->undo->read "version one" | uncommitted | commit after AT-00 |
| AT-05 | Evidence correlation: claim must match target/operation/turn, not "any recent success"; 5 negative controls | TESTING | `Claim.target/operation`, `_correlates`, `_same_target` (file name / last component / host), `audit_claims(history=)` tense carve-out; `honesty` gained interaction verbs (clicked/typed/pressed...) | full-suite verdict | `tests/test_evidence_correlation.py` 16 (5 negative controls) | 16 passed; 78-test batch green (ew1) | uncommitted | commit |
| AT-06 | Claim auditor rules per class | TESTING | six classes; PERCEPTION via `honesty.unbacked_perception`; COMPLETION via `_backs_success` | objective verifier row for COMPLETION folded into AT-08's OBJECTIVE_WORKER rows | `tests/test_action_evidence.py` 51 | green | uncommitted | - |
| AT-07 | Authoritative state snapshots (scope, authority_level, TTL) | TESTING | `CapabilityStateSnapshot.scope/authority_level`, `AUTHORITY_OBSERVED/DECLARED/INFERRED`, `authoritative` property, schema migration `_ADDED_SNAPSHOT_COLUMNS`; `ui_families` snapshot from `voice_brain._surface()` (D-15) | - | `tests/test_evidence_correlation.py`, `tests/test_ui_turn_integrity.py` (stale snapshot does not back) | green | uncommitted | - |
| AT-08 | Hermes/Claude/objective executors publish normalized evidence | TESTING | `continuous._ledger_evidence` -> OBJECTIVE_WORKER row (verified only with a reported verification+evidence); `claude_code.finish` -> one CLAUDE_CODE row per git-listed change READ BACK from disk + one run row; Hermes rows already carry disk read-back (47d112a) | BROWSER/DESKTOP writers = T4 | `tests/test_evidence_writers.py` 6 (ghost file FAILED; unverified step backs no claim; ledger outage never fails the objective) | 6 passed | uncommitted | - |
| AT-09 | Fail-closed output on every surface | TESTING | audit crash -> `AE.hold_if_claim` (`AUDIT_UNAVAILABLE`) on both paths instead of pass-through (was fail-OPEN: "answer passed unaudited"); room: `llm_node` audits before the tee, `tts_node` re-audits; UI: `_honest_about_evidence` before `_remember_turn` | - | `tests/test_reply_surfaces_agree.py` 6 (audio == transcript == log; true claim untouched; outage holds claim, passes small talk; hold passes its own audit; UI store == JSON) | 61-test batch green (rs1) | uncommitted | - |
| AT-10 | File jail observability | TESTING | `JailError(reason, trace)`, `REASONS`, one INFO line per decision (raw/expanded/resolved/matched_root/decision/reason/roots, no content), `%USERPROFILE%`/`$HOME` expansion, `files._safe` keeps the path + reason in the refusal | - | `tests/test_fsjail_observability.py` 10 | 70-test batch green (fj2) | uncommitted | - |
| AT-11 | Containment correctness | TESTING | sibling-prefix (`Desktop-old`), `..` traversal, case variant (Windows), env spellings, symlink escape, typed reasons distinct | - | same file | green | uncommitted | - |
| AT-12 | Objective alias + collision test + search example | TESTING | `capabilities.ALIASES` + `canonical_id()`; resolved in `by_id`, `Router.invocable`, `CapabilityRuntime.execute` (run/policy/evidence carry the canonical id); `objective_start` intent examples name the waiting objective | - | `tests/test_capability_aliases.py` 8 (collision plant proven red) | 61-test batch green (al1) | uncommitted | - |
| AT-13 | `skill_declare_permissions` typed `PREREQUISITE_REQUIRED` | TESTING | NOT_PERMITTED with `error_type=PREREQUISITE_REQUIRED`, `prerequisite=skill_capture`, `next_call` | - | same file (captured skill does not take the branch) | green | uncommitted | - |
| T1-D13 | UI brain one-turn-at-a-time + per-path chain lock | TESTING | `voice_brain.reply` non-blocking `_TURN_LOCK` -> `busy` -> `/api/ask` 409 -> page toast "still on the last one"; `direct_action.run` locks per resolved target path (sorted, no inversion) | - | `tests/test_ui_turn_integrity.py` 8 (same file serializes; different files run side by side; 409 surface) | 134-test batch green (ti1) | uncommitted | - |
| T1-D14 | `ui_probe.py` trace-on-failure | DONE | per-step `tracing.start_chunk`/`stop_chunk`; chunk kept only when the step failed / 4xx+ / error / >80% of the wait | - | syntax check | - | uncommitted | next probe run |
| AT-14 | Targeted E2E steps 3-12 + Hermes evidence case on a RESTARTED worker | NOT_AUDITED | `scripts/e2e_master.py --steps ...` | needs AT-04..13 landed and worker restart (old pid 28172) | - | - | - | Phase 12 |
| AT-15 | M2 full E2E with separated product/harness/infra rates | NOT_AUDITED | - | - | - | - | - | Phase 13 |
| AT-16 | Full regression after M2, CI run read for the exact commit | NOT_AUDITED | `scripts/baseline_suite.py`; `ci_watch.py` | - | - | - | - | Phase 14 |
| AT-17 | Final report `docs/implementation/FRIDAY_ACTION_TRUTH_FINAL_REPORT.md` | NOT_AUDITED | - | - | - | - | - | end |

## 3. Research decisions

| question | source | current | alternative | decision |
|---|---|---|---|---|
| Where is the owner's Desktop on Windows? | `SHGetKnownFolderPath` (shell32, KNOWNFOLDERID FOLDERID_Desktop) | `Path.home()/"Desktop"` guess | shell query with profile fallback | shell query, cached, env override for tests (`friday/known_folders.py`) |
| Path containment | Python `pathlib.PurePath.is_relative_to` (3.9+) vs string prefix | `is_relative_to` after `resolve()` | `os.path.commonpath` | keep `is_relative_to` (component-wise, no sibling-prefix hole); add the variant tests that prove it |
| Where does the reply audit belong in LiveKit? | livekit-agents `voice/agent_activity.py` (`llm_node` -> tee -> `tts_node` / `transcription_node`); re-read 2026-09-20 at docs.livekit.io/agents/logic/nodes (llm_node yields str/ChatChunk; transcription_node "part of the forwarding path"; tts_node the "single synthesis choke point for both generate_reply and say()") | audit in `llm_node` before the tee; `tts_node` re-audits for `say()` | audit in both leaves | before the tee (one audit, one truth) - done in 47d112a; the `say()` path is covered by the tts_node gate |
| Should the audit fail open or closed when it cannot run? | Anthropic "Building effective agents" (ground truth from the environment each step) and "Harness design" (evaluator separate from generator; "Claude ... talk[s] itself into deciding [issues] weren't a big deal") | fail-OPEN: `except: return answer` on both paths | hold claims, pass conversation | fail-CLOSED with `AUDIT_UNAVAILABLE` for any sentence that carries a claim; small talk passes (AT-09) |
| Playwright tracing in the probes | playwright.dev/python tracing API: `tracing.start` once, `start_chunk`/`stop_chunk(path=)` per step; always-on tracing is expensive | trace always on, one zip per run | per-step chunk, keep only failing chunks | per-step chunks kept on failure/anomaly only (`ui_probe.py`) |
| E2E eval shape | Anthropic "Demystifying evals for AI agents": regression evals ~100%, 20-50 tasks from real failures, isolated trials (no shared state inflating results), read the transcripts, 0% = broken task | 16-step master prompt from M1 failures; harness pre-mutation proof; six verdict classes | - | keep; add the Hermes-evidence case and the UI probe as the second surface |
| Suite runs on this host | Windows System log: Kernel-Power 41 (14:29 reboot, dirty shutdown), Kernel-Power 524 "Critical Battery Trigger Met" (15:31) | a chunk that dies takes the run with it | `--resume` on the same tree; host state in every verdict line | done (`baseline_suite.py`); keep the laptop on AC for suite runs |

## 4. Log

- 2026-09-20 09:46 `47d112a` committed (AT-01/02/03).
- 2026-09-20 ~10:05 program start; truth recorded above; Phase 0 suite launched.
- 2026-09-20 10:40 run 1 chunk0: 17 failures, one cause (stand-ins without `act_directly_first`) -> `getattr` seam.
- 2026-09-20 14:29 host rebooted (Kernel-Power 41) mid chunk2; Friday processes restarted in production mode (MCP `server.py`, UI `run_ui.py --password`, voice worker `agent_friday.py start`).
- 2026-09-20 15:00-15:09 live probes: `scripts/ui_probe.py` probe B (7 steps, PIN via `--password` mode, CPU 100% from the suite) -> D-13 found; `fabric_probe.py` over all 32 providers (20 READY / 2 DEGRADED / 9 UNAVAILABLE with named causes).
- 2026-09-20 15:31 run 2 chunk2 killed by "Critical Battery Trigger Met" shutdown; laptop back on AC 15:42.
- 2026-09-20 15:45-17:10 Phases 1-11 + T1 built and unit-verified (AT-05..13, D-13/D-14/D-15, runner `--resume`); run 3 launched on the full tree.
