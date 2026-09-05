"""
Shared scoped memory (PRD v3.1 FR-015..FR-020).

    FR-015  one logical memory service: a fact committed by a worker
            (Hermes outcome path, run_id attribution) is retrievable by
            another reader within scope
    FR-016  memory classes with lifecycle (working/session die with their
            objective/session; durable classes survive)
    FR-017  scoped retrieval: a project's memory never leaks into another
            project's context, at the store AND at the prompt compiler
    FR-018  provenance: source, source_ref, timestamps, confidence,
            supersession links, open contradictions
    FR-019  correction supersedes (old value never ranks current), export
    FR-020  the context compiler reports its token budget and what it
            injected, and never dumps the store
"""
from __future__ import annotations

import pytest

from friday import contracts as c
from friday.store import FACT, INFERENCE, MEMORY_TYPES, Store
from friday.toolsets import memory as M


@pytest.fixture
def store(tmp_path, monkeypatch):
    s = Store(tmp_path / "mem.sqlite3")
    M.reset_store(s)
    yield s
    M.reset_store(None)


# -- FR-016 classes and lifecycle ------------------------------------------


def test_every_prd_memory_class_is_storable_with_a_lifecycle(store):
    ids = {}
    for t in MEMORY_TYPES:
        ids[t] = store.remember(f"class.{t}", "v", kind=FACT, source="test",
                                memory_type=t, run_id="RUN-1")
    rows = {r["memory_type"]: r for r in store.export_memories()}
    assert set(rows) == set(MEMORY_TYPES)
    assert rows["working"]["retention_policy"] == "objective"
    assert rows["session"]["retention_policy"] == "session"
    assert rows["semantic"]["retention_policy"] == "durable"
    with pytest.raises(ValueError):
        store.remember("x", "y", kind=FACT, source="t", memory_type="vibes")


def test_working_memory_dies_with_its_objective_and_durable_memory_does_not(store):
    store.remember("scratch", "half-done plan", kind=INFERENCE, source="planner",
                   memory_type="working", run_id="RUN-1", confidence=0.4)
    store.remember("scratch2", "other run", kind=INFERENCE, source="planner",
                   memory_type="working", run_id="RUN-2", confidence=0.4)
    store.remember("backend", "strapi v5", kind=FACT, source="user", run_id="RUN-1")
    retired = store.expire_memories(retention_policy="objective", run_id="RUN-1")
    assert retired == 1
    current = {r["subject"] for r in store.export_memories()}
    assert current == {"scratch2", "backend"}
    # Retired, not deleted: the audit trail remains.
    assert any(r["subject"] == "scratch" for r in store.export_memories(include_superseded=True))


# -- FR-017 scoped retrieval ----------------------------------------------


def test_a_projects_memory_never_leaks_into_another_project(store):
    store.remember("backend", "strapi v5", kind=FACT, source="user",
                   project_scope="shopfront", scope="preferences")
    store.remember("backend", "django", kind=FACT, source="user",
                   project_scope="internal-tools", scope="preferences")
    store.remember("editor", "vscode", kind=FACT, source="user", scope="preferences")

    shop = {r["value"] for r in store.recall_scoped(project_scope="shopfront")}
    tools = {r["value"] for r in store.recall_scoped(project_scope="internal-tools")}
    nowhere = {r["value"] for r in store.recall_scoped(project_scope="")}
    assert shop == {"strapi v5", "vscode"}
    assert tools == {"django", "vscode"}
    assert nowhere == {"vscode"}                    # global only
    # Same subject in two projects: each project keeps its own current value.
    assert {r["project_scope"] for r in store.recall("backend")} == {"shopfront", "internal-tools"}


def test_the_context_compiler_excludes_other_projects_before_the_prompt(store, monkeypatch):
    """FR-017 acceptance at the seam that matters: memory_stack.aggregate
    is what every brain and every Hermes bundle reads."""
    from friday import memory_stack, ui_server
    monkeypatch.setattr(ui_server, "_db_path", lambda: str(store.path))
    store.remember("stack", "strapi v5 backend", kind=FACT, source="user",
                   project_scope="shopfront", scope="preferences")
    store.remember("stack", "django backend", kind=FACT, source="user",
                   project_scope="internal-tools", scope="preferences")
    store.remember("greeting", "call me boss", kind=FACT, source="user", scope="preferences")
    shop = memory_stack.aggregate("backend stack", budget_tokens=400,
                                  include_episodes=False, project_scope="shopfront")
    assert "strapi" in shop["prompt"] and "django" not in shop["prompt"]
    assert "boss" in shop["prompt"]
    nothing = memory_stack.aggregate("backend stack", budget_tokens=400,
                                     include_episodes=False)
    assert "strapi" not in nothing["prompt"] and "django" not in nothing["prompt"]


# -- FR-018 provenance / FR-019 correction ----------------------------------


def test_correction_supersedes_with_a_link_and_the_old_value_is_not_current(store):
    old = store.remember("backend", "strapi v4", kind=FACT, source="user said so",
                         source_ref="msg:41")
    new = store.remember("backend", "strapi v5", kind=FACT, source="user corrected it",
                         source_ref="msg:97")
    p_new = store.memory_provenance(new)
    p_old = store.memory_provenance(old)
    assert p_new["current"] and p_new["supersedes"]["id"] == old
    assert p_new["source_ref"] == "msg:97" and p_new["source"] == "user corrected it"
    assert not p_old["current"] and p_old["superseded_by"]["id"] == new
    assert [r["value"] for r in store.recall("backend")] == ["strapi v5"]
    assert store.recall_scoped(subject="backend")[0]["value"] == "strapi v5"
    # UC-10: the old value is history, reachable only when asked for.
    assert {r["value"] for r in store.recall("backend", include_superseded=True)} == {
        "strapi v4", "strapi v5"}


