"""
"Continue Halo." — picking a project up again, from nothing but the store.

Journey C. The storage was already good: `projects`, `project_decisions`,
`open_questions`, `artifacts`, and `store.projects()` to read them. What was
missing was anything the boss could actually say. There was no `projects_list`
and no `project_resume` in the registry, so "what am I working on?" and
"continue Halo" had no capability behind them at all — the answer came from
whatever the model remembered of the conversation, which is precisely the
failure durable memory exists to prevent.

The test that matters is the restart one: a project built in one place,
resumed from a different store handle, with no conversation history anywhere.
"""
from __future__ import annotations
import pytest
from friday import capability_runtime as R
from friday import contracts as c
from friday.store import Store
from friday.toolsets import memory as M


@pytest.fixture
def store(tmp_path) -> Store:
    db = Store(tmp_path / "projects.sqlite3")
    M.reset_store(db)
    yield db
    M.reset_store(None)


def call(capability: str, arguments: dict | None = None):
    return R.CapabilityRuntime(principal=R.CONVERSATION).execute(
        capability, arguments or {})


def a_project(store, name="halo"):
    store.record_decision(project=name, decision="bus - CAN, not I2C",
                          source="the boss answered",
                          rationale="noise immunity over distance")
    store.record_decision(project=name, decision="engine - Godot",
                          source="the boss answered",
                          rationale="better 2D tooling")
    store.ask_question(name, "How many nodes on the bus?",
                       why="decides the addressing scheme")


@pytest.mark.parametrize("capability", ["projects_list", "project_resume"])
def test_the_capability_is_registered_and_reachable(capability):
    from friday import capabilities as C

    assert C.by_id(capability) is not None, f"{capability} is not registered"
    assert capability in R.reachable(), f"{capability} resolves to nothing"


def test_they_read_memory_rather_than_the_machine():
    """
    Both defaulted to SYSTEM, which put "what am I working on" in competition
    with `system_get_info`. A project is durable memory about a body of work.
    """
    from friday import semantics as S

    assert S.for_capability("projects_list") == ("LIST", "MEMORY")
    assert S.for_capability("project_resume") == ("FOLLOW_UP", "MEMORY")


def test_resuming_a_project_is_not_resuming_a_song():
    """
    The same object-decides-the-verb problem `_REQUEST_VERBS` documents:
    "resume" is CONTROL of playback and FOLLOW_UP of a project.
    """
    from friday import semantics as S

    assert S.for_capability("music_resume")[0] == "CONTROL"
    assert S.for_capability("project_resume")[0] == "FOLLOW_UP"


def test_it_lists_what_is_on_record(store):
    a_project(store, "halo")
    a_project(store, "lighthouse")

    result = call("projects_list")
    assert result.status == c.SUCCEEDED, result.error
    assert {row["project"] for row in result.output["projects"]} == \
        {"halo", "lighthouse"}


def test_it_says_how_much_is_settled_and_how_much_is_not(store):
    a_project(store)
    row = call("projects_list").output["projects"][0]

    assert row["decisions"] == 2
    assert row["open_questions"] == 1


def test_activity_orders_them_not_creation(store):
    """
    A project touched an hour ago is the one being asked about. One started in
    March and untouched since is history.
    """
    store.ensure_project("old")
    store.ensure_project("newer")
    store.record_decision(project="old", decision="still going",
                          source="the boss")

    assert call("projects_list").output["projects"][0]["project"] == "old"


def test_no_projects_is_said_rather_than_faked(store):
    result = call("projects_list")
    assert result.status == c.FAILED
    assert "no projects" in result.error


def test_it_reconstructs_a_project_with_no_conversation_at_all(store):
    """
    The journey. Nothing here has ever spoken to Friday - the project exists
    only as rows.
    """
    a_project(store)
    result = call("project_resume", {"project": "halo"})

    assert result.status == c.SUCCEEDED, result.error
    found = result.output
    assert {d["decision"] for d in found["decisions"]} == \
        {"bus - CAN, not I2C", "engine - Godot"}
    assert found["open_questions"][0]["question"] == "How many nodes on the bus?"


def test_it_survives_a_restart(tmp_path):
    """
    Built through one handle, resumed through another, with the first closed.
    A project that only exists in a live process is not a durable project.
    """
    path = tmp_path / "restart.sqlite3"
    first = Store(path)
    M.reset_store(first)
    try:
        a_project(first)
    finally:
        M.reset_store(None)
    first.close()

    second = Store(path)
    M.reset_store(second)
    try:
        found = call("project_resume", {"project": "halo"}).output
        assert len(found["decisions"]) == 2
        assert len(found["open_questions"]) == 1
    finally:
        M.reset_store(None)


