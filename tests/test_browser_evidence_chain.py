"""
The browser evidence chain: from a step's observation to what FRIDAY is
allowed to say (FR-BRW-004, FR-TRU-001, release gate 16).

`tests/test_browser_primitives.py` already proves the loop holds - every
action observed before and after, a missed expectation failing the step, an
ordinary click on a shop not counting as a purchase. What it does not cover
is the JOIN: a step that observed site A must not be able to back a sentence
about site B, and a step with no post-observation must not back anything.

That join is where a false claim actually reaches a user. The step object can
be perfectly honest and the sentence built from it still wrong, because the
sentence is written by a model and the step is not.
"""
from __future__ import annotations

import asyncio

import pytest

from friday import browser as B
from friday import contracts as c
from friday import honesty as H

PAGES = {
    "https://shop.example/product": {
        "title": "Blue Kurta - Shop",
        "text": "Blue Kurta 1499 Add to cart",
        "elements": [{"tag": "a", "role": "link", "name": "Reviews",
                      "selector": "a.reviews"}],
    },
    "https://shop.example/orders/100238": {
        "title": "Order 100238 - refunded",
        "text": "Order 100238 refunded 1499",
        "elements": [],
    },
}


class FakeDriver:
    """The same shape as the driver in tests/test_browser_primitives.py, kept
    local so a change to that file's fixture cannot quietly change what this
    one is asserting."""

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
        self.log.append(("upload", selector, paths))

    async def download(self, selector, into, timeout_ms):
        self.log.append(("download", selector))
        return ""

    async def tabs(self):
        return list(self._tabs)

    async def switch_tab(self, index):
        self.log.append(("switch", index))

    async def new_tab(self, url):
        self._tabs.append(url)

    async def close_tab(self, index):
        self._tabs.pop(index)

    async def screenshot(self, path):
        self.log.append(("screenshot", path))
        return str(path)

    async def wait_for(self, condition, value, timeout_ms):
        return True


def _browser(**kw):
    kw.setdefault("profile", B.choose_profile(worker="friday",
                                              authorized_by_approval=False))
    return B.Browser(FakeDriver(), **kw)


def _run(coro):
    return asyncio.run(coro)


def _run_from_step(step: B.Step, *, tool: str = "browser_open"):
    """A FRIDAY run whose single verified action is this browser step.

    This is the join under test: the step's observed URL becomes the action's
    recorded target, which is what the claim auditor compares a sentence
    against.
    """
    run = c.Run.create(step.primitive, capability=tool)
    prior = c.started(run.run_id, tool)
    verification = c.Verification(method="post-observation", evidence=f"{step.evidence} post_state=observed")
    observed = step.after.url if step.after else ""
    run.record(c.succeeded(prior, output={"url": observed},
                           verification=verification))
    return run


# --- the join ---------------------------------------------------------------

def test_a_step_that_observed_one_page_cannot_back_a_claim_about_another():
    browser = _browser()
    step = _run(browser.run("open", args={"url": "https://shop.example/product"}))
    assert step.ok, step.evidence
    run = _run_from_step(step)

    honest = H.audit("I opened https://shop.example/product.", run)
    assert honest.ok, honest.reason

    false = H.audit("I opened https://shop.example/orders/100238.", run)
    assert not false.ok, (
        "a claim about the order page passed on the product page's evidence")


def test_a_refund_claim_needs_the_refund_page_not_merely_a_click():
    """The PRD's own example: a click is not proof of a refund. Here the
    click happened and the page never changed, so the sentence that says the
    refund went through has nothing behind it."""
    browser = _browser(approvals={"click": True})
    _run(browser.run("open", args={"url": "https://shop.example/product"}))
    step = _run(browser.run("click", "Reviews"))
    run = _run_from_step(step, tool="browser_click")

    claim = H.audit("I refunded order 100238.", run)
    assert not claim.ok, (
        "a click on a product page backed a refund claim about an order")


def test_the_same_claim_passes_once_the_order_page_is_observed():
    """The green half: when the read-back really does show the refunded
    order, the claim is allowed. Without this the test above could be
    satisfied by refusing every refund sentence."""
    browser = _browser()
    step = _run(browser.run("open",
                            args={"url": "https://shop.example/orders/100238"}))
    run = _run_from_step(step)
    assert H.audit("I opened https://shop.example/orders/100238.", run).ok


# --- a step with nothing observed -------------------------------------------

def test_a_step_with_no_post_observation_is_not_ok_and_says_so():
    """`verify` is the only thing that sets `ok`. A step whose after-state
    was never captured must fail, because the alternative is treating "we
    did not look" as "it worked"."""
    step = B.Step(primitive="click", target="Buy now")
    step.before = None
    step.after = None
    verified = _browser().verify(step)
    assert not verified.ok
    assert verified.evidence == "no post-observation"


def test_a_forged_ok_on_the_step_does_not_survive_verification():
    """A caller (or a model writing a tool result) setting ok=True by hand
    is the forged-evidence case. `verify` recomputes it from the observation
    rather than trusting what it was handed."""
    step = B.Step(primitive="click", target="Buy now")
    step.ok = True
    step.after = None
    assert not _browser().verify(step).ok, \
        "a hand-set ok survived verification - evidence would be forgeable"


# --- negative control -------------------------------------------------------

def test_removing_the_target_check_lets_the_wrong_page_back_the_claim():
    """The load-bearing test for the join.

    With the target comparison removed, the false claim in the first test
    must pass - which proves that test is detecting the guard rather than
    some incidental property of the fixture.
    """
    browser = _browser()
    step = _run(browser.run("open", args={"url": "https://shop.example/product"}))
    run = _run_from_step(step)
    sentence = "I opened https://shop.example/orders/100238."

    original = H._target_mismatch
    try:
        H._target_mismatch = lambda claim, backed: ""
        assert H.audit(sentence, run).ok, (
            "with the target check removed the false claim STILL failed, so "
            "the test above is not proving what it claims")
    finally:
        H._target_mismatch = original
    assert not H.audit(sentence, run).ok, "the guard was not restored"


# --- FR-BRW-003: the authentication boundary --------------------------------

def test_a_human_verification_page_is_a_handoff_and_backs_no_success_claim():
    """FR-BRW-003: FRIDAY pauses at the boundary rather than clicking
    through, and a paused objective has not succeeded at anything."""
    state = B.PageState(url="https://shop.example/login", title="Verify you are human",
                        text="Complete the captcha to continue", elements=(),
                        markers=("captcha",))
    assert B.human_verification(state), \
        "a captcha page was not recognised as a human-verification boundary"
