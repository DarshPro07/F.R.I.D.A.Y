"""
ASSUME-001: an assumption register attached to the objective, and a
high-impact unvalidated assumption validated before dependent work starts.
"""

from __future__ import annotations

import pytest

from friday import capabilities
from friday import contracts as c
from friday import objectives as O
from friday import requirements as R
from friday.store import Store

TEXT = "create notes.md containing Dune"


@pytest.fixture
def store(tmp_path):
    return Store(tmp_path / "ada.sqlite3")


def _entry(**changes) -> R.Assumption:
    base = dict(statement="Save format? -> markdown", origin="understand:save format",
                confidence=R.LOW, consequence=R.HIGH,
                validation="ask which format the reader opens", state=R.UNVALIDATED)
    return R.Assumption(**{**base, **changes})


def _compile(store, register):
    tasks = [{"capability": "files_create",
              "arguments": {"path": "notes.md", "content": "Dune"}},
             {"capability": "files_read", "arguments": {"path": "notes.md"},
              "dependencies": ["t1"]}]
    return O.compile_objective(
        store, request=TEXT, tasks=tasks, manifest=capabilities.as_dicts(),
        objective_summary=TEXT, provenance=c.PERSON, attended=True,
        assumptions=tuple(register))


def _statuses(store, run_id):
    return [row["status"] for row in store.objective_tasks(run_id)]


def test_a_register_entry_round_trips_with_all_six_fields_across_a_reopen(tmp_path):
    path = tmp_path / "reopen.sqlite3"
    first = Store(path)
    entry = _entry(state=R.REFUTED)
    first.save_assumption("run-1", key=entry.key, **entry.to_dict())
    first.close()

    (row,) = Store(path).list_assumptions("run-1")
    assert R.Assumption.from_dict(row) == entry
    assert (row["statement"], row["origin"], row["confidence"],
            row["consequence"], row["validation"], row["state"]) == (
        entry.statement, entry.origin, R.LOW, R.HIGH, entry.validation, R.REFUTED)


def test_the_due_rule_is_high_consequence_or_unsure_about_something_that_matters():
    high = _entry(statement="a", consequence=R.HIGH, confidence=R.HIGH)
    unsure = _entry(statement="b", consequence=R.MEDIUM, confidence=R.LOW)
    sure = _entry(statement="c", consequence=R.MEDIUM, confidence=R.HIGH)
    trivial = _entry(statement="d", consequence=R.LOW, confidence=R.LOW)
    done = _entry(statement="e", consequence=R.HIGH, state=R.VALIDATED)

    due = R.due_for_validation([unsure, sure, trivial, done, high])

    assert due == (high, unsure)          # worst first; the rest are not due


def test_the_model_can_raise_the_consequence_of_a_decided_question():
    found = R._as_understanding({"subject": "a game", "questions": [
        {"question": "Which save format?", "why": "readers differ",
         "materiality": "SMALL", "proposed": "markdown",
         "confidence": "low", "consequence": "HIGH", "validation": "ask the reader"},
        {"question": "Audio layered?", "why": "nobody will remember",
         "materiality": "SMALL", "proposed": "yes", "consequence": "nonsense"},
    ]})

    first, second = found.register()

    assert (first.confidence, first.consequence, first.validation) == (
        R.LOW, R.HIGH, "ask the reader")
    assert first.origin == "understand:" + found.assumptions[0].key
    assert (second.confidence, second.consequence, second.state) == (
        R.MEDIUM, R.LOW, R.UNVALIDATED)
    assert R.due_for_validation(found.register()) == (first,)


def test_a_high_impact_unvalidated_assumption_is_validated_before_dependent_work(store):
    created = _compile(store, [_entry()])
    run_id = created["run_id"]

    assert created["status"] == O.RUN_WAITING_QUESTION
    assert _statuses(store, run_id) == [O.TASK_QUEUED, O.TASK_QUEUED]
    (due,) = [e for e in store.objective_events(run_id)
              if e["event"] == O.EVENT_ASSUMPTION_DUE]
    assert due["detail"]["assumptions"][0]["consequence"] == R.HIGH
    (row,) = store.list_assumptions(run_id)
    assert row["state"] == R.UNVALIDATED


