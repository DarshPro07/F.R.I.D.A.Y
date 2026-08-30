"""
diagram-design: turn a description into a diagram, in one editorial style.

Thirty-nine diagram types - architecture, sequence, ER, flowchart, Gantt,
Wardley map, swimlane - authored as self-contained HTML/SVG, plus importers
for Mermaid and draw.io sources. Friday can already write a file and preview
it in the browser (`workbench_write`, `workbench_preview`); what it does not
have is the design system that makes the output look like one deliberate hand
drew it. That is what this skill supplies, and why it is `presentation`
alongside `adhd_mode` rather than a new capability.

## One skill, references on demand

Unlike the role packs, this is a single skill with a `references/` directory
it pulls from only when a diagram type is chosen. The adapter mirrors that:
`instructions` returns the main SKILL.md, `reference` returns one file from
`references/` by name, and the catalogue of reference names is what a caller
consults to decide which to load. The point, as everywhere in this family, is
that thirty-nine type references never enter a prompt at once.

`integration_mode=SKILL`: the markdown is instructions Friday follows to write
a diagram with its own file tools. No upstream code runs, and there is no
diagram binary to install.
"""

from __future__ import annotations

from friday import fabric
from friday.fabric_adapters import _skillpack

UPSTREAM = "diagram-design"
BASE = "skills/diagram-design"
ENTRY = f"{BASE}/SKILL.md"
REFERENCE_DIR = f"{BASE}/references"


def _reference_catalogue() -> list[str]:
    """Reference file names, relative to the references directory."""
    root = _skillpack.pack_root(UPSTREAM) / "skills" / "diagram-design" / "references"
    if not root.is_dir():
        return []
    return sorted(p.name for p in root.rglob("*.md")
                  if ".git" not in p.parts)


def health(handle) -> dict:
    return _skillpack.health(UPSTREAM, ENTRY)


def call(operation: str, handle, **arguments):
    if operation == "instructions":
        return _skillpack.read(UPSTREAM, ENTRY)

    if operation == "references":
        return _reference_catalogue()

    if operation == "reference":
        name = (arguments.get("name") or "").strip()
        if not name:
            raise fabric.FabricError("reference needs a `name` from `references`")
        # The catalogue is the allowlist: without this, `name` is an arbitrary
        # read of anything under third_party by whoever composes the call.
        if name not in _reference_catalogue():
            raise fabric.FabricError(f"{name!r} is not a diagram-design reference")
        return _skillpack.read(UPSTREAM, f"{REFERENCE_DIR}/{name}")

    raise fabric.FabricError(f"{UPSTREAM} has no operation {operation!r}")


DESCRIPTOR = fabric.Provider(
    id="diagram_design",
    family="presentation",
    upstream=UPSTREAM,
    operations=("instructions", "references", "reference"),
    risk="low",
    license_mode=fabric.PERMISSIVE,
    integration_mode=fabric.SKILL,
    fallbacks=("adhd_mode",),
    cost_class="free",
    model_required=False,
    commit="ac490fd1ac4b4014100f93e729cb4ad198700bd4",
    version="2.6",
    notes=(
        "MIT. One skill, 39 diagram types, with per-type detail in references/ "
        "loaded only when selected. Markdown instructions Friday follows using "
        "its own workbench_write/workbench_preview - no upstream code runs and "
        "no diagram binary is installed."
    ),
)
