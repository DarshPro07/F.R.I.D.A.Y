"""
The channel a coding agent asks Friday a question through.

`ada_ask` has been a registered capability for a long time and resolved to
nothing. `audit_planner` even says so out loud - "registered but nothing to
call" - and `broker_for` builds the object that would have answered it, from
a function nothing calls. So the executor was told in its prompt that it
could ask, and had nowhere to ask.

That is the shape of defect the reachability invariant exists for: declared,
tested in isolation, and unreachable. This is the missing middle.

## What it does

An agent working inside a development run hits a decision it cannot make -
"which engine?", "single or multiplayer?", "what should I call this?" - and
calls this instead of guessing. Friday then decides who answers:

    ANSWER_FROM_PROJECT       an accepted decision settles it
    ANSWER_LOW_RISK_DEFAULT   reversible, invisible, cheap to be wrong about
    WAIT_USER                 he decides this one
    CONFLICT                  project memory disagrees with itself

The point is the *absence* of a fifth outcome. There is no "make something
up", which is what an agent does with a prompt telling it to keep going and
no way to ask.

## Which run is asking

Resolved from the store, not from a module-level variable. The question
arrives over MCP in Friday's process while the agent runs in another, so
process-local state would be a guess - and it would evaporate on the restart
that `WAITING_QUESTION` exists to survive.
"""

from __future__ import annotations

import json
import logging

from friday import contracts as c
from friday.policy import PolicyEngine, default_engine

logger = logging.getLogger("friday-agent")

TOOL_ID = 'ada.ask'


def _gate(run: c.Run, tool_id: str, engine: PolicyEngine):
    """The same policy check every capability makes."""
    verdict = engine.decide(tool_id)
    if verdict.allowed:
        return None
    started = c.started(run.run_id, tool_id)
    return run.record(started.finish(
        status=c.NOT_PERMITTED,
        error=f"APPROVAL_REQUIRED: {verdict.reason} [{verdict.decision}]"))


def _live_development_run(store, run_id: str = "") -> dict | None:
    """
    The run this question belongs to.

    By id when the agent knows it, otherwise the most recent live one. The
    fallback is honest rather than clever: Friday runs development work
    through one objective engine, so "the live one" is nearly always the only
    one - and when it is not, `run_id` is how the caller says which.
    """
    from friday.executors.runs import LIVE

    if run_id:
        found = store.executor_run(run_id)
        return dict(found) if found else None
    try:
        rows = [dict(r) for r in store.executor_runs()]
    except Exception:                                       # noqa: BLE001
        logger.exception("could not read the executor runs")
        return None
    live = [r for r in rows if r.get("status") in LIVE]
    return live[0] if live else None


def ada_ask(run: c.Run, question: str, options: str = "", run_id: str = "",
            engine: PolicyEngine = default_engine) -> c.ActionResult:
    """
    Answer a development question from project memory, or say he must decide.

    `options` is a JSON list or a comma-separated string of the choices the
    agent is weighing. Used only for a low-risk default, where picking the
    first is better than stopping - and recorded as an implementation
    decision so it can never be read back as his.

    Returns SUCCEEDED with an answer, or PARTIAL when the run must wait. Not
    FAILED: a question that has to reach a person is the system working.
    """
    blocked = _gate(run, TOOL_ID, engine)
    if blocked:
        return blocked
    started = c.started(run.run_id, TOOL_ID)

    asked = (question or "").strip()
    if not asked:
        return run.record(started.finish(
            status=c.FAILED, error="no question was asked"))

    from friday.toolsets.memory import store as memory_store

    store = memory_store()
    development = _live_development_run(store, run_id)
    project = (development or {}).get("project") or ""
    development_id = (development or {}).get("run_id") or ""

    choices = _as_options(options)
    broker = _broker(store, development)
    verdict = broker.classify(asked, choices)

    logger.info("executor.asked run=%s project=%r outcome=%s q=%r",
                development_id or "-", project, verdict.outcome, asked[:80])

    payload = {
        "execution_scope": "agent_runtime",
        "question": asked,
        "development_run": development_id,
        "project": project,
        **verdict.as_dict(),
    }

    if verdict.answered:
        # Recorded so the same question is never asked twice, and recorded
        # with its authority so a default can never be mistaken for his
        # decision later.
        _remember(store, project, asked, verdict)
        return run.record(started.finish(
            status=c.SUCCEEDED, output=payload,
            verification=c.Verification(
                method=f"question_broker:{verdict.outcome.lower()}",
                evidence=f"{asked!r} -> {verdict.answer!r} [{verdict.authority}, "
                         f"source={verdict.source}] because {verdict.because}"[:400])))

    # The run waits. Marked in the store rather than held in memory, because
    # the whole point of WAITING_QUESTION is that it survives a restart.
    _mark_waiting(store, development_id, asked, verdict)
    return run.record(started.finish(
        status=c.PARTIAL, output=payload,
        error=f"{verdict.outcome}: {verdict.because}"))


