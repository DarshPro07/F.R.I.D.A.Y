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
    DEVELOPMENT_RUN: ("DevelopmentRun", "for_goal"),
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
    "friday/toolsets": (PRODUCTION,
                        "reached by capability id through the MCP surface and "
                        "CapabilityRuntime, never by a static call"),
    "friday/tools": (PRODUCTION,
                     "same: resolved by id at call time"),
    "friday/platform": (PRODUCTION,
                        "OS-specific implementations chosen at runtime"),
    "friday/resources": (INTENTIONALLY_INTERNAL, "data, not behaviour"),
    "friday/prompts": (PRODUCTION, "loaded as text by the agent"),
    "friday/audit_fixtures": (TEST_ONLY, "fixtures for the audit suite"),
    "friday/reflex": (FUTURE,
                      "the local fast path, off by default and behind a "
                      "feature flag; reachable when enabled"),
    "friday/shadow": (PRODUCTION,
                      "observes every turn from watch_from_the_side"),
    "friday/forge": (FUTURE, "skill authoring, frozen by the product rescue"),
    "friday/forge_runtime": (FUTURE, "same"),
}

KNOWN: dict[str, tuple[str, str]] = {
    'on_exit': (PRODUCTION, 'LiveKit calls it when the session ends'),
    'visit_ClassDef': (PRODUCTION, 'ast.NodeVisitor dispatches to it by name'),
    '_takes_a_run': (
        PRODUCTION,
        'used while deriving capability resolution, through a comprehension the reference walk does not follow',
    ),
    '_spawn': (PRODUCTION, 'objective_cli subcommand, dispatched by name'),
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