def test_validating_it_releases_the_dependents(store):
    entry = _entry()
    run_id = _compile(store, [entry])["run_id"]

    assert O.resolve_assumption(store, run_id, entry.key, R.VALIDATED)

    assert store.objective_run(run_id)["status"] == O.RUN_RUNNING
    assert _statuses(store, run_id) == [O.TASK_READY, O.TASK_QUEUED]
    assert store.list_assumptions(run_id)[0]["state"] == R.VALIDATED


def test_a_held_run_is_a_legitimate_wait_that_names_what_to_confirm(store):
    entry = _entry()
    created = _compile(store, [entry])

    assert created["next_wake"] is None     # nobody wakes it, like a pause
    assert entry.statement in created["blocker"]

    O.resolve_assumption(store, created["run_id"], entry.key, R.WAIVED)

    released = store.objective_run(created["run_id"])
    assert released["next_wake"] and not released["blocker"]


def test_the_person_validates_it_through_the_resume_tool(store):
    from friday.toolsets import objectives as OT

    OT.reset_store(store)
    try:
        entry = _entry()
        run_id = _compile(store, [entry])["run_id"]
        run = c.Run.create("assume-test", capability="objectives")

        listed = OT.objective_status(run, run_id).output["assumptions"]
        assert [(row["key"], row["state"]) for row in listed] == [
            (entry.key, R.UNVALIDATED)]

        bad = OT.objective_resume(run, run_id, assumption=entry.key, state="UNVALIDATED")
        assert bad.status == c.FAILED
        missing = OT.objective_resume(run, run_id, assumption="no such key")
        assert missing.status == c.FAILED
        assert store.objective_run(run_id)["status"] == O.RUN_WAITING_QUESTION

        done = OT.objective_resume(run, run_id, assumption=entry.key, state="validated")

        assert done.status == c.SUCCEEDED
        assert done.output["status"] == O.RUN_RUNNING
        assert _statuses(store, run_id) == [O.TASK_READY, O.TASK_QUEUED]
        assert store.list_assumptions(run_id)[0]["state"] == R.VALIDATED
    finally:
        OT.reset_store(None)


def test_a_refuted_assumption_does_not_release_the_work_built_on_it(store):
    entry = _entry()
    run_id = _compile(store, [entry])["run_id"]

    assert O.resolve_assumption(store, run_id, entry.key, R.REFUTED)

    assert store.objective_run(run_id)["status"] == O.RUN_WAITING_QUESTION
    assert _statuses(store, run_id) == [O.TASK_QUEUED, O.TASK_QUEUED]


def test_a_validated_or_low_impact_assumption_schedules_nothing(store):
    for register in ([_entry(state=R.VALIDATED)],
                     [_entry(consequence=R.LOW, confidence=R.LOW)], []):
        created = _compile(store, register)
        assert created["status"] == O.RUN_RUNNING
        assert _statuses(store, created["run_id"]) == [O.TASK_READY, O.TASK_QUEUED]
        assert not [e for e in store.objective_events(created["run_id"])
                    if e["event"] == O.EVENT_ASSUMPTION_DUE]


class _Ctx:
    """A chat context that records what the agent adds to it."""

    def __init__(self):
        self.said = []

    def add_message(self, *, role, content):
        self.said.append(content)


@pytest.fixture
def wired(store):
    """The store the objective and memory tools read, closed after the test."""
    from friday.toolsets import memory as M
    from friday.toolsets import objectives as OT

    M.reset_store(store)
    OT.reset_store(store)
    yield store
    OT.reset_store(None)
    M.reset_store(None)


