"""
open-design: a library of design skills, design systems and templates.

This is what the operator asked for as "Claude Design in Friday": generate
mock-ups, slide decks, presentations and branded UI. open-design ships the
*method* for all of it - 162 skills (Apple HIG, brand extraction, brutalist,
card layouts, ad creative, ...), 153 named design systems (apple, airbnb, ant,
binance, ...) and 114 templates including html slide decks - each as markdown
a model follows.

## What Friday takes, and what it does not

open-design is itself a product: a pnpm monorepo that detects a code-agent CLI,
runs these skills, and streams artifacts into its own sandboxed preview. Friday
does not run that product - that would be a second agent-driving control layer,
the OpenHands problem. Friday takes the *content* (Apache-2.0 skills, systems,
templates) as a SKILL provider, and generates the actual artifact with what it
already has: `workbench_write`/`workbench_preview` under its own model, or a
`hermes_delegate` for a larger build. Read the method here, build it there.

## The per-subtree licence gate

The repository is Apache-2.0, and 35 of the templates carry their own LICENSE
(the sampled ones are MIT with author attribution). A repository licence does
not cover a subtree, so `template` checks the template's own licence and, like
science_skills, fails closed: a template whose licence is not recognised as
permissive is withheld rather than read. Skills and systems inherit the
Apache-2.0 root.

## Laziness

429 content items is far past a prompt. `catalogue` returns names and one-line
descriptions, `route` narrows skills by their own `triggers:` frontmatter, and
`skill`/`system`/`template` each read one file. No bulk read; the catalogue is
the read allowlist, so none of these can reach a path outside the pack.
"""

from __future__ import annotations

import functools
import re

from friday import fabric
from friday.fabric_adapters import _skillpack

UPSTREAM = "open-design"

#: content kind -> directory.
DIRS = {"skill": "skills", "system": "design-systems", "template": "design-templates"}

#: The file to read for each kind.
ENTRY = {"skill": "SKILL.md", "system": "DESIGN.md", "template": "SKILL.md"}

#: Licence text that permits reading the material into a prompt.
PERMISSIVE = (
    r"Permission is hereby granted, free of charge",     # MIT
    r"Apache License",
    r"BSD \d-Clause",
    r"CC0 1\.0 Universal",
    r"Mozilla Public License",
)
RESTRICTED = (
    ("non-commercial", r"NonCommercial|CC[- ]BY[- ]NC"),
    ("Anthropic terms", r"ADDITIONAL RESTRICTIONS|Anthropic, PBC\. All rights reserved"),
    ("all rights reserved", r"All rights reserved(?![\s\S]{0,400}Permission is hereby)"),
)
LICENCE_NAMES = ("LICENSE", "LICENSE.txt", "LICENSE.md", "LICENCE", "COPYING")

_DESCRIPTION = re.compile(r"^description:\s*(.+?)\s*$", re.M)
_FRONTMATTER = re.compile(r"\A---\r?\n(.*?)\r?\n---", re.S)
_TRIGGERS = re.compile(r"^triggers:\s*$(.*?)(?=^\S|\Z)", re.M | re.S)
_ITEM = re.compile(r'^\s*-\s*"?([^"\n]+?)"?\s*$', re.M)


def _classify(text: str) -> tuple[bool, str]:
    for reason, pattern in RESTRICTED:
        if re.search(pattern, text, re.IGNORECASE):
            return False, reason
    for pattern in PERMISSIVE:
        if re.search(pattern, text, re.IGNORECASE):
            return True, ""
    return False, "licence present but not recognised as permissive"


def _template_ok(name: str) -> tuple[bool, str]:
    """A template may carry its own licence; skills/systems inherit Apache root."""
    root = _skillpack.pack_root(UPSTREAM) / DIRS["template"] / name
    for licence in LICENCE_NAMES:
        path = root / licence
        if path.is_file():
            try:
                return _classify(path.read_text(encoding="utf-8", errors="replace")[:6000])
            except OSError:
                return False, "licence unreadable"
    return True, ""  # no own licence -> Apache-2.0 root


