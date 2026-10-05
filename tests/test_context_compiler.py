"""
The objective engine hands work to the context compiler, never straight to a
model or a worker (PRD v7 CTX-003, owner ruling 2026-10-03).

CriticalFactSet is an input constraint on compaction: the compiler may shorten
representation (whole droppable sections, quote markers) but never silently
alters or omits a critical fact, an exact literal, an active blocker or the
authority a run holds. A package that cannot keep them fails loudly.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

from friday import capabilities
from friday import compaction as K
from friday import compression
from friday import contracts as c
from friday import development
from friday import model_gateway as mg
from friday import objectives as O
from friday.store import Store

ROOT = Path(__file__).resolve().parents[1]


def _objective(tmp_path, *, literal="Q3 report.md"):
    store = Store(tmp_path / "ada.sqlite3")
    contract = c.ObjectiveContract(
        goal=f"write the Q3 summary to '{literal}' and tidy the old drafts",
        success_criteria=("the old drafts are gone and the summary opens in Word",),
        hard_constraints=("keep the numbers exactly as the finance sheet has them",),
        exact_literals=(literal,))
    created = O.compile_objective(
        store, request=contract.goal, objective_summary=contract.goal,
        tasks=[{"id": "t1", "capability": "files_create",
                "arguments": {"path": "Q3 report.md", "content": literal}},
               {"id": "t2", "capability": "files_delete", "arguments": {"path": "draft1.txt"}}],
        manifest=capabilities.as_dicts(), provenance=c.PERSON, attended=True,
        contract=contract)
    run_id = created["run_id"]
    first, second = store.objective_tasks(run_id)
    store.update_objective_task(first["task_id"], status=O.TaskStatus.SUCCEEDED,
                                evidence="wrote Q3 report.md (412 bytes)")
    store.update_objective_task(second["task_id"], status=O.TaskStatus.BLOCKED,
                                evidence="waiting on the board's sign-off to delete")
    return store, run_id


def _content(working_set) -> str:
    return "\n".join(m["content"] for m in working_set.messages)


def test_a_critical_fact_a_literal_and_an_active_blocker_survive_a_tight_budget(tmp_path):
    store, run_id = _objective(tmp_path)
    facts = K.for_objective(store, run_id)
    floor = mg.compile_working_set(user="tidy the drafts", facts=facts).total_tokens
    crowd = dict(state=("a long note " * 40,), memory="the boss likes tables " * 30,
                 code_refs=("src/report.py " * 30,), history=[("user", "earlier turn " * 30)])
    roomy = mg.compile_working_set(user="tidy the drafts", facts=facts, **crowd)
    assert roomy.dropped == () and roomy.total_tokens > floor

    tight = mg.compile_working_set(user="tidy the drafts", facts=facts, budget_tokens=floor, **crowd)
    # what could be shortened was, and the package says what it shed
    assert set(tight.dropped) == {"state", "memory", "code", "history"}
    assert tight.telemetry()["dropped"] == list(tight.dropped)
    # the constraint, the literal exactly, and the blocker are all still there
    text = _content(tight)
    assert "MUST: keep the numbers exactly as the finance sheet has them" in text
    assert "EXACT: Q3 report.md" in text
    # what done means is a fact too: the worker is held to it, not to a paraphrase
    assert "OBJECTIVE (done when): the old drafts are gone and the summary opens in Word" in text
    assert any("draft1.txt" in line and "sign-off" in line for line in facts.avoid)
    assert K.missing(text, facts) == []
    assert tight.facts_kept == len(facts.lines())
    assert "recorded" in tight.sections and "recorded" not in tight.dropped


def test_every_dropped_section_has_a_record_and_comes_back_exactly(tmp_path, monkeypatch):
    monkeypatch.setenv("FRIDAY_ARTIFACT_VAULT", str(tmp_path / "vault"))
    store, run_id = _objective(tmp_path)
    facts = K.for_objective(store, run_id)
    floor = mg.compile_working_set(user="tidy the drafts", facts=facts).total_tokens
    crowd = dict(state=("a long note " * 40,), memory="the boss likes tables " * 30,
                 code_refs=("src/report.py " * 30,), history=[("user", "earlier turn " * 30)])
    roomy = mg.compile_working_set(user="tidy the drafts", facts=facts, **crowd)
    tight = mg.compile_working_set(user="tidy the drafts", facts=facts, budget_tokens=floor, **crowd)
    assert roomy.transformations == () and "transformations" not in roomy.telemetry()
    moves = {t["section"]: t for t in tight.transformations}
    assert set(moves) == set(tight.dropped) == {"state", "memory", "code", "history"}
    assert tight.telemetry()["transformations"] == [dict(t) for t in tight.transformations]
    said = {"state": "a long note", "memory": "the boss likes tables",
            "code": "src/report.py", "history": "earlier turn"}
    for name, record in moves.items():
        assert record["action"] == compression.DROP and record["refusal"] == ""
        original = compression.recover(record["recovery"])
        assert compression.sha256(original) == record["source_sha256"]
        assert said[name] in original.decode() and said[name] not in _content(tight)
    assert K.missing(_content(tight), facts) == []


def test_a_package_that_cannot_fit_the_facts_fails_rather_than_dropping_them(tmp_path):
    store, run_id = _objective(tmp_path)
    facts = K.for_objective(store, run_id)
    with pytest.raises(mg.ContextOverBudget) as refused:
        mg.compile_working_set(user="tidy the drafts", facts=facts, budget_tokens=20)
    assert "recorded" in str(refused.value) and "pinned" in str(refused.value)
    request = K.ContextRequest(objective_id=run_id, work="tidy the drafts", budget_tokens=20)
    with pytest.raises(mg.ContextOverBudget):
        mg.compile_request(store, request)


def test_a_package_that_lost_a_fact_is_rejected_whatever_dropped_it(tmp_path, monkeypatch):
    """The mutation: a compile that loses the literal on the way out."""
    store, run_id = _objective(tmp_path)
    facts = K.for_objective(store, run_id)
    quoted = mg._quoted
    monkeypatch.setattr(mg, "_quoted", lambda header, items: quoted(
        header, [i for i in items if not str(i).startswith("EXACT:")]))
    with pytest.raises(K.LossyCompaction) as refused:
        mg.compile_working_set(user="tidy the drafts", facts=facts)
    assert refused.value.missing == ["EXACT: Q3 report.md"]


def test_a_package_that_lost_what_done_means_is_rejected(tmp_path, monkeypatch):
    store, run_id = _objective(tmp_path)
    facts = K.for_objective(store, run_id)
    quoted = mg._quoted
    monkeypatch.setattr(mg, "_quoted", lambda header, items: quoted(
        header, [i for i in items if "(done when)" not in str(i)]))
    with pytest.raises(K.LossyCompaction) as refused:
        mg.compile_working_set(user="tidy the drafts", facts=facts)
    assert refused.value.missing == [
        "OBJECTIVE (done when): the old drafts are gone and the summary opens in Word"]


def test_a_literal_that_spans_lines_is_kept_exactly(tmp_path):
    literal = "line one\nline two"
    store, run_id = _objective(tmp_path, literal=literal)
    compiled = mg.compile_request(store, K.ContextRequest(objective_id=run_id, work="go"))
    assert literal in compiled.facts.render()
    assert K.missing(_content(compiled.working_set).replace("\n> ", "\n"), compiled.facts) == []


def test_facts_from_read_material_travel_as_data_and_are_still_kept(tmp_path):
    store, run_id = _objective(tmp_path)
    facts = K.for_objective(store, run_id)
    ws = mg.compile_working_set(user="tidy the drafts", facts=facts, facts_bind=False)
    assert "pinned" not in ws.sections and "recorded" in ws.sections
    assert K.missing(_content(ws), facts) == []


def test_a_context_request_names_its_objective_and_its_work():
    with pytest.raises(c.ContractError):
        K.ContextRequest(objective_id=" ", work="go")
    with pytest.raises(c.ContractError):
        K.ContextRequest(objective_id="run-1", work="")
    with pytest.raises(c.ContractError):
        K.ContextRequest(objective_id="run-1", work="go", budget_tokens=-1)


def test_development_work_is_staffed_only_after_the_compiler_has_kept_the_facts(
        tmp_path, monkeypatch):
    store, run_id = _objective(tmp_path)
    staffed = []
    monkeypatch.setattr(development, "for_goal",
                        lambda goal, root, **kw: staffed.append((goal, root, kw)) or "run")

    too_big = K.ContextRequest(objective_id=run_id, work="tidy", root=str(tmp_path),
                               budget_tokens=20)
    with pytest.raises(mg.ContextOverBudget):
        development.for_request(store, too_big)
    assert staffed == []

    with pytest.raises(LookupError):
        development.for_request(store, K.ContextRequest(
            objective_id="no-such-run", work="tidy", root=str(tmp_path)))
    with pytest.raises(c.ContractError):
        development.for_request(store, K.ContextRequest(objective_id=run_id, work="tidy"))
    assert staffed == []

    fits = K.ContextRequest(objective_id=run_id, work="tidy", root=str(tmp_path),
                            project="p", strategy_hint="reduce", budget_tokens=4000)
    assert development.for_request(store, fits) == "run"
    ((goal, root, kwargs),) = staffed
    assert (goal, root) == ("tidy", str(tmp_path))
    assert kwargs == {"project": "p", "strategy_hint": "reduce", "store": store,
                      "objective_id": run_id}


def _production_calls(names: set[str]) -> set[tuple[str, str]]:
    """(file, enclosing function) of every production call to one of `names`."""
    found: set[tuple[str, str]] = set()
    files = [*sorted((ROOT / "friday").rglob("*.py")),
             *(ROOT / n for n in ("agent_friday.py", "server.py", "main.py", "start.py"))]
    for path in files:
        if not path.exists():
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))

        def visit(node, scope):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                scope = node.name
            if isinstance(node, ast.Call):
                func = node.func
                called = func.id if isinstance(func, ast.Name) else getattr(func, "attr", "")
                if called in names:
                    found.add((path.relative_to(ROOT).as_posix(), scope))
            for child in ast.iter_child_nodes(node):
                visit(child, scope)

        visit(tree, "")
    return found


def test_the_objective_path_reaches_a_development_worker_only_through_the_compiler():
    # for_goal is called from one place, and that place compiles first
    assert _production_calls({"for_goal"}) == {("friday/development.py", "for_request")}
    # and development.for_request is called from the one capability that starts
    # a run (semantics.for_request is another function with the same name)
    callers = [path.relative_to(ROOT).as_posix()
               for path in sorted((ROOT / "friday").rglob("*.py"))
               if "development.for_request(" in path.read_text(encoding="utf-8")]
    assert callers == ["friday/toolsets/development_runs.py"]
    # and a DevelopmentRun is built nowhere else
    assert _production_calls({"DevelopmentRun"}) == {("friday/development.py", "for_goal")}
    source = (ROOT / "friday/development.py").read_text(encoding="utf-8")
    (node,) = [n for n in ast.parse(source).body
               if isinstance(n, ast.FunctionDef) and n.name == "for_request"]
    code = "\n".join(ast.get_source_segment(source, stmt) for stmt in node.body[1:])
    assert code.index("mg.compile_request(") < code.index("return for_goal(")


def test_the_objective_engine_does_not_build_prompts_or_reach_a_worker_itself():
    engine = "friday/continuous.py"
    built = {"compile_working_set", "TaskBundle", "for_goal", "for_request"}
    assert [hit for hit in _production_calls(built) if hit[0] == engine] == []
    tree = ast.parse((ROOT / engine).read_text(encoding="utf-8"))
    imported = {alias.name for n in ast.walk(tree) if isinstance(n, ast.Import)
                for alias in n.names}
    imported |= {f"{n.module}.{alias.name}" for n in ast.walk(tree)
                 if isinstance(n, ast.ImportFrom) for alias in n.names}
    assert not [name for name in imported
                if "development" in name or "friday.executors" in name]
