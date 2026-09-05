"""
Browser primitives and boundaries (PRD v3.1 FR-031, FR-033, FR-034,
FR-035, FR-036, FR-037).

Two layers of evidence:

  * the rule layer, against an in-memory driver (fast, deterministic);
  * `@live` E2E against real Chromium (Playwright) over a local HTTP
    fixture - every primitive the PRD names, plus the FR-034 / FR-035
    boundaries firing on real pages.
"""
from __future__ import annotations

import asyncio
import http.server
import socketserver
import threading
from pathlib import Path

import pytest

from friday import browser as B
from friday import policy as P

# ---------------------------------------------------------------------------
# In-memory driver
# ---------------------------------------------------------------------------

PAGES = {
    "https://shop.example/product": {
        "title": "Widget - Shop",
        "text": "Widget $10. Add to cart. Buy now with one click.",
        "elements": [
            {"tag": "button", "role": "button", "text": "Add to cart", "name": "add", "selector": "#add"},
            {"tag": "button", "role": "button", "text": "Buy now", "name": "buy", "selector": "#buy"},
            {"tag": "a", "role": "link", "text": "Reviews", "href": "https://shop.example/reviews", "selector": "a.reviews"},
            {"tag": "input", "role": "input", "text": "", "name": "q", "kind": "search", "selector": "#q"},
        ]},
    "https://shop.example/reviews": {"title": "Reviews", "text": "Great widget.", "elements": []},
    "https://social.example/compose": {
        "title": "Compose", "text": "What is happening?",
        "elements": [{"tag": "textarea", "role": "textarea", "text": "", "name": "status", "selector": "#status"},
                     {"tag": "button", "role": "button", "text": "Post now", "name": "post", "selector": "#post"}]},
    "https://settings.example/settings/security": {
        "title": "Security", "text": "Two-factor authentication",
        "elements": [{"tag": "button", "role": "button", "text": "Continue", "name": "c", "selector": "#c"}]},
    "https://bot.example/check": {
        "title": "Just a moment...",
        "text": "Checking your browser before accessing bot.example. Verify you are human. reCAPTCHA",
        "elements": [{"tag": "button", "role": "button", "text": "I am not a robot", "name": "cb", "selector": "#cb"}],
        "markers": ["g-recaptcha"]},
    "https://mail.example/inbox": {
        "title": "Inbox", "text": "token=sk-abcdefghijklmnopqrstuvwxyz1234 hello",
        "elements": [{"tag": "a", "role": "link", "text": "Compose", "selector": "#compose"}]},
}


class FakeDriver:
    def __init__(self, url="https://shop.example/product"):
        self.url = url
        self.log: list[tuple] = []
        self._tabs = [url]

    async def goto(self, url, timeout_ms):
        self.log.append(("goto", url))
        self.url = url
        self._tabs[0] = url
        return 200 if url in PAGES else 404

    async def current(self):
        return self.url, PAGES.get(self.url, {}).get("title", "")

    async def text(self):
        return PAGES.get(self.url, {}).get("text", "")

    async def elements(self):
        page = PAGES.get(self.url, {})
        return {"elements": page.get("elements", []), "forms": [],
                "markers": page.get("markers", [])}

    async def click(self, selector):
        self.log.append(("click", selector))
        if selector == "a.reviews":
            self.url = "https://shop.example/reviews"

    async def fill(self, selector, text, clear):
        self.log.append(("fill", selector, text))

    async def press(self, key):
        self.log.append(("press", key))

    async def scroll(self, dx, dy):
        self.log.append(("scroll", dx, dy))

    async def select(self, selector, value):
        self.log.append(("select", selector, value))
        return [value]

    async def upload(self, selector, paths):
        self.log.append(("upload", selector, tuple(paths)))

    async def download(self, selector, into, timeout_ms):
        p = Path(into) / "file.txt"
        p.write_text("x")
        return str(p)

    async def tabs(self):
        return [{"index": i, "url": u, "title": "", "active": u == self.url}
                for i, u in enumerate(self._tabs)]

    async def switch_tab(self, index):
        self.url = self._tabs[index]

    async def new_tab(self, url):
        self._tabs.append(url)
        self.url = url
        return len(self._tabs) - 1

    async def close_tab(self, index):
        self._tabs.pop(index)
        self.url = self._tabs[-1]

    async def screenshot(self, path):
        Path(path).write_bytes(b"\x89PNG")
        return path

    async def wait_for(self, condition, value, timeout_ms):
        return condition == "load"


