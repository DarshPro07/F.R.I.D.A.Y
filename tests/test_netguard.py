"""
The outbound boundary: what is refused, and when it is checked.

The distinction the whole module exists for:

    check()   may say VALID_BUT_UNRESOLVED. A catalogue must not fail
              ingestion because DNS blinked.
    fetch()   resolves again, immediately, and connects to the address it
              just validated. It never accepts UNRESOLVED, and never follows
              a redirect it has not put through the same checks.
"""

from __future__ import annotations

import ipaddress

import pytest

from friday import netguard as N

# ---------------------------------------------------------------------------
# Addresses
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("address,reason", [
    ("127.0.0.1", "loopback"),
    ("127.9.9.9", "loopback"),
    ("::1", "loopback"),
    ("169.254.169.254", "metadata"),
    ("169.254.170.2", "metadata"),
    ("100.100.100.100", "metadata"),
    ("169.254.1.1", "link-local"),
    ("10.0.0.1", "private"),
    ("192.168.1.1", "private"),
    ("172.16.0.1", "private"),
    ("0.0.0.0", "unspecified"),
    ("0.1.2.3", "not public"),
    ("100.64.0.1", "not public"),
    ("224.0.0.1", "multicast"),
    ("fe80::1", "link-local"),
    ("fc00::1", "not public"),
    ("fd00:ec2::254", "metadata"),
])
def test_non_public_addresses_are_refused(address, reason):
    refusal = N.address_refusal(ipaddress.ip_address(address))
    assert refusal, f"{address} was allowed"
    assert reason in refusal, f"{address}: {refusal!r} does not mention {reason!r}"


@pytest.mark.parametrize("address", ["1.1.1.1", "8.8.8.8", "93.184.216.34",
                                     "2606:4700:4700::1111"])
def test_ordinary_public_addresses_are_allowed(address):
    assert N.address_refusal(ipaddress.ip_address(address)) == ""


def test_the_metadata_endpoint_is_caught_in_its_ipv6_spelling():
    """
    The case that actually needed unwrapping, found by measuring rather than
    assuming. `ipaddress` DOES see through mapped addresses for is_loopback
    and is_private, so those are fine either way - but the metadata denylist
    compares strings, and `::ffff:169.254.169.254` is not the string
    `169.254.169.254`. Without normalisation the most dangerous address in the
    file is missed in one of its two spellings.
    """
    mapped = ipaddress.ip_address("::ffff:169.254.169.254")
    assert str(mapped) not in N.METADATA_ADDRESSES, "the trap this guards is gone"
    assert "metadata" in N.address_refusal(mapped)


@pytest.mark.parametrize("address", ["::ffff:127.0.0.1", "::ffff:10.0.0.1",
                                     "2002:7f00:1::"])
def test_mapped_and_6to4_forms_of_local_addresses_are_refused(address):
    assert N.address_refusal(ipaddress.ip_address(address))


# ---------------------------------------------------------------------------
# check(): syntax and resolution
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("url,reason", [
    ("", "empty"),
    ("file:///c:/windows/win.ini", "scheme"),
    ("ftp://example.com/a", "scheme"),
    ("gopher://example.com", "scheme"),
    ("http:///nohost", "no host"),
    ("http://localhost/a", "this machine"),
    ("http://127.0.0.1/a", "loopback"),
    ("http://[::1]/a", "loopback"),
    ("http://169.254.169.254/latest/meta-data/", "metadata"),
    ("http://10.1.2.3/a", "private"),
    ("http://user:pass@example.com/a", "credentials"),
])
def test_urls_that_are_refused_outright(url, reason):
    with pytest.raises(N.UrlRefused, match=reason):
        N.check(url)


def test_an_ordinary_url_resolves_and_is_public():
    verdict = N.check("https://example.com/a.jpg")
    assert verdict["verdict"] == N.PUBLIC
    assert verdict["host"] == "example.com"
    assert verdict["addresses"]


def test_an_unresolvable_host_is_valid_but_unresolved_at_validation_time():
    """
    A product feed must not fail ingestion because DNS was unreachable. This
    is a real answer, not a pass.
    """
    verdict = N.check("https://nonexistent-host.invalid/a.jpg")
    assert verdict["verdict"] == N.UNRESOLVED
    assert verdict["addresses"] == []


