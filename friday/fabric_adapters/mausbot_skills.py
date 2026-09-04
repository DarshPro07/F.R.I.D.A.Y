"""
OpenMausBot's two skills, read on demand. The app itself is never run.

Apache-2.0 at the root (NOTICE records the relicense from MIT); `enterprise/`
carries an all-rights-reserved licence and this adapter cannot reach it - the
allowlist below is the only thing readable. OpenMausBot is an Electron chat
app hosting a roster of Claude/Codex agents, which is a second control layer
(NON_NEGOTIABLE 1). What survives the audit is its two skill files:

  phone-harness             how to drive an authorised-USB Android phone
                            safely (status, open_app, read_screen, tap_text;
                            never passwords, payments, OTPs). Friday's own
                            desktop toolset has the same three-layer gate,
                            and this is the mobile-side companion brief.
  create-verification-skill how to write one launch-and-verify recipe for a
                            project: ready signal, one health check, up to
                            three workflows with observable proof. This is
                            the shape Friday's self-build loop uses when it
                            adds a capability and must prove it works.
"""

from __future__ import annotations

from friday import fabric
from friday.fabric_adapters import _skillpack

UPSTREAM = "openmausbot"

SKILLS = {
    "phone-harness": "skills/phone-harness/SKILL.md",
    "create-verification-skill": "skills/create-verification-skill/SKILL.md",
}


def health(handle) -> dict:
    return _skillpack.health(UPSTREAM, *SKILLS.values())


def call(operation: str, handle, **arguments):
    if operation == "catalogue":
        return sorted(SKILLS)
    if operation == "skill":
        name = (arguments.get("name") or "").strip()
        if name not in SKILLS:
            raise fabric.FabricError(
                f"unknown skill {name!r}; known: {sorted(SKILLS)}")
        return _skillpack.read(UPSTREAM, SKILLS[name])
    raise fabric.FabricError(f"{UPSTREAM} has no operation {operation!r}")


DESCRIPTOR = fabric.Provider(
    id="mausbot_skills",
    family="orchestration",
    upstream=UPSTREAM,
    operations=("catalogue", "skill"),
    risk="low",
    license_mode=fabric.PERMISSIVE,
    integration_mode=fabric.SKILL,
    cost_class="free",
    model_required=False,
    commit="a3d2870528fbe185c978bb6ffda0decc8fd8a365",
    notes=("Apache-2.0 root; enterprise/ is proprietary and unreachable "
           "(allowlist of two files). The Electron app is never run."),
)
