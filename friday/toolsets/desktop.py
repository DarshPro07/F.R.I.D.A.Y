"""
Taking the mouse and keyboard.

This is the most dangerous capability Friday has. It is not gated by taste; it
is gated by construction, in three independent layers:

  1. `policy.DESKTOP_CONTROL` is **CONFIRM** - "requires a human to say yes,
     and no autonomy mode says it for them". FULL autonomy cannot approve a
     takeover for itself.
  2. Every step spends a one-shot `confirmation` nonce, fingerprinted against
     the exact action and target, so an approval cannot be replayed or aimed
     at something else afterwards.
  3. `forbidden()` below refuses whole categories of work outright - before
     the policy engine is consulted at all, and with no way to switch it off.

Layer 3 exists because the Screen Pack puts these refusals in a prompt, and
this codebase has already written down why that is not enough: *an instruction
is a thing a model can decide differently about*. A refusal that can be argued
with is not a refusal.

Two further rules the code enforces rather than promises:

  * **Freshness.** Every step re-captures the screen. Acting on what the screen
    looked like several seconds ago is treated as a defect.
  * **Stopping is free.** `desktop_stop` is AUTO and sets a process-global
    flag checked before every step. A stop that needs permission is not a stop.
"""

from __future__ import annotations

import json
import os
import re
import threading
import time
from typing import NamedTuple

from friday import confirmation
from friday import contracts as c
from friday import policy as policy_module
from friday.policy import PolicyEngine, default_engine
from friday.toolsets import vision
from friday.toolsets.system import APPROVAL_PREFIX
from friday.platform import windows as native

EXECUTION_SCOPE = "local_machine"

#: Clicking demands more certainty than pointing: a wrong arrow costs a glance,
#: a wrong click costs an action that may not be undoable.
CLICK_MIN_CONFIDENCE = float(os.getenv("JARVIS_CLICK_MIN_CONFIDENCE", "0.65"))

#: An approved plan goes stale. The screen it was approved against is gone.
PLAN_TTL_SECONDS = float(os.getenv("JARVIS_PLAN_TTL_SECONDS", "180"))

MAX_STEPS = 8

ACTIONS = ("move", "click", "double_click", "right_click", "type", "key")

#: The action name the confirmation is bound to. One name for the whole
#: takeover, recomputed on spend, so an approval is tied to the task it was
#: given for and cannot be pointed at a different one.
TAKEOVER = "desktop.takeover"


# ---------------------------------------------------------------------------
# Layer 3: the refusals, in code
# ---------------------------------------------------------------------------

class Refusal(NamedTuple):
    refused: bool
    reason: str
    matched: str


#: Whole categories Friday does not do with the boss's own hands on his own
#: machine. Written as words rather than a model's judgement so that the answer
#: is the same every time, and the same under every autonomy setting.
_FORBIDDEN = (
    (r"\b(pay|payment|paying|purchase|buy|checkout|check\s?out|order|subscribe|"
     r"billing|invoice|refund|donate|transfer|wire|remit|withdraw|deposit)\b",
     "anything that moves money"),
    (r"\b(card|cvv|cvc|credit\s?card|debit\s?card|expiry|iban|swift|routing|"
     r"sort\s?code|account\s?number|upi|paypal|wallet\s?address|seed\s?phrase)\b",
     "card, bank or wallet details"),
    (r"\b(password|passphrase|passcode|otp|one[-\s]?time\s?code|2fa|mfa|"
     r"authenticator|api[-\s]?key|secret|token|credential|private\s?key)\b",
     "passwords, codes and credentials"),
    (r"\b(delete|erase|wipe|format|uninstall|remove\s+all|factory\s?reset|"
     r"rm\s+-rf|drop\s+(table|database))\b",
     "destroying data"),
    (r"\b(sudo|administrator|elevat(e|ed|ion)|regedit|group\s?policy|"
     r"disable\s+(the\s+)?(firewall|defender|antivirus))\b",
     "changing system or security settings"),
)

#: Actions that put words in front of other people. Allowed only when the exact
#: text is in the step, so the plan the boss approved contained the message.
_OUTWARD = re.compile(
    r"\b(send|post|publish|submit|reply|tweet|dm|message|email|comment|share)\b",
    re.I)


