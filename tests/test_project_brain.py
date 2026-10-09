"""
BRAIN-001: the human-inspectable Project Brain.

Thirteen structured record kinds live in one `project_brain_records` table of
the existing Store and are exported as Markdown under STATE_ROOT/brain/<project>/.
These tests drive the real Store (':memory:') and a tmp export root; none of
them touch data/ada.sqlite3.
"""

from __future__ import annotations

import re
import sqlite3

import pytest

from friday.project_brain import KINDS, BrainRecordError, ProjectBrain
from friday.store import SCHEMA, Store

PROJECT = "friday-demo"

# One valid body per kind: exactly the required fields plus a harmless extra.
BODIES: dict[str, dict] = {
    "architecture": {"title": "Store seam", "summary": "One SQLite table in Store."},
    "requirements": {"statement": "The brain survives a restart."},
    "decisions": {"decision": "Use the store, not loose files.",
                  "rationale": "store.py owns durable project state."},
    "assumptions": {"statement": "Single owner, single machine."},
    "research": {"question": "Is there a prior art?", "finding": "No unified store."},
    "failures": {"fingerprint": "ab12cd34", "summary": "Retry loop on the same error."},
    "experiments": {"hypothesis": "Paging fixes truncation.", "outcome": "Confirmed."},
    "objectives": {"goal": "Ship the brain export."},
    "incidents": {"summary": "Index lock left behind.", "severity": "low"},
    "artifacts": {"title": "Design note", "path": "docs/design.md"},
    "handoffs": {"summary": "Stopped after the schema.",
                 "next_steps": ["write the export", "run the suite"]},
    "procedures": {"title": "Restart Friday",
                   "steps": ["stop the agent", "run restart_friday.py"]},
    "entities": {"name": "Hermes", "entity_type": "service"},
}


@pytest.fixture()
def store():
    s = Store(":memory:")
    yield s
    s.close()


@pytest.fixture()
def brain(store, tmp_path):
    return ProjectBrain(store, state_root=tmp_path)


def test_every_kind_has_a_fixture_body():
    # Guards the parametrised tests below: a fourteenth kind with no body here
    # would otherwise silently go untested.
    assert set(BODIES) == set(KINDS)
    assert len(KINDS) == 13


def test_schema_check_constraint_lists_exactly_the_python_kinds():
    # The CHECK in SQL and KINDS in Python are two statements of one list.
    match = re.search(
        r"CREATE TABLE IF NOT EXISTS project_brain_records.*?kind\s+TEXT NOT NULL"
        r"\s+CHECK\s*\(kind IN \((.*?)\)\)", SCHEMA, re.S)
    assert match, "project_brain_records.kind has no CHECK (kind IN (...))"
    in_sql = set(re.findall(r"'([a-z_]+)'", match.group(1)))
    assert in_sql == set(KINDS)


@pytest.mark.parametrize("kind", KINDS)
def test_round_trip_per_record_type(brain, kind):
    body = BODIES[kind]
    written = brain.add(PROJECT, kind, "k1", body, origin="person")
    read = brain.get(PROJECT, kind, "k1")
    assert read is not None
    assert read.id == written.id
    assert (read.project, read.kind, read.key) == (PROJECT, kind, "k1")
    assert read.body == body
    assert read.state == "active"
    assert read.origin == "person"
    assert read.supersedes is None
    assert [r.id for r in brain.list(PROJECT, kind)] == [written.id]


def test_round_trip_survives_reopening_the_database(tmp_path):
    path = tmp_path / "brain.sqlite3"
    first = Store(path)
    ProjectBrain(first, state_root=tmp_path).add(
        PROJECT, "decisions", "d1", BODIES["decisions"], origin="person")
    first.close()
    second = Store(path)
    try:
        got = ProjectBrain(second, state_root=tmp_path).get(PROJECT, "decisions", "d1")
        assert got is not None and got.body == BODIES["decisions"]
    finally:
        second.close()


