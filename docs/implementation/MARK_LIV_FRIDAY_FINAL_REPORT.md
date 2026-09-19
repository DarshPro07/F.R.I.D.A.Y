# F.R.I.D.A.Y. — Mark-LIV / Skill-Intelligence / PRD v6.0 — Final Report

**Worktree** `D:/friday-product-phase1`, branch `product/phase1-context`,
forked from `d7e365d` (the release candidate; untouched).
**Range** `d7e365d..d75e972`, 8 commits, 63 files, +9,343 / −31.
**Authority** PRD v6.0 (`docs/product/FRIDAY_JARVIS_MASTER_PRD_v6.0.md`) §0.1
audit-before-build, §0.2 evidence rule, §102 termination contract.
**Live ledger** `docs/implementation/SKILL_INTELLIGENCE_LEDGER.md` (one row per
requirement, statuses NOT_AUDITED → … → VERIFIED with the evidence inline).

This report says what changed, what is proven, what is deferred and why, and
what is not done. Nothing below is marked VERIFIED without a test count, a
plant count and a gate result behind it; "code exists" is not verification.

---

## 1. Final readiness verdict

| Contract field | Verdict | Basis |
|---|---|---|
| VOICE_RUNTIME | **PARTIAL** | LiveKit path live and gated (`agent_friday.py`); UI path has echo-cancellation + barge-in + interruption truncation (FR-039). EchoGuard on real hardware (ML-18), audio-device probe (ML-19/20), PTT + local wake word (ML-15/16), transcript dedup (ML-23): NOT verified this package — they need microphone/speaker measurement on the owner's machine, not a test process |
| ACTION_TRUTH | **VERIFIED** | `honesty.audit` on both conversational paths; `contracts.succeeded` requires `Verification`; `Store.finish_objective_run(COMPLETED)` refuses without evidence rows; new `contracts.WAITING` is non-terminal and not CLAIMABLE (plant red) |
| UNDO | **VERIFIED** | `friday/action_journal.py` + `files_undo` — before-state captured never guessed, conflict-safe, read-back verified; 19 tests on the real jail, 9 plants red/restored, live MCP probe |
| CONFIRMATION | **VERIFIED** | `confirmation.Book` binds nonce → exact action (pre-existing, test_confirmation 17); a skill manifest cannot widen anything (GB-13, 36 tests, 9 plants); an event cannot forge a wake (`/api/event` gate + A-042 replay, plants red) |
| SELF_KNOWLEDGE | **VERIFIED** | `friday/self_model.py` — every capability claim in BOTH prompts comes from runtime state; static "look at his screen or through the camera" removed; golden journey camera-off→truthful→on passes; 15 tests, 7 plants |
| HUD | **DEFERRED** | Reactive HUD / avatar: the Control Room (`ui/index.html`) already renders live state (island, transcript, memory, vision); Mark-LIV's HUD layer and avatar are not adopted — the PRD lists them as optional and nothing in the tree needs them. Explicitly deferred, not silently skipped |
| PLUGIN_RUNTIME | **PARTIAL** | The Capability Fabric IS the plugin model (pinned upstreams, `risk`, `permissions`, UNAVAILABLE-never-breaks-boot); skill permission manifests enforced at three seams (GB-13). Missing: a plugin *lifecycle* (install/quarantine/promote for non-skill plugins, ML-28..30) beyond fabric's crash isolation |

Deterministic regression suite on `d75e972`: see §7 — the four-chunk canonical
run, exit codes not tails.

---

## 2. Architecture changes (what is different in the tree)

All additions sit on existing seams. No second registry, bus, scheduler,
memory or approval path was created (NON_NEGOTIABLES; PRD §0.1).

