"""
The executor question channel, which existed and was wired to nothing.

`ada_ask` was a registered capability resolving to no implementation -
`audit_planner` says so in its own source, "registered but nothing to call" -
and `broker_for` built the object that would have answered it, from a
function nothing called. The agent was told in its prompt that it could ask,
and had nowhere to ask.

The two tests that matter are the acceptance cases: a question project memory
settles must be answered without troubling him, and a material question it
does not settle must stop the run rather than being guessed.
"""
import pytest
from friday import contracts as c
from friday.executors import brokers as B
from friday.executors import runs as RUNS
from friday.toolsets import executor as X


@pytest.fixture
def store(tmp_path, monkeypatch):
    from friday.store import Store
    import friday.toolsets.memory as memory

    built = Store(str(tmp_path / "ada.sqlite3"))
    monkeypatch.setattr(memory, "_store", built, raising=False)
    monkeypatch.setattr(memory, "store", lambda: built)
    return built


@pytest.fixture
def project(store):
    store.ensure_project("drone")
    store.record_decision("drone", "engine - Godot 4",
                          source="checked against sources")
    return "drone"


def _development_run(store, project, run_id='DEV-1', status=RUNS.RUNNING):
    """
    A run as the executor actually opens one.

    The bundle is stored in full because that is what `RunManager.open` does,
    and a fixture storing "{}" tested a shape production never produces.
    """
    from dataclasses import asdict
    import json as _json
    from friday.executors.claude_code import TaskBundle
    bundle = TaskBundle(goal='build the thing', workspace='.', project=project, run_id=run_id)
    store.open_executor_run(run_id, executor_type='claude_code', working_directory='.', project=project, task_bundle=_json.dumps(asdict(bundle)), status=status)
    return run_id


def _ask(question, options="", run_id=""):
    run = c.Run.create(f"ask: {question}", capability="ada_ask")
    return X.ada_ask(run, question, options=options, run_id=run_id)


def test_a_settled_question_is_answered_from_the_project(store, project):
    """
    Acceptance case B. He already decided the engine; asking him again is the
    annoyance this whole channel exists to prevent.
    """
    _development_run(store, project)
    result = _ask("Which engine should I use for this game?")

    assert result.status == c.SUCCEEDED
    assert result.output["outcome"] == B.ANSWER_FROM_PROJECT
    assert "Godot" in result.output["answer"]
    assert result.output["authority"] == B.USER_DECISION


def test_a_material_question_stops_the_run(store, project):
    """
    Acceptance case C. Nothing settles this and it changes what gets built,
    so the run waits. WAIT_USER, not a guess.
    """
    _development_run(store, project)
    result = _ask("Should the game be single player or multiplayer?")

    assert result.status == c.PARTIAL
    assert result.output["outcome"] == B.WAIT_USER
    assert not result.output["answer"]
    assert not result.may_claim_completion


def test_a_cheap_reversible_choice_is_decided_and_labelled(store, project):
    _development_run(store, project)
    result = _ask("What should I call the input variable?",
                  options='["speed", "velocity"]')

    assert result.status == c.SUCCEEDED
    assert result.output["outcome"] == B.ANSWER_LOW_RISK_DEFAULT
    assert result.output["answer"] == "speed"
    assert result.output["authority"] == B.IMPLEMENTATION_DECISION, \
        "a default recorded as his decision would come back tomorrow as fact"


@pytest.mark.parametrize("question", [
    "Should I deploy this to production?",
    "Can I delete the old save files?",
    "Shall I force-push over main?",
    "Should we charge users for this?",
    "Can I email the users about it?",
    "Should I run the database migration?",
])
def test_the_dangerous_questions_always_reach_him(store, project, question):
    """
    Checked before the evidence, deliberately. A decision saying "deploy to
    production" must not authorise a *different* deploy later, and the only
    way to guarantee that is to never let this class be answered by keyword
    match.
    """
    _development_run(store, project)
    result = _ask(question)
    assert result.output["outcome"] == B.WAIT_USER, question


def test_a_dangerous_question_is_not_answered_even_when_memory_has_it(store):
    """The ordering, proven rather than asserted."""
    store.ensure_project("drone")
    store.record_decision("drone", "deploy to production with one click",
                          source="he said once")
    _development_run(store, "drone")

    assert _ask("Should I deploy to production?").output["outcome"] == \
        B.WAIT_USER