def forbidden(step: dict) -> Refusal:
    """Refuse whole categories outright. Independent of policy and autonomy."""
    blob = " ".join(str(step.get(k) or "") for k in ("target", "text", "say", "action"))
    lowered = blob.lower()
    for pattern, reason in _FORBIDDEN:
        found = re.search(pattern, lowered, re.I)
        if found:
            return Refusal(True, reason, found.group(0))
    if _OUTWARD.search(lowered) and not str(step.get("text") or "").strip():
        return Refusal(
            True,
            "sending something without showing you the exact text first",
            _OUTWARD.search(lowered).group(0))
    return Refusal(False, "", "")


def plan_refusal(steps: list[dict]) -> Refusal:
    """A plan is refused entirely if any single step in it is refused."""
    for step in steps:
        verdict = forbidden(step)
        if verdict.refused:
            return verdict
    return Refusal(False, "", "")


# ---------------------------------------------------------------------------
# Stopping
# ---------------------------------------------------------------------------

#: Process-global. Checked before every step; set by `desktop_stop`, and by the
#: UI's stop word through the same capability.
ABORT = threading.Event()


def _foreground() -> int:
    """The window that has the keyboard right now, or 0 if that is unknowable."""
    try:
        if native.AVAILABLE and native.GetForegroundWindow is not None:
            return int(native.GetForegroundWindow() or 0)
    except Exception:  # noqa: BLE001
        pass
    return 0


def _title(hwnd: int) -> str:
    """A window's title, for telling the boss where the focus went."""
    if not hwnd:
        return ""
    try:
        import ctypes
        buffer = ctypes.create_unicode_buffer(256)
        native.GetWindowTextW(hwnd, buffer, 256)
        return buffer.value.strip()
    except Exception:  # noqa: BLE001
        return ""


def _gate(run: c.Run, tool_id: str, engine: PolicyEngine) -> c.ActionResult | None:
    """For the AUTO tools here (stopping). CONFIRM uses `_precheck` instead."""
    verdict = engine.decide(tool_id)
    if verdict.allowed:
        return None
    return run.record(c.started(run.run_id, tool_id).finish(
        status=c.CANCELLED,
        error=f"{APPROVAL_PREFIX}: {verdict.reason} [{verdict.decision}]",
    ))


def _precheck(run: c.Run, tool_id: str, started: c.ActionResult,
              engine: PolicyEngine) -> c.ActionResult | None:
    """Everything true before a takeover may even be *asked about*.

    Order copied deliberately from `toolsets.power`, which is the other CONFIRM
    capability in this codebase:

    1. provenance - text Friday read somewhere may not reach this, and no
       answer would change that, so no question is created;
    2. DENY - a refusal is a refusal;
    3. somebody present - an unattended run has nobody to say yes, and a live
       authorisation left pending is worse than a refusal.

    Note what is *not* here: `verdict.allowed`. CONFIRM is never "allowed" -
    that is the point of the tier. It is satisfied by a person, through
    `friday.confirmation`, per action, at the moment of the action.
    """
    refusal = policy_module.provenance_verdict(tool_id, run.provenance)
    if refusal is not None:
        return run.record(started.finish(
            status=c.CANCELLED, error=f"BLOCKED: {refusal.reason}",
            output=_scoped({"result": "refused", "reason": refusal.reason})))

    verdict = engine.decide(tool_id)
    if verdict.denied:
        return run.record(started.finish(
            status=c.CANCELLED, error=f"BLOCKED: {verdict.reason}",
            output=_scoped({"result": "refused", "reason": verdict.reason})))

    if getattr(run, "attended", True) is False:
        return run.record(started.finish(
            status=c.CANCELLED,
            error=("BLOCKED: there is nobody here to approve a takeover. "
                   "Nothing was left pending."),
            output=_scoped({"result": "refused", "reason": "unattended"})))
    return None


def _scoped(payload: dict) -> dict:
    return {"execution_scope": EXECUTION_SCOPE, **payload}


# ---------------------------------------------------------------------------
# Planning
# ---------------------------------------------------------------------------

