"""CUA-004: a computer-use action is done only when an independent read of
the application's state shows the change, not because the backend said so."""
from __future__ import annotations

import asyncio
import os
import time
import types
from pathlib import Path

import pytest

from friday import browser as B
from friday import contracts as c
from friday import honesty
from friday import netguard
from friday import policy as P
from friday.computer_use_verify import (
    ELEMENT, FILE, PAGE, WINDOW, Expectation, FailureRecord, aconfirm, asnapshot,
    changes_state, confirm, expectation_for, snapshot, url_matches, verify,
    verify_action)
from friday.toolsets import desktop, vision
from friday.toolsets import web as W


def _ok(tool_id: str, output=None) -> c.ActionResult:
    """What a backend hands back when it says the action worked."""
    started = c.started("run-1", tool_id)
    return c.succeeded(started, output=output or {"ok": True},
                       verification=c.Verification("backend", "backend said ok"))


class Screen:
    """A fake read of application state, standing in for a backend read."""

    def __init__(self, state=None):
        self.state = state or {}
        self.reads: list[tuple[str, str]] = []

    def __call__(self, kind: str, target: str):
        self.reads.append((kind, target))
        return self.state.get((kind, target))


class AsyncScreen(Screen):
    async def __call__(self, kind: str, target: str):
        return Screen.__call__(self, kind, target)


# -- the verifier -------------------------------------------------------------


def test_unchanged_state_after_reported_success_is_a_failure():
    seen = Screen()  # the page never navigated
    records: list[FailureRecord] = []
    want = Expectation(PAGE, "url", "example.com/pricing")
    result = confirm(_ok("browser.navigate"), want, seen, record=records.append)
    assert result.status == c.FAILED
    assert not result.may_claim_completion
    assert "reported success" in result.error
    assert result.output["backend_reported"] == {"ok": True}
    assert [(r.tool_id, r.run_id, r.kind) for r in records] == [
        ("browser.navigate", "run-1", PAGE)]
    assert records[0].observed is None


def test_wrong_state_after_reported_success_is_a_failure():
    seen = Screen({(PAGE, "url"): "https://example.com/home"})
    result = confirm(_ok("browser.navigate"),
                     Expectation(PAGE, "url", "example.com/pricing"), seen)
    assert result.status == c.FAILED
    assert "example.com/home" in result.error


def test_matching_state_keeps_success_and_carries_the_observation():
    seen = Screen({(PAGE, "url"): "https://example.com/pricing?x=1"})
    result = confirm(_ok("browser.navigate"),
                     Expectation(PAGE, "url", "example.com/pricing"), seen)
    assert result.status == c.SUCCEEDED
    assert seen.reads == [(PAGE, "url")]
    # The proof is the independent read, and it satisfies ACT-001's shape.
    assert "post_state=verified" in result.verification.evidence
    assert honesty.proof_gap(result) == ""


def test_a_backend_report_that_is_not_success_is_not_second_guessed():
    seen = Screen()
    started = c.started("run-1", "browser.navigate")
    failed = c.failed(started, "timeout")
    assert confirm(failed, Expectation(PAGE, "url", "x"), seen) is failed
    assert seen.reads == []


def test_absence_is_checked_for_a_close():
    gone = confirm(_ok("browser.close"),
                   Expectation(PAGE, "session", present=False), Screen())
    assert gone.status == c.SUCCEEDED
    still = confirm(_ok("browser.close"),
                    Expectation(PAGE, "session", present=False),
                    Screen({(PAGE, "session"): "open"}))
    assert still.status == c.FAILED


def test_each_kind_reads_its_own_state():
    state = Screen({(WINDOW, "foreground"): "Untitled - Notepad",
                    (ELEMENT, "#q"): "friday",
                    (FILE, "C:/tmp/a.txt"): "exists"})
    assert verify(Expectation(WINDOW, "foreground", "Notepad"), state).verified
    assert verify(Expectation(ELEMENT, "#q", "friday"), state).verified
    assert verify(Expectation(FILE, "C:/tmp/a.txt"), state).verified
    assert not verify(Expectation(WINDOW, "foreground", "Calculator"), state).verified


def test_an_unreadable_backend_leaves_the_action_unverified_not_succeeded():
    def broken(kind, target):
        raise RuntimeError("backend read failed")

    verdict = verify(Expectation(PAGE, "url", "x"), broken)
    assert not verdict.verified
    assert "backend read failed" in verdict.reason
    result = confirm(_ok("browser.navigate"), Expectation(PAGE, "url", "x"), broken)
    assert result.status == c.FAILED


