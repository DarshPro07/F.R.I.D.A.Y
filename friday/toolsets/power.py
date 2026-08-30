"""
Lock, sleep, hibernate, shut down, restart - and calling any of it back.

Separate from `processes.py` on purpose. That module answers "which process";
this one has a single target, `LOCAL_MACHINE`, and everything interesting is
about what a result is allowed to claim.

The claim is the whole subject. Microsoft's documentation for `ExitWindowsEx`
says plainly that success means shutdown was *initiated* - any application can
still stop it - and `LockWorkStation` returning true means the request reached
the input desktop, not that the screen locked. So a request that was accepted
is `INITIATED`, which is not a gentler `SUCCEEDED`; it is a different fact,
and it is settled later by `power_state.reconcile` reading the machine's boot
identity rather than by anything this module can see.

Three things this module will not do:

    force       No force flag is ever set by the ordinary path. Forcing is a
                separate action with its own confirmation, because a yes to
                "restart" is not a yes to "discard everyone's unsaved work".
    substitute  Hibernate is not sleep. A machine that cannot hibernate is
                told so, rather than quietly put to sleep instead.
    guess       Availability comes from SYSTEM_POWER_CAPABILITIES. The legacy
                IsPwrSuspendAllowed reports False on a modern-standby machine
                that sleeps every night, and believing it would have Friday
                tell somebody their own hardware cannot do something it does
                nightly.
"""

from __future__ import annotations

import ctypes

from friday import confirmation as CF
from friday import contracts as c
from friday import policy as policy_module
from friday import power_state
from friday.platform import windows as native
from friday.policy import PolicyEngine, default_engine
from friday.store import DEFAULT_DB, Store
from friday.toolsets.system import APPROVAL_PREFIX

EXECUTION_SCOPE = "local_machine"

#: One machine, and it is this one. No remote target in v1.
TARGET = "LOCAL_MACHINE"

#: Asked for, so there is time to say "no, wait".
#:
#: A confirmation catches a misrouted request. Only a callback window catches a
#: misheard yes, a changed mind, or the document nobody remembered was open.
GRACE_SECONDS = 30

#: The floor a cancellation must still succeed inside, allowing for the
#: request and the callback themselves taking time.
CALLBACK_FLOOR_SECONDS = 25

LOCK = "LOCK_WORKSTATION"
SLEEP = "SLEEP_MACHINE"
HIBERNATE = "HIBERNATE_MACHINE"
SHUTDOWN = "SHUTDOWN_MACHINE"
RESTART = "RESTART_MACHINE"
FORCED_SHUTDOWN = "FORCED_SHUTDOWN"
FORCED_RESTART = "FORCED_RESTART"

#: action -> (tool id, what the person is being asked, what it costs them)
ACTIONS = {
    LOCK: ("power.lock", "Lock this computer?",
           "You will need to sign in again. Nothing is lost."),
    SLEEP: ("power.sleep", "Put this computer to sleep?",
            "Open applications stay as they are. Friday will disconnect."),
    HIBERNATE: ("power.hibernate", "Hibernate this computer?",
                "Open applications stay as they are. Friday will disconnect."),
    SHUTDOWN: ("power.shutdown", "Shut down this computer?",
               "Unsaved work may be lost. Friday will disconnect."),
    RESTART: ("power.restart", "Restart this computer?",
              "Unsaved work may be lost. Friday will disconnect."),
    # Their own tool ids, not a flag on the ordinary ones. The policy category
    # and the confirmation's action then say the same thing, and a yes to
    # "restart" cannot be spent on "force restart" by either route.
    FORCED_SHUTDOWN: (
        "power.force_shutdown", "Force this computer to shut down?",
        "Applications will NOT be given the chance to save. "
        "Anything unsaved is lost."),
    FORCED_RESTART: (
        "power.force_restart", "Force this computer to restart?",
        "Applications will NOT be given the chance to save. "
        "Anything unsaved is lost."),
}

_store: Store | None = None


def _get_store() -> Store:
    global _store
    if _store is None:
        import os
        _store = Store(os.getenv("ADA_DB") or DEFAULT_DB)
    return _store


def _scoped(payload: dict) -> dict:
    return {"execution_scope": EXECUTION_SCOPE, "target": TARGET, **payload}


