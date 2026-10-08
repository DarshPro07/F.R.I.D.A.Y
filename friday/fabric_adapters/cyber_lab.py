"""
The Cyber Lab behind the capability fabric (CYBER-002): the one way in.

`friday/cyber_lab.py` holds the boundaries - provider interface, sandbox
tiers, the authorization contract, hash-bound evidence - and `run_routed` is
the gate in front of every provider. Until this adapter, nothing called it:
only a direct Python import reached a lab provider. This registers it as a
fabric provider, so the fabric stays the one registry for code that acts on
a target (AGENTS.md) and a lab run is an ordinary, attributable fabric call.

## Three gates, none of them the caller's to supply

**Permission.** `security.authorized_scope`, enforced by `fabric.call` before
this adapter is even activated. The one operation, `run`, is not in
`open_operations`: running against a target is doing, not knowing.

**Route.** The caller hands over `statement`, the prose of the engagement, and
the adapter asks `domain_gate.check` what it is. `run_routed` only reaches a
provider on the `cyber_lab` route. The decision is computed here and never
read from the arguments, so a caller cannot label its own request
"authorized testing"; a `decision=` argument is refused as an unknown one.

**Contract.** `security_authorization` is the PRD 11.2 `SecurityAuthorization`
(a dict or the dataclass). `run_routed` -> `execute` -> `trust.target_guard`
refuses an absent, expired, out-of-scope, out-of-action or over-intense
request, and the provider's sandbox tier must fit inside the contract's
intensity ceiling. The adapter does not duplicate any of it.

This adapter is deliberately not in `trust.SECURITY_NAMESPACE`. That gate
reads the fabric *operation* as the contract's action, and the lab's operation
is `run` while the contract names real actions (`port_scan`). The lab applies
the same `target_guard` to the real action itself, and writes the same R3
audit row `fabric.call` writes for a namespaced provider.

## Providers

The adapter ships none. A lab provider registers itself with
`cyber_lab.register`; until one does the adapter reports UNAVAILABLE (never
breaking boot, NON_NEGOTIABLE 15). Refusals are policy outcomes, returned as a
failed result carrying the evidence digest - not raised, so three refusals in
a row never mark a healthy provider FAILED.
"""

from __future__ import annotations

from dataclasses import dataclass

from friday import contracts as c
from friday import cyber_lab, domain_gate, fabric
from friday import trust as T

OPERATION = "run"
ROUTE = "cyber_lab"
TOOL_ID = f"fabric.{ROUTE}.{OPERATION}"
ARGUMENTS = ("provider", "host", "action", "intensity", "statement",
             "security_authorization")
MAX_FIELD = 500
MAX_STATEMENT = 4000


@dataclass(frozen=True)
class _Statement:
    """The one field of a work packet `domain_gate.packet_text` reads here."""

    goal: str


def start():
    return {"lab": cyber_lab}


def stop(handle) -> None:
    return None


def health(handle) -> dict:
    registered = handle["lab"].names()
    if not registered:
        return {"state": fabric.UNAVAILABLE,
                "detail": "no lab provider is registered; every run would be refused"}
    return {"state": fabric.READY,
            "detail": f"{len(registered)} lab provider(s): {', '.join(registered)}"}


def _text(arguments: dict, name: str, *, limit: int = MAX_FIELD, required: bool = True) -> str:
    value = arguments.get(name)
    if value is None:
        value = ""
    if not isinstance(value, str):
        raise fabric.FabricError(f"cyber_lab.run: `{name}` must be a string")
    value = value.strip()
    if required and not value:
        raise fabric.FabricError(f"cyber_lab.run needs `{name}`")
    if len(value) > limit:
        raise fabric.FabricError(f"cyber_lab.run: `{name}` is longer than {limit} characters")
    return value


def _contract(raw) -> T.SecurityAuthorization | None:
    """The contract, or None when there is none. A malformed one raises ValueError."""
    if raw is None or isinstance(raw, T.SecurityAuthorization):
        return raw
    if not isinstance(raw, dict):
        raise ValueError("security_authorization must be a mapping")
    try:
        return T.SecurityAuthorization(**raw)
    except TypeError as exc:
        raise ValueError(str(exc)) from exc


def _audit(auth: T.SecurityAuthorization | None, *, host: str, allowed: bool,
           reason: str, detail: dict) -> None:
    objective = auth.objective_id if auth else ""
    T.audit().record(
        actor=objective or "friday", action=TOOL_ID, target=host, tier=T.R3,
        decision="ALLOW" if allowed else "DENY", result=reason,
        objective_id=objective, detail=detail)


def call(operation: str, handle=None, *, run_id: str = "", **arguments):
    if operation != OPERATION:
        raise fabric.FabricError(f"cyber_lab has no operation {operation!r}")
    unknown = sorted(set(arguments) - set(ARGUMENTS))
    if unknown:
        raise fabric.FabricError(
            f"cyber_lab.run takes {list(ARGUMENTS)}, not {unknown}")

    name = _text(arguments, "provider")
    request = cyber_lab.LabRequest(
        host=_text(arguments, "host"), action=_text(arguments, "action"),
        intensity=_text(arguments, "intensity", required=False) or T.LOW_ACTIVE)
    statement = _text(arguments, "statement", limit=MAX_STATEMENT)

    result = c.started(run_id or c.new_run_id(), TOOL_ID)
    try:
        auth = _contract(arguments.get("security_authorization"))
    except ValueError as exc:
        reason = f"invalid SecurityAuthorization: {exc}"
        _audit(None, host=request.host, allowed=False, reason=reason,
               detail={"provider": name, "lab_action": request.action})
        return c.failed(result, reason)

    decision = domain_gate.check(_Statement(statement), ROUTE)
    lab = (handle or {"lab": cyber_lab})["lab"]
    evidence = lab.run_routed(decision, name, auth, request)

    _audit(auth, host=evidence.host, allowed=evidence.allowed, reason=evidence.reason,
           detail={"provider": evidence.provider, "sandbox_tier": evidence.tier,
                   "lab_action": evidence.action, "intensity": evidence.intensity,
                   "approval_id": evidence.approval_id, "route": decision.next_step,
                   "digest": evidence.digest})
    if not evidence.allowed:
        return c.failed(result, f"cyber lab refused: {evidence.reason} "
                                f"(evidence {evidence.digest[:12]})")
    return c.succeeded(
        result,
        verification=c.Verification(
            method="cyber_lab.evidence",
            evidence=(f"provider {evidence.provider} ({evidence.tier}) ran "
                      f"{evidence.action} on {evidence.host} under approval "
                      f"{evidence.approval_id}; evidence digest {evidence.digest[:12]}")),
        output=evidence.to_dict())


DESCRIPTOR = fabric.Provider(
    id="cyber_lab",
    family="security",
    upstream="",
    operations=(OPERATION,),
    risk="restricted",
    license_mode=fabric.BUILTIN_LICENSE,
    integration_mode=fabric.BUILTIN,
    permissions=("security.authorized_scope",),
    cost_class="free",
    notes="Friday's own Cyber Lab gate (friday/cyber_lab.py). `run` reaches a "
          "registered lab provider only on the domain gate's cyber_lab route, "
          "under a SecurityAuthorization contract, inside the provider's sandbox "
          "tier; every call writes an R3 audit row and returns hash-bound "
          "evidence. Ships no provider: UNAVAILABLE until one registers.",
)
