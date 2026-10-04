"""
Whether FRIDAY can really drive a browser here (PRD v7 BROW-001; owner's
invariant, 2026-09-29): BROWSER_READY only when the browser extra is
installed, its Chromium build is present, and a real launch reads a harmless
public page. Importing Playwright is not readiness.
"""
from __future__ import annotations

import asyncio
import sys
import types

import pytest

from friday import browser_readiness as B
from friday import contracts as c
from friday import netguard
from friday import policy as p
from friday.toolsets import web as W


class _Chromium:
    def __init__(self, error=None):
        self.error = error

    def launch(self, **kwargs):
        raise self.error


class _Playwright:
    def __init__(self, error):
        self.chromium = _Chromium(error)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def test_a_missing_package_is_not_installed(monkeypatch):
    monkeypatch.setitem(sys.modules, "playwright", None)
    monkeypatch.setitem(sys.modules, "playwright.sync_api", None)
    readiness = B.probe()
    assert readiness.state == B.NOT_INSTALLED
    assert "browser" in readiness.detail


def test_a_missing_chromium_build_says_how_to_install_it(monkeypatch):
    pytest.importorskip("playwright")
    import playwright.sync_api as api

    error = RuntimeError("BrowserType.launch: Executable doesn't exist at C:/x/chrome.exe")
    monkeypatch.setattr(api, "sync_playwright", lambda: _Playwright(error))
    readiness = B.probe()
    assert readiness.state == B.BINARY_MISSING
    assert "playwright install chromium" in readiness.detail


def test_any_other_launch_failure_is_not_ready(monkeypatch):
    pytest.importorskip("playwright")
    import playwright.sync_api as api

    monkeypatch.setattr(api, "sync_playwright", lambda: _Playwright(RuntimeError("sandbox denied")))
    readiness = B.probe()
    assert readiness.state == B.LAUNCH_FAILED and "sandbox denied" in readiness.detail


def test_the_probe_page_is_held_to_the_network_guard():
    with pytest.raises(netguard.UrlRefused):
        B.probe(url="http://127.0.0.1:8000/")


# --- the browser tools consult the same typed reason (BROW-004) -------------


class _AsyncChromium:
    def __init__(self, error):
        self.error = error

    async def launch(self, **kwargs):
        raise self.error


class _AsyncPlaywright:
    def __init__(self, error):
        self.chromium = _AsyncChromium(error)
        self.stopped = False

    async def stop(self):
        self.stopped = True


class _Starter:
    def __init__(self, driver):
        self.driver = driver

    async def start(self):
        return self.driver


@pytest.fixture
def run():
    return c.Run.create("test", capability="web")


@pytest.fixture
def engine():
    return p.PolicyEngine(autonomy=p.GUARDED)


@pytest.fixture
def fresh_session(monkeypatch):
    session = W.BrowserSession()
    monkeypatch.setattr(W, "session", session)
    return session


def _fake_async_playwright(monkeypatch, driver):
    monkeypatch.setitem(sys.modules, "playwright.async_api",
                        types.SimpleNamespace(async_playwright=lambda: _Starter(driver)))


def test_browser_open_reports_unavailable_when_the_chromium_build_is_missing(
        monkeypatch, run, engine, fresh_session):
    error = RuntimeError("BrowserType.launch: Executable doesn't exist at C:/x/chrome.exe")
    driver = _AsyncPlaywright(error)
    _fake_async_playwright(monkeypatch, driver)
    result = asyncio.run(W.browser_open(run, "https://example.com", engine=engine))
    assert result.status == c.UNSUPPORTED
    assert B.BINARY_MISSING in result.error and "playwright install chromium" in result.error
    assert result.output["readiness"] == B.BINARY_MISSING
    assert not result.may_claim_completion
    assert driver.stopped, "the driver started before the failed launch must be released"
    assert not fresh_session.running


def test_browser_open_reports_unavailable_when_the_package_is_missing(
        monkeypatch, run, engine, fresh_session):
    monkeypatch.setitem(sys.modules, "playwright", None)
    monkeypatch.setitem(sys.modules, "playwright.async_api", None)
    result = asyncio.run(W.browser_open(run, "https://example.com", engine=engine))
    assert result.status == c.UNSUPPORTED
    assert B.NOT_INSTALLED in result.error and "[browser]" in result.error


