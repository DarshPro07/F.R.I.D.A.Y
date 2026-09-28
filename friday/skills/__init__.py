"""
Skill Intelligence: FRIDAY's policy, lifecycle and evaluation layer over
Hermes's skill system.

Hermes owns the physical store (`HERMES_HOME/skills/<name>/SKILL.md`,
`external_dirs`, `skill_manage`, its security guard, usage counters,
curator and taps). Nothing here duplicates that. What Hermes cannot know is
added on top:

    candidates     WHEN a skill should exist  (friday.skills.candidates)
    lint           whether it is well-formed and safe to activate or share
    dependencies   WHICH skill a code change invalidates
    evaluator      WHETHER it still works, and how well
    lifecycle      versions, rollback, quarantine - verification is evidence
                   produced outside the skill, never a flag inside it
    router         which skill a task needs, loaded lazily, tokens counted
    publisher      WHETHER it may be shared, and where (tap layout)
    curator        incident noise out, procedure in; duplicates merged
    registry       the ledger all of the above write to

Three extension levels, never confused: a SKILL is instructions plus
existing tools; a CAPABILITY is approved code/API/CLI/MCP
(friday.capabilities, friday.fabric); a PLUGIN is an installed Python
extension (friday.forge). A SKILL.md can never turn itself into either.
"""