def test_contradictory_project_memory_is_escalated(store):
    """
    Choosing one silently would make Friday the author of a decision nobody
    made.
    """
    store.ensure_project("drone")
    store.record_decision("drone", "the game works offline only",
                          source="he said")
    store.record_decision("drone", "the game works online multiplayer",
                          source="he said")
    _development_run(store, "drone")

    result = _ask("Should the game work offline or online?")
    assert result.output["outcome"] in (B.CONFLICT, B.WAIT_USER)
    if result.output["outcome"] == B.CONFLICT:
        assert result.output["conflicting"]


def test_there_is_no_outcome_that_means_make_something_up():
    """
    The absence of a fifth outcome is the point. An agent with a prompt
    telling it to keep going and no way to ask invents an answer.
    """
    assert set(B.OUTCOMES) == {B.ANSWER_FROM_PROJECT,
                              B.ANSWER_LOW_RISK_DEFAULT,
                              B.WAIT_USER, B.CONFLICT}


def test_an_answered_question_is_recorded_so_it_is_asked_once(store, project):
    _development_run(store, project)
    _ask("What should I call the input variable?", options='["speed"]')

    decisions = " ".join(row["decision"] for row in store.decisions(project))
    assert "speed" in decisions


def test_a_default_is_recorded_as_friday_s_choice_not_his(store, project):
    _development_run(store, project)
    _ask("What should I call the input variable?", options='["speed"]')

    row = next(r for r in store.decisions(project) if r["decision"] == "speed")
    assert B.IMPLEMENTATION_DECISION in row["source"]
    assert B.USER_DECISION not in row["source"]


def test_a_waiting_run_is_marked_in_the_store(store, project):
    """
    In the store, not in memory. The whole point of WAITING_QUESTION is that
    it survives the restart that loses process state.
    """
    run_id = _development_run(store, project)
    _ask("Should the game be single player or multiplayer?")

    assert store.executor_run(run_id)["status"] == RUNS.WAITING_QUESTION


def test_an_unanswered_question_claims_no_completion(store, project):
    _development_run(store, project)
    assert not _ask("Should this be single or multiplayer?").may_claim_completion


def test_the_run_can_be_named_explicitly(store, project):
    _development_run(store, project, run_id="DEV-7")
    result = _ask("Which engine should I use?", run_id="DEV-7")
    assert result.output["development_run"] == "DEV-7"


def test_a_finished_run_is_not_treated_as_live(store, project):
    _development_run(store, project, status=RUNS.SUCCEEDED
                     if hasattr(RUNS, "SUCCEEDED") else "SUCCEEDED")
    result = _ask("Which engine should I use?")
    assert result.output["development_run"] == "", \
        "a completed run answered a live question"


def test_a_question_with_no_run_at_all_still_gets_a_verdict(store):
    """
    Worse context, same policy. Refusing to answer because the bookkeeping is
    missing would strand the agent.
    """
    result = _ask("Should I deploy to production?")
    assert result.output["outcome"] == B.WAIT_USER


def test_an_empty_question_is_refused(store, project):
    _development_run(store, project)
    assert _ask("   ").status == c.FAILED


@pytest.mark.parametrize("given,expected", [
    ('["a", "b"]', ["a", "b"]),
    ("a, b", ["a", "b"]),
    ("", []),
    ("not json at all", ["not json at all"]),
])
def test_options_are_read_either_way(given, expected):
    assert X._as_options(given) == expected


def test_ada_ask_actually_resolves():
    """
    It was a registered capability pointing at nothing for a long time. This
    is the assertion that would have caught that.
    """
    from friday.capability_runtime import resolutions, unresolved

    assert "ada_ask" not in unresolved()
    assert resolutions()["ada_ask"].function == "ada_ask"


def test_broker_for_is_on_the_production_path():
    """
    It existed, was correct, and was called by nothing. Building a second
    path to the same object would have left it that way while looking like
    progress.
    """
    import inspect

    assert "broker_for" in inspect.getsource(X._broker)


def test_the_audit_still_knows_it_needs_a_live_run():
    """
    It was SESSION_REQUIRED because nothing called it. It is SESSION_REQUIRED
    now because a question needs a run to belong to. Same verdict, different
    reason - and the audit planner must not quietly start exercising it
    against no run just because it became reachable.
    """
    from friday import audit_planner as A
    assert A.plan_audit().strategy['ada_ask'] == A.SESSION_REQUIRED
