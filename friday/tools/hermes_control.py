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

import logging
import threading
import time

from friday import execution_economics as ee
from friday import hermes_bridge as hb

logger = logging.getLogger("friday-agent")

_supervisor: hb.HermesSupervisor | None = None
_lock = threading.Lock()


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


def configure(new_supervisor: hb.HermesSupervisor | None) -> None:
    """Test seam. Production never calls this."""
    global _supervisor
    with _lock:
        _supervisor = new_supervisor


def register(mcp):

    @mcp.tool()
    def hermes_delegate(goal: str, user_outcome: str = "",
                        acceptance: str = "", constraints: str = "",
                        code_refs: str = "", skill_hints: str = "",
                        model: str = "", route_reason: str = "",
                        wait_seconds: int = 5) -> dict:
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
        """
        split = (lambda raw: tuple(
            s.strip() for s in (raw or "").split(",") if s.strip()))
        bundle = hb.TaskBundle(
            goal=goal, user_outcome=user_outcome,
            acceptance=split(acceptance), constraints=split(constraints),
            code_refs=split(code_refs), skill_hints=split(skill_hints))
        # H1/H2: when the caller did not pin a model, one deterministic
        # pass picks the minimum capable route - zero model calls spent
        # deciding which model to call. An explicit `model` argument is
        # the user-override path and wins untouched.
        if not model:
            econ = ee.classify_task(goal, code_refs=len(bundle.code_refs),
                                    acceptance=len(bundle.acceptance))
            route = ee.choose_route(econ)
            model = ee.resolve_model(route.tier)
            if not route_reason:
                route_reason = (f"{route.level}/{route.tier}: {route.reason}"
                                f" [class={econ.kind},"
                                f" consequence={econ.consequence}]")
        try:
            out = supervisor().delegate(bundle, model=model,
                                        route_reason=route_reason,
                                        wait=False)
        except hb.HermesUnavailable as exc:
            return {"status": "failed", "error": str(exc)}
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
                "gateway": sup.health() if sup.alive()
                else {"alive": False, "state": sup.state},
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
