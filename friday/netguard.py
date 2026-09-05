"""
The outbound network boundary.

    A URL IS NEVER FETCHED SOLELY BECAUSE IT PASSED AN EARLIER VALIDATION.

Validation-time safety and connection-time safety are different properties,
and conflating them is the whole DNS-rebinding attack: a hostname resolves to
a public address while a row is being checked, and to `127.0.0.1` a moment
later when the client actually connects. Nothing about the first answer
constrains the second.

So this module splits them:

    check(url)          syntax, and resolution *if it can*. May legitimately
                        return VALID_BUT_UNRESOLVED - a catalogue must not
                        fail validation because DNS was briefly unreachable.
    fetch(url)          resolves again, immediately, and connects to the
                        address it just validated rather than to a name it
                        re-resolves. Redirects are never followed
                        automatically; each destination goes through the whole
                        check again.

Connecting to the validated address is what makes this more than a smaller
race. The request is sent to the IP, with `Host:` set to the real hostname and
`sni_hostname` set so TLS still verifies against the name - measured working
against a live host before being relied on. A second DNS answer arriving
between the check and the connection changes nothing, because no second
lookup happens.

This matters more as Friday and Forge start producing URLs themselves. A URL
from a product feed, from a model, and from a web page are equally untrusted;
none of them is evidence about where it points.
"""

from __future__ import annotations

import ipaddress
import logging
import socket
from urllib.parse import urlsplit, urlunsplit

logger = logging.getLogger("friday.netguard")

PUBLIC = "public"
UNRESOLVED = "valid_but_unresolved"

ALLOWED_SCHEMES = ("http", "https")

#: Names meaning "this machine" that are not written as addresses.
LOCAL_NAMES = frozenset({
    "localhost", "localhost.localdomain", "ip6-localhost", "ip6-loopback",
})

#: Cloud instance-metadata endpoints. Every one of these is a request for
#: credentials wearing an ordinary URL's clothes, and each is reachable from
#: inside a machine that has no other reason to be interesting.
METADATA_ADDRESSES = frozenset({
    "169.254.169.254",      # AWS, Azure, GCP, DigitalOcean, Oracle
    "169.254.170.2",        # AWS ECS task metadata
    "100.100.100.100",      # Alibaba Cloud
    "192.0.0.192",          # Oracle Cloud legacy
    "fd00:ec2::254",        # AWS IMDS over IPv6
})

#: Ranges `ipaddress` does not flag on its own.
EXTRA_DENIED = (
    ipaddress.ip_network("0.0.0.0/8"),        # "this network"
    ipaddress.ip_network("100.64.0.0/10"),    # carrier-grade NAT
    ipaddress.ip_network("192.0.0.0/24"),     # IETF protocol assignments
    ipaddress.ip_network("198.18.0.0/15"),    # benchmarking
    ipaddress.ip_network("::/128"),
    ipaddress.ip_network("fe80::/10"),        # IPv6 link-local
    ipaddress.ip_network("fc00::/7"),         # IPv6 unique-local
)

DEFAULT_TIMEOUT = 15.0
MAX_REDIRECTS = 4
MAX_BYTES = 8_000_000


class UrlRefused(ValueError):
    """The URL is not one Friday will fetch. The message says why."""


# ---------------------------------------------------------------------------
# Addresses
# ---------------------------------------------------------------------------


def normalise(address) -> ipaddress._BaseAddress:
    """
    Unwrap IPv4-mapped and 6to4 IPv6 before judging it.

    I assumed `ipaddress` did not see through these and wrote that down. It is
    false, and measuring it was cheaper than believing it. On 3.11:

        ::ffff:127.0.0.1          is_loopback True   is_private True
        ::ffff:10.0.0.1           is_loopback False  is_private True
        ::ffff:169.254.169.254    is_link_local True is_private True
        2002:7f00:1::             is_private True    (6to4 of 127.0.0.1)

    So the *property* checks are already correct without this. What is not
    correct without it is the metadata denylist, which compares strings:
    `str(::ffff:169.254.169.254)` is not `"169.254.169.254"`, so the most
    dangerous single address in the file would be missed in its IPv6 spelling
    while every property said "private" and nothing said "metadata".
    """
    mapped = getattr(address, "ipv4_mapped", None)
    if mapped is not None:
        return mapped
    sixtofour = getattr(address, "sixtofour", None)
    if sixtofour is not None:
        return sixtofour
    return address


