"""
Not every problem is code. When FRIDAY (or the owner) notices something
should improve, the fix belongs to exactly one layer, and choosing the
wrong one is how a policy becomes a comment or a fact becomes a module:

    MEMORY         a fact keeps being re-asked or re-discovered
    SKILL          a multi-step procedure keeps being rediscovered
    CODE           a tool is missing, wrong, or crashes
    CONFIG         a default, threshold, path or key is wrong
    POLICY         an action should need approval, or must never happen
    TEST           the check that should have caught it does not exist
    DOCUMENTATION  the knowledge exists but nobody can find it

Deterministic keyword rules over the symptom text; the reasons are
returned with the verdict so a caller can disagree out loud.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

MEMORY, SKILL, CODE, CONFIG, POLICY, TEST, DOCUMENTATION = (
    "MEMORY", "SKILL", "CODE", "CONFIG", "POLICY", "TEST", "DOCUMENTATION")
CHANGE_TYPES = (MEMORY, SKILL, CODE, CONFIG, POLICY, TEST, DOCUMENTATION)

#: (type, pattern, why). Earlier rows win ties; POLICY and TEST are checked
#: first because a safety gap is not a bug and a missing check is not a fix.
RULES: tuple[tuple[str, re.Pattern, str], ...] = (
    (POLICY, re.compile(r"\b(should (need|require|ask for) (approval|confirmation)|without (approval|asking|confirmation)|must never|forbidden|unauthori[sz]ed|dangerous action|policy)\b", re.I),
     "an approval or a prohibition is a policy decision"),
    (TEST, re.compile(r"\b(no test|untested|missing (test|check)|regression|slipped through|should have (been )?caught|no coverage|flaky)\b", re.I),
     "the missing check is the fix"),
    (CONFIG, re.compile(r"\b(default|threshold|timeout|port|path|env(ironment)? var(iable)?|setting|key is wrong|misconfigured|wrong (model|provider|url)|too (low|high|short|long))\b", re.I),
     "a value, not behaviour"),
    (SKILL, re.compile(r"\b(procedure|steps?|workflow|how to|rediscover|every time we|again and again|repeat(ed|edly)? (the )?(same )?(process|approach|workflow)|forgot how|had to work out)\b", re.I),
     "a repeatable procedure belongs in a skill"),
    (MEMORY, re.compile(r"\b(prefers?|preference|remember that|keeps? asking|always (uses?|wants?)|fact|his |her |their |the owner('s)? (name|address|time ?zone|choice))\b", re.I),
     "a durable fact belongs in memory"),
    (DOCUMENTATION, re.compile(r"\b(docs?|documentation|readme|undocumented|nobody knew|could not find (the|any) (doc|guide|instructions)|explain(ed)? (nowhere|badly))\b", re.I),
     "the knowledge exists; its findability does not"),
    (CODE, re.compile(r"\b(bug|crash|exception|traceback|raises?|returns? (the )?wrong|broken|does not work|doesn'?t work|fails? (to|with)|tool (is )?missing|not implemented|typeerror|keyerror|attributeerror)\b", re.I),
     "behaviour is wrong or absent; that is code"),
)


@dataclass
class Classification:
    change_type: str
    reasons: list[str] = field(default_factory=list)
    alternatives: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {"change_type": self.change_type, "reasons": self.reasons, "alternatives": self.alternatives}


def classify(symptom: str) -> Classification:
    """The one layer a symptom's fix belongs to, with the runners-up named."""
    text = (symptom or "").strip()
    matched = [(kind, why) for kind, pattern, why in RULES if pattern.search(text)]
    if not matched:
        return Classification(CODE, ["no signal matched; behaviour is the default suspect"], [])
    winner, why = matched[0]
    return Classification(winner, [why], [kind for kind, _ in matched[1:] if kind != winner])
