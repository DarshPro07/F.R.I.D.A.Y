"""PROMPT-001: instructions are compiled from ranked layers.

A lower layer that contradicts a higher one is dropped and reported, a
duplicated instruction appears once, and the prompt's health measure falls
when tool schemas nothing uses are added.
"""
from __future__ import annotations

import sys

sys.path.insert(0, ".")

from friday.prompt_compiler import Layer, compile_instructions

CONTRACT = Layer("worker_contract", 0,
                 "Never push to the main branch.\nAlways report file:line evidence.")
SKILL = Layer("skill", 1,
              "Always push to the main branch.\nKeep answers short.")


def test_a_skill_contradicting_the_contract_loses_and_is_reported():
    out = compile_instructions([SKILL, CONTRACT])
    assert "Never push to the main branch." in out["prompt"]
    assert "Always push to the main branch." not in out["prompt"]
    assert "Keep answers short." in out["prompt"]
    [conflict] = out["conflicts"]
    assert conflict["kept_layer"] == "worker_contract"
    assert conflict["dropped_layer"] == "skill"
    assert conflict["dropped"] == "Always push to the main branch."


def test_a_duplicated_instruction_appears_once():
    other = Layer("persona", 2, "always report file:line evidence.\nBe warm.")
    out = compile_instructions([CONTRACT, other])
    assert out["prompt"].lower().count("report file:line evidence") == 1
    assert out["duplicates_removed"] == 1
    assert "Be warm." in out["prompt"]


def test_health_falls_when_unused_tool_schemas_are_added():
    schema = {"name": "files_delete", "description": "Delete a file. " * 40}
    base = compile_instructions([CONTRACT], tool_schemas=[], used_tools=[],
                                min_health=0.0)
    padded = compile_instructions([CONTRACT], tool_schemas=[schema],
                                  used_tools=[], min_health=0.0)
    assert padded["health"] < base["health"]
    assert padded["unused_tools"] == ["files_delete"]


def test_health_below_the_floor_prunes_unused_schemas_before_the_call():
    schema = {"name": "files_delete", "description": "Delete a file. " * 40}
    used = {"name": "web_search", "description": "Search."}
    out = compile_instructions([CONTRACT], tool_schemas=[schema, used],
                               used_tools=["web_search"], min_health=0.9)
    assert [t["name"] for t in out["tool_schemas"]] == ["web_search"]
    assert out["pruned_tools"] == ["files_delete"]
    assert out["health"] >= 0.9
