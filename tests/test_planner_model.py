"""
Asking a model what a request means, without letting it choose the tools.

Three constraints, and each has a gate here because each fails silently:

    it never sees the capabilities   a planner prompt carrying 125 schemas is
                                     the token and accuracy problem CORE-02B
                                     removed, rebuilt inside the planner
    it never names a capability      a model that can name tools invents them,
                                     and `magic_super_tool` cannot fail well
    it is not trusted                schema-constrained output is well-formed,
                                     which is not the same as correct

And it is only asked when the deterministic pass could not do the job, because
most of what the boss says is an instruction, and paying a model to be told
what `files_create` already knew is the waste this architecture exists to
avoid.
"""
from __future__ import annotations
import pytest
from friday import planner as P
from friday import planner_model as PM
from friday import semantics as S
COMMANDS = ['check my computer and open Paint', 'create a note, read it, then recycle it', 'open Paint', 'play something by daft punk']


@pytest.mark.parametrize("text", COMMANDS)
def test_an_instruction_does_not_need_a_model(text):
    """
    The deterministic reading is free, instant, and works with the provider
    down. Spending a model call to confirm it is the waste this avoids.
    """
    plan = P.interpret(text)
    assert not PM.needs_model(text, plan), \
        f"{text!r} would have paid for a model call"


def test_a_described_idea_does_need_a_model():
    """
    No verb it knows, no target it can name. The deterministic parser says so
    honestly rather than guessing, and honest is not the same as useful.
    """
    text = ("I have been thinking about a small game where you play a "
            "lighthouse keeper during a storm and I would like your view on "
            "which engine suits that")
    plan = P.interpret(text)
    assert PM.needs_model(text, plan)


def test_unplaceable_content_in_a_long_request_asks_for_help():
    """
    Length is what separates a description from a command. Short and
    unreadable stays unreadable - see the floor gate below - but a long
    request the parser could only half-read is exactly what a model is for.
    """
    text = 'when you get a moment I would like you to have a look at the situation with the storage on this machine and give me your honest read on whether it needs attention soon'
    plan = P.interpret(text)
    assert PM.needs_model(text, plan), f"goals={[g.intent for g in plan.goals]} unresolved={plan.unresolved}"


def test_the_prompt_does_not_carry_the_capability_catalogue():
    """
    §28. The whole reason a model is allowed near planning at all is that it
    is not asked to choose from everything.
    """
    from friday import capabilities as C

    prompt = PM.INSTRUCTIONS.format(
        operations=", ".join(S.OPERATIONS),
        targets=", ".join(S.TARGETS),
        domains=PM.domain_summary(),
    )

    named = [cap.id for cap in C._ALL if cap.id in prompt]
    assert not named, f"the planning prompt names capabilities: {named[:5]}"
    assert len(prompt) < 4000, (
        f"the planning prompt is {len(prompt)} characters; the point of the "
        f"taxonomy is that it does not grow with the registry")


def test_the_domain_summary_is_built_from_the_registry():
    """Written down, it would drift from what Friday can actually do."""
    summary = PM.domain_summary()
    assert "FILE" in summary and "WEB" in summary and "APPLICATION" in summary
    assert len(summary) < 2000, len(summary)


def test_the_prompt_tells_the_model_not_to_name_tools():
    assert "Do NOT name tools" in PM.INSTRUCTIONS


def test_a_hallucinated_capability_never_reaches_the_plan():
    """
    §27. The model was told not to name tools. This is what happens when it
    does anyway - the entity field is the only place one could hide.
    """
    plan = PM._as_plan({"goals": [
        {"intent": "do the thing", "operation": "OPEN",
         "target": "APPLICATION", "entity": "magic_super_tool"},
    ]})

    assert len(plan.goals) == 1
    assert plan.goals[0].entity == "", \
        "a capability name survived in the entity field"
    assert plan.goals[0].capability == "", "interpretation chose a capability"


def test_an_operation_outside_the_vocabulary_is_refused():
    """
    An enum can be schema-valid and still wrong. Recorded, not repaired -
    guessing at what it meant is how a wrong tool gets chosen.
    """
    plan = PM._as_plan({"goals": [
        {"intent": "frobnicate it", "operation": "FROBNICATE",
         "target": "FILE"},
        {"intent": "open Paint", "operation": "OPEN", "target": "APPLICATION"},
    ]})

    assert len(plan.goals) == 1, [g.intent for g in plan.goals]
    assert plan.goals[0].intent == "open Paint"
    assert "frobnicate it" in plan.unresolved


def test_a_target_outside_the_vocabulary_is_refused():
    plan = PM._as_plan({"goals": [
        {"intent": "do it", "operation": "OPEN", "target": "SPACESHIP"},
    ]})
    assert plan.goals == []
    assert plan.unresolved == ["do it"]


def test_a_goal_with_no_intent_is_dropped():
    plan = PM._as_plan({"goals": [
        {"intent": "  ", "operation": "OPEN", "target": "APPLICATION"},
    ]})
    assert plan.goals == []


def test_the_model_may_say_one_goal_follows_another():
    plan = PM._as_plan({"goals": [
        {"intent": "research engines", "operation": "SEARCH", "target": "WEB"},
        {"intent": "write it down", "operation": "CREATE", "target": "FILE",
         "follows_previous": True},
    ]})
    assert plan.goals[1].depends_on == (plan.goals[0].goal_id,)


def test_independent_goals_stay_independent():
    plan = PM._as_plan({"goals": [
        {"intent": "check the computer", "operation": "READ",
         "target": "SYSTEM"},
        {"intent": "open Paint", "operation": "OPEN", "target": "APPLICATION"},
    ]})
    assert plan.goals[1].depends_on == (), \
        "goals were chained without the model saying they follow"


