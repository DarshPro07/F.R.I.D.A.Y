"""The amnesia regressions: a new session must not start from nothing.

Each test here failed before 2026-09-01. `messages` and `conversations` were
real tables with zero writers, memory_stack had no transcript tier, and the
capability surface the conversational brain offered was a hand-written dict
that had already fallen behind the fabric.
"""
import os
import tempfile

import pytest


@pytest.fixture()
def db(monkeypatch):
    path = os.path.join(tempfile.mkdtemp(), "t.sqlite3")
    monkeypatch.setenv("ADA_DB", path)
    from friday.toolsets import memory as M
    M.reset_store(None)
    yield M.store()
    M.reset_store(None)


def test_a_turn_survives_the_session(db):
    from friday import voice_brain as V
    V._remember_turn("user", "the warehouse cutover is on the ninth")
    V._remember_turn("assistant", "noted, sir")
    # A "new session" is just another read: nothing is cached in the page.
    assert [t for _, t in V._recent_turns()] == ["the warehouse cutover is on the ninth"]
    assert db.recent_messages(10)[-1]["content"] == "noted, sir"


def test_history_spans_conversations(db):
    db.add_message("web-2026-08-30", "user", "yesterday's decision")
    db.add_message("web-2026-08-31", "user", "today's question")
    said = [r["content"] for r in db.recent_messages(10)]
    assert "yesterday's decision" in said, "recall must not stop at a session boundary"


def test_the_transcript_reaches_the_prompt(db):
    from friday import memory_stack as M
    for i in range(30):
        db.add_message("c", "user", "turn %d about the cutover" % i)
    out = M.aggregate("what did we say about the cutover", budget_tokens=900)
    assert out["injected"]["episodes"] > 0
    assert "cutover" in out["prompt"]


def test_the_transcript_does_not_eat_the_whole_budget(db):
    from friday import memory_stack as M
    for i in range(200):
        db.add_message("c", "user", "a long turn number %d " % i + "x" * 200)
    out = M.aggregate("anything", budget_tokens=900)
    assert out["tokens_used"] <= 900
    # The share, not the lot: room is left for the tiers that come after.
    assert out["injected"]["episodes"] * 60 < 900


def test_contacts_are_remembered_and_recalled(db, monkeypatch):
    from friday import voice_brain as V
    from friday import memory_stack as M
    # Saving a contact is a write; the UI brain does it only when the
    # owner's words for the turn ask for it (A-036), so say them.
    monkeypatch.setitem(V._CURRENT_TURN, "text", "save my mum's contact: Sunita Rao")
    assert "saved" in V._run_capability(
        "contacts", "save",
        {"name": "Sunita Rao", "relation": "mother", "aliases": "mum, mummy"})["result"]
    monkeypatch.setitem(V._CURRENT_TURN, "text", "call mum")
    found = V._run_capability("contacts", "lookup", {"query": "call mum"})["result"]
    assert found and found[0]["name"] == "Sunita Rao"
    assert "Sunita Rao" in M.aggregate("ring mum tonight")["prompt"]


def test_the_capability_surface_comes_from_the_registry():
    """The bug this replaces: `scraping` shipped and stayed unreachable."""
    from friday import voice_brain as V
    from friday import fabric
    surface = V._surface()
    live = {p.family for p in fabric.registry().values() if p.risk == "low"}
    assert live - set(surface) == set(), "a live low-risk family must be reachable"


def test_mutating_operations_still_need_a_gate():
    from friday import voice_brain as V
    assert not V._is_read_only("delete_project")
    assert not V._is_read_only("run")
    assert V._is_read_only("search")
    assert V._is_read_only("architecture")
