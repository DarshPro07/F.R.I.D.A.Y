# CODEBASE_PRIMER � Index

_Generated 2026-09-23 from PRIMER_A/B/C. Parts concatenated unchanged below. No source edits; part files unmodified._

## What each part covers

- **Part A � PRIMER_A_core.md**: top-level `friday/*.py` + roots (`server.py`, `agent_friday.py`, `main.py`, `start.py`).
- **Part B � PRIMER_B_services.md**: services � `friday/toolsets/` + `friday/executors/` + `friday/fabric_adapters/` with cross-cutting conventions.
- **Part C � PRIMER_C_tests.md**: `tests/` + `scripts/` map � golden oracles, verify scripts, per-file coverage, honest gaps.

## File counts (from part headers)

- A: **136 files** (132 `friday/*.py` + 4 roots).
- B: **76 files** (7 executors + 35 fabric_adapters + 34 toolsets).
- C: **291 tracked** (224 tests + 67 scripts); 22 untracked; **313 .py on disk**.
- Primer scope total: 136 + 76 + 291 = **503 tracked files**.

## 15 deepest findings (across parts)

1. Face gate locks everything until owner verified; enforced server-side, audited (A: access).
2. Confirmation binds one yes to one exact action fingerprint; non-reusable, non-retargetable (A: confirmation).
3. Learned user model never grants authorization; preference != permission (A: policy).
4. Banking/financial pages never enter model context; secrets redacted pre-exposure (A/B: sensitive_domains, browser_capability).
5. Secrets exist as aliases only; raw values never in prompts/logs/memory/Hermes context (A: secret_broker).
6. 74 tools (~22.7k schema chars) caused Gemini empty completions; router exposes ~dozen tools (A: capability_router).
7. Toolsets return ActionResult with read-back verify; success without observation = PARTIAL (B: conventions).
8. Hermes is mandatory execution engine over JSON-RPC stdio; Friday stays manager only (A/B: hermes_bridge).
9. Worktree is version control, not containment; real boundary = JobObject + allowlist egress + redacted env (A: execution/sandbox).
10. Read-material text authorises nothing; provenance gates destructive reach; owner words licence writes (B/C: action_chain, write_licence).
11. Memory rows never trimmed; supersede never delete; recall is query, not paste (A: store/profile).
12. Fabric: copyleft = isolated modes only; every upstream pinned; absent = UNAVAILABLE, never boot-break (A/B: fabric).
13. Claude CLI prompt goes on stdin, never argv; bypassPermissions always refused (B: executors/cli).
14. Power accept = INITIATED not SUCCEEDED; settled later by boot-identity reconcile (B: toolsets/power).
15. Goldens demand verified ActionResult evidence; live-provider paths are @live/skipped, proven by golden runs (C).

---
# PRIMER A — Core modules (top-level friday/*.py + roots)

_Source: `git ls-files` tracked tree; 136 files (132 friday/*.py + server.py, agent_friday.py, main.py, start.py). Generated 2026-09-23. No source edits made; big files paged via offset/limit reads._

Entry points: `start.py` launches `server.py` (FastMCP :8000/sse) then `agent_friday.py dev` (LiveKit voice); `main.py` is a stub. `server.py` registers tools+prompts+resources; voice agent consumes MCP as toolset.

## friday\__init__.py (1 lines)
Role: Friday MCP Server Package
Flows: constants/re-exports only
Gotchas: —

## friday\access.py (409 lines)
Role: friday/access.py -- the face gate. Nothing works until the owner is seen. The rule the owner set: until his face is verified, Friday is locked -- no chat, no tasks, no Hermes -- and everything that touches the locked system is logged. This module enforces that on the SERVER, so the lock is real even if the page is bypassed: * recognition happens in the browser (face-api descriptors, 128 floats); *
Flows: classes: GateMiddleware(__init__, __call__) | funcs: _now, log, recent_log, _valid, load_owner, enrolled, enrol, _hash_pin, has_pin, set_pin, note_camera_hold, camera_held_by_friday, pin_allowed, verify_pin, distance | imports: from __future__ import annotations; import hashlib; import hmac; import json; import logging; import math
Gotchas: env-driven, secret-sensitive

## friday\adversarial.py (404 lines)
Role: Adversarial reasoning (PRD v3.1 FR-008 contrarian decision mode, FR-012 independent verification worker). Two things, one mechanism: * `deliberate(question, evidence)` - a panel of five fixed roles (proposer, contrarian, failure analyst, evidence checker, judge), each one bounded inference through the Hermes MODEL_GATEWAY (no agent loop, no tools). The output keeps every role's position verbatim, 
Flows: classes: Position, Decision(consensus, to_dict), ChangeUnderReview, ReviewEvidence(independent, to_dict) | funcs: gateway_infer, parse_tagged, confidence_of, cited, _evidence_block, _ask, deliberate, _addressed, independent_review | imports: from __future__ import annotations; import logging; import re; from dataclasses import dataclass, field; from typing import Callable
Gotchas: confirmation-gated

## friday\answer.py (234 lines)
Role: Deciding how hard to think about a question, before answering it. The thing Friday is actually for. Every other subsystem here - the durable runs, the capability registry, the policy engine - exists so the boss can stop opening ChatGPT, and none of them help with the commonest request of all, which is a question. Until now every question took one path: a single Gemini turn with seventeen tools att
Flows: classes: Answer(needs_sources) | funcs: classify, plan, brief | imports: from __future__ import annotations; import logging; import re; from dataclasses import dataclass, field
Gotchas: —

## friday\approval.py (124 lines)
Role: How a person's "yes" reaches a confirmation, and why the model cannot supply one. `friday/tools/system_control.py` has said the important half of this since Phase 1A: They are deliberately NOT self-approvable: there is no MCP tool that grants permission, because the agent could then call it unprompted and approve itself. That was right, and it left a hole: every capability in the process and power
Flows: classes: Ambiguous(__init__) | funcs: normalise, reads_as_yes, reads_as_no, awaiting, answer | imports: from __future__ import annotations; import re; from friday import confirmation as CF
Gotchas: confirmation-gated

## friday\apps.py (218 lines)
Role: Application discovery and resolution. Independent implementation. Mark-L (CC BY-NC) resolves apps from a hardcoded alias table of ~50 names x 3 operating systems; that table is both a licence problem and a correctness one - on this machine Chrome lives under "Program Files (x86)", which a table of standard paths would miss. So apps are *discovered*, in this order: 1. Windows "App Paths" registry -
Flows: classes: AppTarget(display_name) | funcs: app_paths_registry, start_menu_shortcuts, refresh_discovery, _expected_processes, _resolve_stem, resolve, known_apps | imports: from __future__ import annotations; import os; import shutil; import sys; from dataclasses import dataclass, field; from functools import lru_cache
Gotchas: env-driven

## friday\arbiter.py (116 lines)
Role: InputArbiter: route new input while an objective run is active. New input must never be destructive by default. A status question is answered from durable state and never cancels the run; a side conversation does not touch it; only an explicit stop cancels it. Modification requests change the active run's inputs; a fresh request (no active run) starts a new objective. Classification is determinist
Flows: funcs: _is_command, classify_input | imports: from __future__ import annotations; import re
Gotchas: —

## friday\audit_fixtures.py (145 lines)
Role: What an audit may safely call a capability with, and what it may not. The registry says a capability exists and `capability_runtime` says it can be reached. Neither says what to pass it, and 60 of the 113 runnable capabilities take a required argument. Calling them with nothing produces a `TypeError` that the runtime honestly reports as a failed task - and "files_read takes (run, path)" is a fact 
Flows: classes: Workspace(__init__, file) | funcs: arguments_for, _own | imports: from __future__ import annotations; import pathlib; from friday.fsjail import DEFAULT_WORKSPACE
Gotchas: —

## friday\audit_planner.py (284 lines)
Role: Auditing every capability, from the registry rather than from prose. A spoken request to "audit everything you can do" produced 205 tasks, because the planner read 1967 words as 205 instructions. That is the wrong shape from the first step: the list of things Friday can do is not in the sentence, it is in the registry, and the registry is data. the old way 1967 words -> split -> 205 nearest-capabi
Flows: classes: AuditGroup(testable), AuditPlan(accounted_for, counts) | funcs: is_an_audit_request, strategy_for, plan_audit, children_of, as_goals | imports: from __future__ import annotations; import re; from dataclasses import dataclass, field; from friday import audit_fixtures as F; from friday import semantics as S
Gotchas: confirmation-gated

## friday\autolearn.py (267 lines)
Role: The learning loop nobody has to remember to run. `profile.learn_from_turn` worked from the day it was written, but only if the model chose to call `profile_learn_from_turn` - and it almost never did, because deciding to file a memory is not what a voice assistant is thinking about mid-sentence. So the user model stayed nearly empty while the boss said plenty worth keeping, and the briefing that wa
Flows: classes: AutoLearner(__init__, start, aclose, observe, _worker, _learn, _gate, _refresh) | funcs: payload, last_assistant_text | imports: from __future__ import annotations; import asyncio; import json; import logging; from collections.abc import Awaitable, Callable; from typing import Any
Gotchas: sqlite, livekit, secret-sensitive

## friday\brain.py (337 lines)
Role: SharedBrainAdapter — the one door between Friday/Hermes and GBrain. Phase 2 ruling (boss): GBrain answers "what do we already know?" and NOTHING else. ObjectiveRun keeps "what are we doing", WorkRun keeps "what executed", Skills keep "how do we repeat it". This adapter is deliberately small so that separation cannot blur: * Verbs surface ONLY. The child process is `gbrain serve --surface verbs` (7
Flows: classes: AdmissionRefused, BrainAnswer(compact), SharedBrainAdapter(__init__, _call, recall, remember, _ledger_path, _ledger_append, replay_ledger, entity) | funcs: _untrusted_provenance, _sensitive | imports: from __future__ import annotations; import json; import logging; import os; import re; import subprocess
Gotchas: env-driven, spawns-proc, secret-sensitive

## friday\breaker.py (207 lines)
Role: A circuit breaker for outbound HTTP, per host. Why this exists --------------- `web_fetch` and the research crawler each open httpx with a 25s timeout and no memory. A host that is down costs the full 25s *every* call, and a research pass crawls many URLs: several dead sources in one objective serialise into minutes of a voice session sitting silent, waiting on hosts that already failed. The break
Flows: classes: CircuitOpen(__init__), _Host, guard(__init__, __enter__, __exit__) | funcs: _int_env, host_of, allow, record_failure, record_success, state_of, reset, is_transport_failure | imports: from __future__ import annotations; import os; import threading; import time; from dataclasses import dataclass, field; from urllib.parse import urlparse
Gotchas: env-driven

## friday\browser.py (762 lines)
Role: Browser primitives (PRD v3.1 FR-031, FR-033, FR-034, FR-035, FR-036, FR-037). One deterministic layer between "Friday wants the browser to do X" and Playwright, so that every primitive the PRD names (open, inspect, navigate, click, type, scroll, select, upload, download, tabs, screenshot, wait, verify) exists once, is policy-classified once, and runs inside the same observe -> plan -> policy -> ac
Flows: classes: ProfileChoice, Element, PageState(find, to_dict), Driver(goto, current, text, elements, click, fill, press, scroll), PlaywrightDriver(__init__, goto, current, text, elements, click, fill, press), Step(to_dict), Refused(__init__), Handoff(__init__), Browser(__init__, observe, plan, act, verify, run, transcript) | funcs: choose_profile, workspace_profile_dir, owns_profile_dir, human_verification, external_write_intent | imports: from __future__ import annotations; import logging; import re; import time; from dataclasses import dataclass, field; from pathlib import Path
Gotchas: confirmation-gated, secret-sensitive

## friday\browser_capability.py (163 lines)
Role: Friday Browser Capability - the policy layer in FRONT of any browser actuation (Phase 3 of the vnext build). This module deliberately contains NO browser engine. Hermes already owns navigation/snapshot/click/type via its browser toolset and the extension-controller broker (verified live in Phase 1F.c: 11 control capabilities advertised). Friday's job is the part Hermes must never decide alone: int
Flows: classes: BrowserVerdict(allowed), SensitiveRedirect | funcs: classify_url, redact_secrets, observe_page | imports: from __future__ import annotations; import re; from dataclasses import dataclass; from friday import netguard, sensitive_domains
Gotchas: secret-sensitive

## friday\browser_profiles.py (285 lines)
Role: The browser the boss actually uses, not a blank one. "Open YouTube" opening a disposable Playwright Chromium signed into nothing is the wrong answer to the right request. He has twelve Chrome profiles and five Google accounts; the useful question is not "can Friday open a browser" but "which of his identities is this for". Chrome Local State -> profile.info_cache {"Profile 55": {"name": "aicodepro
Flows: classes: Profile(label, as_dict, matches) | funcs: user_data_dir, executable, discover, all_profiles, last_used, resolve, safe_url, open_url, describe | imports: from __future__ import annotations; import json; import logging; import os; import re; import shutil
Gotchas: env-driven, spawns-proc

## friday\build_identity.py (203 lines)
Role: What code is actually running, so a test can refuse to test the wrong build. This exists because of an hour lost to a lie. `agent_friday.py dev` printed registered worker {"id": "AW_m4i9JfMEo5iT", ...} four times while the process id never changed, because LiveKit's dev watcher re-registers the worker without re-importing modules already in `sys.modules`. And separately, `server.py` had been runni
Flows: classes: Build(age_seconds, to_dict, matches) | funcs: _from_manifest, _git, registry_hash, current, expected, describe | imports: from __future__ import annotations; import hashlib; import json; import os; import subprocess; import time
Gotchas: livekit, spawns-proc

## friday\camera.py (152 lines)
Role: friday/camera.py -- who is using the camera, and may Friday have it. The rule the owner set: the camera is the first way in, but it is never taken from him. If he is in a meeting, on a stream, or has Friday's own voice agent running in a terminal, Friday does not fight for the device -- she asks, and falls back to a PIN. Windows tells us this honestly. Every app that opens the camera writes to Cap
Flows: funcs: _registry_holders, _classify, _running, status, free_enough | imports: from __future__ import annotations; import os; import sys; import time; from pathlib import Path
Gotchas: env-driven

## friday\capabilities.py (2635 lines)
Role: Capability metadata for every MCP tool. This is deliberately not a permission system yet. It is the data a policy engine will need later, declared now so no tool can be added without stating what it touches. The immediately useful property: ``requires_edge`` marks the tools that act on a physical machine and therefore break the moment the agent is deployed to a container. Kept as a plain table rat
Flows: classes: Capability(requires_approval, policy_tool_ids, __post_init__) | funcs: get, by_id, specialist_for, requiring_edge, as_dicts | imports: from __future__ import annotations; from dataclasses import asdict, dataclass
Gotchas: mcp-tool, confirmation-gated, secret-sensitive

## friday\capability_router.py (556 lines)
Role: Dynamic toolsets: put a dozen tools in front of the model, not seventy-four. §18 said not to expose every tool permanently. It was ignored, and the bill came due: 74 tools meant ~22,700 characters of JSON schema on every single request, roughly 7,600 tokens of pure overhead before the user had said anything. Gemini began returning empty completions - `FinishReason.STOP` with no content, four retri
Flows: classes: Router(load, known_names, active_names, active_tools, enable, describe, _schema_of, describe_tool) | funcs: _content, _phrase_score, group_of, catalogue, unassigned | imports: from __future__ import annotations; import re; from dataclasses import dataclass, field; from friday import capabilities; from friday import semantics
Gotchas: secret-sensitive

## friday\capability_runtime.py (483 lines)
Role: One implementation of each ability, callable from both Fridays. There were two. Conversation reached 132 capabilities through MCP; a durable objective reached 5, through a table written by hand in `objective_cli`: fns = { "system_get_info": system.system_get_info, "system_list_processes": system.system_list_processes, "apps_open": system.apps_open, "files_create": files.files_create, "web_search":
Flows: classes: Resolution(load), CapabilityRuntime(__init__, execute, _approval_on_record, _bind) | funcs: _takes_a_run, _by_domain_prefix, _parsed, _toolset_directories, _toolset_functions, resolutions, required_arguments, unresolved, reachable, build_dispatch | imports: from __future__ import annotations; import ast; import functools; import importlib; import inspect; import json
Gotchas: confirmation-gated

## friday\capture.py (385 lines)
Role: Writing down what the boss said he is building. `projects_list` says this about the read side: "What am I working on?" had no capability behind it at all - the storage was there, `store.projects()` was there, and nothing in the registry reached either. So the answer came from whatever the model remembered of the conversation, which is the failure durable memory exists to prevent. That was fixed. T
Flows: classes: Change(describe) | funcs: project_named_in, remember_the_project, remember_the_decision, requirements_in, change_in, _tidy, remember_the_requirements, apply_change, _store | imports: from __future__ import annotations; import logging; import re; from dataclasses import dataclass
Gotchas: sqlite

## friday\codegraph.py (603 lines)
Role: A structural map of a codebase, built without asking a model anything. The idea is Graft's: a coding agent that has to discover a repository by grepping spends most of its context finding out where things are, and most of that discovery is the same every run. A structural graph - what exists, where it is defined, and what calls what - answers those questions for a few kilobytes instead of a few hu
Flows: classes: Symbol(qualified), _PythonReader(__init__, _define, visit_FunctionDef, visit_ClassDef), CodeGraph(build, worth_building, stale, refresh, named, find, mentions, callers) | funcs: _calls_in, _references_in, read_python, read_approximate, _walk, graph_path, ensure | imports: from __future__ import annotations; import ast; import json; import logging; import re; import time
Gotchas: —

## friday\config.py (303 lines)
Role: Configuration - environment loading, plus an explicit classification of every variable this project recognises. The classification exists because the .env had accumulated keys nothing read (Supabase for a ticketing tool that does not exist, Deepgram and Google Cloud for STT providers that were never implemented). Unexplained credentials are a security liability, not harmless clutter, so every name
Flows: classes: Config | funcs: frozen, executable_dir, portable_mode, default_state_root, env_file_candidates, adopt_legacy_env_file, load_env_files, runtime_paths, is_set, by_classification, dead_but_present, unknown_env_names | imports: from __future__ import annotations; import os; import sys; from pathlib import Path; from dotenv import load_dotenv
Gotchas: env-driven, sqlite, livekit, secret-sensitive

## friday\confirmation.py (214 lines)
Role: A yes that means one thing. `CONFIRM` says a human has to approve an action that autonomy will not approve for them. This is what turns that into something safe, because the dangerous shape is not "Friday asked" - it is: Friday: "Restart this computer?" boss: "yes" Friday: terminates Spotify A confirmation is therefore bound to the exact action, and the binding is checked again at the moment of ex
Flows: classes: Confirmation(expired, to_dict), Verdict, Book(ask, approve, refuse, consume, forget_expired) | funcs: _now, fingerprint, reset | imports: from __future__ import annotations; import hashlib; import json; import secrets; from dataclasses import dataclass, field; from datetime import datetime, timedelta, timezone
Gotchas: confirmation-gated, secret-sensitive

## friday\continuity.py (624 lines)
Role: Durable task and wake state for objectives that outlive one model turn.
Flows: classes: ContinuityInvariantError, StaleClaim, PortionBudget, RunBudget, WakeCondition(__post_init__, immediate, with_task), RunSnapshot, PortionClaim, AttemptReservation, ContinuityManager(__init__, start_run, status, claim_next_due, recover_expired_leases, list_runs, events, claim_run) | funcs: _utcnow, _iso, _loads, _id | imports: from __future__ import annotations; import json; import uuid; from dataclasses import asdict, dataclass, field, replace; from datetime import datetime, timedelta, timezone; from typing import Callable
Gotchas: secret-sensitive

## friday\continuity_livekit.py (391 lines)
Role: LiveKit event adapter for durable objective continuation.
Flows: classes: LiveKitContinuity(__init__, attach, start, aclose, begin_user_objective, accept_user_turn, update_active, on_speech_created) | funcs: _tool_evidence | imports: from __future__ import annotations; import asyncio; import contextlib; import json; import logging; import time
Gotchas: livekit, secret-sensitive

## friday\continuous.py (1681 lines)
Role: The continuous execution engine: lease-protected task execution, a driver loop, and the orphan watchdog. Progression is deterministic and never depends on a user message. The executor acquires a generation-bumped lease on the run row, executes a bounded portion of ready tasks by calling capabilities directly (no LLM in the loop), persists every transition plus a trace event, computes the next wake
Flows: classes: InvariantViolation, FailedToAcquire, FailureClassifier(classify), ContinuousTaskExecutor(__init__, stop, acquire, release, _heartbeat, _driver_loop, _driver_tick, start), RunWatchdog(__init__, start, _loop, sweep_once, store_runs, _is_orphan, _reconcile, _continue_after) | funcs: _as_naive, _due, _fresh, _normalize_failure_text, failure_fingerprint, _in_seconds, _looks_like_a_provider, _refused, _is_connectivity, _is_auth, _is_approval, _working_worker, _pending_worker_id, _worker_status, _describe | imports: from __future__ import annotations; import asyncio; import hashlib; import json; import logging; import os
Gotchas: env-driven, confirmation-gated

## friday\contracts.py (435 lines)
Role: Proof-of-work contracts: Verification, Artifact, ActionResult, Run. The failure mode these exist to make impossible: User: "Let's build an Arc Reactor design." Agent: "Boss, the Arc Reactor design is ready." <- nothing was built This is not hypothetical. `Mark-L/actions/open_app.py` ships it today: its launch helper returns True because `subprocess.Popen` did not raise, and its last-resort branch 
Flows: classes: ContractError, Verification(__post_init__), Artifact(__post_init__), ActionResult(__post_init__, may_claim_completion, is_terminal, honest_summary, finish, to_dict), Run(__post_init__, create, from_read_material, record, transition, artifacts, unverified, all_succeeded) | funcs: now_iso, new_run_id, new_artifact, started, succeeded, failed, partial, cancelled | imports: from __future__ import annotations; import uuid; from dataclasses import dataclass, field, replace; from datetime import datetime, timezone
Gotchas: spawns-proc, confirmation-gated

## friday\control.py (185 lines)
Role: friday/control.py -- the control channel: the UI talking to the real agent. Until now the UI could only observe. This connects it to the running MCP server (server.py on :8000/sse) as an ordinary MCP CLIENT, so the deck and the voice path can invoke Friday's real tools instead of queueing forever. Two rules make this safe rather than a remote-execution hole: READ_ONLY -- an allowlist of tools that
Flows: funcs: reachable, _text, _session, list_tools_async, call_async, _run, status, call, call_approved, request_call, pending, approve, reject | imports: from __future__ import annotations; import asyncio; import os; import socket; from friday import confirmation as _C
Gotchas: env-driven, confirmation-gated

## friday\dag.py (54 lines)
Role: Dependency ordering, shared by the automation engine and the product pipeline. Both need the same two things, and the second is the one that matters: an order to run in, and a rule for what happens to the things downstream of a failure. A step whose dependency did not succeed is *skipped and recorded as skipped* - not run into the same failure, and not silently dropped. That rule is the whole diff
Flows: classes: CycleError | funcs: topological, blocked_by | imports: from __future__ import annotations
Gotchas: —

## friday\data_class.py (215 lines)
Role: The classification a piece of content earns, and what that earns it at the gateway door. The failure this exists to prevent is a model talking its way past a rule that was never meant to be negotiable: a prompt that says "this is public, send it anywhere" must not change what actually leaves the machine. So classification is deterministic and structural - regexes over shapes a credential, a card n
Flows: classes: _Rule, Decision | funcs: _rule, classify, redact, decision | imports: from __future__ import annotations; import re; from dataclasses import dataclass; from friday import privacy
Gotchas: secret-sensitive

## friday\dbconn.py (47 lines)
Role: One rule for every SQLite ledger that opens a connection per call. `sqlite3.Connection` as a context manager commits or rolls back - it does NOT close. Written as `with sqlite3.connect(...) as db:` the connection lives until the garbage collector finds it: on Windows that is a kernel handle per call, and the A-051 soak measured it as a monotonic handle leak (seven ledgers, ~3.5 handles per Hermes 
Flows: classes: LedgerConnection(__init__, __enter__, __exit__, __getattr__) | funcs: ledger_connection | imports: from __future__ import annotations; import sqlite3; from contextlib import contextmanager; from typing import Iterator
Gotchas: sqlite

## friday\deck.py (205 lines)
Role: friday/deck.py -- the Command Deck: one-click intents, and a queue for the rest. Two honest classes of intent: LIVE -- this process can actually do it now (memory, vitals, harness, gates, browser). Pressing the button runs it and returns a result. QUEUED -- it needs the live Friday agent (news brief, plan the day, run an objective). Pressing the button writes an intent to the queue and says so. It
Flows: funcs: _now, _read_queue, _write_queue, queue, enqueue, _run_live, run, schedule, state | imports: from __future__ import annotations; import json; import os; import threading; import time; from datetime import datetime, timezone
Gotchas: env-driven

## friday\desk.py (171 lines)
Role: friday/desk.py -- what the owner is looking at right now. Two questions Friday could not answer before: "what's on my clipboard?" "read what I've selected" The clipboard is easy: the system toolset already has a hardened reader that survives the Windows clipboard lock, so this reuses it rather than opening a second one. The selection is harder, because Windows has no "give me the highlighted text"
Flows: funcs: _busy, _read, _write, clipboard, _tap_copy, selection, grab | imports: from __future__ import annotations; import sys; import time
Gotchas: —

## friday\development.py (516 lines)
Role: One development run, from a goal to a decision. Everything this needs already existed as of an hour ago and none of it was connected, which is the exact failure that has just been fixed one layer up: `text_input_callback` called `prepare_turn` and nothing else, so the whole research pipeline was dead for typed input. Not broken. Not connected. A module that is built, tested, and never called is de
Flows: classes: DevelopmentRun(ready, note_the_base, understand, context_for, staff, pick, execute, _execute_via_team) | funcs: _diff_against, for_goal | imports: from __future__ import annotations; import asyncio; import logging; import time; from dataclasses import dataclass, field; from pathlib import Path
Gotchas: spawns-proc, confirmation-gated

## friday\evaluation.py (300 lines)
Role: Objective verdicts on work an agent says it finished. NO MODEL PROMOTES ITS OWN CODE BECAUSE IT SAID THE TESTS PASSED. That sentence is the whole module. A coding agent reporting success is a claim, and this repository already knows what to do with claims: check them. The check has to be something the agent cannot influence - a command, run afterwards, in the boundary, whose exit code is the verdi
Flows: classes: Verifier(describe), Attempt(passed), Record(add, scored, best_for, compare, save, load) | funcs: verify, record_path, remember, graded | imports: from __future__ import annotations; import json; import logging; import statistics; import time; from dataclasses import asdict, dataclass, field
Gotchas: spawns-proc

## friday\execution.py (723 lines)
Role: Where Friday runs code it did not write, and what that actually guarantees. A git worktree is version control, not containment. It stops a coding agent corrupting the branch. It does nothing about the agent reading `.env` two directories up, forking until the machine swaps, opening a socket to somewhere it should not, or leaving a process alive after the run is "finished". Four failures, and the w
Flows: classes: Limits, Egress(__post_init__, allows, describe), _IO_COUNTERS, _JOBOBJECT_BASIC_LIMIT_INFORMATION, _JOBOBJECT_EXTENDED_LIMIT_INFORMATION, JobObject(__init__, _create, adopt, close), Execution(ok), ExecutionError, NativeExecutionEnvironment(__init__, __enter__, __exit__, terminate, strength, environment, redacted, run), ExecutionBackend(create, exec, status, put_file, get_file, list_files, collect_artifacts, terminate), NativeWindowsBackend(create, exec, status, put_file, get_file, list_files, collect_artifacts, terminate) | funcs: _looks_absolute, for_development, backend_named | imports: from __future__ import annotations; import ctypes; import logging; import os; import shutil; import subprocess
Gotchas: env-driven, spawns-proc, secret-sensitive

## friday\execution_economics.py (557 lines)
Role: Execution economics - Friday decides how much intelligence a task deserves. Phase H (H1-H6). Design constraints, from the adjudicated H spec: - H1-H5 are LOGICAL STAGES, not agents: one deterministic pass, zero model calls. An LLM call to decide which LLM to call is the first prohibited anti-pattern. - Route selection is risk-weighted, not size-weighted: a one-line auth change is not "small"; a re
Flows: classes: TaskEconomics, Route, RouteOutcomes(__init__, _connect, record, mark_rework, by_class, execution_value) | funcs: classify_task, choose_route, known_models, _tier_table, tier_from_requirements, resolve_model, resolve_effort, _fallback_candidates, candidates, _fmt_hhmm, plan_delegation, verification_depth | imports: from __future__ import annotations; import functools; import logging; import os; import sqlite3; import time
Gotchas: env-driven, sqlite, secret-sensitive

## friday\executor_router.py (252 lines)
Role: Which coding agent gets the work, decided from evidence where there is any. Friday's routing has been assumption all the way down - "Opus is probably best for this" - and an assumption that nobody measures never gets corrected, because there is nothing for it to be corrected against. `evaluation.py` records what actually happened; this is the thing that reads those records and acts on them. Three 
Flows: classes: Executor(path, installed, usable), Choice(as_dict) | funcs: installed, usable, discover, choose, build | imports: from __future__ import annotations; import logging; import shutil; from dataclasses import dataclass, field
Gotchas: —

## friday\fabric.py (880 lines)
Role: The Capability Fabric: what Friday could reach for, and what that would cost. `friday/capabilities.py` already answers "what can Friday do" for the 158 things Friday implements itself. It has no concept of a pinned upstream commit, a license that forbids importing, a sidecar that has to be started before it answers, or a health probe against code we did not write. Those are the four facts that mat
Flows: classes: FabricError, Provider(__post_init__, imported), Activation | funcs: _discover, reap_orphans, registry, reload, get, by_family, families, _adapter, state, activate, deactivate, deactivate_all, health, report, family_report | imports: from __future__ import annotations; import importlib; import logging; import os; import pkgutil; import time
Gotchas: env-driven, spawns-proc, secret-sensitive

## friday\fabric_cli.py (239 lines)
Role: friday/fabric_cli.py -- the CLI integration mode: invoke, work, exit. Seven of the unintegrated upstreams (cline, crewai, strix, agenticseek, openhands, firstmate, openworker) are command-line agents. The mode vocabulary had BUILTIN, ADAPTER, MCP, SKILL, SIDECAR and REFERENCE_ONLY, and none of them describes a program you run once. A contributor could not write a descriptor for these even in princ
Flows: classes: CliError, Command, Bootstrap | funcs: _fill, _redacted, _root, health, run | imports: from __future__ import annotations; import json; import pathlib; import re; import subprocess; import threading
Gotchas: spawns-proc

## friday\fabric_memory.py (185 lines)
Role: friday/fabric_memory.py -- which providers actually work, learned from use. `call_with_fallback()` ordered candidates by declared cost and current health. Observed success rate was not an input, so a provider that failed nine calls in ten was chosen exactly as readily as one that always worked, and the fallback chain re-learned that the hard way on every single request. This is the fabric's half o
Flows: funcs: _store, _cutoff_iso, _load, record, score, rank, report, prune, forget | imports: from __future__ import annotations; import os; import threading; import time
Gotchas: env-driven

## friday\fabric_process.py (389 lines)
Role: friday/fabric_process.py -- the fabric owns its children, or it owns nothing. `fabric.activate()` called an adapter's `start()` and then polled `health()` immediately. Nothing waited for readiness, nothing captured output, nothing noticed a crash, nothing cleaned up an orphan. `SIDECAR` and `MCP` were words in `INTEGRATION_MODES` with no runtime behind them, which is why exactly one of sixteen pro
Flows: classes: ProcessError, Ready(check), Immediate(check), LogLine(check), TcpPort(check), HttpOk(check), Spec, Child(alive, log_tail) | funcs: _free_port, _environment, _drain, _launch, spawn, stop, restart, child, logs, running, stop_all, reap_orphans | imports: from __future__ import annotations; import logging; import os; import pathlib; import socket; import subprocess
Gotchas: env-driven, spawns-proc

## friday\fabric_service.py (322 lines)
Role: friday/fabric_service.py -- the SIDECAR mode gets a runtime. `bolt.diy`, `onlook`, `open-lovable`, `postiz`, `maxun`, `anythingllm` and `open-notebook` are web applications. `SIDECAR` named that shape and supplied nothing to work with: no base-URL discovery, no auth, no timeout, no retry policy. An adapter author had to write all of it by hand, differently each time, and the one existing SIDECAR p
Flows: classes: ServiceError, Service, Endpoint | funcs: spec_for, _fill, _base, _reaper_loop, _ensure_reaper, ensure_up, _headers, _should_retry, _evidence, health, request, shutdown | imports: from __future__ import annotations; import re; import threading; import time; from dataclasses import dataclass, field; from friday import contracts as c
Gotchas: secret-sensitive

## friday\first_run.py (139 lines)
Role: First-run operating contract (Phase 9; R14 / build-pack 10). Ten adaptive questions, persisted as versioned policy. Rules: - NOT a questionnaire prison: any question whose answer is already derivable from context is skipped, and everything can be changed later in plain conversation ("I trust you to publish Instagram without asking" -> versioned permission update via user_policy). - Answers land in
Flows: classes: FirstRunContract(__init__, exists, read, pending_questions, record, adapt) | imports: from __future__ import annotations; import json; import time; from pathlib import Path; from friday.user_policy import UserPolicy
Gotchas: confirmation-gated

## friday\forge.py (677 lines)
Role: Forge: Friday writing its own capabilities, with the boundaries outside the generated code. The lifecycle is borrowed from Ada-SI (spec -> static check -> isolated verification -> registration). Its *trust model* is not, and that is the whole point of this module: A Python venv is dependency isolation. It is not a security sandbox. Python's own documentation describes venv as isolating the interpr
Flows: classes: ForgeError, CapabilitySpec(__post_init__, to_dict, from_dict), StaticFinding(__str__), VerificationReport(to_dict), Registry(__init__, submit, get, list, history, _transition, verify, register) | funcs: _module_root, static_gate, scrubbed_env, env_leaks, digest, verify, risk_of, store, reset_store | imports: from __future__ import annotations; import ast; import hashlib; import json; import logging; import os
Gotchas: env-driven, sqlite, livekit, spawns-proc, secret-sensitive

## friday\forge_runtime.py (261 lines)
Role: The other side of the process boundary: where a forged skill actually runs. Executed as `python -I -m friday.forge_runtime <request.json>` with an environment built from nothing, so this process holds none of Friday's credentials. It loads one skill, gives it a CapabilityContext, runs the spec's declared cases, and prints one line of JSON. Two layers stop a skill from being arbitrary Python, and t
Flows: classes: CapabilityDenied, CapabilityContext(__init__, log, progress, read_text, fetch, emit) | funcs: _guarded_import, load, _matches, main, _safe | imports: from __future__ import annotations; import builtins; import json; import sys; import time; from pathlib import Path
Gotchas: —

## friday\fsjail.py (254 lines)
Role: Filesystem jail: every path the agent touches is resolved and contained. §11 requires paths to be normalised and validated, and traversal outside permitted roots prevented. Mark-L's file controller is marked REWRITE in the parity matrix with path traversal as its flagged risk, so containment is the design here rather than a check bolted on afterwards. Two layers, because either alone is insufficie
Flows: classes: JailError, FileJail(__init__, _reject_shape, _reject_reserved, _reject_denylisted, _contained, resolve, describe) | funcs: is_reparse_point, default_roots, configured_roots | imports: from __future__ import annotations; import os; import re; import stat; from pathlib import Path; from friday.config import DATA_DIR, PROJECT_ROOT
Gotchas: env-driven, secret-sensitive

## friday\golden.py (719 lines)
Role: Golden Objective evaluation suite (PRD v3.1 7.2, 7.3, 12.1). A Golden Objective is a replayable objective with acceptance criteria written BEFORE it runs. The suite scores every case on the five axes 7.3 requires - correctness, evidence, policy compliance, latency/cost, manual-intervention count - and every run records build/commit, configuration, model/provider, machine profile and date. corpus d
Flows: classes: Case(from_dict), _Web(__init__, __enter__, __exit__, url), Bench(__init__, __enter__, __exit__), Outcome, CaseResult(to_dict), Report(summary, to_dict, write) | funcs: load, load_all, _substitute, _run_case, count_clarifications, score, provenance, _median, _p95, golden_dispatch, run, main | imports: from __future__ import annotations; import logging; import asyncio; import json; import os; import platform
Gotchas: env-driven, sqlite, spawns-proc

## friday\governor.py (379 lines)
Role: The resource governor: how much the machine can carry, decided outside the model. PRD v3.1 FR-056 (P0): monitor CPU, RAM, disk, browser processes, worker count and queue pressure, applying backpressure or shedding optional work. FR-013 (P0): default active execution concurrency stays bounded (0-2 workers) and scales only for demonstrably parallel work. NFR-P09. Three things, deliberately separate:
Flows: classes: Thresholds, Sample(to_dict), Pressure(to_dict), Decision(admitted, to_dict), Governor(__init__, pressure, active, active_leases, queue_depth, justify_parallel, admit, release), Refused(__init__) | funcs: sample_machine, assess, governor, configure | imports: from __future__ import annotations; import logging; import threading; import time; from dataclasses import dataclass, field
Gotchas: —

## friday\handoff.py (75 lines)
Role: S4b: what one delegated task hands back, as data rather than prose. `render_completion()` already turns a work-run record into the sentence a person hears; `Handoff` is the structured sibling of that same record - the fields a parent (continuous.py, a digest, a future review pass) can read without re-parsing a transcript. Built ONLY from what the work-run record and the in-memory progress dict act
Flows: classes: Handoff(to_json, from_json, from_work_run) | imports: from __future__ import annotations; import json; from dataclasses import dataclass, asdict
Gotchas: secret-sensitive

## friday\harness.py (107 lines)
Role: Browser / computer-control harness -- engine selection, one manager. The owner wants "all of these, chosen per need": Friday's own headed browser for most work, browser-use when a task needs ChatGPT-browser-class autonomy, and OpenHands / agenticSeek only for heavy computer-use -- kept isolated because they are second control layers (agenticSeek is GPL). Friday stays the manager (non-negotiable #1
Flows: classes: Engine | funcs: _installed, _clone_present, status, availability, select | imports: from __future__ import annotations; import importlib.util; from dataclasses import dataclass; from pathlib import Path
Gotchas: —

## friday\health.py (194 lines)
Role: Health / status report. python -m friday.health human-readable python -m friday.health --json machine-readable Answers "why won't it start?" without starting anything expensive: no models are loaded, no plugins are imported, no LLM is contacted. Credential VALUES are never read, printed or returned. Only whether a name is set, and which names are missing.
Flows: funcs: _package_version, _mcp_reachable, serving, wait_until_serving, report, main | imports: from __future__ import annotations; import argparse; import json; import os; import socket; import sys
Gotchas: env-driven, livekit

## friday\hermes_bridge.py (2014 lines)
Role: The Friday ↔ Hermes bridge: supervisor, work record, bounded task bundles. Hermes is an EXTERNAL, independently-upgradeable runtime. This module speaks its supported TUI-gateway protocol - newline-delimited JSON-RPC over stdio, the same dispatch the Ink TUI and the desktop dashboard use - and nothing else. No Hermes source is imported, no private protocol is invented, and if Hermes is upgraded und
Flows: classes: TaskBundle(with_memory, render, measure), WorkRunLog(__init__, _connect, create, update, on_terminal, get, active, recent), HermesUnavailable, HermesSupervisor(__init__, _release_lease, start, _launch_plan, alive, health, stop, restart) | funcs: profile_home, native_tools_healthy, process_owner, owner_alive, run_origin, _describe_tool, _profile_model_default, _with_subagent, render_completion, _write_outcome, locate | imports: from __future__ import annotations; import functools; import json; import logging; import os; import shutil
Gotchas: env-driven, sqlite, spawns-proc, confirmation-gated, secret-sensitive

## friday\hermes_health.py (249 lines)
Role: Hermes connectivity, diagnosed by layer. Why this module exists ---------------------- The RC1 live regression produced this pair of facts at the same moment: - `hermes gateway status` reported a healthy Windows Scheduled Task with live PIDs; - Friday told the boss "Hermes gateway is currently disconnected" and asked whether it should continue reading telemetry. Both came from representing Hermes 
Flows: classes: Recovery, Report(__init__, failed_layer, healthy, gateway_is_dead, recovery, blocks_read_only_telemetry, summary, __repr__) | funcs: live_probe_factory, live_recover_factory | imports: from __future__ import annotations; from enum import Enum
Gotchas: mcp-tool

## friday\hermes_model_gateway_worker.py (348 lines)
Role: The Hermes MODEL_GATEWAY worker - runs INSIDE the Hermes venv, never Friday's. PRD v3.1 FR-069/070/071/072/073/079: Friday sends a bounded request envelope; this process asks Hermes's own provider layer to resolve a configured provider/model, performs ONE stateless provider transaction, and returns the text plus usage/route metadata. It never builds an AIAgent, never loads toolsets, skills, subage
Flows: funcs: _reply, _err, classify_failure, _usage_dict, _default_model_for, _is_local_endpoint, _route_kind_for, _providers, _infer, serve | imports: from __future__ import annotations; import json; import os; import sys; import time; import traceback
Gotchas: env-driven, secret-sensitive

## friday\hermes_team.py (271 lines)
Role: Hermes specialist profiles + kanban routing for durable multi-role work. ADR-001: Friday owns the objective, Hermes owns durable execution. This module adds no second objective engine - it is a thin translation from `roles.compile_team` (already-decided staffing) to native Hermes primitives (`hermes profile`, `hermes kanban`) reached through the CLI, the same way an operator would drive them by ha
Flows: funcs: reset_state, _hermes_exe, _hermes, profiles, ensure_profile, plan_team, submit, poll, _sweep_idle, gateway_for | imports: from __future__ import annotations; import json; import logging; import os; import shutil; import subprocess
Gotchas: env-driven, spawns-proc

## friday\honesty.py (401 lines)
Role: Narration guard: catch completion claims that no ActionResult backs. `contracts.py` makes an *unverified success* impossible to construct. This module closes the other half of the hole: the agent generating the sentence "Boss, the Arc Reactor design is ready" without any tool having run at all. No contract can prevent a language model from emitting that token sequence, so it is checked after gener
Flows: classes: ClaimAudit(__bool__) | funcs: sentences, is_negated, is_completion_claim, find_claims, required_evidence, _still_going, _missing_evidence, named_targets, touched_targets, _same_target, _target_mismatch, audit, safe_alternative | imports: from __future__ import annotations; import re; from dataclasses import dataclass, field; from friday import runstate; from friday.contracts import Run
Gotchas: —

## friday\launcher.py (1023 lines)
Role: One entry point for the packaged FRIDAY.exe and for a source checkout. Friday is three processes (see `start.py` and `scripts/restart_friday.py`, which this module replaces for the packaged build): server the MCP server, SSE on :8000 (server.py) ui the control room, HTTP on :8770 (scripts/run_ui.py) agent the LiveKit voice worker (agent_friday.py start) The packaged executable cannot shell out to 
Flows: funcs: frozen, app_root, exe_dir, manifest, version, state_root, env_file_candidates, load_env, ensure_state_dirs, migrate_legacy_state, adopt_legacy_state, bundled_module_name, _importable, maybe_run_as_python, run_role | imports: from __future__ import annotations; import argparse; import importlib.util; import json; import os; import runpy
Gotchas: env-driven, sqlite, livekit, spawns-proc, secret-sensitive

## friday\manifest.py (305 lines)
Role: The Capability Manifest (PRD v3.1 §9.6, FR-023, FR-024, FR-026, FR-027). One queryable view over everything Friday can execute or consult: * the fabric's external providers (`friday/fabric.py`), typed by their integration mode - NATIVE / MCP / CLI / SDK / HTTP / SIDECAR / SKILL / REFERENCE / SPECIALIST_RUNTIME; * Friday's own MCP tools (`friday/capabilities.py`), all NATIVE; * the coding executors
Flows: classes: Manifest(to_dict) | funcs: manifest_type, executable, _trust_level, _review_status, _dangerous, _latency, of_provider, of_native_tool, of_executor, build, summary, describe, export | imports: from __future__ import annotations; import logging; import json; from dataclasses import dataclass, field, asdict; from friday import fabric
Gotchas: secret-sensitive

## friday\memory_graph.py (266 lines)
Role: friday/memory_graph.py -- the shared memory as a graph, built from what is real. The 3D view in the HUD renders THIS. Nothing here is decorative: every node is a fact, an entity or a conflict that exists in the store, and every edge is a relation the data actually carries. Sources (all read-only, all already canonical): memories subject paths ("user.goals.exam") become a hierarchy: the namespace i
Flows: funcs: _validity_token, invalidate, _conn, _ledger, build, adjacency | imports: from __future__ import annotations; import json; import threading; from collections import defaultdict; from pathlib import Path
Gotchas: —

## friday\memory_promotion.py (193 lines)
Role: S7: the gate between an observed candidate and Friday's canonical memory. ADR-001: no second canonical memory. This module never writes anywhere but the store `friday/store.py` already owns (and, for a procedure, a skill candidate file) - it only decides whether a candidate is trustworthy enough to cross from "observed" into "known". Fed by `friday/handoff.py`'s `memory_candidates`/`skill_candidat
Flows: classes: Candidate(__post_init__), Decision | funcs: _subject, promote, _promote_decision, _to_skill_candidate, promote_handoff | imports: from __future__ import annotations; from dataclasses import dataclass, field; from datetime import datetime, timezone; from pathlib import Path; from friday.brain import _sensitive; from friday.store import FACT, PREFERENCE, Store
Gotchas: —

## friday\memory_stack.py (450 lines)
Role: friday/memory_stack.py -- the four-tier memory, assembled into one context. The owner's blueprint, made concrete on what Friday already has: Tier 1 PREFERENCES (the Mem0 role) what you like, want and rule out -- extracted automatically from conversation by friday/profile.py into `memories` (scopes: preferences, wants, goals), plus the Mem0 feed adapter when it is installed. Tier 2 SPECS (the Obsid
Flows: funcs: _tokens, _approx_tokens, _now, preferences, specs, rules, relations, _store, episodes, contacts, hermes_outcomes, aggregate, overview, log_result | imports: from __future__ import annotations; import json; import os; import re; from datetime import datetime, timezone; from pathlib import Path
Gotchas: env-driven, sqlite

## friday\model_capabilities.py (146 lines)
Role: Model capability manifest. Phase 0's registry answers "does this model exist?" by introspecting the installed package's Literal types. That is the right source for *existence* and it cannot drift. It says nothing about what a model can DO. Nothing here is inferred from the model ID string. "flash" does not imply fast, "pro" does not imply vision, and a name that contains "3" tells you nothing abou
Flows: classes: ModelCapabilities(__post_init__), CapabilityError | funcs: _cap, get, for_role, supports, undeclared_models, stale_declarations, as_dicts | imports: from __future__ import annotations; from dataclasses import asdict, dataclass; from friday.providers import LLM_ROLE_MODELS, installed_llm_models
Gotchas: livekit, confirmation-gated

## friday\model_gateway.py (1410 lines)
Role: Hermes MODEL_GATEWAY - Friday's inference-only door into Hermes's providers. PRD v3.1 §4.9 / §9.4. Friday is the brain; Hermes brokers model access. Two things are true of every call through here, by construction: 1. No Hermes agent loop starts. The other side of the pipe is `friday/hermes_model_gateway_worker.py`, which calls Hermes's stateless `agent.auxiliary_client.call_llm` once per request -
Flows: classes: TokenBudget, BudgetExceeded, GrowthStopped, ModelGatewayRequest(input_tokens), ModelGatewayResult(to_dict), GrowthVerdict, GrowthGuard(__init__, fingerprint, check, record, spent, forget), GatewayTelemetry(__init__, _connect, record, for_objective, recent, summary, spikes), RouteJudgement, RouteDecision(routes, rejected, to_dict), GatewayUnavailable, ModelGatewayWorker(__init__, _launch_plan, start, alive, stop, _request, call) | funcs: budget_for, approx_tokens, _joined_text, capability_verdict, gateway, compile_context | imports: from __future__ import annotations; import json; import logging; import os; import queue; import subprocess
Gotchas: env-driven, sqlite, spawns-proc, confirmation-gated, secret-sensitive

## friday\modification.py (302 lines)
Role: Changing a run that is already under way. `classify_input` has returned MODIFICATION since it was written, and the handler did this: # MODIFICATION and SIDE_CONVERSATION both leave the run alone. Which was the right call at the time and is a bad answer to say out loud: the boss says "skip the World Monitor part", Friday says nothing about it, and the World Monitor opens anyway two minutes later. T
Flows: classes: Edit(applied) | funcs: graph_version, _record, _words, find_task, skip_task, amend_task, append_task | imports: from __future__ import annotations; import json; import re; from dataclasses import dataclass, field; from friday import objectives as O; from friday.store import Store
Gotchas: —

## friday\netguard.py (326 lines)
Role: The outbound network boundary. A URL IS NEVER FETCHED SOLELY BECAUSE IT PASSED AN EARLIER VALIDATION. Validation-time safety and connection-time safety are different properties, and conflating them is the whole DNS-rebinding attack: a hostname resolves to a public address while a row is being checked, and to `127.0.0.1` a moment later when the client actually connects. Nothing about the first answ
Flows: classes: UrlRefused, evaluation_fixture(__init__, __enter__, __exit__) | funcs: normalise, address_refusal, resolve, _is_evaluation_fixture, check, _pinned_request, fetch | imports: from __future__ import annotations; import ipaddress; import logging; import socket; from urllib.parse import urlsplit, urlunsplit
Gotchas: —

## friday\objective_budget.py (171 lines)
Role: The objective budget, enforced (audit A-022). `compile_objective` records `cost_budget_tokens` and `time_budget_s` on the run at admission, and the model gateway's `GrowthGuard` stops runaway context per call - but nothing stopped a run that had *already* spent its ceiling from dispatching the next task, and nothing counted tool calls, worker delegations or replans at all. This module is the one c
Flows: classes: Spend, Verdict | funcs: _parse, measure, check | imports: from __future__ import annotations; from dataclasses import dataclass, field; from datetime import datetime, timezone
Gotchas: —

## friday\objective_cli.py (322 lines)
Role: Phase 3 CLI: start and drive objective runs without the MCP server. Two faces of one process: * ``python -m friday.objective_cli --objective "..."`` (mode B, the child) plans the objective, compiles it, and drives it with a ContinuousTaskExecutor until the run is terminal, printing a final summary. This is what the demo and the agent job spawn. * ``python -m friday.objective_cli --objective "..."`
Flows: funcs: _db, build_dispatch, _plan, _drive, _status, _list, _pause, _resume, _cancel, _history, _port, _spawn, main | imports: from __future__ import annotations; import argparse; import asyncio; import inspect; import json; import os
Gotchas: env-driven, spawns-proc

## friday\objectives.py (730 lines)
Role: Durable objective runs: statuses, the task graph, and the compiler. The P0 gate: one multi-step objective must reach a terminal state without the user saying "Continue". Everything here is pure data and state-machine logic - no I/O, no asyncio - so it is testable without the engine. The compiler takes an LLM-produced plan (a list of capability tasks with dependencies), validates it against the cap
Flows: classes: TaskStatus, RunStatus, FailureKind, CompileError, CompiledTask(to_dict), CompiledChild(to_dict), TaskResultSummary(to_dict), TaskContext(to_dict) | funcs: _summarise, task_context, continuity_state, compile_objective, _why_it_cannot_run, _save_composite, _retarget, _validate_references, _walk, json_dumps, active_run, pause_run, resume_run, cancel_run | imports: from __future__ import annotations; import re; from dataclasses import dataclass, field; from friday import dag; from friday.contracts import new_run_id, now_iso; from friday.store import Store
Gotchas: —

## friday\observability.py (544 lines)
Role: Observability (PRD v3.1 FR-054, 12.3 Operational Diagnostics). Two things, both read-only and both assembled from durable state only: trace(run_id) one timeline for one objective, reconstructed from the objective ledger (events + tasks), the tool-result table, the model-gateway call ledger, the executor (worker) runs and the trust audit log. No model thoughts, no prompts: state transitions, tool c
Flows: funcs: _secret_key, redact, _parse, _utc, _stamp, _ms_between, _gateway_calls, _audit_rows, _executor_runs, _tool_results, trace, trace_text, _timed, _build, _sqlite_health | imports: from __future__ import annotations; import logging; import json; import os; import re; import sqlite3
Gotchas: env-driven, sqlite, livekit, secret-sensitive

## friday\onboarding.py (579 lines)
Role: Progressive onboarding: who the user is, written where production reads it. FR-ONB-004 and FR-POL-002. The PRD asks for a conversation rather than a form, a user who is operational in roughly five to ten minutes, deep personalisation that continues over days instead of blocking first use, and - the part that decides the design - a profile that lands in *inspectable memory and policy* rather than i
Flows: classes: Question, Stage, Profile(get, as_dict), Onboarding(__init__, policy, profile, pending, complete_enough, progress, record, skip) | funcs: _questions, state_root_marker, console | imports: from __future__ import annotations; import json; from dataclasses import dataclass, field; from pathlib import Path
Gotchas: confirmation-gated

## friday\org.py (268 lines)
Role: friday/org.py -- the one-person company: You -> Friday -> Hermes -> teams. Nothing in the org is typed by hand. Teams are the divisions of the pinned agency-agents upstream (divisions.json + each agent's front matter: name, description, colour), so the roster, its colours and its size come from data and change when the upstream does. Live status comes from the same probes the rest of Friday trusts
Flows: funcs: _frontmatter, _load_agency_divisions, _load_voltagent_divisions, _load_divisions, divisions, _friday_tier, _hermes_tier, chain, _tools_for, route, assemble, _sources, state | imports: from __future__ import annotations; import json; import re; import threading; from pathlib import Path
Gotchas: —

## friday\orgplane.py (245 lines)
Role: OrganizationControlPlane - the neutral interface above any org backend (Phase 6 of the vnext build; requirement R7). Friday decides WHEN a persistent organization is justified; this module decides NOTHING - it is the seam that keeps Friday independent of Paperclip internals. Paperclip stays invisible and optional: Friday -> OrganizationControlPlane -> PaperclipAdapter (today) -> anything else (tom
Flows: classes: LocalControlPlane(__init__, _connect, create_operation, update_goal, assign_work, record_work_product, set_budget, record_decision), PaperclipAdapter(__init__, available, __getattr__) | funcs: control_plane | imports: from __future__ import annotations; import json; import sqlite3; import time; from pathlib import Path
Gotchas: sqlite

## friday\os_map.py (164 lines)
Role: friday/os_map.py -- the Agentic OS map: You -> Jarvis -> capability domains. The architecture diagram, generated from what this machine ACTUALLY has rather than drawn by hand: columns are domains, cards are real MCP capability groups and real fabric providers, and every count comes from the live registry. Cadence marks how a card runs, which is the honest distinction: on_demand -- you or Jarvis in
Flows: funcs: capability_label, _groups, _providers, _engines, build | imports: from __future__ import annotations
Gotchas: —

## friday\overlay.py (75 lines)
Role: Putting the arrow on the actual screen. `screen.screen_point` always writes an annotated PNG - that is the guaranteed answer. This module is the better one: a short-lived, click-through, always-on- top child process that draws the same arrow over the live desktop. It is deliberately best-effort. Every failure path here returns False and the caller still has its image. An overlay that cannot be dra
Flows: funcs: dismiss, show | imports: from __future__ import annotations; import os; import subprocess; import sys; from pathlib import Path
Gotchas: env-driven, spawns-proc

## friday\ownership.py (277 lines)
Role: Who is doing this piece of work, so it does not get done twice. Admission creates a durable run and then returns, and LiveKit generates the reply for that same turn with the whole toolset still in front of the model. So "check my system, open Paint and find a story" can open Paint twice: once because the objective's task graph says to, and once because the model read the sentence and helpfully did
Flows: classes: Deferred(__init__) | funcs: store, reset_store, _age_seconds, claimed_by, is_conversational, is_read_only, _same_work, _bound_arguments, guard | imports: from __future__ import annotations; import inspect; import os; from datetime import datetime, timezone; from friday import objectives as O; from friday.store import DEFAULT_DB, Store
Gotchas: env-driven, livekit

## friday\planner.py (891 lines)
Role: Understanding a request before deciding what to call. The planner this replaces did one thing: split the sentence on connectives and match each fragment to the nearest capability name. Everything it got wrong follows from there. "Friday, check my computer" -> a task called objective.unmapped, because "Friday" is a fragment and fragments become tasks "keep going" -> memory_remember "do not ask me t
Flows: classes: Segment, Goal, Plan(executable), PlanProblem | funcs: _split_requests, _strip_vocative, split_off_the_instruction, classify_segment, segments, interpret, _entity, candidates, resolve, _fit, _operation_from_examples, _target_from_examples, validate, plan_objective, _wanted | imports: from __future__ import annotations; import re; from dataclasses import dataclass, field; from friday import semantics as S
Gotchas: secret-sensitive

## friday\planner_model.py (403 lines)
Role: Asking a model what a request means, without letting it choose the tools. The deterministic interpreter in `friday/planner.py` reads instructions well - verbs it knows, objects it knows, "then" meaning order. It does not read people. "I want to make a game where you play as a lighthouse keeper" contains no verb it recognises and no target it can name, and it will say so honestly rather than guess.
Flows: funcs: domain_summary, _model, needs_model, interpret, _as_plan, _entity, what_was_decided, plan_objective | imports: from __future__ import annotations; import json; import logging; import os; import re; from friday import planner as P
Gotchas: env-driven, livekit

## friday\policy.py (765 lines)
Role: Policy engine v1 (§20). Deterministic authorization for tool calls. Three decisions: AUTO - run it, no prompt ASK - require explicit user approval first DENY - refuse; only an out-of-band escalation can change this The one rule that shapes the API: **the learned user model must never grant authorization.** Preference learning and security are separate systems, and the separation has to be structur
Flows: classes: Verdict(allowed, needs_approval, needs_confirmation, denied, tier), PolicyError, PolicyEngine(__init__, set_autonomy, category_of, decide, approve_for_session, revoke, require, describe) | funcs: provenance_verdict, current_autonomy, resolve_policy, set_autonomy, skip_permissions | imports: from __future__ import annotations; import os; from dataclasses import dataclass; from pathlib import Path; from friday.config import DATA_DIR as _DATA_DIR
Gotchas: env-driven, confirmation-gated, secret-sensitive

## friday\power_state.py (195 lines)
Role: The note Friday leaves itself before the machine may stop. Every other capability can record its own outcome, because it is still running when the outcome arrives. These cannot. Ask Windows to restart and Friday has perhaps thirty seconds of life left; ask it to sleep and the answer may arrive after the process has been frozen mid-sentence. Whatever was going to write "that worked" does not run. S
Flows: classes: PendingPower(overdue) | funcs: boot_id, _now, remember, pending, _settle, cancel, reconcile, wait_for_settlement | imports: from __future__ import annotations; import time; from dataclasses import dataclass; from datetime import datetime, timedelta, timezone; import psutil; from friday import contracts as c
Gotchas: —

## friday\privacy.py (202 lines)
Role: A mode in which nothing leaves the machine, and an honest account of it. PRIVATE_ONLY means refused, never quietly rerouted. The failure this exists to prevent is not a leak; it is a *silent* leak. A mode that promises privacy and then falls back to a cloud model when the local one cannot answer is worse than having no mode at all, because the person stopped watching. So the contract is narrow and
Flows: classes: Refused(__init__), Guarantee(complete) | funcs: mode, private, refuses, _scope_of, guard, backend_allowed, guarantee, available_under | imports: from __future__ import annotations; import logging; import os; from dataclasses import dataclass
Gotchas: env-driven

## friday\product.py (648 lines)
Role: Turning a spoken idea into something that can actually be built. The requirements loop already asked good questions and recorded the answers. It stopped there, which is one step short of useful: the arc ended exactly where the build should begin, so the boss still had to write the requirements, the acceptance criteria and the Claude prompts himself. idea "I want a small desktop game where I contro
Flows: classes: Requirement(to_dict), Claim(settled), Readiness(can_build) | funcs: claims_in, _building, as_question, wants_to_be_challenged, verify, _readable, acceptance_for, conflicts, _touches, readiness, brief | imports: from __future__ import annotations; import logging; import re; from dataclasses import dataclass, field
Gotchas: confirmation-gated

## friday\product_stages.py (165 lines)
Role: The stages a catalogue actually goes through. Separate from `products.py` on purpose: the engine there knows about graphs, retries, quarantine and provenance, and knows nothing about SKUs. A second kind of feed - suppliers, media, anything - is a new module here rather than an edit to the engine. Nothing in this file reaches the network. Image URLs are validated at row level and fetched, if at all
Flows: funcs: _first, validate, normalize, images, process_image, enrich, classify, generate, export, build | imports: from __future__ import annotations; import re; from friday import products as P
Gotchas: —

## friday\products.py (586 lines)
Role: Product processing: a per-record graph, not a linear script. The shape that makes this worth building is that a missing image must not prevent normalisation, classification or pricing analysis - but it must prevent the export that needs the image. That is a graph statement, and a pipeline written as a sequence of `for` loops cannot make it. INGEST -> VALIDATE -> NORMALIZE -+-> ENRICH -> CLASSIFY -
Flows: classes: ProductError, StageResult(to_dict), ProductRecord(__post_init__, key_for, value, set, resolve_status, to_dict), Stage, Pipeline(__init__, process, _attempt), Quarantine, Retryable, Batch(__init__, start, done_keys, process, summarise, exports) | funcs: safe_url, provenance, hashed, _ms, ingest_csv, ingest_json, product_key, classify_duplicates | imports: from __future__ import annotations; import csv; import hashlib; import io; import json; import logging
Gotchas: —

## friday\profile.py (530 lines)
Role: The user model: what Friday learns about you, and how it corrects itself. Phase 1D gave memory a shape - kind, source, confidence, provenance - but nothing filled it in unless the model chose to call `memory_remember`. This is the layer that learns from ordinary conversation, every day, without being asked. Four stages: observe pull candidate facts out of a turn, each with the quote reconcile comp
Flows: classes: Candidate(validated, as_dict), Outcome(as_dict), ExtractionError | funcs: known_subjects, extract_candidates, same_value, decide, learn_from_turn, learn, resolve, profile, brief, explain | imports: from __future__ import annotations; import json; import os; from dataclasses import asdict, dataclass; from friday.store import FACT, INFERENCE, MEMORY_KINDS, PATTERN, PREFERENCE, Store
Gotchas: env-driven, confirmation-gated

## friday\progress_digest.py (267 lines)
Role: Spoken progress digests, composed from Hermes work-run events. Owner's ask (2026-09-04): milestones as they happen, plus a digest every ~3 minutes while work is in flight, silent otherwise. `compose()` is pure - it never touches the bridge, the store or the network - so the room loop, the UI endpoint and the tests all read the exact same composition logic. STATICALLY_CONFIRMED shapes it reads (see
Flows: classes: Digest | funcs: _normalize_detail, read_detail_level, _model_reason, _milestone_line, _digest_line, outcome_line, compose, gather | imports: from __future__ import annotations; import logging; from dataclasses import dataclass, field; from pathlib import Path; from friday.hermes_bridge import TERMINAL
Gotchas: confirmation-gated

## friday\promotion.py (429 lines)
Role: The gate between "the agent finished" and "the code is on the branch". The existing worktree manager can already promote and roll back. What it cannot do is refuse, because it was never told what would make a promotion wrong - it takes an instruction and carries it out. This module is the part that decides, and its default answer is no. A development run may be promoted when, and only when: 1. an 
Flows: classes: Decision(note, refuse, as_dict) | funcs: debris_in, global_config_in, unexpected_binaries, base_matches, touches_forbidden, out_of_scope, secrets_in, decide | imports: from __future__ import annotations; import logging; import re; import subprocess; from dataclasses import dataclass, field; from pathlib import Path
Gotchas: sqlite, spawns-proc, confirmation-gated, secret-sensitive

## friday\provider_cooldowns.py (83 lines)
Role: Durable memory of "this provider/model is capped until X". A CAPPED diagnosis (see `friday/provider_diagnostics.py`) names a provider that cannot answer for a while, not a broken request. This is the record that lets the next delegation route AROUND it instead of hammering the same capped provider on every retry - JSON under DATA_DIR, same shape as `data/autonomy.json`, so it survives process rest
Flows: funcs: _load, _save, _key, mark, active, clear | imports: from __future__ import annotations; import json; import threading; from datetime import datetime; from pathlib import Path; from friday.config import DATA_DIR
Gotchas: —

## friday\provider_diagnostics.py (234 lines)
Role: Saying what the provider actually did, instead of that it did nothing. `livekit-plugins-google` raises one sentence for a whole family of unrelated failures: google/llm.py:486 if not candidate.content or not candidate.content.parts: continue google/llm.py:510 if not response_generated: raise APIStatusError("no response generated", ...) Every chunk with no parts is skipped, and if none of them had 
Flows: classes: Diagnosis(worth_retrying, __str__) | funcs: _cap_reset_at, diagnose, describe_failure, request_fingerprint | imports: from __future__ import annotations; import re; from dataclasses import dataclass; from datetime import datetime, timedelta
Gotchas: livekit, secret-sensitive

## friday\provider_fallback.py (179 lines)
Role: History-aware provider fallback without metadata reconstruction.
Flows: classes: HistoryInspection, ProviderRequestRejected(__init__), HistoryAwareFallback(__init__, set_telemetry, chat, record_request), HistoryAwareFallbackStream(__init__, _run) | funcs: classify_provider_error, _tool_name, provider_request_fingerprint, inspect_native_history | imports: from __future__ import annotations; import asyncio; import hashlib; import json; import time; from dataclasses import dataclass
Gotchas: livekit

## friday\provider_health.py (289 lines)
Role: Provider health from evidence (PRD Requirement 9 / audit A-008, A-024). "Usable" from Hermes means authenticated: a credential exists. The live suite (2026-09-05) showed how little that says - seven providers were "usable", three actually answered, one answered with nothing. Requirement 9 is explicit: a route is only labelled usable after it has passed a current probe for the intended API mode, an
Flows: classes: Verdict(to_dict) | funcs: _parse, latest_by_provider, verdict_for, route_key, latest_by_route, assess_routes, verdict_for_route, assess, routable, probe | imports: from __future__ import annotations; import time; from dataclasses import dataclass, field; from datetime import datetime, timezone
Gotchas: secret-sensitive

## friday\providers.py (343 lines)
Role: Provider registry - the single place that decides which STT / LLM / TTS implementation the agent runs, and which concrete model ID a role maps to. Two rules this module exists to enforce: 1. Roles are separate from model IDs. Callers ask for NORMAL or DEEP, never for "gemini-2.5-flash". A LiveKit upgrade changes the table here, not the agent. 2. A model ID that the installed packages do not expose
Flows: classes: ProviderError | funcs: _flatten_literal, installed_llm_models, resolve_llm_model, missing_credentials, _require_credentials, build_stt, build_llm, fallback_role, build_resilient_llm, _share_thought_signatures, build_tts | imports: from __future__ import annotations; import os; from functools import lru_cache; from typing import get_args; import logging; from livekit.agents import inference
Gotchas: env-driven, livekit, secret-sensitive

## friday\reachability.py (496 lines)
Role: Whether a feature can actually be reached by a person using Friday. IMPLEMENTED + TESTED != PRODUCT FUNCTIONAL. That sentence is the whole module, and it was earned three times in one session: 1. Typed input called `prepare_turn` and skipped the rest of the answer pipeline. Research, claim-checking and project capture existed and were unreachable for the way Friday is actually used. 2. `sandbox`, 
Flows: classes: Finding(as_dict), Audit(dead, summary) | funcs: at_least, _declared_for, audit, _is_test, parity | imports: from __future__ import annotations; import logging; from dataclasses import dataclass, field
Gotchas: livekit

## friday\reflex.py (545 lines)
Role: A local reflex for simple commands, so "pause the music" costs no cloud tokens. Most of what the boss says to a machine is not thinking. "Open Paint." "Pause it." "Show my windows." Sending those to Gemini spends thousands of input tokens deliberating over a request that a 14MB model on this machine can read in a quarter of a second - and Friday's own measurements are that the routine commands are
Flows: classes: Reflex(acts), Deterministic(complete), DomainGated(__init__, complete, _for) | funcs: is_dangerous, enabled, admit, is_one_command, is_grounded, _missing_arguments, tool_schemas, domain_of, _agent, warm, reset, route | imports: from __future__ import annotations; import logging; import os; import re; import time; from dataclasses import dataclass, field
Gotchas: env-driven, confirmation-gated

## friday\request_shape.py (284 lines)
Role: What kind of thing the boss just said, before anything asks what it means. The local reflex path exists to act in milliseconds on simple commands. Almost everything that makes that dangerous is visible in the *shape* of the sentence rather than in its vocabulary, and shape is cheap to read: "pause the music" COMMAND act "what is playing?" QUESTION answer, do not act "should I restart my PC?" QUEST
Flows: classes: Shape(actionable) | funcs: _is_question, forbidden_spans, is_forbidden, _parts, _without, read | imports: from __future__ import annotations; import re; from dataclasses import dataclass, field
Gotchas: —

## friday\requirements.py (625 lines)
Role: Turning an idea into something that can be built, by asking about it. Friday can now read what a request means and choose capabilities for it. That covers instructions. It does not cover the thing the boss actually wants to stop using ChatGPT for: "I want to make a game where you play a lighthouse keeper." There is no plan in that sentence, and there should not be. What is missing is not capabilit
Flows: classes: Question(key), Concern, Understanding(ready, spoken) | funcs: _key, recorded, already_answered, remember_answer, _model, understand, _as_understanding, is_an_idea, project_name, current_project, ask, outstanding, about_this_project, still_blocking, capture_answers | imports: from __future__ import annotations; import json; import logging; import re; from dataclasses import dataclass, field
Gotchas: —

## friday\research_cache.py (455 lines)
Role: Research artifact cache: FR-MEM-005. Every research call in friday.toolsets.research builds its corpus from scratch - search, crawl, dedupe, rank, budget - even when the exact same question was answered an hour ago. That is not just wasted network time: it means "how fresh is what Friday just told you" has no answer anywhere in the system, because nothing records when a fact was fetched or how lon
Flows: classes: Source, Artifact(create) | funcs: _matches, classify_freshness, normalize_question, question_key, _connect, _iso, _parse_iso, _row_to_artifact, save, lookup, stale, invalidate, prune | imports: from __future__ import annotations; import hashlib; import json; import re; import sqlite3; from contextlib import contextmanager
Gotchas: sqlite

## friday\resilience.py (242 lines)
Role: What happens when the model returns nothing. Gemini intermittently answers with `finish_reason=STOP` and no content at all. The plugin raises "no response generated" (google/llm.py:512), LiveKit retries, and usually the second attempt is fine - the boss hears a pause. Twice in the logs so far it has taken two retries at session start. Three layers, in the order they fire: retry LiveKit's own, alre
Flows: classes: ToolOutcome(succeeded), TurnGuard(attach, on_tools_executed, on_item_added, on_error, rescue, describe) | funcs: is_empty_completion, read_outcome, outcomes_from, narrate | imports: from __future__ import annotations; import json; import logging; from dataclasses import dataclass, field; from friday.autolearn import payload
Gotchas: livekit

## friday\response_render.py (137 lines)
Role: Speech-only cleanup and bounded, project-anchored phrasing variation.
Flows: classes: ResponseStyleState(__init__, _load, _save, choose_line, acknowledgement, greeting) | funcs: speech_text, render_speech_stream, normalize_opening | imports: from __future__ import annotations; import json; import re; from collections.abc import AsyncIterable, AsyncIterator, Sequence; from datetime import datetime; from pathlib import Path
Gotchas: —

## friday\reversible.py (259 lines)
Role: Changing something and putting it back, provably. Batch 2C starts touching state that belongs to the person using the machine - the volume, the mute, the brightness. A live gate that sets the volume to 20, asserts it worked, and leaves it there has proved one thing and broken another, and the report says only the first: "Friday successfully changed the volume" true "Friday left the machine as it f
Flows: classes: Attempt(mutated, left_alone, clean, summary) | funcs: attempt, _restore | imports: from __future__ import annotations; from dataclasses import dataclass, field; from typing import Any, Callable
Gotchas: confirmation-gated

## friday\roles.py (306 lines)
Role: Which specialists a development run gets, and why those. The idea is Agency Agents': a library of specialist role definitions, so that "review this for security" comes with the framing a security reviewer would actually bring rather than being a generic model told to be careful. The thing that library gets wrong for Friday's purposes is scale. A hundred role definitions loaded into every run is no
Flows: classes: Role(instructions), Team(cost, instructions, as_dict) | funcs: claude_agent_for, size_of, compile_team | imports: from __future__ import annotations; import logging; import re; from dataclasses import dataclass, field
Gotchas: sqlite, livekit, secret-sensitive

## friday\routing_memory.py (144 lines)
Role: friday/routing_memory.py -- what the router learned from being wrong. The pieces for a self-improving router were already here and not joined up. `shadow_predictions` has recorded, for months, which capability was predicted against which one actually ran and whether the intent was right. `routing_corrections` holds the times the boss said "no, I meant X". Both were written and neither was ever rea
Flows: funcs: _store, fingerprint, _corrections, _outcomes, prior, forget, explain | imports: from __future__ import annotations; import os; import time
Gotchas: env-driven

## friday\runcontext.py (136 lines)
Role: Which run is "that one"? `run_id` is system identity. It is not user ergonomics, and it is not something the boss can be expected to have. Asked "how did that product catalogue job finish?" in a fresh conversation, Friday called a status-by-id tool, had no id, and asked *him* for one - which he could not know, because Friday invented it. So resolution is a first-class step. It returns a named basi
Flows: classes: Resolution(__bool__, safe_to_mutate) | funcs: _label, _matches, resolve | imports: from __future__ import annotations; from dataclasses import dataclass, field
Gotchas: —

## friday\runstate.py (132 lines)
Role: Two questions about a run, which are not the same question. is it still going? QUEUED RUNNING WAITING COMPLETED INTERRUPTED how did it turn out? SUCCEEDED PARTIAL FAILED QUARANTINED DEDUPLICATED SKIPPED PENDING Friday reported both through one field, and it cost a false statement to the boss. Handed `status: "PARTIAL"` for a catalogue that had finished processing, the model said "processing is und
Flows: funcs: split, describe, is_over | imports: from __future__ import annotations
Gotchas: secret-sensitive

## friday\runtime_control.py (356 lines)
Role: Execution ownership and pre-dispatch single-flight controls.
Flows: classes: StaleExecutionOwner, OwnerDecision, ExecutionOwner, ToolAuthorization, ActionPresentationPlan, ActionPresentationPolicy(__init__, _line, plan, present), RunExecutionArbiter(__init__, bind_speech, authorize_owner, reject_stale, authorize_tool, settle_tool, settle_exception, reserve_result_narration), GuardedMCPServerHTTP(__init__, _make_function_tool) | funcs: canonical_execution_key, _is_side_effecting, _result_body, _milestone_part, narration_milestone, _annotate_narration | imports: from __future__ import annotations; import hashlib; import json; import re; from dataclasses import dataclass; from typing import Any, Callable, NoReturn, cast
Gotchas: livekit, confirmation-gated

## friday\runtime_metrics.py (160 lines)
Role: Safe phase telemetry for the live voice runtime.
Flows: classes: PhaseEvent, RuntimeTelemetry(__init__, set_correlation, attach, mark, increment, measure, heartbeat_tick, on_user_input_transcribed) | imports: from __future__ import annotations; import asyncio; import time; from dataclasses import asdict, dataclass, field; from typing import Any, Callable
Gotchas: secret-sensitive

## friday\sandbox.py (582 lines)
Role: The execution boundary around code Friday did not write. A git worktree is version control, not containment. It stops a coding agent from corrupting the branch; it does nothing about the agent reading `.env` two directories up, opening a socket to somewhere it should not, forking until the machine swaps, or leaving a process alive after the run is "finished". Those are four different failures and 
Flows: classes: Limits, Egress(__post_init__, allows, describe), _IO_COUNTERS, _JOBOBJECT_BASIC_LIMIT_INFORMATION, _JOBOBJECT_EXTENDED_LIMIT_INFORMATION, JobObject(__init__, _create, adopt, close), Execution(ok), SandboxError, Sandbox(__init__, __enter__, __exit__, terminate, strength, environment, redacted, run) | funcs: _looks_absolute, for_development | imports: from __future__ import annotations; import ctypes; import logging; import os; import shutil; import subprocess
Gotchas: env-driven, spawns-proc, secret-sensitive

## friday\secret_broker.py (232 lines)
Role: Secret broker - credentials exist; models see aliases (Phase 4). The contract, from the north star (boss-decided): - Zero-credential start. The model NEVER receives a raw secret: not in prompts, not in logs, not in memory, not in Hermes context, not in Paperclip prompts. - Entry: the USER types the secret into a scratch file Friday opens for them (interim surface until the UI panel exists). A dete
Flows: classes: SecretBroker(__init__, home_for, _load_fernet, _read_store, _write_store, scratch_file, ingest_scratch, list_aliases) | funcs: _safe_alias, _migrate_legacy_secrets, clipboard_is_dead | imports: from __future__ import annotations; import base64; import json; import os; import re; import shutil
Gotchas: env-driven, secret-sensitive

## friday\selective.py (548 lines)
Role: A router that would rather say nothing than say the wrong thing. The deterministic router handles 23.7% of requests in 16 milliseconds and gets 13.6% of them wrong, two of those dangerously. Those numbers are not a router that needs tuning; they are a router that has never been allowed to decline. the goal is not handle as much as possible locally the goal is act locally only where the evidence is
Flows: classes: Evidence(to_dict), Decision(routes, blame, to_dict), Context(resolve) | funcs: route, abstain, reflex_direct_allowed, decide, _points_at_one_capability, _asks_for_anything, _fit, _positive_evidence, _resolve_referent, _any_capability_for, _target_of, _words_of, _arguments, _required, _missing | imports: from __future__ import annotations; import logging; import time; from dataclasses import dataclass, field; from friday import capabilities as C; from friday import request_shape as RS
Gotchas: —

## friday\self_upgrade.py (269 lines)
Role: Staged self-upgrade with automatic rollback (Phase 11; R16 / build-pack 11). The 14-step protocol reduced to its load-bearing invariants, each one enforced by state-machine order rather than narrative discipline: checkpoint -> stage -> targeted tests -> [live check] -> promote | | v (fail) v (fail) ROLLBACK <--------------- health-fail Invariants: - No stage without a rollback target (git stash ch
Flows: classes: UpgradeRefused, SelfUpgrade(__init__, _log, _git, guard_kernel, checkpoint, verification_plan, run_tests, rollback) | funcs: _normalize_path, is_kernel_path | imports: from __future__ import annotations; import json; import subprocess; import time; from pathlib import Path; from friday.execution_economics import AFFECTED, FULL, INTEGRATION, TARGETED, classify_task, verification_depth
Gotchas: spawns-proc, confirmation-gated, secret-sensitive

## friday\selfcheck.py (331 lines)
Role: friday/selfcheck.py -- Friday checks herself, and does the work herself. The owner pasted docs/MASTER_VALIDATION_PROMPT.md into the control room and said "go according to the verification prompt" (2026-09-03). The first version ran the easy half and read him a list of "phases that need you" - which he rightly called the opposite of autonomy. This one does everything a program can do on its own: it
Flows: funcs: _cap, _clock, _files_roundtrip, _files_confined, _executives, _find_agent, _archetypes, _traversal_refused, _commerce_honest, _helpers_roster, _bundle_is_memory_not_transcript, _economy_route, _deep_route, _metrics_envelope, _organisation | imports: from __future__ import annotations; import json; import os; from typing import Callable
Gotchas: env-driven, sqlite

## friday\selfdev.py (379 lines)
Role: Controlled self-development (PRD v3.1 FR-047, FR-048, FR-049, FR-050, FR-051). The loop the PRD names, as a state machine whose order is enforced by code rather than by discipline: OBSERVE -> PROPOSE -> SANDBOX -> IMPLEMENT -> TEST -> REVIEW -> REGRESSION -> BENCHMARK -> PROMOTE -> MONITOR -> [ROLLBACK] * SANDBOX (FR-048): the change is made in a git worktree on its own branch (`friday.executors.w
Flows: classes: GateRefused, Candidate(to_dict), SelfDevelopment(__init__, _transition, _reject, _journal, _audit, _pytest, observe, propose) | funcs: _live_differs_from_base | imports: from __future__ import annotations; import json; import logging; import subprocess; import time; from dataclasses import dataclass, field, asdict
Gotchas: spawns-proc

## friday\selfdev_benchmark.py (174 lines)
Role: FR-050: benchmark before promotion - the measurement a self-change is held to. `selfdev.SelfDevelopment.benchmark` compares `measure(sandbox)` against a `before` dict and rejects on regression past BENCHMARK_TOLERANCE. Until now the production loop passed no `measure`, so every candidate reached BENCHMARKED as "skipped: no performance claim" - a gate that could not fail. This module supplies the m
Flows: funcs: claims, measure, _failed, baseline | imports: from __future__ import annotations; import json; import os; import subprocess; import sys; from pathlib import Path
Gotchas: env-driven, sqlite, spawns-proc

## friday\semantics.py (593 lines)
Role: What a capability *does*, and to *what*, as machine vocabulary. Routing was lexical, and lexical routing has a specific failure that this codebase has now hit five times: two capabilities share the user's words and the wrong one wins. The sharpest example is not subtle. "open Paint" wants apps_open "what windows are open?" wants windows_list Both contain "open". No amount of example phrasing settl
Flows: funcs: _derive, for_capability, defaulted, for_request, compatible, target_for_request, target_affinity | imports: from __future__ import annotations; import re
Gotchas: —

## friday\sensitive_domains.py (128 lines)
Role: The banking/payment observation block. A PAGE ON A FINANCIAL DOMAIN IS NEVER PLACED IN MODEL CONTEXT. The rule comes from the governance pack (docs/security/, action `banking_site_model_observation: DENY`) and it has to be code rather than markdown, because the thing it constrains is not a person - it is every code path that turns a URL into text, a screenshot or DOM state that a model will read. 
Flows: funcs: enabled, _extra, _host_of, is_sensitive, refusal | imports: from __future__ import annotations; import os; from urllib.parse import urlsplit
Gotchas: env-driven

## friday\shadow.py (801 lines)
Role: Watching the selective router work, without letting it work. Every number the router has comes from a corpus this project wrote. Templates reduce the circularity and do not remove it, and the store holds **four** real utterances - all long compound dictations from one test. So the distribution that decides whether local execution is safe does not exist yet, and the only place it exists is the boss
Flows: classes: Prediction(as_row) | funcs: mode, enabled, may_act, settles, observed_reliability, attempted_to_act, _no_execution, fingerprint, argument_shape, predict, dropped, written, queue_depth, _writer_loop, _ensure_writer | imports: from __future__ import annotations; import hashlib; import logging; import os; import queue; import re
Gotchas: env-driven, sqlite, secret-sensitive

## friday\skill_ladder.py (181 lines)
Role: Skill candidates and the compaction recovery packet (Phase 7; R8/R10). SKILL LADDER (the north star's decision, Friday decides - the boss explicitly refused to adjudicate skill creation): lesson -> project memory (default for one-off facts) lesson -> skill CANDIDATE (when promotion criteria match) candidate -> validated skill (after replay/eval, versioned) Promotion criteria (R8): repeated procedu
Flows: classes: SkillLadder(__init__, _connect, capture, validate, deprecate, current, listing) | funcs: write_recovery_packet, read_recovery_packet | imports: from __future__ import annotations; import json; import sqlite3; import time; from pathlib import Path
Gotchas: sqlite

## friday\store.py (3073 lines)
Role: SQLite persistence for runs, results, artifacts and memory. Two design rules taken from auditing the donors: 1. **Memory is not a prompt cache.** Mark-L's memory_manager keeps a 2200-char JSON blob and silently deletes the oldest entries to stay under the cap, so remembering something new can erase something old with no record. Here rows are never trimmed, and recall is a query rather than a paste
Flows: classes: CompletionRefused, _Rows(__init__, fetchone, fetchall, __iter__, close), _SerializedConnection(__init__, execute, executemany, executescript, commit, rollback, close, __getattr__), Store(__init__, _migrate, _retire_superseded_shadow_table, close, _tx, register_workspace, workspaces, ensure_default_workspace) | imports: from __future__ import annotations; import json; import logging; import re; import sqlite3; import threading
Gotchas: sqlite, confirmation-gated, secret-sensitive

## friday\task_class.py (197 lines)
Role: Task complexity classification (PRD v3.1 FR-002, P0). Every request is placed in exactly one of six classes before anything is routed, so a trivial request never reaches a coding executor and a critical one never runs on the economy path: TRIVIAL one deterministic action, no model reasoning needed SIMPLE one capability call or a direct answer; fast model at most STANDARD bounded multi-step work in
Flows: classes: Classification(to_dict) | funcs: _words, _clauses, _hits, classify, route_for, risk_tier_for | imports: from __future__ import annotations; import re; from dataclasses import dataclass; from friday import execution_economics as ee; from friday.model_gateway import COMPLEX, CRITICAL, LONG_RUNNING, SIMPLE, STANDARD, TASK_CLASSES, TRIVIAL
Gotchas: secret-sensitive

## friday\trust.py (421 lines)
Role: The trust plane's three missing pieces (PRD v3.1 §4.8, §4.11, §11): 1. Risk tiers R0-R4 (FR-059). Every policy category maps to exactly one tier, deterministically, from a table - not from the model. The tier is what the approval card and the audit record say; the PolicyEngine's AUTO/ASK/CONFIRM/DENY decision stays authoritative per call. 2. SecurityAuthorization (FR-061/062, §11.2). Active securi
Flows: classes: SecurityAuthorization(__post_init__, expired, covers_target, covers_action, to_dict), AuditLog(__init__, _connect, close, _digest, record, query, verify_chain) | funcs: tier_of_category, tier_of_tool, is_security_capability, target_guard, _redact, _looks_like_token, audit, configure_audit, record_decision, expiry_in | imports: from __future__ import annotations; import hashlib; import ipaddress; import json; import logging; import sqlite3
Gotchas: sqlite, confirmation-gated, secret-sensitive

## friday\turn_timing.py (104 lines)
Role: friday/turn_timing.py -- where a slow turn's time went, and what to say about it. The owner's rule: "if latency occurs, the program should report the cause while preserving output quality". A number alone does not do that - "the turn took 14s" is a complaint, "the screen read took 11 of 14s" is a cause. So a turn is timed in named stages and the slowest stage is the attribution. This is deliberate
Flows: classes: TurnTimer(start, stop, add, total, report) | funcs: _note, host_load | imports: from __future__ import annotations; import os; import time; from dataclasses import dataclass, field
Gotchas: env-driven, livekit

## friday\ui_browser.py (298 lines)
Role: friday/ui_browser.py -- the UI's own persistent, headed browser, driven and gated. This is the "replace the ChatGPT browser" surface: a headed Chromium with a PERSISTENT profile (logins survive between runs), driven from the control room. Every navigation passes friday.browser_capability, which blocks banking / authenticated-financial pages and private network addresses BEFORE any capture (no DOM 
Flows: classes: _Worker(__init__, _ensure, _loop, call, running) | funcs: _headless, _emit, drain_events, _snap, open_url, study_url, request_act, approve_act, reject_act, pending_gates, shot_path, status | imports: from __future__ import annotations; import logging; import os; import queue; import threading; import time
Gotchas: env-driven, livekit, confirmation-gated, secret-sensitive

## friday\ui_server.py (1600 lines)
Role: Friday UI server -- a read-mostly web surface over the live system. Deliberately a SEPARATE process from server.py (the MCP/agent). It never writes to the agent state and never imports the live FastMCP server, so it cannot destabilise a running Friday (the dev-mode/watchfiles landmine stays in its box). It reads data/ada.sqlite3 READ-ONLY and asks the same modules the agent already uses for health
Flows: funcs: _compute_mem_snapshot, _mem_refresher, memory_snapshot, _now, _db_path, _connect, _rows, _val, _build, _system, _mcp_inventory, _tcp_up, _probe_gbrain, _cached, _gbrain_status | imports: from __future__ import annotations; import asyncio; import json; import io; import logging; import os
Gotchas: env-driven, sqlite, mcp-tool, livekit, spawns-proc, confirmation-gated

## friday\user_policy.py (382 lines)
Role: User-delegated permissions - versioned, audited, conversation-updatable (Phase 5 of the vnext build). The static tables in friday/policy.py answer "what does this tool class require by default". This module answers the OTHER question: "what has THIS user explicitly delegated or revoked", with three properties the static tables cannot provide: 1. VERSIONED: every change is a new row; the current st
Flows: classes: UserPolicy(__init__, _connect, grant, set_level, state_of, snapshot, level, audit_trail) | funcs: compile_level | imports: from __future__ import annotations; import json; import sqlite3; import time; from pathlib import Path
Gotchas: sqlite, confirmation-gated, secret-sensitive

## friday\vault.py (238 lines)
Role: friday/vault.py -- the Vault: Friday's memory as readable, linked markdown. "If it's not in the vault, it didn't happen." The point is that a human can open a folder and read everything the system knows, in plain files, without a database client. vault/raw/ what was captured, by day (the brain ledger + new facts) vault/wiki/ distilled knowledge, one page per subject namespace vault/outputs/ what F
Flows: funcs: _slug, _now, _stamp, _header, _write, _facts, _ledger, _runs, sync, _safe, read, tree | imports: from __future__ import annotations; import hashlib; import json; import os; import re; from collections import defaultdict
Gotchas: env-driven, sqlite

## friday\voice_brain.py (1465 lines)
Role: friday/voice_brain.py -- the conversational brain behind the UI's voice. Talk to Jarvis from the browser: the page does speech-to-text (Web Speech) and text-to-speech; this module is the middle. It recalls shared memory, calls the SAME Gemini model Friday uses (friday.providers + GOOGLE_API_KEY from .env), and routes a few explicit commands to REAL actions -- open a page in the gated browser, sear
Flows: funcs: _persona, _thinking_budget, _is_read_only, _surface, _asked_for, _run_clock, _run_files, _keep_literal_paths, _run_hermes, _run_work, _run_desktop, _capability_menu, _run_extract, _run_web, _capability_tool | imports: from __future__ import annotations; import logging; import os; import re; from friday.write_licence import OWN_WRITES as _OWN_WRITES; from friday.write_licence import own_write_licensed as _own_write_licensed
Gotchas: env-driven, livekit, confirmation-gated, secret-sensitive

## friday\voice_input.py (50 lines)
Role: Keep LiveKit microphone state aligned with actual audio processing.
Flows: classes: VoiceInputGate(__init__, accepting_audio, set_muted, attach, _sync_from_room, describe) | funcs: _is_microphone | imports: from __future__ import annotations; from friday.runtime_metrics import RuntimeTelemetry
Gotchas: livekit

## friday\workspace.py (273 lines)
Role: Workspace identity: the tenant boundary private state is not allowed to cross. FR-PRV-002 / architectural invariant 6. One FRIDAY install can hold several workspaces - a user, a company, a brand, an agency's client - and the PRD's acceptance criteria are absolute about what that must mean: a memory query in Workspace A cannot return Workspace B data; secrets are namespaced per workspace/user; brow
Flows: classes: InvalidWorkspace, UnknownWorkspace, CrossWorkspaceRefused, use(__init__, __enter__, __exit__) | funcs: valid_id, slug, current, _recorded_active, reset_override, resolve, guard | imports: from __future__ import annotations; import os; import re
Gotchas: env-driven, sqlite, secret-sensitive

## friday\world_monitor.py (77 lines)
Role: Canonical World Monitor dashboard URLs and observed-destination checks.
Flows: classes: WorldMonitorView(url), DestinationVerification | funcs: build_world_monitor_url, _one, _same_number, verify_world_monitor_destination | imports: from __future__ import annotations; from dataclasses import dataclass; from decimal import Decimal, InvalidOperation; from urllib.parse import parse_qs, urlencode, urlparse
Gotchas: —

## friday\worldmonitor.py (183 lines)
Role: The World Monitor dashboard, as parameters rather than as one magic string. What was there: url = "https://worldmonitor.app/" webbrowser.open(url) return "Displaying the World Monitor on your primary screen now, sir." Three separate problems in four lines. It opened the marketing landing page rather than the dashboard, so a request for the global intelligence view got something that is not a view 
Flows: classes: WorldMonitorView(__post_init__, url, focused_on, at, over) | funcs: parse, shows | imports: from __future__ import annotations; from dataclasses import dataclass, field, replace; from urllib.parse import parse_qs, urlencode, urlparse
Gotchas: —

## friday\write_licence.py (102 lines)
Role: The owner's words are the licence for a write. Nothing Friday read is. PRD Requirement 26 (prompt-injection resistance), audit A-036. One table, one check, used by BOTH conversational paths so they cannot drift: * the UI brain (`friday.voice_brain._run_capability`) - families and operations as its `use_capability` function sees them; * the LiveKit agent (`agent_friday.FridayAgent.use_capability`) 
Flows: funcs: _words, licensed, refusal, own_write_licensed, capability_licensed | imports: from __future__ import annotations; import re
Gotchas: livekit, confirmation-gated

## server.py (34 lines)
Role: Core build snapshot telemetry
Flows: imports: import os; from mcp.server.fastmcp import FastMCP; from friday.tools import register_all_tools; from friday.prompts import register_all_prompts; from friday.resources import register_all_resources; from friday.config import config
Gotchas: env-driven, mcp-tool

## agent_friday.py (2574 lines)
Role: FRIDAY - Voice Agent (MCP-powered) =================================== Iron Man-style voice assistant. Speech pipeline and orchestration run on LiveKit; every tool comes from an MCP server exposed as an MCPToolset. Run: python start.py - both halves in one console (preferred) uv run agent_friday.py dev - agent only, LiveKit Cloud mode uv run agent_friday.py console - agent only, text-only console 
Flows: classes: FridayAgent(__init__, tts_node, _refuse_unbacked_claim, list_capability_areas, search_capabilities, use_capability, _keep_group_open, _call_capability) | funcs: _log_to_its_own_file, announceBuild, _escape_lone_backslashes, _repair_windows_path, mcp_sse_url, session_config, turn_handling_for, with_turn_detector, _env_float, turn_detector_for, temporal_context, build_instructions, _clean_for_speech, _ack_kind, _ack_line | imports: import asyncio; import json; import logging; import os; import pathlib; import re
Gotchas: env-driven, sqlite, livekit, confirmation-gated, secret-sensitive

## main.py (6 lines)
Role: (no module docstring; see key symbols)
Flows: funcs: main
Gotchas: —

## start.py (149 lines)
Role: !/usr/bin/env python3
Flows: funcs: log_msg, find_runtime, check_sock, check_ready, exec_sub, stream_out, dispatch, terminate_proc, run_preflight, main | imports: import os; import sys; import time; import socket; import threading; import subprocess
Gotchas: env-driven, livekit, spawns-proc

---
# PRIMER B — Services: `toolsets` / `executors` / `fabric_adapters`

Source of truth: `git ls-files friday/toolsets friday/executors friday/fabric_adapters | sort` → **76 files**
(7 executors + 35 fabric_adapters + 34 toolsets). Read in full; big files paged with
`offset`/`limit`. No source edits. Generated 2026-09-23.

## Cross-cutting conventions (read once)

- **Toolsets** are capability-runtime implementations: `run: c.Run` first, return
  `c.ActionResult`, never a bare string. Every toolset has `_gate(run, tool_id, engine)`
  → `CANCELLED + APPROVAL_REQUIRED` when policy denies, plus `_scoped()` adding
  `execution_scope` (`local_machine` | `network` | `agent_runtime` | `user_device`).
  Writes are verified by read-back; success without observation is `PARTIAL`, not success.
- **Executors** hand development work to another agent (Claude CLI / Hermes) and own the
  `run_id ↔ session_id` mapping in SQLite, the permission profile, the question channel,
  and the `ActionResult` verdict.
- **Fabric adapters**: one module per external upstream. Module-level
  `DESCRIPTOR: fabric.Provider` + optional `start/stop/health/call`. Nothing imported by
  core at startup; failure to import = `UNAVAILABLE`, never a boot break (NON_NEGOTIABLE 15).
  Shared helpers: `_cli_adapter.make()` (CLI shape), `_skillpack` (markdown-only packs,
  lazy single-file reads, `MAX_CHARS=40_000`, empty-dir gitlink guard).

---

## 1. `friday/executors/` (7)

### `executors/__init__.py`
Role: package doc — executor ≠ model provider; ADA gives a TaskBundle, answers questions,
enforces policy, verifies. Exports `ClaudeCodeExecutor, TaskBundle`.

### `executors/brokers.py`
Role: the two brokers that must stay separate. Flows: `QuestionBroker` (decisions >
FACTs > ask-once > persist; `classify()` order: `must_ask` → `conflicting()` → evidence →
`low_risk` default; `Verdict`: ANSWER_FROM_PROJECT / ANSWER_LOW_RISK_DEFAULT / WAIT_USER /
CONFLICT) and `PermissionBroker` (pre-launch `--allowedTools/--disallowedTools`; `EXPLORE`
read-only vs `BUILD` explicit list; `OUT_OF_SCOPE` never granted; `denials_from()` readback).
Gotchas: broad `Bash(*)` beats narrow rules — allowlist must be explicit; PREFERENCE/PATTERN
is context-only, INFERENCE never decides; `must_ask` regex checked *before* memory.

### `executors/cli.py`
Role: Claude Code CLI v2.1.233 as probed, not as documented. Flows: `Launch.argv()`
(`-p --output-format stream-json`, prompt on **stdin** — trailing positional is swallowed as
tool name; `disableAllHooks:true`; never move `CLAUDE_CONFIG_DIR`), `parse()` stream-json →
`Event`, `Run.start/events/cancel` (tree-kill via job object / `taskkill /F /T`).
Gotchas: `AskUserQuestion` + `PermissionRequest` hook don't fire in `-p`; `manual` is the
CLI's name for `default`; `bypassPermissions` always refused; user hooks hang without bash/node.

### `executors/claude_code.py`
Role: ADA→Claude run owner (`TaskBundle` → prompt → `cli.Run` → `finish()` → promote).
Flows: `TaskBundle.prompt()` (goal/context/role/constraints/acceptance/reporting),
`execute()` (governor admit → `runs.open` → stream events → `finish()`),
`continue_run()` (attach/resume/restart), `promote/reject/rollback/cleanup` via worktrees.
Gotchas: verdict = `changed_files()` + denials, never Claude's summary; no acceptance →
max PARTIAL; isolated runs inspect the worktree dir, not launch dir; sandbox backend =
allowlist egress only (`CODING_HOSTS`); prompt-questions only logged (`WAITING_QUESTION`),
never answered back into closed stdin.

### `executors/hermes.py`
Role: Hermes as mandatory engine (NON_NEGOTIABLE 2) through the same
`execute(bundle)→ActionResult` contract. Flows: `to_bridge_bundle()` field-for-field
(context→known_facts), `plan_delegation()` for model/effort/route, `supervisor().delegate()`
→ COMPLETE/PARTIAL/failed mapping. Gotcha: claude_code stays as explicit fallback only.

### `executors/runs.py`
Role: durable runs in `executor_runs` — transcript is accelerator, DB is truth. Flows:
`open/running/saw/close/cancelled`, `reconcile()` (LIVE+dead pid → INTERRUPTED, not FAILED),
`recover()` ladder attach→resume→restart→ask, `brief()` voice-readable. Gotchas: exit 0 ≠
SUCCEEDED (ActionResult decides); pid identity = pid+`pid_started_at` (name match caused
false-alive on recycled node pids); session lookup best-effort, resume failure falls to restart.

### `executors/worktrees.py`
Role: isolation boundary — worktree → verify → promote/reject → rollback. Flows: `create`
(`-b worktree-<name>` under `.claude/worktrees`), `commit` (headless runs leave uncommitted),
`promote` (merge `--no-ff`; conflict → REJECTED + `merge --abort`), `rollback` (`revert -m 1`,
never `reset --hard`), `unlock/cleanup/stale`. Gotchas: CLI leaves worktree LOCKED;
`verify()` checks git-common-dir + toplevel (empty subdir inside repo fails safe); promote
refuses unless checkout is on target branch; base_commit recorded, never assumed.

---

## 2. `friday/fabric_adapters/` (35)

### `fabric_adapters/__init__.py`
Role: contract doc — `DESCRIPTOR` + `start/stop/health/call`, lazy discovery via pkgutil.

### `fabric_adapters/_cli_adapter.py`
Role: single copy of CLI shape (`make(descriptor, bootstrap, commands)`). Flows: `start`=None,
`health`=version-check probe, `call`=one declared command, secrets as env never argv.
Gotcha: upstreams invisible in UI; reached via `capability_use(family, operation)`.

### `fabric_adapters/_skillpack.py`
Role: shared loader for markdown-only upstreams (SKILL mode). Flows: `cloned()` (non-empty
dir — gitlinks are empty dirs on fresh checkout), `health()` (READY only if entry exists
*and* ≥32 bytes), `read()` (40k cap), `catalogue()` (names only). Gotcha: eager load of
e.g. 348-file packs = token failure; `call()` reads one file.

### `fabric_adapters/agent_reach_transcribe.py`
Role: Agent-Reach narrowed to `transcribe` (yt-dlp+ffmpeg→Groq Whisper) + `doctor`.
Flows: absolute venv python path, `GROQ_API_KEY` from broker as env, ffmpeg via PATH/
`FFMPEG_DIR`/drive-root probe. Gotchas: `doctor` open (key not on descriptor or probe
closes too); READY without ffprobe is DEGRADED.

### `fabric_adapters/agenticseek_cli.py`
Role: agenticSeek local autonomous agent as isolated CLI worker. GPL-3.0 → CLI only
(`__post_init__` refuses ADAPTER). `version` open, `ask` gated `orchestration.local_agent`,
`paid` compute. Gotcha: owns its own browser + local model.

### `fabric_adapters/agents_team_pack.py`
Role: agents-team content pack (8 archetypes, 13 rules, 4 SKILL briefs), one file at a time.
Generator (`scaffold.py/lint.py`) never run (NON_NEGOTIABLE 1). MIT.

### `fabric_adapters/anythingllm_research.py`
Role: AnythingLLM RAG over REST (`ANYTHINGLLM_URL`, Bearer `anythingllm_api_key`). MIT root,
AGPL `open-computer/` subtree untouched. `workspaces/ask/documents` all open reads; never
spawns the instance. Gotcha: chat = slow LLM call, 60s timeout.

### `fabric_adapters/claude_subagents.py`
Role: 158 VoltAgent subagent briefs, 10 categories. `agents/find_agent/agent_category`
names-only; `agent` allowlisted against frontmatter index (bare name resolves only if
unique; `../` refused). MIT.

### `fabric_adapters/codebase_memory.py`
Role: tree-sitter repo graph binary (`third_party/bin/cbm`, 7763 nodes/39.5k edges).
Flows: one-shot `cli --json <tool>` (stateless across restarts) + warm daemon in `start()`
(~14s cold → ~4.5s). Gotchas: `install` never run (rewrites 43 client configs); daemon binds
127.0.0.1:9749 regardless of config (B-002, loopback, lazy activation bounds it); payload =
last JSON line with `content` key (log-tolerant).

### `fabric_adapters/company_playbooks.py`
Role: auto-company 14 executive playbooks + ~36 business skills, one file at a time.
Agent loop never run. MIT-via-package.json (no LICENSE file — recorded honestly).
Gotcha: some playbooks in Chinese (model-readable).

### `fabric_adapters/diagram_skill.py`
Role: diagram-design single skill + `references/` on demand (39 types). Friday renders with
`workbench_write/preview`; no upstream code runs. MIT.

### `fabric_adapters/dummy.py` / `dummy_backup.py`
Role: deterministic test instruments. `BEHAVIOUR` (ok/start_fails/degraded/auth/unavailable/
call_raises), `STARTS` counter proves idempotent activate; backup always READY (`cheap` vs
`free` so router orders dummy first) to prove `call_with_fallback`. Stay in tree like a multimeter.

### `fabric_adapters/graft.py`
Role: conceptual code context (`build/check/ask/grep/map/callers/skeleton/blast`),
deterministic tree-sitter only. No daemon; graph files on disk. Gotchas: `init` never run
(rewrites `.claude/`, AGENTS.md); `build` appends `graft/` to `.gitignore` (declared);
`build --deep` not exposed (spend decision); telemetry forced off (`DO_NOT_TRACK=1`, opt-out noted).

### `fabric_adapters/graphiti_memory.py`
Role: Graphiti temporal-graph *feed* into the one shared memory, never a second store.
Needs `graphiti_core` + Neo4j/FalkorDB; else UNAVAILABLE (never crashes, never starts a DB).

### `fabric_adapters/gstack_process.py`
Role: gstack 61 review/QA/ship methodologies as text; `route()` ranks by upstream `triggers:`
frontmatter with rarity-weighted tokens. `DENIED` withholds browser/host-setup/iOS flows
(Friday owns browser policy). Gotcha: prose mentions `/scrape` etc. — inert here.

### `fabric_adapters/harness_templates.py`
Role: awesome-harness-engineering templates (AGENTS/PLAN/IMPLEMENT/HARNESS_CHECKLIST).
`checklist` structured for the self-build completion gate; reading list deliberately not an
operation. CC0-1.0.

### `fabric_adapters/mausbot_skills.py`
Role: OpenMausBot 2 skills only (`phone-harness`, `create-verification-skill`). Electron app
never run; `enterprise/` proprietary subtree unreachable by allowlist. Apache-2.0 root.

### `fabric_adapters/maxun_scraping.py`
Role: Maxun robots over REST (`MAXUN_API_URL`, `x-api-key` — never `Authorization`).
AGPL → SIDECAR only. `robots/runs/results` open; `run_robot` needs `scraping.run` +
confirmation. Never spawns postgres+minio+browser stack. Gotcha: bare-id validation (`/` refused).

### `fabric_adapters/medusa_commerce.py`
Role: Medusa v2 commerce over admin REST (`MEDUSA_BACKEND_URL`, Basic `secret:` — Bearer
refused upstream). MIT core, enterprise subtree untouched. Reads open; product/inventory
writes need `commerce.write` (never retried — a retried POST is two products); payments/
refunds not operations. Never spawns store.

### `fabric_adapters/mem0_memory.py`
Role: Mem0 as extract-only feed (`extract` → candidate facts → `friday.profile` admission).
Stores nothing (one-memory rule). UNAVAILABLE until `mem0ai` installed. Apache-2.0.

### `fabric_adapters/open_design.py`
Role: open-design 162 skills + 153 systems + 114 templates; `catalogue/route` names-only,
single-file reads; Friday builds via workbench/Hermes (runner product never run). Per-template
licence gate, fail-closed (35 carry own LICENSE). Apache-2.0 root.

### `fabric_adapters/open_notebook_research.py`
Role: Open Notebook over FastAPI (`OPEN_NOTEBOOK_URL`, optional Bearer password).
`notebooks/notebook/ask` open reads (`ask` = open-session + execute-turn in one call);
`add_source` needs `research.write`. Never spawns SurrealDB. MIT.

### `fabric_adapters/openmontage_media.py`
Role: OpenMontage Backlot board reads (`OPENMONTAGE_URL`, `/api/health|projects|state`).
AGPL → SIDECAR; no auth/write route upstream, so no permission. Never spawns render pipeline.

### `fabric_adapters/openworker_cli.py`
Role: OpenWorker optional coding worker (`version/plan/run`); Hermes stays mandatory.
`run` needs `coding.workspace_write`; `bypass-approvals` mode not exposed. MIT.

### `fabric_adapters/postiz_social.py`
Role: Postiz scheduling over `/public/v1` (`POSTIZ_API_URL`, raw-key `Authorization` —
no `Bearer` prefix). AGPL SIDECAR. Reads open; `schedule` needs `social.publish` + human
confirmation; writes never retried. Never spawns app.

### `fabric_adapters/presentation_skill.py` (`adhd_mode`)
Role: i-have-adhd opt-in output mode (action first). `guidance` + `catalogue` only.

### `fabric_adapters/prompt_skill.py` (`prompt_master`)
Role: prompt-master prompt-engineering skill (root SKILL.md + `references/` on demand,
catalogue-allowlisted). Writing family; activates only on explicit request. MIT.

### `fabric_adapters/role_recipes.py`
Role: agency-agents 318 role recipes across 13 divisions. `catalogue` names-only, `recipe`
one file allowlisted against catalogue; no bulk read (NON_NEGOTIABLE 7).

### `fabric_adapters/science_skills.py`
Role: 163 science procedures with *computed* per-skill licence gate (fail-closed; docx/pdf/
pptx/xlsx carry Anthropic no-extract terms). `catalogue/search` cheap, `skill` one file,
`blocked` diagnostic. Licence scan cached.

### `fabric_adapters/scrapling_parse.py`
Role: Scrapling parse-only (`parse/fields/similar/by_text`, ≤200 results). Fetchers
(requests/curl/Playwright) deliberately not installed — `html` required, fetch via
`web_fetch` so egress stays in netguard; no second browser (NON_NEGOTIABLE 11). Adaptive
fingerprints opt-in under `config.DATA_DIR`. Version-pinned health (DEGRADED on drift). BSD-3.

### `fabric_adapters/security_skills.py`
Role: 818 Anthropic security procedures, dual-gated. `catalogue/search` open (index.json,
knowing ≠ doing); `skill` needs `authorized_scope` arg + `risk=restricted` +
`security.authorized_scope` permission. Apache-2.0.

### `fabric_adapters/smartstore_commerce.py`
Role: Smartstore OData v4 fallback commerce (`SMARTSTORE_URL`, Basic `PublicKey:SecretKey`).
Numeric-id guard; `$filter/$top/...` forwarded, `$top` ≤100. Orders read-only (checkout
side-effects). AGPL SIDECAR; Medusa preferred.

### `fabric_adapters/strix_pentest.py`
Role: Strix agentic pentest CLI (`version` open, `test --target --instruction` gated
`security.authorized_scope`; single-argv placeholders prevent injection). Expensive/model.
Apache-2.0; key via env never argv.

### `fabric_adapters/writing_skill.py` (`no_ai_slop`)
Role: no-ai-slop finalisation pass (`guidance/checklist`). Router treats as final step,
not per-sentence rewrite.

---

## 3. `friday/toolsets/` (34)

### `toolsets/__init__.py`
Role: one-line contract — each toolset returns ActionResult.

### `toolsets/adversarial.py`
Role: FR-008 panel (`decision_deliberate`: disagreements preserved, judge UNAVAILABLE →
failed) + FR-012 (`change_review` independent reviewer). Verification cites coverage.

### `toolsets/audio.py`
Role: Windows Core Audio *sessions* (not processes). `audio_sessions/volume/mute`,
`audio_master_volume` (stronger policy, never default reading of "turn it down").
`sessions` via pycaw; reversible via `friday.reversible`; ±2% `close_enough`. Gotchas:
import-safe off Windows (UNSUPPORTED, registry stays up — ubuntu CI lesson); COM
init per-thread in `finally`.

### `toolsets/automations.py`
Role: §14 DAG automations (manual/scheduled), OS owns schedule (`ADAAutomation*` tasks).
`TOOLS` allowlist only (no shell step, no fs writes, no money); `needs` deps, skip-on-fail,
retry ≤3, ≤20 steps; `{{placeholders}}` resolved from context; steps run in-process (no MCP
dependency at 08:00). Gotcha: create validates trigger+graph at save time.

### `toolsets/brain.py`
Role: shared-brain executor face (`recall/remember/entity/forget` via `SharedBrainAdapter`).
`recall` down → OBSERVED miss (never blocks objective); `remember` needs provenance,
`AdmissionRefused` → failed. Domain-prefix binds `brain_recall` here.

### `toolsets/brightness.py`
Role: WMI brightness (`WmiMonitorBrightness*`). Missing instance → `NOT_CONFIGURED`, never
keystroke fallback (unverifiable); floor 10% (never voice-to-dark); `revert_to_policy`
alternative restore. PowerShell, 20s timeout.

### `toolsets/connectors.py`
Role: connector plane executor face (`list/describe/connect/verify/smoke/status/repair`).
`done`→succeeded, `human_step`→cancelled (parks at USER_REQUIRED, no retry burn).

### `toolsets/desktop.py`
Role: mouse/keyboard takeover — most dangerous capability, 3 construction layers:
CONFIRM `DESKTOP_CONTROL` (FULL can't self-approve) + one-shot nonces bound to exact action +
code `forbidden()` (money/credentials/destruction/sys-settings/outward-without-text, no off
switch). Fresh capture every step; `PLAN_TTL` 180s; ≤8 steps; click ≥0.65 confidence;
`desktop_stop` AUTO global flag. Gotcha: prompt refusals aren't refusals — code is.

### `toolsets/documents.py`
Role: read PDF/DOCX/XLSX/PPTX/ZIP that `files_read` calls binary (pypdf, 40k chars, 200 rows,
page/sheet select; scanned-PDF flag). Same fsjail as files. Deliberately no `run` action,
no model summarise inside tool, no audio/video (no ffmpeg). Encrypted PDF without blank
password → `DocumentError`.

### `toolsets/executor.py` (`ada_ask`)
Role: the missing middle — coding agent's question channel. Resolves live dev run from
store (not process-local), classifies via `broker_for`, SUCCEEDED with authority-tagged
answer (persisted as USER vs IMPLEMENTATION decision) or PARTIAL + `WAITING_QUESTION` in DB
(survives restart). No fifth "make something up" outcome.

### `toolsets/files.py`
Role: Phase 1C jailed fs (fsjail first, write-then-read-back verify). Reads AUTO; writes/
recycle ASK; permanent `files_delete` separate (nonce-bound; Friday-owned `ARTIFACTS_DIR`
exempt, else confirmation). Search: 6s/`MAX_HITS`/20k-examined caps, prune
`.venv/node_modules/.git/...` via `os.walk` (rglob can't prune — 300s MCP-ceiling lesson);
reparse-point aware. Gotcha: `status --porcelain` sliced per-line (leading-space bug).

### `toolsets/hardware.py`
Role: Batch 2A physical inventory (battery/disks/displays/network via psutil). Read-only.
Replaces 91 unverifiable pyautogui keystrokes (volume key vs pycaw read-back lesson).
Skips virtual filesystems + empty optical drive.

### `toolsets/identity.py`
Role: which-account resolution (12 answers to "open YouTube"). Named → remembered FACT
preference → last-used → ask-once-and-remember. Preference = personalisation, never
authorisation. Scope `user_device`.

### `toolsets/manifest.py`
Role: capability manifest runtime (`capability_manifest`, read-only, nothing activated).

### `toolsets/media.py`
Role: Phase 1F Spotify without API creds: `WM_APPCOMMAND` to Spotify's own window (not global
keys — browser steals them), verified by title change (idle `spotify free/premium`, ad titles
excluded). No-change → PARTIAL. `play` = search URI + targeted transport.

### `toolsets/memory.py`
Role: Phase 1D durable facts (kind/source/confidence; `spoken_form` hedges INFERENCE so DB
distinction survives speech; supersede never delete). `remember/recall/search/forget/
provenance/export`, project decisions/context/resume, `session_recap` idempotent, scope
`agent_runtime`.

### `toolsets/model_gateway.py`
Role: Hermes MODEL_GATEWAY runtime (`providers` with ledger-evidenced health vs credential
state; `infer` one bounded inference attributed to objective; `usage/pressure/diagnostics/
trace`). Failures are ActionResults, never bare strings.

### `toolsets/music.py`
Role: account-free playback via YouTube+yt-dlp+ffplay (Spotify desktop automation measured
unreliable 0/5). Stream via `android` client first (web 403s), download fallback; evidence =
`ffplay pid alive + exact title`. Moods = query phrasing, no fake audio analysis.

### `toolsets/objectives.py`
Role: Phase 3 durable compiled task graphs. Deterministic `plan_objective` (regex clauses →
capability port; unmapped → FAILED/CAPABILITY_MISSING task, PARTIAL run — never hallucinated);
`start/status/list/pause/resume/cancel/history`. Store-level, lease-free; watchdog observes
at task boundaries. Hard to start, trivial to stop.

### `toolsets/power.py`
Role: lock/sleep/hibernate/shutdown/restart on `LOCAL_MACHINE`. Accepted = `INITIATED`
(ExitWindowsEx/Lock semantics), settled later by boot-identity reconcile — not SUCCEEDED.
No force on ordinary path (own confirmation), no hibernate→sleep substitution, availability
from `SYSTEM_POWER_CAPABILITIES` (not legacy `IsPwrSuspendAllowed`). 30s grace + cancellable
callback; `power_cancel`.

### `toolsets/processes.py`
Role: Batch 2D pid+create_time+image identity (recycled-pid lesson ×3). `close` (WM_CLOSE,
may prompt/decline) vs `terminate` (unconditional, own yes); both wait-then-look. Triple
critical guard (names + pid {0,4} + own chain); `own_chain` never killed. Token re-check
before action.

### `toolsets/products.py`
Role: catalogue pipeline capabilities (transport extraction from MCP adapter; domain in
`products.py`). Store is authority (restart-safe `run_id` continuity + `_active_run_id`
"those" resolution); failed rows = results not transport errors; catalogue+export jailed;
mutating calls refuse to guess (`safe_to_mutate`); FAILED vs `needs_attention` literal.

### `toolsets/profile.py`
Role: user-model runtime over `friday/profile.py` (transport extraction; domain untouched).
`get/explain/learn_from_turn/open_conflicts/resolve_conflict`; writes verified by read-back.

### `toolsets/reminders.py`
Role: Phase 1G OS-scheduled reminders (`ADAReminder*` + SQLite row marked by fire script).
`parse_when` natural language; creation verified by `schtasks /Query` (not returncode);
fired history answerable. Survives exit/sleep/reboot; asyncio timer would not.

### `toolsets/research.py`
Role: FAST (`web_answer` one grounded call ~3s) / RESEARCH (`crawl_all` parallel →
trafilatura markdown, Chromium only when <400 chars) / DEEP (`web_deep_research`
search→crawl→rank→budgeted corpus 3k/18k chars, cited, fingerprinted, deduped).
`ADA_CRAWL_ENGINE` trafilatura default; crawl4ai opt-in (41 pkgs + TLS bump — documented no).

### `toolsets/schedules.py`
Role: FR-041/042 persisted schedules + conditional monitoring on top of automations-style
tasks + objective engine (same ledger/policy). Triggers once/daily/interval/manual; budgets;
ASK-only pre-approvals (never CONFIRM); delivery session/toast/none; conditions
(`always/task_output/task_status/any_failed`) evaluated in code — false → `suppressed` row,
no delivery (no-noise rule). Async `fire()` entry.

### `toolsets/screen.py`
Role: pointing only (fractions not pixels — model sees downscaled frame; tip-on-control
arrow in Friday orange). Below 0.45 confidence draws nothing ("not visible" first-class).
Never moves mouse (that's `desktop`).

### `toolsets/selfdev.py`
Role: FR-047..051 controlled self-dev (`run` OBSERVE→…→BENCHMARKED, never promotes;
`promote` needs human exact-action yes; `rollback/status`). Patch = unified diff applied in
sandbox; thread-locked candidates.

### `toolsets/system.py`
Role: Phase 1A machine state/apps/volume/clipboard. `apps_open` succeeds only on observed
process (Mark-L Popen-truth lesson); provenance-refused web-lifted objectives BLOCKED
(not asked); clipboard with retry; volume via endpoint read-back. `APPROVAL_PREFIX` home.

### `toolsets/vision.py`
Role: Phase 1E camera/screen on demand (open-per-call, 5-frame warmup, 30s staleness cap,
artifact PNG+sha256 per capture). Numeric confidence → status (≥0.75 assert, <0.45 ask for
better view, else hedged PARTIAL — honesty as state machine). Gemini `ADA_VISION_MODEL`,
30s deadline. `locate_in_frame` feeds desktop/screen.

### `toolsets/web.py`
Role: Phase 1B search/fetch/news + Playwright (brave→tavily→duckduckgo recorded in evidence;
stub returns eliminated → NOT_CONFIGURED). Browser not kept running; computer-use loop donor
minus auto-ack of safety confirmations; egress via netguard/sensitive_domains; world/finance
monitors. Gotcha: separate `BROWSER_SCOPE=local_machine` vs `network`.

### `toolsets/windows.py`
Role: Batch 2B window objects (not keystrokes): list/focus/min/max/restore/arrange by
verified state (`isMaximized` read-back). No close (destructive). `pygetwindow` optional —
off Windows answers UNSUPPORTED (registry-survives lesson). 40-window voice cap.

### `toolsets/workbench.py`
Role: generate→validate→serve→iterate sites. Real files under `data/workbench/<name>`
(`SAFE_NAME`), `validate()` before announce, loopback-only lazy server opened in *his*
Chrome (no embedded UI, no `file://` — argv-injection + relative-path reasons).

### `toolsets/youtube.py`
Role: YouTube as data: API (1-unit lists, full stats) preferred, Atom feed fallback (no key/
quota, documented endpoint — current runner since key 403s). `search.list` 100 units → resolve
channel ONCE and remember (`youtube.channel.<slug>`), uploads playlist for recents. Handle/
name resolved via page then remembered (prevents fan-channel drift).

---
# PRIMER C — tests/ + scripts/ map

Source: `git ls-files tests scripts` = 291 tracked Python files (224 tests/*.py + 67 scripts/*.py); 22 untracked on disk; **313 total .py on disk**. No source edits made for this primer.

Method: every file read in full (AST: module docstring + test_ funcs + friday.* imports); big files paged with offset/limit (e.g. test_invariants.py 748 lines, prd_ledger.py 1271 lines, soak.py 826 lines, test_model_gateway.py 751 lines).

## Golden oracle + verify script roles (summary)

- Golden oracles (`scripts/golden_*.py`, 30 files): live end-to-end journeys run against the real machine with verified ActionResult evidence, not absence-of-exception. Families: phase goldens 1a-1g (system/web+jail/files/memory-restart-across-processes/vision-on-demand/spotify/browser-automation); domain goldens (voice, audio, music, youtube, power, processes, windows, workbench, worktree, executor, router, research, temporal, profile, products, product_mcp, product_agent, compound_turn, continuity, continuous_run, autolearn, automations, corpus, jarvis, agentability). Exit 0 only if every journey produced verified result.
- Verify scripts: `scripts/verify_mcp.py` (running MCP server matches tree, every tool reachable); `scripts/verify_all.py` (aggregate gate); `scripts/verify_hermes_engine.py` (Hermes execution-engine checks); `scripts/upstream_lock.py --check` (upstream lock vs pinned clones has not drifted); `scripts/restart_friday.py --check` (server matches tree); `scripts/release_gate.py` (first-release acceptance); `scripts/baseline_suite.py` (deterministic N-chunk pytest gate, Windows-first, one log per chunk + summary).
- Promotion/eval: `scripts/promotion_1f.py`, `scripts/evaluate_selective.py`, `scripts/build_corpus.py`, `scripts/benchmark_reflex.py` (frozen LEGACY_REGRESSION_SET), `scripts/fabric_code_intel_ab.py` (code-graph vs grep A/B), `scripts/fabric_secret_isolation.py` (M6: no live secret value in provider store), `scripts/soak.py` (A-051 workload-driven endurance, real control plane + fake workers, PASS/SMOKE/INCOMPLETE/FAIL).

## Coverage gaps (honest)

- UI/E2E thin: only `tests/e2e/friday-ui.spec.ts` + `tests/test_ui_server.py`, `test_voice_brain_ui.py`, `test_jarvis_screen.py`; no Playwright python suite in tests/.
- Live-provider paths are `@live`/skipped by default (`tests/live/test_live_providers.py`, `test_phase1b.py` x2); real STT/LLM/TTS + Spotify/cloud only proven by golden_* manual runs.
- Soak/endurance + Windows packaging (`scripts/soak.py`, `scripts/build_windows.py`, `scripts/validate_exe*.py`) have scripts but no automated test asserts their verdicts in CI.
- New untracked skill tests (`test_skills_golden.py`, `test_skills_units.py`, `test_skills_extensions.py`, workspace/isolation, route_fitness, prompt_transport) not yet in the tracked gate.
- Adversarial/injection coverage is fixture-driven (`fixtures/injection_pages/*.html` + `test_injection_pages.py`, `test_adversarial.py`) — no fuzzing/property corpus beyond `build_corpus.py` dev/calibration/holdout split.

## tests/ — one section per file

### `tests/_tmp_probe_ids.py` (15 lines)

Covers: No docstring; 1 test(s).
Tests[1]: test_probe

### `tests/conftest.py` (179 lines)

Covers: Shared test fixtures. The event-loop fixture exists because of a real, confusing failure: test_agent_session_accepts_turn_handling_without_deprecated_args passed when run alone and failed when run after the capability-router tests, with RuntimeError: There is no current event loop in thread 'MainThread' `asyncio.run()` creates a loop, runs, then closes it and leaves the policy with `_set_called = True` and no loop. Anything that later calls `get_event_loop()` - AgentSession construction does - t
Exercises: friday, friday.toolsets.memory

### `tests/fake_claude_cli.py` (52 lines) [UNTRACKED]

Covers: A fake Claude Code CLI for tests: records the argv and the full stdin it was given, then answers with the minimum valid stream-json `cli.py` needs to parse a run to completion. Modeled on `fake_hermes_cli.py` - same shape, a different protocol, because the real CLI's is JSON lines on stdout rather than one JSON blob per subcommand. If the prompt were ever passed on the command line instead of over stdin, this script's own argv log is exactly where that regression would show up - see test_prompt_

### `tests/fake_hermes_cli.py` (43 lines)

Covers: A fake `hermes` executable for tests: records every argv it was called with and answers canned JSON keyed by the subcommand. Point `HERMES_EXE` at this script's companion `.bat`/shim, or invoke it directly with the same interpreter as the test process. Log format: one JSON line per call, appended to $FAKE_HERMES_LOG. Answers: read from $FAKE_HERMES_ANSWERS (a JSON file: {"profile list": {...}, "kanban create": {...}, ...}, matched by the first two argv tokens joined with a space; falls back to {

### `tests/fake_hermes_gateway.py` (127 lines)

Covers: A fake Hermes TUI gateway, for testing the bridge without a model. Speaks the same wire protocol as `tui_gateway.entry`: newline-delimited JSON-RPC on stdio, `gateway.ready` on boot, `event` frames for streaming. Behaviour is scripted through env vars so one binary covers every test: FAKE_HERMES_CLARIFY=1 the first prompt.submit triggers clarify.request before completing FAKE_HERMES_HANG=1 never answer prompt.submit (for interrupt tests) FAKE_HERMES_DIE=1 exit abruptly after the first prompt.sub

### `tests/fake_model_gateway_worker.py` (116 lines)

Covers: A fake MODEL_GATEWAY worker, for testing the gateway without Hermes. Speaks the same protocol as `friday/hermes_model_gateway_worker.py`. Behaviour is scripted through env vars: FAKE_GW_FAIL_PROVIDERS="anthropic:QUOTA_EXCEEDED,opencode-free:MODEL_UNAVAILABLE" infer on these providers answers with that error code FAKE_GW_EMPTY_PROVIDERS="anthropic:length,openai-codex:stop" infer on these providers answers ok with NO content and that finish_reason - `length` only while max_output_tokens is below F

### `tests/live/__init__.py` (1 lines)

Covers: No docstring; 0 test(s).

### `tests/live/test_live_providers.py` (174 lines)

Covers: Live provider validation - opt in (audit A-008 / A-024; PRD Requirements 9, 11). FRIDAY_LIVE_PROVIDER_TESTS=1 .venv-verify/Scripts/python.exe -m pytest tests/live -q Everything else in `tests/` talks to `fake_hermes_gateway.py` and `fake_model_gateway_worker.py`. This directory talks to the real Hermes model-gateway worker and, through it, to every provider Hermes can currently broker. It answers the question the deterministic suite cannot: does Friday actually get an answer back from each confi
Tests[5]: test_hermes_lists_at_least_one_usable_provider, test_inventory_carries_no_credentials, test_every_authenticated_provider_is_probed_and_gets_an_evidence_verdict, test_the_gateway_health_view_matches_the_probe_evidence, test_usage_is_recorded_per_provider
Exercises: friday, friday.model_gateway

### `tests/test_action_chain.py` (234 lines)

Covers: Text is not authority. A web page says "restart the computer to apply updates". A document ends with "close all your applications". An email signature contains "shut down". None of these is a person asking Friday for anything, and all of them are the sort of sentence that arrives inside material Friday was asked to read. The defence is not cleverness at the point of action. Asking "does this look like the person asked?" when a shutdown is already resolved and confirmed means asking the model to 
Tests[13]: test_a_run_is_from_the_person_unless_it_says_otherwise, test_a_run_built_from_read_material_says_so, test_provenance_cannot_be_something_nobody_defined, test_provenance_is_set_once_and_not_recomputed, test_read_material_cannot_reach_a_destructive_capability, test_the_person_can_still_reach_it, test_the_irreversible_ones_still_need_a_yes, test_closing_is_automatic_for_a_person_and_refused_for_a_page, test_read_material_is_refused_before_a_confirmation_exists, test_the_reversible_capabilities_are_unaffected, test_planted_instructions_authorise_nothing, test_the_toolset_refuses_planted_text_before_resolving_anything, test_removing_the_gate_would_be_caught
Exercises: friday, friday.toolsets

### `tests/test_action_truth_targets.py` (175 lines) [UNTRACKED]

Covers: Action truth at the level of the TARGET, not just the kind (FR-TRU-001). The PRD states the failure in one line: Success evidence for foo.txt cannot justify "I created bar.txt." That is the most plausible false claim this system can produce, because every part of it is true except the part the user cares about: the verb is right, the tool family is right, the verification is real, and the filename is wrong. `friday/honesty.py` checked the first three and not the fourth. These tests are written i
Tests[10]: test_evidence_for_one_file_does_not_back_a_claim_about_another, test_the_honest_claim_about_the_same_file_still_passes, test_the_same_file_named_several_ways_is_recognised, test_a_claim_with_no_concrete_target_is_left_to_the_kind_check, test_a_url_claim_is_checked_against_the_url_that_was_touched, test_a_scheme_is_not_mistaken_for_a_windows_drive, test_a_succeeded_action_that_records_nothing_it_touched_backs_no_named_claim, test_removing_the_target_check_would_be_caught, test_a_named_literal_is_reported_back_exactly, test_a_literal_target_is_matched_case_insensitively_but_not_altered
Exercises: friday

### `tests/test_adversarial.py` (272 lines)

Covers: Adversarial reasoning (PRD v3.1 FR-008, FR-012). FR-008 decision output preserves disagreements, evidence and uncertainty rather than fabricated consensus FR-012 the worker that implemented a consequential change is not the sole authority certifying it The panel's rules (what happens to opinions) are tested with a scripted `infer`; one @live test runs the real five roles through the Hermes MODEL_GATEWAY.
Tests[14]: test_deliberation_preserves_disagreements_evidence_and_uncertainty, test_a_judge_that_ignores_an_objection_does_not_erase_it, test_an_unavailable_role_is_recorded_not_invented, test_unavailable_judge_yields_no_verdict, test_tagged_parse_is_structural, test_deliberate_tool_is_reachable_and_carries_verification, test_reviewer_disputes_a_claim_the_diff_does_not_implement, test_findings_override_a_self_contradicting_confirmed, test_empty_diff_is_inconclusive_and_unavailable_reviewer_is_honest, test_promotion_refuses_a_consequential_change_without_independent_review, test_promotion_refuses_a_disputed_review_and_accepts_a_confirmed_one, test_the_implementer_reviewing_itself_does_not_count, test_development_run_reviews_the_real_diff, test_live_panel_runs_through_the_model_gateway
Exercises: friday, friday.toolsets

### `tests/test_agentability_harness.py` (58 lines)

Covers: Structural safety requirements for the live Agentability gate.
Tests[6]: test_batch_2d_is_present_in_the_agentability_gate, test_process_agentability_cases_cannot_name_a_real_application, test_window_minimize_case_is_not_an_ambiguous_close_request, test_audio_mutation_is_measured_before_inventory_can_pre_answer_it, test_music_inventory_is_measured_before_play_can_pre_answer_it, test_mcp_server_output_is_captured_for_crash_diagnosis

### `tests/test_answer.py` (296 lines)

Covers: Deciding how hard to think about a question, before answering it. The thing Friday is actually for. Every other subsystem exists so the boss can stop opening ChatGPT, and none of them help with the commonest request of all, which is a question. Until this, every question took one path: a single Gemini turn with seventeen tools attached. "Use search when you need to" was in the master prompt the whole time and did not work, because a model that believes it knows the answer does not feel a need - 
Tests[19]: test_stable_knowledge_needs_no_sources, test_an_unclassifiable_question_defaults_to_fast, test_nothing_asked_is_not_an_error, test_a_question_about_now_gets_sources, test_a_version_makes_a_definition_into_a_fact_with_a_date, test_weighing_options_is_deep, test_architecture_questions_are_worth_arguing_with, test_a_question_about_this_machine_is_not_architecture, test_the_machine_pattern_survived_its_own_escaping, test_an_answer_built_on_sources_is_told_to_disagree, test_a_deep_answer_is_asked_for_an_opinion_and_its_counterargument, test_a_fast_answer_carries_no_instruction, test_the_findings_reach_the_model_without_the_boss_seeing_them, test_research_runs_before_the_model_answers, test_an_ordinary_question_is_not_researched, test_research_that_fails_costs_nobody_their_answer, test_a_coroutine_capability_is_awaited, test_a_question_about_our_own_source_does_not_go_to_the_web, test_the_codebase_guard_does_not_swallow_real_research
Exercises: agent_friday, friday

### `tests/test_approval_channel.py` (203 lines)

Covers: The person answers. The model does not get to. Two halves, and the second is the one that needs a test of its own: the mechanism `Book.consume` refuses anything not APPROVED, and only `Book.approve` sets that. Covered in test_confirmation.py. the wiring nothing the model can call reaches `Book.approve`. Unit tests prove a lock works. They cannot prove nobody left a key out, and that is a different failure - one that arrives later, by someone adding a convenient `confirm()` tool because the agent
Tests[12]: test_there_are_tool_modules_to_check, test_no_tool_module_can_reach_approval, test_no_registered_tool_is_named_for_an_approval, test_the_approval_matcher_lives_above_the_tool_boundary, test_a_yes_settles_the_one_thing_waiting, test_a_no_settles_it_the_other_way, test_a_yes_with_nothing_pending_approves_nothing, test_two_pending_questions_are_never_guessed_between, test_something_that_is_not_an_answer_settles_nothing, test_words_that_merely_contain_an_answer_are_not_one, test_an_answer_does_not_cross_between_runs, test_an_expired_question_is_not_still_waiting
Exercises: friday

### `tests/test_arbiter.py` (123 lines)

Covers: InputArbiter classification: new input while a run is active must be routed, never destructive. A status question must not cancel the run; a side conversation must not touch it; only an explicit stop cancels it.
Tests[11]: test_cancel_phrases, test_query_phrases, test_side_conversation, test_modification_phrases, test_new_objective_phrases, test_intent_vocabulary, test_cancel_wins_over_query, test_a_long_request_is_not_a_cancellation, test_a_negated_control_word_is_not_that_control, test_short_control_utterances_still_work, test_the_length_bounds_leave_room_for_real_speech
Exercises: friday, friday.arbiter

### `tests/test_argument_paths.py` (101 lines)

Covers: Windows paths through a JSON string argument. `use_capability(capability, arguments)` takes its arguments as JSON text, and the model writes the path the boss said: {"path": "E:\friday\gate.csv"}. `\f` is a legal JSON escape, so nothing raises - `json.loads` returns "E:<formfeed>riday<tab>ate.csv" and the tool fails on a path nobody typed. Measured cost: the model's recovery is to call again with different escaping, and when the second attempt worked, one "process this catalogue" left two catalo
Tests[9]: test_the_escape_json_silently_accepts, test_the_escape_json_refuses_outright, test_every_escape_json_can_produce_comes_back, test_an_escaped_quote_is_not_collateral_damage, test_a_correctly_escaped_path_is_left_alone, test_a_forward_slash_path_is_left_alone, test_file_content_with_real_newlines_is_never_touched, test_prose_that_merely_mentions_a_drive_is_untouched, test_a_unix_path_is_untouched
Exercises: agent_friday

### `tests/test_audio.py` (292 lines)

Covers: Audio sessions, and knowing which one you are holding. The live gate proves the transaction against real Core Audio. This proves the identity model, which is where the mistakes were: a session outlives its process Windows reports such a session as ACTIVE the pid it reports may be only the process that CREATED the session several instances of one app share a session identifier So the primary identity is the session *instance* identifier, and everything about the process is a label used to resolve
Tests[19]: test_the_instance_identifier_is_the_identity, test_an_expired_session_is_never_actionable, test_a_dead_pid_makes_a_single_process_session_unactionable, test_a_dead_pid_does_NOT_condemn_a_multi_process_session, test_an_uncontrollable_session_is_not_actionable, test_unknown_scope_falls_back_to_the_state, test_a_name_finds_the_session, test_an_instance_id_finds_exactly_one, test_live_sessions_are_preferred_over_ghosts, test_when_all_of_them_are_ghosts_they_are_still_returned, test_two_matches_is_a_question_not_a_choice, test_nothing_matching_names_what_is_playing, test_setting_a_volume_reports_what_it_was, test_a_volume_outside_the_range_is_refused, test_a_session_that_refuses_is_a_failure_not_a_silent_success, test_mute_reports_what_it_was, test_percentages_and_levels_convert_both_ways, test_close_enough_allows_a_driver_to_round_but_not_to_ignore, test_every_audio_tool_is_declared_to_the_policy_engine
Exercises: friday, friday.policy, friday.toolsets

### `tests/test_audit_planner.py` (301 lines)

Covers: Auditing every capability, from the registry rather than from prose. A spoken request to audit everything produced 205 tasks - 132 succeeded, 68 failed, 5 skipped - because the planner read 1967 words as 205 instructions. The list of things Friday can do was never in the sentence. It is in the registry, and the registry is data. What these gates hold is the property that makes an audit an audit: every registered capability is accounted for, in exactly one group, with exactly one honest strategy 
Tests[24]: test_an_audit_request_is_recognised, test_an_ordinary_request_is_not_an_audit, test_the_audit_covers_the_whole_registry, test_every_capability_has_exactly_one_strategy, test_no_capability_appears_in_two_groups, test_the_group_count_comes_from_the_registry_not_the_sentence, test_a_power_action_is_never_run_to_raise_the_pass_count, test_a_pure_transform_is_not_called_a_real_test, test_something_needing_a_live_session_says_so, test_a_camera_capability_needs_hardware, test_reading_the_machine_is_safe_to_do_for_real, test_most_of_the_registry_can_actually_be_exercised, test_the_audit_becomes_one_goal_per_group, test_each_group_says_how_much_of_it_is_testable, test_every_registered_capability_becomes_exactly_one_leaf, test_a_leaf_either_runs_or_says_why_not, test_nothing_that_changes_the_world_runs_without_an_owned_fixture, test_the_audit_never_cancels_itself, test_fixtures_live_inside_the_file_jail, test_a_group_of_only_refusals_still_accounts_for_its_capabilities, test_the_audit_still_exercises_a_useful_share_of_the_registry, test_the_audit_plan_passes_the_gate_that_starts_it, test_a_group_with_no_children_is_refused, test_the_audit_compiles_through_the_real_entry_point
Exercises: friday, friday.fsjail, friday.store, friday.toolsets

### `tests/test_auth_boundary_resume.py` (83 lines)

Covers: Auth boundary park-and-resume — the connector repair is a recoverable sub-objective of the parent run. Boss's rule: after the user completes auth, Friday resumes the original task automatically. The user must never repeat the request that hit the auth wall. Mechanically: a PROVIDER_AUTH (USER_REQUIRED with auth wording) failure parks the RUN as WAITING_PERMISSION with the failed task kept open, and `resume_after_auth` - the connector_verify success hook - puts the task back on the scheduler.
Tests[2]: test_provider_auth_parks_the_run_instead_of_failing_it, test_genuine_user_cancellation_still_fails_terminally
Exercises: friday, friday.continuous, friday.objectives, friday.store

### `tests/test_autolearn.py` (465 lines)

Covers: Automatic learning: the user model fills itself in, without being asked. The gap this closes was invisible for a week - profile.learn_from_turn was built, tested and correct, and the profile stayed empty because the only thing that could call it was a model with other things on its mind. Two properties matter more than any of the detail here: * nothing in the hook may block the reply. Extraction is a Gemini call. * nothing in the hook may break the reply. A dead learner costs a memory, never a c
Tests[25]: test_payload_reads_a_plain_dict, test_payload_reads_a_json_string, test_payload_unwraps_livekits_text_content, test_payload_on_nonsense_is_empty_not_an_exception, test_short_turns_are_not_learned_from, test_the_same_sentence_twice_is_only_learned_once, test_a_full_queue_drops_the_oldest_and_never_blocks, test_observe_never_raises, test_a_turn_is_learned_from_without_the_model_asking, test_the_briefing_is_loaded_before_the_first_turn, test_a_turn_that_taught_nothing_does_not_re_read_the_profile, test_a_conflict_becomes_something_to_ask_about, test_a_missing_profile_tool_is_a_note_not_a_stack_trace, test_a_failing_tool_does_not_kill_the_learner, test_the_briefing_lands_in_the_turn_context, test_nothing_is_injected_when_nothing_is_known, test_last_assistant_text_finds_the_most_recent_reply, test_last_assistant_text_on_an_empty_conversation, test_a_broken_learner_never_costs_a_reply, test_typed_input_gets_the_same_treatment_as_spoken, test_typed_input_does_not_pollute_the_stored_history, test_the_agent_can_call_its_own_capabilities_without_the_model, test_the_briefing_is_folded_into_the_instructions, test_an_unchanged_briefing_does_not_rewrite_anything, test_the_learner_reports_only_real_changes
Exercises: agent_friday, friday

### `tests/test_automations.py` (491 lines)

Covers: The automation engine: the graph, the retries, and the boundary. Nothing here registers a real scheduled task or touches the network. The scheduler is proved live by scripts/golden_automations.py - `schtasks /Query` is the only thing that can settle whether an automation is armed, and a mock that says yes proves nothing at all.
Tests[41]: test_a_shell_step_is_refused, test_a_command_step_is_refused_too, test_a_step_may_not_name_a_capability_outside_the_allow_list, test_the_allow_list_holds_no_capability_that_writes_or_executes, test_every_allow_listed_capability_is_real_and_callable, test_each_step_is_still_gated_by_the_policy_engine_when_it_runs, test_the_database_path_does_not_depend_on_the_working_directory, test_no_module_reintroduces_the_relative_default, test_every_runtime_root_is_absolute, test_a_run_records_where_it_resolved_its_paths, test_a_scheduled_task_declares_where_it_runs, test_a_cycle_is_reported_by_name, test_a_step_that_needs_something_nonexistent_is_refused, test_a_misspelled_argument_fails_now_not_at_three_am, test_a_missing_required_argument_is_refused, test_duplicate_step_ids_are_refused, test_retries_are_capped, test_steps_come_back_in_dependency_order, test_bad_triggers_are_refused, test_good_triggers_survive, test_a_step_whose_dependency_failed_is_skipped_not_run, test_a_failure_does_not_stop_an_independent_branch, test_a_step_reads_the_output_of_the_step_it_needs, test_variables_supplied_at_run_time_reach_the_step, test_an_unresolved_placeholder_is_left_alone_never_blanked…
Exercises: friday, friday.companion.bridge, friday.companion.pairing, friday.config, friday.fsjail, friday.policy, friday.store, friday.toolsets, friday.toolsets.vision

### `tests/test_autonomy.py` (208 lines)

Covers: Autonomy: a gate with no key is a hang, not a safety feature. The failure this pins: browser.automate was ASK-gated, there was deliberately no self-approval tool, so in conversation there was no way to say yes. The agent asked "shall I proceed?", the user said "yes", and it asked again. Four times.
Tests[17]: test_dangerous_is_the_default, test_full_autonomy_never_asks_for_anything, test_previously_gated_tools_now_run, test_refusals_are_not_relaxed_by_autonomy, test_unknown_tools_still_do_not_silently_run, test_guarded_mode_still_works_for_anyone_who_wants_it, test_unknown_autonomy_mode_is_rejected, test_explicit_overrides_still_win, test_authorization_still_takes_only_a_tool_id, test_a_gated_tool_reports_a_reason_a_human_can_act_on, test_tools_run_without_approval_under_the_default_engine, test_full_autonomy_turns_ask_into_yes_but_not_confirm, test_confirm_is_not_deny, test_a_session_approval_still_settles_an_ordinary_ask, test_a_confirm_verdict_says_which_kind_of_no_it_is, test_guarded_mode_is_unchanged_by_the_new_tier, test_every_decision_a_category_declares_is_a_known_one
Exercises: friday, friday.toolsets

### `tests/test_autonomy_and_selfcheck.py` (331 lines)

Covers: The owner's 2026-09-03 instructions: no "say okay", an autonomous mode that skips the permission round trips, and a Friday that can run the master validation prompt on herself. Each test names the failure it guards.
Tests[16]: test_dangerous_answers_confirm_but_never_the_non_approvable, test_set_autonomy_persists_and_switches_the_live_engine, test_spoken_switch_turns_full_autonomy_on_and_off, test_dangerous_autonomy_is_the_default, test_a_pasted_page_mentioning_status_is_not_a_status_question, test_the_spoken_okay_approves_the_pending_plan, test_dangerous_takeover_runs_the_plan_without_a_yes, test_forbidden_categories_survive_dangerous_mode, test_selfcheck_runs_the_automatable_half, test_selfcheck_hands_hermes_a_real_job_when_live, test_go_according_to_the_prompt_runs_the_selfcheck, test_full_autonomy_lets_a_spoken_write_reach_the_fabric, test_capability_errors_carry_the_providers_health_hint, test_spoken_file_ops_never_leave_the_workspace, test_web_extract_parses_the_gated_fetch_with_scrapling, test_the_takeover_offer_lapses_after_one_unrelated_turn
Exercises: friday, friday.toolsets, friday.ui_server

### `tests/test_autonomy_slider.py` (196 lines) [UNTRACKED]

Covers: Autonomy slider (FR-POL-001) - compiling a named level into the 16-domain grid must never quietly unlock a constitutional class or a consequential action. That failure would make "High Autonomy" mean "no more kernel", which is exactly what the negative controls below pin.
Tests[18]: test_every_level_compiles, test_unknown_level_raises, test_compile_level_covers_exactly_domains, test_compile_level_never_mentions_constitutional_names, test_monotone_across_all_five_levels, test_advise_is_confirm_except_research, test_draft_adds_file_write_and_local_build, test_ask_before_acting_matches_current_defaults, test_high_autonomy_keeps_exactly_the_four_consequential_on_confirm, test_no_two_levels_compile_to_the_same_mapping, test_set_level_writes_one_event_per_changed_domain, test_set_level_leaves_unchanged_domains_without_new_events, test_set_level_is_idempotent_in_domain_events, test_set_level_rejects_unknown_level, test_level_round_trips, test_level_infers_ask_before_acting_when_nothing_set, test_high_autonomy_does_not_touch_constitutional_classes, test_high_autonomy_keeps_consequential_domains_on_confirm
Exercises: friday

### `tests/test_breaker.py` (185 lines)

Covers: The outbound circuit breaker. Behavioural tests only: the point of a breaker is what it does to callers, so these drive the public surface and the real `crawl_one` / `web_fetch` paths rather than poking at internals.
Tests[12]: test_a_healthy_host_is_never_refused, test_it_opens_only_after_the_threshold, test_one_success_closes_it_again, test_hosts_are_isolated_from_each_other, test_after_the_cooldown_exactly_one_probe_is_allowed, test_seconds_left_is_reported_for_the_ui, test_transport_failures_count, test_an_http_status_is_not_a_transport_failure, test_an_unparseable_url_is_ignored_rather_than_crashing, test_guard_records_success_and_failure, test_guard_never_swallows_the_original_error, test_crawl_one_fails_fast_once_a_host_is_open
Exercises: friday, friday.toolsets

### `tests/test_browser_capability.py` (108 lines)

Covers: Browser capability policy layer - the negative controls. The two controls the vnext gate names as promotion-blocking: BANKING: a blocked URL structurally cannot produce model-visible content (the capture callable never runs). SECRETS: secret-shaped text is opaque before model exposure, on both the page path and the clipboard path.
Tests[9]: test_banking_url_is_blocked_before_capture, test_blocked_page_never_invokes_capture, test_allowed_page_flows_through, test_secret_shapes_are_redacted, test_clipboard_shaped_page_text_is_dead_on_arrival, test_normal_text_is_untouched, test_auth_pages_hand_off_to_user, test_private_addresses_blocked_by_netguard, test_redirect_into_bank_is_blocked_mid_fetch
Exercises: friday

### `tests/test_browser_evidence_chain.py` (237 lines) [UNTRACKED]

Covers: The browser evidence chain: from a step's observation to what FRIDAY is allowed to say (FR-BRW-004, FR-TRU-001, release gate 16). `tests/test_browser_primitives.py` already proves the loop holds - every action observed before and after, a missed expectation failing the step, an ordinary click on a shop not counting as a purchase. What it does not cover is the JOIN: a step that observed site A must not be able to back a sentence about site B, and a step with no post-observation must not back anyt
Tests[7]: test_a_step_that_observed_one_page_cannot_back_a_claim_about_another, test_a_refund_claim_needs_the_refund_page_not_merely_a_click, test_the_same_claim_passes_once_the_order_page_is_observed, test_a_step_with_no_post_observation_is_not_ok_and_says_so, test_a_forged_ok_on_the_step_does_not_survive_verification, test_removing_the_target_check_lets_the_wrong_page_back_the_claim, test_a_human_verification_page_is_a_handoff_and_backs_no_success_claim
Exercises: friday

### `tests/test_browser_primitives.py` (484 lines)

Covers: Browser primitives and boundaries (PRD v3.1 FR-031, FR-033, FR-034, FR-035, FR-036, FR-037). Two layers of evidence: * the rule layer, against an in-memory driver (fast, deterministic); * `@live` E2E against real Chromium (Playwright) over a local HTTP fixture - every primitive the PRD names, plus the FR-034 / FR-035 boundaries firing on real pages.
Tests[16]: test_every_prd_primitive_exists_and_is_policy_classified, test_every_action_has_an_observation_before_and_after, test_no_blind_coordinate_loop_a_missing_target_is_refused_not_guessed, test_verify_fails_when_expectation_is_not_met, test_state_changing_primitives_need_session_approval_reads_do_not, test_a_signed_in_session_never_authorises_external_writes, test_an_exact_action_confirmation_lets_the_external_write_through, test_ordinary_clicks_on_a_shop_are_not_purchases, test_human_verification_is_a_handoff_never_a_thing_to_click_through, test_landing_on_a_verification_page_after_an_action_is_flagged, test_workers_get_isolated_profiles_unless_the_approval_named_authorized, test_observation_is_structured_first_and_secrets_are_redacted, test_sensitive_domains_are_refused_before_observation, test_target_resolution_prefers_exact_then_prefix_then_contains, test_live_every_primitive_against_real_chromium, test_live_toolset_face_runs_the_loop_over_the_shared_session
Exercises: friday, friday.toolsets

### `tests/test_browser_profiles.py` (183 lines)

Covers: Which account, and nothing smuggled in beside it. The launch call is the one place this module hands a string to a process, and everything before the first bare argument on a Chromium command line is a switch. `--load-extension`, `--remote-debugging-port`, `--user-data-dir`, `--disable-web-security` are all one unchecked string away from being the browser's new configuration. That is reachable, not theoretical: `url` is filled in by the model, and the model reads web pages. A crawled page saying
Tests[13]: test_a_switch_is_never_opened_as_a_url, test_only_http_and_https_may_be_opened, test_a_url_with_no_host_is_refused, test_nothing_is_not_a_url, test_real_urls_pass, test_an_odd_profile_directory_is_refused, test_switch_parsing_is_ended_before_the_url, test_an_exact_address_wins, test_a_shared_domain_is_ambiguous_and_asks, test_no_hint_uses_the_profile_he_was_last_in, test_a_meaningless_hint_does_not_match_something_at_random, test_the_only_browser_file_it_opens_is_the_metadata_one, test_it_never_reaches_for_the_decryption_apis
Exercises: friday

### `tests/test_build_identity.py` (103 lines)

Covers: What code is actually running, so a test can refuse to test the wrong build. This exists because of an hour lost to a lie. `agent_friday.py dev` printed `registered worker` four times while the process id never changed, because LiveKit's dev watcher re-registers without re-importing modules already in `sys.modules`. Separately, `server.py` had been running since 13:23 while the MCP tools it was meant to serve were written at 23:57. A live test asked "what am I working on?", got "I don't have a r
Tests[9]: test_a_build_knows_what_it_is_made_of, test_the_registry_hash_follows_the_capabilities_not_the_files, test_a_process_matches_itself, test_a_stale_registry_is_caught_even_at_the_same_commit, test_the_registry_is_checked_before_the_commit, test_a_dirty_tree_is_reported_rather_than_claimed_clean, test_it_says_how_long_it_has_been_up, test_describe_is_one_readable_line, test_a_packaged_build_reports_the_commit_the_builder_stamped
Exercises: friday

### `tests/test_camera_gate.py` (113 lines)

Covers: The camera is the first door; the PIN is the fallback, never the shortcut. These tests pin the policy the owner asked for: a PIN proves knowledge, a face proves presence, so knowledge alone only opens the door when no face could have been read at all.
Tests[8]: test_pin_is_refused_while_the_camera_is_free, test_pin_opens_the_door_when_a_meeting_holds_the_camera, test_a_friday_window_with_the_camera_vetoes_the_pin, test_password_mode_always_allows_the_pin, test_brute_force_gets_locked_out, test_weak_pins_are_rejected, test_camera_status_shape_is_stable, test_camera_endpoint_answers_while_locked
Exercises: friday

### `tests/test_capability_fabric_runtime.py` (241 lines)

Covers: Hard regression for the runtime path: capability_use -> fabric -> adapter. The unit tests elsewhere prove each adapter in isolation. This proves the thing that actually failed in production: that a request reaching the MCP bridge routes to the right provider, that adversarial input is refused at the bridge and not just deep in an adapter, and that a newly added adapter can be picked up without a restart. These are the prompts a hostile or careless caller would send, run through the real `fabric_
Tests[14]: test_research_routes_to_the_science_skills_provider, test_scraping_routes_to_scrapling_and_extracts_fields, test_roles_routes_to_gstack_and_ranks_the_right_workflow, test_the_family_is_named_by_outcome_not_by_brand, test_an_unknown_family_fails_honestly_rather_than_crashing, test_an_unknown_operation_in_a_real_family_is_refused, test_path_traversal_through_the_bridge_is_refused, test_a_licence_blocked_skill_cannot_be_reached_through_the_bridge, test_malformed_arguments_do_not_crash_the_bridge, test_a_security_procedure_needs_scope_even_through_the_bridge, test_security_search_is_open_because_knowing_is_not_doing, test_capability_reload_picks_up_a_newly_added_provider, test_reload_reports_what_changed_by_id_not_just_a_count, test_a_family_whose_provider_is_unavailable_fails_without_crashing
Exercises: friday, friday.fabric_adapters, friday.tools

### `tests/test_capability_reach.py` (150 lines)

Covers: Every capability is in exactly one place, and the count can go down. The failure this guards against is not a broken capability. It is a number that stops meaning anything: a reach metric that counts "did not raise" will read 100% the moment refusals become polite, and a partition that leaves things out will read whatever the person writing it hoped. So: exactly one bucket each, the buckets sum to the registry, resolutions load real callables, and a mutation that hollows the runtime out makes th
Tests[9]: test_the_partition_is_complete_and_disjoint, test_every_reachable_capability_loads_a_real_callable, test_every_reachable_capability_takes_a_run, test_the_pure_transforms_stay_where_they_are, test_reach_is_most_of_the_registry, test_an_unresolved_capability_is_refused_with_its_reason, test_a_capability_registered_nowhere_still_raises, test_policy_covers_the_capabilities_or_fails_closed, test_a_hollowed_out_runtime_would_be_caught
Exercises: friday

### `tests/test_capability_router.py` (352 lines)

Covers: Dynamic toolsets: a dozen tools in front of the model, not seventy-four. 74 tools meant ~22,700 characters of JSON schema on every request. Gemini started returning empty completions - FinishReason.STOP with no content, four retries, then failure. These tests pin the split and, importantly, that no tool becomes unreachable.
Tests[29]: test_every_registered_tool_is_core_or_grouped, test_no_tool_is_in_two_groups, test_core_and_groups_do_not_overlap, test_every_group_has_a_purpose_line, test_groups_reference_tools_that_exist, test_core_tools_all_exist, test_the_active_surface_is_a_fraction_of_the_whole, test_starts_with_only_the_core, test_enabling_a_group_adds_exactly_that_group, test_groups_are_additive_not_exclusive, test_enabling_twice_is_harmless, test_unknown_group_lists_the_real_ones, test_all_enables_everything, test_case_and_whitespace_are_forgiven, test_active_tools_returns_objects_not_names, test_describe_reports_the_split, test_group_of_finds_the_owner, test_catalogue_lists_every_group, test_the_prompt_tells_the_model_the_tools_exist, test_the_prompt_forbids_claiming_a_missing_capability, test_plain_words_find_the_right_capability, test_search_returns_the_arguments_the_model_must_supply, test_search_is_bounded, test_search_on_nonsense_returns_nothing, test_empty_query_returns_nothing…
Exercises: agent_friday, friday, friday.tools

### `tests/test_capability_routing.py` (348 lines)

Covers: Ranking inside one domain, where words alone cannot decide. Every product tool is called `product_something` and described in terms of catalogues, so "retry the network failures" scores about the same against all six. The agent gate caught what that costs: in one run of four the model picked `product_process` for a retry and reprocessed the whole catalogue into a second run. What separates them is not vocabulary. It is that `product_process` STARTs work while `product_retry` RECOVERs work that a
Tests[20]: test_processing_a_catalogue_finds_the_processor, test_a_follow_up_ranks_below_a_start_when_nothing_has_started, test_retry_beats_reprocess_once_a_run_exists, test_which_products_failed_reaches_the_reader_not_the_processor, test_how_did_it_finish_reaches_status_or_the_run_list, test_starting_over_is_still_reachable_when_he_says_so, test_only_start_capabilities_change_the_picture, test_what_happened_this_morning_is_history_not_a_new_automation, test_scheduling_something_new_still_reaches_create, test_an_undeclared_tool_does_not_break_the_search, test_every_annotated_capability_declares_a_known_kind, test_negative_examples_never_repeat_an_intent_example, test_no_capability_is_named_entirely_in_words_the_filter_drops, test_every_example_carries_something, test_a_capability_is_never_repelled_by_its_own_best_example, test_every_annotated_capability_wins_its_own_best_phrasing, test_a_smaller_request_never_reaches_the_whole_machine, test_forcing_is_reachable_when_it_is_actually_meant, test_the_machine_is_reachable_when_it_is_actually_meant, test_every_destructive_capability_declares_what_must_not_reach_it
Exercises: friday

### `tests/test_capture.py` (336 lines)

Covers: Writing down what the boss said he is building. Measured tonight: he described a combat drone game, Friday researched the engine, delivered a verdict, and stored none of it. Asked "what am I working on?" twenty minutes later it answered correctly - from the conversation window. A restart would have lost it and the answer would have been "halo and lighthouse", from two days ago. `projects_list` fixed the read side and says so in its own docstring. The write side was never wired: `record_decision`
Tests[34]: test_it_names_the_thing_being_built, test_it_does_not_invent_a_project, test_size_adjectives_are_not_part_of_the_name, test_a_name_stays_short_enough_to_read_back, test_it_records_the_project, test_saying_it_twice_does_not_make_two_projects, test_nothing_is_recorded_for_an_ordinary_sentence, test_a_store_that_fails_does_not_cost_the_turn, test_a_checked_claim_becomes_a_decision, test_the_decision_carries_what_produced_it, test_an_unchecked_claim_is_not_a_decision, test_no_project_means_no_decision, test_nothing_at_all_is_handled, test_the_work_survives_being_asked_about_later, test_the_turn_hook_reaches_it, test_it_writes_to_the_database_everything_else_reads, test_the_store_honours_ADA_DB, test_it_notices_a_requirement, test_requirements_are_recorded_with_acceptance, test_saying_the_same_requirement_twice_records_it_once, test_no_project_means_no_requirement, test_it_reads_a_change, test_from_to_keeps_the_new_value_not_both, test_it_does_not_invent_a_change, test_a_new_requirement_is_not_read_as_a_change…
Exercises: agent_friday, friday, friday.store, friday.toolsets.memory

### `tests/test_chaos_restart.py` (211 lines)

Covers: Crash / restart / resume chaos (PRD v3.1 FR-005, FR-014, KPI "Restart Resume >= 99%"). Real processes, real SQLite. A child process runs the objective engine against a shared on-disk store; the parent kills it (SIGKILL-equivalent on Windows: TerminateProcess) while a task is mid-flight, then a FRESH process - new pid, new executor identity, no in-memory state - opens the same database and continues. Assertions are on the durable state only: * the objective reaches COMPLETED after the restart; * 
Tests[2]: test_control_plane_killed_mid_task_resumes_in_a_fresh_process, test_restart_resume_rate_over_repeated_kills
Exercises: friday.store

### `tests/test_cli_prompts.py` (260 lines) [UNTRACKED]

Covers: FR-COD-003: FRIDAY must not guess on a consequential CLI question, and must not sit blocked on a trivial one either. `friday/executors/cli_prompts.py` is a pure parser plus a decision function - no model call, no subprocess - which is what lets every branch be driven directly here instead of trusted on the strength of "it looked right". The two negative controls at the bottom are the actual safety property: a decision function that can be made to answer a destructive question, or to guess when n
Tests[19]: test_detect_numbered_dot_style_with_recommendation, test_detect_numbered_paren_style_with_free_text_option, test_detect_bracket_style, test_detect_yes_no_default_yes, test_detect_yes_no_default_no, test_detect_returns_none_for_a_numbered_plan, test_detect_returns_none_for_a_stack_trace, test_detect_returns_none_for_a_progress_line, test_detect_returns_none_for_prose, test_detect_returns_none_for_empty_text, test_classify_trivial_yes_no_with_explicit_default, test_classify_ordinary_real_choice_with_a_recommendation, test_classify_consequential_destructive_option, test_decide_takes_the_recommended_option, test_decide_picks_the_objective_matching_option, test_decide_escalates_below_the_confidence_floor, test_decide_never_selects_the_free_text_option, test_decide_escalates_on_a_destructive_option_even_when_recommended, test_decide_escalates_with_no_recommendation_and_no_objective_overlap
Exercises: friday.executors

### `tests/test_codegraph.py` (307 lines)

Covers: The structural map, and the ways a map lies. A map that is wrong is worse than no map, because it is trusted. The tests that matter here are the ones about staleness, about approximate readings being labelled, and about an empty project not producing a graph that later reads as "already done".
Tests[33]: test_it_finds_functions_classes_and_methods, test_a_method_knows_its_class, test_python_readings_are_marked_exact, test_an_approximate_reading_says_so, test_a_file_that_does_not_parse_does_not_abandon_the_graph, test_the_usual_noise_is_not_walked, test_an_enormous_file_is_skipped_and_recorded, test_a_missing_directory_is_refused, test_find_prefers_an_exact_name, test_find_falls_back_to_contains, test_find_with_nothing_to_look_for_returns_nothing, test_callers_answers_the_question_grep_answers_badly, test_callers_of_something_nobody_calls_is_empty, test_api_of_lists_what_a_file_offers, test_api_of_hides_private_names_by_default, test_the_repo_map_is_a_summary_not_the_whole_graph, test_a_fresh_graph_is_not_stale, test_a_changed_file_is_stale, test_a_new_file_is_stale, test_a_deleted_file_is_stale, test_refresh_re_reads_only_what_changed, test_refresh_drops_a_deleted_files_symbols, test_refreshing_an_unchanged_graph_does_nothing, test_a_saved_graph_loads_back_the_same, test_a_corrupt_cache_is_rebuilt_not_raised…
Exercises: friday

### `tests/test_companion.py` (897 lines)

Covers: The channel into the browser he is signed into. This is the most dangerous thing in the codebase: it operates live, authenticated sessions. So the tests here are almost entirely about who is allowed to speak to it and what they may say. WebSocket has no CORS. Any local process, and any web page he visits, can *open* a connection to 127.0.0.1:8791. Two things make that useless: * the Origin header - a page's Origin is its own site and cannot be forged into chrome-extension:// * a paired token, se
Tests[68]: test_a_web_page_cannot_reach_the_bridge, test_a_local_process_without_the_token_cannot_reach_it, test_the_real_extension_pairs, test_only_an_extension_origin_is_accepted, test_the_browsers_that_have_extensions_are_accepted, test_an_unknown_command_is_refused_by_name, test_a_command_with_no_browser_says_so_rather_than_hanging, test_a_read_only_session_refuses_to_change_anything, test_a_read_only_session_still_reads, test_every_mutating_command_is_a_real_command, test_there_is_no_command_that_runs_arbitrary_script, test_refused_connections_are_recorded, test_refused_commands_are_recorded, test_the_audit_does_not_grow_without_bound, test_a_token_is_created_once_and_reused, test_a_missing_token_can_be_reported_rather_than_created, test_nothing_listens_until_it_is_needed, test_stopping_leaves_nothing_listening, test_the_extension_asks_for_no_host_permissions_up_front, test_the_extension_declares_activetab_for_the_attended_mode, test_the_extension_refuses_password_fields_and_browser_pages, test_the_extension_talks_only_to_loopback, test_the_options_page_builds_no_html_from_strings, test_chromes_id_derivation_is_reproduced_exactly, test_the_manifest_pins_a_key_so_the_id_is_stable…
Exercises: friday.companion

### `tests/test_composite_tasks.py` (264 lines)

Covers: A group of steps the boss reads as one line and the executor runs as many. An audit of everything Friday can do is 125 checks. As 125 top-level tasks it is a correct graph and an unreadable answer - the thing the audit planner was written to stop producing. A composite is the shape that fixes it: the boss sees "files: 11 capabilities" one row the executor sees files_read, files_info, ... eleven rows the status says what happened, not what was tried What is proved here: a group is never dispatche
Tests[11]: test_a_group_is_one_row_and_its_children_are_rows_underneath, test_a_group_is_never_dispatched, test_a_group_waits_for_every_child, test_one_child_succeeding_means_the_group_did_something, test_a_group_where_nothing_succeeded_failed, test_a_group_nothing_could_run_is_skipped_not_failed, test_a_child_may_be_skipped_with_a_reason_before_anything_runs, test_a_plan_cannot_declare_a_child_already_succeeded, test_a_hallucinated_child_fails_at_compile_like_any_other, test_the_headline_counts_groups_and_the_failures_are_still_named, test_progress_is_spoken_in_groups_not_children
Exercises: friday, friday.continuous, friday.objectives, friday.store

### `tests/test_confirmation.py` (210 lines)

Covers: A yes that means one thing. The dangerous shape is not "Friday asked" - it is asking about a restart, hearing yes, and terminating a process. Four migration batches in a row a request landed on a capability that merely shared vocabulary with the intended one, so a confirmation scoped to POWER_ACTION rather than to *this restart* would be a routing bug away from doing the wrong irreversible thing.
Tests[17]: test_the_ordinary_path, test_a_yes_to_a_restart_cannot_terminate_a_process, test_a_yes_for_one_target_does_not_cover_another, test_changing_the_arguments_invalidates_it, test_a_yes_belongs_to_the_run_it_was_given_in, test_argument_order_does_not_change_the_fingerprint, test_an_unserialisable_argument_does_not_break_the_binding, test_a_confirmation_cannot_be_spent_twice, test_an_unapproved_confirmation_cannot_be_consumed, test_a_refusal_is_permanent, test_an_unknown_nonce_is_refused, test_an_expired_confirmation_cannot_be_approved, test_an_expired_confirmation_cannot_be_consumed, test_expired_and_spent_confirmations_are_forgotten, test_two_requests_never_share_a_nonce, test_what_the_model_sees_carries_the_question_and_no_fingerprint, test_the_shared_book_can_be_reset
Exercises: friday

### `tests/test_connector_plane.py` (143 lines)

Covers: ConnectorControlPlane — discovery, flows, isolation of secrets. The product contract: "Jarvis, connect X" leaves the boss exactly one human step, and no secret value ever reaches model-visible space (tool results, logs, state rows). Providers are DISCOVERED from the Hermes registry, never memorized.
Tests[11]: test_discovery_is_dynamic_not_memorized, test_each_connector_states_its_one_human_step, test_oauth_flow_launches_and_asks_exactly_one_human_step, test_oauth_launch_failure_degrades_to_clear_instruction, test_authenticated_provider_connects_without_any_human_step, test_api_key_flow_stores_only_an_opaque_ref, test_cancelled_entry_window_is_a_calm_human_step_not_an_error, test_verify_after_signin_flips_to_connected, test_repair_reauths_only_when_credential_is_genuinely_absent, test_unknown_connector_names_the_known_ones, test_status_never_contains_secret_material
Exercises: friday.connectors

### `tests/test_continuity.py` (313 lines)

Covers: No docstring; 24 test(s).
Tests[24]: test_start_run_persists_authority_task_and_one_immediate_wake, test_claim_checkpoint_reopen_and_reclaim_same_run, test_duplicate_wake_claim_creates_only_one_portion, test_narration_reservation_is_atomic_for_one_semantic_milestone, test_narration_reservation_survives_store_reopen, test_non_terminal_checkpoint_requires_a_wake, test_checkpoint_refuses_a_task_reference_missing_from_this_run, test_invariant_detector_rejects_a_manually_deleted_wake, test_only_the_matching_external_signal_makes_a_run_runnable, test_provider_exhaustion_schedules_a_bounded_future_retry, test_a_successful_attempt_is_never_reserved_for_execution_twice, test_pause_invalidates_a_claim_and_cancel_defeats_stale_wakes, test_a_total_portion_budget_stops_without_claiming_success, test_completion_requires_a_succeeded_verification_task, test_an_expired_active_lease_recovers_to_a_new_portion, test_an_expired_attempt_with_unknown_side_effect_cannot_be_retried, test_each_wake_kind_maps_to_one_truthful_nonterminal_state, test_action_budget_is_enforced_when_the_spend_lands, test_model_token_budget_is_enforced_when_the_spend_lands, test_elapsed_budget_is_enforced_at_the_next_claim, test_retry_budget_exhaustion_is_an_explicit_failure, test_portion_token_cap_marks_claim_exhausted, test_run_budget_finishes_immediately_not_at_next_claim, test_remaining_budget_reports_what_is_left
Exercises: friday, friday.store

### `tests/test_continuity_fresh_db.py` (111 lines)

Covers: The durable-objective engine must work on a *fresh* database. Regression for a real bug found 2026-08-29 while running the autonomous test: `continuity.start_run` INSERTs into `runs` (columns attended, provenance) and into run_controls / run_tasks, but the reconstructed schema created only the `runs` table and omitted attended/provenance and all eight run_* tables. The live database had them from an older schema it was migrated across, so the bug was invisible in production and total on any clea
Tests[5]: test_the_runs_table_has_the_columns_continuity_writes, test_all_continuity_tables_exist_on_a_fresh_db, test_an_objective_is_admitted_on_a_fresh_database, test_the_objective_survives_a_restart, test_provenance_is_validated
Exercises: friday, friday.store

### `tests/test_continuity_livekit.py` (326 lines)

Covers: No docstring; 12 test(s).
Tests[12]: test_tool_budget_starts_a_second_generation_without_user_input, test_unrecoverable_model_error_becomes_a_durable_retry, test_structural_history_failure_uses_one_transcript_free_continuation, test_structural_recovery_pumps_when_tool_context_bound_speech_already_finished, test_fresh_adapter_reconstructs_due_work_without_chat_history, test_run_update_persists_the_next_task_before_starting_it, test_late_speech_callback_cannot_checkpoint_the_new_portion, test_friday_records_the_objective_before_learning_the_turn, test_session_usage_records_only_the_new_cumulative_tokens, test_progress_speech_requires_a_new_event_and_respects_cooldown, test_session_close_releases_the_lease_to_a_durable_restart_wake, test_external_pause_invalidates_late_speech_and_next_turn_resumes
Exercises: agent_friday, friday, friday.continuity_livekit, friday.provider_fallback, friday.store

### `tests/test_contracts.py` (495 lines)

Covers: Proof-of-work contract tests. The headline test is test_arc_reactor_* - a permanent regression against the agent narrating an action that never happened.
Tests[40]: test_succeeded_without_verification_is_a_construction_error, test_succeeded_with_verification_is_allowed, test_non_succeeded_statuses_may_not_claim_completion, test_partial_cannot_claim_completion_even_with_verification, test_verification_rejects_empty_evidence, test_failed_requires_an_error_message, test_unknown_status_rejected, test_constructor_flow_started_to_succeeded, test_failed_constructor_records_error, test_artifact_requires_known_type_and_path, test_artifact_must_belong_to_its_run, test_run_cannot_complete_with_an_unverified_action, test_run_cannot_complete_with_no_actions_at_all, test_run_completes_when_every_action_succeeded, test_reading_the_file_does_not_back_having_processed_it, test_processing_it_does_back_having_processed_it, test_a_verb_with_no_capability_behind_it_can_never_be_claimed, test_a_run_that_is_still_going_does_not_back_a_past_tense_claim, test_an_interrupted_run_does_not_back_it_either, test_a_completed_run_backs_it, test_a_tool_that_reports_no_state_is_unchanged, test_the_generic_rule_still_holds_for_verbs_with_no_entry, test_every_prefix_the_claim_table_names_matches_a_real_capability, test_a_verb_with_no_capability_is_deliberate_not_forgotten, test_an_honest_sentence_about_reading_is_never_flagged…
Exercises: friday, friday.store

### `tests/test_conversation_memory.py` (136 lines)

Covers: The amnesia regressions: a new session must not start from nothing. Each test here failed before 2026-09-01. `messages` and `conversations` were real tables with zero writers, memory_stack had no transcript tier, and the capability surface the conversational brain offered was a hand-written dict that had already fallen behind the fabric.
Tests[8]: test_a_turn_survives_the_session, test_history_spans_conversations, test_the_transcript_reaches_the_prompt, test_the_transcript_does_not_eat_the_whole_budget, test_contacts_are_remembered_and_recalled, test_the_capability_surface_comes_from_the_registry, test_mutating_operations_still_need_a_gate, test_an_old_preference_is_still_found_behind_newer_ones
Exercises: friday, friday.store, friday.toolsets

### `tests/test_core01_gates.py` (302 lines)

Covers: CORE-01 integration gates: one execution, one owner, no "Continue". RED-A/B/C proved the three defects were real and are fixed. These prove the things that only go wrong once they *are* fixed: the model helpfully doing the objective's work as well a run nobody can ask about or stop by speaking a compound request that still needs a second message to finish The last one is the whole point of CORE-01, so it is measured rather than asserted from the shape of the code: the counters are incremented by
Tests[12]: test_a_queued_capability_is_claimed_by_the_objective, test_a_capability_the_objective_does_not_want_is_free, test_nothing_is_claimed_when_no_objective_is_running, test_a_finished_task_releases_its_claim, test_a_claim_expires_so_a_later_request_is_not_refused, test_the_guard_defers_a_claimed_conversational_call, test_the_objective_executor_is_not_guarded, test_asking_how_it_is_going_reports_and_does_not_disturb_the_run, test_saying_stop_cancels_the_run, test_a_side_question_leaves_the_run_alone, test_stop_with_nothing_running_is_just_conversation, test_one_compound_turn_needs_no_second_message
Exercises: agent_friday, friday, friday.continuous, friday.store, friday.toolsets

### `tests/test_core01_red.py` (327 lines)

Covers: CORE-01 gates: admission, reach, and a graph that is actually a graph. These three failures are one failure wearing three costumes, and fixing any one alone makes the system worse rather than better: admission without reach objectives start, reach 5 of 132, and abandon the rest silently - worse than today, because today's failure is at least visible reach without admission nothing admits anything, so nothing changes either without the graph one unlucky task strands every independent one So they 
Tests[9]: test_red_a_an_objective_can_reach_a_registered_capability, test_red_a_reach_is_most_of_the_registry_not_a_handful, test_red_a_an_unresolved_capability_says_why_rather_than_vanishing, test_red_b_a_compound_request_becomes_a_durable_run, test_red_b_an_ordinary_turn_does_not_touch_the_turn_context, test_red_b_an_admitted_turn_tells_the_model_it_is_owned, test_red_b_a_simple_request_does_not_become_a_run, test_red_c_independent_clauses_do_not_depend_on_each_other, test_red_c_a_failure_does_not_strand_its_siblings
Exercises: agent_friday, friday, friday.arbiter, friday.continuous, friday.store, friday.toolsets

### `tests/test_cross_capability_journey.py` (177 lines)

Covers: Slice 9 - the cross-capability golden journey. Every family has its own tests. This is the one that proves they compose: a single user objective that crosses several capability families, routed through the real `capability_use` bridge, with Friday as the only manager - the model names an outcome (a family), never a brand, and the result of one step feeds the next. The objective, stated once: "I'm working through a single-cell RNA dataset that's published on a web page. Find the right method, pul
Tests[4]: test_one_objective_crosses_four_families_and_the_work_carries_forward, test_friday_names_outcomes_not_brands_across_the_whole_journey, test_the_security_scope_gate_holds_mid_journey, test_an_unavailable_family_step_fails_without_sinking_the_journey
Exercises: friday, friday.fabric_adapters, friday.tools

### `tests/test_daily_driver_hardening.py` (52 lines)

Covers: The two behavioural gaps between 'supervised' and 'daily driver': item 4 - a skill-shaped request (diagram, report, expert review) is surfaced to the fabric families instead of coming back empty and getting answered from the model's own head; item 6 - the worker rides out a longer link outage than the datacenter default, so a home-connection blip does not leave a dead assistant. Both are unit-testable here; the remaining part of each (does the model *prefer* the family? does the link actually re
Tests[3]: test_skill_shaped_requests_surface_a_fabric_family, test_plain_core_tool_requests_get_no_family_hint, test_worker_rides_out_a_longer_outage_than_the_datacenter_default
Exercises: agent_friday

### `tests/test_data_class.py` (108 lines) [UNTRACKED]

Covers: FR-PRV-001: classification vocabulary, the deterministic classifier, and the routing rule - proven independently of any prompt or gateway call.
Tests[16]: test_classes_are_ordered_least_to_most_sensitive, test_classify_public, test_classify_internal, test_classify_personal, test_classify_customer_pii, test_classify_confidential, test_classify_secret, test_classify_local_only, test_hint_can_only_raise_never_lower, test_secret_and_local_only_refuse_off_a_local_backend, test_secret_and_local_only_allow_on_a_local_backend, test_customer_pii_and_confidential_redact_on_cloud, test_public_and_internal_allow_anywhere, test_private_only_mode_refuses_rather_than_redacts, test_redact_removes_and_counts, test_redact_removes_email_and_name_pair
Exercises: friday

### `tests/test_dbconn.py` (83 lines)

Covers: The per-call ledger connections close (A-051 finding). `sqlite3.Connection` as a context manager commits/rolls back and does NOT close. Seven ledgers wrote `with self._connect() as db:` and left every connection to the garbage collector - a kernel handle per call on Windows, measured as a monotonic handle leak in the A-051 soak (~3.5 per Hermes crash cycle). `friday.dbconn.ledger_connection` is the one fix; this test holds it for every ledger that uses the pattern, by counting the open handles o
Tests[4]: test_ledger_connection_closes_on_exit_and_commits, test_ledger_connection_rolls_back_on_error_and_still_closes, test_a_bare_sqlite3_connection_would_have_leaked, test_every_per_call_ledger_leaves_no_handle_behind
Exercises: friday

### `tests/test_delivery_modes.py` (64 lines)

Covers: Semantic delivery vs voice rendering - separate decisions, one message. The three cases from the Phase 1F contract: A. voice session (audio output attached) -> spoken + in chat B/C. text-only session (no audio output) -> in chat, no speech attempt Plus: the mode check reads REAL session output state, never the request's origin.
Tests[5]: test_voice_session_allows_speech, test_disabled_audio_is_text_mode, test_missing_audio_output_is_text_mode, test_broken_session_defaults_to_silent, test_delivery_routes_by_mode
Exercises: agent_friday

### `tests/test_delivery_staleness.py` (160 lines)

Covers: A finished delegation is news for a while and then it is history. Measured on 2026-08-27: four smoke-test completions were recorded PENDING at 18:13-18:22 while no LiveKit session was connected. At 22:43 a fresh probe room opened - a conversation that had asked for none of them - and Friday recited all four, cold, four and a half hours late. `hermes_deliveries` has no room or session column, so "deliver what is PENDING" means "deliver into whoever turns up". That is right for the reconnect case 
Tests[11]: test_a_fresh_delivery_is_still_spoken, test_a_delivery_older_than_the_ttl_is_not_spoken, test_an_expired_delivery_records_why_rather_than_vanishing, test_expiring_is_idempotent_and_does_not_touch_fresh_rows, test_an_expired_delivery_is_never_resurrected_by_a_later_sweep, test_the_ttl_is_the_same_number_the_sweep_uses, test_a_non_production_origin_is_still_refused_regardless_of_age, test_a_fresh_objective_completion_is_still_announced, test_an_objective_completion_older_than_the_ttl_is_not_announced, test_an_expired_objective_delivery_records_why, test_both_halves_of_the_seam_use_the_same_window
Exercises: friday

### `tests/test_development.py` (375 lines)

Covers: The orchestrator, and the thing it exists to prevent. Five modules were built, tested, and called by nothing. That is decoration, and it is the same failure as `text_input_callback` calling `prepare_turn` and nothing else: not broken, not connected. These tests exist mostly to prove the connections, so the next person who adds a stage cannot quietly leave it unwired. The coding agent is injected, so this drives the whole pipeline without an authenticated CLI and without spending money on every s
Tests[30]: test_it_maps_the_repository, test_a_project_with_no_code_is_not_mapped_and_still_runs, test_a_broken_graph_costs_the_shortcut_not_the_run, test_the_context_is_a_summary_not_the_whole_map, test_no_graph_means_no_context_rather_than_a_crash, test_it_chooses_a_team_and_says_why, test_the_team_reaches_the_agent, test_the_map_reaches_the_agent, test_a_run_that_fails_keeps_what_it_learned, test_scope_reaches_the_agent_as_a_constraint, test_no_scope_means_no_invented_constraints, test_passing_work_verifies, test_broken_work_does_not_verify, test_verification_runs_with_the_network_off, test_a_verifier_that_cannot_run_is_inconclusive, test_the_attempt_is_recorded_for_later_routing, test_verified_and_approved_work_may_land, test_unverified_work_may_not_land, test_the_gate_decides_but_does_not_act, test_the_report_covers_every_stage, test_a_run_that_never_started_still_reports, test_it_explains_a_refusal_in_words, test_it_explains_success_in_words, test_every_module_is_actually_reached, test_it_picks_an_executor_and_records_why…
Exercises: friday, friday.executors.claude_code

### `tests/test_devops_health.py` (73 lines)

Covers: Phase 14 devops: production-health proofs. - Migration: a REAL pre-H0 database (production schema from before this build) opens cleanly, keeps its rows, gains the new columns/tables. - Crash recovery: a WorkRun left WORKING by a dead process is visible; the delivery sweep backfills old runs without announcing history. - Missing optional backends (Paperclip) degrade honestly (tested in test_orgplane, asserted here end-to-end via control_plane()). - Stale scratch: an abandoned secret-entry scratch
Tests[5]: test_pre_vnext_database_migrates_and_keeps_rows, test_crashed_working_run_is_visible_not_lost, test_sweep_backfills_old_completions_silently, test_paperclip_absent_is_honest_end_to_end, test_abandoned_scratch_is_inert
Exercises: friday, friday.execution_economics, friday.user_policy

### `tests/test_documents.py` (356 lines)

Covers: Reading the files `files_read` cannot. Every document here is built in the test, with the real libraries, and read back with the real readers. A fixture PDF checked into the repo proves that a particular file still parses; building one proves the round trip. What is NOT here is as much the point. The donor this came from (`Mark-L/actions/file_processor.py`) has `action="run"` -> `subprocess.run(["python", path])`, an arbitrary execution primitive reachable by naming a file. It did not come acros
Tests[20]: test_a_pdf_comes_back_as_text, test_a_page_range_reads_only_those_pages, test_a_page_range_outside_the_document_reads_all_of_it, test_a_scanned_pdf_says_it_needs_ocr_rather_than_reporting_nothing, test_a_word_document_includes_its_tables, test_a_spreadsheet_comes_back_as_csv, test_a_named_sheet_is_read_and_a_missing_one_is_refused, test_a_huge_spreadsheet_stops_and_says_so, test_a_slide_deck_comes_back_slide_by_slide, test_an_archive_is_listed_not_unpacked, test_inspecting_a_pdf_counts_pages_without_the_text, test_inspecting_a_workbook_names_its_sheets, test_a_document_outside_the_roots_is_refused, test_a_protected_file_is_refused_even_with_a_document_suffix, test_a_plain_text_file_is_sent_to_files_read, test_a_corrupt_document_fails_as_a_result_not_an_exception, test_nothing_here_can_execute_anything, test_files_read_points_at_the_document_reader_instead_of_stopping, test_a_binary_with_no_reader_still_just_says_binary, test_a_document_read_is_auto_and_needs_no_approval
Exercises: friday, friday.fsjail, friday.toolsets

### `tests/test_evaluation.py` (150 lines)

Covers: Deciding from evidence instead of from "Opus is probably best for this". The dangerous failure here is a router that swings on one lucky run and looks informed while doing it. Most of these tests are about refusing to conclude.
Tests[16]: test_a_pass_rate_is_computed_from_decided_attempts, test_inconclusive_attempts_are_counted_and_excluded, test_nothing_recorded_yields_no_rate_rather_than_zero, test_timing_is_measured_over_passing_attempts_only, test_scoring_can_be_narrowed_to_a_model, test_one_lucky_run_does_not_decide_anything, test_a_verdict_needs_the_minimum_number_of_attempts, test_correctness_outranks_speed, test_speed_only_separates_equals, test_evidence_for_one_task_does_not_decide_another, test_compare_ranks_agents_for_a_human_to_read, test_a_saved_record_loads_back, test_a_missing_record_starts_empty, test_a_corrupt_record_starts_empty_rather_than_raising, test_a_verifier_describes_what_it_proves, test_an_attempt_that_did_not_pass_is_not_passed
Exercises: friday

### `tests/test_execution.py` (302 lines)

Covers: The execution boundary, proven rather than described. The claims worth testing are the ones that would otherwise be comments: a secret does not cross, a path does not escape, a process does not outlive the block, and a runaway does not take the machine.
Tests[34]: test_a_secret_does_not_cross_the_boundary, test_the_environment_is_an_allowlist_not_a_denylist, test_the_process_still_gets_what_it_needs_to_run, test_an_inherited_proxy_does_not_survive, test_injected_credentials_reach_the_process, test_injected_credentials_are_redacted_from_output, test_credentials_are_dropped_when_the_block_ends, test_the_report_names_credentials_without_carrying_them, test_deny_all_is_the_default, test_an_allowlist_permits_only_what_it_names, test_a_leading_dot_covers_subdomains, test_an_empty_allowlist_is_refused_rather_than_silently_denying, test_an_unknown_mode_is_refused, test_development_defaults_to_no_network, test_development_with_named_hosts_is_an_allowlist, test_a_path_cannot_climb_out, test_an_absolute_path_cannot_reach_out, test_an_absolute_path_is_refused_on_every_host, test_reading_and_writing_inside_is_fine, test_export_copies_one_named_file, test_export_refuses_a_path_outside_the_sandbox, test_export_refuses_something_too_large, test_a_missing_workspace_is_refused_before_anything_runs, test_a_command_runs_in_the_workspace, test_a_failing_command_reports_its_code…
Exercises: friday

### `tests/test_execution_bridge.py` (75 lines)

Covers: The execution bridge's tool functions, exercised directly. The MCP transport is the official SDK's; what needs proving here is OUR surface: jail refusals, forbidden-command refusals, honest errors, and that outputs stay bounded.
Tests[9]: test_read_inside_workspace, test_read_escape_is_refused_not_errored, test_write_then_read_roundtrip, test_write_escape_refused, test_listing_and_search, test_run_command_real_execution, test_shells_are_refused_by_name, test_timeout_is_bounded_and_reported, test_missing_workspace_env_fails_loudly
Exercises: friday, friday.tools

### `tests/test_execution_economics.py` (107 lines)

Covers: Phase H - execution economics. The gate's own acceptance table: simple QA -> Friday direct, no worker mechanical -> deterministic, ZERO model calls tiny bounded -> economy tier auth/one-line -> DEEP tier + FULL verification (risk beats size - the spec's "change one authentication header" case) research/deep -> deep tier multi-stream -> parallel workers explicit override -> honored untouched no routing calls -> classify/choose are pure functions (no model) Plus H6: outcome records, rework economi
Tests[11]: test_simple_question_stays_out_of_hermes, test_mechanical_work_uses_no_model, test_tiny_change_routes_economy, test_auth_change_is_high_consequence_despite_tiny_size, test_architecture_reasoning_routes_deep, test_multi_stream_routes_parallel, test_verification_is_priced_not_uniform, test_router_never_invents_models, test_routing_costs_zero_model_calls, test_outcomes_record_and_rework_economics, test_failed_runs_have_zero_value
Exercises: friday

### `tests/test_executor.py` (446 lines)

Covers: Handing work to Claude Code without handing over control. Two things are load-bearing here and everything else supports them: * a question broker decides what is TRUE, a permission broker decides what is ALLOWED, and neither may answer the other's question. That is how a preference turns into an authorisation. * "Claude says it's done" is not a result. finish() wants a diff. The CLI facts these tests encode were probed against the installed 2.1.233, not read from the docs - see friday/executors/
Tests[44]: test_bypass_permissions_is_not_a_choice, test_the_prompt_is_never_on_the_command_line, test_variadic_flags_come_last, test_hooks_are_off_by_default, test_the_default_permission_mode_is_the_installed_one, test_resuming_replaces_a_fresh_session_id, test_mcp_config_is_strict_by_default, test_an_assistant_message_yields_one_event_per_block, test_the_session_id_is_read_from_init, test_a_junk_line_is_skipped_not_fatal, test_exploring_cannot_change_anything, test_an_unrecognised_task_gets_the_smallest_profile, test_a_building_task_may_write, test_no_profile_opens_up_shell_access_wholesale, test_the_dangerous_commands_are_in_no_profile, test_what_the_cli_refused_is_read_back, test_an_accepted_decision_answers_without_asking, test_a_stated_fact_can_answer, test_an_inference_never_decides, test_a_preference_is_context_not_a_decision, test_a_pattern_is_context_too, test_two_facts_that_both_fit_are_not_chosen_between, test_the_user_is_asked_when_nothing_settles_it, test_the_same_question_is_never_asked_twice, test_his_answer_survives_the_run…
Exercises: friday, friday.executors, friday.executors.claude_code, friday.store

### `tests/test_executor_continuity.py` (402 lines)

Covers: The coding session is disposable. The work is not. This is the claim the whole executor exists to make good on: kill Claude, kill ADA, open a conversation that has never heard of any of it, and the run is still there with everything needed to carry on. Two invariants underneath: * a process exiting is not a verdict. A run whose process died was INTERRUPTED, which invites a resume; FAILED invites a shrug. * one run_id means at most one live executor. Asking twice must attach, not launch a second 
Tests[30]: test_a_run_is_recorded_before_anything_happens, test_the_whole_bundle_is_kept_not_a_summary_of_it, test_progress_is_heartbeated, test_a_dead_process_makes_the_run_interrupted, test_a_live_process_is_left_alone, test_a_finished_run_is_not_reopened, test_startup_reconciles_every_run_that_thinks_it_is_alive, test_exit_code_zero_is_not_success, test_a_second_executor_for_a_live_run_is_refused, test_resuming_twice_attaches_rather_than_launching_again, test_a_dead_run_may_be_restarted, test_nothing_is_alive_without_a_pid, test_a_recycled_pid_is_not_the_process_we_meant, test_a_run_records_when_its_process_started, test_a_row_with_no_start_time_still_works, test_rung_one_attach, test_rung_two_resume_the_saved_session, test_rung_three_a_lost_session_does_not_lose_the_project, test_rung_three_also_covers_a_run_that_never_got_a_session, test_rung_four_asks_only_when_something_is_really_missing, test_an_already_finished_run_is_not_silently_restarted, test_a_restart_tells_the_new_session_what_the_old_one_did, test_a_fresh_conversation_can_describe_the_work, test_the_briefing_is_empty_when_there_is_nothing_to_say, test_active_work_excludes_what_is_finished…
Exercises: friday, friday.executors, friday.executors.claude_code, friday.store

### `tests/test_executor_router.py` (221 lines)

Covers: Routing from evidence, and refusing to when there isn't any. The failure guarded here is a router that looks informed. Swinging on one lucky run is worse than a fixed default, because a fixed default is at least honest about being one.
Tests[22]: test_discovery_is_a_runtime_check_not_a_config_file, test_an_installed_executor_is_discovered, test_installed_is_not_the_same_as_usable, test_discovery_separates_missing_from_unsupported, test_one_executor_needs_no_deliberation, test_no_usable_executor_says_so_rather_than_guessing, test_without_a_record_the_default_is_used_and_labelled, test_a_fallback_is_distinguishable_from_a_measurement, test_enough_evidence_decides, test_the_reason_carries_the_number, test_evidence_for_another_task_does_not_decide_this_one, test_what_was_considered_is_recorded, test_the_choice_serialises_for_the_run_record, test_a_named_executor_is_used_when_usable, test_a_named_executor_that_is_not_usable_is_refused_with_the_reason, test_an_unknown_name_is_refused, test_building_something_unknown_raises, test_building_something_with_no_builder_raises_and_explains, test_building_something_not_installed_raises, test_it_never_substitutes_a_different_executor, test_every_declared_executor_says_why_it_is_or_is_not_buildable, test_the_default_is_one_of_the_known
Exercises: friday

### `tests/test_executor_sandbox.py` (140 lines)

Covers: The development run inside the boundary. A git worktree is version control, not containment: it protects the branch and does nothing about the environment the agent inherits, the processes it leaves behind, or the memory it takes. These cover the seam where the two meet.
Tests[11]: test_the_host_backend_is_still_the_default, test_a_run_with_no_sandbox_behaves_exactly_as_before, test_a_sandboxed_run_gets_an_allowlisted_environment, test_the_coding_allowlist_permits_the_agents_own_api, test_the_coding_allowlist_refuses_somewhere_new, test_cancelling_a_sandboxed_run_closes_the_job, test_every_exit_path_closes_the_sandbox, test_closing_twice_is_safe, test_the_host_backend_builds_no_sandbox, test_the_sandbox_backend_builds_one_and_reports_its_strength, test_a_boundary_that_cannot_be_built_is_raised_not_downgraded
Exercises: friday, friday.executors

### `tests/test_fabric.py` (365 lines)

Covers: The Capability Fabric's contract, proved against the deterministic dummy. Every assertion here is about the fabric, not about an upstream. That is the point of `fabric_adapters/dummy`: when one of these fails it is a fact about this code, and when a real provider's test fails later it is a fact about that provider.
Tests[34]: test_registry_is_discovered_not_written_down, test_registering_starts_nothing, test_unknown_provider_names_itself, test_copyleft_cannot_declare_an_importing_integration_mode, test_copyleft_sidecar_is_allowed, test_an_upstream_must_be_pinned, test_reference_only_needs_no_pin, test_a_provider_must_declare_operations, test_every_registered_fallback_exists, test_activation_is_lazy_and_idempotent, test_a_provider_that_will_not_start_is_recorded_not_raised, test_health_probe_raising_is_contained, test_degraded_is_reported_as_degraded_not_ready, test_auth_required_is_its_own_state, test_deactivate_is_safe_when_nothing_is_up, test_health_without_a_probe_makes_the_weak_claim_out_loud, test_router_prefers_the_cheaper_provider, test_router_skips_a_provider_that_cannot_come_up, test_router_filters_by_operation, test_router_filters_by_permission, test_allow_model_false_excludes_model_backed_providers, test_reference_only_is_never_routed_to, test_call_returns_an_honest_envelope_correlated_to_the_run, test_a_succeeded_result_cannot_exist_without_verification, test_calling_an_undeclared_operation_is_a_construction_error…
Exercises: friday, friday.fabric_adapters

### `tests/test_fabric_agent_packs.py` (173 lines)

Covers: S4: two more upstreams pinned as `roles`-family SKILL packs -- agents-team's templates/rules/skills and awesome-claude-code-subagents' 158 briefs. Same discipline as role_recipes / company_playbooks: names are cheap, one file is read at a time, and there is no bulk read.
Tests[12]: test_descriptor_commit_matches_the_lock, test_catalogue_covers_all_categories_and_is_non_empty, test_search_finds_python_pro, test_recipe_reads_one_file_and_refuses_traversal_and_unknown_names, test_category_operation_is_scoped_and_validated, test_archetypes_and_rules_are_non_empty, test_archetype_reads_one_file_and_refuses_traversal_and_unknown_names, test_rule_and_skill_read_one_file_each, test_absent_clone_reports_unavailable_without_raising, test_voice_brain_surface_exposes_the_new_reads, test_org_divisions_gain_a_voltagent_category_and_total_grows, test_agent_resolves_a_bare_name_to_its_catalogue_path
Exercises: friday, friday.fabric_adapters, friday.voice_brain

### `tests/test_fabric_cli_adapters.py` (88 lines)

Covers: The CLI-mode adapters: the upstreams the owner listed, reachable through the fabric without any of them appearing in a UI.
Tests[8]: test_each_is_registered_pinned_and_isolated, test_the_gpl_worker_is_isolated_not_imported, test_every_mutating_operation_is_gated, test_a_gated_call_refuses_before_spawning, test_a_hostile_instruction_is_one_argv_element, test_unbuilt_upstream_is_unavailable_with_the_install_hint, test_secrets_reach_the_child_as_environment_not_argv, test_the_matrix_has_no_unclassified_clone
Exercises: friday, friday.fabric_adapters

### `tests/test_fabric_code_intel.py` (406 lines)

Covers: The code-intelligence providers, as adapters rather than as upstreams. These assert Friday's side of the contract - descriptor shape, licence mode, argument translation, error handling - which holds whether or not the binary is installed on this machine. The tests that need the real binary are marked `live` and skip cleanly, so CI on a fresh checkout is honest rather than green by omission.
Tests[32]: test_it_is_registered_in_the_code_intelligence_family, test_it_is_pinned_to_the_audited_commit, test_it_runs_out_of_process, test_it_declares_owning_a_process_so_the_singleton_check_sees_it, test_every_declared_operation_maps_to_an_upstream_tool, test_it_needs_no_secret_and_no_model, test_unknown_arguments_are_dropped_rather_than_passed_through, test_empty_arguments_are_omitted_not_sent_as_empty_flags, test_an_upstream_error_object_becomes_an_exception_not_a_success, test_log_lines_before_the_payload_do_not_break_parsing, test_a_nonzero_exit_names_the_tool_and_keeps_the_stderr, test_a_missing_binary_is_unavailable_not_a_crash, test_no_warm_daemon_is_degraded_rather_than_down, test_a_real_query_finds_a_known_symbol_at_its_real_line, test_the_index_excludes_the_env_file, test_graft_is_registered_in_the_same_family_as_the_exact_provider, test_graft_is_pinned_to_the_audited_commit, test_graft_holds_no_daemon_so_the_singleton_check_leaves_it_alone, test_graft_falls_back_to_the_exact_provider, test_every_graft_operation_maps_to_a_subcommand, test_graft_costs_no_model_and_no_secret, test_graft_init_is_not_an_operation, test_graft_deep_build_is_not_reachable_through_the_operation_table, test_graft_drops_unknown_arguments_rather_than_passing_them_through, test_graft_passes_the_positional_argument_without_a_flag…
Exercises: friday, friday.fabric_adapters

### `tests/test_fabric_commerce_and_packs.py` (146 lines)

Covers: The 2026-09-02 upstream set: commerce, company playbooks, harness templates, transcription, and the OpenMausBot skills. Each provider is tested for the thing that makes it safe rather than for the thing that makes it work: the allowlist, the gate, the honest UNAVAILABLE.
Tests[15]: test_every_new_provider_is_pinned_and_registered, test_commerce_is_a_real_family_with_medusa_first, test_smartstore_is_copyleft_and_isolated, test_commerce_writes_are_gated_and_reads_are_open, test_commerce_never_exposes_payment_or_refund, test_a_write_without_a_grant_is_refused_before_any_http, test_absent_store_is_unavailable_with_the_reason, test_medusa_id_must_be_a_bare_id, test_smartstore_key_is_numeric, test_playbooks_read_one_at_a_time_and_refuse_unknown_names, test_harness_checklist_is_structured, test_mausbot_enterprise_tree_is_unreachable, test_transcribe_without_the_key_fails_by_alias_never_value, test_transcribe_puts_the_key_in_env_never_argv, test_agent_reach_exposes_only_the_two_safe_verbs
Exercises: friday, friday.fabric_adapters

### `tests/test_fabric_completion.py` (300 lines)

Covers: The rest of the integration-gap plan: SVC-01, LEARN-01, CENSUS-01, HEALTH-01. Plus the per-operation permission field that landed when GATE-01's fail-closed gate met a provider declaring one permission across three operations, two of which its own notes call open.
Tests[31]: test_what_the_fabric_learns_survives_a_restart, test_a_store_that_will_not_write_does_not_cost_the_call, test_pruning_drops_nothing_inside_the_window, test_knowing_is_not_doing, test_an_open_operation_needs_no_grant, test_a_gated_operation_still_refuses_without_the_grant, test_ranking_and_enforcement_agree, test_one_outcome_is_not_a_habit, test_repeated_success_earns_a_bonus, test_repeated_failure_earns_a_penalty, test_a_split_record_teaches_nothing, test_the_adjustment_is_capped, test_rank_is_stable_for_providers_it_knows_nothing_about, test_a_failing_provider_sinks_below_its_fallback, test_a_refusal_is_not_recorded_as_a_provider_failure, test_a_real_call_is_recorded, test_report_is_json_shaped, test_a_non_loopback_base_url_is_refused, test_the_url_comes_from_the_supervisors_port, test_only_idempotent_gets_are_retried, test_an_unknown_placeholder_is_refused, test_evidence_does_not_overclaim, test_health_on_a_service_never_started_is_registered, test_an_unknown_operation_is_refused, test_every_clone_lands_in_exactly_one_bucket…
Exercises: friday, friday.fabric_adapters, friday.toolsets

### `tests/test_fabric_execution.py` (291 lines)

Covers: Execution modes: the fabric must be able to RUN third-party code, safely. Before 2026-09-01 nine of sixteen providers were `SKILL` (no code executed), `SIDECAR` was a word with no runtime, there was no mode at all for a command-line agent, and `fabric.call()` checked neither permissions nor secrets. Every test here failed before that date.
Tests[26]: test_a_call_without_the_grant_is_refused, test_the_grant_lets_it_through, test_authorized_none_means_no_grants_not_all, test_both_entry_points_refuse_identically, test_a_missing_secret_names_the_alias_not_the_value, test_a_child_that_never_readies_is_stopped_and_reported, test_a_child_that_exits_at_startup_is_not_ready, test_readiness_is_waited_for_not_assumed, test_an_external_kill_becomes_crashed, test_stop_actually_stops_a_stubborn_child, test_the_child_does_not_inherit_fridays_secrets, test_a_declared_env_var_does_reach_the_child, test_each_child_gets_its_own_port, test_restart_is_bounded, test_a_literal_json_argument_is_not_a_placeholder, test_a_placeholder_reaches_the_child_as_one_argument, test_an_unknown_placeholder_fails_before_spawning, test_a_timeout_kills_and_reports, test_malformed_json_is_a_failure_not_a_string, test_good_json_is_parsed, test_a_bad_exit_code_is_a_failure, test_evidence_redacts_values_and_keeps_names, test_exit_zero_is_not_claimed_as_verification, test_an_unknown_operation_is_refused, test_cli_is_an_isolated_mode…
Exercises: friday

### `tests/test_fabric_gstack.py` (252 lines)

Covers: gstack as methodology, and the discipline that keeps it methodology. The risk with a 61-workflow pack is not that it fails to load. It is that it loads too well: that it becomes a second orchestrator, that its browser rules end up next to Friday's, or that answering "review this" costs a hundred thousand tokens of procedure nobody read. These tests are mostly about those three, not about whether markdown can be opened.
Tests[24]: test_it_executes_no_upstream_code, test_it_is_pinned_to_the_audited_commit, test_it_falls_back_to_the_other_role_pack, test_the_browser_workflows_are_withheld, test_host_setup_and_self_upgrade_are_withheld, test_every_exclusion_carries_a_reason, test_reading_a_withheld_workflow_says_why_rather_than_reading_it, test_the_catalogue_is_metadata_not_procedures, test_one_skill_is_read_at_a_time_and_nothing_is_cached, test_there_is_no_bulk_read_operation, test_a_procedure_is_size_capped, test_a_plain_request_routes_to_the_right_workflow, test_a_security_review_reaches_a_workflow_that_covers_security, test_routing_needs_no_slash_command_syntax, test_routing_without_a_task_is_refused, test_an_arbitrary_path_cannot_be_read_through_the_skill_operation, test_a_specialist_path_outside_the_specialist_directory_is_refused, test_the_review_specialists_are_offered, test_it_does_not_duplicate_an_existing_friday_capability, test_the_other_skill_packs_are_still_registered, test_two_role_providers_coexist_rather_than_one_overwriting_the_other, test_an_uncloned_pack_is_unavailable_rather_than_a_broken_boot, test_an_uncloned_pack_returns_an_empty_catalogue_rather_than_raising, test_an_unknown_operation_is_named
Exercises: friday, friday.fabric_adapters

### `tests/test_fabric_helpers_research.py` (243 lines)

Covers: open_notebook_research, maxun_scraping and openmontage_media: request shape, auth header, honest UNAVAILABLE, and that a write refuses without its required fields. Each test spins up one real (fake) HTTP server in a thread so the request that leaves the adapter is proven, not assumed.
Tests[21]: test_every_new_provider_is_pinned_and_registered, test_maxun_is_copyleft_and_isolated, test_openmontage_is_copyleft_and_isolated, test_open_notebook_writes_are_gated_and_reads_are_open, test_maxun_writes_are_gated_and_reads_are_open, test_openmontage_has_no_permissions_or_secrets, test_open_notebook_absent_store_is_unavailable_with_the_reason, test_maxun_absent_store_is_unavailable_with_the_reason, test_openmontage_absent_store_is_unavailable_with_the_reason, test_open_notebook_notebooks_sends_bearer_password, test_open_notebook_notebook_needs_id, test_open_notebook_ask_creates_session_then_executes, test_open_notebook_ask_refuses_without_notebook_or_question, test_open_notebook_add_source_is_a_write_and_posts_url, test_maxun_robots_sends_raw_api_key_header, test_maxun_requires_the_key_before_any_http, test_maxun_runs_needs_a_bare_robot_id, test_maxun_results_hits_robot_and_run_path, test_maxun_run_robot_is_a_write_and_posts, test_openmontage_projects_needs_no_secret, test_openmontage_project_needs_a_bare_id
Exercises: friday, friday.fabric_adapters

### `tests/test_fabric_helpers_social.py` (185 lines)

Covers: postiz_social and anythingllm_research: request shape, auth header, honest UNAVAILABLE, and that a write refuses without its required fields. Each test spins up one real (fake) HTTP server in a thread so the request that leaves the adapter is proven, not assumed.
Tests[12]: test_every_new_provider_is_pinned_and_registered, test_postiz_is_copyleft_and_isolated, test_anythingllm_open_reads_and_no_permission_needed, test_postiz_writes_are_gated_and_reads_are_open, test_postiz_absent_store_is_unavailable_with_the_reason, test_anythingllm_absent_store_is_unavailable_with_the_reason, test_postiz_integrations_sends_raw_key_no_bearer, test_postiz_schedule_posts_text_when_and_integrations, test_postiz_schedule_refuses_without_text_or_when, test_anythingllm_workspaces_sends_bearer_key, test_anythingllm_ask_posts_message_to_workspace_chat, test_anythingllm_ask_refuses_without_workspace_or_question
Exercises: friday, friday.fabric_adapters

### `tests/test_fabric_new_skills.py` (171 lines)

Covers: The security and diagram skill packs. security_skills is the one with teeth: 818 procedures, many offensive, so the question is not whether it loads but whether reading an attack procedure is gated. diagram_design is an ordinary skill and is here mostly to assert it does not accidentally grow a second file-read escape.
Tests[17]: test_security_declares_restricted_risk_and_a_scope_permission, test_reading_a_procedure_without_a_scope_is_refused, test_a_blank_scope_is_not_a_scope, test_a_procedure_read_with_a_scope_records_the_scope, test_catalogue_and_search_are_open_because_knowing_is_not_doing, test_search_reaches_defensive_skills_by_description, test_the_catalogue_carries_no_procedure_text, test_security_has_no_bulk_read_operation, test_security_is_pinned_to_the_audited_commit, test_security_uncloned_is_unavailable, test_diagram_is_a_presentation_skill_pinned_and_codeless, test_the_instructions_load_and_the_references_are_named, test_a_reference_outside_the_catalogue_is_refused, test_a_named_reference_loads, test_diagram_unknown_operation_is_named, test_the_new_providers_do_not_shadow_an_existing_capability, test_each_new_family_is_a_declared_fabric_family
Exercises: friday, friday.fabric_adapters

### `tests/test_fabric_open_design.py` (103 lines)

Covers: open-design as a Friday design-skill provider. The "Claude Design in Friday" request: a library of design skills, systems and templates that Friday reads and then builds from with its own model or Hermes. The assertions that matter: it executes no upstream code, it coexists with diagram_design in presentation, the catalogue is the read allowlist, and the per-template licence gate fails closed.
Tests[10]: test_it_is_a_presentation_skill_pinned_and_codeless, test_it_coexists_with_diagram_design, test_the_catalogue_offers_skills_systems_and_templates, test_a_design_request_routes_to_a_skill, test_a_named_skill_system_and_template_load, test_an_arbitrary_path_cannot_be_read, test_the_template_licence_gate_fails_closed, test_there_is_no_bulk_read_operation, test_an_uncloned_pack_is_unavailable, test_an_unknown_operation_is_named
Exercises: friday, friday.fabric_adapters

### `tests/test_fabric_prompt_master.py` (92 lines)

Covers: prompt-master as a Friday fabric skill. A single skill with references loaded on demand, in the `writing` family beside no_ai_slop. The assertions that matter: it executes no upstream code, it is pinned, its reference catalogue is the read allowlist, and it coexists with the other writing provider rather than replacing it.
Tests[8]: test_it_is_a_writing_skill_pinned_and_codeless, test_it_coexists_with_no_ai_slop_rather_than_replacing_it, test_the_instructions_load, test_the_references_are_named_and_loadable, test_a_reference_outside_the_catalogue_is_refused, test_reference_without_a_name_is_refused, test_an_unknown_operation_is_named, test_it_is_reachable_through_the_capability_bridge
Exercises: friday, friday.fabric_adapters, friday.tools

### `tests/test_fabric_science_skills.py` (201 lines)

Covers: The licence gate on scientific-agent-skills. Almost everything here is about four directories. The repository is MIT, and `skills/docx`, `skills/pdf`, `skills/pptx` and `skills/xlsx` are not: they carry Anthropic terms forbidding extraction, copying and derivative works. Reading one into a prompt is the extraction those terms prohibit, so the adapter must refuse - and must keep refusing when someone adds a fifth.
Tests[19]: test_the_anthropic_licensed_skills_are_blocked, test_a_blocked_skill_is_not_in_the_catalogue, test_reading_a_blocked_skill_is_refused_with_the_licence_reason, test_every_block_states_which_file_and_why, test_a_permissively_licensed_skill_is_still_offered, test_most_of_the_pack_survives_the_gate, test_an_unrecognised_licence_blocks_rather_than_allows, test_a_non_commercial_licence_is_blocked, test_a_source_available_licence_is_blocked, test_anthropic_terms_are_named_specifically_not_just_refused, test_recognised_permissive_licences_are_allowed, test_a_newly_restricted_skill_would_be_excluded_without_a_code_change, test_the_catalogue_is_names_and_descriptions_not_procedures, test_there_is_no_bulk_read_operation, test_search_narrows_a_hundred_and_fifty_skills_to_a_shortlist, test_search_without_a_query_is_refused, test_it_executes_no_upstream_code_and_is_pinned, test_an_uncloned_pack_is_unavailable_rather_than_a_broken_boot, test_an_unknown_operation_is_named
Exercises: friday, friday.fabric_adapters

### `tests/test_fabric_scrapling.py` (227 lines)

Covers: Scrapling as a parser, and the boundary that keeps it one. The interesting assertions here are negative. Scrapling can fetch pages and drive a browser; Friday's copy must do neither, because a second fetcher is a second egress path outside `netguard` and a second browser is the duplicate NON_NEGOTIABLE 11 forbids. Most of this file exists to make that boundary fail loudly if someone later adds `url=` convenience to the adapter.
Tests[19]: test_it_is_the_scraping_provider_and_is_pinned, test_it_costs_nothing_and_needs_no_model, test_it_holds_no_process, test_no_operation_accepts_a_url_to_fetch, test_every_operation_refuses_to_work_without_markup, test_scraplings_own_fetchers_are_not_installed, test_the_declared_extra_asks_only_for_the_core, test_named_fields_come_back_as_a_dict_of_lists, test_a_css_selector_returns_text_tag_and_attributes, test_xpath_is_available_as_well_as_css, test_an_unknown_selector_kind_is_refused_rather_than_guessed, test_finding_by_text_returns_the_matching_element, test_similar_elements_are_found_from_one_example, test_a_selector_that_matches_nothing_is_empty_not_an_error, test_results_are_capped_so_a_large_page_cannot_flood_the_context, test_the_relocation_database_lives_under_the_project_data_directory, test_relocation_is_off_unless_asked_for, test_a_missing_package_is_unavailable_not_an_import_error, test_an_unknown_operation_is_named
Exercises: friday, friday.fabric_adapters

### `tests/test_fabric_skillpacks.py` (180 lines)

Covers: The markdown-only providers, and the two rules that make them safe. They install nothing and start nothing, so most of what could go wrong here is about *how much* they hand a model and *which file* they are willing to read. Both are enforced in code and asserted here.
Tests[14]: test_a_skill_pack_executes_no_upstream_code, test_a_skill_pack_is_pinned, test_the_packs_cover_three_distinct_families, test_the_catalogue_is_names_only_and_far_cheaper_than_the_recipes, test_there_is_no_bulk_read_operation, test_a_single_file_is_capped, test_nothing_is_read_at_import, test_a_path_outside_the_catalogue_is_refused, test_an_absolute_path_is_refused, test_an_unknown_division_names_the_known_ones, test_a_pack_that_was_never_cloned_is_unavailable, test_a_cloned_pack_missing_its_entry_file_is_degraded, test_an_empty_placeholder_directory_is_not_cloned, test_the_real_packs_report_ready
Exercises: friday, friday.fabric_adapters

### `tests/test_face_gate.py` (86 lines)

Covers: The face gate: locked means locked, on the server, with an audit trail. Recognition happens in the browser; these tests exercise the part that matters for security -- matching and enforcement -- with synthetic descriptors.
Tests[4]: test_everything_is_locked_and_logged_until_a_face_matches, test_first_enrolment_is_open_then_matching_unlocks_and_lock_relocks, test_bad_descriptors_never_unlock, test_gate_can_be_switched_off_for_headless_use
Exercises: friday

### `tests/test_failure_fingerprint.py` (161 lines)

Covers: S4a: failure fingerprint + strategy-change guard. A retryable failure that keeps landing on the exact same fingerprint with no new hypothesis is not "still transient" - it is a loop. These tests drive the same engine used by the P0 gate (tests/test_objective_continuity.py) through that loop and assert it changes strategy, then gives up cleanly at BLOCKED instead of retrying forever.
Tests[5]: test_same_fingerprint_without_new_evidence_changes_strategy, test_new_evidence_allows_a_bounded_retry, test_strategy_budget_ends_in_blocked_not_a_loop, test_fingerprint_ignores_numbers_and_paths, test_iteration_budget_caps_attempts
Exercises: friday.continuous, friday.objectives, friday.store

### `tests/test_file_control.py` (52 lines)

Covers: No docstring; 1 test(s).
Tests[1]: test_files_delete_schema_and_original_run_nonce_contract
Exercises: friday, friday.fsjail, friday.policy, friday.store, friday.tools, friday.toolsets

### `tests/test_files.py` (126 lines)

Covers: No docstring; 8 test(s).
Tests[8]: test_recycle_requires_source_absence, test_recycle_os_failure_is_truthful, test_missing_file_and_directory_have_distinct_safe_results, test_permanent_user_delete_consumes_exact_original_run_confirmation, test_permanent_confirmation_refuses_retarget_expiry_and_reuse, test_friday_artifact_can_be_cleaned_directly_without_nonce, test_jail_denylist_outside_root_and_reparse_fail_before_confirmation, test_delete_tools_use_delete_policy_category
Exercises: friday, friday.fsjail, friday.policy, friday.toolsets

### `tests/test_files_recycle.py` (144 lines)

Covers: Deleting a file the way a person means it. `files.py` said for a long time that there was "deliberately no delete tool", and while the only thing on offer was `os.remove` that was the right call. A capability that destroys a file permanently, named after the word people use when they expect the Recycle Bin, is a trap with a friendly label. So this is a recycle and it is called one. The distinction it protects: recycle the boss can change their mind delete the boss cannot Fixtures here are create
Tests[7]: test_a_file_this_test_created_goes_to_the_recycle_bin, test_the_evidence_is_the_path_being_gone_not_the_call_returning, test_a_file_that_is_not_there_is_not_a_success, test_a_directory_is_refused_rather_than_emptied, test_a_path_outside_the_jail_is_refused, test_it_is_reachable_from_a_durable_objective, test_permanent_deletion_is_not_hiding_behind_this_name
Exercises: friday, friday.toolsets

### `tests/test_first_run.py` (87 lines)

Covers: First-run contract - adaptive wizard, no questionnaire prison, policy unification.
Tests[9]: test_fresh_profile_asks_all_ten, test_known_context_skips_questions, test_answers_persist_and_stop_reasking, test_permission_answers_reach_user_policy, test_restrictive_answers_stay_confirm, test_natural_adaptation_updates_later, test_deleted_contract_reasks, test_progress_detail_is_a_tenth_question, test_progress_detail_is_a_plain_preference_not_a_policy_domain
Exercises: friday, friday.user_policy

### `tests/test_forge.py` (280 lines)

Covers: Forge: the specification, the static gate, the scrub, and the escapes. The escape tests are the point of this file. A boundary nobody attacked is a boundary nobody has evidence for, so each one below is a real thing generated code could plausibly emit - reading the user's home directory, shelling out, walking `object.__subclasses__` back to the interpreter - and each must be refused for a stated reason. What is deliberately NOT asserted anywhere here: that a skill which defeats the static gate i
Tests[27]: test_a_spec_without_verification_criteria_is_refused, test_a_spec_without_declared_inputs_is_refused, test_process_access_is_refused_at_the_spec_level, test_environment_access_is_refused_at_the_spec_level, test_unscoped_network_is_refused_but_scoped_is_allowed, test_an_unknown_spec_field_is_refused_rather_than_ignored, test_a_spec_survives_a_round_trip, test_the_good_case_passes_cleanly, test_routes_out_of_the_reviewed_code_are_refused, test_the_constructs_that_make_static_reading_meaningless_are_refused, test_aliasing_a_forbidden_builtin_is_refused_even_uncalled, test_walking_back_into_the_interpreter_is_refused, test_an_undeclared_dependency_is_refused, test_a_declared_dependency_is_allowed, test_relative_imports_are_refused, test_a_skill_must_have_a_run_function, test_run_must_take_the_context_first, test_run_must_take_exactly_the_declared_inputs, test_code_that_does_not_parse_is_reported_as_such, test_no_credential_reaches_a_verification_process, test_the_environment_is_built_from_nothing_not_filtered, test_the_child_can_still_start, test_verification_does_not_reach_enabled_in_one_step, test_rejection_is_terminal, test_every_state_can_be_rejected…
Exercises: friday

### `tests/test_forge_registry.py` (202 lines)

Covers: Gate 5: passing is not promotion. The lifecycle exists because "its tests passed" and "this is switched on" are different claims, and a system that writes its own code and then enables it is a system that grows capabilities nobody chose.
Tests[15]: test_a_submitted_skill_is_only_a_candidate, test_a_candidate_carries_its_provenance_and_hash, test_risk_is_derived_from_the_spec_not_asserted_by_the_author, test_the_happy_path_takes_four_deliberate_steps, test_a_candidate_cannot_be_enabled_directly, test_a_verified_skill_still_cannot_be_enabled_without_registering, test_forge_may_not_switch_on_what_forge_wrote, test_enabling_without_a_scope_is_refused, test_a_skill_that_fails_its_own_cases_is_rejected, test_a_skill_the_static_gate_refuses_is_rejected, test_rejection_is_terminal, test_code_changed_after_submission_is_rejected_not_verified, test_every_transition_is_recorded_with_who_and_why, test_a_new_version_is_never_born_enabled, test_verifying_something_that_does_not_exist_fails_clearly
Exercises: friday, friday.store

### `tests/test_forge_runtime.py` (299 lines)

Covers: The runtime side, executed for real: skills that work, and skills that try. These call `verify()`, which starts an actual subprocess with an actual scrubbed environment and runs the code. Marked slow rather than live - there is no network and no credential involved, but each one pays process startup. The escape cases are the ones worth reading. Every one is something generated code could plausibly emit, and each must be refused with a reason - not merely fail to work.
Tests[16]: test_a_correct_skill_verifies, test_a_wrong_skill_fails_with_what_it_returned, test_a_skill_that_raises_is_reported_not_swallowed, test_the_context_is_usable, test_an_allowed_stdlib_import_works, test_a_skill_over_its_budget_fails, test_reading_the_users_home_directory_is_refused, test_shelling_out_is_refused, test_opening_a_socket_is_refused, test_the_subclasses_walk_is_refused, test_the_static_gate_runs_before_the_code_does, test_a_skill_cannot_see_a_credential_even_via_the_context, test_declaring_a_forbidden_module_as_a_dependency_does_not_unlock_it, test_the_filesystem_context_refuses_a_path_outside_its_scope, test_the_network_context_refuses_a_host_the_spec_did_not_name, test_a_skill_denied_the_network_cannot_fetch_at_all
Exercises: friday, friday.forge_runtime

### `tests/test_fsjail_reparse.py` (251 lines)

Covers: The jail against real reparse points, created on the real filesystem. An absolute root is necessary and not sufficient. A Windows junction is an alias to another directory - possibly on another volume - and a path under one is syntactically inside the jail while accessing something outside it. Junctions are the alias that matters most here because creating one needs **no administrator rights**, unlike a symlink, so anything that can write next to the workspace can attempt this. Measured on this 
Tests[15]: test_dotdot_escape_is_refused, test_an_absolute_path_outside_is_refused, test_a_real_file_inside_is_allowed, test_a_junction_pointing_out_of_the_jail_is_refused, test_detection_survives_the_python_312_upgrade, test_a_symlink_pointing_out_of_the_jail_is_refused, test_a_nested_junction_is_refused, test_a_path_that_does_not_exist_yet_under_a_junction_is_refused, test_a_junction_to_another_volume_is_refused, test_a_junction_pointing_back_inside_the_jail_is_still_allowed, test_a_jail_root_that_is_a_reparse_point_fails_closed, test_a_root_that_is_an_ordinary_directory_starts_normally, test_case_variations_reach_the_same_verdict, test_a_sibling_whose_name_starts_with_the_root_is_refused, test_forward_and_back_slashes_reach_the_same_verdict
Exercises: friday.fsjail

### `tests/test_golden_engineering_journey.py` (192 lines)

Covers: S8a: the golden engineering journey, end to end, on fakes only. One module, no live Hermes, no network: objective -> team -> hermes bundle -> injected verifier failure -> strategy change -> repair -> handoff -> terminal verification -> a capped provider cooling over -> restart recovery. Each step asserts store rows / returned dicts, never source text.
Tests[1]: test_golden_engineering_journey
Exercises: friday, friday.continuous, friday.executors, friday.executors.claude_code, friday.objectives, friday.store

### `tests/test_golden_suite.py` (227 lines)

Covers: Golden Objective harness (PRD v3.1 7.2 / 7.3) and the permission boundary it exposed (FR-060: an ASK-tier action inside a durable objective parks the run, records the exact action, and resumes only on an approval bound to that action).
Tests[13]: test_corpus_has_at_least_150_cases_in_the_prd_mix, test_corpus_rejects_a_case_without_acceptance, test_corpus_generator_is_idempotent, test_false_completion_is_detected_not_trusted, test_policy_axis_fails_when_a_refused_action_succeeded, test_latency_and_model_budget_axes, test_report_metrics_and_gates, test_provenance_block_has_what_7_3_requires, test_a_real_case_runs_through_the_engine_with_loopback_web, test_ask_tier_action_parks_the_objective_and_records_the_exact_action, test_resume_needs_an_approval_bound_to_that_action, test_after_approval_the_engine_performs_exactly_that_action_once, test_a_forged_approved_by_argument_grants_nothing
Exercises: friday, friday.continuous, friday.store, friday.toolsets

### `tests/test_governor.py` (257 lines)

Covers: The resource governor (PRD v3.1 FR-056, FR-013, NFR-P09). Pressure is assessed from injected samples so every level is exercised deterministically; the live-machine sampler is exercised once for shape. The dispatch seams are proven with the REAL supervisor against the scripted gateway: a worker under a saturated governor is refused before any session is created, and a finished run releases its lease.
Tests[16]: test_assess_levels_are_monotonic_in_cpu_and_ram, test_assess_reports_every_reason_and_the_worst_level_wins, test_live_sampler_has_the_shape_and_never_guesses, test_worker_cap_is_two_by_default_then_queue_then_shed, test_parallel_justification_lifts_the_cap_by_one_for_that_objective, test_high_pressure_reduces_concurrency_to_one, test_critical_pressure_sheds_all_new_work, test_optional_work_is_shed_first, test_browser_cap_and_high_pressure_serialise_browsers, test_status_and_banner_explain_reduced_concurrency, test_sample_failure_degrades_to_elevated_not_a_crash, test_unknown_kind_is_a_programming_error, test_control_plane_reads_stay_responsive_under_pressure, test_hermes_delegate_is_refused_under_a_saturated_governor, test_hermes_run_holds_a_lease_and_releases_it_on_completion, test_hermes_delegate_tool_reports_refusal_honestly
Exercises: friday, friday.tools

### `tests/test_handoff.py` (83 lines)

Covers: S4b: a delegated task hands back structured data, not just a spoken sentence. Before this slice `friday/handoff.py` did not exist and `hermes_work_runs` had no `handoff` column.
Tests[5]: test_handoff_from_work_run_has_every_field, test_handoff_never_invents_files_or_tests, test_secret_shaped_summary_is_refused, test_handoff_is_stored_on_the_work_run_and_spoken, test_on_terminal_stays_idempotent
Exercises: friday, friday.handoff, friday.toolsets

### `tests/test_hardware.py` (199 lines)

Covers: What this machine physically is. Batch 2A, and a build rather than a port: the donor's `computer_settings.py` turned out to be 91 pyautogui calls, and a keystroke cannot satisfy the proof-of-work contract because it produces no observation. Everything here reads real state and reports what it found. These run against the actual machine, because that is the only thing they are for. Where the machine's answer cannot be predicted - a laptop or a desktop, one screen or three - the test asserts the *
Tests[12]: test_it_answers_and_says_how_it_knows, test_a_machine_with_no_battery_is_an_answer_not_a_failure, test_the_battery_sentinels_are_not_reported_as_seconds, test_disks_reports_every_volume_not_the_one_we_started_in, test_an_unreadable_volume_is_listed_rather_than_hidden, test_nearly_full_volumes_are_named, test_displays_finds_at_least_the_screen_this_is_running_on, test_network_never_reports_a_mac_address, test_network_separates_existing_from_connected, test_every_one_is_declared_to_the_policy_engine, test_every_one_of_these_is_a_read, test_nothing_here_presses_a_key
Exercises: friday, friday.policy, friday.toolsets

### `tests/test_health_recovery_wiring.py` (153 lines)

Covers: V3 Phase 1A/1B — hermes_health.Report wired into executor recovery. The RC1.1 handoff left the health module decided-but-unconsulted: a CONNECTIVITY refusal was retried on a blind timer without ever asking WHICH layer failed. These gates prove the executor consults the report, records the diagnosis durably, drives the layer-appropriate repair, and refuses to restart anything while live work exists.
Tests[4]: test_connectivity_failure_consults_the_report_and_repairs_the_layer, test_dead_gateway_with_live_work_reconciles_before_restart, test_failed_recovery_still_retries_and_never_asks_the_user, test_executor_without_probe_keeps_the_plain_retry_contract
Exercises: friday, friday.continuous, friday.objectives, friday.store

### `tests/test_health_serving.py` (87 lines)

Covers: A listening port is not a running server. The difference cost a live gate. A previous run's MCP server had been killed but its socket lingered in CLOSE_WAIT, so a TCP connect still succeeded; the gate concluded the server was up, every agent session started with "no MCP tools found", and the run reported thirteen perfectly reachable capabilities as unreachable. Nothing looked wrong - it looked like a routing regression.
Tests[4]: test_a_socket_that_accepts_but_never_answers_is_not_serving, test_a_server_that_answers_at_all_is_serving, test_nothing_listening_is_not_serving, test_waiting_gives_up_rather_than_hanging_forever
Exercises: friday

### `tests/test_hermes_bridge.py` (370 lines)

Covers: The Friday↔Hermes bridge, proven against a scripted gateway. Every test here runs the REAL supervisor - real subprocess, real stdio JSON-RPC, real reader thread - against `fake_hermes_gateway.py`, which speaks the same wire protocol as Hermes's `tui_gateway.entry`. What is faked is the model, not the transport; the protocol handling being tested is exactly what production uses. The four journeys the bridge must survive: delegate → bounded bundle in, structured completion out steer → mid-run corr
Tests[25]: test_bundle_renders_only_what_it_has, test_bundle_measure_flags_oversize, test_work_run_log_round_trip, test_work_run_log_refuses_unknown_fields, test_delegate_completes_with_structured_result, test_events_are_mapped_not_parsed, test_steer_reaches_the_running_session, test_interrupt_marks_partial, test_grounded_question_is_answered_automatically, test_unknown_question_is_parked_not_guessed, test_crash_is_detected_and_restart_recovers, test_health_answers_with_a_real_rpc, test_request_without_process_refuses_loudly, test_missing_profile_refuses_at_start, test_profile_home_env_override_wins, test_gateway_env_carries_the_profile_home, test_tool_with_progress_is_running_not_stalled, test_tool_class_ceilings_resolve_prefixes, test_dead_gateway_is_named_not_inferred, test_terminal_run_is_idle, test_recover_interrupts_a_stalled_tool, test_a_waited_delegate_leaves_nothing_behind, test_unread_finished_sessions_are_bounded, test_a_crash_releases_the_dead_sessions_memory, test_stop_closes_the_pipes_and_joins_the_readers
Exercises: friday

### `tests/test_hermes_delivery.py` (114 lines)

Covers: The completion-delivery broker's durable half. Every guarantee the delivery loop relies on is a database property, so every test here runs against the real sqlite layer: exactly-once creation, atomic claims, release-on-failure, startup sweep, and old-run backfill.
Tests[10]: test_delivery_created_exactly_once, test_claim_is_atomic_and_single_winner, test_release_puts_a_failed_attempt_back, test_mark_delivered_is_terminal, test_sweep_creates_deliveries_for_orphan_terminal_runs, test_sweep_backfills_ancient_runs_silently, test_working_runs_are_not_swept, test_render_completion_success_carries_findings, test_render_completion_failure_names_the_failure, test_supervisor_terminal_event_creates_delivery
Exercises: friday

### `tests/test_hermes_engine.py` (160 lines)

Covers: Hermes as the single execution engine: shared memory, token-aware depth, and the router preferring it over the coding-CLI fallbacks.
Tests[12]: test_bundle_with_memory_pulls_from_the_one_memory_stack, test_memory_that_cannot_be_read_costs_nothing, test_memory_is_bounded_not_the_store, test_delegate_sends_effort_and_memory_to_the_gateway, test_plan_delegation_maps_tier_to_effort, test_caller_pins_win_and_are_recorded, test_every_tier_has_a_valid_hermes_effort, test_hermes_is_the_default_and_claude_the_fallback, test_without_hermes_claude_is_chosen_and_labelled, test_build_constructs_the_hermes_executor, test_hermes_executor_returns_an_honest_envelope, test_hermes_executor_reports_unavailable_not_a_crash
Exercises: friday, friday.executors.claude_code, friday.executors.hermes

### `tests/test_hermes_health_layers.py` (91 lines)

Covers: RC1.1 — Hermes connectivity must be diagnosed by LAYER, not by one bool. The live regression: `hermes gateway status` reported a healthy Windows Scheduled Task with live PIDs, and Friday still told the boss "Hermes gateway is currently disconnected" and asked whether to continue. One stale layer was reported as the whole gateway being dead, and a recoverable internal fault was handed back to the user as a question. These gates assert the product behavior: name the failed layer, and let the objec
Tests[8]: test_layers_are_ordered_from_process_outwards, test_all_layers_up_is_healthy_and_needs_no_recovery, test_a_stale_bridge_is_not_a_dead_gateway, test_only_a_dead_process_may_be_restarted, test_restart_is_refused_while_work_is_in_flight, test_no_layer_failure_ever_blocks_read_only_telemetry, test_summary_names_the_layer_and_never_asks_the_user, test_each_layer_maps_to_its_own_repair
Exercises: friday

### `tests/test_hermes_health_live_probe.py` (87 lines)

Covers: The live Hermes probe must not mistake an HTTP error for a dead server. Friday's MCP server is FastMCP over SSE and has no route at "/", so a perfectly healthy one answers `GET /` with 404. The probe used to catch that as "the MCP server is gone", which failed `mcp_server_alive`, which failed `hermes_bridge_ready`, which stopped Friday delegating to a Hermes that was running the whole time. An HTTP status is proof of life. Only a refused connection or a timeout is not.
Tests[4]: test_any_http_answer_means_the_mcp_server_is_alive, test_nothing_listening_still_reads_as_dead, test_a_reachable_server_still_defers_the_bridge_to_a_real_round_trip, test_http_error_is_not_swallowed_by_the_generic_handler
Exercises: friday

### `tests/test_hermes_memory_writeback.py` (111 lines)

Covers: S2: a terminal Hermes run must reach Friday's SHARED memory, so (a) her next spoken turn can answer "what did Hermes just do?" and (b) the next TaskBundle.with_memory() for a follow-up goal already knows the result. Before this slice nothing writes it: hermes_bridge.py only READS memory_stack at the TaskBundle.with_memory() call site, and memory_stack.log_result() has zero production callers.
Tests[6]: test_terminal_run_is_written_to_shared_memory, test_outcome_visible_to_next_hermes_bundle, test_outcome_visible_to_voice_brain_context, test_outcome_with_secret_shape_is_refused, test_write_is_idempotent_per_work_run, test_one_complete_run_through_the_fake_gateway_reaches_both_readers
Exercises: friday, friday.toolsets, friday.toolsets.memory

### `tests/test_hermes_team.py` (145 lines)

Covers: `friday/hermes_team.py` against a fake `hermes` CLI (`fake_hermes_cli.py`): records argv, answers canned JSON. No real Hermes process involved.
Tests[9]: test_plan_team_small_goal_is_empty, test_plan_team_orders_by_profile_not_role_order, test_ensure_profile_idempotent, test_ensure_profile_creates_when_missing, test_submit_creates_linked_tasks_with_idempotency_and_model, test_submit_refuses_kanban_worker_origin, test_submit_falls_back_on_kanban_error, test_poll_parses_show_json, test_gateway_cap_stops_lru
Exercises: friday

### `tests/test_injection_pages.py` (230 lines)

Covers: A-036: prompt-injection pages (PRD Requirement 26). WHEN external content contains instructions to ignore policy, reveal secrets, change tools, or execute unrelated actions, THEN FRIDAY SHALL treat those instructions as untrusted content. The attack that matters is not "the model says something rude". It is the tool loop in `voice_brain.reply()`: the boss asks a harmless question, the model reads a page, and the page's text becomes the model's next tool call. Before this suite (2026-09-05) that 
Tests[5]: test_a_page_cannot_turn_a_question_into_a_write, test_the_same_call_with_his_own_words_goes_through, test_reads_are_never_gated_by_the_turn, test_every_own_write_family_is_behind_the_gate, test_the_pages_carry_no_real_secret_and_are_the_documented_set
Exercises: friday, friday.tools.hermes_control, friday.toolsets

### `tests/test_invariants.py` (749 lines)

Covers: A-048: the system invariants, attacked (PRD §16 readiness gate; §20 DoD). Not unit tests of features - each test is an ATTACK on a property the architecture promises, at the weakest entry point it has, with the expected outcome being "physically impossible" rather than "handled". Where an invariant has a driver-level guard, the attack goes UNDER the driver: at the store, at the gateway, at the adapter - because a guard on one caller is a promise about one caller. completion no COMPLETED without 
Tests[28]: test_completion_a_run_cannot_be_completed_without_passing_evidence, test_completion_other_terminal_states_need_no_evidence, test_budget_exhausted_spend_makes_a_provider_call_impossible, test_budget_an_objective_without_a_ceiling_is_not_refused, test_provider_a_foreign_model_never_reaches_a_provider, test_provider_every_candidate_route_carries_a_model_the_provider_owns, test_health_credentials_and_transport_success_alone_are_not_healthy, test_health_a_transport_success_with_zero_output_is_not_healthy, test_authority_no_form_of_non_approval_becomes_authorization, test_authority_a_timed_out_approval_leaves_zero_mutation, test_trust_every_alias_of_a_kernel_path_is_refused, test_trust_ordinary_paths_are_not_kernel, test_trust_a_symlink_into_the_kernel_is_refused_by_the_real_path, test_ownership_two_live_owners_are_impossible_and_a_stale_lease_recovers, test_ownership_a_terminal_run_cannot_be_leased, test_cancellation_nothing_starts_after_cancel_is_accepted, test_memory_read_material_cannot_write_a_rule_directly, test_memory_the_promotion_gate_is_the_only_door_and_it_wants_evidence, test_memory_verified_provenance_still_writes, test_scheduler_one_execution_key_has_one_side_effect_across_a_crash, test_scheduler_the_claim_is_a_primary_key_two_processes_cannot_both_win, test_replay_a_consumed_nonce_stays_consumed_across_a_restart, test_audit_a_completion_claim_the_run_does_not_back_is_refused, test_kernel_a_provider_that_explodes_at_import_cannot_take_the_registry_down, test_kernel_a_provider_that_fails_to_start_is_unavailable_not_an_exception…
Exercises: friday, friday.continuous, friday.store, friday.toolsets

### `tests/test_jarvis_screen.py` (287 lines)

Covers: The Jarvis screen powers: pointing, and the gated takeover. Every test here has a known failure mode written next to it, because the point of these is not coverage - it is that the specific way this feature could hurt someone stays impossible.
Tests[22]: test_takeover_is_confirm_and_full_autonomy_does_not_grant_it, test_stopping_is_never_gated, test_pointing_is_auto_and_reads_only, test_credential_entry_is_denied_and_cannot_be_granted, test_a_page_cannot_ask_for_a_takeover, test_danger_gate_refuses_whole_categories, test_danger_gate_is_independent_of_autonomy, test_ordinary_work_is_not_refused, test_sending_needs_the_exact_text_first, test_a_dangerous_task_is_refused_before_the_screen_is_touched, test_stop_sets_the_flag_and_the_next_step_refuses, test_a_step_without_an_approved_plan_is_honest, test_an_unattended_run_is_refused_rather_than_left_pending, test_low_confidence_never_draws, test_not_visible_says_so_instead_of_inventing_a_place, test_a_frame_remembers_which_monitor_it_came_from, test_the_arrow_tip_is_at_the_requested_fraction, test_locate_rejects_a_coordinate_that_is_not_a_fraction, test_a_locked_gate_refuses_the_speech_websocket, test_an_open_gate_accepts_the_socket_and_is_honest_about_a_missing_key, test_typing_refuses_when_the_focus_moved_since_the_last_step, test_every_vision_call_carries_a_deadline
Exercises: friday, friday.config, friday.policy, friday.toolsets

### `tests/test_journey_e.py` (402 lines)

Covers: Journey E on the host boundary: "build this" to a verified, gated result. The chain, and the acceptance cases from the work order: readiness -> DevelopmentRun -> HOST_WORKTREE inside the Job Object boundary -> executor -> a question answered from project memory -> tests run inside the boundary -> patch validated against the base commit -> host untouched until promotion The coding agent is a script. That is a real limit, stated: what this proves is that the plumbing carries work end to end and th
Tests[13]: test_a_ready_project_gets_built_verified_and_gated, test_a_question_the_project_settles_never_reaches_him, test_an_unresolved_material_question_waits_for_him, test_a_blocking_question_stops_the_run_before_it_starts, test_an_unrelated_blocking_question_does_not_stop_this_work, test_assumptions_reach_the_agent_labelled_as_assumptions, test_an_executor_that_fails_promotes_nothing, test_failing_tests_promote_nothing, test_the_host_is_untouched_until_promotion, test_a_commit_arriving_mid_run_refuses_the_promotion, test_verification_runs_with_the_network_off, test_no_provider_key_crosses_into_the_work, test_the_run_can_explain_itself_end_to_end
Exercises: friday, friday.executors, friday.executors.claude_code, friday.store, friday.toolsets, friday.toolsets.memory

### `tests/test_launcher.py` (255 lines) [UNTRACKED]

Covers: The packaged launcher (friday/launcher.py), tested from source. FRIDAY.exe is three roles supervised by one executable, plus a shim that serves Friday's own `sys.executable -c/-m/script.py` re-invocations because a frozen build has no python.exe to hand. Everything with a decision in it is a pure function here; the process supervision itself is exercised against the built artifact (docs/build/FRIDAY_EXE_VALIDATION.md), not mocked.
Tests[22]: test_bundled_paths_map_to_module_names, test_paths_outside_the_bundle_or_not_python_are_not_modules, test_shim_runs_dash_c_and_skips_interpreter_flags, test_shim_runs_a_script_on_disk_with_its_arguments, test_shim_runs_a_module_by_name, test_shim_serves_a_bundled_path_that_does_not_exist_on_disk, test_shim_leaves_launcher_arguments_alone, test_shim_reports_a_script_it_cannot_find, test_role_commands_target_this_same_runtime, test_state_directories_are_created_under_the_root, test_the_launcher_defers_env_and_state_decisions_to_config, test_frozen_stop_matches_only_this_executable, test_source_stop_matches_only_launcher_roles, test_the_verdict_is_the_worst_check, test_doctor_bootstraps_the_database_and_names_what_is_missing, test_provider_credentials_are_keys_not_settings_with_defaults, test_the_console_hold_is_a_decision_not_a_crash, test_doctor_never_prints_a_secret_value, test_version_answers_without_starting_anything, test_a_role_needs_a_name, test_an_unknown_role_is_refused, test_exclusive_modes_cannot_be_combined
Exercises: friday, friday.config

### `tests/test_launcher_doctor.py` (64 lines) [UNTRACKED]

Covers: The doctor's later sections (skills, voice, security, Hermes version) and the debug variant of the launcher.
Tests[5]: test_doctor_reports_skills_voice_and_security_sections, test_skills_check_degrades_when_skills_are_on_disk_but_unregistered, test_debug_mode_is_read_from_the_executable_name_or_the_environment, test_hermes_version_never_raises, test_security_check_flags_credentials_inside_a_packaged_application
Exercises: friday, friday.config

### `tests/test_lazy_resolution.py` (106 lines)

Covers: Finding out where a capability lives must not load what implements it. The regression this exists for was mine. Resolution originally imported all 21 toolset modules and inspected them with `getmembers`, which is the obvious way to find every run-taking function and cost **1.6 seconds** on the first call - paid by anything that touched the runtime, every test process included. Before that layer existed `build_dispatch` imported three modules; resolution quietly turned that into all of them, and 
Tests[5]: test_resolution_imports_nothing_heavy, test_resolution_imports_no_toolset_module, test_resolution_is_not_pathologically_slow, test_the_fast_answer_is_still_the_right_answer, test_loading_a_capability_is_what_imports_its_dependencies
Exercises: friday

### `tests/test_live_pass_fixes.py` (106 lines)

Covers: Four defects found by the 2026-09-04 21:15 live pass through the control room: the browser digest re-spoke the same step every poll; the model rewrote the boss's path ('friday/desk.py' -> '/desk.py') in the Hermes goal; 'next:' showed a raw tool name or 'wrapping up' before the first tool; a content search from '.' read every file under .venv / node_modules / third_party.
Tests[5]: test_browser_digest_keeps_the_room_cadence, test_the_boss_path_reaches_hermes_verbatim, test_run_hermes_delegates_the_literal_path, test_next_step_is_described_or_first_tool, test_search_skips_dependency_trees_and_caps
Exercises: friday, friday.tools, friday.tools.hermes_control

### `tests/test_manifest.py` (237 lines)

Covers: Capability Fabric manifest and health (PRD v3.1 FR-023, FR-024, FR-025, FR-026, FR-027, FR-028). FR-023 registry queryable; no unregistered capability executes FR-024 nine types; a SKILL is never presented as executable FR-025 summaries first, one full schema on demand; context bounded FR-026 READY / DEGRADED / UNAVAILABLE / FAILED / DISABLED; an unavailable provider is never reported as executed FR-027 version/commit/license recorded before activation; exportable FR-028 fabric calls pass throug
Tests[13]: test_manifest_covers_every_provider_tool_and_executor_with_prd_fields, test_types_are_distinguished_and_skills_are_never_executable, test_unregistered_capability_cannot_execute, test_native_tools_carry_their_risk_tier_and_dangerous_actions, test_summary_is_bounded_and_full_schema_is_per_id, test_router_exposes_summaries_first_and_schemas_on_selection, test_repeated_raises_turn_a_ready_provider_into_failed, test_one_raise_does_not_fail_the_provider, test_unavailable_provider_is_never_reported_as_executed, test_family_report_ranks_failed_below_degraded, test_export_records_pin_license_and_review_for_every_upstream, test_fabric_calls_pass_the_permission_gate_before_activation, test_manifest_tool_is_reachable_from_the_runtime
Exercises: friday, friday.fabric, friday.toolsets

### `tests/test_memory_contract.py` (298 lines)

Covers: Shared scoped memory (PRD v3.1 FR-015..FR-020). FR-015 one logical memory service: a fact committed by a worker (Hermes outcome path, run_id attribution) is retrievable by another reader within scope FR-016 memory classes with lifecycle (working/session die with their objective/session; durable classes survive) FR-017 scoped retrieval: a project's memory never leaks into another project's context, at the store AND at the prompt compiler FR-018 provenance: source, source_ref, timestamps, confiden
Tests[12]: test_every_prd_memory_class_is_storable_with_a_lifecycle, test_working_memory_dies_with_its_objective_and_durable_memory_does_not, test_a_projects_memory_never_leaks_into_another_project, test_the_context_compiler_excludes_other_projects_before_the_prompt, test_correction_supersedes_with_a_link_and_the_old_value_is_not_current, test_provenance_surfaces_open_contradictions, test_retrieval_stamps_last_retrieved_at, test_a_fact_committed_by_one_worker_is_readable_by_another_in_scope, test_toolset_remember_provenance_and_export_round_trip, test_context_compiler_reports_budget_and_never_dumps_the_store, test_the_graph_is_rebuilt_only_when_memory_changes, test_the_graph_stays_bounded_and_fast_on_a_large_memory
Exercises: friday, friday.store, friday.toolsets, friday.ui_server

### `tests/test_memory_promotion.py` (156 lines)

Covers: S7: the gate between an observed candidate and Friday's canonical memory.
Tests[6]: test_promotion_rejects_unverified_and_secret_shaped_candidates, test_duplicate_is_not_written_twice, test_contradiction_supersedes_not_overwrites, test_procedure_becomes_a_skill_candidate_not_a_fact, test_handoff_candidates_flow_through_promotion, test_autolearn_uses_the_gate
Exercises: friday, friday.handoff, friday.memory_promotion, friday.store

### `tests/test_memory_provenance.py` (332 lines)

Covers: How often, and from where. Confidence answers "how sure", and on its own it cannot tell a fact stated five times from one mentioned once at high conviction. Two columns close that: `evidence_count` and `last_confirmed`, plus `observation_id` so every belief traces back to the raw episode it came from. The migration matters as much as the columns. Databases were already in use, and CREATE TABLE IF NOT EXISTS does nothing to a table that exists - losing what Friday has learned to gain a column is 
Tests[22]: test_a_new_memory_has_been_seen_once, test_a_new_memory_is_confirmed_when_it_is_created, test_a_memory_can_point_at_the_episode_it_came_from, test_evidence_count_below_one_is_refused, test_hearing_the_same_thing_again_raises_the_count, test_the_count_survives_the_new_row_reinforcement_writes, test_a_corrected_fact_starts_counting_again, test_reinforcement_moves_last_confirmed_forward, test_the_reason_says_how_many_times, test_something_heard_once_is_marked_as_heard_once, test_something_confirmed_repeatedly_says_so, test_a_stated_fact_needs_no_hedge, test_the_profile_exposes_the_provenance, test_low_confidence_never_reaches_the_profile, test_a_rejected_candidate_is_still_on_record, test_a_guess_never_overwrites_something_he_stated, test_a_passing_remark_does_not_become_permanent, test_something_worth_keeping_is_kept, test_an_existing_database_keeps_what_it_learned, test_old_rows_get_sensible_provenance, test_migrating_twice_is_harmless, test_a_migrated_database_can_still_be_written_to
Exercises: friday, friday.store

### `tests/test_model_capabilities.py` (89 lines)

Covers: §22: capability metadata must be declared, never inferred from model IDs. The two tests that matter are the sync tests: every role-reachable model has a declaration, and no declaration outlives the model it describes.
Tests[9]: test_every_role_reachable_model_has_declared_capabilities, test_no_declaration_describes_an_uninstalled_model, test_for_role_resolves_on_every_backend, test_undeclared_model_raises_rather_than_guessing, test_capabilities_are_not_inferred_from_the_name, test_max_context_is_absent_rather_than_guessed, test_supports_reads_declared_fields, test_invalid_declaration_is_rejected, test_preview_channel_models_are_flagged_unknown_health
Exercises: friday, friday.providers

### `tests/test_model_gateway.py` (752 lines)

Covers: Hermes MODEL_GATEWAY (PRD v3.1 §4.9): proven against a scripted worker. Every test runs the REAL gateway - real subprocess, real JSON-lines transport, real budget/growth/failover/telemetry logic - against `fake_model_gateway_worker.py`, which speaks the worker protocol. What is faked is the provider, not the gateway. FR-069/079 inference-only: one bounded request, one reply, no loop FR-070 envelope in, envelope out, Friday owns the objective id FR-071/072 provider inventory is queried, route kin
Tests[39]: test_every_task_class_has_a_budget_and_a_default_tier, test_budgets_grow_with_class, test_compile_context_is_bounded_and_ordered, test_infer_returns_envelope_and_records_telemetry, test_infer_sends_only_the_compiled_package, test_provider_inventory_distinguishes_route_kinds, test_preferred_tier_is_honoured, test_over_budget_context_is_refused_without_escalation, test_growth_guard_stops_geometric_context_growth, test_growth_guard_stops_repeated_identical_context, test_growth_guard_enforces_objective_ceiling, test_gateway_refuses_before_budget_exhaustion, test_failover_is_bounded_and_keeps_the_objective, test_failover_disabled_reports_entitlement_truthfully, test_all_routes_failing_is_a_failed_result_not_an_exception, test_denylist_and_allowlist_filter_routes, test_a_pinned_provider_outside_the_tier_table_gets_its_own_default, test_a_provider_with_no_catalog_default_is_no_route, test_a_reply_truncated_before_any_content_is_retried_wider_then_ok, test_an_empty_reply_with_finish_stop_fails_the_route_and_fails_over, test_an_empty_reply_without_failover_is_a_failed_result, test_local_only_privacy_policy_refuses_cloud_routes, test_secret_shaped_content_is_refused_and_never_sent, test_customer_pii_is_sent_redacted, test_secret_shaped_content_still_reaches_a_local_route…
Exercises: friday

### `tests/test_model_selection.py` (45 lines)

Covers: Model selection from the spoken requirement, validated against the cache.
Tests[4]: test_a_named_requirement_picks_the_tier_but_not_the_level, test_an_explicit_model_beats_the_requirement, test_unknown_tier_models_fall_back_to_the_profile_default, test_the_cache_reader_understands_both_shapes
Exercises: friday

### `tests/test_modification.py` (234 lines)

Covers: Changing a run that is already under way, and refusing to change what is not. The behaviour these gates pin down is mostly about what modification will *not* do. Editing future work is easy; the failures worth guarding are the three that look like helpfulness: rewriting history "actually don't open Paint" after Paint opened. The task stays SUCCEEDED with its evidence. Saying it was skipped would put a false statement in the trace. losing a race the boss edits a task in the same moment the execut
Tests[16]: test_skipping_a_pending_task, test_independent_work_is_untouched_by_a_skip, test_a_skip_that_matches_nothing_says_so, test_amending_a_pending_argument, test_amending_preserves_completed_evidence, test_appending_a_task_waits_for_the_outstanding_work, test_appending_only_waits_for_work_that_is_still_outstanding, test_appending_to_a_finished_run_is_a_new_objective, test_a_completed_side_effect_is_not_retracted, test_every_edit_advances_the_graph_version, test_the_version_survives_a_restart, test_an_edit_records_what_was_said_and_both_states, test_a_running_task_cannot_be_amended, test_a_running_task_cannot_be_skipped, test_the_conditional_update_is_what_decides_the_race, test_asking_how_far_along_changes_nothing
Exercises: friday, friday.arbiter, friday.store

### `tests/test_music_preflight.py` (82 lines)

Covers: Check the player before doing the work that needs it. Measured on a machine with no ffmpeg installed: `music_play("daft punk")` took **39.3 seconds** to answer "ffplay not found on PATH". A YouTube search and a stream resolve, both thrown away, to discover something knowable in a microsecond - and the boss waited forty seconds for an answer that existed before he finished speaking. The agentability gate is what surfaced it. Routing was correct throughout: the model reached `music_play` every tim
Tests[5]: test_a_missing_player_is_reported_before_the_search, test_the_message_says_what_to_do_about_it, test_a_mood_request_fails_the_same_way, test_an_override_pointing_nowhere_is_refused, test_a_missing_player_never_reports_success
Exercises: friday, friday.toolsets

### `tests/test_native_bindings.py` (178 lines)

Covers: Every Win32 call declared, and declared correctly. `ctypes` assumes a C `int` for anything undeclared, and on 64-bit Windows a pointer-sized handle does not fit in one. The failure is silent: an HWND is truncated, `GetWindowThreadProcessId` reports the wrong owner, and a function that enumerates windows returns nothing for a window plainly on the screen. That happened, and it happened because the prototype was written inline at the call site where nobody was thinking about widths. This walks the
Tests[11]: test_something_is_actually_bound, test_every_binding_declares_its_prototype, test_every_handle_argument_is_pointer_sized, test_every_handle_returning_function_returns_pointer_sized, test_the_functions_that_matter_are_all_here, test_initiate_shutdown_returns_an_error_code_not_a_boolean, test_the_power_structs_are_the_size_windows_expects, test_enumeration_finds_real_windows_with_real_owners, test_owning_pids_are_plausible_process_ids, test_a_window_title_can_be_read, test_the_force_flags_exist_but_nothing_defaults_to_them
Exercises: friday.platform

### `tests/test_netguard.py` (296 lines)

Covers: The outbound boundary: what is refused, and when it is checked. The distinction the whole module exists for: check() may say VALID_BUT_UNRESOLVED. A catalogue must not fail ingestion because DNS blinked. fetch() resolves again, immediately, and connects to the address it just validated. It never accepts UNRESOLVED, and never follows a redirect it has not put through the same checks.
Tests[18]: test_non_public_addresses_are_refused, test_ordinary_public_addresses_are_allowed, test_the_metadata_endpoint_is_caught_in_its_ipv6_spelling, test_mapped_and_6to4_forms_of_local_addresses_are_refused, test_urls_that_are_refused_outright, test_an_ordinary_url_resolves_and_is_public, test_an_unresolvable_host_is_valid_but_unresolved_at_validation_time, test_the_same_url_is_refused_when_resolution_is_required, test_a_private_address_can_be_allowed_deliberately, test_allowing_private_does_not_also_allow_loopback_or_metadata, test_fetch_refuses_a_url_that_check_refuses, test_fetch_refuses_an_unresolvable_host_rather_than_trying, test_fetch_does_not_follow_redirects_automatically, test_the_redirect_chain_is_capped, test_the_request_is_aimed_at_the_validated_address_not_the_name, test_an_ipv6_address_is_bracketed_when_pinned, test_a_nonstandard_port_survives_pinning, test_a_real_fetch_through_the_gate_works
Exercises: friday

### `tests/test_objective_budget.py` (210 lines)

Covers: The objective budget is enforced from recorded spend (audit A-022). Every check here drives the real ContinuousTaskExecutor against a real Store; the "spend" is put on record the way production records it (GatewayTelemetry rows, task attempts, strategy changes, created_at) and the engine is expected to PAUSE the run with a blocker naming the dimension - never to trim work silently and never to guess.
Tests[8]: test_measure_reads_only_durable_records, test_a_run_over_its_token_ceiling_is_paused_before_the_next_call, test_a_run_past_its_wall_clock_budget_is_paused, test_worker_delegations_are_capped_per_class, test_within_budget_runs_are_untouched, test_a_missing_ledger_is_recorded_not_a_free_pass_and_not_a_block, test_every_class_lets_one_task_exhaust_its_own_strategy_budget, test_a_stuck_single_task_concludes_blocked_before_the_replans_cap
Exercises: friday, friday.continuous, friday.model_gateway, friday.objectives, friday.store

### `tests/test_objective_continuity.py` (251 lines)

Covers: P0 gate tests: one user message must drive a multi-step objective to COMPLETED without a single "Continue". These tests are written BEFORE the engine exists. The harness sends exactly one user message and then stays silent - no helper may secretly send continue/resume/go-on/next. Progression must come from the ContinuousTaskExecutor, the lease, the wake schedule and the watchdog alone.
Tests[4]: test_single_message_runs_to_completed, test_tool_boundary_schedules_continuation, test_no_user_message_ever_sent_to_capabilities, test_task_status_vocabulary_is_stable
Exercises: friday.continuous, friday.objectives, friday.store

### `tests/test_objective_control_plane.py` (269 lines)

Covers: Test A: the objectives control plane. The toolset entry points (objective_start/status/list/pause/resume/cancel/ history) are the contract the MCP adapter and the CLI both call. What they must guarantee: * one active run at a time; a second start is refused, replace=true cancels the old run first, with a reason trail * pause puts the run into a legitimate wait (no next_wake); resume wakes it; cancel interrupts every unfinished task and reaches CANCELLED - all lease-free, because stopping must wi
Tests[12]: test_start_compiles_and_persists, test_start_refuses_second_active_run, test_start_rejects_empty_and_bad_explicit_tasks, test_pause_resume_cycle, test_pause_and_resume_are_lease_free_and_ledgered, test_resume_without_run_id_targets_most_recent_paused, test_cancel_interrupts_unfinished_tasks, test_cancel_of_terminal_run_is_refused, test_status_list_history, test_status_without_active_run_is_honest, test_unmapped_clause_is_failed_at_compile_and_never_dispatched, test_dependents_of_unmapped_are_skipped_not_run
Exercises: friday, friday.continuous, friday.store, friday.toolsets

### `tests/test_objective_dispatch.py` (176 lines)

Covers: Test D: routing and dispatch reachability. Three layers must agree on the same seven names: * the capability router assigns every objective_* tool to the objectives group, and policy lets them through (AUTO, agent_runtime scope) * OT.capability_port() exposes the same names to the CLI and the engine * build_dispatch() (objective_cli) resolves those names - and the four demo capabilities - to working calls, raising LookupError for anything it does not know, and handling both sync and async toolse
Tests[11]: test_router_assigns_all_objective_tools_to_the_objectives_group, test_objective_tools_are_auto_policy_and_agent_scoped, test_port_exposes_the_same_seven_names, test_port_start_compiles_via_the_port, test_port_pause_resume_cancel_are_reachable, test_dispatch_routes_known_capabilities, test_dispatch_handles_async_toolset_functions, test_dispatch_handles_sync_toolset_functions, test_dispatch_unknown_capability_raises_lookup_error, test_dispatch_output_is_json_serialisable, test_port_flag_runners_work
Exercises: friday, friday.capability_router, friday.objective_cli, friday.policy, friday.store, friday.toolsets, friday.toolsets.web

### `tests/test_objective_engine.py` (211 lines)

Covers: Test H: the agent engine wiring and the lease arbitration. The durable lease is the only shared state between drivers - the agent engine, the MCP server's executor and a CLI child may all be alive in the same process space, and exactly one of them may drive any given wake. What is proved here: the engine FridayAgent.start_objective_engine() exists, starts once, and its background loop actually drives a compiled run to COMPLETED through the real dispatch. the lease two executors racing over one r
Tests[4]: test_agent_engine_starts_once_and_has_process_identity, test_agent_engine_drives_a_real_run_to_completed, test_two_executors_never_double_drive, test_stopped_executor_does_not_drive_and_new_one_resumes
Exercises: agent_friday, friday.continuous, friday.objectives, friday.store

### `tests/test_objective_failures.py` (303 lines)

Covers: Failure-local transitions: one task failing must not sink the run. Classification: TRANSIENT retries (bounded); STRUCTURAL / CAPABILITY_MISSING / POLICY_BLOCK / INVALID_ARGUMENT / NOT_CONFIGURED fail terminally; dependents of a failed task are skipped with blocked_by; independent tasks complete; the run ends PARTIAL at worst, and summaries never claim more than happened.
Tests[14]: test_failure_is_local, test_transient_retry_succeeds, test_transient_exhausted_is_terminal, test_capability_missing_not_recalled, test_invalid_arguments_are_terminal, test_policy_block_is_terminal, test_summary_honesty, test_failure_classifier_mapping, test_a_refusing_result_is_not_a_success, test_the_refusal_keeps_the_name_it_came_with, test_partial_work_is_not_called_a_failure, test_a_result_that_says_nothing_still_means_it_worked, test_an_unplaceable_clause_is_named_not_the_marker, test_a_genuinely_unknown_capability_still_says_so
Exercises: friday.continuous, friday.objectives, friday.policy, friday.store

### `tests/test_objective_ledger.py` (190 lines)

Covers: The objective ledger (PRD v3.1 FR-001, FR-002, FR-052, FR-053). FR-001 every objective carries class, risk tier, budgets, constraints, approvals, evidence and blocker as durable columns; the PRD 9.2 shape is assembled from the rows and is inspectable end-to-end FR-002 the class is decided at admission, deterministically FR-052 every consequential step writes expected -> actual -> method -> pass/fail to the ledger FR-053 COMPLETED requires passing evidence for every succeeded task; a task somebod
Tests[7]: test_admission_classifies_and_persists_the_objective_schema, test_critical_wording_sets_the_r3_floor, test_schema_columns_survive_a_reopen_and_the_ledger_is_traceable, test_evidence_and_approvals_are_append_only_with_sequence_and_time, test_every_step_writes_evidence_and_completion_enumerates_it, test_a_failed_step_is_evidence_too, test_completion_gate_refuses_a_task_that_was_only_said_to_be_done
Exercises: friday, friday.continuous, friday.store

### `tests/test_objective_live_server.py` (185 lines)

Covers: Test B: the Phase 3 objective surface over a real wire. The MCP adapter tests run in-process. This proves the two process paths the demo actually uses, end to end: server.py spawned on an unused port with its own database; an MCP SSE client connects, lists the seven objective tools, starts a run, reads its status, and cancels it - all over HTTP. the child the CLI driver spawned as its own process drives a run to COMPLETED against the same kind of database, and the run is read back from the sqlit
Tests[2]: test_objective_tools_work_over_sse, test_spawned_child_drives_to_completed_in_its_own_db
Exercises: friday.store

### `tests/test_objective_mcp.py` (168 lines)

Covers: Test E: the MCP surface of the objectives control plane. The adapter in friday/tools/objective_control.py is what the model actually touches: the seven tools it registers must be the same seven names the router, the port and the CLI know, and every control-plane operation must round-trip through them. The CLI flags are the operator's version of the same surface, so they are exercised here too - in a real subprocess, because an operator flag that only works in-process is a demo, not a flag.
Tests[8]: test_register_exposes_the_seven_tools, test_mcp_start_compiles_the_demo_objective, test_mcp_start_refuses_a_second_active_run, test_mcp_pause_resume_cancel_round_trip, test_mcp_history_without_run_id_uses_most_recent, test_cli_port_flags_round_trip, test_cli_drive_drives_a_real_run_to_terminal, test_cli_drive_is_honest_about_an_unmappable_objective
Exercises: friday, friday.store, friday.tools, friday.toolsets

### `tests/test_objective_planner.py` (167 lines)

Covers: Test F: the deterministic planner (friday/toolsets/objectives.py). The planner is the no-LLM path from a spoken sentence to a compiled run: clauses split on commas and "then" but NOT bare "and", each clause maps through the pattern rules, an "X and Y" compound falls back to planning each half, and anything unmappable becomes the objective.unmapped marker task - persisted as FAILED/CAPABILITY_MISSING by compile, never silently dropped. The planner must also never emit an objective_* capability as
Tests[17]: test_clauses_split_on_commas_and_then_not_bare_and, test_independent_clauses_do_not_depend_on_each_other, test_steps_inside_one_clause_still_chain, test_check_health_maps_to_system_get_info, test_open_app_maps_and_title_cases, test_open_app_rejects_bare_verb_remainder, test_search_maps_to_web_search, test_notify_clause_is_a_note_not_a_task, test_compound_note_create_plus_cleanup, test_cleanup_alone_is_honest_unmapped, test_fallback_scores_against_manifest_examples, test_fallback_never_emits_an_objective_capability, test_gibberish_becomes_unmapped_marker, test_compound_with_one_unmappable_half_is_one_marker, test_empty_objective_plans_nothing, test_demo_objective_plans_against_the_real_manifest, test_planner_never_emits_objective_capability_with_real_manifest
Exercises: friday, friday.toolsets

### `tests/test_objective_restart.py` (146 lines)

Covers: Restart recovery: a run in progress survives a process restart. The store is the only thing shared across restarts. A fresh executor (new process identity) reacquires the lease, marks the dead portion interrupted with evidence, and finishes the run - without any user message.
Tests[3]: test_fresh_executor_resumes_same_run, test_a_finished_task_is_never_dispatched_twice, test_an_interrupted_task_is_re_dispatched_and_a_finished_one_is_not
Exercises: friday.continuous, friday.objectives, friday.store

### `tests/test_objective_watchdog.py` (181 lines)

Covers: Watchdog + invariant tests. The invariant NON_TERMINAL_RUN_HAS_FUTURE must hold at every instant: a non-terminal run has an active executor, a scheduled next_wake, or a legitimate WAITING_* state. An orphaned run (stale lease) is reconciled exactly once per outage, with a generation bump preventing double continuation.
Tests[4]: test_watchdog_reconciles_orphan_once, test_watchdog_does_not_steal_a_live_lease, test_invariant_holds_throughout, test_waiting_states_are_legitimate_no_wake
Exercises: friday.continuous, friday.contracts, friday.objectives, friday.store

### `tests/test_observability.py` (183 lines)

Covers: Observability (PRD v3.1 FR-054; 12.3 Operational Diagnostics). FR-054 acceptance: "A single trace reconstructs what happened without reading raw model thoughts." So the trace is built from durable state only - objective ledger, tool results, gateway calls, audit log, worker runs - and the test drives a REAL objective through the engine (one task fails and is retried, one succeeds), then checks the trace tells that story: transitions, the retry, latency, the error, the verification. 12.3: one dia
Tests[7]: test_trace_reconstructs_a_real_objective_from_durable_state, test_unknown_run_is_said_not_invented, test_redaction_by_key_and_by_token_shape, test_diagnostics_reports_every_section_and_never_raises, test_diagnostics_surfaces_recent_critical_failures, test_the_tool_faces_are_wired, test_toolset_faces_run
Exercises: friday, friday.continuous, friday.model_gateway, friday.objectives, friday.store, friday.toolsets

### `tests/test_onboarding.py` (328 lines) [UNTRACKED]

Covers: Progressive onboarding (FR-ONB-004), and policy compiled from it (FR-POL-002). The PRD's acceptance criteria are the test list: a normal user operational in roughly five to ten minutes, deep personalisation continuing afterwards rather than blocking, and a profile that is inspectable memory and policy rather than a hidden system prompt. The last one is what most of these tests are about, because it is the one an implementation quietly fails. A prompt paragraph assembled from the answers would sa
Tests[30]: test_an_answer_becomes_an_inspectable_memory_row, test_the_profile_reads_back_out_of_memory, test_deleting_the_memory_changes_the_profile, test_a_list_answer_round_trips_as_a_list, test_a_later_answer_supersedes_without_destroying_the_earlier_one, test_an_answered_question_is_not_asked_again, test_a_declined_question_is_not_asked_again_either, test_pending_comes_back_in_interview_order, test_a_user_is_useful_once_the_essential_stages_are_done, test_progress_tells_the_user_where_they_are, test_an_unknown_field_is_refused_rather_than_silently_stored, test_the_workspace_is_named_after_the_company, test_a_user_with_no_company_stays_in_the_default_workspace, test_two_workspaces_do_not_see_each_others_profiles, test_policy_is_compiled_from_the_profile_not_from_a_template, test_a_user_who_says_nothing_gets_the_behaviour_the_product_always_had, test_an_unknown_level_is_refused, test_compiled_policy_still_cannot_reach_the_constitutional_set, test_the_consequential_domains_survive_the_highest_level, test_workers_are_suggested_from_the_answers_with_the_reason, test_an_empty_profile_suggests_nothing, test_suggesting_a_worker_creates_nothing, test_the_console_reports_status_before_anything_is_answered, test_the_console_records_an_answer_and_reads_it_back, test_the_console_splits_a_comma_list_into_items…
Exercises: friday, friday.store, friday.user_policy

### `tests/test_orgplane.py` (69 lines)

Covers: OrganizationControlPlane - neutrality, optionality, honest degradation.
Tests[6]: test_operation_lifecycle, test_pause_resume, test_customer_shape_has_no_backend_jargon, test_paperclip_adapter_degrades_honestly, test_simple_tasks_never_touch_the_plane, test_control_plane_accessor_prefers_truth
Exercises: friday, friday.tools

### `tests/test_ownership.py` (55 lines)

Covers: The claim guard, and what it must not change about the tools it wraps. The claim semantics themselves are gated in test_core01_gates.py. This file is about the wrapper: `guard()` sits in front of all 125 registered tools, so a mistake in it is a mistake in every one of them at once - which is exactly what happened, and the only symptom was a RuntimeWarning in a log nobody was reading during a voice session.
Tests[2]: test_an_async_tool_stays_async_through_the_guard, test_a_guarded_async_tool_actually_runs
Exercises: friday.tools

### `tests/test_phase0.py` (451 lines)

Covers: Phase 0 regression tests. Scope: the modernization contract. Providers resolve correctly, unsupported models cannot start the app, deprecated LiveKit constructor arguments are gone, and no tool can lie about what it did. These run offline. Nothing here contacts a model provider or a live MCP server, and no test asserts on a credential value.
Tests[37]: test_every_role_resolves_to_an_installed_model, test_backends_use_different_ids_for_the_same_role, test_unknown_role_is_rejected, test_unknown_backend_is_rejected, test_model_missing_from_installed_package_is_rejected, test_gemini_35_and_36_are_not_installed, test_groq_stt_requires_its_own_credential, test_livekit_backend_needs_livekit_credentials_not_google, test_missing_credentials_reports_names_never_values, test_unknown_provider_is_rejected, test_plugins_are_imported_at_module_scope_not_lazily, test_build_functions_work_off_the_main_thread, test_turn_handling_respects_a_pause, test_turn_delays_come_from_the_environment_and_are_bounded, test_the_turn_detector_is_wired_at_the_entrypoint, test_turn_handling_keys_are_valid_for_installed_livekit, test_agent_session_accepts_turn_handling_without_deprecated_args, test_mcp_toolset_is_constructible_with_a_stable_id, test_agent_source_has_no_deprecated_constructor_arguments, test_dead_host_resolution_helper_is_gone, test_mcp_url_is_configurable, test_the_search_stub_is_gone_not_merely_reworded, test_system_info_declares_it_is_not_the_users_pc, test_current_time_declares_scope, test_every_capability_declares_valid_scope_and_side_effect…
Exercises: agent_friday, friday, friday.tools

### `tests/test_phase1a.py` (416 lines)

Covers: Phase 1A: policy engine, app resolution, system toolset. The load-bearing test is test_open_never_claims_success_without_process_evidence - it is the unit-level statement of the whole Phase 1 premise.
Tests[31]: test_default_decisions, test_unknown_tool_defaults_to_ask_not_auto, test_secret_read_is_denied_and_cannot_be_approved, test_deny_tier_is_reachable_not_dead_policy, test_unknown_tool_cannot_be_approved, test_session_approval_upgrades_ask_to_auto, test_require_raises_on_ask, test_decide_accepts_only_a_tool_id, test_policy_overrides_validated, test_unknown_app_resolves_to_none, test_discovery_finds_apps_without_hardcoded_paths, test_calculator_resolves_with_uwp_process_aliases, test_alias_maps_language_onto_stems, test_expected_processes_always_present, test_open_never_claims_success_without_process_evidence, test_open_succeeds_only_when_a_matching_process_appears, test_open_reports_already_running_honestly, test_open_unknown_app_fails_and_says_where_it_looked, test_open_reports_launch_failure_rather_than_success, test_ask_gated_tools_are_cancelled_not_executed, test_clipboard_write_is_gated, test_every_system_result_declares_local_machine_scope, test_scope_distinguishes_user_machine_from_agent_runtime, test_list_processes_returns_real_data, test_list_processes_rejects_unknown_sort…
Exercises: friday, friday.tools, friday.toolsets

### `tests/test_phase1b.py` (327 lines)

Covers: Phase 1B: web search, fetch, news, browser control. Most tests are offline. The few that touch the network are marked `live` and skipped with -m "not live".
Tests[25]: test_confirmation_request_halts_instead_of_being_acknowledged, test_ordinary_actions_do_not_trigger_the_gate, test_non_confirmation_safety_decisions_pass_through, test_browser_automation_is_ask_gated, test_browser_control_is_auto_but_automation_is_not, test_search_failure_is_failed_not_empty_results, test_search_falls_through_to_the_next_provider, test_provider_returning_zero_results_is_not_success, test_empty_query_rejected, test_search_evidence_names_the_provider_and_first_url, test_fetch_rejects_non_http_schemes, test_news_rejects_unknown_topic, test_source_label_strips_meaningless_subdomains, test_ddg_redirect_urls_are_unwrapped, test_html_is_stripped_and_unescaped, test_navigate_without_session_fails_clearly, test_inspect_without_session_fails, test_close_with_no_session_is_honest_not_a_lie, test_browser_rejects_non_http_url, test_bare_hostname_is_upgraded_to_https, test_http_error_page_is_partial_not_succeeded, test_live_search_returns_real_external_sources, test_live_fetch_returns_real_page_text, test_live_browser_opens_and_closes, test_session_rebuilds_rather_than_failing_on_a_new_event_loop
Exercises: friday, friday.toolsets

### `tests/test_phase1c.py` (418 lines)

Covers: Phase 1C: filesystem jail and files toolset. Most of this file is escape attempts. The jail is the security boundary for every file capability, so it gets tested adversarially rather than happily.
Tests[37]: test_parent_traversal_is_refused, test_absolute_path_outside_root_is_refused, test_home_expansion_cannot_escape, test_symlink_pointing_outside_is_refused, test_denylisted_files_are_refused_even_inside_the_root, test_git_and_ssh_directories_are_refused, test_alternate_data_stream_is_refused, test_reserved_device_names_are_refused, test_unc_paths_are_refused, test_empty_and_null_paths_refused, test_legitimate_paths_are_allowed, test_default_root_is_not_the_project_or_home, test_read_returns_content_with_hash_evidence, test_read_missing_file_fails, test_read_refuses_escape, test_read_binary_is_partial_not_success, test_list_and_info, test_search_by_glob_and_content, test_a_search_that_finds_everything_says_it_was_complete, test_a_search_stops_on_a_budget_and_admits_it, test_the_hit_cap_is_reported_the_same_way, test_the_virtualenv_is_not_the_project, test_search_excludes_protected_files_from_results, test_writes_are_ask_gated, test_create_writes_and_verifies_by_readback…
Exercises: friday, friday.fsjail, friday.toolsets, friday.toolsets.system

### `tests/test_phase1d.py` (270 lines)

Covers: Phase 1D: durable memory with provenance. The two load-bearing tests: - test_memory_survives_a_real_process_restart (§13) - test_recall_falls_back_when_the_exact_key_is_unknown (regression)
Tests[19]: test_memory_survives_a_real_process_restart, test_recall_falls_back_when_the_exact_key_is_unknown, test_fuzzy_fallback_does_not_invent_matches, test_fuzzy_ranks_the_best_match_first, test_a_fact_and_an_inference_are_not_spoken_the_same_way, test_low_confidence_is_hedged_further, test_every_kind_has_its_own_phrasing, test_remember_rejects_an_unknown_kind, test_remember_requires_subject_and_value, test_remember_verifies_by_reading_back, test_forget_is_ask_gated, test_forget_supersedes_but_retains_history, test_forget_unknown_subject_fails, test_project_context_gathers_memories_and_decisions, test_session_recap_is_not_destructive, test_recap_with_no_history_fails_rather_than_inventing, test_raw_utterance_is_preserved_alongside_the_correction, test_memory_results_declare_agent_runtime_scope, test_memory_policy_defaults
Exercises: friday, friday.store, friday.toolsets, friday.toolsets.system

### `tests/test_phase1e.py` (316 lines)

Covers: Phase 1E: vision, on demand, without inventing anything. The structural claim under test: confidence decides the ActionResult status, so an uncertain identification cannot be asserted. Tests that need a camera or the vision model are marked `live`.
Tests[23]: test_confidence_bands, test_spoken_form_matches_the_band, test_high_confidence_without_an_identification_does_not_hedge, test_high_confidence_with_neither_identification_nor_text, test_model_sentences_are_tidied_into_ours, test_tidy_leaves_proper_nouns_alone, test_low_confidence_asks_for_a_better_view, test_uncertain_identifications_cannot_be_claimed, test_a_partial_result_still_carries_the_evidence, test_capture_failure_is_failed_not_a_guess, test_analysis_failure_keeps_the_frame_but_does_not_answer, test_a_stale_frame_is_refused, test_missing_monitor_fails, test_no_display_is_unsupported_not_a_failed_capture, test_every_answer_names_the_frame_it_answered_about, test_capture_writes_a_retrievable_artifact, test_screen_artifacts_are_typed_as_screenshots, test_frame_digest_changes_with_content, test_vision_policy_defaults, test_camera_can_be_gated_without_gating_screenshots, test_gated_camera_is_cancelled_not_executed, test_live_screen_capture_produces_a_real_image, test_live_camera_is_released_between_captures
Exercises: friday, friday.toolsets, friday.toolsets.system

### `tests/test_phase1f.py` (215 lines)

Covers: Phase 1F: Spotify transport control, verified by state change. The claim under test: sending a command is not evidence that music changed. Only Spotify's own window title changing is.
Tests[14]: test_parse_title, test_advertisement_is_playing_but_not_a_track, test_a_track_with_a_dash_in_its_name_keeps_it, test_no_state_change_is_partial_not_succeeded, test_a_change_to_the_wrong_state_is_partial, test_already_in_the_requested_state_is_success, test_successful_transport_records_both_titles, test_not_running_fails_rather_than_pretending, test_play_with_a_query_cannot_claim_it_played, test_play_without_a_query_is_a_plain_resume, test_empty_search_is_refused, test_search_reports_that_playback_did_not_start, test_media_policy_defaults, test_live_spotify_state_is_readable
Exercises: friday, friday.toolsets

### `tests/test_phase1g.py` (278 lines)

Covers: Phase 1G: durable, OS-scheduled reminders. Time parsing is tested exhaustively and offline. Scheduler tests create real Windows tasks and clean up after themselves. The test that proves a reminder actually fires is marked `slow` because it has to wait for the clock.
Tests[19]: test_when_parsing, test_unparseable_times_are_refused_not_guessed, test_a_clock_time_already_past_rolls_to_tomorrow, test_iso_with_timezone_is_normalised, test_openers_match_the_hour, test_openers_vary_within_a_band, test_every_opener_is_in_character, test_generated_fire_script_is_valid_python, test_fire_script_never_interpolates_the_message_into_powershell, test_past_time_is_refused, test_empty_message_is_refused, test_unparseable_when_fails_the_tool_too, test_cancel_unknown_id_fails, test_reminder_policy_defaults, test_create_verifies_the_task_is_actually_registered, test_pending_list_reports_whether_the_os_still_holds_it, test_cancel_removes_the_task_from_the_scheduler, test_reminder_row_persists_independently_of_the_scheduler, test_a_reminder_actually_fires
Exercises: friday, friday.store, friday.toolsets, friday.toolsets.reminders

### `tests/test_planner.py` (281 lines)

Covers: Understanding the request before choosing a tool. The six cases below are the ones the clause-splitting planner got wrong, and each is taken from something that actually happened rather than invented to be easy. Measured against the old planner first, so "red" is a fact: "Friday, check my computer" -> objective.unmapped + system_get_info the name became a task, because fragments became tasks "Check my computer and open Paint. If one part fails, keep going." -> system_get_info + memory_remember "
Tests[24]: test_a_vocative_is_not_work, test_the_goal_beside_the_vocative_survives, test_a_technology_story_is_a_web_search, test_the_candidate_set_is_small, test_do_not_ask_me_to_continue_is_a_constraint, test_the_goals_beside_the_constraint_survive, test_a_file_lifecycle_is_one_ordered_branch, test_the_lifecycle_carries_its_dependencies, test_a_failure_policy_is_a_constraint, test_and_open_paint_is_still_its_own_goal, test_a_long_request_does_not_become_a_long_plan, test_the_parts_that_are_not_work_are_kept_apart, test_an_unplaceable_phrase_is_reported_not_guessed, test_validation_catches_a_capability_that_does_not_exist, test_validation_catches_a_wrong_domain_choice, test_validation_catches_a_dangling_dependency, test_a_good_plan_has_nothing_to_complain_about, test_the_vocative_gate_would_catch_a_regression, test_the_constraint_gate_would_catch_a_regression, test_a_request_whose_target_is_unread_is_not_confined_to_the_system, test_the_bare_word_web_names_a_target, test_the_bare_word_system_names_a_target, test_being_the_only_near_thing_is_not_being_the_right_thing, test_an_exact_match_that_is_the_only_one_still_wins
Exercises: friday

### `tests/test_planner_model.py` (295 lines)

Covers: Asking a model what a request means, without letting it choose the tools. Three constraints, and each has a gate here because each fails silently: it never sees the capabilities a planner prompt carrying 125 schemas is the token and accuracy problem CORE-02B removed, rebuilt inside the planner it never names a capability a model that can name tools invents them, and `magic_super_tool` cannot fail well it is not trusted schema-constrained output is well-formed, which is not the same as correct An
Tests[22]: test_an_instruction_does_not_need_a_model, test_a_described_idea_does_need_a_model, test_unplaceable_content_in_a_long_request_asks_for_help, test_the_prompt_does_not_carry_the_capability_catalogue, test_the_domain_summary_is_built_from_the_registry, test_the_prompt_tells_the_model_not_to_name_tools, test_a_hallucinated_capability_never_reaches_the_plan, test_an_operation_outside_the_vocabulary_is_refused, test_a_target_outside_the_vocabulary_is_refused, test_a_goal_with_no_intent_is_dropped, test_the_model_may_say_one_goal_follows_another, test_independent_goals_stay_independent, test_a_missing_provider_keeps_the_deterministic_reading, test_a_failing_model_keeps_the_deterministic_reading, test_what_the_deterministic_pass_understood_is_not_lost, test_a_model_plan_is_resolved_by_the_registry, test_short_nonsense_is_not_handed_to_a_model, test_a_long_description_still_reaches_the_model, test_a_recorded_decision_becomes_planning_context, test_nothing_decided_adds_nothing_to_the_prompt, test_two_projects_in_flight_contribute_no_context, test_the_context_stays_small
Exercises: friday, friday.store

### `tests/test_platform_power.py` (171 lines)

Covers: What this machine can do about power, and why the obvious API is not asked. Two findings are pinned here, both measured rather than reasoned: SeShutdownPrivilege is held and DISABLED, so every power call fails until it is enabled - and AdjustTokenPrivileges returns non-zero having enabled nothing, so its return value is not the answer. IsPwrSuspendAllowed answers about S1-S3. On a modern-standby machine it returns False while the machine sleeps every night. The second is the one worth a test of 
Tests[6]: test_capabilities_can_be_read_at_all, test_a_modern_standby_machine_is_reported_as_able_to_sleep, test_the_legacy_suspend_api_is_never_consulted, test_a_failed_capability_call_raises_rather_than_reporting_nothing, test_enabling_the_privilege_actually_flips_the_token_bit, test_the_privilege_check_does_not_trust_the_return_value
Exercises: friday.platform

### `tests/test_power.py` (389 lines)

Covers: Turning the machine off, and everything that has to be true first. Nothing here actually shuts anything down. The Win32 calls are replaced at the platform boundary, which is exactly where the honest limit of this feature sits: what Friday does with the request is testable, and whether Windows then really restarts is not something a test suite gets to find out repeatedly. What is proven here is the part that can be wrong in Friday: no yes nothing is requested a yes for something else refused, nam
Tests[23]: test_without_a_yes_nothing_is_requested, test_full_autonomy_does_not_answer_for_the_person, test_the_question_names_the_action_the_target_and_the_cost, test_locking_does_not_claim_unsaved_work_is_at_risk, test_a_shutdown_yes_cannot_restart, test_a_normal_restart_yes_cannot_force_one, test_the_forced_variant_says_what_it_costs, test_a_spent_yes_cannot_be_spent_again, test_an_accepted_restart_is_initiated_and_never_succeeded, test_the_countdown_is_long_enough_to_call_back, test_a_lock_request_is_initiated_not_verified, test_the_ordinary_path_sets_no_force_flag, test_suspending_never_forces_either, test_the_pending_record_exists_before_the_request_is_made, test_a_refused_request_does_not_leave_a_pending_record, test_a_countdown_can_be_called_back, test_cancelling_needs_no_approval, test_cancelling_nothing_says_so, test_hibernate_is_never_quietly_replaced_with_sleep, test_an_unsupported_action_is_refused_before_it_is_offered, test_a_planted_request_is_refused_before_the_machine_is_consulted, test_an_unattended_run_is_refused_rather_than_left_pending, test_a_page_cannot_ask_for_this
Exercises: friday, friday.platform, friday.store, friday.toolsets

### `tests/test_power_state.py` (197 lines)

Covers: Settling what Friday could not stay to watch. The whole module exists for one situation: Friday asks the machine to restart, and then stops existing. Nothing it was going to write gets written. So the note goes down first, and a later run - a different process, after a different boot - decides what happened by looking at the machine rather than by trusting a record left by something that is no longer running. These tests prove that without restarting anything, by fabricating the boot identity th
Tests[11]: test_a_request_is_recorded_as_initiated_not_succeeded, test_only_the_actions_the_feature_offers_can_be_recorded, test_the_boot_identity_is_stable_within_one_boot, test_a_restart_that_happened_settles_as_observed, test_a_restart_that_never_happened_settles_as_not_carried_out, test_a_request_still_inside_its_window_is_left_alone, test_two_pending_requests_settle_independently, test_settling_is_not_repeated, test_a_cancelled_request_is_not_carried_out_rather_than_failed, test_a_settled_record_names_both_runs_and_claims_nothing_for_the_first, test_removing_the_boot_comparison_would_be_caught
Exercises: friday, friday.store

### `tests/test_privacy.py` (196 lines)

Covers: Privacy mode, and the promise it must not overstate. The failure being guarded is not a leak. It is a *silent* leak: a mode that says private and quietly reroutes to a cloud model when the local one cannot answer, after the person has stopped watching.
Tests[23]: test_the_default_is_open, test_the_mode_is_read_at_call_time, test_an_unknown_mode_falls_back_to_open, test_open_mode_refuses_nothing, test_private_mode_refuses_a_network_capability, test_private_mode_allows_a_local_capability, test_the_guard_raises_with_the_reason, test_the_guard_is_silent_when_allowed, test_a_capability_can_be_named_rather_than_passed, test_an_unknown_capability_is_not_refused_by_guesswork, test_open_mode_allows_any_backend, test_private_mode_allows_a_local_backend, test_private_mode_refuses_a_cloud_backend, test_an_unknown_backend_fails_closed, test_open_mode_does_not_claim_privacy, test_private_mode_with_a_cloud_model_says_what_it_does_not_cover, test_private_mode_with_a_local_model_is_complete, test_the_backend_is_read_from_the_environment_when_unstated, test_open_mode_leaves_everything_available, test_private_mode_costs_only_the_network_capabilities, test_the_runtime_refuses_a_network_capability, test_the_refusal_happens_before_anything_is_loaded, test_a_local_capability_still_runs_under_privacy_mode
Exercises: friday, friday.capability_runtime

### `tests/test_processes.py` (338 lines)

Covers: Ending a process, and knowing which one. Two operations that are deliberately not interchangeable: asking an application to close, which it may refuse, and terminating it, which it cannot. The second needs a yes that names *this* process, and the target is resolved again immediately before the act - because between the question and the answer a pid can be released and handed to something else, and that gap is exactly where the confirmation sits.
Tests[19]: test_a_token_survives_a_pid_being_reused, test_the_window_owner_is_not_the_launched_pid, test_ownership_comes_from_windows_not_from_the_title, test_a_cooperative_application_closes, test_an_application_that_refuses_is_not_reported_as_closed, test_closing_never_terminates, test_the_app_level_close_never_terminates_either, test_closing_is_auto_under_full_autonomy_and_still_graceful, test_a_process_with_no_window_is_not_quietly_killed, test_without_a_yes_nothing_is_terminated, test_a_yes_for_something_else_does_not_terminate_it, test_the_right_yes_terminates_exactly_that_process, test_a_confirmation_cannot_be_spent_twice, test_a_target_that_exits_while_waiting_is_not_replaced, test_system_processes_are_refused_by_name, test_friday_will_not_end_itself, test_a_protected_process_is_refused_before_it_is_offered, test_a_protected_process_is_not_reported_as_missing, test_something_that_genuinely_is_not_running_says_so
Exercises: friday, friday.toolsets

### `tests/test_product.py` (401 lines)

Covers: Turning a spoken idea into something that can actually be built. Journey D. The requirements loop asked good questions and recorded the answers, and stopped there - one step short of useful, because the arc ended exactly where the build should begin. The boss still had to write the requirements, the acceptance criteria and the Claude prompts himself. The sentence this is measured against: "I want to build a small desktop game where I control a combat drone. I think Godot is probably the best cho
Tests[38]: test_being_asked_to_check_is_recognised, test_the_technology_claim_is_taken_as_something_to_check, test_a_statement_about_the_world_is_a_claim, test_a_preference_is_not_researched, test_verifying_a_preference_changes_nothing, test_reading_sources_does_not_by_itself_settle_a_claim, test_the_challenge_instruction_refuses_to_soften, test_acceptance_describes_an_outcome_not_a_call, test_an_unmeasurable_requirement_is_flagged_not_invented, test_a_requirement_that_carries_a_number_is_testable, test_nothing_asked_produces_nothing, test_a_contradiction_is_found, test_requirements_that_agree_produce_no_conflict, test_a_blocking_question_means_not_ready, test_an_assumption_alone_still_allows_building, test_everything_answered_is_ready, test_a_project_with_nothing_decided_is_not_ready, test_a_requirement_keeps_its_acceptance, test_superseding_keeps_the_old_one_and_the_reason, test_changing_one_requirement_leaves_the_others, test_the_brief_is_assembled_from_the_store, test_the_brief_surfaces_conflicts_rather_than_choosing, test_an_unknown_project_has_no_brief, test_a_superlative_is_checked_against_alternatives_not_the_subject, test_the_question_carries_what_he_is_building…
Exercises: friday, friday.store, friday.toolsets

### `tests/test_product_mcp.py` (381 lines)

Covers: The MCP surface of product processing: the boundary, and the counting. The pipeline itself is proved in test_products.py and the two golden gates. What is proved here is the adapter - the part the model actually touches - because two things about it are easy to get wrong in a way no pipeline test would notice: the path "process this catalogue" is a file read whose path the *model* chooses. Without the jail it is a read primitive pointed at any file on the disk, with the contents coming back as p
Tests[27]: test_a_catalogue_outside_the_roots_is_refused, test_a_protected_file_inside_a_root_is_still_refused, test_a_refused_path_is_a_result_not_an_exception, test_export_will_not_write_outside_the_roots, test_export_inside_the_roots_works, test_a_duplicate_is_counted_as_deduplicated_not_as_processed, test_the_outcomes_account_for_every_canonical_product, test_an_unparseable_price_is_quarantined_and_named, test_reading_a_run_does_not_reprocess_it, test_a_returned_run_states_that_it_is_over, test_which_products_failed_is_answered_without_inference, test_outcome_is_literal_and_needs_attention_is_the_wide_one, test_the_filters_combine_rather_than_override, test_retryable_is_its_own_question, test_the_removed_filter_is_refused_rather_than_ignored, test_an_unknown_outcome_says_so_rather_than_answering_something_else, test_a_retry_refuses_when_it_would_have_to_guess_which_run, test_an_export_refuses_the_same_way, test_the_run_this_session_started_is_not_a_guess, test_reading_still_answers_when_mutating_would_not, test_the_answer_comes_before_the_provenance, test_a_retry_says_what_it_re_ran, test_asking_without_a_run_id_still_gets_an_answer, test_asking_about_nothing_at_all_says_there_is_nothing, test_a_named_catalogue_picks_that_run…
Exercises: friday, friday.fsjail, friday.store, friday.tools, friday.toolsets

### `tests/test_products.py` (516 lines)

Covers: The eight failure journeys. A product pipeline is only worth anything on the bad day, so the tests are organised by what goes wrong rather than by which function is being called. In every one of them the question is the same: does the failure stay the size it actually is, or does it grow to swallow the batch? 1 bad row one row invalid others continue 2 missing image image branch fails text branch continues 3 invalid url refused before fetching no SSRF surface 4 duplicate sku exact vs conflicting
Tests[35]: test_a_clean_row_goes_all_the_way_through, test_every_generated_field_says_where_it_came_from, test_a_direct_field_carries_no_invented_confidence, test_a_bad_row_is_quarantined_and_the_others_continue, test_a_row_with_no_sku_is_quarantined_not_guessed, test_a_quarantined_row_stops_at_the_stage_that_refused_it, test_a_missing_image_costs_only_the_image_branch, test_a_missing_image_does_prevent_the_export_that_needs_it, test_the_skipped_stage_says_what_it_was_waiting_for, test_a_url_from_a_spreadsheet_is_untrusted_input, test_an_ordinary_url_is_allowed, test_a_private_url_can_be_permitted_explicitly, test_an_unresolvable_host_is_not_treated_as_hostile, test_a_bad_url_fails_only_the_image_branch, test_an_exact_duplicate_is_deduplicated, test_a_conflicting_duplicate_is_never_silently_merged, test_a_collapsed_duplicate_is_not_counted_as_a_processed_product, test_distinct_products_are_left_alone, test_a_conflicting_duplicate_is_recorded_rather_than_dropped, test_a_timeout_is_retried_up_to_its_cap_then_marked_retryable, test_a_timeout_does_not_stop_an_independent_branch, test_retries_are_capped_at_definition_time, test_content_generation_failing_is_not_ingestion_failing, test_a_batch_where_the_model_is_down_is_partial_not_failed, test_a_batch_resumes_without_redoing_what_it_finished…
Exercises: friday, friday.store

### `tests/test_profile.py` (321 lines)

Covers: The user model: learning, reconciliation, and correcting itself. Extraction needs a model, so it is `live`. Reconciliation is deterministic by design - that is the point - so all of it is tested offline.
Tests[28]: test_new_fact_supersedes_an_earlier_inference, test_an_inference_never_overwrites_something_you_said, test_two_stated_facts_disagreeing_is_a_conflict_not_a_decision, test_seeing_the_same_thing_again_reinforces, test_nothing_known_yet_is_simply_stored, test_low_confidence_candidates_are_rejected, test_a_clearly_more_confident_peer_replaces_the_earlier_one, test_similar_confidence_peers_conflict_rather_than_coin_flip, test_learning_records_the_quote_it_came_from, test_an_observation_cannot_be_stored_without_evidence, test_rejected_candidates_are_kept_on_record, test_reinforcement_raises_confidence_but_not_to_certainty, test_repeated_observation_climbs_over_days, test_a_conflict_is_recorded_with_both_sides, test_resolving_a_conflict_in_favour_of_the_new_value, test_resolving_in_favour_of_the_existing_value, test_resolve_rejects_a_nonsense_choice, test_resolve_unknown_conflict_raises, test_profile_groups_by_the_users_own_dimensions, test_brief_marks_inferences_so_they_are_not_read_as_facts, test_brief_is_empty_when_nothing_is_known, test_brief_is_bounded, test_explain_traces_a_belief_back_to_the_words, test_explain_shows_superseded_beliefs_too, test_one_active_belief_per_subject_across_kinds…
Exercises: friday, friday.store

### `tests/test_progress_digest.py` (213 lines)

Covers: No docstring; 16 test(s).
Tests[16]: test_digest_composed_only_from_events_and_dedupes, test_cadence_holds_digest_until_due, test_completion_is_a_milestone_not_a_digest_line, test_no_raise_on_garbage, test_gather_adds_recently_terminal_run_once, test_gather_merges_row_and_progress, test_caller_must_not_store_next_at_when_no_digest_fired, test_balanced_is_the_unset_default, test_critical_only_drops_process_chatter_but_keeps_the_facts, test_detailed_is_a_superset_of_balanced_information, test_milestone_detail_levels, test_unknown_detail_level_falls_back_to_balanced_not_a_crash, test_outcome_line_detail_levels, test_read_detail_level_defaults_to_balanced_when_nothing_set, test_read_detail_level_reads_the_contract, test_read_detail_level_ignores_unknown_value
Exercises: friday.progress_digest

### `tests/test_projects.py` (276 lines)

Covers: "Continue Halo." — picking a project up again, from nothing but the store. Journey C. The storage was already good: `projects`, `project_decisions`, `open_questions`, `artifacts`, and `store.projects()` to read them. What was missing was anything the boss could actually say. There was no `projects_list` and no `project_resume` in the registry, so "what am I working on?" and "continue Halo" had no capability behind them at all — the answer came from whatever the model remembered of the conversati
Tests[21]: test_the_capability_is_registered_and_reachable, test_they_read_memory_rather_than_the_machine, test_resuming_a_project_is_not_resuming_a_song, test_it_lists_what_is_on_record, test_it_says_how_much_is_settled_and_how_much_is_not, test_activity_orders_them_not_creation, test_no_projects_is_said_rather_than_faked, test_it_reconstructs_a_project_with_no_conversation_at_all, test_it_survives_a_restart, test_a_blocking_question_comes_before_everything_else, test_once_everything_is_answered_the_next_move_is_to_build, test_a_project_with_nothing_in_it_says_so, test_the_name_does_not_have_to_be_exact, test_an_ambiguous_name_is_refused_rather_than_guessed, test_an_unknown_project_is_not_invented, test_both_carry_verification_a_person_could_check, test_a_cosmetic_question_does_not_stop_the_work, test_a_material_question_does_stop_it, test_the_assumption_is_named_rather_than_hidden, test_both_kinds_together_block_only_on_the_material_one, test_a_question_nobody_classified_blocks
Exercises: friday, friday.store, friday.toolsets

### `tests/test_promotion.py` (330 lines)

Covers: The gate, and every way round it. The tests worth having are the ones that prove a refusal, because the failure mode of a gate is that it opens.
Tests[33]: test_an_unverified_run_is_refused, test_a_failed_verifier_is_refused, test_an_inconclusive_verifier_is_not_a_pass, test_a_verified_run_nobody_approved_is_refused, test_a_run_that_changed_nothing_is_refused, test_a_verified_approved_in_scope_run_is_allowed, test_a_file_outside_the_scope_is_refused, test_an_unscoped_run_is_not_blocked_by_the_scope_check, test_a_forbidden_file_is_refused_even_when_in_scope, test_the_never_list_holds, test_a_secret_in_a_changed_file_is_refused, test_the_secret_scan_runs_before_anyone_is_asked_to_approve, test_ordinary_code_is_not_called_a_secret, test_a_binary_or_missing_file_does_not_break_the_scan, test_every_check_that_ran_is_recorded, test_a_refusal_says_which_check_failed, test_the_decision_serialises_for_a_report, test_a_passing_verifier_passes, test_a_failing_verifier_fails, test_a_missing_command_is_inconclusive_not_failed, test_a_hanging_verifier_is_inconclusive, test_the_verifier_runs_in_the_workspace, test_graded_records_the_attempt, test_tool_debris_is_refused, test_machine_configuration_is_refused…
Exercises: friday

### `tests/test_promotion_wire.py` (31 lines)

Covers: S7's gate is only worth anything if the bridge actually calls it: a terminal work run must route its handoff through memory_promotion exactly once.
Tests[1]: test_terminal_run_routes_its_handoff_through_the_promotion_gate
Exercises: friday, friday.memory_promotion, friday.toolsets

### `tests/test_prompt_transport.py` (138 lines) [UNTRACKED]

Covers: PRD-OWN-WINPROMPT: a worker's prompt must never ride the Windows command line. Windows caps a process's command line and environment block at roughly 32,767 characters, and this repository has already been crash-looped by that limit once - AGENTS.md's operational notes record dev mode's `watchfiles` overflowing the Windows env-var limit on a large change set. A work packet (`TaskBundle.prompt()`) for a real engineering task, with real context and acceptance criteria, is easily tens of thousands 
Tests[3]: test_the_work_packet_is_realistically_large, test_no_argv_element_could_carry_the_prompt, test_a_huge_prompt_reaches_the_cli_over_stdin
Exercises: friday.executors, friday.executors.claude_code

### `tests/test_provider_diagnostics.py` (204 lines)

Covers: Provider failures, told apart. Every case here is a shape actually observed against the installed stack (google-genai 1.70.0, livekit-plugins-google 1.5.1) during the provider root-cause investigation, not an invented one. The distinction that matters is whether retrying could possibly help: a 504 DEADLINE_EXCEEDED retry; it was a bad moment a missing thought_signature do not; the request is missing it on the second attempt too Both used to arrive as "failed to generate llm completion" and be re
Tests[14]: test_a_missing_thought_signature_is_structural, test_an_unexpected_tool_call_is_structural, test_a_malformed_function_call_is_structural, test_a_deadline_exceeded_is_transient, test_server_and_rate_limit_codes_are_transient, test_an_empty_stop_is_no_content_not_structural, test_a_truncated_answer_says_it_was_truncated, test_a_blocked_generation_is_not_a_hiccup, test_the_same_sentence_produces_different_diagnoses, test_the_description_names_a_cause_not_a_symptom, test_nothing_secret_reaches_the_description, test_the_same_request_shape_fingerprints_the_same, test_a_different_tool_configuration_is_a_different_request, test_the_fingerprint_carries_no_prompt
Exercises: friday

### `tests/test_provider_fallback.py` (138 lines)

Covers: No docstring; 6 test(s).
Tests[6]: test_provider_error_classes_are_objective, test_request_fingerprint_contains_no_content_or_signature_bytes, test_native_history_requires_original_signature_and_matching_output, test_incomplete_history_is_rejected_before_provider_dispatch, test_complete_history_can_fallback_unchanged_after_transient_failure, test_structural_provider_response_is_not_sent_to_fallback_or_replayed
Exercises: friday.provider_fallback

### `tests/test_provider_health.py` (339 lines)

Covers: Provider health is a verdict over evidence, never over credentials (PRD Requirement 9; audit A-008/A-024). Every check runs against the real GatewayTelemetry ledger and, where a provider is involved, the real ModelGateway over the scripted worker. The live suite (2026-09-05) is the fixture these encode: seven providers "usable", three answering, one (gemini) answering with nothing, three failing durably on account facts (no payment method, unsupported model, 404). "Usable" must not be read as "h
Tests[18]: test_no_evidence_is_unprobed_not_healthy, test_a_recent_answer_with_content_is_healthy, test_durable_failures_are_unavailable_with_the_reason, test_transient_failures_are_degraded_not_dead, test_an_unclassified_failure_is_still_a_failure, test_old_evidence_expires_into_stale_never_into_healthy, test_the_latest_row_wins_and_refusals_say_nothing_about_a_route, test_routable_excludes_only_unavailable, test_health_separates_usable_from_healthy, test_a_durable_failure_is_unavailable_and_routing_skips_it, test_probe_reads_its_verdict_back_from_the_ledger, test_an_empty_answer_is_not_health, test_a_broken_model_does_not_condemn_the_provider, test_a_working_model_does_not_hide_a_broken_one, test_evidence_under_one_credential_is_not_evidence_under_another, test_the_provider_level_view_still_answers_can_this_be_reached, test_a_ledger_written_before_the_credential_column_still_opens, test_the_credential_label_is_never_the_secret
Exercises: friday, friday.model_gateway

### `tests/test_provider_outage.py` (278 lines)

Covers: A provider outage may end an attempt. It must never end the objective. `all LLMs are unavailable, retrying...` is a symptom, and on its own it says nothing about what to do next. What matters is the error immediately before it: 429 RESOURCE_EXHAUSTED rate or quota pressure wait, then ask again 503 UNAVAILABLE, timeout the service is struggling wait, then ask again 400 missing thought_signature the request is malformed never blindly UNEXPECTED_TOOL_CALL tool protocol state never blindly Every one
Tests[9]: test_an_outage_is_worth_waiting_for, test_a_malformed_request_is_not_sent_twice, test_an_ordinary_bug_is_still_structural, test_an_outage_does_not_end_the_run, test_the_task_succeeds_once_the_provider_comes_back, test_independent_work_continues_through_an_outage, test_an_outage_does_not_skip_dependents, test_a_structural_provider_bug_still_fails_the_task, test_an_outage_is_logged_with_what_to_do_about_it
Exercises: friday, friday.continuous, friday.objectives, friday.store

### `tests/test_question_broker.py` (261 lines)

Covers: The executor question channel, which existed and was wired to nothing. `ada_ask` was a registered capability resolving to no implementation - `audit_planner` says so in its own source, "registered but nothing to call" - and `broker_for` built the object that would have answered it, from a function nothing called. The agent was told in its prompt that it could ask, and had nowhere to ask. The two tests that matter are the acceptance cases: a question project memory settles must be answered withou
Tests[19]: test_a_settled_question_is_answered_from_the_project, test_a_material_question_stops_the_run, test_a_cheap_reversible_choice_is_decided_and_labelled, test_the_dangerous_questions_always_reach_him, test_a_dangerous_question_is_not_answered_even_when_memory_has_it, test_contradictory_project_memory_is_escalated, test_there_is_no_outcome_that_means_make_something_up, test_an_answered_question_is_recorded_so_it_is_asked_once, test_a_default_is_recorded_as_friday_s_choice_not_his, test_a_waiting_run_is_marked_in_the_store, test_an_unanswered_question_claims_no_completion, test_the_run_can_be_named_explicitly, test_a_finished_run_is_not_treated_as_live, test_a_question_with_no_run_at_all_still_gets_a_verdict, test_an_empty_question_is_refused, test_options_are_read_either_way, test_ada_ask_actually_resolves, test_broker_for_is_on_the_production_path, test_the_audit_still_knows_it_needs_a_live_run
Exercises: friday, friday.capability_runtime, friday.executors, friday.executors.claude_code, friday.store, friday.toolsets, friday.toolsets.memory

### `tests/test_quota_routing.py` (202 lines)

Covers: S2 quota routing: a 429/quota message is CAPPED, not TRANSIENT, and the router switches provider instead of retrying the same capped one.
Tests[13]: test_rate_limit_wording_is_capped_not_transient, test_reset_at_parses_explicit_clock_time, test_reset_at_parses_retry_after_seconds, test_reset_at_defaults_by_wording, test_plain_429_stays_transient, test_cooldown_persists_and_expires, test_candidates_order, test_plan_delegation_skips_a_cooled_candidate, test_plan_delegation_all_cooled_waits, test_bridge_marks_cooldown_from_a_capped_gateway_error, test_continuous_requeues_capped_without_backoff, test_plan_delegation_all_cooled_returns_no_capped_candidate, test_capped_requeue_delay_reaches_the_reset
Exercises: friday

### `tests/test_rc11_objective_continuity.py` (259 lines)

Covers: RC1.1 P0 regressions from the REAL failed production objective. Evidence row RUN-7361928cb223: - files_read got a malformed invented path and failed STRUCTURAL; - hermes_delegate was adapter-only inside the durable runtime and failed NOT_CONFIGURED; - dependent hermes_status skipped; - run settled PARTIAL, so the user's later `continue` was a new model turn and manual_continue_count stayed falsely 0. These tests are RED before the repair. They assert product behavior, not implementation details.
Tests[10]: test_planner_never_places_objective_control_inside_its_own_graph, test_compiler_refuses_control_plane_tasks_even_if_planner_regresses, test_submit_first_worker_is_reconciled_without_duplicate_delegation, test_active_objective_claim_does_not_expire_by_wall_clock, test_conversational_continue_after_terminal_partial_is_counted, test_live_agent_objective_engine_uses_its_connected_mcp_port, test_objective_completion_creates_exactly_once_delivery, test_continuity_state_projects_required_parent_cursor_fields, test_c1_finished_objective_reaches_the_user_with_no_model_call, test_c3_a_recoverable_connectivity_fault_does_not_end_the_run
Exercises: agent_friday, friday, friday.continuous, friday.objectives, friday.store

### `tests/test_rc11_production_isolation.py` (115 lines)

Covers: RC1.1 production-isolation cleanup — stale gate deliveries + honest WorkRun refusal classification. Live production evidence (2026-08-27): after C1-C9, a real Friday startup spoke two OLD recovery-gate prompts ("bridge recovered"/"gateway recovered", runs hermes-efff49e33a / hermes-c10474cdf4) into the user's session as Hermes failures. Two distinct defects: 1. ISOLATION: gate/test WorkRuns carried no origin, so the startup sweep and the delivery drain treated them as user-facing. Exactly-once h
Tests[10]: test_uppercase_workrun_failed_is_a_refusal_not_a_success, test_provider_auth_failure_is_user_required_not_connectivity, test_auth_words_veto_connectivity_classification, test_workrun_complete_and_partial_are_not_failures, test_delegate_envelope_with_nested_failed_record_is_a_refusal, test_delegate_envelope_with_nested_complete_is_not_a_refusal, test_gate_workrun_is_never_delivered_to_the_user, test_production_workrun_is_still_delivered_exactly_once, test_event_path_delivery_is_also_origin_filtered, test_origin_defaults_to_production
Exercises: friday

### `tests/test_reachability.py` (219 lines)

Covers: The gate that replaces "tests pass" as the completion signal. Three times in one session a feature was implemented, tested, green, and unreachable from the way Friday is actually used. This file is the standing check against a fourth. The important tests here are not the ones about the machinery. They are the named-feature gates at the bottom: each one asserts that a specific thing a person does reaches the code that does it. Those are the tests that would have failed on the typed-input defect, 
Tests[17]: test_the_depths_are_ordered, test_at_least_compares_depth_not_string, test_an_unknown_depth_never_satisfies_a_gate, test_tests_cannot_prove_the_depths_that_matter, test_the_minimum_for_a_user_facing_feature_is_reachability, test_every_declared_entry_point_exists, test_both_input_modalities_have_an_entry_point, test_a_test_import_is_not_production_reachability, test_module_scope_counts_as_a_root, test_an_approximate_reading_is_never_called_dead, test_a_declared_module_is_not_reported_dead, test_every_declaration_gives_a_reason, test_the_audit_reports_who_mentions_an_unreachable_thing, test_the_typed_path_prepares_a_turn_like_the_spoken_one, test_both_paths_share_the_preparation, test_a_real_journey_reaches_its_implementation, test_the_number_of_unreachable_things_does_not_grow
Exercises: friday

### `tests/test_reflex.py` (415 lines)

Covers: The local fast path, and the gate that decides it is allowed to act. Needle 2 is a 45M-parameter tool-calling model that runs on this machine in about 28MB of RAM. The case for it is real - "pause the music" should not cost a cloud reasoning turn - and so is the case against trusting it, which was measured rather than assumed. Asked five questions with four tools declared, the base model answered: "restart the song" -> power_restart confidence 0.197 "pause the music" -> no call at all confidence
Tests[36]: test_the_fast_path_is_off_by_default, test_turning_it_on_takes_a_deliberate_act, test_a_missing_model_escalates_rather_than_failing, test_the_reboot_it_proposed_for_a_media_command_is_refused, test_the_same_mistake_is_caught_where_policy_cannot_help, test_a_capability_for_the_wrong_kind_of_request_is_refused, test_nothing_a_person_would_be_asked_about_is_a_reflex, test_nothing_risky_is_a_reflex, test_the_model_is_only_offered_capabilities_it_could_use, test_a_capability_that_does_not_exist_is_refused, test_a_tuned_model_reporting_no_confidence_escalates, test_a_score_below_the_floor_escalates, test_confidence_alone_does_not_admit_anything, test_a_call_missing_its_arguments_escalates, test_an_empty_argument_is_a_missing_one, test_a_good_proposal_is_admitted, test_it_asks_the_model_and_never_lets_the_model_act, test_no_call_is_the_escalation_signal_not_a_failure, test_a_model_that_throws_costs_nobody_their_reply, test_it_is_not_a_planner, test_the_corpus_comes_from_the_registry, test_a_model_that_proposes_nothing_scores_zero_false_actions, test_the_deterministic_arm_needs_no_model, test_the_deterministic_arm_escalates_what_it_cannot_read, test_a_domain_is_read_without_a_model…
Exercises: agent_friday, friday

### `tests/test_registry_order_contract.py` (37 lines)

Covers: Registry order is the planner's shortlist priority - a behavioral contract, not an accident (RC1 finding #10). The defect this guards: 16 vnext (READ, SYSTEM) capabilities were appended mid-registry and crowded system_resource_usage out of the planner's 8-slot candidate shortlist, silently misrouting "check my computer" to system_battery. The planner tests caught one phrase; this test pins the CONTRACT: daily drivers must appear in the shortlist for their own shape, whatever gets added later.
Tests[3]: test_daily_drivers_stay_in_their_shortlists, test_daily_driver_phrases_still_route, test_operating_layer_sits_behind_daily_drivers
Exercises: friday

### `tests/test_release_gate.py` (185 lines)

Covers: The release gate's own honesty rules. `scripts/release_gate.py` is the one command that decides whether Friday is production-ready, so the ways it could LIE matter more than the ways it could crash. Each test here pins one rule from PRD §16/§20 that the gate exists to enforce.
Tests[13]: test_a_skipped_soak_is_not_a_pass, test_a_soak_that_ran_but_did_not_return_PASS_fails, test_the_soak_pass_states_its_detection_floor, test_a_pass_with_no_command_behind_it_fails_the_audit_gate, test_readiness_requires_every_mandatory_gate, test_the_report_records_host_load, test_a_low_disk_host_is_recorded_as_a_limitation, test_a_dirty_tree_is_named_in_the_report, test_the_report_carries_the_command_for_every_gate, test_not_ready_is_stated_plainly, test_without_live_it_says_so, test_live_actually_runs_the_live_suite, test_a_failing_live_probe_fails_the_gate

### `tests/test_remote_channel.py` (165 lines)

Covers: Remote channels (PRD v3.1 FR-040): an authenticated remote objective uses the same identity, policy and objective ledger as a local request. The UI server's /api/objective is the remote door. The face/PIN session gate is the identity (a request without a valid session is 423 before any handler runs); the objective toolset compiles into the same ledger a spoken request uses; the run is tagged `source_channel=remote:<channel>` and the running control plane drives it. Nothing about policy changes w
Tests[9]: test_unauthenticated_remote_request_is_refused_before_any_handler, test_authenticated_remote_objective_enters_the_same_ledger, test_remote_channel_name_is_sanitised_and_logged, test_empty_remote_objective_is_a_400, test_remote_policy_is_the_local_policy, test_a_remote_command_without_a_nonce_is_refused, test_the_same_remote_message_sent_twice_is_refused_the_second_time, test_a_stale_timestamp_is_refused_even_with_a_fresh_nonce, test_the_local_control_room_is_not_subject_to_replay_protection
Exercises: friday, friday.store, friday.toolsets

### `tests/test_repo_hygiene.py` (95 lines)

Covers: Repository hygiene invariants (audit A-007 / A-017; PRD Requirement 23). WHEN secrets are detected in tracked code or history, THEN production readiness SHALL fail unless the finding is a documented deliberate fake. WHEN a test uses fake secrets, THEN the allowlist SHALL be specific and auditable. The scanner's two exception files are the only places a finding can be waved through, so they are pinned here: every allow-listed path must exist and be a test or public asset, and the fingerprint igno
Tests[5]: test_the_gitleaks_ignore_list_is_exactly_the_one_rotated_key, test_the_ignored_finding_is_a_documented_rotation_not_a_live_secret, test_the_key_file_is_not_tracked_and_is_ignored, test_every_allowlisted_path_exists_and_is_a_test_or_public_asset, test_the_allowlisted_regexes_are_obviously_fake

### `tests/test_requirements.py` (531 lines)

Covers: Asking about an idea before trying to build it. Friday can read instructions and choose capabilities for them. An idea is not an instruction: "I want to make a game where you play a lighthouse keeper." There is no plan in that sentence and there should not be. What is missing is not capabilities, it is decisions, and they only exist in the boss's head. Three things can go wrong with asking, and there is a gate for each: asking about everything eleven thorough questions do not get answered. Small
Tests[41]: test_a_described_idea_is_recognised, test_an_instruction_is_not_an_idea, test_an_instruction_wearing_an_ideas_opening_words, test_a_small_reversible_choice_is_decided_not_asked, test_a_small_choice_with_no_proposal_is_still_asked, test_a_question_that_cannot_say_why_is_dropped, test_questions_are_bundled_rather_than_asked_one_at_a_time, test_nothing_to_ask_reads_as_ready, test_a_question_already_decided_is_not_asked_again, test_the_same_question_in_different_words_is_still_answered, test_a_genuinely_new_question_is_not_suppressed, test_an_answer_becomes_a_decision_that_survives, test_a_superseded_decision_stops_counting, test_a_weak_claim_is_challenged_not_echoed, test_a_concern_with_no_reason_is_dropped, test_no_provider_gives_an_empty_understanding_not_a_crash, test_an_idea_puts_its_questions_in_front_of_the_model, test_an_instruction_is_left_alone, test_an_idea_with_nothing_to_ask_says_nothing, test_a_failure_to_read_the_idea_does_not_cost_the_turn, test_an_admitted_objective_is_not_also_asked_about, test_a_reply_closes_the_questions_it_answers, test_an_unanswered_question_stays_open, test_an_answer_becomes_a_durable_decision, test_an_inferred_answer_is_recorded_as_inferred…
Exercises: agent_friday, friday, friday.store, friday.toolsets

### `tests/test_research.py` (361 lines)

Covers: Three web modes, so the fast path stays fast. `web_fetch` strips tags with a regex, which returns the nav bar, the cookie banner and the footer alongside the article. Acceptable for one page; useless for six, which is what research actually needs. Nothing here touches the network. The live proof is scripts/golden_research.py.
Tests[32]: test_extraction_keeps_the_article_and_drops_the_furniture, test_extraction_returns_markdown_not_a_wall_of_text, test_extraction_on_nothing, test_extraction_falls_back_to_the_title_tag, test_the_same_url_after_redirects_is_one_source, test_a_mirror_of_the_same_article_is_one_source, test_different_articles_both_survive, test_failures_are_kept_so_they_can_be_reported, test_relevance_prefers_the_page_that_talks_about_the_subject, test_relevance_ignores_short_noise_words, test_relevance_of_an_empty_question_is_zero, test_one_word_repeated_cannot_dominate_the_ranking, test_the_corpus_stays_inside_its_budget, test_dropping_for_budget_is_stated_never_silent, test_the_most_relevant_source_survives_the_budget, test_unreadable_pages_never_enter_the_corpus, test_crawling_nothing_fails_rather_than_succeeding_emptily, test_too_many_urls_is_refused_before_any_request_is_made, test_researching_nothing_fails, test_a_fresh_cache_hit_answers_without_touching_the_network, test_a_cache_miss_falls_through_to_the_normal_search, test_answering_nothing_fails, test_a_bad_url_is_reported_not_raised, test_the_default_engine_needs_no_extra_install, test_crawl4ai_is_opt_in…
Exercises: friday, friday.capabilities, friday.policy, friday.tools, friday.toolsets

### `tests/test_research_cache.py` (278 lines) [UNTRACKED]

Covers: Tests for friday/research_cache.py (FR-MEM-005). Every test points DB_PATH at a fresh tmp_path file and clears the workspace override before and after, so no test can see another test's cache or inherit another test's workspace - the same discipline tests/test_workspace_isolation.py uses for the main store. Freshness tests inject `now` rather than sleeping: two days old is a fact about the data, not about the clock.
Tests[21]: test_save_then_lookup_returns_the_artifact_with_sources_intact, test_lookup_of_a_never_saved_question_is_none, test_a_volatile_artifact_two_days_old_is_not_returned, test_a_stable_artifact_two_days_old_is_returned, test_differently_punctuated_and_cased_questions_hit_one_artifact, test_normalize_question_collapses_case_punctuation_and_word_order, test_classify_freshness_representative_questions, test_classify_freshness_defaults_to_weekly_when_nothing_matches, test_classify_freshness_hint_overrides_text, test_classify_freshness_bad_hint_falls_through_to_text, test_lookup_does_not_cross_workspaces, test_lookup_workspace_id_kwarg_overrides_the_ambient_workspace, test_stale_finds_an_expired_artifact, test_stale_does_not_list_a_fresh_artifact, test_invalidate_makes_a_fresh_artifact_stop_being_returned, test_invalidate_returns_false_for_an_unknown_key, test_invalidate_requires_a_reason, test_invalidated_reason_is_kept_on_record, test_prune_deletes_expired_and_leaves_fresh_artifacts, test_save_refuses_an_artifact_with_no_sources, test_create_refuses_an_unknown_freshness_class
Exercises: friday

### `tests/test_resilience.py` (421 lines)

Covers: When the model returns nothing. Gemini answers `finish_reason=STOP` with no content, LiveKit retries, and usually the second attempt is fine. The failure that matters is the other one: the tool already ran, the retries were spent, and the boss is left in silence after his music started playing. The rule underneath every test here: a rescue may never claim something an ActionResult does not support. Silence is better than a lie.
Tests[34]: test_the_empty_completion_is_recognised, test_the_exhausted_form_is_recognised_too, test_a_blocked_generation_is_not_an_empty_completion, test_a_real_api_failure_is_not_an_empty_completion, test_a_succeeded_tool_is_read_as_succeeded, test_succeeded_without_permission_to_claim_is_not_claimable, test_partial_is_not_success, test_an_unreadable_result_is_unproven_not_assumed, test_a_plain_json_result_is_read_too, test_everything_worked_says_so, test_some_of_it_worked_says_that_instead, test_nothing_worked_never_says_done, test_no_tools_at_all_is_treated_as_a_broken_turn, test_no_line_ever_names_a_tool, test_the_line_varies_so_it_does_not_sound_like_a_recording, test_the_boss_is_not_left_in_silence_after_something_worked, test_a_recoverable_failure_is_counted_but_not_spoken_over, test_a_turn_that_spoke_for_itself_is_never_rescued, test_an_empty_turn_with_no_tool_behind_it_stays_quiet, test_a_failed_tool_is_never_narrated_as_done, test_a_real_api_error_is_not_rescued, test_one_failure_is_rescued_once, test_the_guard_counts_what_it_saw, test_a_broken_tool_reader_never_breaks_the_session, test_a_session_that_cannot_speak_does_not_crash_the_guard…
Exercises: friday

### `tests/test_response_render.py` (186 lines)

Covers: No docstring; 13 test(s).
Tests[13]: test_speech_renderer_removes_markdown_tables_urls_and_code_across_chunks, test_inline_links_keep_the_label_without_reading_the_url, test_greeting_uses_context_and_does_not_repeat_after_reopen, test_late_night_greeting_restores_the_requested_paused_line, test_morning_greetings_pause_and_rotate_before_reuse, test_default_voice_rate_is_natural_and_still_configurable, test_acknowledgement_opening_does_not_repeat_within_previous_four, test_friday_tts_node_cleans_only_the_tts_stream, test_an_unbacked_spoken_claim_is_replaced_before_it_is_synthesised, test_a_spoken_claim_backed_by_a_real_action_is_spoken_unchanged, test_an_ordinary_spoken_answer_is_never_gated, test_a_plan_is_not_an_action_on_the_voice_path_either, test_the_contraction_the_live_model_used_is_caught_when_spoken
Exercises: agent_friday, friday, friday.response_render

### `tests/test_reversible.py` (250 lines)

Covers: Changing something and putting it back, provably. A live gate that sets the volume to 20, asserts it worked, and leaves it there has proved one thing and broken another - and reports only the first. Both halves, or it is not a pass.
Tests[17]: test_the_happy_path_records_all_four_observations, test_restoration_happens_even_when_the_control_is_never_read_again, test_a_control_that_does_not_exist_is_not_a_failure, test_a_refused_write_leaves_nothing_to_undo, test_a_write_that_is_accepted_and_does_nothing_is_refused_not_succeeded, test_failing_to_put_it_back_is_reported_loudly, test_a_control_that_cannot_be_read_back_assumes_the_worst, test_hardware_rounding_is_a_fact_not_a_failure, test_only_one_outcome_may_be_called_clean, test_the_summary_never_hides_a_machine_left_changed, test_nothing_that_did_not_change_claims_to_have_changed, test_a_human_changing_it_afterwards_is_not_overwritten, test_no_interference_still_restores, test_the_restore_runs_even_when_the_work_raises, test_a_target_that_disappears_is_not_a_failed_restore, test_a_device_that_invalidates_mid_transaction_is_target_gone, test_every_tidy_outcome_leaves_the_machine_as_it_was_found
Exercises: friday

### `tests/test_review_followups.py` (90 lines)

Covers: The independent review's medium/low items (2026-09-04): each one observable.
Tests[5]: test_strategy_hint_reaches_the_worker_arguments, test_development_run_carries_the_hint_into_its_constraints, test_hermes_role_section_names_the_claude_subagent, test_cooldown_file_torn_read_is_logged_not_silent, test_cooldown_save_is_atomic
Exercises: friday, friday.continuous, friday.objectives, friday.roles, friday.store

### `tests/test_roles.py` (209 lines)

Covers: Choosing specialists, and refusing to choose too many. The failure this guards is architecture cosplay: a hundred roles loaded into every run, none of which fit, all of which cost context.
Tests[27]: test_a_rename_is_trivial, test_a_bug_fix_is_small, test_a_feature_is_medium, test_architecture_is_large, test_a_measurement_beats_a_description, test_a_trivial_word_does_not_shrink_a_wide_change, test_a_trivial_change_gets_one_person, test_a_large_change_gets_more_but_still_not_a_company, test_no_run_ever_gets_the_whole_catalogue, test_security_words_field_a_security_reviewer, test_voice_words_field_the_voice_engineer, test_a_migration_fields_the_data_engineer, test_somebody_always_writes_the_code, test_an_unrecognised_goal_still_gets_somebody, test_work_past_trivial_gets_reviewed_without_being_asked, test_a_trivial_change_is_not_reviewed, test_the_context_budget_is_respected, test_a_tiny_budget_still_returns_somebody, test_every_chosen_role_has_a_stated_reason, test_the_team_serialises_for_the_run_record, test_instructions_are_scoped_text_not_a_runtime, test_every_role_has_triggers_and_a_deliverable, test_role_ids_are_unique, test_the_catalogue_stays_small_enough_to_choose_from, test_at_least_one_role_can_do_and_one_can_review…
Exercises: friday

### `tests/test_route_fitness.py` (199 lines) [UNTRACKED]

Covers: Capability fitness as a routing input, and the decision as a record (FR-MDL-006, PRD section 11). Two defects this covers, both of the same kind - a declaration with no consumer: * `ModelGatewayRequest.required_capabilities` was a field nothing read. A caller could ask for a route that supports tools, be given one that does not, and learn about it from the provider's error. * `friday/model_capabilities.py` declared vision, tools, structured output and context for every model it knew, and no prod
Tests[12]: test_a_route_declared_without_tools_is_refused_for_a_tool_using_request, test_a_route_declared_without_vision_is_refused_for_an_image_request, test_a_capable_route_is_kept_and_says_why, test_a_request_too_large_for_a_declared_context_is_refused, test_a_request_inside_a_declared_context_is_kept, test_an_undeclared_route_is_kept_and_the_reason_says_it_was_not_checked, test_the_two_kept_cases_are_distinguishable, test_an_unknown_required_capability_raises_instead_of_passing, test_the_decision_records_the_inputs_the_choice_and_every_rejection, test_the_decision_records_a_preference_when_the_caller_set_one, test_an_empty_route_list_produces_a_decision_with_no_chosen_route, test_removing_the_fitness_filter_would_let_the_incapable_route_through
Exercises: friday

### `tests/test_route_observability.py` (86 lines)

Covers: H0 route observability: every run carries provider/model/route_reason/ fallback fields durably, and old databases migrate additively. The spec's gate: "If you cannot observe this, H cannot pass yet." These tests make the observability itself regression-proof.
Tests[5]: test_new_database_has_route_columns, test_old_database_migrates_additively, test_update_accepts_route_fields, test_delegate_records_route, test_delegate_records_fallback_on_model_mismatch
Exercises: friday

### `tests/test_routing_memory.py` (107 lines)

Covers: The router must learn. It recorded outcomes for months and read none of them. Before 2026-09-01 `capability_router.search()` scored purely from static metadata, so a sentence mis-routed once was mis-routed identically forever, even after the boss had corrected it out loud.
Tests[6]: test_nothing_learned_is_no_opinion, test_a_correction_moves_the_next_route, test_one_outcome_is_not_a_habit, test_repeated_outcomes_become_a_prior, test_a_split_record_teaches_nothing, test_the_prior_reaches_the_router
Exercises: friday, friday.toolsets

### `tests/test_run_control.py` (57 lines)

Covers: No docstring; 3 test(s).
Tests[3]: test_status_reads_durable_state_without_chat_history, test_mutation_refuses_to_guess_between_multiple_runs, test_exact_run_control_never_changes_a_different_run
Exercises: friday, friday.store, friday.tools

### `tests/test_runcontext.py` (122 lines)

Covers: Picking the run a sentence means, and knowing how sure that makes us. The failure: a fresh session was asked "how did that product catalogue job finish?" and answered "do you have the run id?". He cannot. Friday invented it. The failure this exists to prevent next: "retry those" landing on whichever run happens to be newest, and reprocessing a catalogue nobody was talking about. A reader may act on a likely answer if it says which it picked. A mutator may not act on a guess at all.
Tests[12]: test_nothing_recorded_says_so_rather_than_guessing, test_one_run_needs_no_disambiguation, test_the_run_this_session_started_wins_over_the_newest, test_an_active_run_that_is_not_in_the_list_is_ignored, test_a_hint_that_fits_one_run_resolves_to_it, test_a_hint_that_fits_several_refuses_and_offers_them, test_a_hint_that_fits_nothing_falls_back_rather_than_failing, test_a_partial_run_id_is_a_hint_like_any_other, test_the_newest_of_several_is_readable_but_not_mutable, test_every_basis_that_permits_mutation_is_a_single_candidate, test_an_empty_run_id_is_never_safe_to_mutate, test_the_reason_is_never_empty
Exercises: friday

### `tests/test_runstate.py` (77 lines)

Covers: Splitting "is it still going?" from "how did it turn out?". The bug this exists to make unrepeatable: one `status: "PARTIAL"` field, and the model told the boss that a finished catalogue was "still processing, two done so far". Both readings of the word are reasonable. Only one field was carrying them.
Tests[7]: test_each_legacy_status_says_two_things, test_partial_is_finished_and_imperfect_not_half_done, test_the_store_overrules_the_word, test_an_unknown_status_is_not_reported_as_finished, test_over_means_nothing_more_will_happen_not_that_it_worked, test_every_contract_status_has_a_split, test_describe_is_the_two_fields_and_nothing_else
Exercises: friday

### `tests/test_runtime_authority.py` (167 lines)

Covers: What actually authorises a capability call, and what only looks like it does. Every capability the runtime can resolve takes `run` as its first parameter. That is a real invariant and `test_capability_reach.py` checks it - but it is a *structural* guard: it proves the dispatcher is able to hand over the right context, and nothing more. It says nothing about whether policy ran, whether the caller was allowed, or whether the evidence that came back belongs to the run the decision was made under. T
Tests[9]: test_the_runtime_supplies_a_run_when_the_caller_does_not, test_the_result_is_bound_to_the_authorising_run, test_a_capability_cannot_return_another_runs_result, test_arguments_cannot_smuggle_in_a_run, test_policy_runs_for_every_capability_the_runtime_dispatches, test_read_material_provenance_cannot_reach_a_destructive_capability, test_recycling_is_reachable_from_read_material_and_that_is_deliberate, test_the_signature_guard_is_not_claimed_as_authorisation, test_an_async_capability_is_awaited_before_it_is_checked
Exercises: friday

### `tests/test_runtime_control.py` (328 lines)

Covers: No docstring; 18 test(s).
Tests[18]: test_only_one_generated_speech_binds_to_a_claim, test_runtime_say_is_presentation_not_a_competing_owner, test_stale_speech_cannot_reserve_a_side_effect, test_revoked_speech_context_cannot_bind_itself_to_the_next_portion, test_identical_argument_objects_have_one_execution_key, test_successful_side_effect_reservation_cannot_execute_twice, test_running_and_unknown_attempts_are_not_dispatched_again, test_late_tool_result_cannot_settle_after_claim_checkpoint, test_guarded_mcp_tool_checks_owner_before_network_dispatch, test_tool_run_context_binds_owner_before_speech_observer, test_livekit_injects_run_context_into_guarded_mcp_tool, test_three_step_live_invocations_share_one_owner_without_observer_ordering, test_startup_profile_read_is_the_only_additional_ownerless_tool, test_guarded_mcp_tool_rejects_ownerless_model_calls, test_presentation_policy_classifies_known_and_unknown_tools, test_music_acknowledgement_plays_out_before_network_dispatch, test_semantic_milestones_ignore_phrasing_but_allow_a_new_task, test_repeated_verified_app_state_is_returned_without_repeat_narration
Exercises: friday.continuity, friday.runtime_control, friday.runtime_metrics, friday.store

### `tests/test_runtime_metrics.py` (96 lines)

Covers: No docstring; 8 test(s).
Tests[8]: test_snapshot_marks_missing_realtime_measurements_unmeasured, test_transcript_body_and_unsafe_fields_are_not_recorded, test_provider_history_shape_counts_are_safe_without_history_content, test_livekit_metrics_populate_phase_measurements_without_content, test_heartbeat_records_event_loop_stall, test_live_heartbeat_starts_and_closes_without_a_background_leak, test_objective_aggregation_preserves_tokens_and_success_ratios, test_comparison_does_not_invent_an_unavailable_baseline
Exercises: friday.runtime_metrics

### `tests/test_runtime_roots.py` (121 lines)

Covers: The invariant, enforced by launching Friday from somewhere else. NO SUBSYSTEM MAY DERIVE A PERSISTENT, PRIVILEGED OR SECURITY-SENSITIVE LOCATION FROM THE PROCESS WORKING DIRECTORY. CWD IS EXECUTION CONTEXT, NOT TRUSTED CONFIGURATION. Asserting `Path(X).is_absolute()` is not enough to hold this. An absolute path can still be *computed* from the working directory - `Path("data").resolve()` is absolute and wrong - so this starts real interpreters in four different directories, including the one Tas
Tests[2]: test_every_runtime_root_is_identical_wherever_friday_is_launched_from, test_the_probe_would_actually_catch_a_relative_path

### `tests/test_sandbox.py` (350 lines)

Covers: The execution boundary, proven rather than described. The claims worth testing are the ones that would otherwise be comments: a secret does not cross, a path does not escape, a process does not outlive the block, and a runaway does not take the machine.
Tests[34]: test_a_secret_does_not_cross_the_boundary, test_the_environment_is_an_allowlist_not_a_denylist, test_the_process_still_gets_what_it_needs_to_run, test_an_inherited_proxy_does_not_survive, test_injected_credentials_reach_the_process, test_injected_credentials_are_redacted_from_output, test_credentials_are_dropped_when_the_block_ends, test_the_report_names_credentials_without_carrying_them, test_deny_all_is_the_default, test_an_allowlist_permits_only_what_it_names, test_a_leading_dot_covers_subdomains, test_an_empty_allowlist_is_refused_rather_than_silently_denying, test_an_unknown_mode_is_refused, test_development_defaults_to_no_network, test_development_with_named_hosts_is_an_allowlist, test_a_path_cannot_climb_out, test_an_absolute_path_cannot_reach_out, test_an_absolute_path_is_refused_on_every_host, test_reading_and_writing_inside_is_fine, test_export_copies_one_named_file, test_export_refuses_a_path_outside_the_sandbox, test_export_refuses_something_too_large, test_a_missing_workspace_is_refused_before_anything_runs, test_a_command_runs_in_the_workspace, test_a_failing_command_reports_its_code…
Exercises: friday

### `tests/test_schedules.py` (242 lines)

Covers: Scheduled objectives and conditional monitoring (PRD v3.1 FR-041, FR-042). FR-041 acceptance: a scheduled task survives restart and records every execution. FR-042 acceptance: the no-noise test suppresses notification when the condition is false. Every firing here goes through the REAL objective engine (compile_objective + ContinuousTaskExecutor) against a real SQLite store; only the capability being called and `schtasks` are stand-ins.
Tests[11]: test_condition_false_runs_the_objective_but_delivers_nothing, test_condition_true_delivers_once_to_the_session, test_any_failed_condition_fires_only_on_failure, test_condition_evaluation_is_code_not_a_model, test_definition_survives_a_restart_and_records_every_execution, test_once_schedule_disarms_itself_after_firing, test_permissions_cannot_pre_approve_confirm_or_high_tier_tools, test_bad_definitions_are_refused_before_anything_is_stored, test_list_run_history_delete_faces, test_task_xml_is_well_formed_for_every_trigger_kind, test_tool_faces_are_wired
Exercises: friday, friday.store, friday.toolsets

### `tests/test_secret_broker.py` (93 lines)

Covers: Secret broker - the no-plaintext-anywhere contract. Promotion-blocking assertions: - raw value never appears in any model-facing return; - scratch file is shredded after ingestion; - store on disk is encrypted (raw value not greppable); - clipboard-shaped keys are dead on arrival; - machine path (env injection) works without exposing the value.
Tests[9]: test_ingest_returns_metadata_never_value, test_scratch_is_shredded, test_store_on_disk_is_encrypted, test_model_facing_surfaces_carry_no_value, test_machine_path_round_trips, test_empty_scratch_stores_nothing, test_remove, test_clipboard_key_is_dead, test_alias_traversal_is_refused
Exercises: friday

### `tests/test_selective.py` (341 lines)

Covers: A router that would rather say nothing than say the wrong thing. The deterministic router handled 23.7% of requests in 16ms and got 13.6% of them wrong, two dangerously. Those are not the numbers of a router that needs tuning; they are the numbers of a router that was never allowed to decline. What is proved here is the declining. Every rule below exists because a measured request got past its absence: "should I restart my PC?" a question that names an action "what if I shut down?" a hypothetica
Tests[29]: test_a_decision_routes_or_abstains_never_both, test_every_abstention_names_a_reason_and_a_place_to_look, test_uncertainty_is_kept_per_facet, test_a_question_about_an_action_never_becomes_one, test_asking_about_an_action_is_not_asking_for_it, test_thinking_is_never_a_reflex, test_a_sentence_that_only_forbids_produces_nothing, test_the_cue_is_found, test_the_cue_reaches_only_to_the_clause_boundary, test_a_broken_cue_would_fail_a_scoping_test_not_go_quiet, test_a_pronoun_with_nothing_behind_it_abstains, test_a_referent_this_conversation_established_grounds_it, test_two_live_things_make_the_referent_ambiguous_again, test_a_destructive_referent_is_never_guessed, test_being_the_only_candidate_left_is_not_evidence, test_matching_the_domain_is_not_matching_the_request, test_an_unread_verb_is_never_answered_by_a_read, test_a_clear_command_still_gets_through, test_identifying_a_capability_is_not_permission_to_run_it, test_nothing_that_ends_something_is_reflex_executable, test_a_reversible_low_risk_capability_is, test_more_than_one_outcome_is_not_a_reflex, test_it_stays_inside_the_latency_budget, test_a_polite_instruction_is_still_an_instruction, test_the_courtesy_is_stripped_before_anything_reads_it…
Exercises: friday

### `tests/test_self_upgrade.py` (94 lines)

Covers: Self-upgrade loop - staged, tested, rolled back; kernel refused. Uses a REAL throwaway git repo per test (not the working repo), so rollback behavior is proven against actual git operations.
Tests[6]: test_kernel_surface_is_refused, test_successful_upgrade_promotes, test_failing_tests_roll_back, test_failed_live_check_rolls_back, test_crash_in_apply_rolls_back, test_journal_records_the_whole_story
Exercises: friday

### `tests/test_selfdev.py` (446 lines)

Covers: Controlled self-development (PRD v3.1 FR-047, FR-048, FR-049, FR-050, FR-051). Every test runs against a REAL throwaway git repository so the sandbox, promotion and rollback claims are proven with actual git state, not with a mock that returns "promoted". FR-047 candidate links to measured evidence FR-048 main runtime unchanged until the promotion gate passes FR-049 promotion impossible while required tests fail FR-050 promotion record includes before/after; regression refuses FR-051 simulated p
Tests[21]: test_candidate_needs_measured_evidence, test_change_happens_in_a_sandbox_and_live_checkout_is_unchanged, test_kernel_surfaces_are_refused_before_any_sandbox_exists, test_a_change_outside_the_proposal_is_rejected, test_promotion_is_impossible_while_tests_fail, test_there_is_no_path_around_the_gates, test_regression_baseline_failure_rejects_after_review, test_a_disputed_independent_review_rejects, test_benchmark_regression_refuses_and_record_has_before_after, test_promote_merges_and_rollback_restores_prior_known_good, test_healthy_monitor_keeps_the_promotion_and_manual_rollback_still_works, test_every_transition_is_journaled_and_audited, test_rejected_candidates_keep_their_sandbox_as_evidence, test_toolset_runs_gates_promotes_on_approval_and_rolls_back, test_toolset_rejects_a_patch_that_does_not_apply, test_toolset_benchmarks_a_performance_claim_and_rejects_a_regression, test_toolset_records_no_claim_for_a_docs_only_change, test_benchmark_claims_and_failed_measurement_shape, test_every_judge_surface_is_kernel, test_ordinary_surfaces_are_not_kernel, test_a_proposal_naming_the_judge_is_refused_before_a_sandbox_exists
Exercises: friday, friday.toolsets

### `tests/test_semantics.py` (232 lines)

Covers: Routing by what a sentence asks for, not by which words it happens to share. The bug these gates exist for: "open Paint" and "what windows are open?" share every content word and mean opposite things, so a lexical scorer picks between them by accident. Five capabilities had been reworded to dodge collisions like that, and the rewording is what kept breaking - `automations_history` had gone so far as to avoid the word "run" in its own examples, and still lost to `product_runs` on "when did that a
Tests[17]: test_every_capability_has_an_operation_and_a_target, test_no_capability_gets_its_operation_by_falling_through, test_the_override_table_stays_small, test_a_question_and_an_instruction_read_differently, test_target_nouns_beat_application_names, test_semantic_routing, test_semantic_routing_excludes, test_a_wrong_guess_narrows_but_never_empties, test_corrupting_open_breaks_routing, test_collapsing_delete_into_mutate_breaks_routing, test_mislabelling_the_target_breaks_routing, test_removing_the_semantic_signal_breaks_routing, test_a_pure_capability_observes_nothing, test_reading_the_world_is_not_purity, test_an_object_dependent_verb_infers_no_operation, test_restarting_a_song_never_reaches_the_power_capabilities, test_a_question_gets_no_exact_operation_bonus
Exercises: friday, friday.tools

### `tests/test_sensitive_domains.py` (135 lines)

Covers: The banking/payment observation block, proven at its production seams. The rule (governance pack, docs/security/): page content from a financial domain never enters model context. The guard is `friday/sensitive_domains.py` and it is wired into `web.fetch`, `browser.open`, `browser.inspect`, `browser.automate` and the research crawler. Two kinds of test here: 1. the classifier itself - suffix matching, env extension, kill switch; 2. the wiring - the production call sites refuse *before* any netwo
Tests[11]: test_known_bank_is_sensitive, test_suffix_matches_on_label_boundaries_only, test_ordinary_sites_are_not_sensitive, test_user_extension_via_env, test_refusal_carries_the_marker_and_no_path, test_kill_switch_disables_the_guard, test_web_fetch_refuses_before_any_network, test_browser_open_refuses_before_launching_a_browser, test_browser_automate_refuses_a_blocked_start_url, test_crawler_refuses_a_blocked_url, test_browser_inspect_refuses_when_the_open_page_is_financial
Exercises: friday, friday.toolsets, friday.toolsets.research

### `tests/test_shadow.py` (486 lines)

Covers: Watching the router work, without letting it work. Every routing number Friday has comes from a corpus this project wrote. The store holds **four** real utterances, all long compound dictations from one test - so the distribution that decides whether local execution is safe does not exist yet, and the only place it exists is the boss talking to Friday. What is proved here: it cannot act by construction, and the counter that proves it it cannot be waited on - the row is queued, and the queue over
Tests[39]: test_off_shadow_and_direct_are_different_states, test_shadow_never_grants_the_reflex_permission_to_act, test_nothing_happens_when_it_is_off, test_the_old_flags_still_mean_something, test_a_prediction_has_no_way_to_execute, test_a_prediction_carries_no_runtime_or_principal, test_execution_attempts_stay_at_zero, test_reaching_for_execution_is_counted_and_refused, test_the_sentence_is_never_stored, test_only_argument_names_are_kept_never_values, test_the_fingerprint_is_stable_and_one_way, test_a_column_nobody_reviewed_cannot_be_written, test_comparison_says_what_happened_not_who_was_right, test_the_production_route_alone_is_the_weakest_label, test_strength_is_ordinal_not_a_guessed_probability, test_human_review_does_not_outrank_the_thing_actually_happening, test_a_verified_action_settles_execution_and_not_intent, test_a_machine_cannot_settle_what_the_boss_meant, test_a_correction_can_settle_intent, test_reliability_is_measured_not_asserted, test_a_verified_outcome_can_settle_correctness, test_a_route_that_failed_proves_nothing_about_the_route, test_every_pairing_has_a_neutral_name, test_a_label_with_no_provenance_is_refused, test_a_correction_is_recognised…
Exercises: agent_friday, friday, friday.store

### `tests/test_shared_brain.py` (116 lines)

Covers: Phase 2 — SharedBrainAdapter and the brain toolset. The narrow question this phase answers (boss): can Friday and Hermes share what they already learned and materially reduce rediscovery without weakening continuity or privacy? These are the unit-level guarantees; the live Golden Journeys are recorded in docs/architecture/GBRAIN_PHASE2.md.
Tests[10]: test_the_suite_never_writes_the_tracked_ledger, test_recall_always_carries_a_server_side_budget, test_budget_report_is_surfaced_not_recomputed, test_secret_shapes_are_refused_before_ingestion, test_banking_shapes_are_refused_before_ingestion, test_provenance_is_mandatory, test_admissible_fact_passes_through_with_entity, test_available_false_never_raises, test_toolset_recall_degrades_to_observed_miss, test_toolset_remember_refusal_is_a_failed_result
Exercises: friday, friday.toolsets

### `tests/test_silent_excepts.py` (136 lines)

Covers: Silent exception handlers must not grow. F6 from the 2026-09-01 audit. `friday/` has ~584 broad `except Exception` handlers, and a subset swallow the error entirely - no log, no re-raise, no recorded failure. That is why a broken dependency can look like a working one: the 500 from /api/state degrades gracefully *and* invisibly. Fixing all of them at once would be a large, risky diff across a live agent. This is the standing ratchet instead: the existing silent handlers are recorded as a baselin
Tests[5]: test_the_detector_recognises_a_silent_handler, test_a_handler_that_logs_is_not_counted, test_a_narrow_handler_is_not_counted, test_silent_exception_handlers_do_not_grow, test_the_baseline_is_not_stale

### `tests/test_skill_ladder.py` (62 lines)

Covers: Skill ladder + recovery packet - anti-pollution and resume-from-files.
Tests[8]: test_capture_without_criteria_is_refused, test_capture_without_evidence_is_refused, test_candidate_to_validated_skill, test_failed_validation_rejects_not_mutates, test_new_version_supersedes, test_deprecate, test_recovery_packet_round_trip, test_recovery_packet_caps_hard
Exercises: friday

### `tests/test_skills_extensions.py` (220 lines) [UNTRACKED]

Covers: The skill layer's later additions: plugins (installed packages, never files in a skill), the change-type classifier, usage statistics, the codebase fingerprint, and built-in skills that are adopted and upgraded without losing the owner's edits.
Tests[8]: test_plugins_are_discovered_without_import_and_loaded_only_when_allowed, test_plugin_shape_is_validated, test_change_type_picks_the_layer, test_registry_stats_are_rates_over_recorded_uses, test_git_identity_is_empty_without_git_and_filled_inside_a_repo, test_activation_stamps_the_codebase_fingerprint, test_builtin_adoption_and_upgrade_replace_untouched_and_merge_edited, test_the_shipped_friday_operate_skill_lints_and_its_check_runs
Exercises: friday.skills, friday.skills.evaluator, friday.skills.lifecycle, friday.skills.registry

### `tests/test_skills_golden.py` (433 lines) [UNTRACKED]

Covers: Golden skill journeys (docs/plans/skill-intelligence/00-status.md). SK-01 a verified, non-trivial objective yields a proposal; a trivial one does not SK-02 approve -> curate -> lint -> test -> ACTIVE, with evidence on record SK-03 a change to a declared dependency marks the skill NEEDS_REVALIDATION SK-04 a change elsewhere (README) leaves it alone; revalidation says FRESH SK-05 a fake secret: quarantined, never activated, never published SK-06 a new version whose test fails: the previous version
Tests[17]: test_sk01_a_verified_hard_objective_proposes_a_skill_and_a_trivial_one_does_not, test_sk01_the_ladder_refuses_without_a_criterion_and_the_detector_respects_it, test_sk02_approve_curate_lint_test_activate, test_sk03_a_dependency_change_marks_needs_revalidation_and_a_test_clears_it, test_sk03_a_broken_dependency_change_makes_the_skill_broken, test_sk04_an_unrelated_change_leaves_the_skill_fresh, test_sk04_git_reports_the_changed_paths, test_sk05_a_secret_quarantines_the_skill_and_blocks_publication, test_sk06_a_bad_new_version_never_replaces_the_active_one, test_sk06_a_good_new_version_is_versioned_and_snapshotted, test_sk07_specificity_decides_a_task_conflict_and_equals_are_surfaced, test_sk08_codebase_knowledge_is_never_published_publicly, test_sk08_public_needs_standing_authorization_or_an_approval, test_sk09_an_irrelevant_request_loads_no_skill_body, test_sk10_a_hostile_skill_cannot_disable_policy_or_trust_itself, test_sk10_an_agent_skill_in_a_high_risk_class_cannot_activate, test_pub01_private_git_publish_and_fresh_clone_discovery
Exercises: friday, friday.skills, friday.skills.dependencies, friday.skills.evaluator, friday.skills.lifecycle, friday.skills.registry

### `tests/test_skills_units.py` (335 lines) [UNTRACKED]

Covers: Unit tests for the skill layer's parts (the journeys are in tests/test_skills_golden.py): files, lint, registry, router, dependencies, curator, bridge, hermes link.
Tests[24]: test_frontmatter_round_trips_and_friday_metadata_is_additive, test_invalid_frontmatter_is_an_error_not_a_guess, test_discovery_follows_hermes_rules, test_supporting_files_stay_in_the_allowed_subdirectories, test_content_hash_covers_every_file, test_lint_names_every_shape_problem, test_lint_sees_secrets_hidden_text_and_private_data, test_lint_ignores_placeholders_and_binary_files, test_lint_scanners_work_on_drafts_without_files, test_risk_is_the_highest_signal_present, test_registry_refuses_bad_records_and_illegal_moves, test_registry_keeps_lists_versions_usage_and_history, test_an_unrecorded_or_mismatched_evaluation_is_no_evidence, test_router_scores_triggers_above_tags_above_description_and_vetoes_never_for, test_router_conflict_resolution_prefers_specific_then_newer, test_dependency_kinds_and_glob_matching, test_fingerprints_track_files_globs_and_packages, test_curator_strips_noise_and_makes_steps_imperative, test_curator_merges_near_duplicates_instead_of_creating, test_curator_rejects_hostile_drafts, test_bridge_extracts_facts_and_proposes_from_repeated_procedure_memories, test_hermes_link_plans_and_applies_without_losing_comments, test_hermes_link_creates_the_section_when_absent, test_signature_and_slug_are_stable
Exercises: friday.skills, friday.skills.registry

### `tests/test_soak.py` (332 lines)

Covers: A-051: the soak harness itself is under test (PRD §16 readiness gate). The 8-hour run is the gate; this proves the thing that produces the verdict cannot lie: - a short real run drives EVERY promised workload move against the real control plane (fakes only behind the provider and Hermes transports), reports zero violations and zero errored cycles, and says SMOKE - not PASS, which only a judged (>= 30 min) run may say; - the analyser calls monotonic growth on a series that never returns to its fi
Tests[20]: test_a_series_that_never_returns_is_monotonic_growth, test_a_sawtooth_is_not_a_leak, test_small_drift_under_the_floor_is_not_called_growth, test_warmup_is_excluded_from_the_first_window, test_a_short_run_is_smoke_never_pass, test_a_shed_worker_lifecycle_is_incomplete_never_pass, test_errored_cycles_and_violations_fail_the_verdict, test_a_real_short_run_drives_every_move_with_no_violations, test_the_short_run_cadence_fires_every_move_at_least_twice, test_a_long_run_keeps_the_rare_moves_rare, test_a_sleeping_host_is_inconclusive_never_pass, test_an_ordinary_run_reports_no_gaps, test_jitter_under_the_factor_is_not_a_gap, test_the_report_states_the_smallest_slope_it_could_detect, test_rss_is_attributed_to_the_move_that_caused_it, test_load_contention_is_not_a_sampling_gap_but_sleep_still_is, test_a_sampler_probe_that_raises_costs_one_series_not_the_sample, test_lost_telemetry_is_inconclusive_never_pass_and_never_a_crash, test_a_violation_still_fails_even_with_lost_telemetry, test_too_few_samples_still_reports_the_full_shape
Exercises: friday

### `tests/test_state_root.py` (231 lines) [UNTRACKED]

Covers: Where state lives, and where `.env` comes from (friday/config.py). FRIDAY_HOME wins everywhere frozen + portable beside the executable frozen %LOCALAPPDATA%\FRIDAY source checkout the repository root and the `.env` order the owner asked for: OS environment, then STATE_ROOT/config/.env, then beside the executable only in portable mode, then the repository only from source. A packaged build must never read the repository and a non-portable build must never read a file lying beside the executable. 
Tests[20]: test_from_source_the_repository_is_the_state_root, test_friday_home_wins_everywhere, test_a_packaged_build_keeps_state_under_localappdata, test_portable_mode_keeps_state_beside_the_executable, test_portable_mode_is_never_true_from_source, test_state_root_never_depends_on_the_working_directory, test_env_order_for_a_packaged_build, test_env_order_in_portable_mode, test_env_order_from_source, test_env_order_from_source_with_friday_home, test_only_the_first_existing_file_is_loaded_and_the_environment_wins, test_no_env_file_is_reported_not_raised, test_a_legacy_env_beside_the_exe_is_adopted_into_config_once, test_portable_mode_does_not_adopt, test_runtime_paths_are_all_strings, test_legacy_state_moves_out_of_internal_and_fixtures_stay, test_migration_never_overwrites_newer_state, test_an_explicit_home_receives_nothing, test_migration_is_a_no_op_when_the_roots_coincide, test_migration_is_a_no_op_from_source
Exercises: friday

### `tests/test_store_durability.py` (143 lines)

Covers: SQLite durability under the conditions Friday actually runs in (audit A-038): several processes on one file, and a process killed in the middle of writing. Real processes, real files - not mocks. The writer child is a separate Python interpreter running `friday.store.Store` against a temp database; the parent kills it with TerminateProcess while it is mid-transaction and then opens the same file and checks that every COMMITTED row is there, nothing half-written is, and the database passes `PRAGM
Tests[4]: test_store_opens_in_wal_with_a_busy_timeout, test_kill_during_write_leaves_a_consistent_database, test_two_processes_write_concurrently_without_lock_errors, test_a_plain_reader_sees_committed_rows_while_a_writer_holds_the_file
Exercises: friday

### `tests/test_store_serialized.py` (85 lines)

Covers: Every statement on the Store's one shared connection is serialised and materialised (A-051 soak finding: "database is locked" once in 1,435 cycles, from a product path). The shape, deterministic: a read on the shared connection whose cursor is not exhausted keeps its statement active - and an active statement holds the WAL read snapshot. ANY other connection then commits (another process, or the second `Store` in this process that `memory.store()` hands out for the same file). The next write on 
Tests[3]: test_the_bare_shape_is_the_bug, test_a_read_through_the_store_never_leaves_a_snapshot_open, test_rows_look_like_the_cursor_callers_use
Exercises: friday.store

### `tests/test_task_budget.py` (52 lines)

Covers: The adaptive execution policy in the TaskBundle. Guards the Phase 1G finding: the waste was reasoning BREADTH, not context size, and the cure must stay an information-value stop condition rather than a tool-call quota (a quota would overfit one benchmark and cripple investigations that honestly need twenty reads).
Tests[6]: test_policy_is_included_by_default, test_no_open_ended_completeness_clause, test_policy_permits_going_deeper, test_policy_is_not_a_call_quota, test_policy_can_be_switched_off, test_policy_keeps_the_bundle_small
Exercises: friday

### `tests/test_task_class.py` (132 lines)

Covers: Task complexity classification benchmark (PRD v3.1 FR-002). Acceptance: >=95% routing agreement on a labelled set, and trivial tasks never route to a coding executor. The labels below were written BEFORE the classifier was run against them (PRD 7.3: criteria precede execution). Every row is a realistic Friday request; the class is the one the PRD's own use-case table (3.3) and risk model (4.11) imply.
Tests[6]: test_every_class_is_represented, test_routing_agreement_is_at_least_95_percent, test_trivial_and_simple_never_reach_a_coding_executor, test_critical_wins_over_every_other_signal, test_classification_is_explainable_and_serialisable, test_empty_request_is_simple_not_an_error
Exercises: friday

### `tests/test_task_contract.py` (118 lines)

Covers: S1: the task contract, goal to Hermes prompt. `development.py` built a TaskBundle with no acceptance at all; Hermes checked acceptance to decide SUCCEEDED vs PARTIAL and never got any. These tests drive that whole chain and would have failed before the fix: an empty `acceptance` tuple, and a worker prompt with none of the contract sections.
Tests[5]: test_acceptance_reaches_hermes_from_a_development_run, test_worker_prompt_has_the_ten_sections_in_order, test_worker_prompt_names_the_claude_subagent, test_iteration_budget_is_rendered, test_empty_fields_render_no_empty_sections
Exercises: friday, friday.executors, friday.executors.claude_code, friday.roles

### `tests/test_temporal.py` (76 lines)

Covers: Temporal grounding: the agent must know what year it is. The failure: asked whether a business idea was viable "in 2026" while running on 16 August 2026, Friday replied "let me look into the future viability ... it's a bit of a projection". It reasoned from its training cutoff and treated the present as forecastable. get_current_time existed; nothing told the model it needed it.
Tests[8]: test_temporal_context_states_the_actual_date, test_temporal_context_says_the_year_is_not_upcoming, test_instructions_lead_with_the_date, test_instructions_still_contain_the_persona, test_the_prompt_names_the_failure_modes_it_forbids, test_the_prompt_tells_it_to_search_for_current_things, test_the_date_is_stamped_at_construction_not_hardcoded, test_get_current_time_is_still_available_for_precision
Exercises: agent_friday, friday

### `tests/test_transport_parity.py` (164 lines)

Covers: The same work, asked for three ways, must come out the same. Friday has three doors into a capability: the toolset function called directly, in-process the MCP adapter what the conversational model reaches the CapabilityRuntime what a durable objective reaches Before CORE-01 the third door did not exist for most capabilities, and before CORE-02B it did not exist for these. The risk once it does is subtler than an outright gap: two doors that both work and disagree. The MCP adapter used to hold t
Tests[5]: test_processing_a_catalogue_agrees_across_all_three_doors, test_the_counting_is_the_same_answer_not_merely_the_same_shape, test_only_the_runtime_carries_evidence, test_a_refusal_keeps_its_shape_on_every_transport, test_the_extracted_capabilities_run_from_a_durable_objective
Exercises: friday, friday.fsjail, friday.store, friday.tools, friday.toolsets

### `tests/test_trust.py` (256 lines)

Covers: The trust plane (PRD v3.1 FR-058, FR-059, FR-060, FR-061, FR-062, FR-063, FR-065). Adversarial by design: every test here is an attempt to get authority the policy did not grant, and the assertion is that the deterministic layer refuses without consulting anything a model could have written.
Tests[16]: test_every_policy_category_has_exactly_one_tier, test_representative_actions_map_deterministically, test_forbidden_tier_is_denied_and_not_approvable, test_an_adversarial_prompt_cannot_change_a_verdict, test_injected_page_content_cannot_reach_destructive_tools, test_injected_instructions_requesting_secrets_fail, test_approval_binds_to_one_action_one_target_one_argument_set, test_approval_expires, test_security_tools_are_disabled_without_a_contract, test_out_of_scope_host_and_action_are_blocked_even_when_the_tool_asks, test_contract_expiry_and_unbounded_scope_are_refused, test_fabric_refuses_a_security_call_outside_scope_and_audits_it, test_audit_answers_who_what_when_target_decision_result, test_audit_chain_detects_edits_and_deletions, test_policy_decisions_above_r0_auto_are_audited, test_audit_query_filters_by_minimum_tier
Exercises: friday

### `tests/test_turn_ownership.py` (443 lines)

Covers: The reply must not race the objective it just started. Reconstructed from a real failed voice run. The boss asked for four things in one breath. A durable run should have taken the work; instead the reply to that sentence executed all four itself: system_resource_usage -> system_list_processes -> apps_open -> web_search all under one LiveKit speech_id. That is four tool steps against a budget of three, so LiveKit did what it documents: one final request with tool use disabled. Gemini answered th
Tests[20]: test_the_compound_request_is_classified_as_an_objective, test_admission_logs_its_decision_either_way, test_the_reply_may_not_do_the_objectives_work, test_talking_about_the_objective_still_works, test_the_claim_lifts_when_the_work_is_finished, test_finished_work_is_still_owned_while_the_reply_is_in_flight, test_the_claim_expires, test_with_no_objective_running_nothing_is_claimed, test_the_reply_cannot_reach_the_tool_step_ceiling, test_a_second_objective_gets_its_own_task_graph, test_task_ids_are_unique_across_runs, test_dependencies_still_resolve_after_the_rename, test_an_admitted_turn_tells_the_model_the_work_is_taken, test_the_classification_is_logged_once, test_an_unowned_turn_gets_the_whole_surface, test_an_owned_turn_is_offered_no_domain_tools, test_ownership_does_not_depend_on_the_claim_still_being_live, test_a_zero_width_window_is_empty_even_at_age_zero, test_use_capability_is_refused_on_an_owned_turn, test_use_capability_still_reports_on_the_objective
Exercises: agent_friday, friday, friday.arbiter, friday.store, friday.toolsets

### `tests/test_turn_timing.py` (72 lines)

Covers: Latency attribution: a slow turn names its cause, on both voice paths.
Tests[5]: test_a_fast_turn_carries_no_note, test_a_slow_turn_names_the_dominant_stage, test_a_loaded_host_is_named_instead_of_the_work, test_livekit_stage_attribution, test_ui_reply_carries_latency_meta
Exercises: agent_friday, friday

### `tests/test_ui_server.py` (411 lines)

Covers: The UI server is a read-mostly VIEW over the live system. The guarantees that matter for a control room the owner will actually open: * it assembles every panel the brief names, in one snapshot; * it NEVER raises -- a down GBrain/Hermes/MCP or a missing database degrades to an "unavailable"/empty status, so the room opens on a fresh clone with an empty .env (acceptance #5); * it stores nothing (it is a view, not a second memory -- non-negotiable #11).
Tests[24]: test_build_state_assembles_every_panel, test_it_degrades_and_never_raises_on_a_missing_db, test_memory_search_is_summary_first_and_degrades, test_the_app_exposes_the_one_event_stream_and_apis, test_os_map_is_built_from_the_live_registry, test_memory_graph_is_real_and_renderable, test_org_is_loaded_from_the_upstream_not_typed, test_memory_feed_adapters_are_pinned_and_honest, test_ui_assets_are_served_from_the_ui_dir_only, test_api_work_returns_runs_objectives_and_digest_shape, test_memory_stack_aggregates_four_tiers_under_budget, test_memory_snapshot_is_cached_and_shaped, test_voice_brain_routes_commands_to_real_actions, test_voice_brain_does_not_import_providers_on_the_hot_path, test_state_carries_metrics_and_the_agency_roster, test_memory_flow_reports_writes_and_reads, test_harness_selection_is_honest_about_engine_state, test_browser_gate_lifecycle_binds_to_the_exact_action, test_browser_open_blocks_banking_before_any_launch, test_shot_path_refuses_traversal, test_http_surface_is_live, test_metrics_are_scoped_to_the_current_objective, test_cross_site_callers_cannot_reach_desk_or_stt, test_api_helpers_returns_providers_families_and_processes
Exercises: friday, friday.fabric_adapters, friday.store

### `tests/test_ui_server_skills.py` (15 lines) [UNTRACKED]

Covers: The control room's read-only view of the skill layer.
Tests[1]: test_api_skills_is_read_only_and_shaped
Exercises: friday.ui_server

### `tests/test_upstream_lock.py` (349 lines)

Covers: The upstream lock as an audit record. NON_NEGOTIABLE 6 - every upstream is untrusted until audited and pinned - is enforced for *registered* providers by `fabric.Provider.__post_init__`. That only covers upstreams someone has already written an adapter for. These tests cover the other end: everything the operator asked for is accounted for, pinned to something immutable, and carries a licence decision, whether or not an adapter exists yet. The distinction matters because the failure mode is sile
Tests[25]: test_every_requested_upstream_is_in_the_lock, test_the_new_set_is_exactly_the_requested_minus_the_build_pack, test_no_upstream_was_staged_without_being_requested, test_every_locked_upstream_is_pinned_to_an_immutable_commit, test_no_lock_entry_points_at_a_moving_ref, test_a_registered_provider_pin_matches_the_lock, test_every_locked_upstream_has_a_verified_licence, test_every_new_upstream_carries_a_decision_and_a_reason, test_no_new_upstream_is_marked_implemented, test_copyleft_is_never_proposed_in_an_importing_mode, test_the_proposed_mode_is_one_the_fabric_understands, test_restricted_subtrees_are_recorded_as_vendoring_blockers, test_a_split_licence_root_is_not_reported_as_its_carve_out, test_the_anythingllm_copyleft_subtree_is_flagged_in_the_lock, test_browser_use_is_reference_only_because_friday_already_has_a_browser, test_no_reference_only_upstream_has_a_registered_provider, test_friday_declares_exactly_one_browser_stack, test_an_implemented_upstream_reports_its_descriptors_mode, test_every_revised_decision_states_its_evidence, test_a_non_fabric_decision_has_no_fabric_provider, test_openhands_is_reference_only_because_it_is_a_control_layer, test_openhands_is_not_declared_as_an_executor, test_cline_is_declared_for_discovery_but_has_no_builder, test_hermes_is_the_engine_not_an_option, test_declaring_a_new_executor_does_not_change_who_gets_the_work
Exercises: friday, friday.executors

### `tests/test_user_policy.py` (107 lines)

Covers: User policy - delegation, revocation, constitutional immunity, audit.
Tests[13]: test_defaults_match_north_star, test_grant_then_act_then_revoke, test_constitutional_classes_cannot_be_granted, test_unknown_domain_rejected, test_versioning_never_destroys, test_snapshot_covers_all_domains, test_no_envelope_means_confirm, test_spend_inside_envelope_is_auto, test_exceeding_daily_cap_reverts_to_confirm, test_total_cap_depletes, test_expired_envelope_is_dead, test_envelope_creation_is_audited, test_negative_and_zero_amounts_never_auto
Exercises: friday

### `tests/test_vertical_slice.py` (165 lines)

Covers: The whole chain, once, on a real project. graph the repo -> choose a team -> work inside the boundary -> verify objectively -> pass the gate -> promote Each piece has its own tests. This is the one that proves they fit together, because a pipeline made of individually-correct parts is exactly how the typed-input bug happened: every component worked and nothing connected them. The coding agent here is a script rather than the Claude CLI. That is a real limit and it is stated rather than hidden - 
Tests[8]: test_the_whole_chain_promotes_verified_work, test_work_that_breaks_the_tests_is_not_promoted, test_work_that_wandered_out_of_scope_is_not_promoted, test_work_that_committed_a_secret_is_not_promoted, test_the_host_project_is_untouched_until_promotion, test_no_process_outlives_the_slice, test_the_agent_never_saw_a_provider_key, test_the_run_is_reportable_end_to_end
Exercises: friday

### `tests/test_voice_brain_ui.py` (444 lines)

Covers: The UI voice brain (friday/voice_brain.py) - the path behind the browser mic. The LiveKit agent got its fixes in tests/test_voice_pipeline.py; the boss talks to Friday through the browser when the machine is struggling, and that path has its own prompt, history replay and tool list. Both are covered now. Observed live (2026-09-02, UI path): - "search for me foreign market Trend" -> research/search -> skill NAMES -> "the research capability is failing". The UI brain had no web search. - that excu
Tests[30]: test_the_web_family_is_offered_to_the_model, test_web_search_needs_a_query_and_says_so, test_unknown_web_operation_lists_the_real_ones, test_web_search_runs_through_the_guarded_toolset, test_a_failed_search_is_an_error_not_an_invented_result, test_a_capability_excuse_is_recognised, test_a_real_answer_is_not_mistaken_for_an_excuse, test_replay_drops_her_excuses_but_keeps_what_the_boss_said, test_replay_never_opens_on_a_model_turn, test_persona_tells_her_history_is_not_evidence_and_to_retry, test_tool_description_routes_world_questions_to_web_not_research, test_desktop_family_is_offered, test_a_forbidden_task_is_refused_before_the_screen_is_read, test_a_step_without_an_approved_plan_does_nothing, test_stop_is_never_gated, test_a_plan_returns_a_nonce_and_touches_nothing, test_ui_voice_is_openai_nova_unless_opted_out, test_ui_and_livekit_agree_on_voice_and_speed_defaults, test_reply_speaks_even_when_the_tool_loop_ends_without_words, test_work_family_is_offered_with_a_status_op, test_work_status_reports_nothing_running_with_no_active_runs, test_whats_running_phrase_triggers_work_status_without_a_model, test_a_claim_with_no_tool_call_at_all_is_replaced, test_the_escalated_second_claim_is_replaced_too, test_a_claim_backed_by_a_family_that_acts_is_left_alone…
Exercises: friday, friday.tools.hermes_control, friday.toolsets, friday.toolsets.memory, friday.voice_brain

### `tests/test_voice_grounding.py` (109 lines)

Covers: "What's running?" and "what did Hermes finish?" come from the run ledger, never from the conversation (2026-09-04 17:12: "Hermes is still working" forty minutes after the ledger showed nothing active).
Tests[9]: test_finished_question_answers_from_the_ledger_not_the_chat, test_no_record_is_said_plainly, test_gate_and_test_runs_never_count_as_her_work, test_running_question_reads_the_work_log, test_nothing_running_is_said_plainly, test_what_did_you_just_do_is_not_hijacked, test_outcome_line_complete_with_handoff_summary, test_a_model_question_without_hermes_is_left_to_the_model, test_a_delegation_claim_without_the_tool_is_replaced
Exercises: friday, friday.tools.hermes_control

### `tests/test_voice_interruption.py` (201 lines)

Covers: Voice interruption and mute (PRD v3.1 FR-038, FR-039). FR-038 mute is a real input state: every mic muted -> audio input detached; unmute -> re-attached (VoiceInputGate, now attached in the entrypoint) FR-039 when interrupted, history reflects only what the user heard. LiveKit path: the framework stores the synchronized transcript with interrupted=True (livekit-agents 1.5.1 agent_activity). Browser-UI path: the page reports the heard prefix; the stored turn is truncated to it with an [interrupte
Tests[9]: test_store_truncates_a_reply_to_what_was_heard, test_history_given_to_the_model_reflects_the_interruption, test_reply_carries_the_message_id_the_page_needs, test_api_interrupted_truncates_through_the_ui_server, test_the_page_reports_the_heard_prefix_on_interruption, test_livekit_stores_only_the_synchronized_transcript_when_interrupted, test_session_interruptions_are_enabled_with_real_speech_threshold, test_mute_gate_detaches_audio_when_every_mic_is_muted_and_reattaches, test_mute_gate_is_attached_in_the_entrypoint
Exercises: agent_friday, friday, friday.runtime_metrics, friday.store, friday.toolsets, friday.voice_input

### `tests/test_voice_path.py` (147 lines)

Covers: Voice path: the loop from audio in to a verified tool call. Phases 0, 1A and 1B all shipped with "nothing has spoken to the agent". These tests close that. They are marked `live` because they make real provider calls (TTS, STT, LLM) - run the suite with -m "not live" to skip them. The round trip is the interesting part: the words the agent would *speak* are fed back into the ears it *listens* with, so both audio legs are exercised against real services without a microphone.
Tests[4]: test_tts_produces_real_audio, test_tts_stt_round_trip, test_stt_needs_an_http_session_outside_a_job, test_a_user_turn_reaches_a_verified_tool
Exercises: agent_friday, friday

### `tests/test_voice_pipeline.py` (320 lines)

Covers: The voice pipeline changes: TTS chunking and the acknowledgement rule. These are the two things the boss reported as broken in a live session: - "excessive pause before the next sentence" -> tts_node flush boundary - simple requests got no "I'm working on it" -> read-only ack exemption
Tests[18]: test_slow_reads_are_acknowledged, test_instant_reads_stay_silent, test_writes_are_always_acknowledged, test_the_acknowledgement_matches_the_work, test_an_unknown_capability_still_gets_a_line, test_the_first_sentence_is_flushed_immediately, test_later_sentences_are_batched_into_fewer_requests, test_no_word_is_lost_or_reordered, test_a_trailing_fragment_is_still_spoken, test_a_url_split_across_chunks_is_still_scrubbed, test_a_single_short_answer_is_not_held_back, test_a_plain_search_does_not_route_to_the_research_skill_family, test_genuine_methodology_requests_still_reach_the_research_family, test_research_skill_without_a_name_explains_instead_of_quoting_empty, test_search_reports_the_outage_rather_than_a_missing_capability, test_listing_areas_while_offline_does_not_answer_from_memory, test_listing_areas_reports_real_inventory_when_tools_are_present, test_room_path_speaks_a_milestone_and_a_timed_digest
Exercises: agent_friday, friday, friday.fabric_adapters

### `tests/test_windows.py` (393 lines)

Covers: Windows as things with observable state. The donor sends Win+Left and hopes. Everything here reads the window back afterwards, which is the only reason this batch can exist at all: a keystroke produces no observation, and the proof-of-work contract needs one. These use a fake window rather than the real desktop, deliberately. The suite runs constantly, and a test run that minimises the boss's Chrome window every time is a test suite that gets disabled. The real desktop is exercised once, by scri
Tests[25]: test_listing_reports_geometry_and_state, test_a_unicode_title_survives_being_returned, test_listing_can_be_narrowed, test_furniture_windows_are_not_listed, test_several_matches_is_a_question_not_a_choice, test_no_match_lists_what_is_open, test_a_pattern_is_a_substring_not_a_regex, test_focus_reports_the_window_it_actually_activated, test_windows_refusing_focus_is_reported_as_refused, test_a_refusal_that_still_left_it_foreground_is_a_success, test_focus_never_works_around_the_os_policy, test_focusing_a_minimized_window_restores_it_first, test_minimize_and_restore_are_reversible, test_maximize_is_verified, test_a_window_manager_refusal_is_partial_not_success, test_arranging_computes_the_rectangle_from_the_real_screen, test_every_side_lands_where_it_says, test_an_unknown_side_is_refused_with_the_list, test_a_maximized_window_is_restored_before_being_moved, test_a_window_manager_that_places_it_elsewhere_is_partial, test_arranging_without_a_known_screen_fails_rather_than_guessing, test_there_is_no_way_to_close_a_window_here, test_every_operation_is_declared_to_the_policy_engine, test_nothing_here_presses_a_key, test_off_windows_every_tool_is_unsupported_not_broken
Exercises: friday, friday.policy, friday.toolsets

### `tests/test_work_run_orphans.py` (103 lines)

Covers: Orphaned work runs and cross-process progress. 2026-09-04 16:08, first digest after a launcher restart: "did 0 tools, last: Hermes is reading the task - 0s in., on claude-opus-5 (default route), next: wrapping up; did 0 tools, ..." twenty times in one breath. Two defects: (1) a run whose owning process died stayed non-terminal forever, so every poll narrated it; (2) the progress ledger lived only in the memory of the process that ran the delegation, so the room and the control room (other proces
Tests[6]: test_sweep_marks_dead_and_ownerless_runs_lost, test_progress_reads_the_persisted_ledger_from_another_process, test_gather_sweeps_orphans_before_narrating, test_digest_caps_at_three_runs_and_counts_the_rest, test_tool_events_persist_the_ledger_for_other_processes, test_milestones_are_capped_like_the_digest
Exercises: friday

### `tests/test_worker_capacity.py` (62 lines)

Covers: Worker capacity: "at full capacity, marking as unavailable". `friday_voice start` runs in production mode, where LiveKit pre-spawns min(cpu_count, 4) idle job processes and refuses work above 0.7 CPU load. On one busy laptop that meant four extra Python processes AND a worker that declined jobs most of the time. Those defaults suit a fleet, not a desktop.
Tests[5]: test_idle_processes_are_not_the_fleet_default, test_load_threshold_is_raised_but_still_legal, test_worker_options_carry_the_tuning, test_both_settings_are_overridable_by_env, test_options_are_accepted_by_livekit
Exercises: agent_friday

### `tests/test_workspace_isolation.py` (398 lines) [UNTRACKED]

Covers: Workspace isolation, adversarially (FR-PRV-002, invariant 6, release gate 8). The PRD's trust metric is *zero* cross-workspace memory leakage, and the success criterion is not "the code has a workspace column" - it is that an attacker-shaped query from workspace B cannot produce workspace A's data. So these tests are written from B's side: plant something private in A, then try every read path B has, including the ones that do not take a subject. Two properties are load-bearing and easy to lose:
Tests[28]: test_an_illegal_id_is_refused_before_it_becomes_a_path, test_slug_is_deterministic_and_safe, test_current_prefers_override_then_env_then_recorded, test_use_restores_the_previous_workspace_even_when_the_body_raises, test_adopting_an_unregistered_workspace_is_refused, test_exactly_one_workspace_is_active, test_recall_cannot_cross, test_recall_scoped_cannot_cross_and_project_scope_is_no_escape, test_free_text_search_cannot_cross, test_export_cannot_cross_by_default, test_whole_install_export_is_opt_in_and_explicit, test_provenance_by_id_refuses_rather_than_returning_none, test_observations_cannot_cross, test_a_write_in_one_workspace_does_not_supersede_anothers_belief, test_forget_is_scoped, test_supersede_is_scoped, test_expire_is_scoped, test_the_guard_refuses_and_can_be_seen_to_refuse, test_an_unfiltered_query_really_would_leak, test_secret_stores_are_separate_key_material, test_a_pre_boundary_secret_store_is_adopted_not_orphaned, test_private_skill_tiers_are_disjoint_and_shared_tiers_are_not, test_a_browser_profile_approved_by_one_workspace_is_refused_to_another, test_the_users_own_chrome_profile_is_not_a_workspaces_to_withhold, test_every_table_is_declared…
Exercises: friday, friday.skills.files, friday.store

### `tests/test_worktrees.py` (380 lines)

Covers: Development happens somewhere else, and only arrives if it earns it. Real git repositories throughout - a worktree test that mocks git tests nothing, because every interesting failure here is git's. The invariant under all of it: **the main checkout is untouched until promotion**, and a rejection moves nothing at all.
Tests[31]: test_the_path_matches_what_the_cli_uses, test_the_branch_matches_what_the_cli_makes, test_a_worktree_is_found_once_it_exists, test_a_real_worktree_verifies, test_a_missing_worktree_does_not_verify, test_a_deleted_worktree_does_not_verify, test_a_directory_that_is_not_a_checkout_does_not_verify, test_resuming_without_a_worktree_is_refused, test_resuming_with_an_intact_worktree_is_allowed, test_work_in_the_worktree_does_not_touch_the_main_checkout, test_uncommitted_work_is_seen, test_the_runs_work_is_committed_before_it_can_move, test_committing_nothing_is_not_an_error, test_committing_into_a_missing_worktree_raises, test_promotion_moves_the_work_onto_the_target, test_promotion_records_where_to_get_back_to, test_promoting_nothing_is_a_rejection, test_promotion_refuses_to_switch_branches_underneath_anyone, test_a_conflicting_promotion_leaves_the_target_exactly_as_it_was, test_rejection_moves_nothing, test_a_rejected_worktree_can_be_kept_as_evidence, test_rollback_restores_the_state_from_what_was_recorded, test_rollback_reverts_rather_than_resetting, test_only_a_completed_promotion_can_be_rolled_back, test_cleanup_removes_the_worktree_and_the_branch…
Exercises: friday.executors, friday.store

### `tests/test_world_monitor.py` (209 lines)

Covers: The dashboard is a view, not a URL. What was there opened `https://worldmonitor.app/` - the marketing landing page - and returned "Displaying the World Monitor on your primary screen now, sir." It said that because `webbrowser.open` returned, which reports that a browser was launched and nothing whatever about where it went. Two separate failures in one sentence, and the second is the worse one: a statement about what is on the boss's screen, made by something that never looked.
Tests[15]: test_the_default_view_is_the_preset_exactly, test_the_landing_page_is_not_a_dashboard, test_a_view_survives_a_round_trip, test_meaning_is_compared_not_the_query_string, test_a_missing_layer_is_named, test_the_wrong_time_range_is_named, test_a_focus_narrows_the_layers_rather_than_adding_to_them, test_a_location_moves_the_viewport_off_global, test_a_time_range_nobody_defined_is_refused, test_a_dashboard_with_no_layers_is_refused, test_opening_it_reports_partial_and_says_which_half_is_missing, test_a_focused_request_reaches_the_browser_focused, test_a_browser_that_refuses_is_a_failure_not_a_success, test_it_is_reachable_from_a_durable_objective, test_reverting_to_the_landing_page_would_be_caught
Exercises: friday, friday.toolsets

### `tests/test_write_licence_agent.py` (152 lines)

Covers: The owner's words license a write; nothing Friday read does - on the LiveKit/MCP path (A-036 / PRD Req. 26, the twin of test_injection_pages). `FridayAgent.use_capability` is a function tool on the agent that dispatches any MCP capability by name. Under DANGEROUS autonomy (the default) the policy table answers AUTO for files_write and hermes_delegate, so before this gate a page the model read mid-turn was enough to write a file or create a WorkRun (probe, 2026-09-05: "delete all tests and force-
Tests[5]: test_a_page_cannot_turn_his_question_into_a_write, test_the_same_call_with_his_own_words_is_dispatched, test_reads_and_control_plane_are_never_licensed_by_words, test_the_owner_words_are_recorded_before_the_model_sees_the_turn, test_both_paths_share_one_table
Exercises: agent_friday, friday

## scripts/ — one section per file

### `scripts/__init__.py` (1 lines)

Covers: No docstring; 0 test(s).

### `scripts/baseline_suite.py` (139 lines)

Covers: The deterministic verification gate, as one cross-platform runner (audit A-014 / A-029): the suite in N chunks against the exact tree, one log per chunk plus a summary with the commit, the timestamp and the per-chunk result line. Windows first - no Bash, no MSYS process trees. python scripts/baseline_suite.py [--out data/baseline] [--chunks 4] [--python .venv-verify/Scripts/python.exe] [--timeout 600] [--chunk-timeout 3600] Exit code is 0 only when every chunk passed. Each chunk is a child pytes

### `scripts/benchmark_reflex.py` (277 lines)

Covers: LEGACY_REGRESSION_SET. Frozen. Not evidence that anything generalises. This corpus is Friday's own `intent_examples`, and it has now been read, re-read and inspected repeatedly across router development. It still detects regressions, which is worth keeping. It can no longer show that a new routing rule works on language nobody had in mind when they wrote the rule. For that, use `scripts/evaluate_selective.py` against the development, calibration and holdout splits built by `scripts/build_corpus.
Exercises: friday

### `scripts/build_corpus.py` (525 lines)

Covers: Build the DEVELOPMENT / CALIBRATION / HOLDOUT corpora for the selective router. Run: .venv/Scripts/python.exe scripts/build_corpus.py ## Why not reuse the 507 The existing benchmark is Friday's own `intent_examples`. It has now been read, re-read and inspected repeatedly across router development, so it can still detect regressions and can no longer be evidence that anything generalises. It is frozen as LEGACY_REGRESSION_SET and nothing here is derived from it. ## Where these come from, and the 
Exercises: friday

### `scripts/build_windows.py` (331 lines) [UNTRACKED]

Covers: Build FRIDAY.exe - the one reproducible command for the Windows package. .venv-build\Scripts\python.exe scripts\build_windows.py .venv-build\Scripts\python.exe scripts\build_windows.py --clean --skip-smoke Never from the live `.venv` (the running agent imports from it, and PyInstaller is deliberately not installed there). Create the build venv once with: set UV_PROJECT_ENVIRONMENT=.venv-build uv sync --extra dev --extra documents --extra web --python 3.11 uv pip install --python .venv-build\Scri

### `scripts/enrol_face.py` (159 lines)

Covers: Enrol the owner's face from a folder of photos -- headless, no windows. This is the training step for the face gate. It uses the SAME recognition model the live gate uses in the browser (face-api's ResNet descriptors), run in a headless Chromium via Playwright, so the descriptors it writes are the descriptors the camera will produce at unlock time. Training on one model and recognising with another is how face gates become unreliable; this avoids it. .venv\Scripts\python.exe scripts\enrol_face.p
Exercises: friday

### `scripts/evaluate_selective.py` (262 lines)

Covers: Measure the selective router: coverage, precision, and what it costs to be wrong. Run: .venv/Scripts/python.exe scripts/evaluate_selective.py development .venv/Scripts/python.exe scripts/evaluate_selective.py calibration --curve .venv/Scripts/python.exe scripts/evaluate_selective.py holdout --i-am-promoting Accuracy is not the number. These are: FALSE ACTION it acted, and the action was wrong DANGEROUS the wrong action ends something FALSE ABSTENTION it declined something it could have done - a 
Exercises: friday

### `scripts/fabric_code_intel_ab.py` (140 lines)

Covers: Does the code graph actually beat grep on real Friday questions? Run: .venv/Scripts/python.exe scripts/fabric_code_intel_ab.py The upstream advertises 120x fewer tokens. That is their number on their repositories; promotion here requires ours. So this asks the same five real questions two ways and measures what a model would actually pay: calls how many tool invocations before the answer is in hand bytes output the model has to read, which is the token bill seconds wall clock found whether the e

### `scripts/fabric_secret_isolation.py` (85 lines)

Covers: MCP_VERIFICATION_PLAN M6: prove no live secret VALUE reached a provider store. Run: .venv/Scripts/python.exe scripts/fabric_secret_isolation.py Reads the real values out of `.env`, asks each provider's own search surface for a 24-character prefix of each, and prints only the verdict. The values are never printed, never logged and never written to a file - which is the point, and is why this is a script rather than a pytest case with a fixture. A `clean` line means the provider's index does not c

### `scripts/fabric_upstreams.py` (343 lines)

Covers: Clone, pin and audit the 21 upstream repositories. Reproducible, not manual. Run: .venv/Scripts/python.exe scripts/fabric_upstreams.py clone .venv/Scripts/python.exe scripts/fabric_upstreams.py audit .venv/Scripts/python.exe scripts/fabric_upstreams.py status ## Why a script rather than 21 `git clone` lines The build pack asks for the same twelve facts about every repository - SHA, license, install path, tests, Windows compatibility, secrets, network, background processes, privilege, overlap, in

### `scripts/face_lbph.py` (150 lines)

Covers: The owner's original OpenCV/LBPH recogniser, upgraded -- and headless. What changed from the original two scripts: * BUG FIXED: the face crop was `gray[y:y+w, x:x+h]` (width and height swapped). It is `gray[y:y+h, x:x+w]`. The old model was trained on mis-cropped faces, which is one reason its confidence drifted. * No windows. `cv2.imshow` is gone; verify mode returns a result, it does not open anything (the control room rule: no Python windows). * Training data expands properly: `train <dir>` w

### `scripts/golden_1a.py` (140 lines)

Covers: Phase 1A golden journeys (§27). Exercises the system toolset against the real machine and prints the ActionResult for each step, so success is judged from verification evidence rather than from the absence of an exception. python scripts/golden_1a.py # read-only checks python scripts/golden_1a.py --apps # also opens and closes Calculator Exit 0 only if every journey produced a verified result.
Exercises: friday, friday.policy, friday.toolsets

### `scripts/golden_1b.py` (143 lines)

Covers: Phase 1B golden journeys (§27): web + browser. python scripts/golden_1b.py # search, fetch, news, browser python scripts/golden_1b.py --automate # also run browser.automate Exit 0 only if every journey produced a verified result.
Exercises: friday, friday.policy, friday.toolsets, friday.toolsets.system

### `scripts/golden_1c.py` (172 lines)

Covers: Phase 1C golden journeys (§27): files, inside a jail. python scripts/golden_1c.py Works in a temporary workspace so nothing real is touched, and finishes with adversarial escape attempts that must all be refused.
Exercises: friday, friday.fsjail, friday.policy, friday.toolsets, friday.toolsets.system

### `scripts/golden_1d.py` (268 lines)

Covers: Phase 1D golden journey (§13): memory must survive a full restart. The point of this script is that the phases run in **separate operating system processes**. A new Store object in the same process proves nothing about durability; a second `python.exe` reading what the first one wrote does. python scripts/golden_1d.py # write -> restart -> read -> agent python scripts/golden_1d.py --no-agent # skip the LLM turn (no spend) Phases, each its own process: 1 write store facts, a preference, an infere
Exercises: agent_friday, friday, friday.store, friday.toolsets

### `scripts/golden_1e.py` (168 lines)

Covers: Phase 1E golden journeys (§27): vision, on demand, without inventing anything. python scripts/golden_1e.py # screen + camera python scripts/golden_1e.py --no-camera # screen only Every capture is written to data/vision/ so any claim about what was seen has a retrievable image behind it. The camera is opened per call and released immediately.
Exercises: friday, friday.toolsets, friday.toolsets.system

### `scripts/golden_1f.py` (152 lines)

Covers: Phase 1F golden journeys (§27, §"FIRST RELEASE ACCEPTANCE"): Spotify. python scripts/golden_1f.py # includes real playback python scripts/golden_1f.py --read-only # no transport commands Playback state is restored at the end: if Spotify was paused when this started, it is paused again when it finishes.
Exercises: friday, friday.toolsets, friday.toolsets.system

### `scripts/golden_1g.py` (176 lines)

Covers: Phase 1G golden journey (§27): "Remind me in 2 minutes" — and verify it fires. Scheduling a reminder is easy to fake; the requirement is that it actually goes off. So this schedules one a short way out and then waits, polling the database until the fired flag flips. The flag is set by the script the OS scheduler runs, so seeing it flip means the operating system genuinely executed our task. python scripts/golden_1g.py # ~90s, waits for a real fire python scripts/golden_1g.py --no-wait # skip the
Exercises: friday, friday.store, friday.toolsets, friday.toolsets.reminders

### `scripts/golden_agentability.py` (680 lines)

Covers: Can Friday be *asked* for what it can do? Three different questions, and Friday needs all three answered: IMPLEMENTED the function works TOOL_VERIFIED calling it directly does the right thing AGENT_ROUTED asked in English, the model finds it and picks it over the plausible alternatives JOURNEY_VERIFIED and the whole multi-turn errand holds together The product tools scored 24/24 on the second and 2/17 on the third, on the same day, with no code between them. That gap is what this measures, and i
Exercises: agent_friday, friday

### `scripts/golden_audio.py` (209 lines)

Covers: Audio session control, on a session Friday created. The ownership invariant, applied: this never touches an application the boss started. It spawns a process that renders a tone, controls *that* session, puts it back, and kills it. Nothing else on the machine is a valid target for a test. The master volume is the exception - there is only one, and it belongs to the machine rather than to anybody. So it is changed by a few percent, checked, and restored through the same conflict-aware transaction
Exercises: friday, friday.toolsets

### `scripts/golden_autolearn.py` (179 lines)

Covers: Does Friday learn on its own, and does it remember on the next turn? The unit tests prove the machinery. This proves the thing that actually failed before: a real session, with a real model that was never told to file a memory, ends up knowing something it was not asked to store - and what it learned is in front of it next time it is asked. Drives the production text path (`text_input_callback`), not `session.run()`, because `session.run()` calls `generate_reply` directly and skips both hooks. R
Exercises: agent_friday, friday

### `scripts/golden_automations.py` (194 lines)

Covers: The automation engine against the real Windows scheduler. Unit tests can prove the graph, the retries and the boundary. They cannot prove the one thing that separates this from the donor design: that the trigger actually fires. Only `schtasks` can settle that, so this registers a real task, waits for the operating system to run it, and then reads the run out of the database from the *other* side - written by a different process than the one that created the automation. python scripts/golden_auto
Exercises: friday, friday.toolsets

### `scripts/golden_compound_turn.py` (185 lines)

Covers: The compound request, through the production seam, without a microphone. Reconstructed from a failed voice run. The boss asked for four things in one breath and the reply executed all four itself: system_resource_usage -> system_list_processes -> apps_open -> web_search four tool calls against LiveKit's budget of three, so LiveKit issued its final tools-disabled request, Gemini answered UNEXPECTED_TOOL_CALL, the fallback inherited the history and died on a missing thought signature. The provider
Exercises: agent_friday, friday, friday.provider_diagnostics, friday.toolsets

### `scripts/golden_continuity.py` (327 lines)

Covers: Kill it for real, then carry on. Test 7 and test 10 of the executor gate, with nothing mocked: * a multi-step task is started, allowed to change a file, then HARD-KILLED - not cancelled. The ADA process that was watching it goes too. * a brand-new ADA runtime opens the database it has never seen, finds the run as INTERRUPTED rather than FAILED, and continues it into the same Claude session. * a run whose Claude session has vanished is restarted from the task bundle instead of being lost. * askin
Exercises: friday, friday.executors, friday.executors.claude_code, friday.store

### `scripts/golden_continuous_run.py` (129 lines)

Covers: Safe deterministic journey: one objective continues without another prompt.
Exercises: friday, friday.continuity_livekit, friday.store

### `scripts/golden_corpus.py` (261 lines)

Covers: Generate docs/golden/objectives.jsonl - the Golden Objective corpus (PRD v3.1 7.2: >= 150 stable, replayable objectives; criteria written before execution). Cases are built from templates per category so the corpus is uniform, but every case is a concrete objective with concrete acceptance. Run: python scripts/golden_corpus.py # writes the corpus python scripts/golden_corpus.py --check # validates without writing
Exercises: friday

### `scripts/golden_executor.py` (239 lines)

Covers: ADA hands real work to Claude Code and stays in charge of it. Runs against a throwaway git repository, never this one. Nothing here touches the project, and the permission profile is the same one production uses. python scripts/golden_executor.py Costs real subscription usage - it starts actual Claude Code sessions.
Exercises: friday, friday.executors, friday.executors.claude_code, friday.store

### `scripts/golden_jarvis.py` (284 lines)

Covers: The local Jarvis journey, end to end. Every other golden script proves one capability. This one proves they hold together in a single conversation: open something, look at the machine, read a file, look at the screen, be told something worth keeping - then throw the whole runtime away and ask for it back. The restart is the point. Between the two phases the agent, the session, the router, the learner and the MCP server process are all destroyed and rebuilt. Only the database survives, which is e
Exercises: agent_friday, friday

### `scripts/golden_live_runtime.py` (141 lines)

Covers: Deterministic offline acceptance journey for the repaired live runtime.
Exercises: friday, friday.fsjail, friday.policy, friday.provider_fallback, friday.response_render, friday.runtime_control, friday.runtime_metrics, friday.store, friday.tools, friday.toolsets, friday.world_monitor

### `scripts/golden_music.py` (142 lines)

Covers: Music golden journey: "play this song" — and prove it actually played. python scripts/golden_music.py python scripts/golden_music.py --song "Mann Mera Gajendra Verma" Audio will play out loud. Everything is stopped at the end.
Exercises: friday, friday.toolsets, friday.toolsets.system

### `scripts/golden_power.py` (227 lines)

Covers: Power control against the real machine, without turning it off. python scripts/golden_power.py safe; the default python scripts/golden_power.py --cancel-journey initiates a REAL shutdown and calls it back python scripts/golden_power.py --lock actually locks the screen The default run touches nothing. It proves the parts that can be wrong in Friday: that availability is read from the right API, that the privilege really flips, that no power action moves without a yes bound to it, and that a yes f
Exercises: friday, friday.platform, friday.toolsets

### `scripts/golden_processes.py` (266 lines)

Covers: Closing and ending programs, against real windows this script created. The unit tests use fixtures because the suite runs constantly and a test run that kills the boss's editor every time is a test run that gets disabled. This is the other half: real windows, real WM_CLOSE, real TerminateProcess, real read-back - and it touches nothing it did not create. That rule is not decoration. An earlier live gate launched `notepad.exe`, matched a window by title, and moved somebody's open file around, bec
Exercises: friday, friday.toolsets

### `scripts/golden_product_agent.py` (336 lines)

Covers: The product journey with the model actually driving it. golden_product_mcp.py calls the six tools directly, which proves the surface. It does not prove the thing that matters in a conversation: that Friday *reaches* for product processing when asked, that "which products failed?" is answered by reading a recorded run rather than by doing the work again, and that a run found by id in a session which never saw it start is the same run. Four asks, one catalogue, and the database checked between eve
Exercises: agent_friday, friday, friday.store

### `scripts/golden_product_mcp.py` (279 lines)

Covers: Product processing through MCP, as the model would actually use it. The pipeline gate proves the pipeline. This proves the *integration*: that a run_id survives the process that created it, that "which products failed?" is answered by reading a recorded run rather than by doing the work again, and that a retry does not duplicate what already succeeded. The restart is real - the last section runs in a separate interpreter with no memory of anything, and has to find the run in the database. python
Exercises: friday.store, friday.tools

### `scripts/golden_products.py` (241 lines)

Covers: The product pipeline on a real catalogue, with the failures deliberately in it. Unit tests prove each journey in isolation. This runs one CSV containing all of them at once, because the question that matters commercially is not "does a missing image fail gracefully" - it is whether forty good products still come out the other end when six rows are broken in six different ways. The crash is real: a child process is killed mid-batch with taskkill, and the resume runs in a third process against the
Exercises: friday, friday.config, friday.store

### `scripts/golden_profile.py` (218 lines)

Covers: User-model golden journey: learn from how someone talks, then be corrected. Feeds real turns through the pipeline and shows what Friday concludes, what it refuses to conclude, and what happens when a later statement contradicts an earlier one. python scripts/golden_profile.py python scripts/golden_profile.py --offline # reconciliation only, no model
Exercises: friday, friday.store

### `scripts/golden_research.py` (217 lines)

Covers: Three web modes, live: fast, research, deep - and the agent choosing between them. The point of the split is latency. A question with a short factual answer must not pay for six page loads, and a research question must not be answered from a search snippet. This measures both and prints the numbers. python scripts/golden_research.py
Exercises: agent_friday, friday, friday.toolsets

### `scripts/golden_router.py` (137 lines)

Covers: Dynamic toolsets end to end: does the agent load what it needs, unprompted? Starts a real session, checks the tool surface narrowed, then asks for things that need groups the model cannot see and watches whether it enables them. python scripts/golden_router.py
Exercises: agent_friday, friday

### `scripts/golden_temporal.py` (122 lines)

Covers: Does Friday know what year it is, and does it research instead of projecting? Asks the exact question that exposed the bug and checks the reply for forecast-hedging language and for an actual web search. python scripts/golden_temporal.py
Exercises: agent_friday, friday

### `scripts/golden_voice.py` (240 lines)

Covers: Voice-path golden journey — closes the gap left open since Phase 0. Phases 0, 1A and 1B all ended with the same caveat: "nothing has spoken to the agent". Every capability was driven directly or over MCP, so the claim "voice still works" was inference, not evidence. This proves the whole loop without a microphone, by exercising each leg with real network calls: LEG 1 TTS text -> real audio samples LEG 2 STT those audio samples -> transcript (round trip) LEG 3 SESSION user turn -> LLM -> MCP tool
Exercises: agent_friday, friday

### `scripts/golden_windows.py` (177 lines)

Covers: Window operations against a real desktop, on a window Friday opened itself. The unit tests use a fake window on purpose: the suite runs constantly, and a test run that minimises the boss's Chrome window every time is a test suite that gets disabled. This is the other half - real Windows, real window manager, real read-back - and it touches nothing it did not create. Every check is the same shape, because it is the point of the whole toolset: do the thing, then ask the window what it became. The 
Exercises: friday, friday.toolsets

### `scripts/golden_workbench.py` (170 lines)

Covers: "Create a coffee shop website and show me." The asked-for version was a Sandpack preview embedded in Friday's chat UI. Friday has no chat UI - no package.json, no HTML, no frontend of any kind. So the preview surface is the browser he already uses, which is a better one anyway: it is the browser the site will actually be viewed in. generate -> validate -> serve on loopback -> open in HIS Chrome -> iterate python scripts/golden_workbench.py
Exercises: friday, friday.toolsets

### `scripts/golden_worktree.py` (303 lines)

Covers: Isolate, verify, promote or reject, roll back, clean up. The boundary that eventually lets ADA change its own code without the running checkout being the experiment. Real Claude Code runs, real git, throwaway repo. python scripts/golden_worktree.py Costs real subscription usage.
Exercises: friday, friday.executors, friday.executors.claude_code, friday.store

### `scripts/golden_youtube.py` (224 lines)

Covers: "Find Techno Gamerz on YouTube and review the channel." The request that started this: it failed because there were no YouTube tools at all, only a browser and a music searcher. This is the journey it should have had. Real network, throwaway database, no browser opened for anything the data already answers. python scripts/golden_youtube.py
Exercises: friday, friday.store, friday.toolsets

### `scripts/integration_matrix.py` (196 lines)

Covers: scripts/integration_matrix.py -- the integration status matrix, generated. `docs/integrations/THIRD_PARTY_INTEGRATION_MATRIX.md` documented 21 upstreams while 41 were cloned. It drifted because it was typed, and a typed table of 41 rows drifts again. `scripts/upstream_lock.py` already proves the pattern for pins; this does the same for integration status, joining three sources it can read rather than trusting a human to keep them aligned: clone directories third_party/upstream/* pins and licence
Exercises: friday

### `scripts/jarvis_overlay.py` (204 lines)

Covers: The arrow that lives on the screen. python scripts/jarvis_overlay.py --x 1830 --y 1059 --label "10:09 AM" A borderless, always-on-top, click-through window the size of the desktop, with one arrow drawn on it and everything else transparent. It dismisses itself. Why a separate process: Tk owns a main loop and wants the main thread, and the UI server has its own. A short-lived child cannot wedge the server, cannot leak a loop into it, and dies on its own if anything goes wrong here. Click-through 

### `scripts/keep_awake.py` (67 lines)

Covers: Keep this process's machine awake for the duration of an 8-hour soak. `SetThreadExecutionState` is the documented, non-elevated way to say "do not sleep while I am working" - the same call a video player makes. It binds to THIS thread, so it lapses the moment the run ends; nothing to restore, and no power-plan edit that outlives the soak. ES_AWAYMODE_REQUIRED is deliberately NOT set: away mode is for media playback and is refused on some systems. ES_SYSTEM_REQUIRED plus ES_CONTINUOUS is what a l

### `scripts/live_companion.py` (404 lines)

Covers: The Browser Companion against real Chrome. Twenty tests. Everything up to now proves the implementation and the protocol. It cannot prove that Chrome suspended the worker, revived it on an alarm, or refused an origin after it was revoked - Chrome is the runtime under test, and only Chrome can answer for it. python -m friday.companion.provision # once # load the unpacked extension, paste the token python scripts/live_companion.py Groups, and what each has to make real: TRANSPORT real Chrome <-> e
Exercises: friday.companion

### `scripts/live_page.py` (70 lines)

Covers: A harmless page the live gate fully controls, served on a fixed loopback port. example.com is a poor subject for proving the content-script link: nothing on it changes, so "click succeeded" and "the page reacted" look identical. This page has a button that provably mutates the DOM and a text field that can be read back exactly, which is what closes the chain Friday <-> socket <-> service worker <-> CONTENT SCRIPT <-> a real tab A fixed port because the granted origin has to be stable enough to a

### `scripts/livekit_chrome_gate.py` (246 lines)

Covers: Prove the LiveKit path works in a real browser, not just in a Python client. Run: .venv/Scripts/python.exe scripts/livekit_chrome_gate.py "Reply with exactly: FRIDAY_CHROME_OK" ## Why this is not the hosted playground `agents-playground.livekit.io` now redirects to `cloud.livekit.io/login`. That is the user's sign-in and their MFA, and it is a true stop boundary - measured, not assumed: Chrome lands on "Sign in | LiveKit Cloud" with the playground never rendering. What the playground *is*, thoug

### `scripts/livekit_probe.py` (184 lines)

Covers: Talk to Friday the way a user does: a real LiveKit room, over the real worker. Run: .venv/Scripts/python.exe scripts/livekit_probe.py "Reply with exactly: FRIDAY_DIRECT_OK" .venv/Scripts/python.exe scripts/livekit_probe.py --room L3 --wait 180 "<a long objective>" .venv/Scripts/python.exe scripts/livekit_probe.py --room L3 --follow "steer text" --wait 120 ## Why this exists The hosted playground at agents-playground.livekit.io now redirects to a LiveKit Cloud sign-in, which is the user's action 

### `scripts/measure.py` (161 lines)

Covers: measure.py - resource + startup baseline for the Friday stack. Launches the whole app via start.py, times the startup milestones, lets it settle, samples the process tree, then tears it down and prints JSON. python scripts/measure.py --label before --out docs/phase0/metrics-before.json python scripts/measure.py --label after --out docs/phase0/metrics-after.json Pure stdlib. Windows process sampling shells out to PowerShell CIM because psutil is not a project dependency and this is not worth one.

### `scripts/module_map.py` (40 lines)

Covers: Print a one-line summary (first docstring sentence + LOC) per module. Usage: python scripts/module_map.py [package_dir ...] (default: friday) Writes docs/architecture/MODULE_MAP.md and prints it.

### `scripts/new_upstream_set.py` (546 lines)

Covers: Compute REQUESTED - ALREADY_STAGED and audit whatever is left over. Run: .venv/Scripts/python.exe scripts/new_upstream_set.py --plan .venv/Scripts/python.exe scripts/new_upstream_set.py --audit The set difference is computed rather than recalled. A list of eight repositories typed from memory is a list nobody can check; deriving it from the requested URLs and the directories actually present under `third_party/upstream/` means a normalisation mistake shows up as a wrong count instead of as a qui

### `scripts/perf_profile.py` (323 lines)

Covers: Performance measurements for PRD v3.1 section 5 (NFR-P01..P13) and the 1.5 KPI table - measured on the exact build, on this machine, and written with the 7.3 provenance block. Anything that needs a live voice session or a person is reported as NOT_MEASURED with the reason rather than estimated. python scripts/perf_profile.py [--out data/perf/latest.json] [--idle-seconds 30] Measured here: NFR-P06 memory retrieval P95 (local scoped retrieval, 2,000 memories) NFR-P07 simple local action end to end
Exercises: friday, friday.model_gateway, friday.store, friday.toolsets

### `scripts/prd_ledger.py` (1272 lines) [UNTRACKED]

Covers: The PRD requirement ledger: one authored table, three generated artifacts. python scripts/prd_ledger.py # regenerate everything python scripts/prd_ledger.py --check # fail if a terminal row lacks evidence python scripts/prd_ledger.py --status ID=ST --evidence ID="..." # update a row Artifacts --------- data/prd_requirements.json machine-readable ledger docs/implementation/PRD_REQUIREMENTS_LEDGER.md human-readable ledger data/prd_final_verdict.json the roll-up verdict Why the table lives in this 

### `scripts/probe_model_gateway.py` (59 lines)

Covers: Manual probe of the model-gateway worker against the real Hermes install.

### `scripts/probe_power.py` (235 lines)

Covers: What this machine actually offers for power control. Nothing is ever initiated. Phase 0 research for specs/001-process-power-control. Three of the findings contradict what the documentation implies, which is why this exists as a script rather than a paragraph: SeShutdownPrivilege held, but DISABLED - every power call fails until AdjustTokenPrivileges enables it, and that function returns non-zero even when it enabled nothing IsPwrSuspendAllowed answers about S1-S3. On a modern-standby machine it

### `scripts/promotion_1f.py` (243 lines)

Covers: Phase 1F manual-promotion gates F1-F5, driven through the production Friday path: real FridayAgent, real LLM, real MCP over SSE (server.py), real Hermes gateway (friday profile), real WorkRun log. Each gate is a separate function; state (timings, ids) is printed as the promotion record. DO NOTHING between send and result - no Continue, no status queries.
Exercises: agent_friday, friday

### `scripts/release_gate.py` (467 lines)

Covers: The single release gate: every mandatory PRD §16 readiness check, one command, one reproducible report. python scripts/release_gate.py # everything but the soak python scripts/release_gate.py --soak-hours 8 # including the 8 h gate python scripts/release_gate.py --out data/release # where the report lands Exit code is 0 only when every MANDATORY gate is PASS. A gate that could not run is never PASS. Why this file exists, and what it refuses to do ----------------------------------------------- P

### `scripts/restart_friday.py` (204 lines)

Covers: Restart Friday's two processes and prove they are running the current code. Run: .venv/Scripts/python.exe scripts/restart_friday.py .venv/Scripts/python.exe scripts/restart_friday.py --check ## Why this exists rather than "just restart it" Friday is two processes and they fail differently: server.py serves the MCP tools. The agent talks to it over SSE, so a stale one means the model never sees a new capability however correctly it is registered. agent_friday.py the LiveKit worker. `dev` mode wat
Exercises: friday

### `scripts/run_ui.py` (126 lines)

Covers: Launch the Friday UI server -- the read-mostly web control room. A SEPARATE process from server.py, so it never destabilises the live agent. Starting it opens Friday full-screen in Chrome as soon as the server answers; the page holds a boot screen until the recognition model is in memory, so the camera never pretends to be scanning while it is still loading. .venv\Scripts\python.exe scripts\run_ui.py .venv\Scripts\python.exe scripts\run_ui.py --no-browser .venv\Scripts\python.exe scripts\run_ui.

### `scripts/seed_demo_db.py` (63 lines)

Covers: Create a fresh Friday database (audit A-001: the live `data/ada.sqlite3` is runtime state and is no longer tracked; the schema lives in code). python scripts/seed_demo_db.py # data/ada.sqlite3 if absent python scripts/seed_demo_db.py --demo # + a few demo rows python scripts/seed_demo_db.py --path x.sqlite3 # elsewhere python scripts/seed_demo_db.py --force # replace an existing file The schema is `friday.store.SCHEMA` plus the additive migrations in `friday.store._ADDED_COLUMNS`; opening a `Sto
Exercises: friday

### `scripts/soak.py` (827 lines)

Covers: A-051 soak: a workload-driven endurance run against the REAL control plane. Not "sleep for eight hours". Every cycle drives the production code paths a day of use would - objectives created and completed, provider success and failure through the real ModelGateway, budget pause/resume, the Hermes supervisor started/stopped/crashed/restarted, DB writes, the scheduler's at-most-once claim, remote-nonce traffic, context handoffs through the promotion gate, and a worker process killed mid-task and re
Exercises: friday, friday.continuous, friday.store

### `scripts/third_party_notices.py` (136 lines)

Covers: Generate THIRD_PARTY_NOTICES.md from the upstream lock. MIT asks that its copyright notice travel with the work. Apache-2.0 asks that NOTICE be retained. AGPL-3.0 asks considerably more: anyone the software is offered to over a network is owed its source. None of that is satisfied by a lock file buried in third_party/, so this writes the notice a reader can find. Generated, never typed: every fact here is read from third_party/UPSTREAM_LOCK.json, which is itself derived from the clones. Re-run i

### `scripts/upstream_lock.py` (458 lines)

Covers: Generate third_party/UPSTREAM_LOCK.json from the pinned clones themselves. Run: .venv/Scripts/python.exe scripts/upstream_lock.py .venv/Scripts/python.exe scripts/upstream_lock.py --check NON_NEGOTIABLE 6 says every upstream is untrusted until audited and pinned, and `fabric.Provider` enforces the pin half of that at construction. This script is the other half: it reads the commit, the tag and the actual LICENSE text out of each clone under `third_party/upstream/`, so the lock is derived evidenc
Exercises: friday

### `scripts/validate_exe.py` (339 lines) [UNTRACKED]

Covers: Drive the built FRIDAY.exe through its golden journeys (docs/build/WINDOWS_EXE.md). .venv-build\Scripts\python.exe scripts\validate_exe.py .venv-build\Scripts\python.exe scripts\validate_exe.py --exe dist\FRIDAY\FRIDAY.exe --with-agent Tests the artifact, never the source tree: every step runs dist\FRIDAY\ FRIDAY.exe as a user would. Journeys: EXE-00 preconditions: the exe exists, :8000 and :8770 are free EXE-01 --version answers with the stamped commit EXE-02 --doctor bootstraps the database an

### `scripts/validate_exe_clean.py` (208 lines) [UNTRACKED]

Covers: Run the built FRIDAY.exe as a machine that has never seen this repository. .venv-build\Scripts\python.exe scripts\validate_exe_clean.py Windows Sandbox is not installed on this host (and enabling it needs elevation and a reboot), so this is the next best thing, and it catches the same class of defect - a hidden dependency on the developer's machine: - the bundle is COPIED to a directory on another drive (the system temp on C:), so no path inside it can reach the checkout on F:; - PATH is reduced

### `scripts/verify_all.py` (414 lines)

Covers: Run every capability TWICE and report what actually works. Once proves it can work. Twice separates "works" from "worked that time" - and flaky is the failure mode that has bitten this project hardest, so it gets its own verdict rather than being averaged away. python scripts/verify_all.py # read-only + reversible python scripts/verify_all.py --full # also camera, music, reminders python scripts/verify_all.py --runs 3 # more repetitions Verdicts: PASS both runs verified FLAKY one run verified, o
Exercises: friday, friday.fsjail, friday.store, friday.toolsets

### `scripts/verify_hermes_engine.py` (151 lines)

Covers: Independent validation of HERMES_EXECUTION_ENGINE (PRD: FRIDAY delegates serious coding work to Hermes; final gate item 5). Starts a Hermes gateway under Friday's own `HermesSupervisor` (the production seam - not a shortcut), delegates ONE bounded coding task into a scratch git repository via `TaskBundle`, waits for the turn, and then checks the OUTCOME on disk (file content, git status) rather than trusting the agent's completion text. Writes the evidence with a provenance block. python scripts
Exercises: friday

### `scripts/verify_mcp.py` (116 lines)

Covers: verify_mcp.py - prove the MCPToolset migration did not lose any tool. Builds the toolset exactly the way agent_friday does, connects to the running MCP server, and checks that every tool declared in friday.capabilities is still exposed. Starts the MCP server itself if nothing is listening. python scripts/verify_mcp.py Exit 0 = every declared tool is reachable through MCPToolset.
Exercises: agent_friday, friday