def test_a_state_changing_action_with_no_expectation_cannot_claim_success():
    result = verify_action(_ok("browser.open"), {}, Screen())
    assert result.status == c.PARTIAL
    assert not result.may_claim_completion
    assert "no independent post-state check" in result.error


def test_a_read_only_primitive_needs_no_expectation():
    read = _ok("browser.inspect")
    assert verify_action(read, {}, Screen()) is read


def test_verify_action_derives_the_expectation_and_fails_a_no_op():
    result = verify_action(_ok("browser.navigate"),
                           {"url": "https://example.com/pricing"}, Screen())
    assert result.status == c.FAILED


def test_a_non_computer_use_action_passes_through_without_expectation():
    plain = _ok("files.write")
    assert confirm(plain, None, Screen()) is plain


def test_classification_follows_the_owning_primitive_table():
    # tabs is read-only in browser.PRIMITIVES; click is not.
    assert not changes_state("browser.tabs")
    assert changes_state("browser.click")
    assert changes_state("browser.open") and changes_state("desktop.step")
    assert not changes_state("browser.inspect") and not changes_state("files.write")


@pytest.mark.parametrize("tool_id, arguments, want", [
    ("browser.open", {"url": "https://example.com/a/"},
     Expectation(PAGE, "url", "https://example.com/a/")),
    ("browser.navigate", {"url": "http://example.com/b"},
     Expectation(PAGE, "url", "http://example.com/b")),
    ("browser.close", {}, Expectation(PAGE, "session", present=False)),
    ("desktop.step", {"expect_window": "Notepad"},
     Expectation(WINDOW, "foreground", "Notepad")),
])
def test_expectation_is_derived_per_action(tool_id, arguments, want):
    assert expectation_for(tool_id, arguments) == want


def test_an_action_without_a_stated_outcome_has_no_expectation():
    assert expectation_for("desktop.step", {}) is None
    assert expectation_for("files.write", {"path": "x"}) is None


# -- the url must be the page asked for ---------------------------------------


def test_a_login_redirect_carrying_the_target_in_its_query_is_not_arrival():
    landed = "https://example.com/login?next=https://example.com/pricing"
    assert not url_matches(landed, "https://example.com/pricing")
    result = confirm(_ok("browser.navigate"),
                     Expectation(PAGE, "url", "https://example.com/pricing"),
                     Screen({(PAGE, "url"): landed}))
    assert result.status == c.FAILED


@pytest.mark.parametrize("observed, wanted, ok", [
    ("https://www.example.com/pricing/", "https://example.com/pricing", True),
    ("https://example.com/pricing?x=1#top", "example.com/pricing", True),
    ("https://example.com/abc", "https://example.com/a", False),
    ("https://example.com/en/", "https://example.com", True),       # bare host
    ("https://evil.test/example.com/pricing", "https://example.com/pricing", False),
    ("https://docs.example.com/pricing", "https://example.com/pricing", False),
])
def test_url_matching_is_host_and_path(observed, wanted, ok):
    assert url_matches(observed, wanted) is ok


# -- the intended change, not a state that was already there ------------------


def test_a_download_into_an_existing_unchanged_file_is_a_failure(tmp_path):
    target = tmp_path / "report.pdf"
    target.write_text("old")

    def signature(kind, path):
        p = Path(path)
        return f"{p.stat().st_size}:{p.stat().st_mtime_ns}" if p.exists() else None

    want = Expectation(FILE, str(target), changed=True)
    before = snapshot(want, signature)
    # The backend reports a download; the file was not touched.
    result = confirm(_ok("browser.download"), want, signature, before=before)
    assert result.status == c.FAILED
    assert "exactly as it did before" in result.error
    # A download that did rewrite it is a success.
    target.write_text("new, longer content")
    after = confirm(_ok("browser.download"), want, signature, before=before)
    assert after.status == c.SUCCEEDED


def test_text_typed_into_a_field_that_already_held_it_is_not_a_change():
    field = Screen({(ELEMENT, "#q"): "friday"})
    want = Expectation(ELEMENT, "#q", "friday", changed=True)
    result = confirm(_ok("browser.type"), want, field, before=snapshot(want, field))
    assert result.status == c.FAILED
    empty = Screen({(ELEMENT, "#q"): ""})
    before = snapshot(want, empty)
    empty.state[(ELEMENT, "#q")] = "friday"
    assert confirm(_ok("browser.type"), want, empty, before=before).status == c.SUCCEEDED


