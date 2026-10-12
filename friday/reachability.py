"""
Whether a feature can actually be reached by a person using Friday.

    IMPLEMENTED + TESTED != PRODUCT FUNCTIONAL.

That sentence is the whole module, and it was earned three times in one
session:

  1. Typed input called `prepare_turn` and skipped the rest of the answer
     pipeline. Research, claim-checking and project capture existed and were
     unreachable for the way Friday is actually used.
  2. `sandbox`, `codegraph`, `evaluation`, `promotion` and `roles` shipped
     with 120 passing tests and no production caller at all.
  3. `record_decision` and `add_requirement` were complete, tested, and
     called only from tests and one golden script - the read side of project
     memory worked and the write side was wired to nothing.

Every one of those was green. None of them worked. So "tests pass" is
retired as a completion gate and replaced with a depth: how far along the
chain from *defined* to *verified in a real journey* a feature has actually
got.

    DEFINED               the code exists
    REGISTERED            something declares it
    DISCOVERABLE          a production caller could find it
    PRODUCTION_REACHABLE  a real chain exists from a real entry point
    EXECUTED              it has run outside a test
    OBSERVED              its effect was seen from outside
    REAL_JOURNEY_VERIFIED a person's journey through it was checked

Cumulative, and the top three cannot be proven by a unit test. This module
computes up to `PRODUCTION_REACHABLE` - the rest needs a live run and is
recorded rather than derived.

## What counts as reachable

Not test imports. That is the entire point: every defect above had tests
calling the code directly, and if a test import counted, all three would have
been reported reachable while the product was broken.

Not registry presence either. Registration proves registration. MCP exposure
proves transport. A capability in the registry that no production routing
ever selects is registered and unreachable.

What counts is a chain of references from a declared production entry point.
Entry points are declared rather than inferred, because "this is where a user
turn begins" is an architectural fact that no static analysis can discover.

## The bias, stated

Reference edges here are deliberately over-inclusive: a name mentioned
anywhere counts, including property reads, decorators, base classes and
identifier-shaped strings. That makes false *reachable* likely and false
*dead* unlikely, which is the correct direction. A false reachable costs
somebody a look; a false dead costs deleted production code - which is not
hypothetical, it is what happened to `Reflex.acts` earlier today on the
strength of a call-graph that could not see property reads.

So `UNREACHABLE` here means "no path was found", never "there is no path".
Nothing in this module may be used on its own to justify a deletion.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

logger = logging.getLogger("friday-agent")

# ---------------------------------------------------------------------------
# Depth
# ---------------------------------------------------------------------------

DEFINED = "DEFINED"
REGISTERED = "REGISTERED"
DISCOVERABLE = "DISCOVERABLE"
PRODUCTION_REACHABLE = "PRODUCTION_REACHABLE"
EXECUTED = "EXECUTED"
OBSERVED = "OBSERVED"
REAL_JOURNEY_VERIFIED = "REAL_JOURNEY_VERIFIED"

#: In order. A feature reports its highest *verified* depth, and the ordering
#: is what makes "at least PRODUCTION_REACHABLE" a checkable statement.
DEPTHS = (DEFINED, REGISTERED, DISCOVERABLE, PRODUCTION_REACHABLE,
          EXECUTED, OBSERVED, REAL_JOURNEY_VERIFIED)

#: The line below which a user-facing feature is not done.
MINIMUM = PRODUCTION_REACHABLE

#: What a unit test can prove on its own. Anything past this needs a real run.
PROVABLE_BY_TESTS = DISCOVERABLE


def at_least(depth: str, minimum: str) -> bool:
    """Whether `depth` is `minimum` or deeper."""
    try:
        return DEPTHS.index(depth) >= DEPTHS.index(minimum)
    except ValueError:
        return False


# ---------------------------------------------------------------------------
# Entry paths
# ---------------------------------------------------------------------------

VOICE_USER = "VOICE_USER"
TEXT_USER = "TEXT_USER"
MODEL_TOOL_CALL = "MODEL_TOOL_CALL"
OBJECTIVE_EXECUTOR = "OBJECTIVE_EXECUTOR"
AUTOMATION = "AUTOMATION"
DEVELOPMENT_RUN = "DEVELOPMENT_RUN"
BACKGROUND_SYSTEM = "BACKGROUND_SYSTEM"

ENTRY_PATHS = (VOICE_USER, TEXT_USER, MODEL_TOOL_CALL, OBJECTIVE_EXECUTOR,
               AUTOMATION, DEVELOPMENT_RUN, BACKGROUND_SYSTEM)

#: Where production actually begins, declared.
#:
#: These are architectural facts. No analysis can work out that
#: `text_input_callback` is where a typed message enters - it looks like any
#: other function - and getting this list wrong is the one way this module
#: can be confidently wrong, so it is small, explicit and commented.
ENTRY_POINTS: dict[str, tuple[str, ...]] = {
    # STT finished, the model has not started.
    VOICE_USER: ("on_user_turn_completed",),
    # A typed message over the room's chat topic. Listed separately from
    # voice *because* they were not the same pipeline, and a single combined
    # entry would have hidden exactly that.
    TEXT_USER: ("text_input_callback", "on_text"),
    # The model invoking a capability. How most of the 127 are reached.
    MODEL_TOOL_CALL: ("use_capability", "search_capabilities"),
    # A durable objective's own execution.
    OBJECTIVE_EXECUTOR: ("ContinuousTaskExecutor", "start_objective_engine",
                         "run_objective", "execute"),
    AUTOMATION: ("automations_run", "automation_tick"),
    # for_request is the one door (CTX-003): it compiles, then reaches for_goal.
    DEVELOPMENT_RUN: ("DevelopmentRun", "for_request"),
    # Things with their own lifecycle: the learner, the world monitor, the
    # shadow writer. A background service with no lifecycle owner is one of
    # the shapes this audit looks for.
    BACKGROUND_SYSTEM: ("on_enter", "start", "settle_power_requests",
                        "entrypoint"),
}

# ---------------------------------------------------------------------------
# Classification
# ---------------------------------------------------------------------------

PRODUCTION = "PRODUCTION"
INTENTIONALLY_INTERNAL = "INTENTIONALLY_INTERNAL"
FUTURE = "FUTURE"
TEST_ONLY = "TEST_ONLY"
DEAD = "DEAD"

#: Modules whose contents are reached dynamically and are not expected to
#: show a static chain. Declared, so that "unreachable" keeps meaning
#: something for everything else.
#:
#: Each entry needs a reason. An undocumented exemption is how an audit
#: quietly stops auditing.
DECLARED = {
    "friday/forge_journeys": (FUTURE,
                              "CAP-FORGE-007: the per-class capability journeys with an "
                              "independent post-state observer that decide whether an "
                              "integration is complete; called by integration_versions.update, run by "
                              "tests/test_forge_journeys.py"),
    "friday/toolsets": (PRODUCTION,
                        "reached by capability id through the MCP surface and "
                        "CapabilityRuntime, never by a static call"),
    "friday/tools": (PRODUCTION,
                     "same: resolved by id at call time"),
    "friday/computer_use_verify": (PRODUCTION,
                                   "CUA-004: post-state verifier; browser_open, browser_close and "
                                   "desktop_step confirm a reported success against a fresh read"),
    "friday/platform": (PRODUCTION,
                        "OS-specific implementations chosen at runtime"),
    "friday/computer_use_sessions": (FUTURE,
                                     "CUA-008/CUA-003: session registry (owner, lease, window and "
                                     "application set); computer_use.route authorizes each "
                                     "action, but nothing opens a session yet, run by "
                                     "tests/test_computer_use_sessions.py"),
    "friday/resources": (INTENTIONALLY_INTERNAL, "data, not behaviour"),
    "friday/specialist": (FUTURE,
                          "FAB-005: composes a temporary specialist (role + "
                          "route + executor + skills + tools + workspace "
                          "policy), runs it on its executor and records the "
                          "result on the agent ledger; the live caller that "
                          "picks role and route per objective task is a later "
                          "slice, run by tests/test_specialist.py"),
    "friday/ultracode/review_workflow": (FUTURE,
                                         "UCW-006: friday.code_review v1, risk-scored panel review, "
                                         "run by tests/test_workflow_code_review.py"),
    "friday/prompts": (PRODUCTION, "loaded as text by the agent"),
    "friday/harness_sync": (FUTURE,
                            "HARN-001: renders the .claude harness into the Claude "
                            "Code and Hermes profile layouts from one source; the "
                            "operator entry point is scripts/harness_sync.py, run "
                            "by tests/test_harness_sync.py"),
    "friday/audit_fixtures": (TEST_ONLY, "fixtures for the audit suite"),
    "friday/ultracode/debug_workflow": (FUTURE,
                                        "UCW-005: the friday.debug v1 autonomous debugging "
                                        "workflow, run by tests/test_workflow_debug.py"),
    "friday/intake": (FUTURE,
                      "INTAKE-001 slice 1: the repository intake front door "
                      "(URL parse, SHA pin, stage order), run by "
                      "tests/test_intake_dossier.py"),
    "friday/evolution_value_gate": (FUTURE,
                                    "EVO-004: the Evolution Value Gate; refuses a proposal with no "
                                    "measurable reason before any paid call and archives it, called by "
                                    "the evolution controller, run by tests/test_evolution_value_gate.py"),
    "friday/research_providers": (FUTURE,
                                  "RSCH-002: the FridayResearchProvider interface and its web "
                                  "provider, no live caller yet, run by "
                                  "tests/test_research_providers.py"),
    "friday/research_bench": (FUTURE,
                              "RSCH-002: the provider benchmark and its cited-report preference "
                              "gate, no live caller yet, run by tests/test_research_bench.py"),
    "friday/ultracode/workflow_author": (FUTURE,
                                         "UCW-008: drafts, validates and dry-runs a workflow "
                                         "definition from a described process; the Command Center "
                                         "is its caller, run by "
                                         "tests/test_ultracode_workflow_author.py"),
    "friday/reflex": (FUTURE,
                      "the local fast path, off by default and behind a "
                      "feature flag; reachable when enabled"),
    "friday/simple_loop": (FUTURE,
                           "COG-005: the bounded act/observe/update loop for a "
                           "SIMPLE request, built and tested; its live caller (the "
                           "voice turn routing a SIMPLE class to it) is the next "
                           "wiring step"),
    "friday/skill_arena": (FUTURE,
                           "CAP-MKT-004: the Skill Arena (same tasks, scored winner ACTIVE, "
                           "losers ARCHIVED with scores) and its skills-lifecycle activation "
                           "hook; no live caller yet, run by tests/test_skill_arena.py and "
                           "tests/test_skill_arena_activation.py"),
    "friday/agent_ledger": (FUTURE,
                            "AGENT-001 slice 1: the durable agent ledger, "
                            "written through by RunManager; the composer, "
                            "registry and team views that read it are later "
                            "slices, run by tests/test_agent_ledger.py"),
    "friday/output_router": (FUTURE,
                             "ORCH-007: test, git and search output as counts and"
                             " failures inside a size cap with the full output in"
                             " the vault; the worker-result caller is the next"
                             " wiring step, run by tests/test_output_router.py"),
    "friday/evolution_governor": (FUTURE,
                                  "EVO-006: evolution admission and stop under the existing "
                                  "governors; the evolution controller that calls it is a later "
                                  "slice, run by tests/test_evolution_governor.py"),
    "friday/shadow": (PRODUCTION,
                      "observes every turn from watch_from_the_side"),
    "friday/binary_lab": (FUTURE,
                          "BIN-004: refusal policy for dynamic analysis of a sample, through an "
                          "offline Cyber Lab provider only; no MCP tool calls it yet, run by "
                          "tests/test_binary_lab.py"),
    "friday/system_one": (FUTURE,
                          "S1-001: the System One decision gateway and its "
                          "mock backend; a Laya/Jev backend and a live caller "
                          "are later slices, run by tests/test_system_one.py"),
    "friday/trace_debugger": (FUTURE,
                              "TRAJ-002: names the first invariant violation in a run's black-box "
                              "trace and feeds the failure genome; a caller that captures its "
                              "failure_record is a later slice, run by tests/test_trace_debugger.py"),
    "friday/system_one_calibration": (FUTURE,
                                      "S1-002: empirical confidence calibration and "
                                      "System Two escalation over System One; a live "
                                      "caller and durable outcome records are later "
                                      "slices, run by tests/test_system_one_calibration.py"),
    "friday/opportunities": (FUTURE,
                             "PRO-001: deterministic anomaly detectors over automation runs "
                             "feeding the Opportunities Inbox; scan() is reached live from "
                             "toolsets/automations.execute on every non-SUCCEEDED run "
                             "(the failure event) and from the UI 5-minute backstop "
                             "(ui_server._proactive_scan); it stays FUTURE only because "
                             "the MCP inbox tool that lists and resolves rows is a later "
                             "slice, run by tests/test_opportunities.py, "
                             "tests/test_proactive_tick.py and tests/test_proactive_event.py"),
    "friday/agent_registry": (FUTURE,
                              "AGENT-002: registered agents, instances and teams over"
                              " the ledger; the composer and team views that call it"
                              " are later slices, run by tests/test_agent_registry.py"),
    "friday/project_brain": (FUTURE,
                             "BRAIN-001: the Project Brain's storage and "
                             "Markdown export; the MCP tools and the "
                             "objective-engine writers that reach it are a "
                             "later slice"),
    "friday/lab_specialist": (FUTURE,
                              "LAB-004: offline-trained specialist micro-model promoted on a "
                              "strict win, no caller wired yet, run by "
                              "tests/test_lab_specialist.py"),
    "friday/research_routing": (PRODUCTION,
                                "web_deep_research routes every completed run through it"),
    "friday/evolution_allowance": (FUTURE,
                                   "EVOLAB-002: evolution's own money, RAM and time allowance, "
                                   "its spend ledger and the foreground yield; the experiment "
                                   "runner that draws on it is a later slice, run by "
                                   "tests/test_evolution_allowance.py"),
    "friday/forge": (FUTURE, "skill authoring, frozen by the product rescue"),
    "friday/evolution_archive": (FUTURE,
                                 "EVO-002: the Evolution Archive of experiments and the "
                                 "rejected-candidate check, kept outside the person's memory; "
                                 "the evolution controller that records into it is a later "
                                 "slice, run by tests/test_evolution_archive.py"),
    "friday/counterfactuals": (FUTURE,
                               "CF-001: the decision record comparing a plan with doing "
                               "nothing, deferring and an experiment, each claim carrying "
                               "its evidence class; the planner that builds one is a later "
                               "step, run by tests/test_counterfactuals.py"),
    "friday/forge_recipes": (FUTURE,
                             "CAP-FORGE-004: recipe templates and the integration plan; "
                             "the generator (CAP-FORGE-005) and the front door "
                             "(CAP-FORGE-001) are its callers, run by "
                             "tests/test_forge_recipes.py"),
    "friday/ultracode/compiler": (FUTURE,
                                  "UCW-009: the Workflow Compiler and its run-time fallback; the"
                                  " callers that feed it recorded trajectories are a later wiring"
                                  " step, run by tests/test_workflow_compiler.py"),
    "friday/forge_runtime": (FUTURE, "same"),
    "friday/evolution_controller": (FUTURE,
                                    "EVO-001: the event-driven Evolution Controller; the callers "
                                    "that raise triggers are a later wiring step, run by "
                                    "tests/test_evolution_controller.py"),
    "friday/evolution_lab": (FUTURE,
                             "EVOLAB-001 slice 1: the protected evaluation lab core; "
                             "selfdev wiring is a later slice, run by "
                             "tests/test_evolution_lab.py"),
    "friday/research_provenance": (PRODUCTION,
                                   "RSCH-003: source-independence clustering; web_deep_research and "
                                   "explore collapse copies through toolsets/research.collapse_copies, "
                                   "run by tests/test_research_provenance.py and "
                                   "tests/test_research_independence.py"),
    "friday/repo_classifier": (FUTURE,
                               "CAP-FORGE-002 classification only; the sandboxed "
                               "analysis (CAP-FORGE-003) and the integration "
                               "decision (CAP-FORGE-004/014) are its callers"),
    "friday/ultracode/definition": (FUTURE,
                                    "UCW-002: the versioned workflow definition that compiles to an "
                                    "ULTRA-001 spec, run by tests/test_ultracode_definition.py"),
    "friday/ultracode": (FUTURE,
                         "ULTRA-001/002: the workflow spec and stage result cache, "
                         "run by tests/test_ultracode_spec.py"),
    "friday/ultracode/tiers": (FUTURE,
                               "ULTRA-006: tier resolution and the model ranked "
                               "within a tier; the stage runner (ULTRA-002) is "
                               "the model-choice caller, run by "
                               "tests/test_ultracode_tiers.py"),
    "friday/pattern_extraction": (FUTURE,
                                  "CAP-FORGE-012 EXTRACT_PATTERN decision and its "
                                  "native build; the forge intake "
                                  "(CAP-FORGE-004/014) is its caller"),
    "friday/quarantine": (FUTURE,
                          "CAP-FORGE-003 repository quarantine; its callers are "
                          "the forge intake (CAP-FORGE-004/014)"),
    "friday/agent_composer": (FUTURE,
                              "AGENT-003: the Agent Composer (capped context packet, "
                              "spend/tool/wall limits, worker lifecycle in the agent "
                              "ledger); no live caller yet, run by "
                              "tests/test_agent_composer.py"),
    "friday/appcontainer": (FUTURE,
                            "the OS boundary under friday.quarantine; reachable "
                            "once the quarantine has a caller (CAP-FORGE-004/014)"),
    "friday/integration_versions": (FUTURE,
                                    "CAP-FORGE-010: versioned integrations with a "
                                    "known-good pointer and automatic rollback; the "
                                    "forge update path is its caller, run by "
                                    "tests/test_integration_versions.py"),
    "friday/scoped_runner": (FUTURE,
                             "EXEC-SBX-001 slice 1: the AppContainer command runner, "
                             "unwired until the escape suite passes (slice 2 restores "
                             "bridge_run_command); run by tests/test_scoped_runner_escape.py"),
    "friday/quarantine_guard": (FUTURE,
                                "copied into a quarantine root and run as the "
                                "child's entry script, never imported"),
    "friday/lab_benchmark": (FUTURE,
                             "LAB-003: the narrow-task benchmark of routing, a linear "
                             "classifier and MiniMind on the frozen set, run by "
                             "tests/test_lab_benchmark.py"),
    "friday/experimental_local": (FUTURE,
                                  "LAB-001 slice 1: the narrow-task fence for the "
                                  "EXPERIMENTAL_LOCAL_MODEL route, run by "
                                  "tests/test_experimental_local.py"),
    "friday/neural_fabric": (FUTURE,
                             "NEURAL-001: the PRD 24.11 training-lifecycle skeleton, "
                             "no network is wired to a caller yet, run by "
                             "tests/test_neural_fabric.py"),
    "friday/model_profiling": (FUTURE,
                               "MODEL-004: the open-set route profiler, built and "
                               "tested against mock routes; its caller is a real "
                               "Completer adapter over the model gateway, the next "
                               "wiring step. Its output is already read live: "
                               "model_capabilities.get falls back to stored profiles"),
    "friday/claude_mcp_route": (FUTURE,
                                "FAB-007: the signed-in Claude Code CLI acting through FRIDAY's "
                                "MCP server. gateway() hands run_request to ModelGateway, which "
                                "calls it when every API route is unavailable; kept FUTURE only "
                                "until a live run on a real claude CLI is recorded; "
                                "run by tests/test_claude_mcp_route.py and "
                                "tests/test_gateway_mcp_route.py"),
    "friday/gateway_completer": (FUTURE,
                                 "MODEL-004: the gateway-backed Completer for the open-set "
                                 "profiler, built and tested against a scripted gateway; its "
                                 "caller is the onboarding trigger for a newly seen route, "
                                 "run by tests/test_gateway_completer.py"),
    "friday/deliberation": (FUTURE,
                            "FAB-009: multi-model deliberation, built and tested against "
                            "deterministic stand-in models; its caller is a model_gateway-backed "
                            "model factory, the next wiring step, run by tests/test_deliberation.py"),
    "friday/model_scout": (FUTURE,
                           "FAB-006: the model scout, built and tested against scripted "
                           "routes; its callers are a gateway-backed probe completer and "
                           "case checker plus the listing event source, the next wiring step"),
    "friday/local_experts": (FUTURE,
                             "MODEL-005: the resident-expert pool, built and tested "
                             "against a simulated 8 GB machine on the real governor; "
                             "its callers are the loaders for the Needle, embedding "
                             "and vision experts, the next wiring step. The governor "
                             "already calls its reconcile through add_observer. "
                             "MODEL-007: it enforces friday.local_model_policy (2 GB "
                             "artifact cap with owner override, one resident general "
                             "model, vision on demand); the policy's default context "
                             "is already read live by hardware_profile.derive_policy"),
    "friday/knowledge_extract": (FUTURE,
                                 "BIN-001 slice 1: the knowledge-extraction core, not yet "
                                 "wired to the forge or registry, run by "
                                 "tests/test_knowledge_extract.py"),
    "friday/telemetry": (PRODUCTION,
                         "OTEL-001: the local GenAI-convention span recorder; the "
                         "claude_code executor, the Hermes bridge and the model "
                         "gateway emit, run by "
                         "tests/test_telemetry.py"),
    "friday/onboarding": (PRODUCTION,
                          "the owner's interview console: friday.onboarding "
                          "subcommands are dispatched by argparse "
                          "(FRIDAY.exe -m friday.onboarding), which the "
                          "reference walk cannot follow; the doctor's "
                          "onboarding section reaches the read side "
                          "statically"),
    "friday/skills": (PRODUCTION,
                      "the owner's skills console: friday.skills.cli subcommands "
                      "are dispatched by argparse (FRIDAY.exe -m friday.skills.cli), "
                      "which the reference walk cannot follow; the voice/objective "
                      "seams are tracked in docs/plans/skill-intelligence/00-status.md"),
    "friday/productivity": (PRODUCTION,
                            "EVAL-005: the continuous executor's _finish writes "
                            "each finished run's verified-units-per-100K-tokens "
                            "report"),
    "friday/capability_sources": (FUTURE,
                                  "CAP-MKT-001: capability source interface and scanned-"
                                  "quarantine harvest, no caller wired yet, run by "
                                  "tests/test_capability_sources.py"),
    "friday/federation": (FUTURE,
                          "FED-001 inbound-peer gate, not yet fed by an A2A "
                          "transport adapter"),
    "friday/lab_dataset": (FUTURE,
                           "LAB-002: privacy-safe micro-model dataset builder, "
                           "no caller wired yet, run by tests/test_lab_dataset.py"),
    "friday/prompt_compiler": (FUTURE,
                               "PROMPT-001 instruction compiler, not yet "
                               "called by the voice agent's prompt build"),
    "friday/sbom": (FUTURE,
                    "SUPPLY-001 Agent Capability SBOM generator, run by "
                    "tests/test_sbom.py"),
    "friday/memory_benchmark": (TEST_ONLY,
                                "MEM-003 delayed-execution poisoning benchmark, "
                                "run by tests/test_memory_poisoning_benchmark.py"),
}

KNOWN: dict[str, tuple[str, str]] = {
    'on_exit': (PRODUCTION, 'LiveKit calls it when the session ends'),
    'visit_ClassDef': (PRODUCTION, 'ast.NodeVisitor dispatches to it by name'),
    '_takes_a_run': (
        PRODUCTION,
        'used while deriving capability resolution, through a comprehension the reference walk does not follow',
    ),
    '_spawn': (PRODUCTION, 'objective_cli subcommand, dispatched by name'),
    'stall_steps': (FUTURE, 'COG-006: the stall detector is not yet wired into the executor loops (slice 2), run by tests/test_trajectory.py'),
    'fit_baseline': (FUTURE, 'LAB-003: the benchmark baseline, fitted on the NEURAL-002 candidate path; reached only through lab_benchmark.run_task, which has no production caller yet, run by tests/test_lab_benchmark.py'),
    'record_withheld': (FUTURE, 'BIN-004: evidence for a lab run whose output is withheld; reached only through binary_lab.dynamic_analysis, which has no production caller yet, run by tests/test_binary_lab.py'),
    'is_terminal': (DEAD, 'no reader; run state is compared directly'),
    'PlanProblem': (DEAD, 'never raised; the planner returns problems as data'),
    'FailedToAcquire': (DEAD, 'never raised; acquisition returns a bool'),
    'TaskContext': (DEAD, 'superseded by the durable Run/TaskGraph'),
    'task_context': (DEAD, 'same'),
    '_briefing_changed': (
        DEAD,
        'the briefing moved into the instructions when the preemptive-generation latency bug was fixed',
    ),
    'refresh_discovery': (DEAD, 'app discovery refreshes on use instead'),
    'broker_for': (
        DEAD,
        "the executor's question channel is never built - a real gap in the development journey, not dead by design",
    ),
    '_contract_refusal': (
        FUTURE,
        'holds amend_task/append_task to the objective contract (BCON-001, V7-D46); voice wires only skip_task so far',
    ),
    'accept_requirement': (FUTURE, 'Journey D: requirement acceptance and readiness'),
    'Requirement': (FUTURE, 'same'),
    'promotions': (FUTURE, 'promotion history, for the development journey'),
    'artifacts_for': (FUTURE, 'artifact retrieval, for the development journey'),
    'list_runs': (FUTURE, 'run listing, for the long-autonomous-work journey'),
    'ensure_started': (FUTURE, 'Journey F: the browser companion lifecycle'),
    'put_file': (
        FUTURE,
        'ExecutionBackend contract: file IO stays behind the backend for the container/remote tier',
    ),
    'get_file': (FUTURE, 'same'),
    'list_files': (FUTURE, 'same'),
    # Standing dead code triaged 2026-08-31: each is a built subsystem not yet
    # on a production entry path (or, for _tool_evidence, genuinely orphaned).
    'replay_ledger': (
        FUTURE,
        'brain rebuild-from-ledger recovery; a maintenance op not wired to a command or tool yet',
    ),
    '_tool_evidence': (
        DEAD,
        'no caller anywhere; the owned-tool-result path records evidence inline in record_owned_tool_result',
    ),
    '_tool_name': (
        FUTURE,
        'provider-fallback request fingerprinting; the history-inspection path is built but not wired to the live fallback',
    ),
    'set_telemetry': (
        FUTURE,
        'provider-fallback telemetry hook; the fallback LLM is not yet given telemetry from the session',
    ),
    '_annotate_narration': (
        FUTURE,
        'runtime narration arbiter; result-narration annotation is not on the live turn path yet',
    ),
    'reserve_result_narration': (
        FUTURE,
        'runtime narration arbiter; result-narration reservation is not on the live turn path yet',
    ),
    'TcpPort': (
        FUTURE,
        'FABRIC-PROC-01 readiness probe for SIDECAR children; chosen by fabric_service.spec_for '
        'through a conditional the reference walk does not follow, and no SIDECAR provider '
        'declares a PROCESS_SPEC yet (maxun/postiz/openmontage are deferred)',
    ),
    'HttpOk': (FUTURE, 'same: the HTTP-health variant of the SIDECAR readiness probe'),
    'spec_for': (
        FUTURE,
        'FABRIC-SVC-01 service contract; no SIDECAR provider declares a Service yet',
    ),
    '_hermes_python': (
        PRODUCTION,
        'executor_router locator, resolved by dotted name from Executor.locator at runtime',
    ),
    '_same_number': (
        FUTURE,
        'World Monitor destination verification (lat/lon/zoom match); the verifier is built but not wired to browser-open',
    ),
    'HistoryAwareFallbackStream': (
        FUTURE,
        'history-aware provider fallback stream; the default resilient LLM does not select this variant yet',
    ),
    'DestinationVerification': (
        FUTURE,
        'result of verify_world_monitor_destination; the verifier is built but not wired to the browser-open path',
    ),
    'clear_refusals': (
        FUTURE,
        "MODEL-006 slice 1 (c317b0d): lifts a route's safeguard exclusion for an operator through "
        'domain_gate.clear_route_exclusion; no operator command or endpoint calls it yet',
    ),
    'promote_model': (
        FUTURE,
        'EVAL-001: promotes a model over its baseline only on versioned, same-verifier evidence; '
        'no learned router consumes it yet',
    ),
}

#: Never reported. Dunders and framework hooks are called by machinery, not
#: by anything this graph can see.
IGNORED_NAMES = frozenset({
    "__init__", "__enter__", "__exit__", "__post_init__", "__repr__",
    "__str__", "__eq__", "__hash__", "__len__", "__iter__", "__next__",
    "__call__", "__del__", "__aenter__", "__aexit__", "__bool__",
})


@dataclass
class Finding:
    """One thing the audit could not find a production path to."""

    name: str
    path: str
    kind: str
    line: int = 0
    verdict: str = DEAD
    why: str = ""
    #: Everything that mentions it, so a person can judge rather than trust.
    mentioned_by: tuple[str, ...] = ()

    def as_dict(self) -> dict:
        return {"name": self.name, "path": self.path, "kind": self.kind,
                "line": self.line, "verdict": self.verdict, "why": self.why,
                "mentioned_by": list(self.mentioned_by)}


@dataclass
class Audit:
    """What is reachable, what is not, and what the gaps actually are."""

    reachable: set[str] = field(default_factory=set)
    findings: list[Finding] = field(default_factory=list)
    entry_points_found: dict[str, tuple[str, ...]] = field(default_factory=dict)
    considered: int = 0

    @property
    def dead(self) -> list[Finding]:
        return [f for f in self.findings if f.verdict == DEAD]

    def summary(self) -> dict:
        counts: dict[str, int] = {}
        for finding in self.findings:
            counts[finding.verdict] = counts.get(finding.verdict, 0) + 1
        return {
            "considered": self.considered,
            "reachable": len(self.reachable),
            "entry_points": {k: len(v) for k, v in
                             self.entry_points_found.items()},
            "verdicts": counts,
            "unreachable": len(self.dead),
        }


def _declared_for(path: str) -> tuple[str, str] | None:
    """The declaration covering this file, if any."""
    posix = (path or "").replace("\\", "/")
    for prefix, decision in DECLARED.items():
        if posix == prefix or posix.startswith(prefix + "/") \
                or posix == prefix + ".py":
            return decision
    return None


def audit(graph, *, include_tests: bool = False) -> Audit:
    """
    Walk from the declared production entry points and see what is reached.

    `graph` is a `codegraph.CodeGraph`. Reference edges are used, not call
    edges: a property read, a decorator and a getattr string are all real
    ways for code to be live, and using calls alone is what produced a wrong
    deletion earlier today.

    Test files are excluded from the walk, deliberately and centrally. A test
    import is not production reachability - if it counted, all three of the
    defects this module exists for would have been reported reachable.
    """
    result = Audit()

    production = [s for s in graph.symbols if not _is_test(s.path)]
    by_name: dict[str, list] = {}
    for symbol in production:
        by_name.setdefault(symbol.name, []).append(symbol)

    # Seed with the declared entry points that actually exist.
    frontier: list[str] = []
    for path_name, names in ENTRY_POINTS.items():
        found = tuple(n for n in names if n in by_name)
        result.entry_points_found[path_name] = found
        frontier.extend(found)
        missing = set(names) - set(found)
        if missing and path_name not in (AUTOMATION,):
            # An entry point that does not exist is worth knowing about: it
            # means either the architecture moved or this list is stale, and
            # a stale entry list silently shrinks what counts as reachable.
            logger.info("reachability.entry_missing path=%s names=%s",
                        path_name, ",".join(sorted(missing)))

    # Module scope is a root: what a module does at import time is live by
    # definition, and everything it references from there is reached.
    from friday.codegraph import MODULE_SCOPE
    frontier.extend(s.name for s in production if s.name == MODULE_SCOPE)
    for symbol in production:
        if symbol.name == MODULE_SCOPE:
            frontier.extend(r for r in symbol.references if r in by_name)

    # Anything inside a declared-PRODUCTION module is a root too, because it
    # is reached by id at runtime and no static chain will ever show it.
    for symbol in production:
        decision = _declared_for(symbol.path)
        if decision and decision[0] == PRODUCTION:
            frontier.append(symbol.name)

    seen: set[str] = set()
    while frontier:
        name = frontier.pop()
        if name in seen:
            continue
        seen.add(name)
        for symbol in by_name.get(name, ()):
            for referenced in symbol.references:
                if referenced not in seen and referenced in by_name:
                    frontier.append(referenced)
    result.reachable = seen

    # Now the gaps.
    for symbol in production:
        if symbol.name in IGNORED_NAMES or symbol.name.startswith("__"):
            continue
        if symbol.name == MODULE_SCOPE or not symbol.exact:
            # The module-scope pseudo-symbol is never a finding, and a symbol
            # the graph could not place exactly is not one it can judge.
            continue
        result.considered += 1
        if symbol.name in result.reachable:
            continue

        mentioned = [f"{s.path}::{s.qualified}"
                     for s in graph.mentions(symbol.name)[:6]]
        declaration = KNOWN.get(symbol.name) or _declared_for(symbol.path)
        if declaration:
            verdict, why = declaration
        elif any(_is_test(m.split("::")[0]) for m in mentioned):
            verdict = TEST_ONLY
            why = "mentioned only from tests, which is not production reach"
        else:
            verdict = DEAD
            why = "no reference chain from any declared production entry point"

        result.findings.append(Finding(
            name=symbol.name, path=symbol.path, kind=symbol.kind,
            line=symbol.line, verdict=verdict, why=why,
            mentioned_by=tuple(mentioned)))

    logger.info("reachability.audit %s", result.summary())
    return result


def _is_test(path: str) -> bool:
    posix = (path or "").replace("\\", "/")
    return (posix.startswith("tests/") or "/tests/" in posix
            or posix.split("/")[-1].startswith("test_")
            or posix.startswith("scripts/golden_"))


# ---------------------------------------------------------------------------
# Text and voice parity
# ---------------------------------------------------------------------------

#: What both modalities must do. The typed path skipped every one of these
#: and looked fine, so the list is explicit rather than trusted.
CANONICAL_PREPARATION = (
    "research classification",
    "claim verification",
    "project capture",
    "objective admission",
    "learning",
)


def parity(graph) -> dict:
    """
    Whether the typed and spoken paths prepare a turn the same way.

    Compares what each entry point reaches. Differences are allowed only
    where the modality genuinely requires them - an STT-specific step has no
    business on the typed path - but a difference in *semantics* is the
    defect this exists to catch.
    """
    def reached_from(name: str) -> set[str]:
        found: set[str] = set()
        frontier = [name]
        by_name: dict[str, list] = {}
        for symbol in graph.symbols:
            if not _is_test(symbol.path):
                by_name.setdefault(symbol.name, []).append(symbol)
        while frontier:
            current = frontier.pop()
            if current in found:
                continue
            found.add(current)
            for symbol in by_name.get(current, ()):
                for referenced in symbol.references:
                    if referenced not in found and referenced in by_name:
                        frontier.append(referenced)
        return found

    spoken = reached_from("on_user_turn_completed")
    typed = reached_from("text_input_callback")
    return {
        "spoken_only": sorted(spoken - typed),
        "typed_only": sorted(typed - spoken),
        "shared": len(spoken & typed),
        "in_parity": not (spoken - typed),
    }
