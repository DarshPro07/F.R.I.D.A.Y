"""The outbound circuit breaker.

Behavioural tests only: the point of a breaker is what it does to callers, so
these drive the public surface and the real `crawl_one` / `web_fetch` paths
rather than poking at internals.
"""

from __future__ import annotations

import asyncio

import httpx
import pytest

from friday import breaker


@pytest.fixture(autouse=True)
def _clean():
    breaker.reset()
    yield
    breaker.reset()


class _Boom(Exception):
    """Stands in for a transport failure by name."""


class ConnectTimeout(Exception):
    pass


class HTTPStatusError(Exception):
    pass


# ---------------------------------------------------------------------------
# The state machine
# ---------------------------------------------------------------------------


def test_a_healthy_host_is_never_refused():
    for _ in range(20):
        breaker.allow("https://example.com/a")
    assert breaker.state_of("https://example.com/")["state"] == "closed"


def test_it_opens_only_after_the_threshold():
    url = "https://dead.example/x"
    for _ in range(breaker.THRESHOLD - 1):
        breaker.record_failure(url)
        breaker.allow(url)          # still closed: must not raise

    breaker.record_failure(url)     # this is the one that trips it
    with pytest.raises(breaker.CircuitOpen) as caught:
        breaker.allow(url)
    assert caught.value.host == "dead.example"
    assert caught.value.failures == breaker.THRESHOLD


def test_one_success_closes_it_again():
    url = "https://flaky.example/x"
    for _ in range(breaker.THRESHOLD):
        breaker.record_failure(url)
    with pytest.raises(breaker.CircuitOpen):
        breaker.allow(url)

    breaker.record_success(url)
    breaker.allow(url)              # must not raise
    assert breaker.state_of(url)["state"] == "closed"


def test_hosts_are_isolated_from_each_other():
    dead = "https://dead.example/x"
    live = "https://live.example/x"
    for _ in range(breaker.THRESHOLD):
        breaker.record_failure(dead)

    with pytest.raises(breaker.CircuitOpen):
        breaker.allow(dead)
    breaker.allow(live)             # a neighbour's outage is not ours


def test_after_the_cooldown_exactly_one_probe_is_allowed(monkeypatch):
    url = "https://slow.example/x"
    for _ in range(breaker.THRESHOLD):
        breaker.record_failure(url, now=0.0)

    with pytest.raises(breaker.CircuitOpen):
        breaker.allow(url, now=1.0)

    later = breaker.COOLDOWN_SECONDS + 1.0
    breaker.allow(url, now=later)   # the probe gets through
    with pytest.raises(breaker.CircuitOpen):
        breaker.allow(url, now=later)   # everyone else keeps failing fast


def test_seconds_left_is_reported_for_the_ui():
    url = "https://dead.example/x"
    for _ in range(breaker.THRESHOLD):
        breaker.record_failure(url)
    state = breaker.state_of(url)
    assert state["state"] == "open"
    assert 0 < state["seconds_left"] <= breaker.COOLDOWN_SECONDS


# ---------------------------------------------------------------------------
# What counts as a failure
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("exc", [
    httpx.ConnectTimeout("no route"),
    httpx.ReadTimeout("too slow"),
    httpx.ConnectError("refused"),
])
def test_transport_failures_count(exc):
    assert breaker.is_transport_failure(exc) is True


def test_an_http_status_is_not_a_transport_failure():
    # A 404 or 403 means the host is alive and answering. Tripping on those
    # would blind Friday to working sites that simply refuse one path.
    request = httpx.Request("GET", "https://example.com/")
    response = httpx.Response(404, request=request)
    exc = httpx.HTTPStatusError("404", request=request, response=response)
    assert breaker.is_transport_failure(exc) is False


def test_an_unparseable_url_is_ignored_rather_than_crashing():
    breaker.allow("not a url")          # must not raise
    breaker.record_failure("not a url")
    assert breaker.state_of("not a url")["state"] == "closed"


# ---------------------------------------------------------------------------
# The context manager
# ---------------------------------------------------------------------------


def test_guard_records_success_and_failure():
    url = "https://guarded.example/x"

    with breaker.guard(url):
        pass
    assert breaker.state_of(url)["failures"] == 0

    for _ in range(breaker.THRESHOLD):
        with pytest.raises(httpx.ConnectError):
            with breaker.guard(url):
                raise httpx.ConnectError("down")

    with pytest.raises(breaker.CircuitOpen):
        with breaker.guard(url):
            pass


def test_guard_never_swallows_the_original_error():
    with pytest.raises(ValueError):
        with breaker.guard("https://example.com/"):
            raise ValueError("the caller's own bug")


# ---------------------------------------------------------------------------
# The real call paths
# ---------------------------------------------------------------------------


def test_crawl_one_fails_fast_once_a_host_is_open():
    """The crawler must skip an open host without opening a connection."""
    from friday.toolsets import research

    url = "https://unreachable.example/page"
    for _ in range(breaker.THRESHOLD):
        breaker.record_failure(url)

    class _NoCalls:
        async def get(self, *a, **k):        # pragma: no cover - must not run
            raise AssertionError("an open circuit must not make a request")

    out = asyncio.run(research.crawl_one(_NoCalls(), url, max_chars=100))
    assert out["ok"] is False
    assert out.get("skipped") is True
    assert "not retrying" in out["error"]