def _ask(run: c.Run, action: str, ledger: CF.Book, started: c.ActionResult,
         extra: dict | None = None) -> c.ActionResult:
    """Return the question, having done nothing."""
    _tool_id, question, impact = ACTIONS[action]
    pending = ledger.ask(run.run_id, action, TARGET, question,
                         {"impact": impact})
    return run.record(started.finish(
        status=c.CANCELLED,
        error=f"{APPROVAL_PREFIX}: {question} {impact}",
        output=_scoped({"confirm": pending.to_dict(), "action": action,
                        "impact": impact,
                        "unsaved_work_at_risk": action not in (LOCK,)}),
    ))


def _authorised(run: c.Run, action: str, nonce: str, ledger: CF.Book,
                engine: PolicyEngine, started: c.ActionResult
                ) -> c.ActionResult | None:
    """
    Everything that must be true before a power action happens.

    Order matters and is deliberate:

    1. provenance - a page cannot ask for this, and no answer would change
       that, so no question is created;
    2. nobody present - refused outright rather than left pending, because a
       live authorisation held open overnight is worse than a refusal;
    3. policy - CONFIRM, which no autonomy setting grants;
    4. the confirmation itself - bound to this action and this target, spent
       once, recomputed here rather than trusted from when it was asked.
    """
    tool_id = ACTIONS[action][0]

    refusal = policy_module.provenance_verdict(tool_id, run.provenance)
    if refusal is not None:
        return run.record(started.finish(
            status=c.FAILED, error=f"BLOCKED: {refusal.reason}"))

    verdict = engine.decide(tool_id)
    if verdict.denied:
        return run.record(started.finish(
            status=c.FAILED, error=f"BLOCKED: {verdict.reason}"))

    if not nonce:
        if not attended(run):
            return run.record(started.finish(
                status=c.FAILED,
                error=("BLOCKED: there is nobody here to approve this. "
                       "A power action needs a person, and this run has no "
                       "way to ask one - nothing was left pending.")))
        return _ask(run, action, ledger, started)

    spent = ledger.consume(nonce, run_id=run.run_id, action=action,
                           target=TARGET,
                           arguments={"impact": ACTIONS[action][2]})
    if not spent.ok:
        return run.record(started.finish(
            status=c.FAILED, error=f"not confirmed: {spent.reason}"))
    return None


def attended(run: c.Run) -> bool:
    """
    Whether there is a person who could answer.

    A scheduled automation at three in the morning has nobody to ask, and the
    honest answer is to refuse rather than to create a confirmation that sits
    there holding a live authorisation until whoever speaks next says
    something that sounds like yes.
    """
    return getattr(run, "attended", True) is not False


def unattended(request: str, capability: str = "system") -> c.Run:
    """A run with nobody to ask. For schedulers and background work."""
    run = c.Run.create(request, capability)
    run.attended = False
    return run


def _privilege(started: c.ActionResult, run: c.Run) -> c.ActionResult | None:
    """Enable SeShutdownPrivilege, or say why the request cannot be made."""
    if native.enable_shutdown_privilege():
        return None
    return run.record(started.finish(
        status=c.NOT_PERMITTED,
        error=("Friday does not hold the privilege Windows requires to do "
               "this. That is a permissions problem on this account, not the "
               "machine refusing the request."),
    ))


# ---------------------------------------------------------------------------
# Lock - nothing is lost, so nothing is delayed
# ---------------------------------------------------------------------------


def power_lock(run: c.Run, nonce: str = "", *,
               engine: PolicyEngine = default_engine,
               book: CF.Book | None = None) -> c.ActionResult:
    """
    Lock the screen. Requested, and reported as requested.

    `LockWorkStation` returning true means the request reached the input
    desktop. It is not the screen being locked, and this cannot see whether it
    was - so the honest result is INITIATED. Observing the lock needs an
    interactive session watching the desktop switch, which is not something a
    tool call can do from inside the session being locked.
    """
    ledger = book if book is not None else CF.book
    started = c.started(run.run_id, "power.lock")

    refused = _authorised(run, LOCK, nonce, ledger, engine, started)
    if refused is not None:
        return refused

    if not native.LockWorkStation():
        error = ctypes.get_last_error()
        return run.record(started.finish(
            status=c.FAILED,
            error=f"Windows refused the lock request (error {error})"))

    return run.record(started.finish(
        status=c.INITIATED,
        output=_scoped({"action": LOCK, "requested": True}),
        side_effects=("asked Windows to lock the session",),
        verification=c.Verification(
            method="lock_request_accepted",
            evidence=("LockWorkStation accepted the request. Whether the "
                      "session actually locked is not observable from inside "
                      "it, so this is reported as requested rather than "
                      "done."),
        ),
    ))


