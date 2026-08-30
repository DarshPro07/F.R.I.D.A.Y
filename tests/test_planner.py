"""
Understanding the request before choosing a tool.

The six cases below are the ones the clause-splitting planner got wrong, and
each is taken from something that actually happened rather than invented to
be easy. Measured against the old planner first, so "red" is a fact:

    "Friday, check my computer"
        -> objective.unmapped + system_get_info
           the name became a task, because fragments became tasks

    "Check my computer and open Paint. If one part fails, keep going."
        -> system_get_info + memory_remember
           "open Paint" vanished entirely - the splitter swallowed it - and
           "keep going" became a memory write. A lost goal is worse than an
           extra one: nothing reports it.

    "Create a note, read it, then recycle it."
        -> files_create, documents_extract, unmapped, unmapped
           one lifecycle, four guesses, no dependencies

    a 1967-word request
        -> 205 tasks, 68 of which failed

The last is the shape of all of them. Length became work, because prose was
read as a list of instructions when most of a spoken request is not work at
all: it is address, constraint, reporting and how-to-behave.
"""
from __future__ import annotations
import pytest
from friday import planner as P
from friday import semantics as S


def test_a_vocative_is_not_work():
    plan = P.plan_objective("Friday, check my computer.")

    assert "Friday" in plan.discarded
    assert len(plan.goals) == 1, [g.intent for g in plan.goals]
    assert not any("friday" in goal.intent.lower() for goal in plan.goals), \
        "the boss's name for Friday became something to do"


def test_the_goal_beside_the_vocative_survives():
    plan = P.plan_objective("Friday, check my computer.")
    assert plan.goals[0].capability == "system_resource_usage"


def test_a_technology_story_is_a_web_search():
    """
    In the live audit this resolved to `product_status` - a catalogue tool,
    because "status" and "story" share letters and nothing checked what kind
    of thing was wanted.
    """
    plan = P.plan_objective("Find one current technology story.")

    goal = plan.goals[0]
    assert goal.target == "WEB", f"{goal.intent!r} -> {goal.target}"
    assert goal.capability.startswith("web_"), goal.capability
    assert goal.capability != "product_status"


def test_the_candidate_set_is_small():
    """
    The resolver asks the registry by shape and gets a handful. This is the
    thing that keeps a planner from needing 125 schemas in a prompt.
    """
    found = P.candidates("SEARCH", "WEB")
    assert found, "no candidates at all"
    assert len(found) <= 8, f"{len(found)} candidates is a prompt, not a choice"
    assert "product_status" not in found


def test_do_not_ask_me_to_continue_is_a_constraint():
    plan = P.plan_objective(
        "Check my computer, open Paint. Do not ask me to continue.")

    assert any("continue" in c.lower() for c in plan.constraints), \
        f"constraints={plan.constraints}"
    assert not any("continue" in goal.intent.lower() for goal in plan.goals), \
        "an instruction about how to work became work"


def test_the_goals_beside_the_constraint_survive():
    """
    The constraint used to be absorbed into a neighbouring clause and take a
    real goal with it. Both goals have to be here.
    """
    plan = P.plan_objective(
        "Check my computer, open Paint. Do not ask me to continue.")

    chosen = {goal.capability for goal in plan.goals}
    assert "apps_open" in chosen, f"open Paint was lost: {chosen}"
    assert "system_resource_usage" in chosen, chosen


def test_a_file_lifecycle_is_one_ordered_branch():
    plan = P.plan_objective("Create a note, read it, then recycle it.")

    chosen = [goal.capability for goal in plan.goals]
    assert chosen == ["files_create", "files_read", "files_recycle"], chosen


def test_the_lifecycle_carries_its_dependencies():
    """
    "read it" means the thing just created. Without that the three run in any
    order, and reading a file that does not exist yet is a failure the plan
    invented.
    """
    plan = P.plan_objective("Create a note, read it, then recycle it.")

    create, read, recycle = plan.goals
    assert read.depends_on == (create.goal_id,), read.depends_on
    assert recycle.depends_on == (read.goal_id,), recycle.depends_on


def test_a_failure_policy_is_a_constraint():
    plan = P.plan_objective(
        "Check my computer and open Paint. "
        "If one independent part fails, keep going.")

    assert any("keep going" in c.lower() for c in plan.constraints), \
        f"constraints={plan.constraints}"
    assert not any("keep going" in goal.intent.lower() for goal in plan.goals)
    assert not any(goal.capability == "memory_remember"
                   for goal in plan.goals), \
        "'keep going' became a memory write again"


def test_and_open_paint_is_still_its_own_goal():
    """
    The lost-goal case. "Check my computer and open Paint" was one segment,
    so Paint was never planned and nothing said so.
    """
    plan = P.plan_objective(
        "Check my computer and open Paint. "
        "If one independent part fails, keep going.")

    assert "apps_open" in {goal.capability for goal in plan.goals}, \
        f"open Paint disappeared: {[g.intent for g in plan.goals]}"
LONG_REQUEST = 'Friday, perform a complete real capability audit of yourself from start to finish. Treat this as one durable audit objective and do not wait for me to say continue. First, read your live capability registry and build the audit from what is actually registered right now. Check my computer and open Paint. Do not shut down my machine. Tell me at the end how it went. Do not ask me whether to continue.'


