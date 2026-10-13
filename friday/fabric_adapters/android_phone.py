"""
The Android phone behind the capability fabric (MOBILE-001): scoped, never
the brain (PRD 64).

`mausbot_skills` only *describes* how to drive a phone. This is the
executable side, and it adds no authority of its own. Three gates, all
ahead of the device:

**Permission.** `mobile.app_interaction`, enforced by `fabric.call` before
this adapter is activated. Only `status` is open (knowing, not doing).

**Grant.** Every acting call carries a `mobile_authorization` naming the
apps and actions it covers, an expiry and the approval on record - the
mobile counterpart of `trust.target_guard`. An absent, malformed, expired,
out-of-app or out-of-action request is refused and audited, and the device
bridge is never touched. `read_screen` and `tap_text` also ask the bridge
which app is in front and refuse unless it is the granted one, so a grant
for one app cannot read or tap whatever else is on screen. The bridge is process state set by
`register_bridge`; a caller cannot supply or swap it.

**Content.** A tap whose text asks for a password, PIN, OTP or payment detail
is refused even inside a grant (the phone-harness rule; NON_NEGOTIABLE 4/5).

The adapter ships no bridge: with none registered it reports UNAVAILABLE and
never breaks boot (NON_NEGOTIABLE 15). Refusals are policy outcomes returned
as failed results, not raised, so they never mark a healthy provider FAILED.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone

from friday import contracts as c
from friday import fabric
from friday import trust as T

PERMISSION = "mobile.app_interaction"
ACTIONS = ("open_app", "read_screen", "tap_text")
OPERATIONS = ("status",) + ACTIONS
MAX_TEXT = 200
_SENSITIVE = re.compile(
    r"\b(password|passcode|pin|otp|one[- ]time|cvv|card number|upi|payment)\b",
    re.IGNORECASE)

_bridge = None


def register_bridge(bridge) -> None:
    """Install the device bridge: status(), foreground() -> package name,
    open_app(app), read_screen(app), tap_text(app, text)."""
    global _bridge
    _bridge = bridge


def clear_bridge() -> None:
    global _bridge
    _bridge = None


@dataclass(frozen=True)
class MobileGrant:
    apps: tuple[str, ...]
    actions: tuple[str, ...]
    expires_at: str
    approval_id: str

    def __post_init__(self) -> None:
        if not self.apps or any(a.strip() in ("", "*") for a in self.apps):
            raise ValueError("apps must name the packages the grant covers")
        unknown = sorted(set(self.actions) - set(ACTIONS))
        if not self.actions or unknown:
            raise ValueError(f"actions must be drawn from {ACTIONS}")
        if not self.approval_id:
            raise ValueError("approval_id is required: the person's yes must be on record")

    def expired(self) -> bool:
        try:
            exp = datetime.fromisoformat(self.expires_at)
        except ValueError:
            return True
        if exp.tzinfo is None:
            exp = exp.replace(tzinfo=timezone.utc)
        return datetime.now(timezone.utc) >= exp


def _guard(raw, app: str, action: str, text: str) -> tuple[MobileGrant | None, str]:
    """The grant and "" when this call is within it, else (grant, why not)."""
    if raw is None:
        return None, "no mobile_authorization: a permission alone is not authority"
    try:
        grant = raw if isinstance(raw, MobileGrant) else MobileGrant(**raw)
    except (TypeError, ValueError) as exc:
        return None, f"invalid MobileGrant: {exc}"
    if grant.expired():
        return grant, f"grant expired at {grant.expires_at}"
    if app not in grant.apps:
        return grant, f"{app!r} is outside the granted apps {list(grant.apps)}"
    if action not in grant.actions:
        return grant, f"action {action!r} is not in the granted actions {list(grant.actions)}"
    if _SENSITIVE.search(text):
        return grant, "sensitive input (password, PIN, OTP or payment) is never driven"
    return grant, ""


def start():
    if _bridge is None:
        raise fabric.FabricError("no phone bridge is registered")
    return {"bridge": _bridge}


def stop(handle) -> None:
    return None


def health(handle) -> dict:
    return {"state": fabric.READY, "detail": "phone bridge registered"}


def _text(arguments: dict, name: str) -> str:
    value = arguments.get(name, "")
    if not isinstance(value, str) or not value.strip():
        raise fabric.FabricError(f"android_phone needs a string `{name}`")
    if len(value) > MAX_TEXT:
        raise fabric.FabricError(f"android_phone: `{name}` is longer than {MAX_TEXT} characters")
    return value.strip()


def call(operation: str, handle, **arguments):
    if operation not in OPERATIONS:
        raise fabric.FabricError(f"android_phone has no operation {operation!r}")
    bridge = handle["bridge"]
    tool_id = f"fabric.android_phone.{operation}"
    result = c.started(c.new_run_id(), tool_id)
    if operation == "status":
        if arguments:
            raise fabric.FabricError("android_phone.status takes no arguments")
        return c.succeeded(
            result, output=bridge.status(),
            verification=c.Verification(method="android_phone.status",
                                        evidence="the bridge answered status"))

    allowed = {"app", "mobile_authorization"} | ({"text"} if operation == "tap_text" else set())
    unknown = sorted(set(arguments) - allowed)
    if unknown:
        raise fabric.FabricError(
            f"android_phone.{operation} takes {sorted(allowed)}, not {unknown}")
    app = _text(arguments, "app")
    text = _text(arguments, "text") if operation == "tap_text" else ""

    grant, reason = _guard(arguments.get("mobile_authorization"), app, operation, text)
    if not reason and operation != "open_app":
        # shortcut: check-then-act, the front app can change before the read or
        # tap lands; a bridge that can bind the action to a package should do so.
        front = bridge.foreground()
        if front != app:
            reason = f"the foreground app is {front!r}, not the granted {app!r}"
    T.audit().record(
        actor="friday", action=tool_id, target=app, tier=T.R1,
        decision="DENY" if reason else "ALLOW", result=reason or "within grant",
        detail={"approval_id": grant.approval_id if grant else ""})
    if reason:
        return c.failed(result, f"mobile scope: {reason}")

    device_args = (app, text) if operation == "tap_text" else (app,)
    output = getattr(bridge, operation)(*device_args)
    return c.succeeded(
        result, output=output,
        verification=c.Verification(
            method="android_phone.bridge",
            evidence=f"{operation} on {app} ran under approval {grant.approval_id}"))


DESCRIPTOR = fabric.Provider(
    id="android_phone",
    family="mobile",
    upstream="",
    operations=OPERATIONS,
    risk="restricted",
    license_mode=fabric.BUILTIN_LICENSE,
    integration_mode=fabric.BUILTIN,
    permissions=(PERMISSION,),
    open_operations=("status",),
    cost_class="free",
    notes="Friday's own Android adapter (MOBILE-001). Acting calls need the "
          "mobile.app_interaction permission AND a per-call MobileGrant "
          "(apps, actions, expiry, approval); refusals are audited and never "
          "reach the device. Ships no bridge: UNAVAILABLE until one registers.",
)
