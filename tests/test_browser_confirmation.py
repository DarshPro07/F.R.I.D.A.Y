"""
A browser external write needs a yes the person gave, not a flag a caller
wrote (PRD v7 TRUST-001 and SEC-004; FR-034).

`browser_act` is an MCP tool. Its `args` come from the model, and through the
model from whatever page it last read. So a `confirmed` or `approved` key in
those arguments cannot be what authorises a purchase, a publish, a deletion
or a security change. The capability runtime already applies that rule to
durable approvals ("anything a model could write into arguments on its own is
not an approval; the record is"). The browser now does the same thing
`files_delete` does: the first call asks through `friday.confirmation` and
does nothing; only the person's answer approves the nonce; the nonce is spent
once, on exactly the action it was asked for.
"""
from __future__ import annotations

import asyncio

import pytest

from friday import browser as B
from friday import confirmation as CF
from friday import contracts as c
from friday.policy import PolicyEngine
from friday.toolsets import web as W
from friday.toolsets.system import APPROVAL_PREFIX

SHOP = "https://shop.example/product"
PAGES = {
    SHOP: {
        "title": "Widget - Shop",
        "text": "Widget $10. Add to cart. Buy now. Delete account.",
        "elements": [
            {"tag": "button", "role": "button", "text": "Add to cart", "name": "add",
             "selector": "#add"},
            {"tag": "button", "role": "button", "text": "Buy now", "name": "buy",
             "selector": "#buy"},
            {"tag": "button", "role": "button", "text": "Delete account", "name": "del",
             "selector": "#del"},
        ]},
}


class FakeDriver:
    """In-memory driver, the same shape as the one in
    tests/test_browser_primitives.py, kept local so a change there cannot
    quietly change what this file asserts."""

    def __init__(self, url=SHOP):
        self.url = url
        self.log: list[tuple] = []

    async def goto(self, url, timeout_ms):
        self.log.append(("goto", url))
        self.url = url
        return 200 if url in PAGES else 404

    async def current(self):
        return self.url, PAGES.get(self.url, {}).get("title", "")

    async def text(self):
        return PAGES.get(self.url, {}).get("text", "")

    async def elements(self):
        page = PAGES.get(self.url, {})
        return {"elements": page.get("elements", []), "forms": [], "markers": []}

    async def click(self, selector):
        self.log.append(("click", selector))

    async def fill(self, selector, text, clear):
        self.log.append(("fill", selector, text))

    async def press(self, key):
        self.log.append(("press", key))

    async def scroll(self, dx, dy):
        self.log.append(("scroll", dx, dy))

    async def screenshot(self, path):
        return path


def _run(coro):
    return asyncio.run(coro)


def _clicks(driver: FakeDriver) -> list[str]:
    return [entry[1] for entry in driver.log if entry[0] == "click"]


@pytest.fixture(autouse=True)
def _fresh_book():
    CF.reset()
    yield
    CF.reset()


# --- the rule layer: friday/browser.py ---------------------------------------


@pytest.mark.parametrize("claimed", [
    {"confirmed": True},
    {"approved": True},
    {"confirmed": "yes", "approved": 1, "nonce": "anything"},
])
def test_a_yes_written_into_the_arguments_does_not_authorise_an_external_write(claimed):
    """Session approval for clicks is in place (a signed-in, approved
    session). The page's Buy now is still refused, and nothing is clicked,
    however the arguments describe themselves."""
    browser = B.Browser(FakeDriver(), profile=B.ProfileChoice(B.AUTHORIZED, "signed in", "C:/p"),
                        approvals={"click": True})
    with pytest.raises(B.Refused) as exc:
        _run(browser.run("click", "Buy now", dict(claimed)))
    assert exc.value.step.external_write == "purchase"
    assert "being signed in does not authorise it" in str(exc.value)
    assert _clicks(browser.driver) == []


def test_an_approved_flag_in_the_arguments_is_not_a_session_approval():
    """Session approval lives in the loop's own `approvals`, which the
    runtime sets after the policy engine allows the primitive. A caller
    writing `approved` into the arguments has not been through that."""
    browser = B.Browser(FakeDriver(), profile=B.ProfileChoice(B.AUTHORIZED, "s", "C:/p"),
                        approvals={})
    with pytest.raises(B.Refused) as exc:
        _run(browser.run("click", "Add to cart", {"approved": True}))
    assert "not approved for this session" in str(exc.value)
    assert _clicks(browser.driver) == []


def test_a_refused_step_does_not_record_the_claimed_authority():
    """The step's recorded arguments are what the action used. A claimed
    `confirmed` that was ignored must not sit in the record looking like a
    confirmation that happened."""
    browser = B.Browser(FakeDriver(), profile=B.ProfileChoice(B.AUTHORIZED, "s", "C:/p"),
                        approvals={"click": True})
    with pytest.raises(B.Refused) as exc:
        _run(browser.run("click", "Buy now", {"confirmed": True, "approved": True,
                                              "nonce": "n"}))
    assert not set(exc.value.step.args) & {"confirmed", "approved", "nonce"}


# --- the toolset: friday/toolsets/web.py browser_act -------------------------


@pytest.fixture()
def shop(monkeypatch):
    """`browser_act` over an in-memory shop page, with a policy engine that
    allows clicking (FULL autonomy answers the ASK). What remains is exactly
    the external-write confirmation this file is about."""
    driver = FakeDriver()
    loop = B.Browser(driver, profile=B.ProfileChoice(B.AUTHORIZED, "signed in", "C:/p"),
                     approvals={})

    async def _loop_browser(*, headless=None, profile=None):
        return loop

    monkeypatch.setattr(W, "_loop_browser", _loop_browser)
    engine = PolicyEngine(autonomy="full")
    assert engine.decide("browser.automate").allowed, \
        "precondition: clicking itself is allowed, so only the confirmation can refuse"
    return driver, engine