def test_browser_act_reports_unavailable_with_the_probes_reason(
        monkeypatch, run, engine, fresh_session):
    _fake_async_playwright(monkeypatch, _AsyncPlaywright(RuntimeError("sandbox denied")))
    result = asyncio.run(W.browser_act(run, "open", "https://example.com", engine=engine))
    assert result.status == c.UNSUPPORTED
    assert B.LAUNCH_FAILED in result.error and "sandbox denied" in result.error


def test_a_failed_start_is_retried_not_cached(monkeypatch, run, engine, fresh_session):
    first = _AsyncPlaywright(RuntimeError("sandbox denied"))
    _fake_async_playwright(monkeypatch, first)
    asyncio.run(W.browser_open(run, "https://example.com", engine=engine))
    second = _AsyncPlaywright(RuntimeError("sandbox denied again"))
    _fake_async_playwright(monkeypatch, second)
    result = asyncio.run(W.browser_open(run, "https://example.com", engine=engine))
    assert "sandbox denied again" in result.error
    assert first.stopped and second.stopped


# --- the control-room browser names the same typed reason (BROW-004) --------


class _SyncChromium:
    def __init__(self, error):
        self.error = error

    def launch_persistent_context(self, *args, **kwargs):
        raise self.error


class _SyncPlaywright:
    def __init__(self, error):
        self.chromium = _SyncChromium(error)
        self.stopped = False

    def stop(self):
        self.stopped = True


def _fake_sync_playwright(monkeypatch, driver):
    monkeypatch.setitem(sys.modules, "playwright.sync_api", types.SimpleNamespace(
        sync_playwright=lambda: types.SimpleNamespace(start=lambda: driver)))


@pytest.fixture
def ui(monkeypatch, tmp_path):
    from friday import ui_browser

    monkeypatch.setattr(ui_browser, "_PROFILE_DIR", tmp_path / "profile")
    monkeypatch.setattr(ui_browser, "_worker", ui_browser._Worker())
    return ui_browser


def test_the_control_room_browser_names_a_missing_chromium_build(monkeypatch, ui):
    driver = _SyncPlaywright(RuntimeError("BrowserType.launch_persistent_context: "
                                          "Executable doesn't exist at C:/x/chrome.exe"))
    _fake_sync_playwright(monkeypatch, driver)
    with pytest.raises(B.BrowserUnavailable) as caught:
        ui._worker.call(lambda page: page, timeout=5)
    assert caught.value.readiness.state == B.BINARY_MISSING
    assert "playwright install chromium" in str(caught.value)
    assert driver.stopped, "the driver started before the failed launch must be released"
    assert ui._worker.running is False


def test_the_control_room_browser_names_a_missing_package(monkeypatch, ui):
    monkeypatch.setitem(sys.modules, "playwright", None)
    monkeypatch.setitem(sys.modules, "playwright.sync_api", None)
    with pytest.raises(B.BrowserUnavailable) as caught:
        ui._worker.call(lambda page: page, timeout=5)
    assert caught.value.readiness.state == B.NOT_INSTALLED and "[browser]" in str(caught.value)


def test_a_failed_control_room_boot_is_retried_not_cached(monkeypatch, ui):
    first = _SyncPlaywright(RuntimeError("sandbox denied"))
    _fake_sync_playwright(monkeypatch, first)
    with pytest.raises(B.BrowserUnavailable):
        ui._worker.call(lambda page: page, timeout=5)
    second = _SyncPlaywright(RuntimeError("sandbox denied again"))
    _fake_sync_playwright(monkeypatch, second)
    with pytest.raises(B.BrowserUnavailable) as caught:
        ui._worker.call(lambda page: page, timeout=5)
    assert "sandbox denied again" in str(caught.value)
    assert first.stopped and second.stopped


def test_open_url_reports_an_unavailable_browser_as_a_typed_status(monkeypatch, ui):
    _fake_sync_playwright(monkeypatch, _SyncPlaywright(RuntimeError("sandbox denied")))
    out = ui.open_url("https://example.com/")
    assert out["status"] == "unavailable" and out["readiness"] == B.LAUNCH_FAILED
    assert "sandbox denied" in out["verdict"] and out["content"] == ""
    study = ui.study_url("https://example.com/")
    assert study["status"] == "unavailable" and study["readiness"] == B.LAUNCH_FAILED


@pytest.mark.live
def test_a_real_launch_is_ready():
    pytest.importorskip("playwright")
    readiness = B.probe()
    assert readiness.state == B.BROWSER_READY, readiness
    assert readiness.title and readiness.version
    assert readiness.seconds > 0 and readiness.memory_mb > 0
