# FRIDAY Action-Truth Program — Final Report (living; closes when §7 is filled)

Program: ACTION TRUTH + E2E STABILIZATION, master autonomous PRD of 2026-09-20.
Tree: `E:/friday-tony-stark-demo-main` main, base `47d112a`.
Ledger: `docs/implementation/FRIDAY_ACTION_TRUTH_MASTER_LEDGER.md`.
Defects: `docs/implementation/FRIDAY_DEFECT_REGISTER.md`.
Product plan going forward: `docs/product/FRIDAY_JARVIS_MASTER_PRD_v7.0.md`.

## 1. The defect, stated once

Friday lied in both directions because "what happened" lived in five places
(`_acted_this_turn`, `_ran_this_turn`, Hermes completion, `skill_list`
state, objective state). Room M1 (2026-09-20) showed both: three turns of
"still running into the same path restriction" with zero file calls
(fabricated FAILURE), and Hermes's genuine `jarvis-hello.py` refused as an
unbacked claim (denied SUCCESS) — with the refused sentence still going out
on the transcript because the gate sat after LiveKit's tee.

## 2. What was built (all unit-verified; live verification in §5)

| layer | change | proof |
|---|---|---|
| One ledger | `friday/action_evidence.py`: rows per executor (8 executors), claim classes ×6, `Claim.target/operation`, `_correlates` (same deed, same thing), tense carve-out (`history=`), snapshots with `scope` + `authority_level` | `test_action_evidence` 51, `test_evidence_correlation` 16 (5 negative controls) |
| Writers | voice `use_capability` + `function_tools_executed`; UI `_run_capability`; Hermes `tool.complete` with disk read-back; OBJECTIVE_WORKER per step (verified only with reported verification); CLAUDE_CODE per git-listed change read back from disk + run row; direct-action steps; state snapshots from `skill_list` / `capability_families` / `self_model_snapshot` / `ui_families` | `test_evidence_writers` 6 (ghost file FAILED; unverified step backs nothing; outage never fails the run) |
| Fail-closed before the fork | `FridayAgent.llm_node` audits before the tee; `tts_node` re-audits for `say()`; UI `_honest_about_evidence` before `_remember_turn`; audit CRASH → `AUDIT_UNAVAILABLE` hold for any sentence with a claim (was fail-open on both paths) | `test_reply_audit_fail_closed` 9, `test_reply_surfaces_agree` 6 (audio == transcript == log; store == JSON) |
| Exactness | `literals.py` (protected before the sentence split), `known_folders.py` (shell Known Folders; OneDrive-safe), `direct_action.py` (exact file chain through `CapabilityRuntime`, one row per step, per-path lock), planner `target_pinned`/`_spoken_path`, admission declines exact chains | `test_direct_action` 30 + planner/semantics batch |
| Jail | typed `JailError(reason, trace)`; one INFO line per decision (no content); `%USERPROFILE%`/`$HOME`; refusal keeps the path; sibling-prefix / `..` / case / symlink tests | `test_fsjail_observability` 10 |
| Names | `capabilities.ALIASES` (`orchestration_new_objective → objective_start`) resolved in `by_id` / router / runtime; collision plant red; typed `PREREQUISITE_REQUIRED` for `skill_declare_permissions` | `test_capability_aliases` 8 |
| UI turn integrity | one turn at a time (`busy` → 409 → "still on the last one"); per-path chain lock; `ui_families` OBSERVED snapshot backs the surface recital | `test_ui_turn_integrity` 8 |
| Harness truth | `e2e_master.py` six verdict classes, pre-mutation proof, plants; `ui_probe.py` request-matched responses, per-step trace kept on failure only, D-16 preflight (`restart_friday.py --check`, exit 3 on STALE); `baseline_suite.py` host state per verdict + `--resume` (same tree only) | `--self-test` 20/20 red-capable; `test_baseline_suite_runner` 2 |
| Ops | `restart_friday.py` starts the worker in `start` (production) mode; governor refusal typed `RESOURCE_PRESSURE` and self-check 3.3 reads it as skipped-with-reason (D-17) | `test_autonomy_and_selfcheck` 17 |

## 3. Negative controls (each proven RED)

- write of `foo.txt` does not back "I created bar.txt"; a read does not back "I wrote"; last turn's write does not back a present-tense claim; a run finishing is not a file existing; "opened Notepad" is not backed by "opened Start"
- alias collision plant; captured skill does not take the prerequisite branch
- jail log carries no file content; an inside-root path is allowed (refusals are not blanket)
- unverified objective step backs no SUCCESS claim; git-listed file absent on disk is FAILED
- audit outage passes small talk (the hold is not "refuse everything"); the hold passes its own audit
- two direct chains on DIFFERENT files run side by side (the per-path lock is not global)
- a `--resume` on a tree with more edits inherits no green chunk

## 4. Full regression

| run | tree | result |
|---|---|---|
| 1 (`p0_baseline`) | direct-action tree | chunk0 17 F (one cause, fixed), chunk1 987 P, chunk2 host reboot |
| 2 (`p0_baseline2`) | same | chunk0 1380 P, chunk1 1009 P, chunk2 battery shutdown |
| 3 (`p0_baseline3`) | Phases 0–11 + T1 | IN FLIGHT — see §7 |

## 5. Live verification (quiet host, restarted worker) — PENDING

Targeted E2E 3–12 + Hermes evidence case (`e2e_master.py`), UI probe
(`ui_probe.py`), M2 full E2E with product / harness / infra rates. Recorded
here when run; a verdict measured under the suite's own CPU load is INVALID.

## 6. Blockers (truthful)

- Host: Modern Standby laptop, 16 GB, two mid-run deaths today (reboot,
  critical battery). Suite and E2E cannot overlap; E2E needs a quiet host.
  Run 3 chunk1 finished on BATTERY (`ac=NO battery=51%` in its verdict line)
  — if chunk2/3 die the same way, `--resume` keeps chunks 0/1.
- Hermes evidence gap (known, not fixed here): a `terminal` command that
  writes a file (`echo > x`) is not read back — only `write_file` / `patch`
  rows carry disk verification. A claim about such a file is refused until a
  Friday read confirms it; that is the safe direction, recorded as T6 work.
- CI: run id for the final commit — pending push.

## 7. Termination block — PENDING
