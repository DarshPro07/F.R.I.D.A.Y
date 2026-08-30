"""
prompt-master: turn a rough idea into a production-ready prompt for a named tool.

Friday writes prose (no_ai_slop keeps that honest) but has nothing that treats
a *prompt* as the artifact - identify the target tool, extract the real intent,
and hand back one paste-ready prompt with no wasted tokens. That is what this
skill supplies, which is why it sits in the `writing` family beside no_ai_slop
rather than duplicating it: one governs how Friday writes, the other builds a
prompt for a different tool to consume.

## One skill, references on demand

Like diagram_design, this is a single SKILL.md with a `references/` directory
(patterns, templates) pulled only when a technique is actually chosen. The
adapter mirrors that: `instructions` returns the skill body, `references` lists
the reference files, `reference` returns one by name. The reference catalogue
is the read allowlist, so `reference` cannot read a path outside the pack.

`integration_mode=SKILL`: the markdown is guidance Friday follows; no upstream
code runs and there is nothing to install. Unlike the other skill packs its
SKILL.md is at the repo root, not under `skills/<name>/`.
"""

from __future__ import annotations

from friday import fabric
from friday.fabric_adapters import _skillpack

UPSTREAM = "prompt-master"
ENTRY = "SKILL.md"
REFERENCE_DIR = "references"


def _reference_catalogue() -> list[str]:
    root = _skillpack.pack_root(UPSTREAM) / REFERENCE_DIR
    if not root.is_dir():
        return []
    return sorted(p.name for p in root.rglob("*.md") if ".git" not in p.parts)


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
            raise fabric.FabricError(f"{name!r} is not a prompt-master reference")
        return _skillpack.read(UPSTREAM, f"{REFERENCE_DIR}/{name}")

    raise fabric.FabricError(f"{UPSTREAM} has no operation {operation!r}")


DESCRIPTOR = fabric.Provider(
    id="prompt_master",
    family="writing",
    upstream=UPSTREAM,
    operations=("instructions", "references", "reference"),
    risk="low",
    license_mode=fabric.PERMISSIVE,
    integration_mode=fabric.SKILL,
    fallbacks=("no_ai_slop",),
    cost_class="free",
    model_required=False,
    commit="2bd92518e26bf659e21e3d9ab90573fcf3ddeccb",
    version="1.8.0",
    notes=(
        "MIT. One skill (SKILL.md at repo root) plus references/ (patterns, "
        "templates) loaded on demand. Generates paste-ready prompts for a "
        "named AI tool; activates only on an explicit prompt-engineering "
        "request. Markdown only - no upstream code runs. Also installed as a "
        "native Claude Code skill under ~/.claude/skills/prompt-master/."
    ),
)