def browser(driver=None, **kw) -> B.Browser:
    return B.Browser(driver or FakeDriver(),
                     profile=B.choose_profile(worker="test", authorized_by_approval=False), **kw)


def run(coro):
    return asyncio.run(coro)


# -- FR-031 / FR-036 ---------------------------------------------------------


def test_every_prd_primitive_exists_and_is_policy_classified():
    named = {"open", "inspect", "navigate", "click", "type", "scroll", "select",
             "upload", "download", "tabs", "screenshot", "wait", "verify"}
    assert set(B.PRIMITIVES) == named
    for prim, (category, changes) in B.PRIMITIVES.items():
        assert category in (P.BROWSER_CONTROL, P.BROWSER_AUTOMATION)
        assert changes == (category == P.BROWSER_AUTOMATION), prim


def test_every_action_has_an_observation_before_and_after():
    b = browser(approvals={"click": True, "type": True})
    run(b.run("open", args={"url": "https://shop.example/product"}))
    step = run(b.run("type", "q", {"text": "blue widget"}))
    assert step.ok and step.before is not None and step.after is not None
    assert step.before.observed_at <= step.after.observed_at
    step = run(b.run("click", "Reviews link", {"expect": {"url": "/reviews"}}))
    assert step.ok and step.after.url.endswith("/reviews")
    assert "navigated from https://shop.example/product" in step.evidence
    assert [s.primitive for s in b.steps] == ["open", "type", "click"]
    assert all(s.after is not None for s in b.steps)


def test_no_blind_coordinate_loop_a_missing_target_is_refused_not_guessed():
    b = browser(approvals={"click": True})
    run(b.run("open", args={"url": "https://shop.example/product"}))
    with pytest.raises(B.Refused) as exc:
        run(b.run("click", "the Nonexistent button"))
    assert "re-observe rather than guessing" in str(exc.value)
    assert b.driver.log == [("goto", "https://shop.example/product")]


def test_verify_fails_when_expectation_is_not_met():
    b = browser(approvals={"click": True})
    run(b.run("open", args={"url": "https://shop.example/product"}))
    step = run(b.run("click", "Reviews", {"expect": {"title": "Checkout"}}))
    assert not step.ok and "expected title to contain 'Checkout'" in step.evidence


def test_state_changing_primitives_need_session_approval_reads_do_not():
    b = browser()
    run(b.run("open", args={"url": "https://shop.example/product"}))
    assert run(b.run("inspect")).ok
    assert run(b.run("scroll", args={"dy": 400})).ok
    assert run(b.run("tabs", args={"op": "list"})).ok
    with pytest.raises(B.Refused) as exc:
        run(b.run("type", "q", {"text": "x"}))
    assert "not approved for this session" in str(exc.value)


# -- FR-034 -----------------------------------------------------------------


@pytest.mark.parametrize("url,target,text,kind", [
    ("https://shop.example/product", "Buy now", "", "purchase"),
    ("https://social.example/compose", "Post now", "", "publish"),
    ("https://settings.example/settings/security", "Continue", "", "security_settings"),
    ("https://social.example/compose", "status", "delete account permanently delete", "destructive"),
])
def test_a_signed_in_session_never_authorises_external_writes(url, target, text, kind):
    """The approvals dict says clicks/typing are fine for this session (as a
    signed-in profile would): the external-write boundary still fires."""
    b = B.Browser(FakeDriver(url),
                  profile=B.ProfileChoice(B.AUTHORIZED, "signed in", "C:/profile"),
                  approvals={"click": True, "type": True})
    run(b.observe())
    prim = "type" if text else "click"
    with pytest.raises(B.Refused) as exc:
        run(b.run(prim, target, {"text": text} if text else {}))
    assert exc.value.step.external_write == kind
    assert exc.value.step.policy == B.EXTERNAL_WRITE_POLICY
    assert "being signed in does not authorise it" in str(exc.value)
    assert not any(e[0] in ("click", "fill") for e in b.driver.log)


