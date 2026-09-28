"""
Incident noise out, procedure in; duplicates merged.

A draft written from a trace says "on 2026-09-14 at 23:32 run 7aa7a99f
failed in C:\\Users\\admin\\..."; a skill says "when X, do Y, verify Z".
The curator makes that conversion deterministically - dates, run ids,
clock times and machine paths become neutral placeholders, steps become
imperative, duplicate lines collapse - and checks the proposal against
what already exists. Near-duplicates are merged into the existing skill as
a patch, never added beside it: five variants of one procedure are worse
than none.

Anything a model could do better (rewording, judging relevance) is left to
a caller that has one; nothing here needs it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from friday.skills import contracts as K
from friday.skills import files as F
from friday.skills import lint as L
from friday.skills.router import tokens

#: Overlap at or above this is the same procedure.
MERGE_THRESHOLD = 0.6

_DATE = re.compile(r"\b20\d\d-\d\d-\d\d\b")
_TIME = re.compile(r"\b\d{1,2}:\d\d(?::\d\d)?\s*(?:am|pm|ist|utc)?\b", re.I)
_RUN_ID = re.compile(r"\b(run|proc|task|job)[-_ ]?[0-9a-f]{8,}\b", re.I)
_HEX_ID = re.compile(r"\b[0-9a-f]{12,}\b")
_WIN_USER = re.compile(r"[A-Za-z]:\\Users\\[^\\\s\"']+")
_UNIX_HOME = re.compile(r"(?<![\w/])/(?:home|Users)/[^/\s\"']+")
_NARRATION = re.compile(r"^(?:\d+\.\s*)?(?:i|we|then i|then we|first i|so i|next i)\s+(ran|run|did|opened|edited|checked|used|started|fixed|added|removed|changed|created|wrote)\b", re.I)
_PAST_TO_IMPERATIVE = {
    "ran": "Run", "run": "Run", "did": "Do", "opened": "Open", "edited": "Edit", "checked": "Check",
    "used": "Use", "started": "Start", "fixed": "Fix", "added": "Add", "removed": "Remove",
    "changed": "Change", "created": "Create", "wrote": "Write",
}


@dataclass
class CurationResult:
    action: str                                  # create | merge | reject
    content: str                                 # the curated SKILL.md
    target: str = ""                             # the existing skill to merge into
    notes: list[str] = field(default_factory=list)
    extra_files: dict[str, str] = field(default_factory=dict)
    similarity: float = 0.0

    def to_dict(self) -> dict:
        return {"action": self.action, "target": self.target, "notes": self.notes,
                "similarity": self.similarity, "extra_files": sorted(self.extra_files)}


def strip_incident_noise(text: str) -> tuple[str, list[str]]:
    """Dates, times, run ids and machine paths -> neutral placeholders."""
    notes: list[str] = []
    replacements = (
        (_DATE, "<date>", "dates"), (_TIME, "<time>", "clock times"),
        (_RUN_ID, "<run-id>", "run ids"), (_HEX_ID, "<id>", "hex ids"),
        (_WIN_USER, "%USERPROFILE%", "Windows profile paths"), (_UNIX_HOME, "~", "home paths"),
    )
    for pattern, placeholder, what in replacements:
        text, count = pattern.subn(placeholder, text)
        if count:
            notes.append(f"replaced {count} {what}")
    return text, notes


def generalize_step(step: str) -> str:
    """'I ran the build script' -> 'Run the build script'."""
    text = step.strip()
    match = _NARRATION.match(text)
    if match:
        verb = _PAST_TO_IMPERATIVE.get(match.group(1).lower(), match.group(1).capitalize())
        text = verb + text[match.end():]
    return text


def dedupe_lines(body: str) -> tuple[str, int]:
    seen: set[str] = set()
    kept: list[str] = []
    removed = 0
    for line in body.splitlines():
        key = line.strip().lower()
        if key and not key.startswith("#") and key in seen and len(key) > 12:
            removed += 1
            continue
        if key:
            seen.add(key)
        kept.append(line)
    return "\n".join(kept), removed


def similarity(a_text: str, b_text: str) -> float:
    """Jaccard over content tokens."""
    a, b = set(tokens(a_text)), set(tokens(b_text))
    if not a or not b:
        return 0.0
    return round(len(a & b) / len(a | b), 3)


def _fingerprint(record: K.SkillRecord) -> str:
    return " ".join([record.name.replace("-", " "), record.description, " ".join(record.tags),
                     " ".join(record.triggers)])


def curate_content(content: str) -> tuple[str, list[str], dict[str, str]]:
    """Clean one SKILL.md draft: noise, narration, duplicates, size."""
    frontmatter, body = F.parse_skill_md(content)
    notes: list[str] = []
    body, noise_notes = strip_incident_noise(body)
    notes += noise_notes
    lines = []
    changed = 0
    for line in body.splitlines():
        stripped = line.lstrip()
        indent = line[: len(line) - len(stripped)]
        if re.match(r"^(\d+\.|-)\s", stripped):
            marker, rest = stripped.split(" ", 1)
            new = generalize_step(rest)
            if new != rest.strip():
                changed += 1
            lines.append(f"{indent}{marker} {new}")
        else:
            lines.append(line)
    if changed:
        notes.append(f"made {changed} step(s) imperative")
    body, removed = dedupe_lines("\n".join(lines))
    if removed:
        notes.append(f"removed {removed} duplicate line(s)")
    lowered = body.lower()
    for section in L.EXPECTED_SECTIONS:
        if section not in lowered:
            body += f"\n\n## {section.title()}\n\n- (to be written)\n"
            notes.append(f"added an empty '{section}' section")
    extra: dict[str, str] = {}
    if len(body) > L.LONG_SKILL_CHARS:
        head, tail = body[: L.LONG_SKILL_CHARS], body[L.LONG_SKILL_CHARS:]
        cut = head.rfind("\n## ")
        if cut > 0:
            head, tail = body[:cut], body[cut:]
        extra["references/details.md"] = tail.strip() + "\n"
        body = head.rstrip() + "\n\n## Details\n\nSee references/details.md.\n"
        notes.append("moved the long tail into references/details.md")
    if "description" in frontmatter:
        frontmatter["description"], _ = strip_incident_noise(str(frontmatter["description"]))
    return F.render_skill_md(frontmatter, body), notes, extra


class SkillCurator:
    def __init__(self, registry, *, threshold: float = MERGE_THRESHOLD) -> None:
        self.registry = registry
        self.threshold = threshold

    def duplicates_of(self, text: str, *, exclude: str = "") -> list[tuple[K.SkillRecord, float]]:
        found = []
        for record in self.registry.list():
            if record.name == exclude or record.state in (K.DEPRECATED, K.DISABLED):
                continue
            score = similarity(text, _fingerprint(record))
            if score >= self.threshold:
                found.append((record, score))
        found.sort(key=lambda pair: -pair[1])
        return found

    def curate(self, proposal: K.SkillProposal) -> CurationResult:
        content, notes, extra = curate_content(proposal.content)
        findings = L.scan_text(content)
        fatal = [f for f in findings if f.level == L.FAIL]
        if fatal:
            return CurationResult(action="reject", content=content,
                                  notes=notes + [f"{f.code}: {f.detail}" for f in fatal], extra_files=extra)
        frontmatter, body = F.parse_skill_md(content)
        probe = " ".join([proposal.name.replace("-", " "), str(frontmatter.get("description", "")),
                          " ".join(F.as_list(F.hermes_metadata(frontmatter).get("tags"))),
                          " ".join(F.as_list(F.friday_metadata(frontmatter).get("triggers")))])
        duplicates = self.duplicates_of(probe, exclude=proposal.name)
        if duplicates:
            target, score = duplicates[0]
            notes.append(f"overlaps {target.name} ({score:.0%}); merge instead of a new skill")
            return CurationResult(action="merge", content=content, target=target.name,
                                  notes=notes, extra_files=extra, similarity=score)
        return CurationResult(action="create", content=content, notes=notes, extra_files=extra)

    @staticmethod
    def merge_into(existing_body: str, incoming_body: str) -> tuple[str, int]:
        """Append the incoming procedure's lines the existing skill lacks,
        under one section, so a merge adds knowledge without rewriting."""
        have = {line.strip().lower() for line in existing_body.splitlines() if line.strip()}
        new_lines = [line for line in incoming_body.splitlines()
                     if line.strip() and not line.startswith("#") and line.strip().lower() not in have]
        if not new_lines:
            return existing_body, 0
        merged = existing_body.rstrip() + "\n\n## Merged notes\n\n" + "\n".join(new_lines) + "\n"
        return merged, len(new_lines)
