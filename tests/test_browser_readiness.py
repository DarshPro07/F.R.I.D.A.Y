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


@pytest.mark.live
def test_a_real_launch_is_ready():
    pytest.importorskip("playwright")
    readiness = B.probe()
    assert readiness.state == B.BROWSER_READY, readiness
    assert readiness.title and readiness.version
    assert readiness.seconds > 0 and readiness.memory_mb > 0
