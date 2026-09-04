"""
agents-team (fadymondy, MIT): a Claude Code plugin that scaffolds a team of
agent files from templates via its own `scaffold.py` / `lint.py`. That
generator is Claude Code tooling for the owner (see
docs/plans/jarvis-agentic-team/03-architecture.md's "packs for the owner"
row) and is never run by Friday -- NON_NEGOTIABLE 1, one control layer.

What Friday reads is the content underneath it: eight role archetypes
(`{{placeholder}}` templates, read verbatim -- there is no render step),
thirteen team rules and four SKILL.md briefs. Same discipline as
company_playbooks: fixed, named tuples are the allowlist, one file is read
at a time, and there is no bulk read.
"""

from __future__ import annotations

from friday import fabric
from friday.fabric_adapters import _skillpack

UPSTREAM = "agents-team"

PLUGIN = "plugins/agents-team"
AGENTS_DIR = f"{PLUGIN}/templates/agents"
RULES_DIR = f"{PLUGIN}/templates/rules"
SKILLS_DIR = f"{PLUGIN}/skills"

#: The eight agent archetype templates, by file stem (minus `.md.template`).
ARCHETYPES = ("designer", "devops-engineer", "domain-engineer", "monitor",
              "orchestrator", "qa-engineer", "security-engineer",
              "tech-leader")

#: The thirteen team rules, by file stem.
RULES = ("01-plan-first", "02-service-boundaries", "03-definition-of-done",
          "04-clarify-unknowns", "05-interrupt-handling", "06-small-wins",
          "07-parallel-execution", "08-client-first-communication",
          "09-no-quick-fixes", "10-style-per-service",
          "11-no-push-without-permission", "12-security-vapt",
          "13-model-selection")

#: The four SKILL.md briefs, by their directory name.
SKILLS = ("evaluate-agent", "evaluate-agent-behavior", "meet", "team-gen")


def health(handle) -> dict:
    return _skillpack.health(
        UPSTREAM, "README.md",
        f"{AGENTS_DIR}/orchestrator.md.template",
        f"{RULES_DIR}/01-plan-first.md")


def call(operation: str, handle, **arguments):
    if operation == "archetypes":
        return list(ARCHETYPES)
    if operation == "archetype":
        name = (arguments.get("name") or "").strip()
        if name not in ARCHETYPES:
            raise fabric.FabricError(
                f"unknown archetype {name!r}; known: {list(ARCHETYPES)}")
        return _skillpack.read(UPSTREAM, f"{AGENTS_DIR}/{name}.md.template")
    if operation == "rules":
        return list(RULES)
    if operation == "rule":
        name = (arguments.get("name") or "").strip()
        if name not in RULES:
            raise fabric.FabricError(
                f"unknown rule {name!r}; known: {list(RULES)}")
        return _skillpack.read(UPSTREAM, f"{RULES_DIR}/{name}.md")
    if operation == "skill":
        name = (arguments.get("name") or "").strip()
        if name not in SKILLS:
            raise fabric.FabricError(
                f"unknown skill {name!r}; known: {list(SKILLS)}")
        return _skillpack.read(UPSTREAM, f"{SKILLS_DIR}/{name}/SKILL.md")
    raise fabric.FabricError(f"{UPSTREAM} has no operation {operation!r}")


DESCRIPTOR = fabric.Provider(
    id="agents_team_pack",
    family="roles",
    upstream=UPSTREAM,
    operations=("archetypes", "archetype", "rules", "rule", "skill"),
    risk="low",
    license_mode=fabric.PERMISSIVE,
    integration_mode=fabric.SKILL,
    open_operations=("archetypes", "archetype", "rules", "rule", "skill"),
    cost_class="free",
    model_required=False,
    commit="7f2f83927109dfac878dc78a53f27925f083aaeb",
    fallbacks=("role_recipes",),
    notes=("MIT (fadymondy/agents-team). Eight role-archetype templates, "
           "13 team rules and 4 SKILL.md briefs read one file at a time. "
           "The plugin's own scaffold.py/lint.py generator (the owner's "
           "Claude Code tooling) is never run by Friday; only the markdown "
           "content is reachable through this pack."),
)
