"""
The dashboard is a view, not a URL.

What was there opened `https://worldmonitor.app/` - the marketing landing
page - and returned "Displaying the World Monitor on your primary screen now,
sir." It said that because `webbrowser.open` returned, which reports that a
browser was launched and nothing whatever about where it went.

Two separate failures in one sentence, and the second is the worse one: a
statement about what is on the boss's screen, made by something that never
looked.
"""

from __future__ import annotations

import pytest

from friday import contracts as c
from friday import policy as p
from friday import worldmonitor as WM

#: The address the boss's own preset uses, character for character.
PRESET = (
    "https://www.worldmonitor.app/dashboard"
    "?lat=20.0000&lon=-0.1800&zoom=1.00&view=global&timeRange=7d"
    "&layers=conflicts%2Cbases%2Chotspots%2Cnuclear%2Csanctions%2Cweather"
    "%2CcanadaAlerts%2Ceconomic%2Cwaterways%2Coutages%2Cmilitary%2Cnatural"
)


@pytest.fixture(autouse=True)
def _registered():
    p.TOOL_CATEGORIES.setdefault("world_monitor.open", p.BROWSER_CONTROL)


# ---------------------------------------------------------------------------
# The view, and the address it lives at
# ---------------------------------------------------------------------------


def test_the_default_view_is_the_preset_exactly():
    assert WM.WorldMonitorView().url() == PRESET


def test_the_landing_page_is_not_a_dashboard():
    """
    The distinction the old implementation could not make, and the reason it
    could report success while showing the wrong thing.
    """
    assert WM.parse("https://worldmonitor.app/") is None

    correct, why = WM.shows("https://worldmonitor.app/", WM.WorldMonitorView())
    assert not correct
    assert "landing page" in why


def test_a_view_survives_a_round_trip():
    view = WM.WorldMonitorView().over("30d").focused_on("weather")
    assert WM.parse(view.url()) == view


def test_meaning_is_compared_not_the_query_string():
    """
    Parameter order belongs to whoever built the URL. Two identical views can
    serialise differently, and asserting on the literal string would fail on a
    reordering that changed nothing while passing a redirect that changed
    everything.
    """
    view = WM.WorldMonitorView()
    scrambled = ("https://www.worldmonitor.app/dashboard?"
                 "layers=" + "%2C".join(view.layers) +
                 "&timeRange=7d&view=global&zoom=1.00&lon=-0.1800&lat=20.0000")

    correct, why = WM.shows(scrambled, view)
    assert correct, why


def test_a_missing_layer_is_named():
    view = WM.WorldMonitorView()
    thin = WM.WorldMonitorView(layers=("weather",)).url()

    correct, why = WM.shows(thin, view)
    assert not correct
    assert "missing layer" in why and "conflicts" in why


def test_the_wrong_time_range_is_named():
    correct, why = WM.shows(WM.WorldMonitorView().over("24h").url(),
                            WM.WorldMonitorView().over("7d"))
    assert not correct
    assert "time range" in why


def test_a_focus_narrows_the_layers_rather_than_adding_to_them():
    """
    A request about the weather should not silently draw sanctions and nuclear
    sites over it.
    """
    weather = WM.WorldMonitorView().focused_on("weather")
    assert "weather" in weather.layers
    assert "nuclear" not in weather.layers
    assert "sanctions" not in weather.layers


def test_a_location_moves_the_viewport_off_global():
    europe = WM.WorldMonitorView().at(50.0, 10.0)
    assert europe.view == "regional"
    assert europe.lat == 50.0
    assert WM.parse(europe.url()).lat == 50.0


@pytest.mark.parametrize("bad", ["yesterday", "1y", ""])
def test_a_time_range_nobody_defined_is_refused(bad):
    with pytest.raises(ValueError):
        WM.WorldMonitorView(time_range=bad)


def test_a_dashboard_with_no_layers_is_refused():
    with pytest.raises(ValueError):
        WM.WorldMonitorView(layers=())


# ---------------------------------------------------------------------------
# The capability
# ---------------------------------------------------------------------------


def test_opening_it_reports_partial_and_says_which_half_is_missing(monkeypatch):
    """
    The address is this process's own construction and can be checked exactly.
    Where the browser landed is a fact about another program, and until the
    Browser Companion can observe it, claiming the screen shows something is a
    claim nobody verified.
    """
    import webbrowser

    from friday.toolsets import web as W

    opened: list[str] = []
    monkeypatch.setattr(webbrowser, "open",
                        lambda url, *a, **k: opened.append(url) or True)

    result = W.world_monitor_open(c.Run.create("dashboard", capability="web"))

    assert result.status == c.PARTIAL
    assert not result.may_claim_completion
    assert opened == [PRESET]
    assert result.output["browser_state_observed"] is False
    assert "not observable" in result.error


def test_a_focused_request_reaches_the_browser_focused(monkeypatch):
    import webbrowser

    from friday.toolsets import web as W

    opened: list[str] = []
    monkeypatch.setattr(webbrowser, "open",
                        lambda url, *a, **k: opened.append(url) or True)

    W.world_monitor_open(c.Run.create("weather", capability="web"),
                         focus="weather", time_range="24h")

    view = WM.parse(opened[0])
    assert view.time_range == "24h"
    assert "weather" in view.layers and "nuclear" not in view.layers


def test_a_browser_that_refuses_is_a_failure_not_a_success(monkeypatch):
    import webbrowser

    from friday.toolsets import web as W

    monkeypatch.setattr(webbrowser, "open", lambda *a, **k: False)
    result = W.world_monitor_open(c.Run.create("dashboard", capability="web"))

    assert result.status == c.FAILED
    assert "no browser" in result.error


def test_it_is_reachable_from_a_durable_objective():
    """
    It was one of the 28. An objective that asked for the dashboard used to get
    "no such capability" about something conversation could open.
    """
    from friday import capability_runtime as R

    assert "open_world_monitor" in R.reachable()
    resolution = R.resolutions()["open_world_monitor"]
    assert resolution.function == "world_monitor_open"


# ---------------------------------------------------------------------------
# The mutation check
# ---------------------------------------------------------------------------


def test_reverting_to_the_landing_page_would_be_caught():
    """
    §24-J. If somebody restored `url = "https://worldmonitor.app/"`, this is
    the assertion that fires - the old behaviour cannot satisfy a dashboard
    request, by construction rather than by vigilance.
    """
    correct, _ = WM.shows("https://worldmonitor.app/", WM.WorldMonitorView())
    assert not correct

    correct, _ = WM.shows("https://www.worldmonitor.app/", WM.WorldMonitorView())
    assert not correct, "a www landing page is still not a dashboard"
