"""
Memory written before trust classes existed (Slice 3 review, 2026-09-27).

A trust class added to new rows says nothing about old ones. Rows the
read-material producers wrote before (a worker's handoff, a skill's facts, a
worker's bridge write, a channel resolved from a page) are marked read when
the column arrives; brain-ledger entries that do not say who wrote them are
not rendered as rules, because a worker could write the ledger before
V7-D34. And a read candidate is checked against every row it would retire,
not only the newest.
"""
from __future__ import annotations

import json
import sqlite3

import pytest

from friday import contracts as c
from friday import memory_promotion as MP
from friday.store import FACT, Store


def _old_database(path):
    """A database from before `memories.trust_class`, holding what the
    producers of the time wrote."""
    Store(path).close()
    conn = sqlite3.connect(path)
    conn.execute("ALTER TABLE memories DROP COLUMN trust_class")
    rows = [("push target", "git@x.example:o/r.git", "handoff:T-1"),
            ("parser", "friday/parse.py handles it", "skill:parsing"),
            ("deploy", "ship on fridays", "worker:friday/deploy.md:1"),
            ("youtube.channel.lofi", "UC123|Lofi Girl", "resolved from search: https://x"),
            ("editor", "editor is vim", "autolearn")]
    conn.executemany(
        "INSERT INTO memories (subject, value, kind, scope, source, confidence, created_at, "
        "superseded) VALUES (?,?,'FACT','project',?,0.8,'2026-01-01T00:00:00+00:00',0)", rows)
    conn.commit()
    conn.close()


def test_rows_the_read_producers_wrote_before_are_marked_read(tmp_path):
    db = tmp_path / "old.sqlite3"
    _old_database(db)
    store = Store(db)
    classes = {r["source"].split(":")[0].split(" ")[0]: r["trust_class"]
               for r in store._conn.execute("SELECT source, trust_class FROM memories")}
    assert classes == {"handoff": c.READ_MATERIAL, "skill": c.READ_MATERIAL,
                       "worker": c.READ_MATERIAL, "resolved": c.READ_MATERIAL,
                       "autolearn": ""}


def test_a_page_resolved_channel_is_remembered_as_read(tmp_path):
    from friday.toolsets import youtube as Y

    store = Store(tmp_path / "ada.sqlite3")
    Y.remember_channel(store, "lofi", Y.Channel(channel_id="UC123", title="Lofi Girl",
                                                source="handle_page"))
    (row,) = store.search_memories("Lofi Girl")
    assert row["trust_class"] == c.READ_MATERIAL


def test_read_material_cannot_retire_an_older_row_the_person_wrote(tmp_path):
    store = Store(tmp_path / "ada.sqlite3")
    store.remember("editor", "editor is vim", kind=FACT, source="he said", supersede=False,
                   confidence=0.5, trust_class=c.PERSON)
    store.remember("editor", "editor is nano", kind=FACT, source="page", supersede=False,
                   confidence=0.5, trust_class=c.READ_MATERIAL)
    # The read row is the newer one, so it is the row promote() looks at first.
    store._conn.execute("UPDATE memories SET created_at='2026-01-01T00:00:00+00:00' "
                        "WHERE value='editor is vim'")
    store._conn.execute("UPDATE memories SET created_at='2026-02-01T00:00:00+00:00' "
                        "WHERE value='editor is nano'")
    assert store.recall("editor")[0]["value"] == "editor is nano"
    decision = MP.promote(MP.Candidate(
        statement="editor is emacs", kind="project_fact", source="page", owner="friday",
        scope="project", confidence=0.99, evidence=["a page"], provenance=c.READ_MATERIAL),
        store=store)
    assert not decision.accepted
    live = [r["value"] for r in store.recall("editor")]
    assert "editor is vim" in live


def test_a_ledger_entry_that_does_not_say_who_wrote_it_is_not_a_rule(tmp_path, monkeypatch):
    from friday import memory_stack

    ledger = tmp_path / "brain_ledger.jsonl"
    monkeypatch.setenv("GBRAIN_LEDGER", str(ledger))
    ledger.write_text("\n".join(json.dumps(e) for e in (
        {"fact": "an entry from before origins", "provenance": "friday/x.py:1"},
        {"fact": "the person says tests come first", "provenance": "chat", "origin": "person"},
    )) + "\n", encoding="utf-8")
    rules = [r["rule"] for r in memory_stack.rules("tests", limit=50)["items"]]
    assert "an entry from before origins" not in rules
    assert "the person says tests come first" in rules