def test_a_missing_provider_keeps_the_deterministic_reading(monkeypatch):
    """
    A planning model that is down is not a reason to plan badly - and
    emphatically not a reason to fall back to the clause splitter, which is
    the thing all of this replaced.
    """
    monkeypatch.setattr(PM, "_model", lambda: None)

    plan = PM.plan_objective("check my computer and open Paint")
    chosen = [goal.capability for goal in plan.goals]
    assert "apps_open" in chosen, chosen
    assert "system_resource_usage" in chosen, chosen


def test_a_failing_model_keeps_the_deterministic_reading(monkeypatch):
    monkeypatch.setattr(PM, "interpret",
                        lambda text, context="": (_ for _ in ()).throw(
                            AssertionError("should not raise out")))
    monkeypatch.setattr(PM, "needs_model", lambda text, plan: False)

    plan = PM.plan_objective("check my computer and open Paint")
    assert [goal.capability for goal in plan.goals]


def test_what_the_deterministic_pass_understood_is_not_lost(monkeypatch):
    """
    It reads constraints out of sentence structure. Losing "do not ask me to
    continue" because a model did not repeat it would be a regression wearing
    an upgrade's clothes.
    """
    assisted = P.Plan(goals=[P.Goal("g1", "open Paint", S.OPEN,
                                    "APPLICATION", entity="Paint")])
    monkeypatch.setattr(PM, "needs_model", lambda text, plan: True)
    monkeypatch.setattr(PM, "interpret", lambda text, context="": assisted)

    plan = PM.plan_objective(
        "open Paint. Do not ask me to continue. Tell me the final result.")

    assert any("continue" in item.lower() for item in plan.constraints), \
        plan.constraints
    assert plan.reporting, "the reporting request was lost"


def test_a_model_plan_is_resolved_by_the_registry(monkeypatch):
    """
    The model says what is wanted; the registry says what can do it. This is
    the join, and it is where `product_status` gets rejected for a research
    goal without anybody writing a rule about product_status.
    """
    assisted = P.Plan(goals=[
        P.Goal("g1", "look into which engine suits it", S.SEARCH, "WEB"),
        P.Goal("g2", "write what you find into a file", S.CREATE, "FILE",
               depends_on=("g1",)),
    ])
    monkeypatch.setattr(PM, "needs_model", lambda text, plan: True)
    monkeypatch.setattr(PM, "interpret", lambda text, context="": assisted)

    plan = PM.plan_objective("a described idea, at length, with no clear verbs")

    research, writing = plan.goals
    assert research.capability.startswith("web_"), research.capability
    assert research.capability != "product_status"
    assert writing.capability.startswith("files_"), writing.capability
    assert writing.depends_on == ("g1",)
    assert P.validate(plan) == []


def test_short_nonsense_is_not_handed_to_a_model():
    """
    Measured: "flurb the wibble, then open Paint" produced a confident goal
    for the wibble. A model answers because answering is what it does, and
    that is the guessing this layer exists to prevent, arriving one level up.
    """
    text = 'flurb the wibble, then open Paint'
    plan = P.interpret(text)
    assert plan.unresolved, 'the deterministic pass understood the nonsense'
    assert not PM.needs_model(text, plan), 'six words of nonsense were sent to a planning model'


def test_a_long_description_still_reaches_the_model():
    """The floor must not close the door it exists to leave open."""
    text = 'I have been thinking about a small game where you play a lighthouse keeper during a storm and I would like your view on which engine would suit that sort of thing best'
    assert len(text.split()) > PM.MODEL_ASSIST_WORDS
    assert PM.needs_model(text, P.interpret(text))


def test_a_recorded_decision_becomes_planning_context(tmp_path):
    """
    Without this the loop stops one step short of useful: Friday asks good
    questions, records the answers durably, and then plans as though the
    conversation never happened.
    """
    from friday import requirements as REQ
    from friday.store import Store
    store = Store(tmp_path / 'context.sqlite3')
    store.record_decision(project='halo', decision='engine - Godot', rationale='2D tooling', source='the boss answered')
    REQ.ask('halo', [REQ.Question('Who is it for?', why='scope')], db=store)
    context = PM.what_was_decided(db=store)
    assert 'halo' in context
    assert 'Godot' in context


def test_nothing_decided_adds_nothing_to_the_prompt(tmp_path):
    """Planning context is retrieved, not accumulated."""
    from friday.store import Store
    assert PM.what_was_decided(db=Store(tmp_path / 'empty.sqlite3')) == ''


def test_two_projects_in_flight_contribute_no_context(tmp_path):
    """
    Better to plan without context than to plan with another project's
    decisions - being confidently wrong about what was agreed is worse than
    not remembering.
    """
    from friday import requirements as REQ
    from friday.store import Store
    store = Store(tmp_path / 'two.sqlite3')
    store.record_decision(project='halo', decision='engine - Godot', rationale='', source='the boss')
    REQ.ask('halo', [REQ.Question('Who is it for?', why='scope')], db=store)
    REQ.ask('pipeline', [REQ.Question('Which CI?', why='scope')], db=store)
    assert PM.what_was_decided(db=store) == ''


def test_the_context_stays_small(tmp_path):
    """
    The point of the durable store is that relevant things are retrieved, not
    that everything is sent. Twenty decisions must not become twenty lines in
    every planning prompt.
    """
    from friday import requirements as REQ
    from friday.store import Store
    store = Store(tmp_path / 'many.sqlite3')
    for index in range(20):
        store.record_decision(project='halo', decision=f"decision {index}", rationale='', source='the boss')
    REQ.ask('halo', [REQ.Question('Who is it for?', why='scope')], db=store)
    context = PM.what_was_decided(db=store)
    assert context.count('decision ') <= 8, context