@pytest.mark.parametrize("kind,key,body,origin", [
    ("nonsense", "k1", {"a": "b"}, "person"),                      # unknown kind
    ("decisions", "k1", {"rationale": "no decision field"}, "person"),
    ("decisions", "k1", {"decision": "   "}, "person"),            # blank string
    ("decisions", "k1", {"decision": 7}, "person"),                # wrong type
    ("procedures", "k1", {"title": "t", "steps": "not a list"}, "person"),
    ("procedures", "k1", {"title": "t", "steps": []}, "person"),
    ("procedures", "k1", {"title": "t", "steps": ["ok", ""]}, "person"),
    ("decisions", "k1", ["not", "an", "object"], "person"),        # body not a dict
    ("decisions", "", {"decision": "x"}, "person"),                # empty key
    ("decisions", "bad key\n## injected", {"decision": "x"}, "person"),
    ("decisions", "k" * 200, {"decision": "x"}, "person"),         # key too long
    ("decisions", "k1", {"decision": "x" * 40_000}, "person"),     # body too large
    ("decisions", "k1", {"decision": "x"}, ""),                    # no origin
    ("decisions", "k1", {"decision": object()}, "person"),         # not JSON
])
def test_unknown_kind_and_malformed_body_rejected(brain, kind, key, body, origin):
    with pytest.raises(BrainRecordError):
        brain.add(PROJECT, kind, key, body, origin=origin)
    assert brain.list(PROJECT) == []


def test_store_refuses_an_unknown_kind_below_the_validation_layer(store):
    with pytest.raises(sqlite3.IntegrityError):
        store.brain_insert(PROJECT, "nonsense", "k1", "{}", origin="person")


def test_duplicate_active_key_is_refused_and_points_at_supersede(brain):
    brain.add(PROJECT, "decisions", "d1", BODIES["decisions"], origin="person")
    with pytest.raises(BrainRecordError, match="supersede"):
        brain.add(PROJECT, "decisions", "d1", {"decision": "other"}, origin="person")
    # Same key under another kind or project is a different record.
    brain.add(PROJECT, "assumptions", "d1", BODIES["assumptions"], origin="person")
    brain.add("other-project", "decisions", "d1", BODIES["decisions"], origin="person")


def test_supersede_keeps_history(brain):
    first = brain.add(PROJECT, "decisions", "d1", {"decision": "files"}, origin="person")
    second = brain.supersede(PROJECT, "decisions", "d1",
                             {"decision": "one SQLite table"}, origin="friday")

    assert second.id != first.id
    assert second.supersedes == first.id
    assert second.state == "active" and second.body == {"decision": "one SQLite table"}

    live = brain.get(PROJECT, "decisions", "d1")
    assert live is not None and live.id == second.id
    assert [r.id for r in brain.list(PROJECT, "decisions")] == [second.id]

    history = brain.history(PROJECT, "decisions", "d1")
    assert [r.id for r in history] == [first.id, second.id]
    assert [r.state for r in history] == ["superseded", "active"]
    assert history[0].body == {"decision": "files"}          # old body kept intact
    assert [r.id for r in brain.list(PROJECT, "decisions",
                                     include_superseded=True)] == [first.id, second.id]


def test_supersede_without_an_active_record_is_refused(brain):
    with pytest.raises(BrainRecordError, match="no active"):
        brain.supersede(PROJECT, "decisions", "missing", {"decision": "x"},
                        origin="person")


def test_supersede_validates_the_new_body_and_leaves_the_old_record_live(brain):
    first = brain.add(PROJECT, "decisions", "d1", {"decision": "files"}, origin="person")
    with pytest.raises(BrainRecordError):
        brain.supersede(PROJECT, "decisions", "d1", {"rationale": "no decision"},
                        origin="person")
    still = brain.get(PROJECT, "decisions", "d1")
    assert still is not None and still.id == first.id and still.state == "active"


def test_ref_must_name_an_existing_store_row(brain, store):
    decision_id = store.record_decision(PROJECT, "use sqlite", source="person")
    ok = brain.add(PROJECT, "decisions", "d1", BODIES["decisions"],
                   origin="person", ref=f"project_decisions:{decision_id}")
    assert ok.ref == f"project_decisions:{decision_id}"
    for bad in ("project_decisions:99999", "no_such_table:1",
                "project_decisions", "project_decisions:1; DROP TABLE x"):
        with pytest.raises(BrainRecordError):
            brain.add(PROJECT, "decisions", "d2", BODIES["decisions"],
                      origin="person", ref=bad)


def test_secret_shaped_content_is_refused_on_write(brain):
    with pytest.raises(BrainRecordError, match="secret"):
        brain.add(PROJECT, "research", "r1",
                  {"question": "q", "finding": "api_key = sk-abcdefghijklmnopqrstuvwx"},
                  origin="person")
    assert brain.list(PROJECT) == []