| Area | Change | Seam it extends |
|---|---|---|
| Skill intelligence | `skill_fingerprint.py` (FR-012): declared dependencies hashed at validation; a code change moves only the skills whose declared inputs moved (`NEEDS_REVALIDATION`); README typo invalidates nothing | `skill_ladder.SkillLadder` (same SQLite, new table + state) |
| | `skill_permissions.py` (GB-13): a manifest *requests* scope, never grants; `Authority` = worker ∩ skill ∩ objective ∩ user policy; R4 never permitted; grant-shaped tokens refused in keys AND values | `CapabilityRuntime.execute` (before privacy/policy), `ClaudeCodeExecutor.launch_for/execute` (CLI allowlist), `HermesSupervisor.delegate` (prohibitions into the contract) |
| | `skill_behavior.py` (GB-16): deterministic detectors over a `contracts.Run` trace; supportive/neutral/competing strictness; a failed step never satisfies a later `after_step`; INCOMPLETE unless all three ran; reports redacted (home path, secrets) | native; concept from ECC `skill-comply`, which is quarantined |
| | `toolsets/skills.py`: the seven skill/self-model tools now return verified `ActionResult`s so objectives can call them (reach 172/200; the 28 unresolved are the declared adapter-only set) | `capability_runtime` resolution |
| Self-knowledge | `self_model.py` (ML-05): snapshot = modalities (probe, "snapshot only, never a live feed") + `fabric.family_report()` + `provider_health.assess` (no ledger = UNPROBED) + durable operator switches; injected into `voice_brain._persona` and `agent_friday.build_instructions`; `self_model_snapshot/switch` tools | both prompt assemblers |
| Reversibility | `action_journal.py` (ML-09/10) + `files_actions/undo/wait`; wired into `files.write/create/edit/move`; `action_journal.py` + its test in `KERNEL_PATHS` | `toolsets/files.py`, `fsjail` (undo re-resolves target and move source) |
| Proactive runtime | `contracts.WAITING`; `RunStatus.WAITING_EVENT` ∈ `RUN_WAITING_STATUSES`; `FailureKind.EVENT_REQUIRED`; `continuous.deliver_event` (exact-key match, payload redacted, `event.delivered/unmatched/expired` in the trace); driver-tick sweep expires deadlines and delivers `file` waits; `POST /api/event` (session gate + A-042); `files_wait` first source | `continuous.ContinuousTaskExecutor` wait machinery (driver loop, watchdog, invariant unchanged) |
| Hardening found by the suite | `agent_friday.refresh_briefing` survives a router-less agent; `build_identity._git` 5 s → 30 s (rev-parse under load returned `unknown` → STALE); `conftest` keeps tests out of the owner's action journal like it keeps them out of `ada.sqlite3` | — |

Mapping documents written before building: `docs/product/PROACTIVE_RUNTIME_MAPPING.md`
(FR-100..106: what existed under other names, the one real gap).

---

## 3. Adopted concepts (clean-room, native)

| Source | Concept | Where it landed |
|---|---|---|
| Mark-LIV brief §9–10 | Undo journal with captured before-state, conflict check, read-back | `action_journal.py` |
| Mark-LIV brief §5 / PRD ML-05 | Runtime self-model instead of static prompt claims | `self_model.py` |
| Gawkbot #1068 | Declarative skill permission manifests, broker-enforced | `skill_permissions.py` (closes the hole upstream left open: free-text skill ran with the invoker's whole tool surface) |
| Gawkbot ARCHITECTURE | Push, not poll; durable task state; scoped per-agent tools | `deliver_event` + WAITING_EVENT; `TaskBundle.skills`; `Authority` |
| ECC `skill-comply` | Behavioural compliance grading under three strictness levels | `skill_behavior.py` — with the two upstream defects fixed (LLM classification → deterministic detectors; failed step could supply evidence → never) |
| ECC `operator-approval-loop` | Epoch-keyed decisions, immutable approval snapshot, delivery claims | **reference only** for `confirmation.Book` hardening in Phase 4; not built now (one authority path stays: `PolicyEngine` → `confirmation.Book`) |

## 4. Rejected / deferred concepts (with reason)

