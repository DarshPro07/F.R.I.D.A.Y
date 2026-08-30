"""
One implementation of each ability, callable from both Fridays.

There were two. Conversation reached 132 capabilities through MCP; a durable
objective reached 5, through a table written by hand in `objective_cli`:

    fns = {
        "system_get_info":       system.system_get_info,
        "system_list_processes": system.system_list_processes,
        "apps_open":             system.apps_open,
        "files_create":          files.files_create,
        "web_search":            web.web_search,
    }

Everything else - music, windows, audio, brightness, documents, products,
memory, reminders, YouTube, vision, processes, power - raised
``LookupError: no such capability`` inside an objective while working perfectly
one layer up. Two products wearing one name.

The objection on record for keeping it small was "the engine must not get 132
tools", and it conflates two different things:

    exposing 132 SCHEMAS to a language model      real harm - context cost,
                                                  routing collapse
    calling 132 capabilities BY ID from code      no schema, no model, no harm

This layer is the second one. It receives a capability id chosen at compile
time and looks it up. Dynamic discovery for the model is untouched.

Resolution is **derived, not written down**. A hand-maintained table of 132
entries is the same mistake as a table of 5, only slower to notice: it drifts
the moment somebody adds a capability and forgets. So the mapping comes from
the registry and the toolset modules, and `unresolved()` reports anything that
does not bind - which a test asserts against, so the gap is data rather than
folklore.
"""

from __future__ import annotations

import ast
import functools
import importlib
import inspect
import pathlib
import pkgutil
from dataclasses import dataclass

from friday import capabilities as C
from friday import contracts as c
from friday import policy as policy_module
from friday import privacy
from friday.policy import PolicyEngine, default_engine

#: Who is asking. Durability grants no authority: an objective calling
#: `power_shutdown` meets the same CONFIRM it would meet in conversation.
OBJECTIVE_EXECUTOR = "OBJECTIVE_EXECUTOR"
CONVERSATION = "CONVERSATION"

#: Capability id -> the toolset function, where the names differ.
#:
#: Two entries, both mine, both from batch 2D: the capability is `process_close`
#: and the function is `processes_close`. Written here rather than renamed
#: because the id is what the model, the policy table, the router and the
#: registry all already say, and one dictionary line is cheaper than changing
#: five places to match a plural.
ALIASES = {
    "process_close": ("friday.toolsets.processes", "processes_close"),
    "process_terminate": ("friday.toolsets.processes", "processes_terminate"),
    "open_world_monitor": ("friday.toolsets.web", "world_monitor_open"),
    "get_world_news": ("friday.toolsets.web", "get_world_news"),
    "get_world_finance_news": ("friday.toolsets.web", "get_world_finance_news"),
    "open_finance_world_monitor": ("friday.toolsets.web", "open_finance_world_monitor"),
    "memory_record_decision": ("friday.toolsets.memory", "project_record_decision"),
    "projects_list": ("friday.toolsets.memory", "projects_list"),
    "project_resume": ("friday.toolsets.memory", "project_resume"),
}

#: Capabilities whose logic lives in the MCP adapter and returns a plain dict.
#:
#: There is no ActionResult beneath these - no verification, no side-effect
#: record, no honest `may_claim_completion`. They could be made reachable by
#: wrapping whatever they return in a synthetic success, and that is precisely
#: the thing this codebase exists not to do: it would manufacture evidence for
#: an action nobody observed.
#:
#: So they are declared, counted, and refused with a reason. CORE-02 gives them
#: real toolset functions; until then an objective that asks for one is told
#: exactly why it cannot have it.
ADAPTER_ONLY_REASON = (
    "this capability's implementation lives in its MCP adapter and returns no "
    "verifiable result, so a durable task cannot record evidence for it")


@dataclass(frozen=True)
class Resolution:
    """Where a capability id actually goes."""

    capability_id: str
    module: str
    function: str

    def load(self):
        return getattr(importlib.import_module(self.module), self.function)


def _takes_a_run(function) -> bool:
    """
    Whether this function speaks the ActionResult contract.

    Every one of the capabilities that resolves by exact name takes `run` as
    its first parameter - measured, all 96 of them, none excepted. That makes
    it a real signature check rather than a convention somebody hopes holds,
    and it is what allows the prefix rule below to be safe: a name that happens
    to match but does not take a run is not a capability implementation, and is
    refused rather than bound.
    """
    try:
        parameters = list(inspect.signature(function).parameters)
    except (TypeError, ValueError):                         # pragma: no cover
        return False
    return bool(parameters) and parameters[0] == "run"