_PLAN_SCHEMA = {
    "type": "object",
    "properties": {
        "understood": {"type": "string"},
        "possible": {"type": "boolean"},
        "why_not": {"type": "string"},
        "steps": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "action": {"type": "string"},
                    "target": {"type": "string"},
                    "text": {"type": "string"},
                    "say": {"type": "string"},
                },
                "required": ["action", "say"],
            },
        },
    },
    "required": ["possible", "steps"],
}

_PLAN_SYSTEM = """You are planning a short sequence of desktop actions for an
assistant that will carry them out one at a time, on the screen you are shown.

Return between 1 and 8 steps. Each step is one of:
  move          - put the pointer on something
  click         - click what is named in target
  double_click  - double-click what is named in target
  right_click   - right-click what is named in target
  type          - type the exact string in text
  key           - press the key named in text ("enter", "ctrl+s")

target names a control **visible in this screenshot**, in the words that appear
on screen. say is one short sentence in the first person describing that step
("Clicking Compose."). Plan only what this screen supports: if the task needs a
window that is not open, the first step is opening it, not guessing.

If the task cannot be done from this screen, set possible to false and explain
in why_not. Do not invent controls you cannot see.
"""


def _propose(frame: vision.Frame, task: str) -> dict:
    """Ask the model for a plan against this exact screen."""
    if not os.getenv("GOOGLE_API_KEY", "").strip():
        raise vision.CaptureError("planning a takeover needs GOOGLE_API_KEY")

    from google import genai
    from google.genai import types

    payload = vision._downscaled(frame.png, frame.width, frame.height)
    mime = "image/jpeg" if payload is not frame.png else "image/png"
    client = vision._client()                # carries the vision deadline
    response = client.models.generate_content(
        model=vision.VISION_MODEL,
        contents=[types.Part.from_bytes(data=payload, mime_type=mime),
                  types.Part(text=f"Task: {task}")],
        config=types.GenerateContentConfig(
            system_instruction=_PLAN_SYSTEM,
            response_mime_type="application/json",
            response_schema=_PLAN_SCHEMA,
            temperature=0.1,
        ),
    )
    text = (response.text or "").strip()
    if not text:
        raise vision.CaptureError("the model proposed nothing")
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise vision.CaptureError(f"unparseable plan: {exc}") from exc


#: run_id -> {"task", "steps", "at", "index"}. Small, in memory, and lost on
#: restart on purpose: an approved plan must not outlive the screen it was
#: approved against.
_PLANS: dict[str, dict] = {}