| Concept | Disposition | Reason |
|---|---|---|
| Mark-LIV as architecture / second brain | REJECTED | NON_NEGOTIABLE 1–2: Friday stays the control layer, Hermes the execution engine |
| ECC bulk install (plugin, 292 skills, hooks, `gateguard`, `continuous-learning-v2`) | REJECTED | NFR-001 bloat; FR-192 untrusted folders → QUARANTINED; second promotion ladder with no per-skill grants (re-opens #1068); node hooks add 5 s timeouts per Bash/Write/Edit; its `verification-loop` is weaker than `scripts/release_gate.py`. README's `install.sh --target hermes` path does not exist |
| Mark-LIV HUD / avatar | DEFERRED | optional per PRD; Control Room already renders live state; no consumer in the tree |
| F: drive portable-brain contract | DEFERRED | owner's instruction ("leave the F: drive portable-brain contract part") |
| Skill publication gates (FR-013), learn-by-demonstration (FR-015) | DEFERRED_WITH_REASON | no publication target exists; PRD marks FR-015 "future workflow" |
| A general pub/sub event bus with topics/subscriptions | REJECTED | the ledger is `objective_events`, the clock is `next_wake`, the identity is the session gate; a second bus duplicates all three |

## 5. License findings

| Upstream | License | Handling |
|---|---|---|
| affaan-m/ecc @ `07756cee15788a54506031462794ad645719b028` | MIT | Two skills + LICENSE in `third_party/quarantine/ecc/` with per-file sha256 (`QUARANTINE.json`), state QUARANTINED; outside every skill loader root (Hermes `HERMES_HOME/skills` + `skills.external_dirs`, Claude Code `.claude/skills`); `tests/test_skill_behavior.py::TestQuarantine` pins location, hashes and "no production module imports from it". Nothing activated |
| Mark-LIV (FatihMakes) | not cloned | ideas only, clean-room; no source in the tree |
| Gawkbot | not cloned | architecture document + issue #1068 read; no source |
| Hermes, LiveKit, existing pinned upstreams | unchanged | `scripts/upstream_lock.py --check` and `integration_matrix.py --check` pass on the committed tree |

## 6. Files changed (`d7e365d..d75e972`, quarantine excluded)

Added: `friday/action_journal.py`, `friday/self_model.py`, `friday/skill_behavior.py`,
`friday/skill_fingerprint.py`, `friday/skill_permissions.py`, `friday/toolsets/skills.py`,
`docs/product/FRIDAY_JARVIS_MASTER_PRD_v6.0.md`, `docs/product/PROACTIVE_RUNTIME_MAPPING.md`,
tests `test_action_journal.py`, `test_event_waits.py`, `test_self_model.py`,
`test_skill_behavior.py`, `test_skill_fingerprint.py`, `test_skill_permissions.py`,
`test_skills_toolset.py`.

Modified: `agent_friday.py`, `friday/build_identity.py`, `friday/capabilities.py`,
`friday/capability_router.py`, `friday/capability_runtime.py`, `friday/continuous.py`,
`friday/contracts.py`, `friday/executors/claude_code.py`, `friday/hermes_bridge.py`,
`friday/objectives.py`, `friday/policy.py`, `friday/runstate.py`, `friday/self_upgrade.py`,
`friday/semantics.py`, `friday/tools/file_control.py`, `friday/tools/vnext_control.py`,
`friday/toolsets/files.py`, `friday/ui_server.py`, `friday/voice_brain.py`,
`tests/conftest.py`, `docs/implementation/SKILL_INTELLIGENCE_LEDGER.md`.

Plus 27 files under `third_party/quarantine/ecc/` (read-only reference).

## 7. Tests

Per item (all on real `Store`/ladder/jail, no stubbed trust boundary):

| Item | Tests | Plants red / restored byte-identical | Focused gates |
|---|---|---|---|
| FR-012 fingerprints | 16 | 4 | 105 + 292 |
| GB-13 permissions | 36 | 9 | 135 + 263 + 213 |
| GB-16 behaviour evaluator + quarantine | 17 | 5 | 305 + 223 |
| ML-05 self-model | 15 | 7 | 268 + 176 + 107 |
| ML-09/10 action journal | 19 | 9 | 283 + 210 + 50 |
| FR-100/104 / GB-35 event waits | 12 | 11 | 158 + 394 + 115 + 59 |
| reach / toolset (ec35920) | 15 | — | 141 + 357 |

Canonical full suite (`scripts/baseline_suite.py`, 4 chunks, marker
`not live and not slow`, verify venv, basetemp on D:):

- On `02fe012` (before the fix commits): chunks 0/1/3 exit 1, chunk 2 exit 0.
  Root causes, all fixed at the root: (a) seven new tools MCP-only → reach 165/200
  (`ec35920`); (b) `refresh_briefing` unguarded attribute reads (`ec35920`);
  (c) `build_identity` 5 s git timeout under load (`ec35920`); (d) `PYTEST_ADDOPTS
  --basetemp` in MY launcher environment leaked into the child pytest of two
  meta-tests (invariant plants, self_upgrade) — a harness fault, reproduced
  poisoned=fail / clean=pass, launcher fixed; (e) the transient
  `third_party/upstream/ecc-quarantine` directory (my duplicate import, since
  removed) tripping the integration matrix.
- On `d75e972`: **4 chunks, every chunk exit 0, runner exit 0** —
  1,300 + 884 + 1,159 + 928 = **4,271 passed, 72 skipped, 0 failed**
  (59 deselected by the marker), 2026-09-19 19:29 IST, Windows, verify venv.
  Summary preserved at `docs/evidence/suite/baseline_d75e972_summary.txt`;
  chunk logs at `D:/friday-test-tmp/baseline_out_d75e972/`. Read from the
  runner's exit codes, not the tail of a pipe.
- `scripts/integration_matrix.py --check`: 46 clones, all classified.
  `scripts/upstream_lock.py --check` reports "drifted" in this worktree only
  because the worktree has empty gitlink directories (no upstream clones
  checked out); on the main tree with the clones it reports "matches", and
  `tests/test_upstream_lock.py` is green in chunk 3 above.
- Remote CI: not run for this branch (unmerged by design; one push per verdict
  when it merges).

## 8. Real-hardware validation

- Self-model probe on this host: verify venv reports camera UNAVAILABLE (cv2
  absent), live venv reports AVAILABLE — the model tells the truth per environment.
- Action journal live probe through the MCP adapter on the real jail: write →
  undo REVERSED ("read back 9 bytes sha == before"), disk shows the original;
  user edit in between → CONFLICT, disk untouched.
- `files_wait` end-to-end in-process: park → file appears → driver tick delivers →
  task re-runs → run COMPLETED.
- NOT done on hardware (needs the owner at the machine): EchoGuard, audio-device
  hot-swap, PTT, wake word, `Friday.exe` relaunch, sleep/resume of a parked run.

## 9. Performance / voice metrics

- No per-turn cost added: self-model is refreshed with the briefing (not per turn);
  the event sweep runs inside the existing 50 ms driver tick over ≤100 runs;
  journal capture is one stat + one hash per mutating file op (bounded by
  `MAX_BACKUP_BYTES`, 64 MB default; above it the op is journaled irreversible).
- Voice metrics (time-to-first-word, barge-in latency) were not re-measured;
  nothing in this package touches the TTS/STT path.

## 10. Known limitations

- Event kinds `email/ci/webhook/booking/message/calendar` have a door
  (`/api/event`) but no connector yet feeds it — Phase 4/6 adapters.
- `files_wait` watches by existence, not content; a partially-written file
  counts as arrived (the re-run's read-back still reports the size).
- Undo covers create/write/edit/move; copy and recycle reversal are the named
  next extension in `ActionJournal.reverse`.
- `skill_behavior` grades traces handed to it; the harness that runs a worker
  three times and collects traces is the caller's (Hermes/Claude bundle).
- The self-model's provider routes reflect the health ledger; with no probes
  recorded they say UNPROBED, which is honest and also uninformative.

## 11. External blockers

- ML-18 EchoGuard, ML-19/20 audio devices, ML-15/16 PTT/wake word: real
  microphone/speaker measurement on the owner's machine.
- FR-090..094 phone operator: no SIP provider credentials.
- Hermes gateway `session.create` offers no per-session tool allowlist, so GB-13
  is enforced on the Friday side (prohibitions in the contract) rather than at
  the Hermes boundary.

## 12. Remaining recommendations (next package, in order the tree justifies)

1. Connectors for the event door (email first — it unblocks FR-070..074).
2. Plugin lifecycle over the fabric (ML-28..30): QUARANTINED → PROMOTED with the
   same red/green discipline the skill ladder has.
3. `confirmation.Book` epoch/content-hash binding (from the ECC approval-ledger
   reference) before Phase 4 comms can send anything.
4. Hardware session with the owner for the voice rows (ML-15/16/18/19/20/23).
5. Merge `product/phase1-context` into the candidate only after its own gate
   result is preserved and CI on the merge commit is green (one push per verdict).
