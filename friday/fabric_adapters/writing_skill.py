"""
no-ai-slop: the pass that makes a finished draft read like a person wrote it.

One SKILL.md and one eval.md. No code, nothing installed, nothing started -
which is why `integration_mode` is SKILL and the licence question is
attribution rather than linkage.

It is a *finalisation* step by design and the router should treat it as one.
Running it over every sentence Friday speaks would spend a model call to
rewrite "opening Spotify"; running it over a report the boss is about to send
is where the value is.
"""

from __future__ import annotations

from friday import fabric
from friday.fabric_adapters import _skillpack

UPSTREAM = "no-ai-slop"
ENTRY = "skills/no-ai-slop/SKILL.md"
EVAL = "skills/no-ai-slop/eval.md"


def health(handle) -> dict:
    return _skillpack.health(UPSTREAM, ENTRY)


def call(operation: str, handle, **arguments):
    if operation == "guidance":
        return _skillpack.read(UPSTREAM, ENTRY)
    if operation == "checklist":
        return _skillpack.read(UPSTREAM, EVAL)
    if operation == "catalogue":
        return _skillpack.catalogue(UPSTREAM, "skills")
    raise fabric.FabricError(f"{UPSTREAM} has no operation {operation!r}")


DESCRIPTOR = fabric.Provider(
    id="no_ai_slop",
    family="writing",
    upstream=UPSTREAM,
    operations=("guidance", "checklist", "catalogue"),
    risk="low",
    license_mode=fabric.PERMISSIVE,
    integration_mode=fabric.SKILL,
    cost_class="free",
    model_required=False,
    commit="d30eddb9e04562234f2070b5ee63ca4649d9a05e",
    notes="Markdown only. Read on demand; never loaded at import.",
)
