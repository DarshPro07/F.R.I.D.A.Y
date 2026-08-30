"""
Test F: the deterministic planner (friday/toolsets/objectives.py).

The planner is the no-LLM path from a spoken sentence to a compiled run:
clauses split on commas and "then" but NOT bare "and", each clause maps
through the pattern rules, an "X and Y" compound falls back to planning
each half, and anything unmappable becomes the objective.unmapped marker
task - persisted as FAILED/CAPABILITY_MISSING by compile, never silently
dropped. The planner must also never emit an objective_* capability as a
task, or a run could schedule itself.
"""
from __future__ import annotations
from friday import capabilities
from friday.toolsets import objectives as OT
UNMAPPED = OT.UNMAPPED_CAPABILITY


def manifest() -> list[dict]:
    """Synthetic manifest with intent_examples, for the scoring fallback."""
    return [
        {"id": "system_get_info", "intent_examples": (
            "how is the computer doing", "check the system",
            "is the machine healthy")},
        {"id": "system_battery", "intent_examples": (
            "how's my battery", "is my battery low")},
        {"id": "web_search", "intent_examples": (
            "search the web", "find me a story", "look that up")},
        {"id": "objective_start", "intent_examples": (
            "handle this whole thing for me",)},
    ]


def caps_of(plan: dict) -> list[str]:
    return [task["capability"] for task in plan["tasks"]]


def test_clauses_split_on_commas_and_then_not_bare_and():
    plan = OT.plan_objective(
        "check the system, then open paint and create a temporary note",
        manifest())
    # "open paint and create a temporary note" is ONE clause - bare "and"
    # never splits, so the compound fallback must have handled it.
    assert caps_of(plan) == ["system_get_info", "apps_open", "files_create"]


def test_independent_clauses_do_not_depend_on_each_other():
    """
    This asserted `[[], ["t1"], ["t2"]]` - the chain - until CORE-01.

    It was a faithful test of what the planner did and a wrong statement about
    what the sentence means. Checking the system is not a prerequisite for
    opening Paint; the speaker listed three errands and meant them in no
    particular order. Under the chain, Paint failing to launch also cancelled
    the story nobody had trouble finding.
    """
    plan = OT.plan_objective('check the system, open paint, find me a story', manifest())
    tasks = plan['tasks']
    assert [t['dependencies'] for t in tasks] == [[], [], []]


def test_steps_inside_one_clause_still_chain():
    """
    The other half, and why this is a rule rather than "drop dependencies".

    "Create and clean up a temporary note" is one errand in two steps, and the
    cleanup genuinely cannot run before the thing it cleans up exists.
    """
    plan = OT.plan_objective('create and clean up a temporary note', manifest())
    assert [t['dependencies'] for t in plan['tasks']] == [[], ['t1']]


def test_check_health_maps_to_system_get_info():
    plan = OT.plan_objective("check whether this computer looks healthy",
                             manifest())
    assert caps_of(plan) == ["system_get_info"]
    assert plan["tasks"][0]["arguments"] == {}


def test_open_app_maps_and_title_cases():
    plan = OT.plan_objective("open paint", manifest())
    assert caps_of(plan) == ["apps_open"]
    assert plan["tasks"][0]["arguments"] == {"name": "Paint"}


def test_open_app_rejects_bare_verb_remainder():
    # From a compound split, "open create" must not become an app named
    # "Create": the remainder is a verb, not an application.
    plan = OT.plan_objective("open create", manifest())
    assert "apps_open" not in caps_of(plan)


def test_search_maps_to_web_search():
    plan = OT.plan_objective("find me one current technology story",
                             manifest())
    assert caps_of(plan) == ["web_search"]
    assert plan["tasks"][0]["arguments"] == {
        "query": "me one current technology story"}


def test_notify_clause_is_a_note_not_a_task():
    plan = OT.plan_objective(
        "check the system, tell me when the whole job is finished",
        manifest())
    assert caps_of(plan) == ["system_get_info"]
    assert plan["notes"] == [{"kind": "notify",
                              "clause": "tell me when the whole job "
                                        "is finished"}]


def test_compound_note_create_plus_cleanup():
    plan = OT.plan_objective("create and clean up a temporary note",
                             manifest())
    assert caps_of(plan) == ["files_create", UNMAPPED]
    assert plan["tasks"][0]["arguments"] == {"content": "create a temporary "
                                                        "note"}
    assert plan["tasks"][1]["arguments"] == {"clause": "clean up a "
                                                       "temporary note"}


def test_cleanup_alone_is_honest_unmapped():
    plan = OT.plan_objective("clean up a temporary note", manifest())
    assert caps_of(plan) == [UNMAPPED]
    assert plan["tasks"][0]["reason"] == "cleanup is not a capability yet"


def test_fallback_scores_against_manifest_examples():
    plan = OT.plan_objective("is my battery low", manifest())
    assert caps_of(plan) == ["system_battery"]


def test_fallback_never_emits_an_objective_capability():
    plan = OT.plan_objective("handle this whole thing for me", manifest())
    assert caps_of(plan) == [UNMAPPED]


def test_gibberish_becomes_unmapped_marker():
    plan = OT.plan_objective("flurb the wibble", manifest())
    assert caps_of(plan) == [UNMAPPED]
    assert plan["unmapped"] == ["flurb the wibble"]


def test_compound_with_one_unmappable_half_is_one_marker():
    plan = OT.plan_objective("open paint and flurb the wibble", manifest())
    assert caps_of(plan) == [UNMAPPED]


def test_empty_objective_plans_nothing():
    plan = OT.plan_objective("", manifest())
    assert plan == {"tasks": [], "notes": [], "unmapped": []}
    plan = OT.plan_objective("  ,  then  ,  ", manifest())
    assert plan == {"tasks": [], "notes": [], "unmapped": []}


def test_demo_objective_plans_against_the_real_manifest():
    plan = OT.plan_objective('check whether this computer looks healthy, open Paint, find me one current technology story, create and clean up a temporary note, tell me when the whole job is finished', capabilities.as_dicts())
    assert caps_of(plan) == ['system_get_info', 'apps_open', 'web_search', 'files_create', UNMAPPED]
    assert plan['notes'] and plan['notes'][0]['kind'] == 'notify'
    assert [t['dependencies'] for t in plan['tasks']] == [[], [], [], [], ['t4']]


def test_planner_never_emits_objective_capability_with_real_manifest():
    plan = OT.plan_objective("handle this whole thing for me, "
                             "resume the objective, cancel that job",
                             capabilities.as_dicts())
    for task in plan["tasks"]:
        assert not task["capability"].startswith("objective_")