def address_refusal(address) -> str:
    """Why this address may not be fetched, or "" if it may."""
    real = normalise(address)
    if str(real) in METADATA_ADDRESSES or str(address) in METADATA_ADDRESSES:
        return f"{real} is a cloud instance-metadata endpoint"
    if real.is_loopback:
        return f"{real} is loopback"
    if real.is_link_local:
        return f"{real} is link-local"
    if real.is_multicast:
        return f"{real} is multicast"
    if real.is_unspecified:
        return f"{real} is unspecified"
    if real.is_reserved:
        return f"{real} is reserved"
    for network in EXTRA_DENIED:
        if real.version == network.version and real in network:
            return f"{real} is inside {network}, which is not public"
    if real.is_private:
        return f"{real} is a private network address"
    return ""


def resolve(host: str) -> list:
    """Every address a host resolves to. A literal resolves to itself."""
    try:
        return [ipaddress.ip_address(host)]
    except ValueError:
        pass
    found = []
    try:
        infos = socket.getaddrinfo(host, None, proto=socket.IPPROTO_TCP)
    except (socket.gaierror, UnicodeError, OSError):
        return []
    for info in infos:
        try:
            found.append(ipaddress.ip_address(info[4][0]))
        except ValueError:
            continue
    return found


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

#: The one loopback origin the Golden Objective runner may register
#: (host, port) while a case runs. Set through `evaluation_fixture()`
#: only - a process-local context, not an environment variable - so a
#: deployed Friday can never be talked into fetching its own control plane
#: by anything short of code running inside it.
_EVALUATION_FIXTURE: tuple[str, int] | None = None


class evaluation_fixture:
    """`with netguard.evaluation_fixture(port):` allows http://127.0.0.1:<port>
    for the block's duration. Nested/other ports stay refused."""

    def __init__(self, port: int, host: str = "127.0.0.1") -> None:
        self.origin = (host.lower(), int(port))
        self._prev: tuple[str, int] | None = None

    def __enter__(self):
        global _EVALUATION_FIXTURE
        self._prev = _EVALUATION_FIXTURE
        _EVALUATION_FIXTURE = self.origin
        return self

    def __exit__(self, *exc):
        global _EVALUATION_FIXTURE
        _EVALUATION_FIXTURE = self._prev


def _is_evaluation_fixture(host: str, port) -> bool:
    return _EVALUATION_FIXTURE is not None and (host, int(port or 0)) == _EVALUATION_FIXTURE