def _by_domain_prefix(capability_id: str) -> tuple[str, str] | None:
    """
    `memory_project_context` -> `friday.toolsets.memory.project_context`.

    The registry names a capability for its domain and the toolset module is
    already that domain, so the prefix is repeated in the id and absent from
    the function. Nine capabilities differ from their implementation only in
    this way - four vision, three memory, two profile - and calling them
    "adapter-only" was wrong: the service exists and is a real one.

    Deliberately domain-scoped rather than a search of every module. Looking
    only inside `friday.toolsets.<prefix>` means a name cannot bind to an
    unrelated function in some other toolset that happens to share it, which a
    broad scan would eventually do to somebody quietly.

    Read rather than imported, for the same reason as `_toolset_functions`:
    finding out where a capability lives should not load the code that
    implements it.
    """
    domain, _, rest = capability_id.partition("_")
    if not rest:
        return None
    for directory in _toolset_directories():
        source = pathlib.Path(directory) / f"{domain}.py"
        if not source.exists():
            continue
        tree = _parsed(str(source))
        if tree is None:
            return None
        for node in tree.body:
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if node.name != rest:
                continue
            arguments = node.args.posonlyargs + node.args.args
            if not arguments or arguments[0].arg != "run":
                return None
            return (f"friday.toolsets.{domain}", rest)
    return None


@functools.lru_cache(maxsize=None)
def _parsed(path: str):
    """
    One AST per file, however many capabilities ask about it.

    This function is called once per capability that did not resolve by name,
    and it re-read and re-parsed the same domain file every time - so
    `friday/toolsets/web.py` and the 600-line `products.py` were being parsed
    over and over inside a single call to `resolutions()`. Resolution had
    grown from 296ms to 540ms simply by the codebase gaining two toolsets,
    which is a cost that scales with the wrong thing.
    """
    try:
        return ast.parse(pathlib.Path(path).read_text(encoding="utf-8"))
    except (OSError, SyntaxError):
        return None


def _toolset_directories() -> list[str]:
    import friday.toolsets as toolsets

    return list(toolsets.__path__)


def _toolset_functions() -> dict[str, tuple[str, str]]:
    """
    Every public run-taking function in every toolset, **read, not imported**.

    The first version imported all 21 toolset modules to inspect them with
    `getmembers`, and that cost **1.6 seconds** on the first call - paid by
    anything that touched the runtime, including every test process. Before
    this layer existed `build_dispatch` imported three modules; resolution
    quietly turned that into all of them, which is the eager-loading the
    architecture explicitly forbids: installed is not the same as running, and
    knowing a function's *name* should not require loading MediaPipe.

    Parsing the source answers the same question in ~13ms and imports nothing.
    `Resolution.load()` then imports exactly the one module a capability
    actually needs, at the moment it is called.

    The `run`-first check is done here too, on the AST, so a same-named helper
    that does not speak the ActionResult contract is never bound.
    """
    import friday.toolsets as toolsets

    found: dict[str, list[tuple[str, str]]] = {}
    for directory in toolsets.__path__:
        for source in sorted(pathlib.Path(directory).glob("*.py")):
            if source.stem == "__init__":
                continue
            module_name = f"friday.toolsets.{source.stem}"
            try:
                tree = ast.parse(source.read_text(encoding="utf-8"))
            except (OSError, SyntaxError):                   # pragma: no cover
                continue
            for node in tree.body:
                if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    continue
                if node.name.startswith("_"):
                    continue
                arguments = node.args.posonlyargs + node.args.args
                if not arguments or arguments[0].arg != "run":
                    continue
                found.setdefault(node.name, []).append((module_name, node.name))

    # A name in two toolsets needs a human decision, and there are none today.
    # Dropping the ambiguous ones is the safe direction: they surface in
    # `unresolved()` rather than binding to whichever module was read first.
    return {key: value[0] for key, value in found.items() if len(value) == 1}


_RESOLUTIONS: dict[str, Resolution] | None = None


def resolutions() -> dict[str, Resolution]:
    """capability id -> Resolution, derived once."""
    global _RESOLUTIONS
    if _RESOLUTIONS is None:
        by_name = _toolset_functions()
        table: dict[str, Resolution] = {}
        for capability in C._ALL:
            if capability.id in ALIASES:
                module, function = ALIASES[capability.id]
                table[capability.id] = Resolution(capability.id, module,
                                                  function)
            elif capability.id in by_name:
                module, function = by_name[capability.id]
                table[capability.id] = Resolution(capability.id, module,
                                                  function)
            else:
                prefixed = _by_domain_prefix(capability.id)
                if prefixed is not None:
                    table[capability.id] = Resolution(capability.id, *prefixed)
        _RESOLUTIONS = table
    return _RESOLUTIONS


def required_arguments(capability_id: str) -> tuple[str, ...] | None:
    """
    The parameters this capability cannot be called without, `run` excluded.

    None means it does not resolve to an implementation at all - a different
    fact from "resolves and takes nothing", and collapsing the two would let
    an audit report an unreachable capability as successfully exercised with
    no arguments.

    Read off the AST rather than by importing, for the same reason as
    `_toolset_functions`: finding out what a capability needs must not load
    MediaPipe.
    """
    resolution = resolutions().get(capability_id)
    if resolution is None:
        return None
    source = pathlib.Path(*resolution.module.split(".")).with_suffix(".py")
    if not source.exists():
        for directory in _toolset_directories():
            candidate = (pathlib.Path(directory)
                         / f"{resolution.module.rsplit('.', 1)[-1]}.py")
            if candidate.exists():
                source = candidate
                break
        else:
            return None
    tree = _parsed(str(source))
    if tree is None:
        return None
    for node in tree.body:
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if node.name != resolution.function:
            continue
        names = [a.arg for a in node.args.posonlyargs + node.args.args][1:]
        defaulted = len(node.args.defaults)
        return tuple(names[:len(names) - defaulted] if defaulted else names)
    return None


