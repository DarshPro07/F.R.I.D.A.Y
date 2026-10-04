"""
V7-D08 step 4b: the person's yes or no settles a pending confirmation from
ANY FRIDAY process, deterministically, before any model sees the words.

`friday.approval.settle` is the one helper both entry points use:
`voice_brain.reply` (browser/UI turn) and `FridayAgent._settle_pending_confirmation`
(the LiveKit voice turn). Both are exercised here against a confirmation
asked by a *different* `StoredBook` instance sharing the same store, the
way an MCP process asking about a browser purchase or a permanent delete
actually looks from the voice process that hears the answer.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

import friday.voice_brain as V
from friday import approval
from friday import confirmation as CF
from friday.store import Store


@pytest.fixture
def stores(tmp_path):
    path = tmp_path / "shared.sqlite3"
    asking, answering = Store(path), Store(path)
    yield asking, answering
    asking.close()
    answering.close()


@pytest.fixture
def books(stores):
    asking, answering = stores
    return (CF.StoredBook(lambda: asking, instance="MCP-1"),
            CF.StoredBook(lambda: answering, instance="VOICE-1"))


def _ask(book, **overrides):
    fields = dict(run_id="RUN-1", action="files.delete", target="x.txt",
                  question="Delete x.txt for good?", arguments={})
    fields.update(overrides)
    return book.ask(**fields)


class _Resp:
    def __init__(self, text):
        self.text = text
        self.function_calls = []
        self.candidates = [SimpleNamespace(content=None)]


def _fake_model(monkeypatch, captured):
    class Models:
        def generate_content(self, model, contents, config):
            captured["contents"] = contents
            return _Resp("Understood, sir.")

    client = SimpleNamespace(models=Models())
    monkeypatch.setattr(V, "_model", lambda: (client, "fake-model"))
    monkeypatch.setattr(V, "_try_command", lambda text: None)
    monkeypatch.setattr(V, "_recent_turns", lambda limit=None: [])
    monkeypatch.setattr(V, "_memory_context", lambda text: "")
    monkeypatch.setattr(V, "_remember_turn", lambda role, text: None)


def test_yes_approves_a_confirmation_asked_by_another_process(monkeypatch, books):
    asking, answering = books
    confirmation = _ask(asking, action="files.delete", target="x.txt")
    monkeypatch.setattr(CF, "book", answering)
    captured = {}
    _fake_model(monkeypatch, captured)

    out = V.reply("yes")

    row = asking.store().confirmation(confirmation.nonce)
    assert row["state"] == CF.APPROVED
    assert row["answered_by"] == "person"
    assert "contents" in captured
    joined = "\n".join(p.text for c in captured["contents"] for p in c.parts)
    assert confirmation.nonce in joined
    assert "files.delete" in joined


def test_no_refuses_and_notes_the_refusal(monkeypatch, books):
    asking, answering = books
    confirmation = _ask(asking, action="files.delete", target="y.txt")
    monkeypatch.setattr(CF, "book", answering)
    captured = {}
    _fake_model(monkeypatch, captured)

    V.reply("no")

    row = asking.store().confirmation(confirmation.nonce)
    assert row["state"] == CF.REFUSED
    joined = "\n".join(p.text for c in captured["contents"] for p in c.parts)
    assert "refused" in joined
    assert "y.txt" in joined


def test_two_pending_asks_which_and_calls_no_model(monkeypatch, books):
    asking, answering = books
    c1 = _ask(asking, action="files.delete", target="x.txt",
              question="Delete x.txt for good?")
    c2 = _ask(asking, action="files.delete", target="y.txt",
              question="Delete y.txt for good?")
    monkeypatch.setattr(CF, "book", answering)
    called = {"n": 0}

    def boom():
        called["n"] += 1
        raise AssertionError("model must not be called while ambiguous")

    monkeypatch.setattr(V, "_model", boom)
    monkeypatch.setattr(V, "_try_command", lambda text: None)
    monkeypatch.setattr(V, "_remember_turn", lambda role, text: None)

    out = V.reply("yes")

    assert called["n"] == 0
    assert "x.txt" in out["reply"] and "y.txt" in out["reply"]
    for nonce in (c1.nonce, c2.nonce):
        row = asking.store().confirmation(nonce)
        assert row["state"] == CF.PENDING


def test_nothing_pending_yes_reaches_model_unchanged(monkeypatch, books):
    _, answering = books
    monkeypatch.setattr(CF, "book", answering)
    captured = {}
    _fake_model(monkeypatch, captured)

    V.reply("yes")

    joined = "\n".join(p.text for c in captured["contents"] for p in c.parts)
    assert joined.strip().endswith("yes") or "yes" in joined
    assert "approved" not in joined and "refused" not in joined


def test_negative_control_without_the_helper_call(monkeypatch, books):
    """Removing the settle() call is exactly what would make the approval
    test above go red: without it, "yes" reaches the model unexamined and
    the other process's confirmation is never touched."""
    asking, answering = books
    confirmation = _ask(asking, action="files.delete", target="x.txt")
    monkeypatch.setattr(CF, "book", answering)
    monkeypatch.setattr(approval, "settle", lambda book, said: (None, ""))
    captured = {}
    _fake_model(monkeypatch, captured)

    V.reply("yes")

    row = asking.store().confirmation(confirmation.nonce)
    assert row["state"] == CF.PENDING  # not settled - proves the call matters


