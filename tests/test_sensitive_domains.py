"""
The banking/payment observation block, proven at its production seams.

The rule (governance pack, docs/security/): page content from a financial
domain never enters model context. The guard is `friday/sensitive_domains.py`
and it is wired into `web.fetch`, `browser.open`, `browser.inspect`,
`browser.automate` and the research crawler.

Two kinds of test here:

  1. the classifier itself - suffix matching, env extension, kill switch;
  2. the wiring - the production call sites refuse *before* any network or
     browser work happens, proven without a network by the fact that a
     blocked call returns instantly with the marker and records no fetch.

And one mutation-style check: with the guard disabled the same URL is NOT
refused by the classifier, so a test passing with the protection removed
would fail - the gate is proven to gate.
"""
from __future__ import annotations
import asyncio
import pytest
from friday import contracts as c
from friday import sensitive_domains as sd


def test_known_bank_is_sensitive():
    assert sd.is_sensitive('https://www.hdfcbank.com/personal')
    assert sd.is_sensitive('https://netbanking.hdfcbank.com/login')
    assert sd.is_sensitive('https://paypal.com/signin')
    assert sd.is_sensitive('http://onlinesbi.sbi')


def test_suffix_matches_on_label_boundaries_only():
    assert not sd.is_sensitive('https://hdfcbank.com.evil.example/')
    assert not sd.is_sensitive('https://nothdfcbank.com/')


def test_ordinary_sites_are_not_sensitive():
    assert not sd.is_sensitive('https://github.com/')
    assert not sd.is_sensitive('https://docs.python.org/3/')
    assert not sd.is_sensitive('')
    assert not sd.is_sensitive('not a url')


def test_user_extension_via_env(monkeypatch):
    monkeypatch.setenv(sd.ENV_EXTRA, 'mybank.example, other.co.in')
    assert sd.is_sensitive('https://portal.mybank.example/accounts')
    assert sd.is_sensitive('https://other.co.in/')
    monkeypatch.delenv(sd.ENV_EXTRA)
    assert not sd.is_sensitive('https://portal.mybank.example/accounts')


def test_refusal_carries_the_marker_and_no_path():
    reason = sd.refusal('https://netbanking.hdfcbank.com/acct/12345?token=x')
    assert reason.startswith(sd.MARKER)
    assert '12345' not in reason
    assert 'token' not in reason


def test_kill_switch_disables_the_guard(monkeypatch):
    """The mutation control: with the guard off, nothing is refused.

    This is what proves the other tests are testing the guard rather than
    some coincidental failure - the same URL flips verdict with the switch.
    """
    url = 'https://www.hdfcbank.com/'
    assert sd.refusal(url)
    monkeypatch.setenv(sd.ENV_ENABLED, 'false')
    assert sd.refusal(url) == ''


def _run() -> c.Run:
    return c.Run.create('test')


def test_web_fetch_refuses_before_any_network(monkeypatch):
    from friday.toolsets import web

    class Boom:
        def __init__(self, *a, **k):
            raise AssertionError('network touched for a blocked domain')
    monkeypatch.setattr(web.httpx, 'AsyncClient', Boom)
    result = asyncio.run(web.web_fetch(_run(), 'https://icicibank.com/login'))
    assert result.status == c.FAILED
    assert sd.MARKER in (result.error or '')


def test_browser_open_refuses_before_launching_a_browser(monkeypatch):
    from friday.toolsets import web

    async def boom(*a, **k):
        raise AssertionError('browser launched for a blocked domain')
    monkeypatch.setattr(web.session, 'page', boom)
    result = asyncio.run(web.browser_open(_run(), 'https://paypal.com/myaccount'))
    assert result.status == c.FAILED
    assert sd.MARKER in (result.error or '')


def test_browser_automate_refuses_a_blocked_start_url():
    from friday.toolsets import web
    result = asyncio.run(web.browser_automate(_run(), 'check my balance', start_url='https://chase.com/'))
    assert result.status == c.FAILED
    assert sd.MARKER in (result.error or '')


def test_crawler_refuses_a_blocked_url():
    from friday.toolsets.research import crawl_one

    async def go():
        return await crawl_one(None, 'https://zerodha.com/portfolio', max_chars=1000)
    page = asyncio.run(go())
    assert page['ok'] is False
    assert sd.MARKER in page['error']


def test_browser_inspect_refuses_when_the_open_page_is_financial(monkeypatch):
    from friday.toolsets import web

    class FakePage:
        url = 'https://netbanking.hdfcbank.com/dashboard'

        async def title(self):
            return 'My Bank'

        async def inner_text(self, sel):
            raise AssertionError('page content read for a blocked domain')

    async def fake_page(*a, **k):
        return FakePage()
    monkeypatch.setattr(web.session, 'page', fake_page)
    monkeypatch.setattr(type(web.session), 'running', property(lambda s: True))
    result = asyncio.run(web.browser_inspect(_run()))
    assert result.status == c.FAILED
    assert sd.MARKER in (result.error or '')