def _broker(store, development: dict | None):
    """
    The broker for this run, built the way the executor builds one.

    Routed through `broker_for` rather than constructing a `QuestionBroker`
    here. That function existed, was correct, and was called by nothing -
    which is exactly the defect this module is fixing one level up, and
    building a second path to the same object would have left it that way
    while looking like progress.

    The bundle comes back out of the store rather than being held in memory,
    so a question asked after a restart still knows which project it belongs
    to.
    """
    from friday.executors.claude_code import TaskBundle, broker_for

    bundle = None
    raw = (development or {}).get("task_bundle")
    if raw:
        try:
            fields = json.loads(raw)
            bundle = TaskBundle(**{
                k: (tuple(v) if isinstance(v, list) else v)
                for k, v in fields.items()
                if k in TaskBundle.__dataclass_fields__})
        except Exception:                                    # noqa: BLE001
            logger.exception("could not read the stored task bundle")
    if bundle is None:
        # No stored bundle: the broker still gets the project, which is the
        # part of the bundle that decides where an answer is looked for.
        bundle = TaskBundle(goal="", workspace="",
                            project=(development or {}).get("project") or "")
    return broker_for(store, bundle)


def _as_options(options: str) -> list[str]:
    """A JSON list, or a comma-separated string, or nothing."""
    raw = (options or "").strip()
    if not raw:
        return []
    try:
        parsed = json.loads(raw)
        if isinstance(parsed, list):
            return [str(item).strip() for item in parsed if str(item).strip()]
    except (json.JSONDecodeError, TypeError):
        pass
    return [part.strip() for part in raw.split(",") if part.strip()]


def _remember(store, project: str, question: str, verdict) -> None:
    """
    Persist the answer with whose decision it was.

    The authority matters more than the answer. `USER_DECISION` outranks
    `IMPLEMENTATION_DECISION` everywhere it is read, and writing a default in
    without the distinction would let Friday's own guess come back tomorrow
    as something the boss settled.
    """
    if not project:
        return
    try:
        store.ensure_project(project)
        store.record_decision(
            project, decision=verdict.answer,
            source=f"{verdict.authority} via the executor question channel",
            rationale=f"asked during a development run: {question}"[:400])
    except Exception:                                       # noqa: BLE001
        logger.exception("could not record the answer; it will be asked again")


def _mark_waiting(store, development_id: str, question: str, verdict) -> None:
    """Record that the run is stopped on a question, and which one."""
    from friday.executors.runs import WAITING_QUESTION

    try:
        if development_id:
            store.touch_executor_run(
                development_id, status=WAITING_QUESTION,
                last_event=f"{verdict.outcome}: {question}"[:200])
    except Exception:                                       # noqa: BLE001
        logger.exception("could not mark the run as waiting")
