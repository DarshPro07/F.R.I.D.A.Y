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

    execute(bundle, *, timeout=...) -> contracts.ActionResult

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
        known_facts=tuple(bundle.context),
    )


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
                      **_ignored) -> c.ActionResult:
        run_ctx = c.Run.create(bundle.goal, capability="development")
        started = c.started(run_ctx.run_id, "executor.hermes")

        plan = ee.plan_delegation(bundle.goal,
                                  code_refs=len(getattr(bundle, "context", ())),
                                  acceptance=len(bundle.acceptance),
                                  model=self.model, effort=self.effort)
        try:
            sup = self.supervisor()
            out = sup.delegate(
                to_bridge_bundle(bundle), friday_run_id=run_ctx.run_id,
                model=plan["model"], reasoning_effort=plan["effort"],
                route_reason=plan["reason"], workspace=bundle.workspace,
                wait=True, turn_timeout=timeout)
        except hb.HermesUnavailable as exc:
            return run_ctx.record(c.failed(started, f"hermes unavailable: {exc}"))
        except Exception as exc:                            # noqa: BLE001
            logger.exception("hermes executor failed")
            return run_ctx.record(c.failed(started, f"hermes: {exc}"))

        record = out.get("result") or {}
        status = str(record.get("status", "")).upper()
        payload = {"work_run_id": out.get("work_run_id"),
                   "model": record.get("model", ""),
                   "effort": plan["effort"], "route": plan["reason"],
                   "result": record.get("result", ""),
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
