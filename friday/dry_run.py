"""
SIDE-002: the dry-run stage between a plan and a real run.

A consequential task runs plan -> dry run -> expected effects -> approval ->
real run. This is the dry run: a pure read of the capability record that says
what a call would touch, without calling it. The blast radius it lands in
(the same vocabulary `execution_economics` routes on) then sets how hard to
think, whether a person must say yes, and how deeply to verify, so a bigger
radius only ever escalates.

It adds a floor and never lowers one: policy still decides per call, and an
integration or core radius asks for a yes even where policy would not.
"""
from __future__ import annotations

from dataclasses import dataclass

from friday import capabilities
from friday import execution_economics as ee

TINY, LOCAL, INTEGRATION, CORE = "tiny", "local", "integration", "core"

#: Radii that always need a yes, whatever the policy table says.
_ALWAYS_CONFIRM = (INTEGRATION, CORE)

_REASONING = {TINY: ee.TIER_ECONOMY, LOCAL: ee.TIER_STANDARD,
              INTEGRATION: ee.TIER_DEEP, CORE: ee.TIER_DEEP}


@dataclass(frozen=True)
class DryRun:
    """What a call would do, stated before it is allowed to do it."""

    tool_id: str
    arguments: dict
    blast_radius: str
    expected_effects: tuple[str, ...]
    requires_confirmation: bool
    reasoning_tier: str
    verification: str
    acted: bool = False          # a dry run never acts


def blast_radius(cap: capabilities.Capability) -> str:
    """The radius a capability's worst case reaches."""
    if cap.risk in ("HIGH", "IRREVERSIBLE"):
        return CORE
    if cap.side_effect == "external_action" or cap.risk == "MEDIUM":
        return INTEGRATION
    if cap.side_effect == "write":
        return LOCAL
    return TINY


def _effects(cap: capabilities.Capability) -> tuple[str, ...]:
    if cap.side_effect in ("none", "read"):
        return ()
    kind = ("an external action outside FRIDAY"
            if cap.side_effect == "external_action"
            else "a write to local state")
    effects = [f"{cap.id}: {kind} ({cap.description})"]
    if cap.requires_edge:
        effects.append("touches the user's device")
    if cap.reversible is False or cap.risk == "IRREVERSIBLE":
        effects.append("cannot be undone")
    if cap.data_classes:
        effects.append("involves " + ", ".join(cap.data_classes) + " data")
    return tuple(effects)


def _unknown(tool_id: str) -> capabilities.Capability:
    """An id the registry has never heard of is unaudited, so worst case."""
    return capabilities.Capability(
        id=tool_id, description="not in the capability registry",
        execution_scope="user_device", side_effect="external_action",
        risk="IRREVERSIBLE", requires_confirmation=True, reversible=False)


def dry_run(tool_id: str, arguments: dict | None = None, *,
            capability: capabilities.Capability | None = None) -> DryRun:
    """Expected effects of `tool_id`, without calling it."""
    cap = capability or capabilities.by_id(tool_id) or _unknown(tool_id)
    radius = blast_radius(cap)
    econ = ee.TaskEconomics(
        kind="code_change", novelty="medium", ambiguity="medium",
        blast_radius=radius,
        consequence="high" if radius == CORE else
        "low" if radius == TINY else "medium")
    verification, _ = ee.verification_depth(econ)
    return DryRun(
        tool_id=tool_id, arguments=dict(arguments or {}),
        blast_radius=radius, expected_effects=_effects(cap),
        requires_confirmation=bool(cap.requires_confirmation)
        or radius in _ALWAYS_CONFIRM,
        reasoning_tier=_REASONING[radius], verification=verification)


def propose(book, run_id: str, tool_id: str, arguments: dict | None = None):
    """Dry-run, then ask for the yes with the effects in the question.

    Returns (plan, confirmation); the confirmation is None when no yes is
    needed. The yes is bound to this exact tool and arguments by
    `confirmation.Book`, so it cannot be spent on a different action.
    """
    plan = dry_run(tool_id, arguments)
    if not plan.requires_confirmation:
        return plan, None
    effects = "; ".join(plan.expected_effects) or "no state change"
    question = (f"Run {tool_id}? Blast radius {plan.blast_radius}. "
                f"Expected effects: {effects}")
    pending = book.ask(run_id, tool_id, tool_id, question,
                       arguments=plan.arguments)
    return plan, pending
