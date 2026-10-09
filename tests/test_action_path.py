"""
The hybrid action-path router (PRD v7 §48, BROW-001).

When several capabilities can take the same step, the most direct path goes
first - a native API, then an official scripting interface, then a
structured tool or MCP, then the DOM or accessibility tree, then semantic
vision, and screen coordinates last - and a path that has kept failing moves
behind the ones that have not.
"""
from __future__ import annotations

import pytest

from friday import action_path as A
from friday import capabilities


def test_the_order_is_section_48s():
    assert A.ORDER == (A.WEBMCP, A.NATIVE_API, A.SCRIPTING, A.STRUCTURED, A.DOM,
                       A.VISION, A.COORDINATES)


def test_each_path_is_what_the_capability_does():
    assert A.path_of("web_fetch") == A.NATIVE_API
    assert A.path_of("connector_list") == A.NATIVE_API
    assert A.path_of("apps_open") == A.SCRIPTING
    assert A.path_of("capability_use") == A.STRUCTURED
    assert A.path_of("files_read") == A.STRUCTURED
    assert A.path_of("browser_act") == A.DOM
    assert A.path_of("vision_inspect_screen") == A.VISION
    assert A.path_of("screen_point") == A.VISION
    # A takeover step finds its target by vision and clicks at screen coordinates.
    assert A.path_of("desktop_step") == A.COORDINATES


def test_every_screen_and_browser_capability_is_on_a_gui_path():
    """A new GUI capability must not pass for a structured one by default."""
    gui = [c["id"] for c in capabilities.as_dicts()
           if c["id"].startswith(("browser_", "vision_", "desktop_", "screen_"))]
    assert gui
    assert {cap: A.path_of(cap) for cap in gui if A.path_of(cap) not in
            (A.DOM, A.VISION, A.COORDINATES)} == {}


def test_a_step_takes_the_most_direct_path_first():
    assert A.rank(["desktop_step", "browser_act", "vision_inspect_screen", "web_fetch"]) == [
        "web_fetch", "browser_act", "vision_inspect_screen", "desktop_step"]


def test_a_path_that_keeps_failing_moves_behind_the_others():
    ranked = A.rank(["web_fetch", "browser_act"], reliability={"web_fetch": (1, 6)})
    assert ranked == ["browser_act", "web_fetch"]
    # Too few attempts to judge: the order stands.
    assert A.rank(["web_fetch", "browser_act"], reliability={"web_fetch": (0, 3)}) == [
        "web_fetch", "browser_act"]
    # A path that mostly works keeps its place.
    assert A.rank(["web_fetch", "browser_act"], reliability={"web_fetch": (5, 6)}) == [
        "web_fetch", "browser_act"]


def test_a_trusted_webmcp_tool_is_selected_first_and_without_one_the_next_tier_is():
    """WEBMCP-001: WebMCP, when present and trusted, is the first tier of the
    browser route; absent or untrusted, the route falls back to the next tier."""
    assert A.path_of("webmcp_call") == A.WEBMCP
    candidates = ["desktop_step", "browser_act", "web_fetch", "webmcp_call"]
    trusted = lambda cap: cap == "webmcp_call"  # noqa: E731
    assert A.rank(candidates, trusted=trusted)[0] == "webmcp_call"
    # Present but not trusted (and the default trusts nothing): never selected.
    assert A.rank(candidates, trusted=lambda cap: False) == [
        "web_fetch", "browser_act", "desktop_step"]
    assert A.rank(candidates) == ["web_fetch", "browser_act", "desktop_step"]
    # Absent: the next tier is first.
    assert A.rank(["browser_act", "web_fetch"], trusted=trusted)[0] == "web_fetch"


@pytest.mark.live
def test_one_objective_runs_a_structured_step_and_a_real_browser_step():
    """BROW-001's journey, for real: the network and a headless Chromium
    (the `browser` extra). One objective reads a public page over HTTP and
    again through the browser's DOM, and both paths see it."""
    import sys
    from pathlib import Path

    pytest.importorskip("playwright")
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
    import journey_hybrid_browser as J

    result = J.run()
    assert result["run_status"] == "COMPLETED", result
    by_capability = {step["capability"]: step for step in result["steps"]}
    assert all(step["status"] == "SUCCEEDED" for step in result["steps"]), result["steps"]
    assert by_capability["web_fetch"]["path"] == A.NATIVE_API and by_capability["web_fetch"]["saw_the_page"]
    assert by_capability["browser_inspect"]["path"] == A.DOM and by_capability["browser_inspect"]["saw_the_page"]


def _execution_db(monkeypatch, rows):
    import sqlite3

    from friday import routing_memory

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("CREATE TABLE objective_tasks (capability TEXT, status TEXT)")
    conn.executemany("INSERT INTO objective_tasks VALUES (?,?)", rows)

    class Db:
        _conn = conn

    monkeypatch.setattr(routing_memory, "_store", lambda: Db())
    routing_memory.forget()
    return routing_memory


def test_combine_sums_tallies():
    assert A.combine({"a": (1, 2)}, {"a": (2, 3), "b": (0, 1)}, None) == {"a": (3, 5), "b": (0, 1)}


def test_execution_outcomes_are_tallied_from_finished_tasks(monkeypatch):
    rows = ([("web_a", "SUCCEEDED")] * 2 + [("web_a", "FAILED")] * 3
            + [("web_a", "RUNNING"), ("", "FAILED")])
    routing_memory = _execution_db(monkeypatch, rows)
    try:
        assert routing_memory.execution_reliability() == {"web_a": (2, 5)}
    finally:
        routing_memory.forget()


def test_the_planner_moves_a_path_that_fails_when_run_behind_the_others(monkeypatch):
    from friday import planner as P

    rows = ([("web_zqxread", "FAILED")] * 4 + [("web_zqxread", "SUCCEEDED")]
            + [("browser_zqxread", "SUCCEEDED")] * 5)
    routing_memory = _execution_db(monkeypatch, rows)
    monkeypatch.setattr(P, "candidates",
                        lambda operation, target, limit=8: ["web_zqxread", "browser_zqxread"])
    monkeypatch.setattr(P, "_fit", lambda intent, cid: 1)
    try:
        goal = P.Goal("g1", "read the zqx page", "READ", "WEB_PAGE")
        assert P.resolve(P.Plan(goals=[goal])).goals[0].capability == "browser_zqxread"
        monkeypatch.setattr(routing_memory, "execution_reliability", lambda: {})
        goal = P.Goal("g1", "read the zqx page", "READ", "WEB_PAGE")
        assert P.resolve(P.Plan(goals=[goal])).goals[0].capability == "web_zqxread"
    finally:
        routing_memory.forget()
