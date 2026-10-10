"""
friday/executors/hermes.py -- Hermes as the development executor.

NON_NEGOTIABLE 2: Hermes is the mandatory execution engine for serious
work. Until now the development pipeline (`friday/development.py`) could only
build a `ClaudeCodeExecutor`, and `executor_router.DEFAULT` was "claude" - so
the one path that actually ran multi-file coding work bypassed Hermes
entirely, and with it the shared-memory bundle, the route/effort economics
and the durable WorkRun ledger the bridge keeps.

This adapter presents the bridge through the executor contract
`DevelopmentRun.execute()` already relies on:

    execute(bundle, *, timeout=..., envelope=None) -> contracts.ActionResult

so Hermes drops into the existing pipeline unchanged. The claude_code
executor stays as an explicit fallback (`prefer="claude"` or Hermes absent);
it is no longer the default.

What is deliberately NOT here: a second bundle format. The development
`TaskBundle` (claude_code.TaskBundle) is translated field-for-field into the
bridge's `TaskBundle`, and the bridge's own budget/memory/effort logic does
the rest.
"""
from __future__ import annotations

import logging

from friday import contracts as c
from friday import execution_economics as ee
from friday import hermes_bridge as hb
from friday.handoff import work_run_record

logger = logging.getLogger("friday-agent.executors.hermes")


def available() -> bool:
    """Whether Hermes can be located on this machine. Discovery, not health."""
    return hb.locate() is not None


def _hermes_python() -> str | None:
    """Locator for executor_router: the gateway's python, or None."""
    found = hb.locate()
    return found["python"] if found else None


def to_bridge_bundle(bundle) -> hb.TaskBundle:
    """Translate a development bundle into what the bridge sends.

    `context` lines from the development run (code map, team instructions,
    assumptions) go in as KNOWN FACTS; the shared memory tier is added by
    `delegate(share_memory=True)` so it is not duplicated here.
    """
    return hb.TaskBundle(
        goal=bundle.goal,
        acceptance=tuple(bundle.acceptance),
        constraints=tuple(bundle.constraints),
        known_facts=tuple(bundle.known_facts or bundle.context),
        assumptions=tuple(bundle.assumptions),
        allowed_paths=tuple(bundle.allowed_paths),
        role=bundle.role,
        verification=tuple(bundle.verification),
        iteration_budget=int(bundle.iteration_budget),
        spawn_reason=bundle.spawn_reason,
    )


def _bounded(bundle: hb.TaskBundle, workspace: str,
             envelope: c.Envelope) -> hb.Bounded:
    """`bundle`, held to `envelope` for a session started in `workspace`.

    The bridge compiles the envelope (`hb.bounded_by`: expired or not allowed to
    delegate is refused). The caller's workspace is held to the envelope here as
    `brokers.within_envelope` holds it for claude_code, because the bridge would
    otherwise start the session in the envelope's own directory instead.
    """
    bounded = hb.bounded_by(bundle, envelope)
    if not envelope.reads(workspace):
        raise c.OutsideEnvelope(
            f"the workspace {workspace} is outside this run's envelope")
    if not envelope.writes(workspace):
        raise c.OutsideEnvelope(
            f"a run that writes needs its workspace {workspace} inside a "
            "write root of its envelope")
    return bounded


class HermesExecutor:
    """Hand a development bundle to Hermes and wait for the turn."""

    name = "hermes"

    def __init__(self, store=None, *, supervisor: hb.HermesSupervisor | None = None,
                 model: str = "", effort: str = "") -> None:
        self.store = store
        self._supervisor = supervisor
        self.model = model
        self.effort = effort

    def supervisor(self) -> hb.HermesSupervisor:
        if self._supervisor is None:
            from friday.tools.hermes_control import supervisor
            self._supervisor = supervisor()
        return self._supervisor

    async def execute(self, bundle, *, timeout: float = 1800.0,
                      envelope: c.Envelope | None = None,
                      **_ignored) -> c.ActionResult:
        if envelope is None:
            run_ctx = c.Run.create(bundle.goal, capability="development")
        else:
            run_ctx = c.Run(run_id=c.new_run_id(), request=bundle.goal,
                            capability="development",
                            provenance=envelope.provenance,
                            attended=envelope.attended, envelope=envelope)
        started = c.started(run_ctx.run_id, "executor.hermes")

        plan = ee.plan_delegation(bundle.goal,
                                  code_refs=len(getattr(bundle, "context", ())),
                                  acceptance=len(bundle.acceptance),
                                  model=self.model, effort=self.effort)
        try:
            sent, within = to_bridge_bundle(bundle), {}
            if envelope is not None:
                bounded = _bounded(sent, bundle.workspace, envelope)
                sent = bounded.bundle
                within = {"expires_in": bounded.expires_in, "scope": bounded.envelope}
                # The run ends when its envelope does, whatever timeout was asked for.
                if bounded.expires_in is not None:
                    timeout = min(timeout, bounded.expires_in)
            sup = self.supervisor()
            out = sup.delegate(
                sent, friday_run_id=run_ctx.run_id,
                model=plan["model"], reasoning_effort=plan["effort"],
                route_reason=plan["reason"], workspace=bundle.workspace,
                wait=True, turn_timeout=timeout, **within)
        except c.OutsideEnvelope as exc:
            # Refused, not attempted: the same answer claude_code gives for a
            # launch its envelope does not allow.
            return run_ctx.record(started.finish(
                status=c.NOT_PERMITTED, error=f"ENVELOPE: {exc}"))
        except hb.HermesUnavailable as exc:
            return run_ctx.record(c.failed(started, f"hermes unavailable: {exc}"))
        except Exception as exc:                            # noqa: BLE001
            logger.exception("hermes executor failed")
            return run_ctx.record(c.failed(started, f"hermes: {exc}"))

        record = out.get("result") or {}
        status = str(record.get("status", "")).upper()
        # ORCH-004: the bounded record, not the transcript; the full result
        # stays in the work-run row, read with hermes_status(work_run_id,
        # full=true); the record's transcript_ref is the Hermes session id.
        work_record = work_run_record(
            {**record, "work_run_id": out.get("work_run_id") or record.get("work_run_id", "")})
        payload = {"work_run_id": out.get("work_run_id"),
                   "model": record.get("model", ""),
                   "effort": plan["effort"], "route": plan["reason"],
                   "record": work_record,
                   "result": work_record["summary"],
                   "pending_question": record.get("pending_question", "")}
        if status == hb.COMPLETE:
            return run_ctx.record(c.succeeded(
                started, output=payload,
                verification=c.Verification(
                    method="hermes.turn_complete",
                    evidence=(f"work run {out.get('work_run_id')} reported "
                              f"COMPLETE; verification of the work itself is "
                              f"DevelopmentRun.verify's job, not this claim"))))
        if status == hb.PARTIAL:
            return run_ctx.record(started.finish(
                status=c.PARTIAL, output=payload,
                error=record.get("pending_question") or "hermes reported partial"))
        return run_ctx.record(c.failed(
            started, f"hermes run {out.get('work_run_id')} ended {status or 'unknown'}",
        ))
