"""
The hybrid action-path router (PRD v7 §48, BROW-001).

When several capabilities can take the same step, the most direct path goes
first - a native API, then an official scripting interface, then a
structured tool or MCP, then the DOM or accessibility tree, then semantic
vision, and screen coordinates last - and a path that has kept failing moves
behind the ones that have not.
"""
from __future__ import annotations

from friday import action_path as A
from friday import capabilities


def test_the_order_is_section_48s():
    assert A.ORDER == (A.NATIVE_API, A.SCRIPTING, A.STRUCTURED, A.DOM, A.VISION,
                       A.COORDINATES)


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
