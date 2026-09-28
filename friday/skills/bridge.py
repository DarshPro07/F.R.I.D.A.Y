"""
Memory <-> skill.

Memory answers "what is true" in a sentence; a skill answers "how do we do
this" in a procedure. Loading a whole skill to recall one fact wastes the
tokens the skill system exists to save, and burying a procedure in twenty
memory rows loses the order that makes it work. So:

    a skill's `## Facts` / `## Remember` bullets  -> memory candidates
    repeated procedure-shaped memories            -> a skill proposal

Both directions go through the existing gates: `friday.memory_promotion`
decides whether a fact is trustworthy enough to keep, and the candidate
detector's criteria decide whether a procedure is worth a skill.
"""

from __future__ import annotations

import re

from friday.skills import candidates as C
from friday.skills import contracts as K
from friday.skills import files as F
from friday.skills.router import tokens

FACT_SECTIONS = ("facts", "remember", "quick reference")
_STEP_VERB = re.compile(r"^(run|open|check|use|start|stop|edit|set|copy|build|verify|install|call|click|type|select|read|write|delete|restart|export|import|create|rebuild|sync)\b", re.I)
MAX_FACT_CHARS = 200


def facts_from_skill(skill: F.SkillFiles) -> list[str]:
    """Short, durable statements a skill keeps under a facts section."""
    if not skill.exists():
        return []
    _, body = skill.read()
    facts: list[str] = []
    current = ""
    for line in body.splitlines():
        if line.startswith("#"):
            current = line.strip("# ").strip().lower()
            continue
        if current in FACT_SECTIONS and line.strip().startswith(("-", "*")):
            text = line.strip()[1:].strip()
            if text and len(text) <= MAX_FACT_CHARS:
                facts.append(text)
    return facts


def looks_like_step(statement: str) -> bool:
    return bool(_STEP_VERB.match(statement.strip()))


def procedure_groups(statements: list[str], *, min_steps: int = 3, min_overlap: int = 2) -> list[list[str]]:
    """Statements that read as steps and share a subject, grouped."""
    steps = [s.strip() for s in statements if looks_like_step(s)]
    groups: list[list[str]] = []
    for step in steps:
        words = set(tokens(step))
        for group in groups:
            shared = set(tokens(" ".join(group)))
            if len(words & shared) >= min_overlap:
                group.append(step)
                break
        else:
            groups.append([step])
    return [g for g in groups if len(g) >= min_steps]


def proposal_from_memories(statements: list[str], *, source: str = "memory",
                           detector: C.SkillCandidateDetector | None = None) -> K.SkillProposal | None:
    """When memory has accumulated a procedure, propose the skill it is."""
    groups = procedure_groups(statements)
    if not groups:
        return None
    steps = max(groups, key=len)
    subject = " ".join(sorted(set(tokens(" ".join(steps))))[:6])
    trace = C.ObjectiveTrace(objective=f"procedure recorded in memory: {subject}", verified=True,
                             tool_calls=[C.ToolCall(name=s[:40]) for s in steps],
                             final_path=steps, evidence=[f"{len(steps)} memory rows read as one procedure"],
                             repeated_signature_count=1, objective_id=source)
    return (detector or C.SkillCandidateDetector()).detect(trace)


def promote_facts(skill: F.SkillFiles, *, store=None, owner: str = "friday") -> list:
    """Hand a skill's facts to friday.memory_promotion; returns its decisions."""
    from friday import memory_promotion as MP
    from friday.contracts import READ_MATERIAL

    decisions = []
    for fact in facts_from_skill(skill):
        # A skill's file may come from anywhere: its facts are read material.
        candidate = MP.Candidate(statement=fact, kind="project_fact", source=f"skill:{skill.name}",
                                 owner=owner, scope="project", confidence=0.8, evidence=[f"skill {skill.name}"],
                                 provenance=READ_MATERIAL)
        decisions.append(MP.promote(candidate, store=store))
    return decisions
