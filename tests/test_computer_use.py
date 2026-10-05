"""CUA-001: one computer-use interface, two fabric backends, routed by action path."""
from __future__ import annotations

import sys
import types

import pytest

from friday import action_path, browser_readiness, computer_use, fabric
from friday.fabric_adapters import native_desktop, playwright_browser


@pytest.fixture(autouse=True)
def clean_fabric():
    fabric.reload()
    yield
    fabric.deactivate_all()


@pytest.fixture
def playwright_present(monkeypatch):
    monkeypatch.setitem(sys.modules, "playwright.sync_api",
                        types.ModuleType("playwright.sync_api"))


@pytest.fixture
def playwright_absent(monkeypatch):
    # None in sys.modules makes the import raise, as a missing package does.
    monkeypatch.setitem(sys.modules, "playwright.sync_api", None)


@pytest.fixture
def screen(monkeypatch):
    native = native_desktop._native()
    monkeypatch.setattr(native, "AVAILABLE", True)
    monkeypatch.setattr(native, "screen_size", lambda: (1920, 1080))
    return native


@pytest.fixture
def no_screen(monkeypatch):
    monkeypatch.setattr(native_desktop._native(), "AVAILABLE", False)


def _ready(url: str = "https://example.com/") -> browser_readiness.Readiness:
    return browser_readiness.Readiness(browser_readiness.BROWSER_READY,
                                       f"loaded {url}", "1.59", "Example Domain", 1.2)


# --- the interface ----------------------------------------------------------


@pytest.mark.parametrize("backend", [playwright_browser, native_desktop])
def test_each_backend_satisfies_the_interface(backend):
    assert computer_use.conforms(backend) == []


def test_both_backends_are_registered_through_the_fabric():
    ids = {p.id for p in fabric.by_family(computer_use.FAMILY)}
    assert {"playwright_browser", "native_desktop"} <= ids


def test_a_module_that_lacks_part_of_the_interface_does_not_conform():
    half = types.SimpleNamespace(PATHS=(action_path.DOM,), start=lambda: None)
    assert computer_use.conforms(half)


def test_a_backend_cannot_serve_a_path_the_interface_does_not_define():
    wrong = types.SimpleNamespace(
        PATHS=(action_path.NATIVE_API,), start=lambda: None, stop=lambda h: None,
        health=lambda h: {}, call=lambda op, h: None, DESCRIPTOR=native_desktop.DESCRIPTOR)
    assert any("not a computer-use path" in p for p in computer_use.conforms(wrong))


# --- routing by action path --------------------------------------------------


def test_a_browser_capability_routes_to_playwright(playwright_present):
    route = computer_use.route("browser_click")
    assert (route.path, route.backend, route.usable) == (
        action_path.DOM, "playwright_browser", True)


def test_a_desktop_capability_routes_to_the_native_desktop(screen):
    route = computer_use.route("desktop_step")
    assert (route.path, route.backend, route.state) == (
        action_path.COORDINATES, "native_desktop", fabric.READY)
    assert "1920x1080" in route.detail


def test_a_capability_that_is_not_a_computer_use_path_has_no_backend():
    assert computer_use.route("connector_gmail_send") is None
    assert computer_use.route("memory_search") is None


# --- a missing backend does not break the others -----------------------------


def test_a_missing_playwright_is_unavailable_and_desktop_still_routes(
        playwright_absent, screen):
    browser = computer_use.route("browser_click")
    assert browser.state == fabric.UNAVAILABLE and not browser.usable
    assert browser.backend == ""
    assert browser_readiness.NOT_INSTALLED in browser.detail
    assert [pid for pid, _, _ in browser.down] == ["playwright_browser"]

    assert computer_use.route("desktop_step").backend == "native_desktop"


def test_a_host_without_a_display_is_unavailable_and_the_browser_still_routes(
        playwright_present, no_screen):
    desktop = computer_use.route("desktop_step")
    assert desktop.state == fabric.UNAVAILABLE and desktop.backend == ""
    assert "Windows-only" in desktop.detail

    assert computer_use.route("browser_click").backend == "playwright_browser"


def test_a_display_of_zero_size_is_unavailable(monkeypatch, screen):
    monkeypatch.setattr(screen, "screen_size", lambda: (0, 0))
    route = computer_use.route("desktop_step")
    assert route.state == fabric.UNAVAILABLE and "no display" in route.detail


def test_an_unregistered_path_is_unavailable_with_the_reason(monkeypatch):
    monkeypatch.setattr(computer_use, "PATHS", (action_path.DOM, action_path.COORDINATES,
                                                action_path.VISION))
    route = computer_use.route("vision_describe")
    assert route.state == fabric.UNAVAILABLE
    assert "no backend is registered for the vision path" in route.detail


# --- the Playwright backend is honest about what it has checked --------------


def test_playwright_is_degraded_until_the_browser_build_is_probed(playwright_present):
    assert computer_use.route("browser_click").state == fabric.DEGRADED


def test_a_passing_probe_makes_playwright_ready(playwright_present, monkeypatch):
    monkeypatch.setattr(browser_readiness, "probe", lambda **kw: _ready())
    result = fabric.call("playwright_browser", "dom.probe")
    assert result.error is None
    assert result.output["state"] == browser_readiness.BROWSER_READY
    route = computer_use.route("browser_click")
    assert (route.backend, route.state) == ("playwright_browser", fabric.READY)


def test_a_probe_that_finds_no_chromium_makes_the_route_unavailable(
        playwright_present, monkeypatch):
    missing = browser_readiness.Readiness(browser_readiness.BINARY_MISSING,
                                          "run: python -m playwright install chromium")
    monkeypatch.setattr(browser_readiness, "probe", lambda **kw: missing)
    computer_use.route("browser_click")          # activates it, DEGRADED
    fabric.call("playwright_browser", "dom.probe")
    route = computer_use.route("browser_click")
    assert route.state == fabric.UNAVAILABLE
    assert browser_readiness.BINARY_MISSING in route.detail


def test_the_probe_loads_no_caller_chosen_url(playwright_present, monkeypatch):
    seen = []
    monkeypatch.setattr(browser_readiness, "probe", lambda **kw: seen.append(kw) or _ready())
    handle = playwright_browser.start()
    with pytest.raises(fabric.FabricError):
        playwright_browser.call("dom.probe", handle, url="http://169.254.169.254/")
    assert seen == []


@pytest.mark.parametrize("timeout_ms", [0, 999, 60_001])
def test_the_probe_timeout_is_bounded(playwright_present, timeout_ms):
    handle = playwright_browser.start()
    with pytest.raises(fabric.FabricError):
        playwright_browser.call("dom.probe", handle, timeout_ms=timeout_ms)


def test_the_desktop_probe_reads_the_screen_and_sends_no_input(screen, monkeypatch):
    sent = []
    for name in ("send_mouse_move_abs", "send_mouse_click", "send_text", "send_key"):
        monkeypatch.setattr(screen, name, lambda *a, **k: sent.append(a))
    fabric.activate("native_desktop")
    fabric.call("native_desktop", "coordinates.probe")
    assert sent == []
    assert native_desktop.call("coordinates.probe", {"native": screen}) == {
        "width": 1920, "height": 1080}


def test_a_backend_refuses_an_operation_it_does_not_have(screen):
    with pytest.raises(fabric.FabricError):
        native_desktop.call("coordinates.click", {"native": screen})
