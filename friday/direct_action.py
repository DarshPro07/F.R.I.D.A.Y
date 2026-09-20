"""
Direct action: an exact, deterministic file request is carried out as it
was spoken - not planned, not reinterpreted, not handed to a model.

    "Create jarvis-test.txt on my Desktop containing exactly the words:
     version one. Then read the file back and tell me what it contains."

is not a planning problem. It is write-exact-file -> read-exact-file ->
verify. Room M1 (2026-09-20, step 3) sent it through objective admission
instead: the planner invented a path and content of its own, the run
claimed `files_*`, and the model's own correct attempt was then refused
as "already part of the objective". Three layers, each doing its job,
producing nothing the owner asked for.

The rule: when EVERY goal in the deterministic reading names an exact file
(a literal the owner spoke, placed where he said) and resolves to a file
capability, the request is executed here, step by step, through the same
`CapabilityRuntime` the objective engine uses - same policy, same jail,
same journal, same evidence ledger. A step's failure stops the chain and
is said out loud with the runtime's own error text, never paraphrased.

Anything else - a goal with no spoken file, a non-file capability, an
unresolved piece, a constraint the planner cannot honour - is declined
here (`None`) and takes its normal route. This is a bypass for the
deterministic case, not a second planner.
"""
from __future__ import annotations

import contextlib
import logging
import os
import re
import threading
from dataclasses import dataclass, field

from friday import contracts as c
from friday import planner as P

logger = logging.getLogger("friday-agent")

#: The capabilities a direct chain may consist of. Every one takes the
#: exact path as its first argument and verifies its own effect (read-back
#: or existence check), so the chain's truth is the runtime's truth.
DIRECT_FILE_CAPABILITIES = frozenset({
    "files_create", "files_write", "files_read", "files_info", "files_wait",
    "files_delete", "files_recycle", "files_undo", "files_edit",
})

#: An undo step needs the action id of the write before it; the runtime
#: returns it in the write's output and the chain threads it forward.
_UNDO = "files_undo"


@dataclass
class Step:
    goal_id: str
    capability: str
    arguments: dict
    status: str = ""
    result: c.ActionResult | None = None
    spoken: str = ""


@dataclass
class DirectRun:
    request: str
    steps: list[Step] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return bool(self.steps) and all(s.status == c.SUCCEEDED for s in self.steps)

    @property
    def capabilities(self) -> tuple[str, ...]:
        return tuple(s.capability for s in self.steps)

    def spoken(self) -> str:
        """One honest sentence per step, in order, stopping at the failure."""
        return " ".join(s.spoken for s in self.steps if s.spoken).strip()


def plan_for(text: str) -> P.Plan | None:
    """The resolved deterministic plan when it is a direct file chain, else
    None. Pure: nothing is executed here."""
    plan = P.resolve(P.interpret(text or ""))
    goals = [g for g in plan.goals if g.operation or g.target]
    if not goals or plan.unresolved:
        return None
    for goal in goals:
        if goal.capability not in DIRECT_FILE_CAPABILITIES:
            return None
        if goal.capability != _UNDO and not P.arguments_for(goal).get("path"):
            return None
        if not any(l.kind == "path" for l in goal.literals):
            # The path came from somewhere other than the owner's words
            # (a derived note name): not exact, not ours.
            return None
    # A safety clause ("do not delete anything") or a constraint the chain
    # cannot represent is a reason to decline, not to guess.
    if plan.safety and any(g.capability in ("files_delete", "files_recycle") for g in goals):
        return None
    return plan


def _speak(step: Step, result: c.ActionResult) -> str:
    out = result.output if isinstance(result.output, dict) else {}
    path = str(out.get("path") or step.arguments.get("path") or "the file")
    name = path.replace("\\", "/").rsplit("/", 1)[-1]
    if result.status != c.SUCCEEDED:
        return f"{name}: {step.capability.replace('files_', '')} did not go through - {result.error}".strip()
    if step.capability in ("files_create", "files_write", "files_edit"):
        aid = out.get("action_id")
        return (f"Wrote {name} ({out.get('bytes', '?')} bytes, read back and matching)"
                + (f", action id {aid}." if aid else "."))
    if step.capability == "files_read":
        body = str(out.get("content", out.get("text", "")) or "")
        shown = body.strip().replace("\n", " / ")
        if len(shown) > 200:
            shown = shown[:200] + "..."
        return f"{name} reads: {shown!r}." if shown else f"{name} is empty."
    if step.capability == _UNDO:
        return f"Undid action {out.get('action_id', '')} on {name}; the previous content is back."
    if step.capability in ("files_delete", "files_recycle"):
        return f"{name} {'recycled' if step.capability == 'files_recycle' else 'deleted'}."
    if step.capability == "files_wait":
        return f"{name} appeared."
    return f"{step.capability.replace('files_', '')} on {name}: done."