def test_the_same_url_is_refused_when_resolution_is_required():
    """Which is the state fetch() uses. Two questions, two answers."""
    with pytest.raises(N.UrlRefused, match="does not resolve"):
        N.check("https://nonexistent-host.invalid/a.jpg", require_resolution=True)


def test_a_private_address_can_be_allowed_deliberately():
    assert N.check("http://10.1.2.3/a", allow_private=True)["verdict"] == N.PUBLIC


def test_allowing_private_does_not_also_allow_loopback_or_metadata():
    """The escape hatch must stay the size it was opened to."""
    for url in ("http://127.0.0.1/a", "http://169.254.169.254/x"):
        with pytest.raises(N.UrlRefused):
            N.check(url, allow_private=True)


# ---------------------------------------------------------------------------
# fetch(): the connection-time boundary
# ---------------------------------------------------------------------------


def test_fetch_refuses_a_url_that_check_refuses():
    with pytest.raises(N.UrlRefused, match="loopback"):
        N.fetch("http://127.0.0.1:9/x")


def test_fetch_refuses_an_unresolvable_host_rather_than_trying():
    with pytest.raises(N.UrlRefused, match="does not resolve"):
        N.fetch("https://nonexistent-host.invalid/a.jpg")


def test_fetch_does_not_follow_redirects_automatically(monkeypatch):
    """
    The destination of a redirect is chosen by the server being fetched. A
    validated URL that 302s to the metadata endpoint is the same attack with
    one more step, so every hop is revalidated.
    """
    hops = []

    class FakeResponse:
        def __init__(self, redirect_to=None):
            self.is_redirect = redirect_to is not None
            self.headers = {"location": redirect_to} if redirect_to else {}
            self.status_code = 302 if redirect_to else 200

        def iter_bytes(self):
            yield b"ok"

        def close(self):
            pass

    class FakeClient:
        def __init__(self, *a, **k):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def build_request(self, method, url, **kwargs):
            hops.append(url)
            return url

        def send(self, request, **kwargs):
            if len(hops) == 1:
                return FakeResponse("http://169.254.169.254/latest/meta-data/")
            return FakeResponse()

    import httpx
    monkeypatch.setattr(httpx, "Client", FakeClient)

    with pytest.raises(N.UrlRefused, match="metadata"):
        N.fetch("https://example.com/start")
    assert len(hops) == 1, "it connected to the redirect destination"


def test_the_redirect_chain_is_capped(monkeypatch):
    class FakeResponse:
        is_redirect = True
        status_code = 302
        headers = {"location": "/next"}

        def close(self):
            pass

    class FakeClient:
        def __init__(self, *a, **k):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def build_request(self, method, url, **kwargs):
            return url

        def send(self, request, **kwargs):
            return FakeResponse()

    import httpx
    monkeypatch.setattr(httpx, "Client", FakeClient)

    with pytest.raises(N.UrlRefused, match="more than"):
        N.fetch("https://example.com/a", max_redirects=2)


def test_the_request_is_aimed_at_the_validated_address_not_the_name():
    """
    What closes the rebinding window rather than narrowing it: no second DNS
    lookup happens, so a second answer cannot be used. Host and SNI keep TLS
    verification pointed at the real name.
    """
    import httpx

    with httpx.Client() as client:
        request = N._pinned_request(
            client, "https://example.com/a.jpg", "93.184.216.34",
            "example.com", {})

    assert "93.184.216.34" in str(request.url), "it connected to the hostname"
    assert request.headers["Host"] == "example.com"
    assert request.extensions["sni_hostname"] == "example.com"


def test_an_ipv6_address_is_bracketed_when_pinned():
    import httpx

    with httpx.Client() as client:
        request = N._pinned_request(
            client, "https://example.com/a", "2606:4700::1111", "example.com", {})
    assert "[2606:4700::1111]" in str(request.url)


def test_a_nonstandard_port_survives_pinning():
    import httpx

    with httpx.Client() as client:
        request = N._pinned_request(
            client, "https://example.com:8443/a", "93.184.216.34",
            "example.com", {})
    assert ":8443" in str(request.url)
    assert request.headers["Host"] == "example.com:8443"


@pytest.mark.live
def test_a_real_fetch_through_the_gate_works():
    """The boundary must not be so tight that nothing legitimate passes."""
    got = N.fetch("https://example.com/", timeout=20)
    assert got["status"] == 200
    assert got["content"]
    assert got["chain"] == ["https://example.com/"]
