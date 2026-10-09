"""
When a skill should exist.

Input: the trace of a finished objective - what was asked, whether the
verifier passed, which tools ran and how, what went wrong on the way, what
the owner corrected, and the path that finally worked. Output: NO_SKILL,
or a proposal with the reason, the promotion criteria it satisfies
(`friday.skill_ladder.CRITERIA`, unchanged), and a draft SKILL.md.

The anti-pollution rule is the ladder's, applied here before anything is
written: a candidate needs at least one criterion WITH evidence, and an
unverified objective never becomes one. A one-off that succeeded first
time with two tool calls is memory at most, not a skill.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field

from friday.skills import contracts as K
from friday.skills import files as F
from friday.skills import lint as L
from friday.skills.router import tokens

REPEATED = "repeated_procedure"
REDISCOVERY = "expensive_rediscovery"
SAFETY = "safety_critical"
OPERATIONAL = "project_operational_knowledge"

#: A workflow this long is worth keeping; Hermes's own heuristic is 5+.
MIN_TOOL_CALLS = 5


@dataclass
class ToolCall:
    name: str
    ok: bool = True
    seconds: float = 0.0
    error: str = ""


@dataclass
class ObjectiveTrace:
    objective: str
    verified: bool
    tool_calls: list[ToolCall] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    corrections: list[str] = field(default_factory=list)      # what the owner corrected
    final_path: list[str] = field(default_factory=list)       # the steps that worked, in order
    evidence: list[str] = field(default_factory=list)         # verifier output, read-backs
    files_touched: list[str] = field(default_factory=list)    # repository paths
    objective_id: str = ""
    repeated_signature_count: int = 0                         # earlier objectives with the same signature
    explicit_request: bool = False                            # "learn this", "make this a skill"


def signature(objective: str) -> str:
    """A stable key for 'the same kind of objective', for repeat detection."""
    words = sorted(set(tokens(objective)))
    return hashlib.sha256(" ".join(words).encode("utf-8")).hexdigest()[:16]


def slug(objective: str, limit: int = 5) -> str:
    words = [w for w in tokens(objective) if w.isalnum()][:limit]
    name = "-".join(words) or "procedure"
    name = re.sub(r"[^a-z0-9.-]+", "-", name.lower()).strip("-")
    return name[:K.MAX_NAME_LENGTH].rstrip("-.") or "procedure"


def criteria_for(trace: ObjectiveTrace) -> list[tuple[str, str]]:
    """(criterion, evidence) pairs the trace supports."""
    found: list[tuple[str, str]] = []
    calls = len(trace.tool_calls)
    failed = [c for c in trace.tool_calls if not c.ok]
    if calls >= MIN_TOOL_CALLS:
        found.append((REDISCOVERY, f"{calls} tool calls to reach a verified result"))
    if failed and trace.verified:
        found.append((REDISCOVERY, f"{len(failed)} dead end(s) before the working path: "
                      + "; ".join((c.error or c.name) for c in failed[:3])))
    if trace.corrections:
        found.append((OPERATIONAL if trace.files_touched else REDISCOVERY,
                      "the owner corrected the approach: " + "; ".join(trace.corrections[:2])))
    if trace.repeated_signature_count >= 1:
        found.append((REPEATED, f"seen {trace.repeated_signature_count + 1} times"))
    if trace.files_touched:
        found.append((OPERATIONAL, "touches " + ", ".join(trace.files_touched[:5])))
    risk = L.classify_risk(" ".join(trace.final_path))
    if K.RISK_ORDER[risk] >= K.RISK_ORDER[K.EXTERNAL_WRITE]:
        found.append((SAFETY, f"the working path is {risk}; the order of steps matters"))
    if trace.explicit_request:
        found.append((REPEATED, "the owner asked for this to become a skill"))
    return found


def draft_skill_md(trace: ObjectiveTrace, name: str, criteria: list[tuple[str, str]]) -> str:
    description = trace.objective.strip().rstrip(".")[:K.MAX_DESCRIPTION_LENGTH]
    scope = K.REPOSITORY if trace.files_touched else K.USER
    tags = [w for w in tokens(trace.objective) if len(w) > 3][:6]
    frontmatter = {
        "name": name,
        "description": description,
        "version": "1.0.0",
        "author": "friday",
        "metadata": {
            "hermes": {"tags": tags},
            "friday": {
                "scope": scope.lower(),
                "depends_on": trace.files_touched[:10],
                "tests": [],
                "triggers": [trace.objective.strip()[:120]],
                "publish": K.PUBLISH_LOCAL,
            },
        },
    }
    if trace.objective_id:
        # the structured link from the skill to the verified objective it came from
        frontmatter["metadata"]["friday"]["objective_id"] = trace.objective_id
    steps = trace.final_path or [c.name for c in trace.tool_calls if c.ok]
    pitfalls = [c.error or c.name for c in trace.tool_calls if not c.ok] + list(trace.errors)
    body = [f"# {description[:80]}", "",
            "## When to use", "",
            f"When asked to: {description}.", "",
            "## Procedure", ""]
    body += [f"{i}. {step}" for i, step in enumerate(steps, 1)] or ["1. (no steps recorded)"]
    body += ["", "## Pitfalls", ""]
    body += [f"- {p}" for p in pitfalls[:6]] or ["- none recorded"]
    if trace.corrections:
        body += ["", "## Owner corrections", ""] + [f"- {c}" for c in trace.corrections[:4]]
    body += ["", "## Verification", ""]
    body += [f"- {e}" for e in trace.evidence[:6]] or ["- the objective verifier passed"]
    return F.render_skill_md(frontmatter, "\n".join(body))


class SkillCandidateDetector:
    def __init__(self, ladder=None, *, min_tool_calls: int = MIN_TOOL_CALLS) -> None:
        self.ladder = ladder
        self.min_tool_calls = min_tool_calls

    def detect(self, trace: ObjectiveTrace) -> K.SkillProposal | None:
        """NO_SKILL (None) or a proposal. Records the candidate on the ladder
        when one is configured; the ladder's refusal is final."""
        if not trace.verified:
            return None
        if len(trace.tool_calls) < self.min_tool_calls and not trace.corrections \
                and not trace.repeated_signature_count and not trace.explicit_request \
                and not any(not c.ok for c in trace.tool_calls):
            return None
        criteria = criteria_for(trace)
        if not criteria:
            return None
        name = slug(trace.objective)
        content = draft_skill_md(trace, name, criteria)
        evidence = [f"{criterion}: {why}" for criterion, why in criteria] + list(trace.evidence[:4])
        if self.ladder is not None:
            outcome = self.ladder.capture(name, "\n".join(trace.final_path) or trace.objective,
                                          criteria=sorted({c for c, _ in criteria}),
                                          evidence="\n".join(evidence))
            if outcome.get("status") != "captured":
                return None
        risk = L.classify_risk(" ".join(trace.final_path))
        reuse = ("repeated already" if trace.repeated_signature_count else
                 "likely: multi-step, verified, with dead ends worth not repeating")
        return K.SkillProposal(
            name=name,
            reason="; ".join(f"{criterion} ({why})" for criterion, why in criteria),
            criteria=sorted({c for c, _ in criteria}),
            expected_reuse=reuse,
            scope=K.REPOSITORY if trace.files_touched else K.USER,
            risk=risk,
            content=content,
            dependencies=list(trace.files_touched[:10]),
            tests=[],
            evidence=evidence,
            source=f"objective:{trace.objective_id}" if trace.objective_id else "objective",
        )