def desktop_plan(run: c.Run, task: str, *,
                 monitor: int = 1,
                 engine: PolicyEngine = default_engine) -> c.ActionResult:
    """Propose a plan. Touches nothing. Returns a confirmation to approve."""
    tool_id = "desktop.plan"
    started = c.started(run.run_id, tool_id)

    wanted = (task or "").strip()
    if not wanted:
        return run.record(c.failed(started, "no task named"))

    # Layer 3 first: refuse before anything is captured or asked.
    early = forbidden({"target": wanted, "text": "", "say": "", "action": "plan"})
    if early.refused:
        return run.record(started.finish(
            status=c.CANCELLED,
            error=f"refused: {early.reason}",
            output=_scoped({"result": "refused", "task": wanted,
                            "reason": early.reason, "matched": early.matched,
                            "spoken": f"I won't do that, sir - it involves "
                                      f"{early.reason}."})))

    blocked = _precheck(run, tool_id, started, engine)
    if blocked:
        return blocked

    ABORT.clear()
    try:
        frame = vision.capture_screen(monitor=monitor)
        proposal = _propose(frame, wanted)
    except vision.CaptureError as exc:
        return run.record(started.finish(
            status=c.NOT_CONFIGURED, error=str(exc),
            output=_scoped({"result": "no_plan", "task": wanted})))
    except Exception as exc:  # noqa: BLE001
        return run.record(c.failed(started, f"could not plan {wanted!r}: {exc}"))

    steps = [s for s in (proposal.get("steps") or []) if isinstance(s, dict)][:MAX_STEPS]
    if not proposal.get("possible") or not steps:
        why = (proposal.get("why_not") or "I can't see how to do that from this screen").strip()
        return run.record(started.finish(
            status=c.OBSERVED,
            output=_scoped({"result": "not_possible", "task": wanted, "why": why,
                            "spoken": f"I can't do that from this screen, sir. {why}"})))

    bad = plan_refusal(steps)
    if bad.refused:
        return run.record(started.finish(
            status=c.CANCELLED,
            error=f"refused: {bad.reason}",
            output=_scoped({"result": "refused", "task": wanted,
                            "reason": bad.reason, "matched": bad.matched,
                            "spoken": f"That plan involves {bad.reason}, sir. "
                                      f"I won't do it."})))

    # One action name for the whole takeover, so the approval the boss gives
    # here is the one `desktop_step` recomputes and spends. Binding it to the
    # task text means an approval for "open a note" cannot drive "empty the bin".
    pending = confirmation.book.ask(
        run.run_id, TAKEOVER, wanted,
        f"Take over the screen and do this? {len(steps)} steps.")
    _PLANS[run.run_id] = {"task": wanted, "steps": steps, "at": time.monotonic(),
                          "index": 0, "monitor": monitor,
                          # The window the plan was made against. Typing later
                          # checks the focus is still where the last action left
                          # it - see desktop_step.
                          "hwnd": _foreground()}

    lines = [f"{i + 1}. {s.get('say') or s.get('action')}" for i, s in enumerate(steps)]
    from friday import policy as _policy
    if engine.autonomy == _policy.DANGEROUS:
        # The owner answered yes in advance (policy.DANGEROUS). The approval
        # is still recorded against this exact task - approve() here, in the
        # run that asked, so desktop_step's fingerprint check still binds it -
        # and forbidden() was applied to every step above and is re-checked
        # at the moment each one acts. Only the waiting is gone.
        confirmation.book.approve(pending.nonce)
        return run.record(started.finish(
            status=c.OBSERVED,
            output=_scoped({
                "result": "planned", "task": wanted, "steps": steps,
                "plan": lines, "confirm": pending.to_dict(), "autorun": True,
                "spoken": "On it, sir: " + "; ".join(lines) + ".",
            })))
    return run.record(started.finish(
        status=c.CANCELLED,
        error=f"{APPROVAL_PREFIX}: a takeover needs your yes",
        output=_scoped({
            "result": "planned", "task": wanted, "steps": steps,
            "plan": lines, "confirm": pending.to_dict(),
            "spoken": "Here's what I'd do, sir: " + "; ".join(lines)
                      + ". Say go and I'll start.",
        })))


#: Step results after which carrying on would be pretending.
_TAKEOVER_STOPS = frozenset({"finished", "stopped", "refused", "stale", "blocked",
                             "no_plan", "cannot_see", "focus_moved", "not_configured"})


def desktop_takeover(run: c.Run, task: str, *, monitor: int = 1,
                     engine: PolicyEngine = default_engine) -> c.ActionResult:
    """Plan, and in dangerous autonomy carry the plan out to the end.

    One call for the owner who said he does not want to be asked "okay?":
    the plan is still made, still refused wholesale by forbidden(), still one
    step at a time with a fresh capture per step, still stoppable - it just
    does not wait for a yes that was given in advance. In any other mode this
    is desktop_plan. Returns the last step's result (finished, or why not).
    """
    planned = desktop_plan(run, task, monitor=monitor, engine=engine)
    out = planned.output if isinstance(planned.output, dict) else {}
    if not out.get("autorun"):
        return planned
    nonce = str((out.get("confirm") or {}).get("nonce") or "")
    last = planned
    for _ in range(len(out.get("steps") or []) + 1):
        last = desktop_step(run, nonce, monitor=monitor, engine=engine)
        nonce = ""                                   # spent on the first step
        lo = last.output if isinstance(last.output, dict) else {}
        if lo.get("result") in _TAKEOVER_STOPS or last.status not in ("succeeded", c.OBSERVED):
            break
    return last