def test_provenance_surfaces_open_contradictions(store):
    mid = store.remember("timezone", "IST", kind=FACT, source="user")
    store.add_contradiction(subject="timezone", existing_value="IST", existing_kind=FACT,
                            new_value="UTC", new_kind=INFERENCE)
    p = store.memory_provenance(mid)
    assert p["current"] and len(p["open_contradictions"]) == 1
    assert p["open_contradictions"][0]["new_value"] == "UTC"


def test_retrieval_stamps_last_retrieved_at(store):
    mid = store.remember("editor", "vscode", kind=FACT, source="user")
    assert store.memory_provenance(mid)["last_retrieved_at"] is None
    store.recall_scoped(subject="editor")
    assert store.memory_provenance(mid)["last_retrieved_at"]


# -- FR-015 one logical service across workers -----------------------------


def test_a_fact_committed_by_one_worker_is_readable_by_another_in_scope(store):
    """The Hermes outcome path writes through the same store the voice
    brain reads (hermes_bridge._write_outcome -> Store.remember). Here the
    worker is any caller with a run id; the reader is the toolset."""
    store.remember("deploy.target", "vercel", kind=FACT, source="hermes:work-run-7",
                   run_id="work-run-7", memory_type="project", project_scope="shopfront",
                   source_ref="hermes_work_runs/work-run-7")
    run = c.Run.create("what do we deploy to", capability="memory")
    out = M.memory_recall(run, "deploy.target")
    assert out.status == c.SUCCEEDED
    assert out.output["memories"][0]["value"] == "vercel"
    prov = store.memory_provenance(out.output["memories"][0].get("id") or 1)
    assert prov["source"].startswith("hermes:")
    assert prov["source_ref"] == "hermes_work_runs/work-run-7"


# -- the toolset faces ----------------------------------------------------


def test_toolset_remember_provenance_and_export_round_trip(store):
    run = c.Run.create("remember", capability="memory")
    out = M.memory_remember(run, "backend", "strapi v5", kind=FACT, source="user",
                            memory_type="project", project="shopfront", source_ref="msg:97")
    assert out.status == c.SUCCEEDED, out.error
    mid = out.output["id"]
    assert out.output["memory_type"] == "project" and out.output["project_scope"] == "shopfront"
    prov = M.memory_provenance(c.Run.create("why", capability="memory"), mid)
    assert prov.output["current"] and prov.output["source_ref"] == "msg:97"
    exp = M.memory_export(c.Run.create("export", capability="memory"), "shopfront")
    assert exp.output["count"] == 1 and exp.output["memories"][0]["subject"] == "backend"
    other = M.memory_export(c.Run.create("export", capability="memory"), "elsewhere")
    assert other.output["count"] == 0
    missing = M.memory_provenance(c.Run.create("why", capability="memory"), 9999)
    assert missing.status == c.FAILED


# -- FR-020 context telemetry ----------------------------------------------


def test_context_compiler_reports_budget_and_never_dumps_the_store(store, monkeypatch):
    from friday import memory_stack, ui_server
    monkeypatch.setattr(ui_server, "_db_path", lambda: str(store.path))
    for i in range(300):
        store.remember(f"pref.{i}", f"value number {i} " * 5, kind=FACT, source="user",
                       scope="preferences")
    out = memory_stack.aggregate("anything", budget_tokens=300, include_episodes=False)
    assert out["budget_tokens"] == 300
    assert out["tokens_used"] <= 300
    # 300 preferences exist; a bounded compiler injects a shortlist, never
    # the store. (The rules tier reads AGENTS.md and may add a line or two.)
    assert out["injected"]["preferences"] <= 12
    assert sum(out["injected"].values()) < 30


def test_the_graph_is_rebuilt_only_when_memory_changes(tmp_path, monkeypatch):
    """A-051: `memory_graph.build()` reads EVERY memory row and allocates a
    node dict per subject (10,480 at 10k memories), and `relations()` called
    it on every Hermes delegation. Cached against a validity token so a
    read-only caller (the UI polling, a delegation that writes nothing) pays
    once - and so that a write is still seen immediately."""
    import sqlite3
    from friday import memory_graph as G
    from friday.store import Store

    db = tmp_path / "graph.sqlite3"
    monkeypatch.setenv("ADA_DB", str(db))
    store = Store(db)
    store.remember("user.name", "Darsh", kind="FACT", source="test")
    G.invalidate()

    first = G.build()
    assert G.build() is first, "a graph with no memory change was rebuilt"

    store.remember("user.city", "Pune", kind="FACT", source="test")
    second = G.build()
    assert second is not first, "a new memory did not invalidate the graph"
    assert second["stats"]["nodes"] > first["stats"]["nodes"]

    # `forget` flips `superseded` and inserts NOTHING: row count and max id
    # are unchanged, so a token built only from those would serve a graph
    # still showing a fact the owner asked Friday to drop.
    assert store.forget("user.city") == 1
    third = G.build()
    assert third is not second, "a forgotten fact was still served from cache"
    assert not any(n.get("value") == "Pune" for n in third["nodes"])
    store.close()