def test_export_markdown_is_human_readable_and_lists_every_kind(brain, tmp_path):
    for kind in KINDS:
        brain.add(PROJECT, kind, "k1", BODIES[kind], origin="person")
    brain.supersede(PROJECT, "decisions", "k1", {"decision": "revised"}, origin="friday")

    written = brain.export_markdown(PROJECT)

    root = tmp_path / "brain" / PROJECT
    assert written["index"] == root / "index.md"
    for kind in KINDS:
        assert written[kind] == root / f"{kind}.md"
        assert written[kind].is_file()
    index = written["index"].read_text(encoding="utf-8")
    for kind in KINDS:
        assert f"{kind}.md" in index
    assert index.startswith(f"# Project Brain: {PROJECT}")

    decisions = written["decisions"].read_text(encoding="utf-8")
    assert "## k1" in decisions
    assert "revised" in decisions
    assert "Superseded" in decisions and "files" not in decisions.split("Superseded")[0]
    procedures = written["procedures"].read_text(encoding="utf-8")
    assert "- stop the agent" in procedures and "- run restart_friday.py" in procedures
    entities = written["entities"].read_text(encoding="utf-8")
    assert "Hermes" in entities and "service" in entities
    assert not any(line.startswith("{") for line in decisions.splitlines())  # not raw JSON


def test_export_is_deterministic_and_covers_empty_kinds(brain):
    first = brain.export_markdown(PROJECT)
    snapshot = {k: p.read_text(encoding="utf-8") for k, p in first.items()}
    assert "No records" in snapshot["incidents"]
    second = brain.export_markdown(PROJECT)
    assert {k: p.read_text(encoding="utf-8") for k, p in second.items()} == snapshot


def test_export_neutralises_markdown_structure_in_values(brain):
    brain.add(PROJECT, "research", "r1",
              {"question": "q\n\n## forged heading\n- forged bullet", "finding": "f"},
              origin="person")
    text = brain.export_markdown(PROJECT)["research"].read_text(encoding="utf-8")
    headings = [ln for ln in text.splitlines() if ln.startswith("## ")]
    assert headings == ["## r1"]


@pytest.mark.parametrize("name", ["../escape", "..", "a/b", "C:\\evil", "x" * 190,
                                  "has space", "con", "trailing."])
def test_export_path_cannot_leave_the_brain_root(store, tmp_path, name):
    brain = ProjectBrain(store, state_root=tmp_path / "state")
    brain.add(name, "decisions", "d1", BODIES["decisions"], origin="person")
    written = brain.export_markdown(name)
    brain_root = (tmp_path / "state" / "brain").resolve()
    for path in written.values():
        assert path.resolve().is_relative_to(brain_root)
        assert path.resolve().parent.parent == brain_root


def test_two_project_names_that_slug_alike_do_not_share_a_directory(store, tmp_path):
    brain = ProjectBrain(store, state_root=tmp_path)
    brain.add("a b", "decisions", "d1", {"decision": "one"}, origin="person")
    brain.add("a_b", "decisions", "d1", {"decision": "two"}, origin="person")
    one = brain.export_markdown("a b")["decisions"]
    two = brain.export_markdown("a_b")["decisions"]
    assert one.parent != two.parent
    assert "one" in one.read_text(encoding="utf-8")
    assert "two" in two.read_text(encoding="utf-8")


def test_export_redacts_secret_shapes(store, brain):
    secret = "sk-abcdefghijklmnopqrstuvwx"
    # Written below the validation layer, as a row from an older or foreign
    # writer would be: the export gate must hold on its own.
    store.brain_insert(PROJECT, "research", "leaky",
                       '{"question": "q", "finding": "api_key = %s"}' % secret,
                       origin="person")
    store.brain_insert(PROJECT, "decisions", "fine", '{"decision": "keep going"}',
                       origin="person")

    written = brain.export_markdown(PROJECT)

    for path in written.values():
        assert secret not in path.read_text(encoding="utf-8")
    research = written["research"].read_text(encoding="utf-8")
    assert "## leaky" in research and "withheld" in research
    assert "keep going" in written["decisions"].read_text(encoding="utf-8")


def test_export_survives_an_unreadable_row_and_get_names_it(store, brain):
    bad_id = store.brain_insert(PROJECT, "decisions", "broken", "{not json",
                                origin="person")
    store.brain_insert(PROJECT, "decisions", "fine", '{"decision": "keep going"}',
                       origin="person")

    text = brain.export_markdown(PROJECT)["decisions"].read_text(encoding="utf-8")

    assert "unreadable" in text and "keep going" in text
    with pytest.raises(BrainRecordError, match=str(bad_id)):
        brain.list(PROJECT, "decisions")
