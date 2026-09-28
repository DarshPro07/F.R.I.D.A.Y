"""
NET-001 (PRD v7 section 44): objective-bound, revocable, auditable network
scope.

Every compiled objective is issued a network grant in the store - audited on
issue and on revocation - and its envelope's NetworkScope carries the grant
id. netguard holds a run with an envelope to that scope: an expired scope,
a revoked grant and a host outside the scope are refused, the last before
any name is even resolved. The address rules - private, loopback, metadata -
apply inside every scope. A conversation, which has no envelope, keeps the
ordinary guard.

Nothing here touches the network: out-of-scope hosts are refused before
resolution, and in-scope ones are public IP literals.
"""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta, timezone

import pytest

from friday import contracts as c
from friday import netguard
from friday import objectives as O
from friday import trust
from friday.store import Store

PUBLIC_IP = "93.184.216.34"
LATER = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat(timespec="seconds")
PAST = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat(timespec="seconds")


def _scope(mode=c.NET_SELECTED_HOSTS, hosts=(PUBLIC_IP,), **fields) -> c.NetworkScope:
    return c.NetworkScope(mode=mode, hosts=hosts, objective_id="OBJ-1",
                          expires_at=fields.pop("expires_at", LATER), **fields)


def _no_dns(monkeypatch):
    def refuse(host):
        raise AssertionError(f"{host} was resolved; the scope should have refused first")
    monkeypatch.setattr(netguard, "resolve", refuse)


def test_a_host_outside_the_scope_is_refused_before_any_lookup(monkeypatch):
    _no_dns(monkeypatch)
    with pytest.raises(netguard.UrlRefused, match="outside this run's network scope"):
        netguard.check("https://exfil.example.org/upload", scope=_scope())


def test_a_host_inside_the_scope_passes():
    assert netguard.check(f"https://{PUBLIC_IP}/", scope=_scope())["verdict"] == netguard.PUBLIC


def test_an_expired_scope_is_refused(monkeypatch):
    _no_dns(monkeypatch)
    with pytest.raises(netguard.UrlRefused, match="expired"):
        netguard.check(f"https://{PUBLIC_IP}/", scope=_scope(expires_at=PAST))


def test_no_network_allows_nothing(monkeypatch):
    _no_dns(monkeypatch)
    with pytest.raises(netguard.UrlRefused, match="outside this run's network scope"):
        netguard.check(f"https://{PUBLIC_IP}/", scope=c.NetworkScope())


def test_the_address_rules_hold_inside_a_public_scope():
    with pytest.raises(netguard.UrlRefused):
        netguard.check("http://169.254.169.254/latest/meta-data/",
                       scope=_scope(mode=c.NET_PUBLIC_WEB_READ, hosts=()))


def test_a_run_without_an_envelope_keeps_the_ordinary_guard():
    assert netguard.run_scope(c.Run.create("fetch")) == {}


# --- grants: issued, audited, revoked across processes --------------------

@pytest.fixture
def stores(tmp_path):
    path = tmp_path / "grants.sqlite3"
    first, second = Store(path), Store(path)
    yield first, second
    first.close()
    second.close()


def _compile(store) -> str:
    created = O.compile_objective(
        store, request="read the news",
        tasks=[{"capability": "web_fetch", "arguments": {}}],
        manifest=[{"id": "web_fetch", "description": "fetch"}],
        objective_summary="read the news", provenance=c.PERSON, attended=True)
    return created["run_id"]


def _envelope(store, run_id) -> c.Envelope:
    return c.Envelope.from_dict(json.loads(store.objective_run(run_id)["envelope"]))


def test_compiling_an_objective_issues_an_audited_grant(stores):
    store, _ = stores
    run_id = _compile(store)
    grant_id = _envelope(store, run_id).network.grant_id
    assert grant_id and store.grant(grant_id)["objective_id"] == run_id
    issued = [row for row in trust.audit().query(limit=50)
              if row["action"] == "network.grant" and row["target"] == grant_id]
    assert issued and issued[0]["decision"] == "ISSUED"


def test_a_grant_revoked_in_another_process_is_refused_here(stores, monkeypatch):
    here, elsewhere = stores
    run_id = _compile(here)
    envelope = _envelope(here, run_id)
    O.revoke_network_grant(elsewhere, envelope.network.grant_id, reason="the person said stop")
    _no_dns(monkeypatch)
    with pytest.raises(netguard.UrlRefused, match="revoked"):
        netguard.check(f"https://{PUBLIC_IP}/", scope=envelope.network,
                       revoked=here.grant_revoked)
    revoked = [row for row in trust.audit().query(limit=50)
               if row["action"] == "network.grant" and row["decision"] == "REVOKED"]
    assert revoked, "the revocation was not audited"


def test_an_unknown_grant_counts_as_revoked(stores):
    """A grant the store has never heard of is not a licence."""
    store, _ = stores
    assert store.grant_revoked("GRANT-NOBODY-ISSUED")


def test_revoking_every_grant_at_once(stores):
    store, _ = stores
    grants = [_envelope(store, _compile(store)).network.grant_id for _ in range(3)]
    assert O.revoke_all_network_grants(store, reason="emergency stop") == 3
    assert all(store.grant_revoked(g) for g in grants)


# --- the toolsets hold a run to it ----------------------------------------