def unresolved() -> list[str]:
    """Registered capabilities an objective cannot execute, and why it matters.

    This list is the honest measure of the gap. It is asserted in tests so it
    can shrink deliberately and never grow by accident.
    """
    bound = resolutions()
    return sorted(cap.id for cap in C._ALL if cap.id not in bound)


def reachable() -> list[str]:
    return sorted(resolutions())


class CapabilityRuntime:
    """
    The one way to execute a capability by id.

    Returns an ActionResult in every case, including refusal. `LookupError` is
    reserved for an id that is not registered *anywhere* - a hallucinated tool
    name - because that is a different fact from "registered but not executable
    here", and collapsing the two is how the five-capability island stayed
    invisible.
    """

    def __init__(self, *, engine: PolicyEngine | None = None,
                 principal: str = OBJECTIVE_EXECUTOR) -> None:
        self.engine = engine or default_engine
        self.principal = principal

    def execute(self, capability_id: str, arguments: dict | None = None, *,
                run: c.Run | None = None) -> c.ActionResult:
        arguments = dict(arguments or {})
        run = run or c.Run.create(f"{capability_id} {arguments}"[:200],
                                  capability=capability_id)
        started = c.started(run.run_id, capability_id)

        capability = C.by_id(capability_id)
        if capability is None:
            raise LookupError(
                f"no capability is registered as {capability_id!r}")

        # Before anything is configured, loaded or called: may this leave the
        # machine at all? Refused here rather than at the adapter, because a
        # refusal that happens after the request has been built is a refusal
        # that depends on every adapter remembering to ask.
        refusal = privacy.refuses(capability)
        if refusal:
            return run.record(started.finish(
                status=c.NOT_PERMITTED, error=refusal))

        resolution = resolutions().get(capability_id)
        if resolution is None:
            return run.record(started.finish(
                status=c.NOT_CONFIGURED,
                error=f"{capability_id}: {ADAPTER_ONLY_REASON}",
            ))

        # Same policy, same table, same decisions as conversation. A durable
        # run is not a licence.
        for tool_id in capability.policy_tool_ids():
            refusal = policy_module.provenance_verdict(tool_id, run.provenance)
            if refusal is not None:
                return run.record(started.finish(
                    status=c.FAILED, error=f"BLOCKED: {refusal.reason}"))
            if tool_id in policy_module.TOOL_CATEGORIES:
                verdict = self.engine.decide(tool_id)
                if not verdict.allowed:
                    return run.record(started.finish(
                        status=c.CANCELLED,
                        error=f"APPROVAL_REQUIRED: {verdict.reason} "
                              f"[{verdict.decision}]"))
                break

        function = resolution.load()
        try:
            result = function(run, **arguments)
        except TypeError as exc:
            # A wrong argument list is the plan's mistake, not a crash: it is
            # reported as a failed task with the signature that was expected.
            signature = inspect.signature(function)
            return run.record(started.finish(
                status=c.FAILED,
                error=f"{capability_id} takes {signature}: {exc}"))

        # An async capability hands back a coroutine. The caller awaits it,
        # and the result is bound to the run only once it exists - binding
        # the coroutine itself would record a result that has not happened.
        # The same checks run either way, so a sync and an async capability
        # cannot drift apart in what they are held to.
        if inspect.isawaitable(result):
            async def awaited():
                return self._bind(await result, run, started, capability_id)

            return awaited()
        return self._bind(result, run, started, capability_id)

    def _bind(self, result, run: c.Run, started: c.ActionResult,
              capability_id: str) -> c.ActionResult:
        if result is None:
            return run.record(started.finish(
                status=c.FAILED,
                error=f"{capability_id} returned no ActionResult"))
        # A result for a different run is not this run's result. An adapter
        # that creates its own Run and returns a result stamped with it would
        # otherwise be recorded as if it had answered the caller - and the
        # caller's run would carry evidence that belongs to another one.
        # Refused rather than re-stamped: rewriting a result's run id would
        # be fabricating provenance.
        if result.run_id != run.run_id:
            return run.record(started.finish(
                status=c.FAILED,
                error=f"{capability_id} returned a result for run "
                      f"{result.run_id}, not {run.run_id}"))
        if result not in run.results:
            run.record(result)
        return result


def build_dispatch(*, engine: PolicyEngine | None = None):
    """The async port the objective executor calls.

    Replaces the hand-written five-entry table. Same signature, so nothing
    upstream changes; 20x the reach, because the table is derived now.
    """
    runtime = CapabilityRuntime(engine=engine)

    async def dispatch(capability_id: str, arguments: dict) -> dict:
        result = runtime.execute(capability_id, arguments)
        if inspect.isawaitable(result):                     # pragma: no cover
            result = await result
        return result.to_dict()

    return dispatch
