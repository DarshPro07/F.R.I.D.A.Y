"""
Choosing specialists, and refusing to choose too many.

The failure this guards is architecture cosplay: a hundred roles loaded into
every run, none of which fit, all of which cost context.
"""
import pytest

from friday import roles as R


# --- how big is this ------------------------------------------------------

def test_a_rename_is_trivial():
    assert R.size_of("rename the helper function", files=1) == R.TRIVIAL


def test_a_bug_fix_is_small():
    assert R.size_of("fix the crash when the list is empty") == R.SMALL


def test_a_feature_is_medium():
    assert R.size_of("implement the export feature") == R.MEDIUM


def test_architecture_is_large():
    assert R.size_of("redesign the memory subsystem") == R.LARGE


def test_a_measurement_beats_a_description():
    """"Just a small fix" across nineteen files is not a small fix."""
    assert R.size_of("just a small rename", files=19) == R.LARGE


def test_a_trivial_word_does_not_shrink_a_wide_change():
    assert R.size_of("rename it everywhere", files=6) != R.TRIVIAL


# --- team size ------------------------------------------------------------

def test_a_trivial_change_gets_one_person():
    """Spawning a committee to rename a variable is theatre."""
    team = R.compile_team("rename the helper function", files=1)
    assert len(team.roles) == 1


def test_a_large_change_gets_more_but_still_not_a_company():
    team = R.compile_team("redesign the database schema and migrate", files=12)
    assert 2 <= len(team.roles) <= 4


def test_no_run_ever_gets_the_whole_catalogue():
    team = R.compile_team(
        "redesign the security architecture, migrate the database, rewrite "
        "the voice latency path, add tests, fix the build pipeline and "
        "review the prompt instructions", files=30)
    assert len(team.roles) <= 4, "the cap is the point of the table"


# --- who gets chosen ------------------------------------------------------

def test_security_words_field_a_security_reviewer():
    team = R.compile_team("store the API token for authentication")
    assert "security" in {r.id for r in team.roles}


def test_voice_words_field_the_voice_engineer():
    team = R.compile_team("reduce the latency of the speech turn in livekit")
    assert "voice" in {r.id for r in team.roles}


def test_a_migration_fields_the_data_engineer():
    team = R.compile_team("add a sqlite migration for the new column")
    assert "data" in {r.id for r in team.roles}


def test_somebody_always_writes_the_code():
    """
    A run with only a Code Reviewer on it reviews nothing. That is the
    failure mode of choosing purely by keyword.
    """
    team = R.compile_team("review the correctness of this audit")
    assert any(not r.reviews for r in team.roles), \
        "the team is all reviewers and nobody to do the work"


def test_an_unrecognised_goal_still_gets_somebody():
    team = R.compile_team("do the thing with the stuff")
    assert team.roles
    assert not team.roles[0].reviews


def test_work_past_trivial_gets_reviewed_without_being_asked():
    """Nobody writes "and check it for bugs" at the end of a request."""
    team = R.compile_team("implement the export feature")
    assert any(r.reviews for r in team.roles)


def test_a_trivial_change_is_not_reviewed():
    team = R.compile_team("fix the typo in the docstring", files=1)
    assert not any(r.reviews for r in team.roles)


# --- the budget -----------------------------------------------------------

def test_the_context_budget_is_respected():
    team = R.compile_team(
        "redesign the security architecture with tests and tooling and "
        "prompt instructions and voice latency", files=20, budget=700)
    assert team.cost <= 700


def test_a_tiny_budget_still_returns_somebody():
    """A run with no roles at all has no instructions, which is worse."""
    team = R.compile_team("implement the export feature", budget=1)
    assert len(team.roles) >= 1


# --- the record -----------------------------------------------------------

def test_every_chosen_role_has_a_stated_reason():
    """"Why was a security reviewer on this?" has to have an answer."""
    team = R.compile_team("add authentication to the endpoint", files=3)
    for role in team.roles:
        assert team.because.get(role.id), f"{role.id} was chosen for no reason"


def test_the_team_serialises_for_the_run_record():
    team = R.compile_team("implement the export feature")
    as_dict = team.as_dict()
    assert as_dict["size"] == R.MEDIUM
    assert as_dict["roles"]
    assert as_dict["context_cost"] == team.cost


def test_instructions_are_scoped_text_not_a_runtime():
    """A role is scoped instructions applied to an executor. Nothing more."""
    team = R.compile_team("fix the crash")
    text = team.instructions()
    assert text
    assert all(role.title in text for role in team.roles)


# --- the catalogue itself -------------------------------------------------

def test_every_role_has_triggers_and_a_deliverable():
    for role in R.CATALOGUE:
        assert role.triggers, f"{role.id} can never be selected"
        assert role.delivers, f"{role.id} does not say what it produces"


def test_role_ids_are_unique():
    ids = [role.id for role in R.CATALOGUE]
    assert len(ids) == len(set(ids))


def test_the_catalogue_stays_small_enough_to_choose_from():
    """
    A list of eighty makes selection its own hard problem, which is the
    problem this module exists to avoid.
    """
    assert len(R.CATALOGUE) <= 20


def test_at_least_one_role_can_do_and_one_can_review():
    assert any(not r.reviews for r in R.CATALOGUE)
    assert any(r.reviews for r in R.CATALOGUE)


# --- which Claude subagent runs a role -------------------------------------

import re
from pathlib import Path

_AGENTS_DIR = Path(__file__).resolve().parent.parent / ".claude" / "agents"


def _frontmatter(path: Path) -> str:
    text = path.read_text(encoding="utf-8")
    match = re.match(r"^---\n(.*?)\n---\n", text, re.DOTALL)
    assert match, f"{path} has no YAML frontmatter"
    return match.group(1)


def test_role_to_agent_map_covers_every_role():
    for role in R.CATALOGUE:
        agent = R.claude_agent_for(role)
        assert agent in R.CLAUDE_AGENT_FOR_ROLE.values() or agent == R._DEFAULT_CLAUDE_AGENT
        assert (_AGENTS_DIR / f"{agent}.md").exists(), (
            f"role {role.id!r} maps to missing agent {agent!r}"
        )


def test_reviewer_agents_have_no_write_tools():
    """Dedicated review-only agents must not carry Write/Edit.

    Not every agent behind a `reviews=True` role qualifies: e.g.
    `friday-security-engineer` intentionally edits to apply fixes.
    """
    reviewer_agents = {"friday-final-reviewer", "friday-performance-reviewer",
                       "friday-codebase-researcher", "friday-tech-lead"}
    for name in reviewer_agents:
        frontmatter = _frontmatter(_AGENTS_DIR / f"{name}.md")
        tools_block = re.search(r"tools:\n((?:\s+-\s+\S+\n)+)", frontmatter)
        tools = tools_block.group(1) if tools_block else ""
        assert "Write" not in tools and "Edit" not in tools, (
            f"{name} is a reviewer but carries a write tool"
        )