def test_the_web_toolset_holds_a_durable_run_to_its_scope(monkeypatch):
    from friday.toolsets import web as W

    _no_dns(monkeypatch)
    envelope = c.Envelope(run_id="RUN-WEB", allowed_capabilities=("web_fetch",),
                          network=_scope())
    run = c.Run(run_id="RUN-WEB", request="fetch", envelope=envelope)
    result = asyncio.run(W.web_fetch(run, "https://exfil.example.org/upload"))
    assert result.status == c.FAILED
    assert "outside this run's network scope" in result.error


def test_research_holds_a_durable_run_to_its_scope(monkeypatch):
    from friday.toolsets import research as R

    _no_dns(monkeypatch)
    envelope = c.Envelope(run_id="RUN-RES", allowed_capabilities=(c.ANY_CAPABILITY,),
                          network=_scope())
    run = c.Run(run_id="RUN-RES", request="research", envelope=envelope)

    async def crawl():
        import httpx
        async with httpx.AsyncClient() as client:
            return await R.crawl_one(client, "https://exfil.example.org/a",
                                     max_chars=100, net=netguard.run_scope(run))

    page = asyncio.run(crawl())
    assert page["ok"] is False and "outside this run's network scope" in page["error"]


def test_the_crawl4ai_engine_is_held_to_the_same_guard(monkeypatch):
    """V7-D14: the opt-in engine handed URLs to its own browser unchecked -
    no netguard, no sensitive-domain refusal - so a crafted search result
    could point it at cloud metadata."""
    import sys
    import types

    from friday.toolsets import research as R

    asked = []

    class _Result:
        def __init__(self, url):
            self.url = ("http://169.254.169.254/latest/meta-data/"
                        if url.endswith("/redirects") else url)
            self.success, self.markdown, self.metadata = True, "the page body", {}

    class _Crawler:
        def __init__(self, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def arun_many(self, urls):
            asked.extend(urls)
            return [_Result(u) for u in urls]

    monkeypatch.setitem(sys.modules, "crawl4ai", types.SimpleNamespace(AsyncWebCrawler=_Crawler))
    monkeypatch.setattr(R, "crawl_engine", lambda: "crawl4ai")
    pages = asyncio.run(R.crawl_all(
        ["http://169.254.169.254/latest/meta-data/", f"https://{PUBLIC_IP}/redirects"],
        max_chars=100, render=False))
    assert asked == [f"https://{PUBLIC_IP}/redirects"], "a refused URL reached the engine"
    assert pages and all(not page["ok"] for page in pages), pages
    assert "the page body" not in json.dumps(pages), "a refused landing page was read"


# --- browser navigation -----------------------------------------------------

def test_browser_navigation_is_held_to_the_run_scope(monkeypatch):
    from friday import browser_capability as bc

    _no_dns(monkeypatch)
    verdict = bc.classify_url("https://exfil.example.org/", net={"scope": _scope()})
    assert verdict.decision == bc.BLOCK_NETWORK
    assert "outside this run's network scope" in verdict.reason


def test_a_scope_that_cannot_be_checked_blocks_navigation():
    """classify_url tolerates an unexpected netguard error by skipping the
    network refinement. A scope that cannot be checked must not fail open."""
    from friday import browser_capability as bc

    def broken(grant_id):
        raise RuntimeError("store unavailable")

    verdict = bc.classify_url(f"https://{PUBLIC_IP}/",
                              net={"scope": _scope(grant_id="GRANT-X"), "revoked": broken})
    assert verdict.decision == bc.BLOCK_NETWORK


def test_the_browser_loop_passes_the_scope_to_navigation(monkeypatch):
    from friday import browser as B

    _no_dns(monkeypatch)
    browser = B.Browser(object(), profile=B.ProfileChoice(B.AUTHORIZED, "t", "C:/p"))
    with pytest.raises(B.Refused, match="outside this run's network scope"):
        browser.plan(B.NAVIGATE, "https://exfil.example.org/", net={"scope": _scope()})


# --- a finished objective keeps no network authority ------------------------

def test_a_finished_objective_releases_its_grant(stores):
    from friday.continuous import ContinuousTaskExecutor

    store, _ = stores
    run_id = _compile(store)
    grant_id = _envelope(store, run_id).network.grant_id

    async def port(capability_id, arguments, *, run=None):
        return c.succeeded(c.started(run.run_id, capability_id), "fetched",
                           verification=c.Verification(method="test", evidence="ok")).to_dict()

    async def go():
        executor = ContinuousTaskExecutor(store, call_capability=port)
        try:
            for _ in range(200):
                if store.objective_run(run_id)["status"] in O.RUN_TERMINAL:
                    return
                await executor.start(run_id)
                await asyncio.sleep(0.02)
        finally:
            executor.stop()

    asyncio.run(go())
    assert store.objective_run(run_id)["status"] in O.RUN_TERMINAL
    assert store.grant_revoked(grant_id), "a finished objective kept its network grant"


def test_a_cancelled_objective_releases_its_grant(stores):
    store, _ = stores
    run_id = _compile(store)
    grant_id = _envelope(store, run_id).network.grant_id
    O.cancel_run(store, run_id=run_id, reason="the person cancelled it", executor_id="test")
    assert store.grant_revoked(grant_id)
    revoked = [row for row in trust.audit().query(limit=50)
               if row["action"] == "network.grant" and row["target"] == grant_id
               and row["decision"] == "REVOKED"]
    assert revoked, "the release was not audited"