def test_a_long_request_does_not_become_a_long_plan():
    """
    The 1967-word request produced 205 tasks. Most of a spoken request is not
    work, and a planner that treats length as work will always produce this.
    """
    plan = P.plan_objective(LONG_REQUEST)

    assert len(plan.goals) < 12, (
        f"{len(plan.goals)} goals from {len(LONG_REQUEST.split())} words: "
        f"{[g.intent for g in plan.goals]}")
    assert plan.constraints, "a request full of constraints extracted none"


def test_the_parts_that_are_not_work_are_kept_apart():
    plan = P.plan_objective(LONG_REQUEST)

    assert plan.safety, "'do not shut down my machine' was not held as safety"
    assert plan.reporting, "'tell me at the end' was not held as reporting"
    assert "Friday" in plan.discarded

    for bucket, name in ((plan.constraints, "constraint"),
                         (plan.safety, "safety"),
                         (plan.reporting, "reporting")):
        for item in bucket:
            assert not any(goal.intent == item for goal in plan.goals), \
                f"a {name} also became a goal: {item!r}"


def test_an_unplaceable_phrase_is_reported_not_guessed():
    """
    §26. "keep going" became `memory_remember` because the old planner had to
    return something. Saying nothing fits is an answer.
    """
    plan = P.plan_objective('Check my computer. Blorp the quux thoroughly.')
    assert any(('blorp' in item.lower() for item in plan.unresolved)), f"unresolved={plan.unresolved}"
    unplaceable = [goal for goal in plan.goals if not goal.capability]
    assert len(unplaceable) == 1, [goal.intent for goal in plan.goals]
    assert 'blorp' in unplaceable[0].intent.lower()
    assert unplaceable[0].why, 'an unplaceable goal did not say why'
    assert any((goal.capability == 'system_resource_usage' for goal in plan.goals))


def test_validation_catches_a_capability_that_does_not_exist():
    plan = P.plan_objective("Check my computer.")
    plan.goals[0].capability = "not_a_real_capability"

    complaints = P.validate(plan)
    assert any("not a capability" in c for c in complaints), complaints


def test_validation_catches_a_wrong_domain_choice():
    """The `product_status` shape, as a gate on the validator itself."""
    plan = P.plan_objective("Find one current technology story.")
    plan.goals[0].capability = "product_status"

    complaints = P.validate(plan)
    assert any("acts on" in c for c in complaints), complaints


def test_validation_catches_a_dangling_dependency():
    plan = P.plan_objective("Check my computer.")
    plan.goals[0].depends_on = ("g99",)

    complaints = P.validate(plan)
    assert any("g99" in c for c in complaints), complaints


def test_a_good_plan_has_nothing_to_complain_about():
    plan = P.plan_objective("Create a note, read it, then recycle it.")
    assert P.validate(plan) == []


def test_the_vocative_gate_would_catch_a_regression(monkeypatch):
    monkeypatch.setattr(P, "_VOCATIVES", frozenset())
    plan = P.plan_objective("Friday, check my computer.")
    assert "Friday" not in plan.discarded, \
        "the mutation did not take, so the gate proves nothing"


def test_the_constraint_gate_would_catch_a_regression(monkeypatch):
    import re

    monkeypatch.setattr(P, "_CONSTRAINT", re.compile(r"(?!x)x"))
    plan = P.plan_objective(
        "Check my computer. Do not ask me to continue.")
    assert not plan.constraints, "the mutation did not take"


def test_a_request_whose_target_is_unread_is_not_confined_to_the_system():
    """
    `candidates` filters on the target, so inventing one does not guess - it
    makes the right capability structurally unreachable. Measured:

        "search the web for this"  ->  system_battery  confidence 0.8

    `web_search` was never a candidate. The comment at the top of planner.py
    already recorded one instance of this and patched it with a regex rather
    than at the cause.
    """
    for text in ('search the web for this', 'look this up online'):
        plan = P.resolve(P.interpret(text))
        chosen = [goal.capability for goal in plan.goals]
        assert chosen == ['web_search'], (text, chosen)


def test_the_bare_word_web_names_a_target():
    """Only "page", "site", "url" and "news" did, which is not how anybody
    asks to search the web."""
    assert S.target_for_request('search the web for cats') == 'WEB'
    assert S.target_for_request('look it up on the internet') == 'WEB'


def test_the_bare_word_system_names_a_target():
    """"computer", "machine", "pc" and "laptop" all did; "system" did not."""
    assert S.target_for_request('check the system') == 'SYSTEM'


def test_being_the_only_near_thing_is_not_being_the_right_thing():
    """
    MOVE's neighbourhood contains CONTROL, and the one CONTROL capability on
    BROWSER is `browser_close`. It resolved with 0.6 confidence and the reason
    "the only capability of that shape", which would have closed the session
    for a request to make the window smaller.

    The neighbourhood exists so a near match can rank, not so it can win by
    default.
    """
    plan = P.resolve(P.interpret('minimise the browser'))
    goal = plan.goals[0]
    assert goal.capability == '', goal.capability
    assert 'nearest thing' in goal.why


def test_an_exact_match_that_is_the_only_one_still_wins():
    """The rule must not close the door it is standing beside."""
    plan = P.resolve(P.interpret('quit the browser'))
    assert plan.goals[0].capability == 'browser_close'
