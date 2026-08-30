"""
i-have-adhd: put the action first and the reasoning after it.

An optional output mode, not a default. Friday already has a voice; this
changes the shape of an answer for someone who needs the decision before the
paragraph, and it is only worth a model's attention when the boss has asked
for that shape.

Markdown only - nothing installed, nothing started.
"""

from __future__ import annotations

from friday import fabric
from friday.fabric_adapters import _skillpack

UPSTREAM = "i-have-adhd"
ENTRY = "skills/i-have-adhd/SKILL.md"


def health(handle) -> dict:
    return _skillpack.health(UPSTREAM, ENTRY)


def call(operation: str, handle, **arguments):
    if operation == "guidance":
        return _skillpack.read(UPSTREAM, ENTRY)
    if operation == "catalogue":
        return _skillpack.catalogue(UPSTREAM, "skills")
    raise fabric.FabricError(f"{UPSTREAM} has no operation {operation!r}")


DESCRIPTOR = fabric.Provider(
    id="adhd_mode",
    family="presentation",
    upstream=UPSTREAM,
    operations=("guidance", "catalogue"),
    risk="low",
    license_mode=fabric.PERMISSIVE,
    integration_mode=fabric.SKILL,
    cost_class="free",
    model_required=False,
    commit="cbe69fb83c08a37cf54d5ec9ec6bb88c8bc9973c",
    notes="Opt-in output mode. Markdown only, read on demand.",
)
