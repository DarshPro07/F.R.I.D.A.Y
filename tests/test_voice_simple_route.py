"""
COG-005: the live voice turn hands a request the complexity classifier marks
SIMPLE to `simple_loop.route_simple`; every other turn stays on the path it
always had. The turn entry is `FridayAgent.read_before_answering`, which the
spoken and the typed path both go through.
"""
from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest
from livekit.agents.llm import StopResponse

import agent_friday as A
from friday import capability_runtime as CR
from friday import contracts as c

SIMPLE_REQUEST = "what is the battery level"
COMPLEX_REQUEST = ("Build a full web app with login, then write tests, then "
                   "deploy it to production and document everything")

ACTION = {"intent": "read the battery level", "capability": "system_get_info",
          "arguments": {}, "postcondition": {"status": c.SUCCEEDED,
                                             "output_contains": "battery"},
          "confidence": 0.9}


class Ctx:
    def __init__(self):
        self.messages = []

    def add_message(self, role, content):
        self.messages.append((role, content))


@pytest.fixture
def turn(monkeypatch):
    """A FridayAgent with stand-ins for everything the turn entry touches."""
    seen = SimpleNamespace(researched=[], said=[], dispatched=[], proposed=[])

    async def research(turn_ctx, text):
        seen.researched.append(text)
        return None

    async def nothing(*args, **kwargs):
        return None

    async def say(note, **kwargs):
        seen.said.append(note)

    async def dispatch(capability, arguments, *, run=None):
        seen.dispatched.append(capability)
        return {"status": c.SUCCEEDED, "output": "battery 80%", "error": ""}

    monkeypatch.setattr(A, "remember_the_project", lambda text: None)
    monkeypatch.setattr(A, "note_the_requirements", lambda *a, **k: None)
    monkeypatch.setattr(A, "research_first", research)
    monkeypatch.setattr(A, "check_what_he_asserted", nothing)
    monkeypatch.setattr(CR, "build_dispatch", lambda **k: dispatch)
    monkeypatch.setattr(A.FridayAgent, "session",
                        SimpleNamespace(say=say, interrupt=lambda: None),
                        raising=False)

    agent = A.FridayAgent.__new__(A.FridayAgent)
    agent.prepare_turn = lambda turn_ctx, text: None
    agent.stop_re_reading = lambda: None
    agent._router = type("R", (), {"note_turn": lambda self: None})()

    def transport(state):
        seen.proposed.append(state.request)
        return json.dumps(ACTION), 50, 0.001

    agent._simple_complete = transport
    return agent, seen


def run_turn(agent, text):
    return asyncio.run(agent.read_before_answering(Ctx(), text))


def test_a_simple_turn_runs_the_simple_loop_and_ends_the_turn(turn):
    agent, seen = turn
    with pytest.raises(StopResponse):
        run_turn(agent, SIMPLE_REQUEST)
    assert seen.proposed == [SIMPLE_REQUEST]
    assert seen.dispatched == ["system_get_info"]
    assert seen.researched == []
    assert seen.said and "postcondition met" in seen.said[0]


def test_a_non_simple_turn_stays_on_the_existing_path(turn):
    agent, seen = turn
    run_turn(agent, COMPLEX_REQUEST)
    assert seen.proposed == [] and seen.dispatched == []
    assert seen.researched == [COMPLEX_REQUEST]


def test_without_a_model_transport_a_simple_turn_is_unchanged(turn):
    agent, seen = turn
    agent._simple_complete = None
    run_turn(agent, SIMPLE_REQUEST)
    assert seen.proposed == [] and seen.dispatched == []
    assert seen.researched == [SIMPLE_REQUEST]


def test_a_transport_failure_falls_back_to_the_existing_path(turn):
    agent, seen = turn

    def broken(state):
        raise RuntimeError("transport down")

    agent._simple_complete = broken
    run_turn(agent, SIMPLE_REQUEST)
    assert seen.dispatched == []
    assert seen.researched == [SIMPLE_REQUEST]
