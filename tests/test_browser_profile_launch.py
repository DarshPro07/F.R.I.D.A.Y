"""
The browser session launches the profile the person approved (BROW-003).

`choose_profile` decides which profile a session runs in; these tests hold the
launcher to that decision: a directory-backed choice is started as a
persistent context on that directory, a temporary one is deleted when the
session ends, and the default stays the in-memory context. Playwright is
faked - what is under test is which launch call the session makes and what it
does with the directory afterwards.
"""
from __future__ import annotations

import asyncio
import sys
import types
from pathlib import Path

import pytest

from friday import browser as B
from friday.toolsets import web as W


class _Page:
    def __init__(self, context):
        self.context = context
        self.url = "about:blank"


class _Context:
    def __init__(self, directory: str = ""):
        self.directory = directory
        self.closed = False

    async def new_page(self):
        return _Page(self)

    async def close(self):
        self.closed = True


class _Browser:
    def __init__(self):
        self.closed = False

    async def new_context(self, **kwargs):
        return _Context()

    async def close(self):
        self.closed = True


class _Chromium:
    def __init__(self):
        self.launches: list[dict] = []
        self.persistent: list[dict] = []

    async def launch(self, **kwargs):
        self.launches.append(kwargs)
        return _Browser()

    async def launch_persistent_context(self, directory, **kwargs):
        # Chromium creates the user-data dir on launch.
        Path(directory).mkdir(parents=True, exist_ok=True)
        (Path(directory) / "Cookies").write_text("session")
        self.persistent.append({"directory": directory, **kwargs})
        return _Context(directory)


class _Playwright:
    def __init__(self):
        self.chromium = _Chromium()
        self.stopped = False

    async def stop(self):
        self.stopped = True


class _Starter:
    def __init__(self, driver):
        self.driver = driver

    async def start(self):
        return self.driver


@pytest.fixture
def pw(monkeypatch, tmp_path):
    monkeypatch.setattr("friday.config.STATE_ROOT", tmp_path)
    driver = _Playwright()
    monkeypatch.setitem(sys.modules, "playwright.async_api",
                        types.SimpleNamespace(async_playwright=lambda: _Starter(driver)))
    return driver


@pytest.fixture
def fresh_session(monkeypatch):
    session = W.BrowserSession()
    monkeypatch.setattr(W, "session", session)
    return session


def _temporary() -> B.ProfileChoice:
    return B.choose_profile(worker="friday", authorized_by_approval=False,
                            temporary_by_approval=True)


def test_a_temporary_profile_is_launched_on_disk_and_deleted_when_the_session_ends(
        pw, fresh_session):
    choice = _temporary()

    async def scenario():
        await fresh_session.page(profile=choice)
        # Mid-session the profile exists on disk and nothing was launched in memory.
        assert Path(choice.directory).is_dir()
        assert fresh_session.profile is choice
        return await fresh_session.close()

    assert asyncio.run(scenario()) is True
    assert [p["directory"] for p in pw.chromium.persistent] == [choice.directory]
    assert pw.chromium.launches == [], "a directory-backed profile must not use the in-memory launch"
    assert not Path(choice.directory).exists(), "the throwaway profile outlived its session"
    assert fresh_session.profile is None and not fresh_session.running


def test_without_a_profile_the_session_stays_in_memory(pw, fresh_session):
    async def scenario():
        await fresh_session.page()
        return fresh_session.profile

    profile = asyncio.run(scenario())
    assert profile.kind == B.ISOLATED and profile.directory == ""
    assert len(pw.chromium.launches) == 1 and pw.chromium.persistent == []


def test_closing_never_deletes_an_authorized_profile(pw, fresh_session, tmp_path):
    mine = tmp_path / "MyChrome"
    choice = B.choose_profile(worker="friday", authorized_by_approval=True,
                              authorized_dir=str(mine))
    assert choice.kind == B.AUTHORIZED

    async def scenario():
        await fresh_session.page(profile=choice)
        await fresh_session.close()

    asyncio.run(scenario())
    assert pw.chromium.persistent[0]["directory"] == str(mine)
    assert (mine / "Cookies").read_text() == "session", "an authorized profile was deleted on close"


def test_a_running_session_refuses_a_different_profile(pw, fresh_session):
    async def scenario():
        await fresh_session.page()
        with pytest.raises(RuntimeError, match="already running with its isolated profile"):
            await fresh_session.page(profile=_temporary())
        assert fresh_session.profile.kind == B.ISOLATED
        # No profile named means "whatever is running" and is fine.
        return await fresh_session.page()

    assert asyncio.run(scenario()) is not None
    assert pw.chromium.persistent == []


def test_a_second_temporary_request_reuses_the_running_throwaway_profile(pw, fresh_session):
    first = _temporary()

    async def scenario():
        page = await fresh_session.page(profile=first)
        again = await fresh_session.page(profile=_temporary())
        assert again is page
        await fresh_session.close()

    asyncio.run(scenario())
    assert len(pw.chromium.persistent) == 1


def test_the_loop_reports_the_profile_the_session_started_with(pw, fresh_session):
    choice = _temporary()

    async def scenario():
        W._LOOP = None
        try:
            loop = await W._loop_browser(profile=choice)
            return loop.profile
        finally:
            W._LOOP = None
            await fresh_session.close()

    profile = asyncio.run(scenario())
    assert profile.kind == B.TEMPORARY and profile.directory == choice.directory


def test_a_dead_event_loop_still_deletes_its_temporary_profile(pw, fresh_session):
    """A session left behind by a closed loop is abandoned on the next call;
    the cookies in its throwaway profile must go with it."""
    choice = _temporary()
    asyncio.run(fresh_session.page(profile=choice))
    assert Path(choice.directory).is_dir()

    asyncio.run(fresh_session.page())

    assert not Path(choice.directory).exists()
    assert fresh_session.profile.kind == B.ISOLATED