def test_an_exact_action_confirmation_lets_the_external_write_through():
    b = B.Browser(FakeDriver(), profile=B.ProfileChoice(B.AUTHORIZED, "s", "C:/p"),
                  approvals={"click": True})
    run(b.observe())
    step = run(b.run("click", "Buy now", {"confirmed": True}))
    assert step.ok and step.external_write == "purchase"
    assert ("click", "#buy") in b.driver.log


def test_ordinary_clicks_on_a_shop_are_not_purchases():
    b = browser(approvals={"click": True})
    run(b.run("open", args={"url": "https://shop.example/product"}))
    step = run(b.run("click", "Add to cart"))
    assert step.ok and step.external_write == ""


# -- FR-035 -----------------------------------------------------------------


def test_human_verification_is_a_handoff_never_a_thing_to_click_through():
    b = B.Browser(FakeDriver("https://bot.example/check"),
                  profile=B.choose_profile(worker="w", authorized_by_approval=False),
                  approvals={"click": True})
    state = run(b.observe())
    assert B.human_verification(state)
    with pytest.raises(B.Handoff) as exc:
        run(b.run("click", "I am not a robot"))
    assert "hand the tab to the user or use an approved API path" in str(exc.value)
    assert not any(e[0] == "click" for e in b.driver.log)
    # Reads are still fine: the person can be told what the page says.
    assert run(b.run("inspect")).ok


def test_landing_on_a_verification_page_after_an_action_is_flagged():
    d = FakeDriver()

    async def click(selector):
        d.url = "https://bot.example/check"
    d.click = click
    b = browser(d, approvals={"click": True})
    run(b.run("open", args={"url": "https://shop.example/product"}))
    step = run(b.run("click", "Reviews"))
    assert step.handoff and "HANDOFF" in step.evidence


# -- FR-033 -----------------------------------------------------------------


def test_workers_get_isolated_profiles_unless_the_approval_named_authorized():
    w = B.choose_profile(worker="hermes:friday", authorized_by_approval=False,
                         authorized_dir="C:/Users/x/profile")
    assert w.kind == B.ISOLATED and w.directory == ""
    a = B.choose_profile(worker="friday", authorized_by_approval=True,
                         authorized_dir="C:/Users/x/profile")
    assert a.kind == B.AUTHORIZED and a.directory == "C:/Users/x/profile"
    none = B.choose_profile(worker="friday", authorized_by_approval=True, authorized_dir="")
    assert none.kind == B.ISOLATED


# -- FR-037 / boundaries -----------------------------------------------------


def test_observation_is_structured_first_and_secrets_are_redacted():
    b = browser()
    run(b.run("open", args={"url": "https://mail.example/inbox"}))
    state = b.last
    assert state.perception == "structured"
    assert state.elements[0].text == "Compose"
    assert "sk-abcdefghijklmnopqrstuvwxyz1234" not in state.text
    assert state.redactions >= 1 and state.text == "[SECRET-REDACTED] hello"


def test_sensitive_domains_are_refused_before_observation(monkeypatch):
    from friday import sensitive_domains as S
    monkeypatch.setattr(S, "refusal", lambda url: "BLOCKED_SENSITIVE_DOMAIN: bank"
                        if "bank" in url else "")
    monkeypatch.setattr(S, "is_sensitive", lambda url: "bank" in url)
    monkeypatch.setattr(S, "enabled", lambda: True)
    b = browser(FakeDriver("https://bank.example/accounts"))
    with pytest.raises(B.Refused):
        run(b.observe())
    assert b.last.perception == "blocked" and b.last.text == ""
    b2 = browser()
    with pytest.raises(B.Refused):
        run(b2.run("open", args={"url": "https://bank.example/login"}))