def desktop_step(run: c.Run, nonce: str = "", *, monitor: int | None = None,
                 engine: PolicyEngine = default_engine) -> c.ActionResult:
    """Carry out exactly one step of an approved plan, then stop and report.

    One step per call is the whole safety model: between any two actions the
    boss can say stop, and the next call re-reads the screen rather than
    trusting what it looked like before the last click.
    """
    tool_id = "desktop.step"
    started = c.started(run.run_id, tool_id)

    if ABORT.is_set():
        return run.record(started.finish(
            status=c.CANCELLED, error="stopped",
            output=_scoped({"result": "stopped",
                            "spoken": "Stopped, sir. Hands off."})))

    plan = _PLANS.get(run.run_id)
    if not plan:
        return run.record(started.finish(
            status=c.OBSERVED,
            output=_scoped({"result": "no_plan",
                            "spoken": "There's no approved plan, sir. "
                                      "Tell me what to take over and I'll show you one."})))

    if time.monotonic() - plan["at"] > PLAN_TTL_SECONDS:
        _PLANS.pop(run.run_id, None)
        return run.record(started.finish(
            status=c.CANCELLED, error="the approved plan is stale",
            output=_scoped({"result": "stale",
                            "spoken": "That plan is too old to trust, sir - "
                                      "the screen has moved on. Ask me again."})))

    blocked = _precheck(run, tool_id, started, engine)
    if blocked:
        return blocked

    if not plan.get("active"):
        if not nonce:
            return run.record(started.finish(
                status=c.CANCELLED,
                error=f"{APPROVAL_PREFIX}: a takeover needs your yes",
                output=_scoped({"result": "needs_confirmation",
                                "spoken": "Say go and I'll start, sir."})))
        spent = confirmation.book.consume(nonce, run_id=run.run_id,
                                          action=TAKEOVER, target=plan["task"])
        if not spent.ok:
            return run.record(started.finish(
                status=c.CANCELLED, error=f"not confirmed: {spent.reason}",
                output=_scoped({"result": "blocked", "reason": spent.reason})))
        plan["active"] = True

    steps = plan["steps"]
    index = int(plan.get("index") or 0)
    if index >= len(steps):
        _PLANS.pop(run.run_id, None)
        return run.record(c.succeeded(
            started,
            output=_scoped({"result": "finished", "steps_done": len(steps),
                            "spoken": "That's the lot, sir."}),
            side_effects=(f"completed {len(steps)} steps",),
            verification=c.Verification(
                method="plan_exhausted",
                evidence=f"all {len(steps)} approved steps were carried out")))

    step = steps[index]
    bad = forbidden(step)
    if bad.refused:                       # re-checked at the moment of acting
        _PLANS.pop(run.run_id, None)
        return run.record(started.finish(
            status=c.CANCELLED, error=f"refused: {bad.reason}",
            output=_scoped({"result": "refused", "reason": bad.reason,
                            "matched": bad.matched, "step": step,
                            "spoken": f"I won't do that step, sir - it involves "
                                      f"{bad.reason}."})))

    action = str(step.get("action") or "").strip().lower()
    if action not in ACTIONS:
        _PLANS.pop(run.run_id, None)
        return run.record(c.failed(started, f"unknown action {action!r}"))

    if not native.AVAILABLE or native.SendInput is None:
        return run.record(started.finish(
            status=c.NOT_CONFIGURED,
            error="driving the desktop is a Windows notion",
            output=_scoped({"result": "not_configured"})))

    try:
        before = vision.capture_screen(monitor=plan.get("monitor", 1))
    except Exception as exc:  # noqa: BLE001
        return run.record(c.failed(started, f"could not read the screen: {exc}"))

    located = None
    if action in ("move", "click", "double_click", "right_click"):
        target = str(step.get("target") or "").strip()
        if not target:
            return run.record(c.failed(started, "the step names nothing to click"))
        try:
            located = vision.locate_in_frame(before, target)
        except Exception as exc:  # noqa: BLE001
            return run.record(c.failed(started, f"could not look for {target!r}: {exc}"))

        confidence = float(located.get("confidence") or 0.0)
        if not located.get("found") or confidence < CLICK_MIN_CONFIDENCE:
            # Refuse to click a guess. The plan does not advance.
            return run.record(started.finish(
                status=c.OBSERVED,
                output=_scoped({
                    "result": "cannot_see", "step": step, "target": target,
                    "confidence": confidence,
                    "spoken": f"I can't see {target} clearly enough to click it, "
                              f"sir. Point me at it and I'll carry on."})))

        px = before.origin_x + int(round(float(located["x"]) * before.width))
        py = before.origin_y + int(round(float(located["y"]) * before.height))
        try:
            native.send_mouse_move_abs(px, py)
            time.sleep(0.05)
            if action == "click":
                native.send_mouse_click("left")
            elif action == "double_click":
                native.send_mouse_click("left", double=True)
            elif action == "right_click":
                native.send_mouse_click("right")
        except Exception as exc:  # noqa: BLE001
            return run.record(c.failed(started, f"the {action} failed: {exc}"))
        did = f"{action} at ({px},{py}) on {located.get('label') or target!r}"
    else:
        text = str(step.get("text") or "")
        if not text:
            return run.record(c.failed(started, f"{action} with nothing to send"))
        # Keystrokes go to whatever has focus, and focus can move between two
        # steps - the boss alt-tabs, a notification steals it, a dialog pops.
        # A screenshot proves nothing about that. So typing requires the focus
        # to be exactly where the previous step left it; otherwise the words
        # would land in whichever window is in front, which may be a password
        # field. The plan does not advance; the boss brings the window back.
        expected = int(plan.get("hwnd") or 0)
        current = _foreground()
        if expected and current and current != expected:
            where = _title(current) or "another window"
            return run.record(started.finish(
                status=c.OBSERVED,
                output=_scoped({
                    "result": "focus_moved", "step": step,
                    "expected_window": _title(expected), "current_window": where,
                    "spoken": f"The focus moved to {where}, sir - I won't type "
                              f"there. Bring the window back and I'll carry on."})))
        try:
            if action == "type":
                native.send_text(text)
            else:
                native.send_key(text)
        except Exception as exc:  # noqa: BLE001
            return run.record(c.failed(started, f"the {action} failed: {exc}"))
        did = f"{action} {text!r}"

    time.sleep(0.4)                       # let the application react
    try:
        after = vision.capture_screen(monitor=plan.get("monitor", 1))
        changed = after.digest != before.digest
    except Exception:  # noqa: BLE001
        after, changed = None, False

    plan["index"] = index + 1
    plan["at"] = time.monotonic()         # the approval stays warm while it runs
    plan["hwnd"] = _foreground()          # "focus is where my last action left it"
    say = str(step.get("say") or did)
    remaining = len(steps) - plan["index"]

    payload = _scoped({
        "result": "stepped", "step": step, "did": did,
        "index": index + 1, "of": len(steps), "remaining": remaining,
        "screen_changed": changed,
        "spoken": say if changed else f"{say} Nothing changed on screen, sir.",
    })
    if not changed:
        # Acted, but nothing happened. That is not success, and saying so is
        # the difference between a report and a claim.
        return run.record(c.partial(
            started, "the action was sent but the screen did not change",
            output=payload))

    return run.record(c.succeeded(
        started, output=payload,
        side_effects=(did,),
        verification=c.Verification(
            method="screen_changed_after_action",
            evidence=f"{did}; screen sha256 {before.digest} -> "
                     f"{after.digest if after else 'unknown'}"),
    ))


def desktop_stop(run: c.Run, *, engine: PolicyEngine = default_engine) -> c.ActionResult:
    """Stop. Always allowed - a stop that needs permission is not a stop."""
    tool_id = "desktop.stop"
    blocked = _gate(run, tool_id, engine)
    if blocked:                                          # pragma: no cover
        return blocked
    started = c.started(run.run_id, tool_id)
    ABORT.set()
    _PLANS.clear()
    return run.record(c.succeeded(
        started,
        output=_scoped({"result": "stopped", "spoken": "Stopped, sir."}),
        side_effects=("stopped the takeover",),
        verification=c.Verification(
            method="abort_flag_set",
            evidence="ABORT is set and the approved plans were dropped; the "
                     "next step refuses before it acts"),
    ))