def check(raw: str, *, allow_private: bool = False,
          require_resolution: bool = False) -> dict:
    """
    Is this a URL Friday would be willing to fetch?

    Returns `{"url", "host", "scheme", "addresses", "verdict"}` where verdict
    is PUBLIC or UNRESOLVED. Raises UrlRefused otherwise.

    UNRESOLVED is a real, useful answer at validation time: a product feed
    should not fail ingestion because DNS was unreachable for a moment. It is
    **not** an answer `fetch` accepts - see `require_resolution`.
    """
    text = (raw or "").strip()
    if not text:
        raise UrlRefused("empty url")
    parts = urlsplit(text)
    if parts.scheme.lower() not in ALLOWED_SCHEMES:
        raise UrlRefused(
            f"{parts.scheme or 'missing'} scheme is refused; "
            f"{' and '.join(ALLOWED_SCHEMES)} only")
    host = (parts.hostname or "").lower()
    if not host:
        raise UrlRefused("url has no host")
    if _is_evaluation_fixture(host, parts.port):
        # Exactly one loopback origin, registered in-process by the Golden
        # Objective runner for the duration of one case (never from the
        # environment, never a wildcard): the suite's pages are served
        # locally so nothing leaves the machine. Everything else on
        # loopback is still refused below.
        return {"url": text, "host": host, "scheme": parts.scheme.lower(),
                "addresses": ["127.0.0.1"], "verdict": PUBLIC,
                "evaluation_fixture": True}
    if host in LOCAL_NAMES:
        raise UrlRefused(f"{host!r} is this machine")
    if parts.username or parts.password:
        raise UrlRefused("credentials embedded in a url are refused")

    addresses = resolve(host)
    if not addresses:
        if require_resolution:
            raise UrlRefused(f"{host!r} does not resolve")
        return {"url": text, "host": host, "scheme": parts.scheme.lower(),
                "addresses": [], "verdict": UNRESOLVED}

    for address in addresses:
        refusal = address_refusal(address)
        if refusal and not (allow_private and "private" in refusal):
            raise UrlRefused(f"{host!r} resolves to an address that {refusal}")

    return {"url": text, "host": host, "scheme": parts.scheme.lower(),
            "addresses": [str(a) for a in addresses], "verdict": PUBLIC}


# ---------------------------------------------------------------------------
# Fetching
# ---------------------------------------------------------------------------


def _pinned_request(client, url: str, address: str, host: str, headers: dict):
    """
    Build a request aimed at the address we validated, not at the name.

    This is the difference between narrowing the rebinding window and closing
    it: no second DNS lookup happens, so a second answer cannot be used. The
    `Host` header and the `sni_hostname` extension keep the request and the
    TLS handshake pointed at the real name, so certificate verification is
    unaffected.
    """
    parts = urlsplit(url)
    literal = f"[{address}]" if ":" in address else address
    netloc = f"{literal}:{parts.port}" if parts.port else literal
    pinned = urlunsplit((parts.scheme, netloc, parts.path or "/",
                         parts.query, ""))
    return client.build_request(
        "GET", pinned,
        headers={**headers, "Host": parts.netloc},
        extensions={"sni_hostname": host},
    )


def fetch(url: str, *, timeout: float = DEFAULT_TIMEOUT,
          max_redirects: int = MAX_REDIRECTS, allow_private: bool = False,
          max_bytes: int = MAX_BYTES, headers: dict | None = None):
    """
    Fetch a URL, validating at the moment of connection and at every redirect.

    Redirects are never followed automatically. `follow_redirects=True` would
    hand the destination straight to the client, and the destination is
    supplied by the server being fetched - which is to say, by whoever the URL
    belongs to. A validated URL that 302s to `http://169.254.169.254/` is the
    same attack with one extra step.
    """
    import httpx

    seen: list[str] = []
    current = url
    with httpx.Client(timeout=timeout, follow_redirects=False) as client:
        for hop in range(max_redirects + 1):
            verdict = check(current, allow_private=allow_private,
                            require_resolution=True)
            seen.append(verdict["url"])
            request = _pinned_request(
                client, verdict["url"], verdict["addresses"][0],
                verdict["host"], headers or {})
            response = client.send(request, stream=True)
            try:
                if response.is_redirect:
                    location = response.headers.get("location", "")
                    if not location:
                        raise UrlRefused("redirect without a destination")
                    current = str(httpx.URL(verdict["url"]).join(location))
                    if hop == max_redirects:
                        raise UrlRefused(
                            f"more than {max_redirects} redirects: {' -> '.join(seen)}")
                    response.close()
                    continue

                body = bytearray()
                for chunk in response.iter_bytes():
                    body.extend(chunk)
                    if len(body) > max_bytes:
                        raise UrlRefused(
                            f"response exceeded {max_bytes} bytes")
                response.close()
                return {
                    "url": verdict["url"], "status": response.status_code,
                    "headers": dict(response.headers), "content": bytes(body),
                    "chain": seen,
                }
            except Exception:
                response.close()
                raise
    raise UrlRefused("redirect loop")