def test_an_unreadable_baseline_cannot_show_a_change():
    calls = iter([RuntimeError("no baseline")])

    def flaky(kind, target):
        for item in calls:
            raise item
        return "friday"

    want = Expectation(ELEMENT, "#q", "friday", changed=True)
    result = confirm(_ok("browser.type"), want, flaky, before=snapshot(want, flaky))
    assert result.status == c.FAILED
    assert "could not be read" in result.error


# -- an async backend read ----------------------------------------------------


def test_an_async_observer_is_awaited_not_blocked_on():
    async def scenario():
        seen = AsyncScreen({(PAGE, "url"): "https://example.com/login"})
        want = Expectation(PAGE, "url", "https://example.com/pricing")
        assert (await asnapshot(want, seen)) is None
        bad = await aconfirm(_ok("browser.navigate"), want, seen)
        seen.state[(PAGE, "url")] = "https://example.com/pricing"
        good = await aconfirm(_ok("browser.navigate"), want, seen)
        return bad, good

    bad, good = asyncio.run(scenario())
    assert bad.status == c.FAILED and good.status == c.SUCCEEDED


def test_the_sync_confirm_refuses_an_async_observer_as_unverified():
    result = confirm(_ok("browser.navigate"), Expectation(PAGE, "url", "x"),
                     AsyncScreen({(PAGE, "url"): "x"}))
    assert result.status == c.FAILED and "async" in result.error


# -- wired into the live toolsets ---------------------------------------------


class FakePage:
    def __init__(self, session):
        self.session, self.url = session, "about:blank"

    async def goto(self, url, timeout=None):
        self.url = self.session.landing
        return types.SimpleNamespace(status=200)

    async def title(self):
        return "A page"

    async def inner_text(self, selector):
        return "body"


class FakeSession:
    """Stands in for web.BrowserSession: the backend says what it likes."""

    def __init__(self, landing="", really_closes=True):
        self.landing, self.really_closes, self._page = landing, really_closes, None

    @property
    def running(self):
        return self._page is not None

    async def page(self, *, headless=None, profile=None):
        if self._page is None:
            self._page = FakePage(self)
        return self._page

    async def close(self):
        was = self.running
        if self.really_closes:
            self._page = None
        return was


@pytest.fixture
def toolset(monkeypatch):
    monkeypatch.setattr(W, "_gate", lambda *a, **k: None)
    monkeypatch.setattr(netguard, "check", lambda url: None)

    def use(session):
        monkeypatch.setattr(W, "session", session)
        return c.Run.create("browser", capability="browser_open")
    return use


def test_browser_open_that_landed_on_a_login_page_is_recorded_failed(toolset):
    session = FakeSession("https://example.com/login?next=https://example.com/pricing")
    run = toolset(session)
    result = asyncio.run(W.browser_open(run, "https://example.com/pricing"))
    assert result.status == c.FAILED
    assert "backend reported success" in result.error
    assert run.results[-1] is result and not run.all_succeeded


def test_browser_open_that_arrived_is_a_verified_success(toolset):
    run = toolset(FakeSession("https://www.example.com/pricing"))
    result = asyncio.run(W.browser_open(run, "https://example.com/pricing"))
    assert result.status == c.SUCCEEDED
    assert "post_state=verified" in result.verification.evidence


def test_browser_close_that_left_the_session_running_is_recorded_failed(toolset):
    session = FakeSession("https://example.com/", really_closes=False)
    run = toolset(session)
    asyncio.run(W.browser_open(run, "https://example.com/"))
    result = asyncio.run(W.browser_close(run))
    assert result.status == c.FAILED and session.running
    honest = FakeSession("https://example.com/")
    run = toolset(honest)
    asyncio.run(W.browser_open(run, "https://example.com/"))
    assert asyncio.run(W.browser_close(run)).status == c.SUCCEEDED


class StuckDriver:
    """A driver whose click and download say ok and change nothing."""

    def __init__(self, into=None):
        self.url = "https://shop.example/product"
        self.into = into

    async def goto(self, url, timeout_ms):
        self.url = url
        return 200

    async def current(self):
        return self.url, "Product"

    async def text(self):
        return "A product"

    async def elements(self):
        return {"elements": [], "forms": [], "markers": []}

    async def tabs(self):
        return [{"index": 0, "url": self.url, "title": "", "active": True}]

    async def click(self, selector):
        pass

    async def download(self, selector, into, timeout_ms):
        return str(Path(into) / "file.txt")


@pytest.fixture
def act(monkeypatch):
    monkeypatch.setattr(netguard, "check", lambda url: None)

    def use(driver):
        loop = B.Browser(driver, profile=B.choose_profile(
            worker="test", authorized_by_approval=False))

        async def fake_loop(*, headless=None, profile=None):
            return loop
        monkeypatch.setattr(W, "_loop_browser", fake_loop)
        return c.Run.create("browser", capability="browser_act")
    return use