def test_target_resolution_prefers_exact_then_prefix_then_contains():
    state = B.PageState(url="u", title="t", text="", elements=[
        B.Element(0, "button", "button", "Sign in with Google", selector="#g"),
        B.Element(1, "button", "button", "Sign in", selector="#s"),
        B.Element(2, "a", "link", "Docs", selector="#d"),
    ])
    assert state.find("Sign in").selector == "#s"
    assert state.find("the Sign in button").selector == "#s"
    assert state.find("google").selector == "#g"
    assert state.find("#raw").selector == "#raw"
    assert state.find("nothing here") is None


# ---------------------------------------------------------------------------
# Live E2E against real Chromium over a local fixture
# ---------------------------------------------------------------------------

FIXTURE = """<!doctype html><html><head><title>Fixture Home</title></head><body>
<h1>Fixture</h1>
<form id="f" action="/result" method="get">
  <input id="q" name="q" placeholder="Search" type="search">
  <select id="colour" name="colour"><option value="red">Red</option><option value="blue">Blue</option></select>
  <input id="up" name="up" type="file">
  <button id="go" type="submit">Search</button>
</form>
<a id="dl" href="/download.txt" download>Download report</a>
<a id="next" href="/second">Second page</a>
<button id="buy">Buy now</button>
<a id="captcha" href="/captcha">Captcha page</a>
<div style="height:3000px"></div><p id="bottom">bottom marker</p>
</body></html>"""

SECOND = "<html><head><title>Second Page</title></head><body><p>You made it</p></body></html>"
CAPTCHA = ("<html><head><title>Just a moment...</title></head><body>"
           "<p>Checking your browser before accessing. Verify you are human.</p>"
           "<div class='g-recaptcha'></div><button id='cb'>I am not a robot</button></body></html>")


class _Handler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *a):  # silence
        pass

    def do_GET(self):
        path = self.path.split("?")[0]
        if path == "/":
            body, ctype = FIXTURE, "text/html"
        elif path == "/second":
            body, ctype = SECOND, "text/html"
        elif path == "/captcha":
            body, ctype = CAPTCHA, "text/html"
        elif path == "/result":
            body, ctype = f"<html><head><title>Result</title></head><body>query={self.path}</body></html>", "text/html"
        elif path == "/download.txt":
            body, ctype = "report contents", "text/plain"
        else:
            self.send_response(404)
            self.end_headers()
            return
        data = body.encode()
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        if path == "/download.txt":
            self.send_header("Content-Disposition", "attachment; filename=report.txt")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


@pytest.fixture
def fixture_server():
    with socketserver.TCPServer(("127.0.0.1", 0), _Handler) as srv:
        port = srv.server_address[1]
        t = threading.Thread(target=srv.serve_forever, daemon=True)
        t.start()
        yield f"http://127.0.0.1:{port}"
        srv.shutdown()


