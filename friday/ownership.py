"""
Who is doing this piece of work, so it does not get done twice.

Admission creates a durable run and then returns, and LiveKit generates the
reply for that same turn with the whole toolset still in front of the model.
So "check my system, open Paint and find a story" can open Paint twice: once
because the objective's task graph says to, and once because the model read
the sentence and helpfully did it.

Both are correct behaviour from where each stands. Neither knows about the
other. That is what this module fixes: for the short window in which the
duplicate would happen, a capability the objective has queued is *claimed*,
and a conversational call to it is deferred rather than executed.

Why a window and not a lock
---------------------------

A lock held for the life of the run reads as the safer choice and is worse. A
research objective can run for minutes, and it may hold `apps_open` in its
graph the whole time - so "open Chrome", said by a person half a minute later
about something else entirely, would be refused by machinery that was supposed
to be preventing an accident.

The duplication only ever happens in the reply to the turn that was admitted.
That is seconds. `CLAIM_SECONDS` is sized to cover it and then get out of the
way, and it is a window rather than a lock because being briefly wrong about
one call is much cheaper than being persistently wrong about every later one.

Why the store rather than a flag
--------------------------------

The MCP server is a different process from the agent - the agent reaches it
over SSE at 127.0.0.1:8000 - so an attribute set on the agent during admission
is invisible to the tool that would duplicate the work. The claim has to live
somewhere both processes can see, and the run they are both talking about is
already there.
"""

from __future__ import annotations

import inspect
import os

from datetime import datetime, timezone

from friday import objectives as O
from friday.store import DEFAULT_DB, Store

CLAIM_SECONDS = float(os.getenv("ADA_CLAIM_SECONDS", "45"))
REPLY_SECONDS = float(os.getenv("ADA_REPLY_SECONDS", "30"))

_store: Store | None = None


def store() -> Store:
    global _store
    if _store is None:
        _store = Store(os.getenv("ADA_DB") or DEFAULT_DB)
    return _store


def reset_store(new: Store | None = None) -> None:
    global _store
    if _store is not None and new is not _store:
        try:
            _store.close()
        except Exception:                                    # noqa: BLE001
            pass
    _store = new


def _age_seconds(iso: str | None) -> float:
    if not iso:
        return 1000000000.0
    try:
        stamp = datetime.fromisoformat(iso)
    except ValueError:
        return 1000000000.0
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - stamp).total_seconds()


def claimed_by(capability_id: str, *, arguments: dict | None = None,
               db: Store | None = None) -> str | None:
    """
    The run that has this capability queued right now, or None.

    Only counts a task that has not finished: once the objective has actually
    opened Paint, the claim is over and a person asking for Paint again means
    it, so they should get it.
    """
    db = db or store()
    try:
        run = O.active_run(db)
    except Exception:                                        # noqa: BLE001
        return None
    if run is None:
        return None
    try:
        tasks = db.objective_tasks(run["run_id"])
    except Exception:                                        # noqa: BLE001
        return None

    age = _age_seconds(run.get("created_at"))
    outstanding = [task for task in tasks
                   if task["status"] not in O.TASK_TERMINAL]

    if age > CLAIM_SECONDS:
        if arguments is None:
            return None
        for task in outstanding:
            if task["status"] == O.TaskStatus.RUNNING:
                continue
            if (task["capability"] == capability_id
                    and (task.get("arguments") or {}) == arguments):
                return run["run_id"]
        return None

    if age <= REPLY_SECONDS:
        planned = tasks or []
        if any(task["capability"] == capability_id for task in planned):
            return None if _is_conversational(capability_id) else run["run_id"]
        if _is_conversational(capability_id):
            return None
        if _same_work(capability_id, planned):
            return run["run_id"]

    if not outstanding:
        return None
    named = [task for task in tasks
             if task["capability"] == capability_id]
    if any(task["status"] not in O.TASK_TERMINAL for task in named):
        return run["run_id"]
    if named:
        return None
    if _is_conversational(capability_id):
        return None
    return run["run_id"] if _same_work(capability_id, outstanding) else None


