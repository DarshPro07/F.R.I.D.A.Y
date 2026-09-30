"""
Whether FRIDAY can really drive a browser here (PRD v7 BROW-001; owner's
invariant, 2026-09-29): BROWSER_READY only when the browser extra is
installed, its Chromium build is present, and a real launch reads a harmless
public page. Importing Playwright is not readiness.
"""
from __future__ import annotations

import sys

import pytest

from friday import browser_readiness as B
from friday import netguard


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


@pytest.mark.live
def test_a_real_launch_is_ready():
    pytest.importorskip("playwright")
    readiness = B.probe()
    assert readiness.state == B.BROWSER_READY, readiness
    assert readiness.title and readiness.version
    assert readiness.seconds > 0 and readiness.memory_mb > 0
