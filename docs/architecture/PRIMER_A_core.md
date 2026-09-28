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