@pytest.mark.live
def test_live_every_primitive_against_real_chromium(fixture_server, tmp_path, monkeypatch):
    from friday import netguard
    # The fixture is on loopback; netguard's private-address refusal is the
    # right default for the internet and the wrong one for this harness.
    monkeypatch.setattr(netguard, "check", lambda url: None)
    from playwright.async_api import async_playwright

    async def scenario():
        async with async_playwright() as pw:
            chromium = await pw.chromium.launch(headless=True)
            context = await chromium.new_context(accept_downloads=True)
            page = await context.new_page()
            b = B.Browser(B.PlaywrightDriver(context, page),
                          profile=B.choose_profile(worker="e2e", authorized_by_approval=False),
                          shots_dir=tmp_path / "shots",
                          approvals={"click": True, "type": True, "select": True,
                                     "upload": True, "download": True})
            base = fixture_server
            s = await b.run("open", args={"url": base + "/", "expect": {"title": "Fixture Home"}})
            assert s.ok, s.evidence
            assert b.last.perception == "structured" and any(e.text == "Search" for e in b.last.elements)
            assert (await b.run("inspect")).ok and "bottom marker" in b.last.text
            s = await b.run("screenshot")
            assert s.ok and Path(b.last.screenshot).stat().st_size > 1000
            s = await b.run("type", "Search", {"text": "widgets"})
            assert s.ok, s.detail
            s = await b.run("select", "#colour", {"value": "blue"})
            assert s.ok and "blue" in s.detail
            f = tmp_path / "up.txt"
            f.write_text("payload")
            s = await b.run("upload", "#up", {"paths": [str(f)]})
            assert s.ok, s.detail
            s = await b.run("scroll", args={"dy": 2500})
            assert s.ok
            s = await b.run("wait", args={"condition": "text", "value": "bottom marker"})
            assert s.ok, s.detail
            s = await b.run("click", "Search", {"expect": {"url": "/result?"}})
            assert s.ok, s.evidence
            assert "q=widgets" in b.last.url and "colour=blue" in b.last.url
            s = await b.run("navigate", args={"url": base + "/", "expect": {"title": "Fixture"}})
            assert s.ok
            s = await b.run("download", "Download report", {"into": str(tmp_path)})
            assert s.ok and Path(s.detail).read_text() == "report contents"
            s = await b.run("tabs", args={"op": "new", "url": base + "/second"})
            assert s.ok and len(b.last.tabs) == 2 and b.last.title == "Second Page"
            s = await b.run("tabs", args={"op": "switch", "index": 0})
            assert s.ok and b.last.title == "Fixture Home"
            s = await b.run("tabs", args={"op": "close", "index": 1})
            assert s.ok and len(b.last.tabs) == 1
            s = await b.run("verify", args={"expect": {"title": "Fixture Home"}})
            assert s.ok
            # FR-034 on a real page: a signed-in session would still be refused.
            with pytest.raises(B.Refused) as exc:
                await b.run("click", "Buy now")
            assert exc.value.step.external_write == "purchase"
            # FR-035 on a real page: the captcha page is a handoff.
            s = await b.run("click", "Captcha page")
            assert s.handoff and "human verification" in s.handoff
            with pytest.raises(B.Handoff):
                await b.run("click", "I am not a robot")
            transcript = b.transcript()
            assert len(transcript) >= 16
            assert all(t["before"] is not None or t["primitive"] in ("open",) for t in transcript)
            assert all(t["after"] is not None for t in transcript)
            await chromium.close()
    asyncio.run(scenario())


@pytest.mark.live
def test_live_toolset_face_runs_the_loop_over_the_shared_session(fixture_server, monkeypatch):
    """`toolsets.web.browser_act` - the MCP tool's implementation - against
    real Chromium, headless, through the policy gate."""
    from friday import contracts as c
    from friday import netguard
    from friday import policy as P
    from friday.toolsets import web as W

    monkeypatch.setattr(netguard, "check", lambda url: None)
    monkeypatch.setenv("ADA_BROWSER_HEADLESS", "true")
    engine = P.PolicyEngine(autonomy=P.FULL)

    async def scenario():
        run = c.Run.create("browser", capability="browser_act")
        base = fixture_server
        r = await W.browser_act(run, "open", args={"url": base + "/", "expect": {"title": "Fixture"}},
                                engine=engine)
        assert r.status == c.SUCCEEDED, r.error
        assert r.output["page"]["perception"] == "structured"
        r = await W.browser_act(run, "type", "Search", {"text": "abc"}, engine=engine)
        assert r.status == c.SUCCEEDED, r.error
        r = await W.browser_act(run, "click", "Buy now", engine=engine)
        assert r.status == c.PARTIAL and "APPROVAL_REQUIRED" in r.error
        assert r.output["external_write"] == "purchase"
        r = await W.browser_act(run, "click", "Captcha page", engine=engine)
        assert r.status == c.SUCCEEDED and "HANDOFF" in r.verification.evidence
        r = await W.browser_act(run, "click", "I am not a robot", engine=engine)
        assert r.status == c.PARTIAL and "HUMAN_VERIFICATION" in r.error
        r = await W.browser_act(run, "teleport", engine=engine)
        assert r.status == c.FAILED and "unknown browser primitive" in r.error
        await W.browser_close(run, engine=engine)
    asyncio.run(scenario())