_CONVERSATIONAL_PREFIXES = ("objective_", "memory_", "profile_")
_CONVERSATIONAL = frozenset({
    "format_json", "get_current_time", "get_system_info",
    "apps_list_known", "ada_ask", "files_roots", "word_count",
})


def is_conversational(capability_id: str) -> bool:
    """
    Whether the reply may use this while an objective owns the turn.

    Public because it is the single definition of that boundary: the
    claim consults it, and so does the admitted-turn tool surface in
    agent_friday. Two lists would drift, and the one that drifted would
    be the one nothing tested.
    """
    return (capability_id in _CONVERSATIONAL
            or capability_id.startswith(_CONVERSATIONAL_PREFIXES))


_is_conversational = is_conversational


def is_read_only(capability_id: str) -> bool:
    """
    Whether invoking this changes nothing - it answers, lists, searches
    or inspects. The acknowledge-then-act boundary consults this: a
    mutation deserves "on it, boss" BEFORE it happens; a read does not
    need a preamble. Defined here beside is_conversational for the same
    reason that one is: one boundary, one definition.
    """
    from friday import semantics
    operation, _target = semantics.for_capability(capability_id)
    return operation in (
        "READ", "LIST", "SEARCH", "FOLLOW_UP")


_INFORMATIONAL = frozenset({"READ", "LIST", "SEARCH", "FOLLOW_UP"})


def _same_work(capability_id: str, outstanding: list) -> bool:
    """Would this do the objective's work under a different name?"""
    from friday import semantics as S
    operation, target = S.for_capability(capability_id)
    for task in outstanding:
        planned_operation, planned_target = S.for_capability(
            task["capability"])
        if planned_target != target:
            continue
        if operation == planned_operation:
            return True
        if (operation in _INFORMATIONAL
                and planned_operation in _INFORMATIONAL):
            return True
    return False


class Deferred(RuntimeError):
    """This work belongs to a running objective, so it was not done here."""

    def __init__(self, capability_id: str, run_id: str) -> None:
        self.capability_id = capability_id
        self.run_id = run_id
        super().__init__(
            f"{capability_id} is already part of objective {run_id}, which "
            f"is running now - it has not been done twice. Tell the boss it "
            f"is in hand, or ask about the objective's progress.")


def _bound_arguments(function, call_args, call_kwargs) -> dict | None:
    """Best-effort call arguments for exact step idempotency matching."""
    try:
        bound = inspect.signature(function).bind_partial(
            *call_args, **call_kwargs)
        return dict(bound.arguments)
    except (TypeError, ValueError):
        return None


def guard(mcp):
    """
    Wrap an MCP server so every tool it registers checks for a claim first.

    One choke point rather than 124 edits, and it sits at the boundary that
    actually distinguishes the two callers: everything registered here is
    reachable by the model in conversation, and the objective executor does
    not come through here at all - it goes through `CapabilityRuntime`. So the
    guard cannot accidentally stop an objective from doing its own work.
    """

    class Guarded:

        def __getattr__(self, name):
            return getattr(mcp, name)

        def tool(self, *args, **kwargs):
            decorate = mcp.tool(*args, **kwargs)

            def register(function):
                capability_id = function.__name__
                if inspect.iscoroutinefunction(function):
                    async def guarded(*call_args, **call_kwargs):
                        owner = claimed_by(
                            capability_id,
                            arguments=_bound_arguments(
                                function, call_args, call_kwargs))
                        if owner:
                            raise Deferred(capability_id, owner)
                        return await function(*call_args, **call_kwargs)
                else:
                    def guarded(*call_args, **call_kwargs):
                        owner = claimed_by(
                            capability_id,
                            arguments=_bound_arguments(
                                function, call_args, call_kwargs))
                        if owner:
                            raise Deferred(capability_id, owner)
                        return function(*call_args, **call_kwargs)

                guarded.__name__ = function.__name__
                guarded.__doc__ = function.__doc__
                guarded.__annotations__ = dict(
                    getattr(function, "__annotations__", {}))
                guarded.__wrapped__ = function
                return decorate(guarded)

            return register

    return Guarded()
