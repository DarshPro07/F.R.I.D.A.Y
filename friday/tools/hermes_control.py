"""
Hermes delegation, reachable from Friday's production path.

The bridge (`friday/hermes_bridge.py`) is the mechanism; these MCP tools are
what makes it PRODUCTION_REACHABLE - the model can find "hand this to
Hermes" the same way it finds every other capability. Without this module
the bridge would be five hundred lines with passing tests and no caller,
which is the exact defect `friday/reachability.py` exists to name.

One supervisor per process, started lazily: the gateway costs seconds to
boot and most sessions never delegate.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from dataclasses import replace

from friday import contracts as c
from friday import execution_economics as ee
from friday import governor as G
from friday import hermes_bridge as hb
from friday.policy import PolicyEngine, PolicyError
from friday.toolsets.system import APPROVAL_PREFIX

logger = logging.getLogger("friday-agent")

_supervisor: hb.HermesSupervisor | None = None
_engine: PolicyEngine | None = None
_lock = threading.Lock()


def _get_engine() -> PolicyEngine:
    """The same policy engine shape every other control consults."""
    global _engine
    if _engine is None:
        _engine = PolicyEngine()
        for tool_id in (t.strip() for t in
                        os.getenv("ADA_PREAPPROVED_TOOLS", "").split(",") if t.strip()):
            try:
                _engine.approve_for_session(tool_id)
            except PolicyError:
                continue
    return _engine


def supervisor() -> hb.HermesSupervisor:
    global _supervisor
    with _lock:
        if _supervisor is None:
            _supervisor = hb.HermesSupervisor(
                answer_question=_broker_answer)
        return _supervisor


def _broker_answer(question: str, options: list[str]) -> str | None:
    """
    Friday's evidence, or nothing. Reuses the executor question broker -
    project decisions and stated user facts - so Hermes questions and
    Claude-executor questions are answered from the same truth.
    """
    try:
        from friday.tools import executor_control

        answer = executor_control.broker().answer(question, options)
        if answer.grounded:
            return answer.text
    except Exception:                                        # noqa: BLE001
        logger.exception("hermes question broker lookup failed")
    return None


def _stop_hermes(reason: str) -> None:
    """STOP-001: an emergency stop ends the Hermes gateway this process owns,
    and every run inside it with it."""
    with _lock:
        running = _supervisor
    if running is not None:
        running.stop()


def _register_stop_hook() -> None:
    from friday import stop

    stop.register_hook("hermes", _stop_hermes)


_register_stop_hook()


def configure(new_supervisor: hb.HermesSupervisor | None) -> None:
    """Test seam. Production never calls this."""
    global _supervisor
    with _lock:
        _supervisor = new_supervisor


def _objective_envelope(run_id: str) -> c.Envelope:
    """
    One durable objective's stored envelope, read here from the shared store.

    A caller supplies only the id. The envelope comes from the store, and an
    envelope can only narrow, so an id pointing at another objective gains a
    caller nothing. Missing or unreadable is a refusal, as it is for the
    executor (fail closed).
    """
    from friday import objectives as O
    from friday.toolsets.objectives import store as objective_store

    db = objective_store()
    row = db.objective_run(run_id)
    raw = (row or {}).get("envelope")
    if not raw:
        raise c.OutsideEnvelope(f"no objective {run_id} with an execution envelope")
    # The id also decides whose budget the work is charged to, so it is
    # accepted only while that objective is itself running a delegation.
    if not any(task.get("capability") == "hermes_delegate"
               and task.get("status") == O.TASK_RUNNING
               for task in db.objective_tasks(run_id)):
        raise c.OutsideEnvelope(
            f"objective {run_id} is not running a delegation now")
    try:
        return c.Envelope.from_dict(json.loads(raw))
    except (TypeError, ValueError) as exc:
        raise c.OutsideEnvelope(
            f"objective {run_id}'s envelope cannot be read ({exc})") from exc


def register(mcp):

    @mcp.tool()
    def hermes_delegate(goal: str, user_outcome: str = "",
                        acceptance: str = "", constraints: str = "",
                        code_refs: str = "", skill_hints: str = "",
                        model: str = "", route_reason: str = "",
                        wait_seconds: int = 5,
                        objective_run_id: str = "") -> dict:
        """
        Hand one bounded engineering task to the Hermes agent: inspect or
        analyse a project/codebase, find architectural problems, implement
        multi-file changes, debug, investigate.

        Use for sustained agentic work - not for anything a single Friday
        capability already does. The task must be self-contained: Hermes
        sees ONLY what is passed here, never this conversation.

        Submit-first: returns quickly. `wait_seconds` (default 5) is only
        a short grace window for tasks that finish instantly. When the
        returned status is "working", the task continues in the background:
        tell the boss it is in progress and STOP - when Hermes finishes,
        the result is delivered into this conversation automatically
        (durable delivery broker). Do NOT re-delegate, and do not poll
        unless the boss asks.

        `acceptance`, `constraints`, `code_refs`, `skill_hints` are
        comma-separated. Keep code_refs to the few files that matter and
        skill_hints to at most three. When you choose a non-default
        `model`, say WHY in `route_reason` (e.g. "hard cross-file
        reasoning" or "small bounded read - economical model") - the
        route and its reason are recorded on the durable run.

        `objective_run_id` is set by FRIDAY's objective engine, not chosen in
        conversation: the delegation is then held to that objective's stored
        execution envelope (where the session starts, its roots and network
        in the task's scope, interrupted when the envelope expires).
        """
        # Delegation hands Hermes terminals and file edits: the policy table
        # classes it COMMAND_EXECUTION. The same decision every other control
        # asks for, before anything starts - the resource governor below is
        # a capacity check, not an authorisation.
        verdict = _get_engine().decide("hermes_delegate")
        if not verdict.allowed:
            return {"status": "failed",
                    "error": f"{APPROVAL_PREFIX}: {verdict.reason} [{verdict.decision}]"}
        split = (lambda raw: tuple(
            s.strip() for s in (raw or "").split(",") if s.strip()))
        bundle = hb.TaskBundle(
            goal=goal, user_outcome=user_outcome,
            acceptance=split(acceptance), constraints=split(constraints),
            code_refs=split(code_refs), skill_hints=split(skill_hints))
        bounded = None
        if objective_run_id:
            try:
                envelope = _objective_envelope(objective_run_id)
                bounded = hb.bounded_by(bundle, envelope)
            except c.OutsideEnvelope as exc:
                return {"status": "failed", "error": f"ENVELOPE: {exc}"}
            bundle = bounded.bundle
            # CTX-003: the fresh worker starts from a packet, not the history.
            # The objective engine asks the context compiler for it and does
            # not read the record or build the packet itself; the compiler
            # keeps every critical fact, and a packet that would not carry
            # every one of them is not sent.
            from friday import compaction
            from friday import model_gateway as mg
            from friday.toolsets.objectives import store as objective_store

            try:
                compiled = mg.compile_request(
                    objective_store(),
                    compaction.ContextRequest(objective_id=objective_run_id, work=goal),
                    bound=envelope.provenance == c.PERSON)
            except compaction.LossyCompaction as exc:
                return {"status": "failed", "error": f"LOSSY_COMPACTION: {exc}"}
            except mg.ContextOverBudget as exc:
                return {"status": "failed",
                        "error": f"CONTEXT: the objective's facts do not fit: {exc}"}
            except (c.ContractError, ValueError, LookupError) as exc:
                return {"status": "failed",
                        "error": f"CONTEXT: the objective's record cannot be read: {exc}"}
            # The worker starts from the compiled packet, not the history.
            bundle = replace(bundle, critical_facts=compiled.binding,
                             recorded_state=compiled.recorded,
                             spawn_reason=G.ISOLATION)
            try:
                compaction.validate(bundle.render(), compiled.facts)
            except compaction.LossyCompaction as exc:
                return {"status": "failed", "error": f"LOSSY_COMPACTION: {exc}"}
        held = ({"envelope": {"objective_run_id": objective_run_id,
                              "strength": bounded.strength}}
                if bounded is not None else {})
        within = ({"workspace": bounded.workspace,
                   "friday_run_id": objective_run_id,
                   "expires_in": bounded.expires_in,
                   "scope": bounded.envelope}
                  if bounded is not None else {})
        # H1/H2: when the caller did not pin a model, one deterministic
        # pass picks the minimum capable route AND its reasoning depth -
        # zero model calls spent deciding which model to call. An explicit
        # `model` argument is the user-override path and wins untouched.
        plan = ee.plan_delegation(goal, code_refs=len(bundle.code_refs),
                                  acceptance=len(bundle.acceptance),
                                  model=model)
        model = plan["model"]
        if not route_reason:
            route_reason = plan["reason"]
        try:
            out = supervisor().delegate(bundle, model=model,
                                        provider=plan.get("provider", ""),
                                        route_reason=route_reason,
                                        reasoning_effort=plan["effort"],
                                        wait=False, **within)
        except hb.HermesUnavailable as exc:
            return {"status": "failed", "error": str(exc)}
        except c.OutsideEnvelope as exc:
            return {"status": "failed", "error": f"ENVELOPE: {exc}"}
        except G.Refused as exc:
            # FR-056: the machine cannot carry another worker right now.
            # Say so, with the measured reason, instead of starting one.
            d = exc.decision
            return {"status": "queued" if d.decision == G.QUEUE else "shed",
                    "reason": d.reason, "pressure": d.pressure,
                    "active_workers": d.active_workers, "queued": d.queued,
                    "note": ("Not started. Tell the boss the machine is under "
                             "resource pressure and the task waits; do not "
                             "retry immediately.")}
        work_run_id = out["work_run_id"]
        # Bounded wait, then hand back a poll token. A blocking wait for the
        # whole run held the MCP SSE stream open past its read timeout - the
        # transport died at ~300s while the run was healthy, and the model
        # truthfully told the boss "Hermes isn't responding". The ceiling
        # stays under that transport budget by construction.
        deadline = time.time() + max(0, min(int(wait_seconds), 240))
        sup = supervisor()
        while time.time() < deadline:
            record = sup.log.get(work_run_id) or {}
            if record.get("status") in ("COMPLETE", "PARTIAL", "FAILED"):
                # The result is returning THROUGH this tool call, so the
                # model will present it in its reply. Consume the pending
                # delivery, or the broker would speak the same findings a
                # second time.
                for delivery in sup.log.pending_deliveries():
                    if (delivery["work_run_id"] == work_run_id
                            and sup.log.claim_delivery(
                                delivery["delivery_id"])):
                        sup.log.mark_delivered(delivery["delivery_id"],
                                               via="tool-return")
                return {
                    "status": record["status"].lower(),
                    "work_run_id": work_run_id,
                    "result": record.get("result", ""),
                    "pending_question": record.get("pending_question", ""),
                    "bundle": out["bundle"],
                    "model": record.get("model", ""),
                    **held,
                }
            time.sleep(2)
        return {
            "status": "working",
            "work_run_id": work_run_id,
            "note": ("Still running. The result will be delivered into the "
                     "conversation automatically when Hermes finishes - "
                     "tell the boss it is in progress and stop. Do not "
                     "delegate the same task again."),
            "bundle": out["bundle"],
            **held,
        }

    @mcp.tool()
    def hermes_status(work_run_id: str = "") -> dict:
        """
        What Hermes is doing right now.

        With a work_run_id: that run's durable record. Without: every
        non-terminal run. Status comes from Friday's own log, never from
        asking the model to summarise itself.
        """
        sup = supervisor()
        if work_run_id:
            record = sup.log.get(work_run_id)
            if record is None:
                return {"status": "failed",
                        "error": f"no work run {work_run_id!r}"}
            return {"status": "succeeded", "run": record,
                    "stall": sup.stall_state(work_run_id)}
        return {"status": "succeeded", "active": sup.log.active(),
                # health() answers a stopped shared gateway as {alive, state}
                # and adds the scoped gateway's, which may be the only one up.
                "gateway": sup.health(),
                # Whether Hermes's OWN file/terminal tools would work if
                # re-enabled. False = keep the friday-exec bridge routing
                # (see hermes_bridge.native_tools_healthy for why True
                # alone is not sufficient to flip the config back).
                "native_tools_healthy": hb.native_tools_healthy()}

    @mcp.tool()
    def hermes_steer(work_run_id: str, text: str) -> dict:
        """
        Course-correct a running Hermes task without restarting it.

        The text lands on the model's next iteration inside the same
        session, preserving the work already done.
        """
        try:
            result = supervisor().steer(work_run_id, text)
        except (LookupError, hb.HermesUnavailable) as exc:
            return {"status": "failed", "error": str(exc)}
        return {"status": "succeeded", **result}

    @mcp.tool()
    def hermes_interrupt(work_run_id: str) -> dict:
        """Stop a running Hermes task. Partial work is recorded, not lost."""
        try:
            result = supervisor().interrupt(work_run_id)
        except (LookupError, hb.HermesUnavailable) as exc:
            return {"status": "failed", "error": str(exc)}
        return {"status": "succeeded", **result}
