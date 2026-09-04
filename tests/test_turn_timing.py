"""Latency attribution: a slow turn names its cause, on both voice paths."""
from __future__ import annotations

import json

from friday import turn_timing as T


def test_a_fast_turn_carries_no_note():
    t = T.TurnTimer()
    t.add("model", 0.8)
    r = t.report(host={"cpu": 10, "ram": 40})
    assert not r["slow"] and r["note"] == ""
    assert r["slowest"] == "model"


def test_a_slow_turn_names_the_dominant_stage():
    t = T.TurnTimer()
    t.add("history", 0.2)
    t.add("screen", 11.0)
    t.add("model", 2.0)
    t.started -= 13.2
    r = t.report(host={"cpu": 20, "ram": 50})
    assert r["slow"] and r["slowest"] == "screen"
    assert "reading the screen" in r["note"]


def test_a_loaded_host_is_named_instead_of_the_work():
    t = T.TurnTimer()
    t.add("model", 6.0)
    t.started -= 6.0
    r = t.report(host={"cpu": 99, "ram": 97})
    assert r["host_loaded"]
    assert "99% CPU" in r["note"] and "this machine" in r["note"]


def test_livekit_stage_attribution():
    import agent_friday as af
    assert af._latency_stage("vision_inspect_screen") == "screen"
    assert af._latency_stage("web_deep_research") == "web"
    assert af._latency_stage("hermes_delegate") == "hermes"
    assert af._latency_stage("get_current_time") == "tool"


def test_ui_reply_carries_latency_meta(monkeypatch):
    """reply() returns latency + latency_note without changing the answer."""
    from friday import voice_brain as V

    class Resp:
        text = "Done, sir."
        function_calls = []
        candidates = []

    class Models:
        def generate_content(self, **k):
            return Resp()

    class Client:
        models = Models()

    monkeypatch.setattr(V, "_model", lambda: (Client(), "fake"))
    monkeypatch.setattr(V, "_recent_turns", lambda limit=None: [])
    monkeypatch.setattr(V, "_memory_context", lambda t: "")
    monkeypatch.setattr(V, "_remember_turn", lambda *a: None)
    monkeypatch.setattr(V, "_try_command", lambda t: None)
    out = V.reply("hello there friend")
    assert out["reply"] == "Done, sir."
    assert "latency" in out and "stages_s" in out["latency"]
    assert "model" in out["latency"]["stages_s"]
    assert out["latency_note"] == ""     # a fast fake is not slow
    json.dumps(out)                       # the page gets it verbatim