# ---------------------------------------------------------------------------
# Sleep and hibernate - the machine may stop before Friday can speak
# ---------------------------------------------------------------------------


def _suspend(run: c.Run, action: str, nonce: str, engine: PolicyEngine,
             book: CF.Book | None, hibernate: bool) -> c.ActionResult:
    ledger = book if book is not None else CF.book
    tool_id = ACTIONS[action][0]
    started = c.started(run.run_id, tool_id)

    try:
        capabilities = native.power_capabilities()
    except OSError as exc:
        return run.record(started.finish(
            status=c.FAILED, error=f"could not read power capabilities: {exc}"))

    available = capabilities.hibernate if hibernate else capabilities.sleep
    if not available:
        # Never substituted with the other one. "Hibernate" and "sleep" are
        # different requests and a machine that cannot do one is told so.
        missing = "hibernate" if hibernate else "sleep"
        return run.record(started.finish(
            status=c.UNSUPPORTED,
            error=(f"this machine cannot {missing}"
                   + (" - there is no hibernation file"
                      if hibernate and not capabilities.hibernate_file
                      else "")),
            output=_scoped({"action": action,
                            "sleep_available": capabilities.sleep,
                            "hibernate_available": capabilities.hibernate}),
        ))

    refused = _authorised(run, action, nonce, ledger, engine, started)
    if refused is not None:
        return refused

    blocked = _privilege(started, run)
    if blocked is not None:
        return blocked

    # The note goes down BEFORE the request. After may never arrive: the
    # machine suspends and this process stops mid-statement.
    record = power_state.remember(_get_store(), run.run_id,
                                  "HIBERNATE" if hibernate else "SLEEP")

    # bForce stays false. Applications get to object.
    accepted = native.SetSuspendState(hibernate, False, False)
    if not accepted:
        error = ctypes.get_last_error()
        power_state.cancel(_get_store(), record.id, settled_by=run.run_id,
                           detail=f"the request was refused (error {error})")
        return run.record(started.finish(
            status=c.FAILED,
            error=f"Windows refused the suspend request (error {error})"))

    return run.record(started.finish(
        status=c.INITIATED,
        output=_scoped({"action": action, "pending_id": record.id}),
        side_effects=(f"asked Windows to {action.lower()}",),
        verification=c.Verification(
            method="suspend_request_accepted",
            evidence=(f"the request was accepted and recorded as pending "
                      f"#{record.id}; whether the machine actually suspended "
                      f"is settled when it comes back"),
        ),
    ))


def power_sleep(run: c.Run, nonce: str = "", *,
                engine: PolicyEngine = default_engine,
                book: CF.Book | None = None) -> c.ActionResult:
    """Put the machine to sleep, once somebody has said so."""
    return _suspend(run, SLEEP, nonce, engine, book, hibernate=False)


def power_hibernate(run: c.Run, nonce: str = "", *,
                    engine: PolicyEngine = default_engine,
                    book: CF.Book | None = None) -> c.ActionResult:
    """Hibernate, if this machine can. Never quietly replaced with sleep."""
    return _suspend(run, HIBERNATE, nonce, engine, book, hibernate=True)


# ---------------------------------------------------------------------------
# Shutdown and restart - and thirty seconds in which to change your mind
# ---------------------------------------------------------------------------


