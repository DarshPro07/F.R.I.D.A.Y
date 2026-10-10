"""
SKILL-005: skills are evaluated in a further tier after safety/validity.

    overlap   a candidate that near-duplicates an ACTIVE skill is flagged and cannot activate
    value     a with/without comparison on recorded task outcomes; activation needs measured lift

A safe skill with no measured value stays inactive, and the reason is on the record.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from friday.skills import contracts as K
from friday.skills import evaluator as EV
from friday.skills import files as F
from test_skills_golden import World, use_stand_in_scanner


@pytest.fixture
def world(tmp_path, monkeypatch):
    use_stand_in_scanner(tmp_path / "scanner", monkeypatch)
    return World(tmp_path)


def staged(world: World, name: str, **kwargs):
    skill = world.write_skill(name, measured=False, **kwargs)
    world.lifecycle.register(skill, tier=K.PROJECT_TIER, trust=K.TRUST_OWNER)
    world.lifecycle.stage(name)
    return skill


def trials(world: World, name: str, *, with_skill: str, without: str) -> None:
    """Record task outcomes: a letter per task, s = success, f = failure, c = corrected."""
    kind = {"s": "success", "f": "failure", "c": "corrected"}
    for letter in with_skill:
        world.registry.record_usage(name, kind[letter], objective="task with the skill")
    for letter in without:
        world.registry.record_baseline(name, kind[letter], objective="same task without the skill")


def history_text(world: World, name: str) -> str:
    return " | ".join(row["reason"] for row in world.registry.history(name))


def check(report, name: str) -> tuple[str, str]:
    found = [(state, detail) for n, state, detail in report.checks if n == name]
    assert found, f"no {name} check in {report.checks}"
    return found[0]


# --- value: no measurement, no activation --------------------------------------------

def test_a_skill_with_no_measured_value_stays_inactive_and_says_why(world):
    staged(world, "unmeasured")
    report, record = world.lifecycle.evaluate_and_activate("unmeasured")
    assert not report.passed
    assert record.state != K.ACTIVE and record.state not in K.SELECTABLE
    state, detail = check(report, "value")
    assert state == "FAIL" and "no measured value" in detail
    assert "no measured value" in history_text(world, "unmeasured")


def test_uses_without_a_baseline_are_not_a_measurement(world):
    staged(world, "one-armed")
    trials(world, "one-armed", with_skill="sss", without="")
    report, record = world.lifecycle.evaluate_and_activate("one-armed")
    assert not report.passed and record.state != K.ACTIVE
    assert "without" in check(report, "value")[1]


def test_too_few_trials_is_not_a_measurement(world):
    staged(world, "thin")
    trials(world, "thin", with_skill="ss", without="ff")
    report, record = world.lifecycle.evaluate_and_activate("thin")
    assert not report.passed and record.state != K.ACTIVE


@pytest.mark.parametrize("with_skill, without", [("sfsf", "sfsf"), ("fffs", "ssss"), ("scsc", "ssff")])
def test_no_lift_or_negative_lift_stays_inactive(world, with_skill, without):
    staged(world, "useless")
    trials(world, "useless", with_skill=with_skill, without=without)
    report, record = world.lifecycle.evaluate_and_activate("useless")
    assert not report.passed and record.state != K.ACTIVE
    assert "lift" in check(report, "value")[1]
    assert "lift" in history_text(world, "useless")


def test_a_measured_lift_activates_and_the_numbers_are_in_the_report(world):
    staged(world, "useful")
    trials(world, "useful", with_skill="sssf", without="fffs")
    report, record = world.lifecycle.evaluate_and_activate("useful")
    assert report.passed, report.checks
    assert record.state == K.ACTIVE
    state, detail = check(report, "value")
    assert state == "PASS" and "75%" in detail and "25%" in detail and "+50" in detail


def test_a_skill_that_was_already_active_is_not_regated_for_revalidation(world):
    skill = staged(world, "veteran")
    record = world.registry.get("veteran")
    record.state = K.NEEDS_REVALIDATION          # it earned ACTIVE before; a dependency changed
    report = world.evaluator.evaluate(skill, record, run_tests=False)
    state, detail = check(report, "value")
    assert state == "WARN" and "already" in detail


# --- overlap ---------------------------------------------------------------------------

DESCRIPTION = "Convert a markdown report into a styled pdf with a table of contents and page numbers"
TRIGGERS = ("make a pdf from markdown", "export the report as pdf")


def measured(world: World, name: str, *, description: str = DESCRIPTION, triggers=TRIGGERS):
    skill = world.write_skill(name, triggers=triggers, measured=False)
    frontmatter, body = skill.read()
    frontmatter["description"] = description
    skill.write(frontmatter, body)
    world.lifecycle.register(skill, tier=K.PROJECT_TIER, trust=K.TRUST_OWNER)
    world.lifecycle.stage(name)
    trials(world, name, with_skill="ssss", without="ffff")
    return skill


def test_a_near_duplicate_of_an_active_skill_is_flagged_and_cannot_activate(world):
    measured(world, "markdown-to-pdf")
    _, original = world.lifecycle.evaluate_and_activate("markdown-to-pdf")
    assert original.state == K.ACTIVE

    measured(world, "report-to-pdf")             # same description and triggers, a new name
    report, record = world.lifecycle.evaluate_and_activate("report-to-pdf")
    state, detail = check(report, "overlap")
    assert state == "FAIL" and "markdown-to-pdf" in detail
    assert not report.passed and record.state != K.ACTIVE
    assert "markdown-to-pdf" in history_text(world, "report-to-pdf")


def test_a_distinct_skill_is_not_flagged_as_overlap(world):
    measured(world, "markdown-to-pdf")
    world.lifecycle.evaluate_and_activate("markdown-to-pdf")
    measured(world, "rotate-the-logs", description="Rotate and compress the service log files weekly",
             triggers=("rotate the logs",))
    report, record = world.lifecycle.evaluate_and_activate("rotate-the-logs")
    assert check(report, "overlap")[0] == "PASS"
    assert report.passed and record.state == K.ACTIVE


def test_an_inactive_twin_and_the_skill_itself_are_not_in_the_comparison(world):
    measured(world, "markdown-to-pdf")               # never activated: not in the comparison pool
    measured(world, "report-to-pdf")
    report, record = world.lifecycle.evaluate_and_activate("report-to-pdf")
    assert report.passed and record.state == K.ACTIVE
    again = world.evaluator.evaluate(F.SkillFiles(Path(record.path)), record, run_tests=False)
    assert check(again, "overlap")[0] == "PASS"      # now ACTIVE itself, still not compared with itself


def test_similarity_is_a_jaccard_over_routing_metadata():
    a = K.SkillRecord(name="a", description="Convert markdown to pdf", triggers=["make a pdf"])
    same = K.SkillRecord(name="b", description="Convert markdown to pdf", triggers=["make a pdf"])
    other = K.SkillRecord(name="c", description="Rotate the service logs", triggers=["rotate logs"])
    assert EV.similarity(a, same) == 1.0
    assert EV.similarity(a, other) < EV.OVERLAP_THRESHOLD


def test_the_cli_records_both_arms_of_the_comparison(tmp_path):
    from friday.skills import cli
    from friday.skills.registry import SkillRegistry

    db = str(tmp_path / "registry.sqlite3")
    SkillRegistry(db).upsert(K.SkillRecord(name="notes"))
    for _ in range(3):
        assert cli.main(["--db", db, "outcome", "notes", "success", "--objective", "with"]) == 0
        assert cli.main(["--db", db, "outcome", "notes", "failure", "--without", "--objective", "without"]) == 0
    registry = SkillRegistry(db)
    assert [row["outcome"] for row in registry.usage("notes")] == ["success"] * 3
    assert [row["outcome"] for row in registry.baseline("notes")] == ["failure"] * 3