@functools.lru_cache(maxsize=8)
def _entries(kind: str) -> dict[str, dict]:
    """name -> {description, path} for one content kind. Frontmatter only."""
    root = _skillpack.pack_root(UPSTREAM) / DIRS[kind]
    if not root.is_dir():
        return {}
    entry_name = ENTRY[kind]
    found: dict[str, dict] = {}
    for directory in sorted(p for p in root.iterdir() if p.is_dir()):
        entry = directory / entry_name
        if not entry.is_file():
            continue
        try:
            head = entry.read_text(encoding="utf-8", errors="replace")[:2000]
        except OSError:
            continue
        block = _FRONTMATTER.search(head)
        described = _DESCRIPTION.search(block.group(1)) if block else None
        found[directory.name] = {
            "description": (described.group(1).strip('"\'') if described
                            else "")[:300].replace("\n", " "),
            "path": f"{DIRS[kind]}/{directory.name}/{entry_name}",
        }
    return found


def _triggers(name: str) -> list[str]:
    root = _skillpack.pack_root(UPSTREAM) / DIRS["skill"] / name / "SKILL.md"
    if not root.is_file():
        return []
    block = _FRONTMATTER.search(root.read_text(encoding="utf-8", errors="replace")[:2000])
    if not block:
        return []
    trig = _TRIGGERS.search(block.group(1))
    return _ITEM.findall(trig.group(1)) if trig else []


def health(handle) -> dict:
    probe = _skillpack.health(UPSTREAM, "README.md")
    if probe["state"] != fabric.READY:
        return probe
    counts = {kind: len(_entries(kind)) for kind in DIRS}
    if not any(counts.values()):
        return {"state": fabric.DEGRADED, "detail": "cloned, but no content read"}
    return {"state": fabric.READY,
            "detail": (f"{counts['skill']} skills, {counts['system']} systems, "
                       f"{counts['template']} templates")}


def call(operation: str, handle, **arguments):
    if operation == "catalogue":
        kind = (arguments.get("kind") or "").strip()
        if kind:
            if kind not in DIRS:
                raise fabric.FabricError(f"kind must be one of {list(DIRS)}")
            return _entries(kind)
        return {k: sorted(_entries(k)) for k in DIRS}

    if operation == "route":
        task = (arguments.get("task") or "").strip().lower()
        if not task:
            raise fabric.FabricError("route needs a `task`")
        words = {w for w in re.findall(r"[a-z0-9]{3,}", task)}
        ranked = []
        for name, entry in _entries("skill").items():
            score = sum(3 for t in _triggers(name) if t.lower() in task)
            hay = f"{name} {entry['description']}".lower()
            score += sum(1 for w in words if w in hay)
            if name.replace("-", " ") in task:
                score += 3
            if score:
                ranked.append({"skill": name, "score": score,
                               "description": entry["description"]})
        ranked.sort(key=lambda r: (-r["score"], r["skill"]))
        return ranked[:10]

    if operation in ("skill", "system", "template"):
        name = (arguments.get("name") or "").strip()
        entries = _entries(operation)
        if name not in entries:
            raise fabric.FabricError(
                f"{name!r} is not an open-design {operation}")
        if operation == "template":
            ok, why = _template_ok(name)
            if not ok:
                raise fabric.FabricError(
                    f"template {name!r} may not be read: {why}")
        return _skillpack.read(UPSTREAM, entries[name]["path"])

    raise fabric.FabricError(f"{UPSTREAM} has no operation {operation!r}")


DESCRIPTOR = fabric.Provider(
    id="open_design",
    family="presentation",
    upstream=UPSTREAM,
    operations=("catalogue", "route", "skill", "system", "template"),
    risk="low",
    license_mode=fabric.PERMISSIVE,
    integration_mode=fabric.SKILL,
    fallbacks=("diagram_design",),
    cost_class="free",
    model_required=False,
    commit="df84ae5b9ebfb4d3cee43ed3037667503bcafe36",
    version="0.21.1",
    notes=(
        "Apache-2.0. 162 design skills, 153 design systems, 114 templates "
        "(mock-ups, slide decks, branded UI). SKILL mode - Friday reads the "
        "method and generates the artifact with workbench_write (its own model) "
        "or hermes_delegate; open-design's own agent-runner product is not run. "
        "Templates may carry their own licence (35 do); `template` gates each "
        "and fails closed. No upstream code executes."
    ),
)