def _start_from_words(monkeypatch, question):
    """objective_start on a request whose reader made `question` a decision."""
    from friday import policy as PO
    from friday.toolsets import objectives as OT

    monkeypatch.setattr(R, "is_an_idea", lambda t: True)
    monkeypatch.setattr(R, "understand", lambda t, project="", db=None: R.Understanding(
        assumptions=[question]))
    result = OT.objective_start(c.Run.create(TEXT), objective=TEXT,
                                engine=PO.PolicyEngine(autonomy=PO.FULL))
    assert result.status == c.SUCCEEDED, result.error
    return result.output


def test_starting_a_held_objective_reports_what_it_awaits(wired, monkeypatch):
    store = wired
    output = _start_from_words(monkeypatch, R.Question(
        "Save format?", why="the reader opens one", materiality=R.SMALL,
        proposed="markdown", confidence=R.LOW, consequence=R.HIGH,
        validation="ask which format the reader opens"))

    (row,) = store.objective_runs(limit=5)
    assert row["status"] == O.RUN_WAITING_QUESTION
    assert output["status"] == O.RUN_WAITING_QUESTION
    assert {t["status"] for t in store.objective_tasks(row["run_id"])} == {O.TASK_QUEUED}
    (held,) = output["awaiting_validation"]
    statement = "Save format? -> markdown"
    assert held == {"key": R.Assumption(statement=statement, origin="request").key,
                    "statement": statement,
                    "validation": "ask which format the reader opens"}


def test_starting_an_unheld_objective_reports_nothing_to_await(wired, monkeypatch):
    store = wired
    output = _start_from_words(monkeypatch, R.Question(
        "Save format?", why="the reader opens one", materiality=R.SMALL,
        proposed="markdown", confidence=R.HIGH, consequence=R.LOW))

    (row,) = store.objective_runs(limit=5)
    assert row["status"] == O.RUN_RUNNING
    assert "awaiting_validation" not in output
    assert O.awaiting_validation(store, row["run_id"]) == []


def test_only_a_run_waiting_on_the_person_awaits_validation(store):
    entry = _entry()
    run_id = _compile(store, [entry])["run_id"]
    assert [row["key"] for row in O.awaiting_validation(store, run_id)] == [entry.key]

    O.resolve_assumption(store, run_id, entry.key, R.VALIDATED)

    assert O.awaiting_validation(store, run_id) == []
    assert O.awaiting_validation(store, "no-such-run") == []


def test_the_model_is_told_a_held_objective_has_not_started(store):
    import agent_friday
    from friday.toolsets import objectives as OT

    entry = _entry()
    run_id = _compile(store, [entry])["run_id"]
    OT.reset_store(store)
    try:
        ctx = _Ctx()
        agent_friday.say_the_objective_owns_this(ctx, run_id)
    finally:
        OT.reset_store(None)

    (said,) = ctx.said
    assert run_id in said
    assert "HELD" in said and "running it now" not in said
    assert entry.statement in said and entry.validation in said
    assert "objective_resume" in said and entry.key in said
    assert "NOT carry out that work yourself" in said and "use_capability" in said


def test_the_model_is_still_told_an_unheld_objective_is_running(store):
    import agent_friday
    from friday.toolsets import objectives as OT

    run_id = _compile(store, [_entry(consequence=R.LOW)])["run_id"]
    OT.reset_store(store)
    try:
        ctx = _Ctx()
        agent_friday.say_the_objective_owns_this(ctx, run_id)
    finally:
        OT.reset_store(None)

    (said,) = ctx.said
    assert "running it now" in said and run_id in said
    assert "HELD" not in said and "objective_resume" not in said


def test_a_reader_failure_leaves_the_objective_compiling(monkeypatch):
    monkeypatch.setattr(R, "is_an_idea", lambda text: True)

    def broken(text, project="", db=None):
        raise RuntimeError("model unavailable")

    monkeypatch.setattr(R, "understand", broken)
    monkeypatch.setattr(R, "still_blocking",
                        lambda text, db=None: (_ for _ in ()).throw(RuntimeError("store")))

    assert R.grounded_for("I want to build a game") == ((), ())
    assert R.ground_for("I want to build a game") == ((), ())