def _act(run, target, args, engine):
    return _run(W.browser_act(run, "click", target, args, engine=engine))


def test_an_external_write_asks_and_does_nothing_even_when_the_arguments_claim_a_yes(shop):
    driver, engine = shop
    run = c.Run.create("buy the widget", capability="web")
    result = _act(run, "Buy now", {"confirmed": True, "approved": True}, engine)
    assert result.error.startswith(APPROVAL_PREFIX)
    confirm = result.output["confirm"]
    assert confirm["state"] == CF.PENDING
    assert _clicks(driver) == []
    assert not result.may_claim_completion


def test_a_nonce_nobody_approved_is_refused(shop):
    """Holding the nonce lets the model say which question is being answered.
    It does not let the model answer it."""
    driver, engine = shop
    run = c.Run.create("buy the widget", capability="web")
    nonce = _act(run, "Buy now", {}, engine).output["confirm"]["nonce"]
    result = _act(run, "Buy now", {"nonce": nonce}, engine)
    assert result.status == c.CANCELLED
    assert "has not been approved yet" in result.error
    assert _clicks(driver) == []


def test_a_made_up_nonce_is_refused(shop):
    driver, engine = shop
    run = c.Run.create("buy the widget", capability="web")
    result = _act(run, "Buy now", {"nonce": "made-up"}, engine)
    assert result.status == c.CANCELLED
    assert "no confirmation 'made-up' is pending" in result.error
    assert _clicks(driver) == []


def test_the_persons_yes_lets_exactly_that_action_through_once(shop):
    """The green half: without it, the tests above could all be satisfied by
    a browser that refuses every purchase forever."""
    driver, engine = shop
    run = c.Run.create("buy the widget", capability="web")
    nonce = _act(run, "Buy now", {}, engine).output["confirm"]["nonce"]
    assert CF.book.approve(nonce).ok            # what the person's answer does

    done = _act(run, "Buy now", {"nonce": nonce}, engine)
    assert done.status == c.SUCCEEDED, done.error
    assert _clicks(driver) == ["#buy"]

    again = _act(run, "Buy now", {"nonce": nonce}, engine)
    assert again.status == c.CANCELLED
    assert "already been used" in again.error
    assert _clicks(driver) == ["#buy"]


def test_a_yes_for_one_action_cannot_be_spent_on_another(shop):
    """A yes to buying the widget is not a yes to deleting the account, even
    on the same page, in the same run, a second later."""
    driver, engine = shop
    run = c.Run.create("buy the widget", capability="web")
    nonce = _act(run, "Buy now", {}, engine).output["confirm"]["nonce"]
    assert CF.book.approve(nonce).ok

    wrong = _act(run, "Delete account", {"nonce": nonce}, engine)
    assert wrong.status == c.CANCELLED
    assert "that yes was for" in wrong.error
    assert _clicks(driver) == []


# --- the MCP boundary: friday/tools/web_control.py ---------------------------


class _FakeMCP:
    def __init__(self):
        self.tools = {}

    def tool(self):
        def register(fn):
            self.tools[fn.__name__] = fn
            return fn
        return register


def test_the_mcp_tool_spends_the_yes_on_the_run_that_asked(shop, monkeypatch, tmp_path):
    """Every MCP call makes a fresh Run, and a confirmation is bound to the
    run that asked. The wrapper therefore reuses that run for the call that
    carries the nonce - as `files_delete` does - or the person's yes could
    never be spent. The flag in the arguments is still worth nothing."""
    from friday.store import Store
    from friday.tools import web_control as WC

    driver, engine = shop
    monkeypatch.setattr(WC, "_engine", engine)
    monkeypatch.setattr(WC, "_store", Store(str(tmp_path / "web.sqlite3")))
    mcp = _FakeMCP()
    WC.register(mcp)
    browser_act = mcp.tools["browser_act"]

    asked = _run(browser_act("click", "Buy now", {"confirmed": True}))
    assert asked["error"].startswith(APPROVAL_PREFIX)
    assert _clicks(driver) == []
    nonce = asked["output"]["confirm"]["nonce"]

    assert CF.book.approve(nonce).ok
    done = _run(browser_act("click", "Buy now", {"nonce": nonce}))
    assert done["status"] == c.SUCCEEDED, done.get("error")
    assert _clicks(driver) == ["#buy"]


def test_another_tools_nonce_does_not_borrow_that_tools_run(shop, monkeypatch, tmp_path):
    """A nonce another tool minted (here a permanent delete) fails consume, and
    the wrapper must not save this call under that tool's run id either."""
    from friday.store import Store
    from friday.tools import web_control as WC

    driver, engine = shop
    monkeypatch.setattr(WC, "_engine", engine)
    monkeypatch.setattr(WC, "_store", Store(str(tmp_path / "web.sqlite3")))
    foreign = CF.book.ask("run-delete-1", "files.delete_permanent", "C:/x.txt", "Delete?")
    assert CF.book.approve(foreign.nonce).ok
    mcp = _FakeMCP()
    WC.register(mcp)

    out = _run(mcp.tools["browser_act"]("click", "Buy now", {"nonce": foreign.nonce}))
    assert out["run_id"] != "run-delete-1"
    assert out["status"] == c.CANCELLED
    assert "that yes was for" in out["error"]
    assert _clicks(driver) == []