def test_a_click_the_page_does_not_show_is_recorded_failed_not_partial(act):
    run = act(StuckDriver())
    engine = P.PolicyEngine(autonomy=P.FULL)

    async def scenario():
        await W.browser_act(run, "open", args={"url": "https://shop.example/product"},
                            engine=engine)
        return await W.browser_act(
            run, "click", "#reviews", {"expect": {"url": "shop.example/reviews"}},
            engine=engine)

    result = asyncio.run(scenario())
    assert result.status == c.FAILED
    assert "backend reported success" in result.error


def test_every_expect_key_is_checked_not_only_the_first(act):
    run = act(StuckDriver())
    engine = P.PolicyEngine(autonomy=P.FULL)

    async def scenario():
        await W.browser_act(run, "open", args={"url": "https://shop.example/product"},
                            engine=engine)
        # url is true, title is not: the later key must still count.
        return await W.browser_act(
            run, "click", "#x",
            {"expect": {"url": "shop.example/product", "title": "Checkout"}},
            engine=engine)

    assert asyncio.run(scenario()).status == c.FAILED


def test_a_download_into_an_untouched_existing_file_is_recorded_failed(act, tmp_path):
    stale = tmp_path / "file.txt"
    stale.write_text("old")
    os.utime(stale, (946684800, 946684800))        # long ago; the driver does not touch it
    run = act(StuckDriver())
    engine = P.PolicyEngine(autonomy=P.FULL)

    async def scenario():
        await W.browser_act(run, "open", args={"url": "https://shop.example/product"},
                            engine=engine)
        return await W.browser_act(run, "download", "#dl", {"into": str(tmp_path)},
                                   engine=engine)

    result = asyncio.run(scenario())
    assert result.status == c.FAILED
    assert "unchanged by the download" in result.error


def test_a_download_that_wrote_the_file_is_a_success(act, tmp_path):
    class Writing(StuckDriver):
        async def download(self, selector, into, timeout_ms):
            path = Path(into) / "file.txt"
            path.write_text("fresh bytes")
            return str(path)

    (tmp_path / "file.txt").write_text("old")
    os.utime(tmp_path / "file.txt", (946684800, 946684800))
    run = act(Writing())
    engine = P.PolicyEngine(autonomy=P.FULL)

    async def scenario():
        await W.browser_act(run, "open", args={"url": "https://shop.example/product"},
                            engine=engine)
        return await W.browser_act(run, "download", "#dl", {"into": str(tmp_path)},
                                   engine=engine)

    assert asyncio.run(scenario()).status == c.SUCCEEDED


def _desktop_step(monkeypatch, foreground_title, expect_window):
    frames = iter([vision.Frame(b"before", 10, 10, "screen"),
                   vision.Frame(b"after", 10, 10, "screen")])
    monkeypatch.setattr(desktop.ABORT, "is_set", lambda: False)
    monkeypatch.setattr(desktop, "_foreground", lambda: 7)
    monkeypatch.setattr(desktop, "_title", lambda hwnd: foreground_title)
    monkeypatch.setattr(desktop.native, "AVAILABLE", True, raising=False)
    monkeypatch.setattr(desktop.native, "SendInput", object(), raising=False)
    monkeypatch.setattr(desktop.native, "send_key", lambda *a, **k: None)
    monkeypatch.setattr(vision, "capture_screen", lambda **k: next(frames))
    monkeypatch.setattr(desktop.time, "sleep", lambda s: None)
    run = c.Run.create("take over")
    desktop._PLANS[run.run_id] = {
        "task": "open notes", "monitor": 1, "index": 0, "active": True,
        "at": time.monotonic(), "hwnd": 0,
        "steps": [{"action": "key", "text": "enter", "say": "Opening.",
                   "expect_window": expect_window}]}
    try:
        return desktop.desktop_step(
            run, engine=P.PolicyEngine(autonomy=P.FULL)), run
    finally:
        desktop._PLANS.clear()


def test_a_desktop_step_that_left_the_wrong_window_in_front_is_failed(monkeypatch):
    result, run = _desktop_step(monkeypatch, "Calculator", "Notepad")
    assert result.status == c.FAILED
    assert "backend reported success" in result.error
    assert run.results[-1] is result


def test_a_desktop_step_that_left_the_named_window_in_front_succeeds(monkeypatch):
    result, _ = _desktop_step(monkeypatch, "Untitled - Notepad", "Notepad")
    assert result.status == c.SUCCEEDED
    assert "post_state=verified" in result.verification.evidence
