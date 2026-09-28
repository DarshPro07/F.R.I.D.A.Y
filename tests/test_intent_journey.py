"""
From what the person says to an objective that can run (PRD v7 INTENT-001).

Two halves. What the person names - a file, a path, quoted words - reaches
the plan verbatim: "create notes.txt containing hello world" used to be cut
at the dot, and the plan invented `friday-notes.txt` holding "Created by
Friday for: create notes". And the journey as a whole: an idea read by
`requirements.understand`, planned by `planner_model.plan_objective`, and
compiled into a durable objective whose every task is a registered
capability with its required arguments filled.
"""
from __future__ import annotations

import pytest

from friday import contracts as c
from friday import objectives as O
from friday import planner as P
from friday.store import Store


# -- what the person named survives -------------------------------------------

def test_a_file_name_is_not_the_end_of_a_sentence():
    assert [p.strip() for p in P._split_requests("create notes.txt containing hello world")
            if p.strip()] == ["create notes.txt containing hello world"]


def test_quoted_words_are_never_split():
    pieces = [p for p in P._split_requests('write "call Anna. Then book the room" to todo.md')
              if p.strip()]
    assert pieces == ['write "call Anna. Then book the room" to todo.md']


def test_a_sentence_still_ends_at_its_full_stop():
    pieces = [p.strip() for p in P._split_requests("open the report. Then email it to Sam.")
              if p.strip(" .")]
    assert pieces[0] == "open the report" and "email it to Sam" in pieces[-1]


def _one_file_task(text):
    specs = [s for s in P.task_specs(P.plan_objective(text))
             if s["capability"].startswith("files_")]
    assert len(specs) == 1, P.task_specs(P.plan_objective(text))
    return specs[0]


def test_a_named_file_and_its_content_reach_the_plan_verbatim():
    spec = _one_file_task("create notes.txt containing hello world")
    assert spec["arguments"]["path"] == "notes.txt"
    assert spec["arguments"]["content"] == "hello world"


def test_quoted_content_is_written_word_for_word():
    spec = _one_file_task('write "call Anna. Then book the room" to todo.md')
    assert spec["arguments"]["path"] == "todo.md"
    assert spec["arguments"]["content"] == "call Anna. Then book the room"


def test_with_no_name_given_friday_names_its_own_file():
    spec = _one_file_task("create a note about the launch")
    assert spec["arguments"]["path"].startswith("friday-")


def test_a_named_file_without_content_gets_none_invented():
    spec = _one_file_task("create reading.md")
    assert spec["arguments"]["path"] == "reading.md"
    assert "content" not in spec["arguments"], spec["arguments"]


def test_a_noun_after_an_article_is_not_a_second_command():
    """ "a reading list" is a thing; split there, "list" planned a process
    listing nobody asked for."""
    for text in ("keep a reading list and create reading.md",
                 "keep a reading list: create reading.md containing Dune"):
        capabilities = [s["capability"] for s in P.task_specs(P.plan_objective(text))]
        assert "system_list_processes" not in capabilities, (text, capabilities)


def test_words_inside_quotes_do_not_steer_the_plan():
    """Quoted words are content. "play loud music in the browser" is what
    goes into the file, not a request to play anything."""
    spec = _one_file_task('write "play loud music in the browser" to todo.md')
    assert spec["arguments"] == {"path": "todo.md",
                                 "content": "play loud music in the browser"}


def test_a_purpose_stated_before_an_instruction_is_not_a_step():
    plan = P.plan_objective("I want to keep a reading list: create reading.md containing Dune")
    assert plan.purpose == ["I want to keep a reading list:"]
    assert [s["capability"] for s in P.task_specs(plan)] == ["files_create"]


def test_said_alone_a_purpose_is_still_the_request():
    plan = P.plan_objective("I want to keep a reading list")
    assert plan.purpose == [] and plan.goals


# -- the journey ---------------------------------------------------------------

