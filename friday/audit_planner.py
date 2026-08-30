
"""
Auditing every capability, from the registry rather than from prose.

A spoken request to "audit everything you can do" produced 205 tasks, because
the planner read 1967 words as 205 instructions. That is the wrong shape from
the first step: the list of things Friday can do is not in the sentence, it is
in the registry, and the registry is data.

    the old way          1967 words -> split -> 205 nearest-capability guesses
                         -> 132 succeeded, 68 failed, 5 skipped, and the
                         failures were mostly the plan's fault

    this way             read the registry -> classify each entry by how it
                         could honestly be tested -> a bounded group per
                         domain -> one composite task each

The count comes from the registry, so it is right by construction, and every
registered capability is accounted for whether or not it can be tested. A
capability that cannot be safely exercised is not a gap in the audit; it is a
result, and it has a name.
"""

from __future__ import annotations

import re

from dataclasses import dataclass, field
from friday import audit_fixtures as F

from friday import semantics as S

#: How a capability can honestly be checked. Every registered capability gets
#: exactly one of these, and the ones that are not tests are not failures.
SAFE_REAL_TEST = "SAFE_REAL_TEST"        # a fixture this run owns

READ_ONLY = "READ_ONLY"                  # observes; nothing to clean up

PURE = "PURE"                            # transforms its arguments

SESSION_REQUIRED = "SESSION_REQUIRED"    # needs something live to talk to

CONFIRM_REQUIRED = "CONFIRM_REQUIRED"    # a person has to say yes

NOT_CONFIGURED = "NOT_CONFIGURED"        # no implementation reachable here

HARDWARE_REQUIRED = "HARDWARE_REQUIRED"  # needs a device that may not exist

FIXTURE_REQUIRED = F.FIXTURE_REQUIRED

STRATEGIES = (
    SAFE_REAL_TEST,
    READ_ONLY,
    PURE,
    SESSION_REQUIRED,
    CONFIRM_REQUIRED,
    NOT_CONFIGURED,
    HARDWARE_REQUIRED,
    FIXTURE_REQUIRED,
)

#: Targets that mean a physical device has to be present.
_HARDWARE = frozenset({"VISION", "DISPLAY"})

#: Operations that only observe. Safe to run against the real machine.
_OBSERVING = frozenset({S.READ, S.LIST, S.SEARCH, S.FOLLOW_UP})


@dataclass
class AuditGroup:
    """One domain's worth of audit, as a bounded piece of work."""

    name: str
    target: str
    capabilities: list[str] = field(default_factory=list)
    strategy_counts: dict[str, int] = field(default_factory=dict)

    @property
    def testable(self) -> int:
        return (self.strategy_counts.get(SAFE_REAL_TEST, 0)
                + self.strategy_counts.get(READ_ONLY, 0)
                + self.strategy_counts.get(PURE, 0))


@dataclass
class AuditPlan:
    """The whole audit: every registered capability, in exactly one group."""

    groups: list[AuditGroup] = field(default_factory=list)
    strategy: dict[str, str] = field(default_factory=dict)
    registered: int = 0

    @property
    def accounted_for(self) -> int:
        return sum(len(group.capabilities) for group in self.groups)

    def counts(self) -> dict[str, int]:
        found: dict[str, int] = {}
        for strategy in self.strategy.values():
            found[strategy] = found.get(strategy, 0) + 1
        return found

#: What the boss says when they mean this rather than a list of errands.
_AUDIT_REQUEST = re.compile(
    '\\b(?:audit|test|verify|check)\\b.{0,40}\\b(?:every|all|each|complete|entire|whole)\\b.{0,30}\\b(?:capabilit|abilit|tool|feature|function)|\\b(?:capability|capabilities) audit\\b|\\b(?:every|all) (?:capabilit|abilit|tool)|\\b(?:audit|test|verify|check)\\b.{0,20}\\beverything\\b.{0,25}(?:can do|you can|able to)',
    re.IGNORECASE,
)


def is_an_audit_request(text: str) -> bool:
    """
    Whether this is "check everything you can do" rather than a list of jobs.

    Recognised as its own intent because the alternative is what happened:
    the planner tried to derive the capability list from the sentence, and a
    long sentence produced a long wrong list.
    """
    return bool(_AUDIT_REQUEST.search(text or ""))


def strategy_for(capability_id: str) -> str:
    """
    How this capability could honestly be checked.

    Reads the live registry rather than a list written down here: a hand-kept
    list would be wrong the first time somebody added a capability, and being
    wrong quietly is the failure mode this whole audit exists to avoid.
    """
    from friday import capabilities as C
    from friday import capability_runtime as R
    from friday import policy as p

    capability = C.by_id(capability_id)
    if capability is None:
        return NOT_CONFIGURED

    if capability_id == "ada_ask":
        # Needs a live executor run to answer into; it resolves, but an
        # audit has no run for it.
        return SESSION_REQUIRED
    if capability_id not in R.reachable():
        # Registered but nothing to call: the pure transforms have no
        # run-taking implementation at all.
        if capability_id in ("format_json", "word_count"):
            return PURE
        return NOT_CONFIGURED

    engine = p.PolicyEngine()
    for tool_id in capability.policy_tool_ids():
        if tool_id in p.TOOL_CATEGORIES:
            if engine.decide(tool_id).decision in (p.CONFIRM, p.DENY):
                return CONFIRM_REQUIRED
            break

    operation, target = S.for_capability(capability_id)
    if target in _HARDWARE and operation not in _OBSERVING:
        return HARDWARE_REQUIRED
    if target == "VISION":
        return HARDWARE_REQUIRED
    if operation in _OBSERVING:
        return READ_ONLY
    return SAFE_REAL_TEST


