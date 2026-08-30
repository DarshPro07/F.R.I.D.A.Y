"""
scientific-agent-skills: 163 domain procedures, minus the ones we may not use.

Bioinformatics, astronomy, crystallography, single-cell analysis, lab
instrument protocols - the kind of domain method Friday has no business
inventing and every reason to follow when asked. The pack is markdown, so
`integration_mode=SKILL`: nothing here executes upstream code.

## The licence gate is the point of this adapter

The repository is MIT at the root. Four of its skills are not. `skills/docx`,
`skills/pdf`, `skills/pptx` and `skills/xlsx` carry Anthropic terms that
forbid, in as many words, extracting the materials, retaining copies outside
the Services, reproducing them, and creating derivative works. Reading one
into a prompt is exactly the extraction they prohibit.

A hardcoded list of those four would be correct today and wrong the first time
upstream adds a fifth. So the gate is computed: every skill directory is
checked for its own licence file, and one that is not clearly permissive is
excluded and *stays* excluded, with the reason recorded. A skill with no
licence file of its own inherits the repository's MIT and is offered.

The rule is fail-closed. An unreadable or unrecognised licence excludes the
skill rather than allowing it, because the cost of being wrong in one
direction is a prompt containing something we were told not to copy, and in
the other direction is a skill nobody can use this week.

## Laziness

163 skills is far past what belongs in any prompt. `catalogue` returns names
and one-line descriptions, `search` narrows by keyword, and `skill` reads
exactly one file. There is no bulk read and there should never be one. The
licence scan is cached for the life of the process because it walks 163
directories and its answer only changes when the clone does.
"""

from __future__ import annotations

import functools
import re

from friday import fabric
from friday.fabric_adapters import _skillpack

UPSTREAM = "scientific-agent-skills"
SKILL_DIR = "skills"

#: Licence text that permits reading a procedure into a prompt. Anything that
#: matches none of these is treated as not permitting it.
PERMISSIVE_SIGNATURES = (
    r"Permission is hereby granted, free of charge",     # MIT
    r"Apache License",
    r"BSD \d-Clause",
    r"CC0 1\.0 Universal",
    r"Mozilla Public License",
)

#: Text that positively identifies terms we must not vendor. Matched only to
#: give a better reason than "unrecognised"; exclusion does not depend on it.
RESTRICTED_SIGNATURES = (
    ("Anthropic Skill terms: extraction, copying and derivative works are "
     "forbidden", r"ADDITIONAL RESTRICTIONS|Anthropic, PBC\. All rights reserved"),
    ("non-commercial", r"NonCommercial|CC[- ]BY[- ]NC"),
    ("source-available, production use gated", r"Enterprise Edition|"
                                               r"may only\s+be used in production"),
)

LICENCE_NAMES = ("LICENSE", "LICENSE.txt", "LICENSE.md", "LICENCE", "COPYING",
                 "NOTICE")

_DESCRIPTION = re.compile(r"^description:\s*(.+?)\s*$", re.M)
_FRONTMATTER = re.compile(r"\A---\r?\n(.*?)\r?\n---", re.S)


def _classify(text: str) -> tuple[bool, str]:
    """(may we read this, why not)."""
    for reason, pattern in RESTRICTED_SIGNATURES:
        if re.search(pattern, text, re.IGNORECASE):
            return False, reason
    for pattern in PERMISSIVE_SIGNATURES:
        if re.search(pattern, text, re.IGNORECASE):
            return True, ""
    return False, "licence present but not recognised as permissive"


