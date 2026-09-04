"""
awesome-harness-engineering: the templates Friday builds herself against.

CC0-1.0 (public domain). The upstream is a reading list plus four templates -
AGENTS.md, PLAN.md, IMPLEMENT.md and HARNESS_CHECKLIST.md - and the lock
first demoted it to REFERENCE_ONLY on the grounds that "a reading list is
not software". That was true and beside the point: the checklist is the
one artefact the self-building loop needs. When Friday asks Hermes to add a
capability to her own tree, the acceptance criteria come from
HARNESS_CHECKLIST.md, and the plan Hermes writes is shaped by PLAN.md. A
template read on demand is a SKILL in every sense the fabric uses the word.

`checklist` is the operation the completion gate calls; `template` reads
one of the other three by name. There is no operation that reads the reading
list itself: 400 links is context nobody asked for.
"""

from __future__ import annotations

from friday import fabric
from friday.fabric_adapters import _skillpack

UPSTREAM = "awesome-harness-engineering"

TEMPLATES = {
    "agents": "templates/AGENTS.md",
    "plan": "templates/PLAN.md",
    "implement": "templates/IMPLEMENT.md",
    "checklist": "templates/HARNESS_CHECKLIST.md",
}


def _items(text: str) -> list[str]:
    """The checkbox lines of a checklist, as bare requirements."""
    out = []
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("- [ ]") or stripped.startswith("- [x]"):
            out.append(stripped[5:].strip())
    return out


def health(handle) -> dict:
    return _skillpack.health(UPSTREAM, *TEMPLATES.values())


def call(operation: str, handle, **arguments):
    if operation == "templates":
        return sorted(TEMPLATES)
    if operation == "template":
        name = (arguments.get("name") or "").strip().lower()
        if name not in TEMPLATES:
            raise fabric.FabricError(
                f"unknown template {name!r}; known: {sorted(TEMPLATES)}")
        return _skillpack.read(UPSTREAM, TEMPLATES[name])
    if operation == "checklist":
        # Structured, so a completion gate can iterate it rather than
        # re-parse markdown; the raw text is one `template` call away.
        text = _skillpack.read(UPSTREAM, TEMPLATES["checklist"])
        return {"items": _items(text), "count": len(_items(text))}
    raise fabric.FabricError(f"{UPSTREAM} has no operation {operation!r}")


DESCRIPTOR = fabric.Provider(
    id="harness_templates",
    family="orchestration",
    upstream=UPSTREAM,
    operations=("templates", "template", "checklist"),
    risk="low",
    license_mode=fabric.PERMISSIVE,
    integration_mode=fabric.SKILL,
    cost_class="free",
    model_required=False,
    commit="6a146704c1672367d88684350a55b0eaf744ab7c",
    notes=("CC0-1.0. Four templates, read one at a time; the reading list "
           "is deliberately not an operation. `checklist` is what the "
           "self-build completion gate consults."),
)
