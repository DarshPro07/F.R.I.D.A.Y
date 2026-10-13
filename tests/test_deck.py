"""
Every Command Deck button that calls an MCP tool sends arguments the tool
accepts.

Measured live 2026-10-13: PLAN TODAY's gate, approved in the UI, returned
"objective_start: 1 validation error ... objective Field required" because the
deck sent {"request": ...}. Binding against the real signature makes the next
wrong argument fail here instead of on the button.
"""
from __future__ import annotations
import inspect
import pytest
from friday import deck
from friday.tools import objective_control, web


class _FakeMCP:
    def __init__(self):
        self.tools = {}

    def tool(self):
        def register(fn):
            self.tools[fn.__name__] = fn
            return fn
        return register


def _real_tools():
    mcp = _FakeMCP()
    for module in (objective_control, web):
        module.register(mcp)
    return mcp.tools


CALLS = [i for i in deck.INTENTS if i["mode"] in (deck.AGENT, deck.GATED)]


@pytest.mark.parametrize("intent", CALLS, ids=[i["id"] for i in CALLS])
def test_a_deck_intent_sends_arguments_its_tool_accepts(intent):
    tools = _real_tools()
    assert intent["tool"] in tools, \
        f"register the module that owns {intent['tool']} in this test"
    inspect.signature(tools[intent["tool"]]).bind(**intent["args"])
