"""
MCP adapter for the Jarvis screen powers.

Same thin shape as the other `*_control` modules: this layer builds a run,
calls the toolset, and hands back the contract. Every judgement about whether
an action is allowed lives below, in `friday.toolsets.screen` (read-only) and
`friday.toolsets.desktop` (CONFIRM, plus refusals that are code).
"""

from __future__ import annotations

import os

from friday import confirmation
from friday import contracts as c
from friday.policy import PolicyEngine, PolicyError
from friday.store import DEFAULT_DB, Store
from friday.toolsets import desktop as D
from friday.toolsets import screen as S

_store: Store | None = None
_engine: PolicyEngine | None = None


def _get_store() -> Store:
    global _store
    if _store is None:
        _store = Store(os.getenv("ADA_DB") or DEFAULT_DB)
    return _store


def _get_engine() -> PolicyEngine:
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


def _finish(run: c.Run, result) -> dict:
    run.transition("completed" if run.all_succeeded else "partial",
                   None if run.all_succeeded else (result.error or "not verified"))
    try:
        _get_store().save_run(run)
    except Exception:  # noqa: BLE001
        pass
    return result.to_dict()


def register(mcp):

    @mcp.tool()
    def screen_point(target: str, hint: str = "", monitor: int = 1) -> dict:
        """
        Show the boss where to click. Put an arrow on the thing he named.

        Use this for "where do I click to ...", "show me where ... is", and any
        question whose real answer is a place on the screen rather than a
        paragraph. The arrow appears on the actual desktop and an annotated
        screenshot is saved as evidence.

        `hint` carries a correction from the last attempt - "a little further
        left", "the small arrow next to Send" - so a second try refines the
        first instead of guessing again.

        If the control is not on screen, or it cannot be found confidently,
        this says so and draws nothing. Read the result before answering: a
        confident arrow in the wrong place is worse than an honest "I can't
        see it".
        """
        run = c.Run.create(f"where is {target}", capability="screen")
        return _finish(run, S.screen_point(run, target, hint=hint,
                                           monitor=monitor, engine=_get_engine()))

    @mcp.tool()
    def desktop_plan(task: str, monitor: int = 1) -> dict:
        """
        Work out how to do something on the screen, and show the plan. Acts on
        nothing.

        This is the first half of "take over and ...". It reads the screen,
        proposes up to eight steps, and returns a confirmation the boss must
        approve. Nothing is clicked or typed here.

        The result carries `confirm` with a nonce. Read the plan out, get a
        real yes, then call `desktop_step` with that nonce. A takeover is never
        granted by autonomy: a person says yes to this one, every time.

        Whole categories are refused outright - moving money, card and bank
        details, passwords and codes, destroying data, changing security
        settings - and no phrasing gets around that.
        """
        run = c.Run.create(f"take over: {task}", capability="desktop")
        from friday import policy as _policy
        engine = _get_engine()
        if engine.autonomy == _policy.DANGEROUS:
            # The owner switched on dangerous autonomy: the plan runs to the
            # end here, one captured step at a time, still stoppable.
            return _finish(run, D.desktop_takeover(run, task, monitor=monitor,
                                                   engine=engine))
        return _finish(run, D.desktop_plan(run, task, monitor=monitor,
                                           engine=engine))

    @mcp.tool()
    def desktop_step(nonce: str = "") -> dict:
        """
        Carry out the next single step of an approved plan, then stop.

        Pass the nonce from `desktop_plan` on the first call; after that the
        plan is live and later calls need nothing. One step per call is the
        safety model - between any two actions the boss can say stop, and each
        call re-reads the screen rather than trusting the last one.

        Say the step's line out loud before calling again. If the result says
        `cannot_see`, the plan has not advanced: point Friday at the control
        with `screen_point` or ask the boss where it is.
        """
        n = (nonce or "").strip()
        run = None
        if n:
            # Spend the approval inside the run that asked for it, so the
            # question and the act are one record and the fingerprint matches.
            pend = confirmation.book.pending.get(n)
            if pend:
                run = c.Run(run_id=pend.run_id, request="take over: step",
                            capability="desktop")
        if run is None:
            run = c.Run.create("take over: step", capability="desktop")
        return _finish(run, D.desktop_step(run, n, engine=_get_engine()))

    @mcp.tool()
    def desktop_stop() -> dict:
        """
        Stop driving. Hands off, immediately.

        Call this the moment the boss says stop, or anything like it. It is
        never gated - a stop that needs approval is not a stop - and it drops
        every approved plan, so nothing can resume without a fresh yes.
        """
        run = c.Run.create("stop", capability="desktop")
        return _finish(run, D.desktop_stop(run, engine=_get_engine()))
