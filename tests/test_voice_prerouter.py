"""
FAB-008: a deterministic pre-router settles status, clock and direct-command
turns before any conversational model is called, and the share of turns that
needed a model is measured.
"""
import datetime

import pytest

from friday import voice_brain as vb


@pytest.fixture
def brain(monkeypatch):
    asked = []
    monkeypatch.setattr(vb, "_model", lambda: asked.append("gemini"))
    monkeypatch.setattr(vb, "_cli_reply", lambda *a, **k: asked.append("cli"))
    monkeypatch.setattr(vb, "_remember_turn", lambda role, text: None)
    monkeypatch.setattr(vb, "_TURN_COUNTS", {"model": 0, "no_model": 0})
    return asked


def test_a_clock_question_is_answered_without_a_model(brain):
    for spoken in ("What time is it?", "Friday, what's the date today?",
                   "what day is it", "what is the time"):
        out = vb.reply(spoken)
        assert out["action"] == "clock", spoken
        assert str(datetime.datetime.now().astimezone().year) in out["reply"]
        assert out["needed_model"] is False
    assert brain == []


def test_a_clock_question_about_somewhere_else_still_goes_to_the_model(brain):
    out = vb.reply("what time is it in Tokyo?")
    assert out["needed_model"] is True
    assert brain == ["gemini", "cli"]


def test_a_status_turn_needs_no_model(brain, monkeypatch):
    import friday.ui_server as ui
    monkeypatch.setattr(ui, "build_state", lambda: {
        "connections": {"gbrain": {"status": "ok"}, "hermes": {"status": "ok"}},
        "mcp": {"total": 3}, "system": {"ram_percent": 40}})
    out = vb.reply("system status")
    assert out["action"] == "status" and out["needed_model"] is False
    assert brain == []


def test_the_share_of_turns_that_needed_a_model_is_measured(brain, monkeypatch):
    import friday.ui_server as ui
    monkeypatch.setattr(ui, "build_state", lambda: {
        "connections": {"gbrain": {"status": "ok"}, "hermes": {"status": "ok"}},
        "mcp": {"total": 3}, "system": {"ram_percent": 40}})
    assert vb.turn_share() == {"turns": 0, "model_turns": 0, "no_model_turns": 0,
                               "model_share": None}
    vb.reply("what time is it")
    vb.reply("system status")
    vb.reply("explain how transformers work")        # nothing deterministic: the model
    assert vb.reply("") == {"reply": "", "empty": True}   # an empty turn is not a turn
    share = vb.turn_share()
    assert share == {"turns": 3, "model_turns": 1, "no_model_turns": 2,
                     "model_share": pytest.approx(1 / 3)}
    status = vb.reply("system status")
    assert "1 of 3 turns so far needed a model" in status["reply"]
    assert status["model_share"] == pytest.approx(1 / 3)
