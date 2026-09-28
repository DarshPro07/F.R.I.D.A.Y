"""
Which skill a task needs, loaded lazily, tokens counted.

Level 0 is metadata only (name, description, tags, triggers, task, scope) -
a few hundred bytes per skill, never the body. The router ranks those
against the request and returns the few that match. Level 1, the SKILL.md
body, is read only for what was selected; Level 2, a reference file, only
when the procedure asks for it. Every load is measured, so "the skill
system reduced context" is a number, not a claim.

Deterministic on purpose: lexical overlap over normalised tokens, with
declared triggers weighted above free text and `never_for` as a veto. A
model can still be asked to choose among the candidates the router
returns; it is never asked to read five hundred skills to find one.

Conflicts: two selectable skills that claim the same `task` are resolved
by scope specificity (repository > project > user > organization >
global), then by newer version. Equal on both is an explicit CONFLICT,
surfaced rather than silently picked.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from friday.skills import contracts as K
from friday.skills import files as F

_TOKEN = re.compile(r"[a-z0-9][a-z0-9._+-]*")
_STOP = frozenset("""a an the and or of to for in on at by with from into over under is are was be
been do does did how what which when where who why can could should would will i me my we our you your
it its this that these those please friday help make use using""".split())

#: A skill needs at least this much evidence to be selected at all.
MIN_SCORE = 0.25


def tokens(text: str) -> list[str]:
    return [t for t in _TOKEN.findall((text or "").lower()) if t not in _STOP and len(t) > 1]


def _overlap(query: set[str], text: str) -> float:
    words = set(tokens(text))
    if not words or not query:
        return 0.0
    return len(query & words) / len(query)


def score(query: str, record: K.SkillRecord) -> float:
    """0..1+: how strongly the request points at this skill."""
    q = set(tokens(query))
    if not q:
        return 0.0
    lowered = (query or "").lower()
    for phrase in record.never_for:
        if phrase and phrase.lower() in lowered:
            return 0.0
    best = 0.0
    for phrase in record.triggers:
        if phrase and phrase.lower() in lowered:
            best = max(best, 1.0)                           # an exact trigger phrase
        else:
            best = max(best, 0.9 * _overlap(q, phrase))
    best = max(best, 0.8 * _overlap(q, record.name.replace("-", " ") + " " + " ".join(record.tags)))
    best = max(best, 0.6 * _overlap(q, record.description))
    return round(best, 3)


def rank(query: str, records: list[K.SkillRecord], *, selectable_only: bool = True) -> list[tuple[K.SkillRecord, float]]:
    candidates = [r for r in records if not selectable_only or r.state in K.SELECTABLE]
    scored = [(r, score(query, r)) for r in candidates]
    scored = [(r, s) for r, s in scored if s >= MIN_SCORE]
    scored.sort(key=lambda pair: (-pair[1], -K.SCOPE_SPECIFICITY.get(pair[0].scope, 0), pair[0].name))
    return scored


def resolve_conflicts(ranked: list[tuple[K.SkillRecord, float]]) -> tuple[list[K.SkillRecord], list[dict]]:
    """Collapse skills that claim the same task; report what could not be decided."""
    chosen: list[K.SkillRecord] = []
    conflicts: list[dict] = []
    by_task: dict[str, list[K.SkillRecord]] = {}
    for record, _ in ranked:
        if record.task:
            by_task.setdefault(record.task, []).append(record)
        else:
            chosen.append(record)
    for task, group in by_task.items():
        if len(group) == 1:
            chosen.append(group[0])
            continue
        group.sort(key=lambda r: (-K.SCOPE_SPECIFICITY.get(r.scope, 0), tuple(-x for x in K.parse_version(r.version))))
        first, second = group[0], group[1]
        same_scope = K.SCOPE_SPECIFICITY.get(first.scope, 0) == K.SCOPE_SPECIFICITY.get(second.scope, 0)
        same_version = K.parse_version(first.version) == K.parse_version(second.version)
        if same_scope and same_version:
            conflicts.append({"task": task, "skills": [r.name for r in group],
                              "reason": "same scope and version; declare precedence or merge them"})
            continue
        chosen.append(first)
    return chosen, conflicts


@dataclass
class Selection:
    query: str
    selected: list[K.SkillRecord]
    scores: dict[str, float]
    conflicts: list[dict]
    considered: int
    #: what entered context, in estimated tokens: metadata for every
    #: candidate, bodies only for the selected
    index_tokens: int = 0
    loaded_tokens: int = 0
    loaded: dict[str, str] = field(default_factory=dict)

    def names(self) -> list[str]:
        return [r.name for r in self.selected]

    def to_dict(self) -> dict:
        return {"query": self.query, "selected": self.names(), "scores": self.scores,
                "conflicts": self.conflicts, "considered": self.considered,
                "index_tokens": self.index_tokens, "loaded_tokens": self.loaded_tokens}


def index_line(record: K.SkillRecord) -> str:
    """What Level 0 costs: one line per skill, like Hermes's skills_list."""
    return f"{record.name}: {record.description[:160]} [{', '.join(record.tags[:6])}]"


class SkillRouter:
    def __init__(self, registry, roots: dict[str, "Path"] | None = None) -> None:
        self.registry = registry
        self.roots = roots

    def candidates(self) -> list[K.SkillRecord]:
        return [r for r in self.registry.list() if r.state in K.SELECTABLE]

    def select(self, query: str, *, limit: int = 3, load: bool = True) -> Selection:
        records = self.candidates()
        ranked = rank(query, records)
        chosen, conflicts = resolve_conflicts(ranked)
        chosen = chosen[:limit]
        selection = Selection(query=query, selected=chosen,
                              scores={r.name: s for r, s in ranked},
                              conflicts=conflicts, considered=len(records),
                              index_tokens=sum(K.estimate_tokens(index_line(r)) for r in records))
        if load:
            for record in chosen:
                body = self.body(record)
                if body:
                    selection.loaded[record.name] = body
                    selection.loaded_tokens += K.estimate_tokens(body)
        return selection

    def body(self, record: K.SkillRecord) -> str:
        """Level 1: the SKILL.md of one selected skill."""
        skill = F.SkillFiles(__import__("pathlib").Path(record.path)) if record.path else None
        if skill is None or not skill.exists():
            return ""
        return skill.text()

    def reference(self, record: K.SkillRecord, relative: str) -> str:
        """Level 2: one supporting file, on demand."""
        if not record.path:
            return ""
        parts = __import__("pathlib").Path(relative).parts
        if not parts or parts[0] not in K.ALLOWED_SUBDIRS or ".." in parts:
            raise K.SkillError(f"references live under {', '.join(K.ALLOWED_SUBDIRS)}: {relative!r}")
        path = __import__("pathlib").Path(record.path).joinpath(*parts)
        return path.read_text(encoding="utf-8", errors="replace") if path.is_file() else ""

    def hints_for(self, query: str, *, limit: int = 3) -> list[str]:
        """Names for `TaskBundle.skill_hints`: Hermes loads at most these."""
        return self.select(query, limit=limit, load=False).names()