def test_an_idea_becomes_an_objective_that_can_run(tmp_path):
    from friday import capabilities
    from friday import planner_model as SPM
    from friday import requirements as REQ

    store = Store(tmp_path / "ada.sqlite3")
    idea = "I want to keep a reading list: create reading.md containing Dune"
    understood = REQ.understand(idea, db=store)
    assert understood.subject or understood.known or understood.questions is not None

    plan = SPM.plan_objective(idea)
    assert P.validate(plan) == []
    specs = P.task_specs(plan)
    created = O.compile_objective(store, request=idea, tasks=specs,
                                  manifest=capabilities.as_dicts(), objective_summary=idea,
                                  provenance=c.PERSON, attended=True)
    tasks = store.objective_tasks(created["run_id"])
    assert tasks, "the objective has nothing to run"
    assert all(t["capability"] != "objective.unmapped" for t in tasks), tasks
    for task in tasks:
        required = [k for k, needed in P.parameters_of(task["capability"]).items() if needed]
        arguments = task["arguments"] if isinstance(task["arguments"], dict) else __import__("json").loads(task["arguments"])
        assert all(arguments.get(k) not in (None, "") for k in required), (task["capability"], arguments)
    (write,) = [t for t in tasks if t["capability"].startswith("files_")]
    arguments = write["arguments"] if isinstance(write["arguments"], dict) else __import__("json").loads(write["arguments"])
    assert arguments["path"] == "reading.md" and arguments["content"] == "Dune"


def test_the_idea_runs_and_the_file_holds_the_persons_words(tmp_path, monkeypatch):
    """The whole way: an idea, planned and compiled, driven by the objective
    executor through the real capability runtime, ends as a file on disk
    whose name and words are the ones the person gave."""
    import asyncio

    from friday import capabilities
    from friday import capability_runtime as CR
    from friday import fsjail
    from friday import planner_model as SPM
    from friday import policy as PO
    from friday.continuous import ContinuousTaskExecutor

    root = tmp_path / "work"
    root.mkdir()
    monkeypatch.chdir(root)
    monkeypatch.setattr(fsjail, "configured_roots", lambda: [root])
    # The file tools' own jail is a process singleton: this run's is set,
    # not inherited from whichever test built one first.
    from friday.toolsets import files as F
    F.reset_jail(fsjail.FileJail(roots=(root,)))
    store = Store(tmp_path / "ada.sqlite3")
    idea = "I want to keep a reading list: create reading.md containing Dune"
    specs = P.task_specs(SPM.plan_objective(idea))
    created = O.compile_objective(store, request=idea, tasks=specs,
                                  manifest=capabilities.as_dicts(), objective_summary=idea,
                                  provenance=c.PERSON, attended=True)
    run_id = created["run_id"]
    dispatch = CR.build_dispatch(engine=PO.PolicyEngine(autonomy=PO.FULL))

    async def drive():
        executor = ContinuousTaskExecutor(store, call_capability=dispatch)
        try:
            loop = asyncio.get_running_loop()
            deadline = loop.time() + 20
            while loop.time() < deadline:
                status = store.objective_run(run_id)["status"]
                if status in O.RUN_TERMINAL or status in O.RUN_WAITING_STATUSES:
                    return
                await executor.start(run_id)
                await asyncio.sleep(0.02)
        finally:
            executor.stop()

    try:
        asyncio.run(drive())
    finally:
        F.reset_jail(None)
    assert store.objective_run(run_id)["status"] == O.RUN_COMPLETED, store.objective_tasks(run_id)
    written = list(root.rglob("reading.md"))
    assert len(written) == 1 and written[0].read_text(encoding="utf-8") == "Dune"


# -- what the person forbids is never planned (V7-D39) -------------------------

@pytest.mark.parametrize("text, forbidden, kept", [
    ("create notes.txt and do not delete anything", ("delete", "recycle"), "files_create"),
    ("tidy my downloads but never delete files", ("delete", "recycle"), None),
    ("create notes.txt, and don't remove anything", ("delete", "recycle", "remove"), "files_create"),
])
def test_a_prohibition_is_never_planned_as_the_thing_it_forbids(text, forbidden, kept):
    plan = P.plan_objective(text)
    capabilities = [s["capability"] for s in P.task_specs(plan)]
    assert not any(word in cap for cap in capabilities for word in forbidden), capabilities
    assert plan.safety, f"the prohibition was not kept as a safety rule: {plan}"
    if kept:
        assert kept in capabilities, f"the request itself was lost: {capabilities}"


def test_listed_content_is_kept_whole():
    spec = _one_file_task("create shopping.txt containing apples, pears and plums")
    assert spec["arguments"]["content"] == "apples, pears and plums"


def test_unquoted_content_ends_where_its_clause_does():
    spec = _one_file_task("create reading.md containing Dune, keep your updates short")
    assert spec["arguments"]["content"] == "Dune"