def test_a_blocking_question_comes_before_everything_else(store):
    """
    The order of `next_step` is the argument. Guessing the next move while a
    *material* question is open is how Friday builds the wrong thing
    confidently.

    This used to assert that any open question blocked, which was the
    contradiction: `requirements.py` says an assumption spoken out loud lets
    work continue, and both could not be true. `a_project` asks an
    unclassified question, which defaults to blocking - the default is to
    wait.
    """
    a_project(store)
    step = call('project_resume', {'project': 'halo'}).output['next_step']
    assert 'blocking question' in step
    assert 'How many nodes on the bus?' in step


def test_once_everything_is_answered_the_next_move_is_to_build(store):
    a_project(store)
    asked = store.open_questions("halo")[0]
    store.answer_question(asked["id"], "twelve")

    step = call("project_resume", {"project": "halo"}).output["next_step"]
    assert "build" in step


def test_a_project_with_nothing_in_it_says_so(store):
    store.ensure_project("empty")
    step = call("project_resume", {"project": "empty"}).output["next_step"]
    assert "nothing decided" in step


def test_the_name_does_not_have_to_be_exact(store):
    """"Continue Halo" is what gets said; `halo` is what got stored."""
    a_project(store, "lighthouse-keeper")

    assert call("project_resume", {"project": "Halo"}).status == c.FAILED
    assert call("project_resume", {"project": "lighthouse"}).status \
        == c.SUCCEEDED


def test_an_ambiguous_name_is_refused_rather_than_guessed(store):
    """
    Resuming the wrong project is worse than asking which - it would answer
    confidently about work the boss is not doing.
    """
    a_project(store, "halo-firmware")
    a_project(store, "halo-app")

    result = call("project_resume", {"project": "halo"})
    assert result.status == c.FAILED
    assert "halo-app" in result.error and "halo-firmware" in result.error


def test_an_unknown_project_is_not_invented(store):
    a_project(store)
    result = call("project_resume", {"project": "atlantis"})

    assert result.status == c.FAILED
    assert "atlantis" in result.error


def test_both_carry_verification_a_person_could_check(store):
    a_project(store)

    for capability, arguments in (("projects_list", {}),
                                  ("project_resume", {"project": "halo"})):
        result = call(capability, arguments)
        assert result.may_claim_completion
        assert result.verification.evidence
        assert result.verification.method == "project_query"


def test_a_cosmetic_question_does_not_stop_the_work(store):
    from friday import requirements as REQ
    store.record_decision(project='drone', decision='engine - Godot', source='the boss answered')
    REQ.ask('drone', [REQ.Question('What colour for the start button?', why='cosmetic', materiality=REQ.SMALL, proposed='the theme accent')], db=store)
    found = call('project_resume', {'project': 'drone'}).output
    assert found['ready_to_build'] is True
    assert 'blocked' in found['next_step'] or 'build' in found['next_step']
    assert found['counts']['blocking'] == 0


def test_a_material_question_does_stop_it(store):
    from friday import requirements as REQ
    REQ.ask('drone', [REQ.Question('Desktop or mobile?', why='decides the whole technology choice', materiality=REQ.MATERIAL)], db=store)
    found = call('project_resume', {'project': 'drone'}).output
    assert found['ready_to_build'] is False
    assert 'Desktop or mobile?' in found['next_step']


def test_the_assumption_is_named_rather_than_hidden(store):
    """
    Friday chose it; the boss did not. Recording it as a decision would put
    words in his mouth, and hiding it would mean he finds out when it is
    expensive.
    """
    from friday import requirements as REQ
    REQ.ask('drone', [REQ.Question('What colour for the start button?', why='cosmetic', materiality=REQ.SMALL, proposed='the theme accent')], db=store)
    found = call('project_resume', {'project': 'drone'}).output
    assert found['assumptions'][0]['assumption'] == 'the theme accent'
    assert 'say if any is wrong' in found['next_step']
    assert not store.decisions('drone'), 'an assumption was recorded as something the boss decided'


def test_both_kinds_together_block_only_on_the_material_one(store):
    from friday import requirements as REQ
    REQ.ask('drone', [REQ.Question('Desktop or mobile?', why='technology', materiality=REQ.MATERIAL), REQ.Question('What colour for the start button?', why='cosmetic', materiality=REQ.SMALL, proposed='the theme accent')], db=store)
    found = call('project_resume', {'project': 'drone'}).output
    assert found['counts']['blocking'] == 1
    assert found['counts']['assumptions'] == 1
    assert [q['question'] for q in found['blocking_questions']] == ['Desktop or mobile?']


def test_a_question_nobody_classified_blocks(store):
    """
    The default is to wait. A question Friday could not classify is one it
    should not be guessing about.
    """
    identifier = store.ask_question('drone', 'Something unclassified', why='unknown')
    row = [q for q in store.open_questions('drone') if q['id'] == identifier][0]
    assert row['blocking']
