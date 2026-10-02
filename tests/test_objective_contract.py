"""
An objective carries its contract (PRD v7 INTENT-001, §10.3 and §11).

The intent compiler extracts the desired outcome, the constraints, the exact
literals, the preferences and why the person wants it; the objective keeps
them, durably, beside its task graph. And a plan that dropped something the
person named - a file, a path, quoted words - is not compiled at all: the
literal was theirs, and a run that silently used another one would be doing
something they did not ask for.
"""
from __future__ import annotations

import json

import pytest

from friday import capabilities
from friday import contracts as c
from friday import objectives as O
from friday import planner as P
from friday.store import Store

TEXT = ("I want to keep a reading list: create reading.md containing Dune, "
        "keep your updates short, and do not delete anything")


@pytest.fixture
def store(tmp_path):
    return Store(tmp_path / "ada.sqlite3")


def test_the_compiler_extracts_the_contract():
    plan = P.plan_objective(TEXT)
    contract = P.contract_for(plan, TEXT)
    assert contract.goal == TEXT
    assert contract.purpose == ("I want to keep a reading list:",)
    assert contract.exact_literals == ("reading.md", "Dune")
    assert any("delete" in s for s in contract.hard_constraints), contract
    assert any("short" in s for s in contract.preferences), contract
    assert contract.success_criteria, "no criterion says what done means"


def test_the_contract_round_trips():
    contract = P.contract_for(P.plan_objective(TEXT), TEXT)
    assert c.ObjectiveContract.from_dict(json.loads(json.dumps(contract.to_dict()))) == contract


def _compile(store, specs, contract):
    return O.compile_objective(store, request=TEXT, tasks=specs,
                               manifest=capabilities.as_dicts(), objective_summary=TEXT,
                               provenance=c.PERSON, attended=True, contract=contract)


def test_compile_keeps_the_contract_with_the_run(store):
    plan = P.plan_objective(TEXT)
    contract = P.contract_for(plan, TEXT)
    created = _compile(store, P.task_specs(plan), contract)
    stored = json.loads(store.objective_run(created["run_id"])["contract"])
    assert c.ObjectiveContract.from_dict(stored) == contract


def test_a_plan_that_dropped_what_the_person_named_is_not_compiled(store):
    plan = P.plan_objective(TEXT)
    contract = P.contract_for(plan, TEXT)
    specs = P.task_specs(plan)
    for spec in specs:                       # the plan "helpfully" renames the file
        if spec["capability"] == "files_create":
            spec["arguments"]["path"] = "friday-reading-list.txt"
    with pytest.raises(O.LiteralDropped, match="reading.md"):
        _compile(store, specs, contract)
    assert store.objective_runs(limit=5) == []


def test_starting_an_objective_from_words_keeps_its_contract(store, monkeypatch):
    from friday.toolsets import objectives as OT
    from friday import policy as PO

    OT.reset_store(store)
    try:
        run = c.Run.create(TEXT)
        result = OT.objective_start(run, objective=TEXT, engine=PO.PolicyEngine(autonomy=PO.FULL))
        assert result.status == c.SUCCEEDED, result.error
        (row,) = store.objective_runs(limit=5)
        contract = c.ObjectiveContract.from_dict(json.loads(row["contract"]))
        assert contract.exact_literals == ("reading.md", "Dune")
    finally:
        OT.reset_store(None)


def test_the_contract_records_what_is_assumed_and_still_unanswered(store, monkeypatch):
    """INTENT-001: assumptions and open questions are filled at compile."""
    from friday import policy as PO
    from friday import requirements as R
    from friday.toolsets import memory as M
    from friday.toolsets import objectives as OT

    text = "build the lighthouse keeper game: create reading.md containing Dune"
    R.ask("lighthouse-keeper", [R.Question("Which engine?", why="decides the build")],
          db=store)
    monkeypatch.setattr(R, "is_an_idea", lambda t: True)
    monkeypatch.setattr(R, "understand", lambda t, project="", db=None: R.Understanding(
        assumptions=[R.Question("Save format?", why="reversible", proposed="markdown")]))
    M.reset_store(store)
    OT.reset_store(store)
    try:
        result = OT.objective_start(c.Run.create(text), objective=text,
                                    engine=PO.PolicyEngine(autonomy=PO.FULL))
        assert result.status == c.SUCCEEDED, result.error
        (row,) = store.objective_runs(limit=5)
        contract = c.ObjectiveContract.from_dict(json.loads(row["contract"]))
        assert contract.open_questions == ("Which engine?",)
        assert contract.assumptions == ("Save format? -> markdown",)
    finally:
        OT.reset_store(None)
        M.reset_store(None)


def test_a_request_waits_for_the_question_it_needs_before_it_is_admitted(store):
    """V7-D38: no run is started while a blocking question is unanswered."""
    import agent_friday
    from friday import requirements as R
    from friday.toolsets import memory as M
    from friday.toolsets import objectives as OT

    text = "build the lighthouse keeper game, then create notes.md containing Dune and show me"
    R.ask("lighthouse-keeper", [R.Question("Which engine?", why="decides the build")],
          db=store)
    M.reset_store(store)
    OT.reset_store(store)
    try:
        assert agent_friday.admit_objective(text) is None
        assert store.objective_runs(limit=5) == []
    finally:
        OT.reset_store(None)
        M.reset_store(None)
