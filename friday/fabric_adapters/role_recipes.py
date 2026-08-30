"""
agency-agents: 318 role recipes, and the discipline not to load them.

Marketing, engineering, finance, GIS, healthcare, sales - a folder of markdown
briefs describing how a specialist would approach a task. Useful exactly once
per task and ruinous in bulk: the pack is 318 files, and putting them in front
of a model that was asked to open Spotify is the token failure the fabric
exists to prevent.

So the operations are deliberately asymmetric. `catalogue` is cheap and returns
names only, which is all a router needs to choose. `recipe` reads one file. The
pack has no "load everything" operation and should never grow one.

NON_NEGOTIABLE 7's "never instantiate hundreds of agents" is the same rule
seen from the other end: a role recipe is text that shapes one turn, not a
process, not a worker, and not a second orchestrator.
"""

from __future__ import annotations

from friday import fabric
from friday.fabric_adapters import _skillpack

UPSTREAM = "agency-agents"

#: The division folders. Named so `catalogue` can be scoped without a
#: directory walk of the whole pack.
DIVISIONS = ("academic", "design", "engineering", "finance",
             "game-development", "gis", "healthcare", "marketing",
             "paid-media", "product", "project-management", "research",
             "sales")


def health(handle) -> dict:
    return _skillpack.health(UPSTREAM, "README.md")


def call(operation: str, handle, **arguments):
    if operation == "catalogue":
        division = (arguments.get("division") or "").strip()
        if division and division not in DIVISIONS:
            raise fabric.FabricError(
                f"unknown division {division!r}; known: {list(DIVISIONS)}")
        return _skillpack.catalogue(UPSTREAM, division)
    if operation == "divisions":
        return list(DIVISIONS)
    if operation == "recipe":
        path = (arguments.get("path") or "").strip()
        if not path:
            raise fabric.FabricError("recipe needs a path from catalogue")
        # The catalogue is the allowlist. Without this, `path` is an arbitrary
        # read of anything under third_party by whoever composes the call.
        if path not in _skillpack.catalogue(UPSTREAM):
            raise fabric.FabricError(f"{path!r} is not in this pack's catalogue")
        return _skillpack.read(UPSTREAM, path)
    raise fabric.FabricError(f"{UPSTREAM} has no operation {operation!r}")


DESCRIPTOR = fabric.Provider(
    id="role_recipes",
    family="roles",
    upstream=UPSTREAM,
    operations=("catalogue", "divisions", "recipe"),
    risk="low",
    license_mode=fabric.PERMISSIVE,
    integration_mode=fabric.SKILL,
    cost_class="free",
    model_required=False,
    commit="3c9588880b7cafaec325a104899fd8bbe27e7d72",
    notes=("318 markdown recipes. `catalogue` returns names, `recipe` returns "
           "one file, and there is deliberately no bulk read."),
)