@functools.lru_cache(maxsize=1)
def _blocked() -> dict[str, str]:
    """
    Skill name -> why it may not be offered.

    Cached: it walks every skill directory, and the answer changes only when
    the clone does. `fabric.reload()` does not clear it, so a test that
    rewrites the tree should call `_blocked.cache_clear()`.
    """
    root = _skillpack.pack_root(UPSTREAM) / SKILL_DIR
    if not root.is_dir():
        return {}
    blocked: dict[str, str] = {}
    for directory in sorted(p for p in root.iterdir() if p.is_dir()):
        for name in LICENCE_NAMES:
            candidate = directory / name
            if not candidate.is_file():
                continue
            try:
                text = candidate.read_text(encoding="utf-8", errors="replace")
            except OSError:
                blocked[directory.name] = f"{name} could not be read"
                break
            allowed, why = _classify(text[:6000])
            if not allowed:
                blocked[directory.name] = f"{name}: {why}"
            break
    return blocked


def _entries() -> dict[str, dict]:
    root = _skillpack.pack_root(UPSTREAM) / SKILL_DIR
    if not root.is_dir():
        return {}
    blocked = _blocked()
    found: dict[str, dict] = {}
    for directory in sorted(p for p in root.iterdir() if p.is_dir()):
        if directory.name in blocked:
            continue
        entry = directory / "SKILL.md"
        if not entry.is_file():
            continue
        try:
            head = entry.read_text(encoding="utf-8", errors="replace")[:1500]
        except OSError:
            continue
        block = _FRONTMATTER.search(head)
        described = _DESCRIPTION.search(block.group(1)) if block else None
        found[directory.name] = {
            "description": (described.group(1).strip('"\'') if described
                            else "")[:300],
            "path": f"{SKILL_DIR}/{directory.name}/SKILL.md",
        }
    return found


def health(handle) -> dict:
    probe = _skillpack.health(UPSTREAM, "README.md")
    if probe["state"] != fabric.READY:
        return probe
    offered, blocked = len(_entries()), len(_blocked())
    if not offered:
        return {"state": fabric.DEGRADED, "detail": "cloned, but no skills read"}
    return {"state": fabric.READY,
            "detail": f"{offered} skills offered, {blocked} licence-blocked"}


def call(operation: str, handle, **arguments):
    if operation == "catalogue":
        return _entries()

    if operation == "blocked":
        # Diagnostic, and the honest answer to "why can't you read the PDF one".
        return dict(_blocked())

    if operation == "search":
        query = (arguments.get("query") or "").strip().lower()
        if not query:
            raise fabric.FabricError("search needs a `query`")
        words = {w for w in re.findall(r"[a-z0-9]{3,}", query)}
        ranked = []
        for name, entry in _entries().items():
            haystack = f"{name} {entry['description']}".lower()
            score = sum(1 for w in words if w in haystack)
            if name.replace("-", " ") in query or query in name:
                score += 3
            if score:
                ranked.append({"skill": name, "score": score,
                               "description": entry["description"]})
        ranked.sort(key=lambda row: (-row["score"], row["skill"]))
        return ranked[:10]

    if operation == "skill":
        name = (arguments.get("name") or "").strip()
        blocked = _blocked()
        if name in blocked:
            raise fabric.FabricError(
                f"{name!r} may not be read: {blocked[name]}")
        entries = _entries()
        if name not in entries:
            raise fabric.FabricError(f"{name!r} is not an offered skill")
        return _skillpack.read(UPSTREAM, entries[name]["path"])

    raise fabric.FabricError(f"{UPSTREAM} has no operation {operation!r}")


DESCRIPTOR = fabric.Provider(
    id="science_skills",
    family="research",
    upstream=UPSTREAM,
    operations=("catalogue", "search", "skill", "blocked"),
    risk="low",
    license_mode=fabric.PERMISSIVE,
    integration_mode=fabric.SKILL,
    cost_class="free",
    model_required=False,
    commit="895b4be37ef0ca1cd55c6e628e7ff937ba5a1cf1",
    notes=(
        "MIT at the root, but four skills (docx, pdf, pptx, xlsx) carry "
        "Anthropic terms forbidding extraction, copying and derivative works. "
        "The exclusion is computed per skill from its own licence file rather "
        "than hardcoded, and fails closed, so a restricted skill added "
        "upstream later is excluded without anyone noticing it appeared. "
        "Markdown only; no upstream code is executed."
    ),
)