def _shutdown(run: c.Run, action: str, nonce: str, engine: PolicyEngine,
              book: CF.Book | None, *, restart: bool, force: bool
              ) -> c.ActionResult:
    ledger = book if book is not None else CF.book
    tool_id = ACTIONS[action][0]
    started = c.started(run.run_id, tool_id)

    refused = _authorised(run, action, nonce, ledger, engine, started)
    if refused is not None:
        return refused

    blocked = _privilege(started, run)
    if blocked is not None:
        return blocked

    record = power_state.remember(_get_store(), run.run_id,
                                  "RESTART" if restart else "SHUTDOWN")

    flags = native.SHUTDOWN_RESTART if restart else native.SHUTDOWN_POWEROFF
    if force:
        # The only place these are ever set, and only behind a confirmation
        # that says out loud what they cost.
        flags |= native.SHUTDOWN_FORCE_OTHERS | native.SHUTDOWN_FORCE_SELF

    code = native.InitiateShutdownW(
        None, None, GRACE_SECONDS, flags,
        native.SHTDN_REASON_MAJOR_OTHER | native.SHTDN_REASON_MINOR_OTHER
        | native.SHTDN_REASON_FLAG_PLANNED)

    # This one returns a Win32 error code, not a BOOL. Zero is success, so the
    # usual `if not code` reads exactly backwards - and backwards here means
    # firing the shutdown and reporting that it failed.
    if code != native.ERROR_SUCCESS:
        power_state.cancel(_get_store(), record.id, settled_by=run.run_id,
                           detail=f"the request was refused (error {code})")
        if code == native.ERROR_ACCESS_DENIED:
            return run.record(started.finish(
                status=c.NOT_PERMITTED,
                error=("Windows refused: Friday does not have permission to "
                       "shut this machine down.")))
        return run.record(started.finish(
            status=c.FAILED,
            error=f"Windows refused the request (error {code})"))

    what = "restart" if restart else "shut down"
    return run.record(started.finish(
        status=c.INITIATED,
        output=_scoped({"action": action, "pending_id": record.id,
                        "seconds_until": GRACE_SECONDS,
                        "can_be_called_back": True,
                        "forced": force}),
        side_effects=(f"asked Windows to {what} in {GRACE_SECONDS}s",),
        verification=c.Verification(
            method="shutdown_request_accepted",
            evidence=(f"the request was accepted and the machine will {what} "
                      f"in about {GRACE_SECONDS} seconds. It has not happened "
                      f"yet and can still be called back."),
        ),
    ))


def power_shutdown(run: c.Run, nonce: str = "", *, force: bool = False,
                   engine: PolicyEngine = default_engine,
                   book: CF.Book | None = None) -> c.ActionResult:
    """Shut the machine down, after thirty seconds and a person's yes."""
    return _shutdown(run, FORCED_SHUTDOWN if force else SHUTDOWN, nonce,
                     engine, book, restart=False, force=force)


def power_restart(run: c.Run, nonce: str = "", *, force: bool = False,
                  engine: PolicyEngine = default_engine,
                  book: CF.Book | None = None) -> c.ActionResult:
    """Restart the machine. A forced restart is a different question."""
    return _shutdown(run, FORCED_RESTART if force else RESTART, nonce,
                     engine, book, restart=True, force=force)


def power_cancel(run: c.Run, *, engine: PolicyEngine = default_engine
                 ) -> c.ActionResult:
    """
    Call back a shutdown or restart that has not happened yet.

    Deliberately needs no approval. Stopping a destructive thing is not itself
    destructive, and requiring a confirmation to say "no, wait" would put a
    question between the person and the one action they most need to be
    instant. This is the only capability in the subsystem that is AUTO, and it
    is the asymmetry working correctly.
    """
    started = c.started(run.run_id, "power.cancel")

    outstanding = [row for row in power_state.pending(_get_store())
                   if row.action in ("SHUTDOWN", "RESTART")]
    if not outstanding:
        return run.record(started.finish(
            status=c.FAILED,
            error="nothing is counting down - there is no shutdown to stop"))

    if not native.AbortSystemShutdownW(None):
        error = ctypes.get_last_error()
        if error == native.ERROR_NO_SHUTDOWN_IN_PROGRESS:
            for row in outstanding:
                power_state.cancel(_get_store(), row.id,
                                   settled_by=run.run_id,
                                   detail="the window had already passed")
            return run.record(started.finish(
                status=c.FAILED,
                error=("too late - the window has already passed and the "
                       "machine is going")))
        return run.record(started.finish(
            status=c.FAILED,
            error=f"could not call it back (error {error})"))

    for row in outstanding:
        power_state.cancel(_get_store(), row.id, settled_by=run.run_id)

    return run.record(started.finish(
        status=c.SUCCEEDED,
        output=_scoped({"cancelled": [row.action for row in outstanding]}),
        side_effects=("called back the pending shutdown",),
        verification=c.Verification(
            method="shutdown_aborted_and_machine_still_running",
            evidence=(f"AbortSystemShutdownW accepted, {len(outstanding)} "
                      f"pending request(s) marked as not carried out, and "
                      f"this process is still running to say so"),
        ),
    ))