def run(text: str, *, runtime=None, turn_id: str = "", executor: str = "FRIDAY_DIRECT") -> DirectRun | None:
    """Execute a direct file chain for `text`, or return None when it is not
    one. Every step is one runtime call and one evidence row; the chain
    stops at the first step that is not SUCCEEDED.

    Chains are serialized PER TARGET PATH (D-13, probe B 2026-09-20
    15:02:54: two overlapping /api/ask requests ran a create chain and an
    overwrite chain interleaved on the same file, and the create's
    read-back reported the overwrite's content). A second chain on the
    same file waits for the first; chains on different files still run
    side by side."""
    plan = plan_for(text)
    if plan is None:
        return None
    from friday import action_evidence as AE
    from friday.capability_runtime import CONVERSATION, CapabilityRuntime

    rt = runtime or CapabilityRuntime(principal=CONVERSATION)
    direct = DirectRun(request=text)
    goals = [g for g in plan.goals if g.operation or g.target]
    with _hold_targets(goals):
        _execute(direct, goals, rt, text, turn_id=turn_id, executor=executor, ledger=AE)
    return direct


_TARGET_LOCKS: dict[str, threading.Lock] = {}
_TARGET_LOCKS_GUARD = threading.Lock()


def _target_key(path: str) -> str:
    return os.path.normcase(os.path.normpath(os.path.abspath(path))) if path else ""


def _lock_for(key: str) -> threading.Lock:
    with _TARGET_LOCKS_GUARD:
        lock = _TARGET_LOCKS.get(key)
        if lock is None:
            lock = _TARGET_LOCKS[key] = threading.Lock()
        return lock


@contextlib.contextmanager
def _hold_targets(goals):
    """Every distinct target path of the chain, locked in sorted order (no
    lock-order inversion between two chains touching the same two files)."""
    keys = sorted({_target_key(str(P.arguments_for(g).get("path") or ""))
                   for g in goals if g.capability != _UNDO} - {""})
    locks = [_lock_for(k) for k in keys]
    for lock in locks:
        lock.acquire()
    try:
        yield
    finally:
        for lock in reversed(locks):
            lock.release()


def _execute(direct: DirectRun, goals, rt, text: str, *, turn_id: str, executor: str, ledger) -> None:
    AE = ledger
    last_action_id = ""
    for goal in goals:
        arguments = P.arguments_for(goal)
        if goal.capability == _UNDO:
            if not last_action_id:
                step = Step(goal.goal_id, goal.capability, {}, status=c.FAILED,
                            spoken="There is no write of mine in this request to undo.")
                direct.steps.append(step)
                break
            arguments = {"action_id": last_action_id}
        step = Step(goal.goal_id, goal.capability, arguments)
        direct.steps.append(step)
        ev = ""
        try:
            ev = AE.ledger().start(executor=executor, capability=goal.capability,
                                   arguments=arguments, turn_id=turn_id,
                                   operation="direct_action")
        except Exception:  # noqa: BLE001 - the ledger never blocks the action
            logger.exception("evidence ledger start failed for %s", goal.capability)
        run = c.Run.create(text[:200], capability=goal.capability)
        try:
            result = rt.execute(goal.capability, arguments, run=run)
        except Exception as exc:  # noqa: BLE001
            result = c.failed(c.started(run.run_id, goal.capability),
                              f"{type(exc).__name__}: {exc}")
        step.result = result
        step.status = result.status
        step.spoken = _speak(step, result)
        if ev:
            try:
                ver = getattr(result, "verification", None)
                ref = (getattr(ver, "evidence", "") or "")[:400] if ver else ""
                verified = result.status == c.SUCCEEDED and bool(ref)
                AE.ledger().finish(ev, status=str(result.status).lower(),
                                   result=(result.error or step.spoken)[:300],
                                   evidence_type="runtime_verification" if verified else "",
                                   evidence_ref=ref, verified=verified)
            except Exception:  # noqa: BLE001
                logger.exception("evidence ledger finish failed for %s", ev)
        logger.info("direct_action step=%s capability=%s status=%s path=%s",
                    goal.goal_id, goal.capability, result.status,
                    re.sub(r"\s+", " ", str(arguments.get("path", "")))[:160])
        if result.status != c.SUCCEEDED:
            break
        out = result.output if isinstance(result.output, dict) else {}
        if out.get("action_id"):
            last_action_id = str(out["action_id"])
    return direct