def plan_audit() -> AuditPlan:
    """
    The audit, derived from the registry.

    Every registered capability lands in exactly one group with exactly one
    strategy, so the report can account for all of them - which is the actual
    requirement, and the thing 205 clause-derived tasks could never satisfy.
    """
    from friday import capabilities as C

    plan = AuditPlan(registered=len(C._ALL))
    groups: dict[str, AuditGroup] = {}

    for capability in sorted(C._ALL, key=lambda item: item.id):
        _operation, target = S.for_capability(capability.id)
        strategy = strategy_for(capability.id)
        plan.strategy[capability.id] = strategy

        group = groups.get(target)
        if group is None:
            group = groups[target] = AuditGroup(name=target.lower(),
                                                target=target)
        group.capabilities.append(capability.id)
        group.strategy_counts[strategy] = \
            group.strategy_counts.get(strategy, 0) + 1

    plan.groups = [groups[key] for key in sorted(groups)]
    return plan

_BECAUSE = {
    PURE: '',
    SESSION_REQUIRED: 'needs a live session to talk to',
    CONFIRM_REQUIRED: 'needs a person to say yes before it runs',
    NOT_CONFIGURED: 'has no implementation reachable on this machine',
    HARDWARE_REQUIRED: 'needs a device that may not be attached',
    F.FIXTURE_REQUIRED: F.NOT_AUDITABLE_HERE,
}


def children_of(group: AuditGroup, plan: AuditPlan, workspace: 'F.Workspace') -> list[dict]:
    """
    One leaf per capability in the group, each either runnable or refused
    with a reason.

    Three ways a capability ends up not run, and none of them is a failure:
    its strategy was never a test, it needs an argument this audit cannot
    honestly build, or it resolves to nothing here. All three are recorded
    before anything is attempted, so the report accounts for every registered
    capability whether or not it was exercised.
    """
    from friday import capability_runtime as R

    children = []
    for capability_id in group.capabilities:
        strategy = plan.strategy.get(capability_id, NOT_CONFIGURED)
        if strategy not in (SAFE_REAL_TEST, READ_ONLY, PURE):
            children.append({"capability": capability_id,
                             "skipped_because": _BECAUSE[strategy]})
            continue
        required = R.required_arguments(capability_id)
        if required is None:
            children.append({
                "capability": capability_id,
                "skipped_because": _BECAUSE[NOT_CONFIGURED]})
            continue
        if not required and strategy != SAFE_REAL_TEST:
            children.append({"capability": capability_id, "arguments": {}})
            continue
        # A capability that takes arguments is exercised against a fixture
        # the audit builds for it, or not at all: an argument invented to
        # make the call go through would make the result mean nothing. The
        # fixture module decides what it can honestly build, and a
        # capability it cannot build for is recorded as needing one - a
        # different fact from "not configured", because it can be fixed by
        # writing a fixture rather than by wiring an implementation.
        arguments = F.arguments_for(capability_id, workspace)
        if arguments is None:
            plan.strategy[capability_id] = F.FIXTURE_REQUIRED
            children.append({
                "capability": capability_id,
                "skipped_because": _BECAUSE[F.FIXTURE_REQUIRED]})
            continue
        children.append({"capability": capability_id, "arguments": arguments})
    return children


def as_goals(plan: AuditPlan, *, workspace: 'F.Workspace | None' = None) -> list:
    """
    The audit as planner goals - one composite per group, not one per tool.

    A group is a composite: the boss sees "files: 11 capabilities", the
    executor sees the children. Flattening it back into 125 top-level tasks
    would reproduce the unreadable list this replaced, just with better
    capability choices.

    The workspace is made here rather than by a child, because the children
    of a group are unordered by design - a fixture built by one leaf would be
    a fixture the others cannot rely on.
    """
    from friday import objectives as O
    from friday import planner as P

    workspace = workspace or F.Workspace()
    goals = []
    for index, group in enumerate(plan.groups, start=1):
        children = children_of(group, plan, workspace)
        runnable = sum(1 for child in children if "arguments" in child)
        goals.append(P.Goal(
            goal_id=f"g{index}",
            intent=f"audit the {group.name} capabilities",
            operation=S.FOLLOW_UP,
            target="OBJECTIVE",
            entity=group.name,
            capability=O.COMPOSITE,
            confidence=1.0,
            why=(f"{len(group.capabilities)} registered, {runnable} "
                 f"exercised here"),
            children=tuple(children),
        ))
    return goals
