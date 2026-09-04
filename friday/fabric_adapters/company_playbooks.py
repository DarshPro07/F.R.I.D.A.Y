"""
auto-company: fourteen executive playbooks and thirty-six business skills.

MIT (declared in package.json; the clone carries no LICENSE file, which the
lock records honestly). The upstream is a whole "AI company" that runs its
agents through Claude Code - a second control layer, which Friday does not
run (NON_NEGOTIABLE 1). What it *has* that Friday lacked is the content: a
CEO, CFO, CTO, operations, sales, marketing, QA and product brief each written
as a role, plus skills for pricing, unit economics, cold outreach, market
sizing, hiring-adjacent work (user research, persona creation) and premortems.

This is the pack behind "Friday as the owner's HR / operations assistant": a
request like "draft the onboarding plan for the new hire" pulls ONE playbook
(`operations-pg`) or ONE skill (`team`), never the company.

Same discipline as role_recipes: `catalogue` returns names, `playbook` and
`skill` return one file, and there is no bulk read.
"""

from __future__ import annotations

from friday import fabric
from friday.fabric_adapters import _skillpack

UPSTREAM = "auto-company"

AGENTS_DIR = ".claude/agents"
SKILLS_DIR = ".claude/skills"

#: The fourteen executives, by the upstream's own file stems. Named so a
#: caller can be told the roster without a directory walk.
EXECUTIVES = ("ceo-bezos", "cfo-campbell", "critic-munger", "cto-vogels",
              "devops-hightower", "fullstack-dhh", "interaction-cooper",
              "marketing-godin", "operations-pg", "product-norman",
              "qa-bach", "research-thompson", "sales-ross", "ui-duarte")


def _skill_entry(name: str) -> str:
    """A skill is a folder with SKILL.md, or a bare <name>.md (frontend-design)."""
    folder = f"{SKILLS_DIR}/{name}/SKILL.md"
    if (_skillpack.pack_root(UPSTREAM) / folder).is_file():
        return folder
    return f"{SKILLS_DIR}/{name}.md"


def _skill_names() -> list[str]:
    root = _skillpack.pack_root(UPSTREAM) / SKILLS_DIR
    if not root.is_dir():
        return []
    names = []
    for entry in sorted(root.iterdir()):
        if entry.is_dir() and (entry / "SKILL.md").is_file():
            names.append(entry.name)
        elif entry.suffix == ".md":
            names.append(entry.stem)
    return names


def health(handle) -> dict:
    return _skillpack.health(
        UPSTREAM, "PROMPT.md", f"{AGENTS_DIR}/operations-pg.md",
        f"{SKILLS_DIR}/team/SKILL.md")


def call(operation: str, handle, **arguments):
    if operation == "executives":
        return list(EXECUTIVES)
    if operation == "catalogue":
        return {"executives": list(EXECUTIVES), "skills": _skill_names()}
    if operation == "playbook":
        name = (arguments.get("name") or "").strip()
        if name not in EXECUTIVES:
            raise fabric.FabricError(
                f"unknown executive {name!r}; known: {list(EXECUTIVES)}")
        return _skillpack.read(UPSTREAM, f"{AGENTS_DIR}/{name}.md")
    if operation == "skill":
        name = (arguments.get("name") or "").strip()
        if name not in _skill_names():
            raise fabric.FabricError(
                f"unknown skill {name!r}; use catalogue for the list")
        return _skillpack.read(UPSTREAM, _skill_entry(name))
    if operation == "charter":
        # The company-wide operating prompt: how the executives hand work to
        # each other. One file, and the only one that describes the whole.
        return _skillpack.read(UPSTREAM, "PROMPT.md")
    raise fabric.FabricError(f"{UPSTREAM} has no operation {operation!r}")


DESCRIPTOR = fabric.Provider(
    id="company_playbooks",
    family="roles",
    upstream=UPSTREAM,
    operations=("executives", "catalogue", "playbook", "skill", "charter"),
    risk="low",
    license_mode=fabric.PERMISSIVE,
    integration_mode=fabric.SKILL,
    cost_class="free",
    model_required=False,
    commit="ebfab9b4bd5f0ab5ad452a1ff85285b3c141acdd",
    fallbacks=("role_recipes",),
    notes=("MIT via package.json, no LICENSE file at the pin. Fourteen "
           "executive playbooks and ~36 business skills read one at a time; "
           "the upstream's Claude Code agent loop is never run. Several "
           "playbooks are written in Chinese - a model reads them fine."),
)