def test_agent_hook_injects_the_note_on_approval(monkeypatch, books):
    import agent_friday as A

    asking, answering = books
    confirmation = _ask(asking, action="files.delete", target="x.txt")
    monkeypatch.setattr(CF, "book", answering)

    class FakeTurnCtx:
        def __init__(self):
            self.messages = []

        def add_message(self, role, content):
            self.messages.append((role, content))

    agent = A.FridayAgent.__new__(A.FridayAgent)
    turn_ctx = FakeTurnCtx()

    import asyncio
    asyncio.run(agent._settle_pending_confirmation(turn_ctx, "yes"))

    assert turn_ctx.messages
    role, content = turn_ctx.messages[0]
    assert role == "system"
    assert confirmation.nonce in content


def test_agent_hook_says_the_question_and_stops_on_ambiguous(monkeypatch, books):
    import agent_friday as A

    asking, answering = books
    _ask(asking, action="files.delete", target="x.txt", question="Delete x.txt?")
    _ask(asking, action="files.delete", target="y.txt", question="Delete y.txt?")
    monkeypatch.setattr(CF, "book", answering)

    said = {}

    class FakeSession:
        async def say(self, text, allow_interruptions=True):
            said["text"] = text

    agent = A.FridayAgent.__new__(A.FridayAgent)
    monkeypatch.setattr(type(agent), "session",
                        property(lambda self: FakeSession()))

    import asyncio
    with pytest.raises(A.StopResponse):
        asyncio.run(agent._settle_pending_confirmation(object(), "yes"))

    assert "x.txt" in said["text"] and "y.txt" in said["text"]


def test_the_turn_entry_settles_the_yes(monkeypatch, books):
    """Spoken and typed turns both enter through `read_before_answering`.
    The yes must be settled there, not only when the hook is called by hand -
    removing the call must turn this red."""
    import asyncio

    import agent_friday as A

    asking, answering = books
    confirmation = _ask(asking, action="files.delete", target="x.txt")
    monkeypatch.setattr(CF, "book", answering)

    async def nothing(*args, **kwargs):
        return None

    monkeypatch.setattr(A, "remember_the_project", lambda text: None)
    monkeypatch.setattr(A, "note_the_requirements", lambda *args, **kwargs: None)
    monkeypatch.setattr(A, "research_first", nothing)
    monkeypatch.setattr(A, "check_what_he_asserted", nothing)
    agent = A.FridayAgent.__new__(A.FridayAgent)
    agent.prepare_turn = lambda turn_ctx, text: None
    agent.stop_re_reading = lambda: None
    agent._router = type('R', (), {'note_turn': lambda self: None})()

    class Ctx:
        def __init__(self):
            self.messages = []

        def add_message(self, role, content):
            self.messages.append((role, content))

    asyncio.run(agent.read_before_answering(Ctx(), "yes"))
    assert answering.pending[confirmation.nonce].state == CF.APPROVED
