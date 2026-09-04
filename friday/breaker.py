"""A circuit breaker for outbound HTTP, per host.

Why this exists
---------------
`web_fetch` and the research crawler each open httpx with a 25s timeout and no
memory. A host that is down costs the full 25s *every* call, and a research pass
crawls many URLs: several dead sources in one objective serialise into minutes
of a voice session sitting silent, waiting on hosts that already failed.

The breaker gives that failure a memory. After `THRESHOLD` consecutive failures
a host is opened for `COOLDOWN_SECONDS` and further calls fail immediately with
a truthful reason instead of hanging. One success closes it again.

Design notes
------------
- **Per host, not global.** One dead domain must never stop the others.
- **Fails open on its own errors.** If anything here breaks, callers still make
  their request: a monitoring aid must not become a new outage
  (NON_NEGOTIABLE 15 - optional machinery never breaks the parent objective).
- **Timeouts and connection errors trip it; HTTP status codes do not.** A 404 or
  a 403 is the server answering, which is a working host. Only a host that fails
  to respond is worth short-circuiting.
- **No background threads or timers.** State is a plain dict consulted on call,
  so there is nothing to shut down and nothing to leak.
"""

from __future__ import annotations

import os
import threading
import time
from dataclasses import dataclass, field
from urllib.parse import urlparse

__all__ = [
    "CircuitOpen",
    "allow",
    "record_failure",
    "record_success",
    "state_of",
    "reset",
    "guard",
]


def _int_env(name: str, default: int) -> int:
    try:
        return max(1, int(os.getenv(name, "") or default))
    except (TypeError, ValueError):
        return default


#: Consecutive failures before a host is short-circuited.
THRESHOLD = _int_env("FRIDAY_BREAKER_THRESHOLD", 3)
#: How long an opened host stays refused before one probe is allowed through.
COOLDOWN_SECONDS = _int_env("FRIDAY_BREAKER_COOLDOWN", 60)


class CircuitOpen(RuntimeError):
    """Raised instead of making a request to a host that is failing."""

    def __init__(self, host: str, seconds_left: int, failures: int):
        self.host = host
        self.seconds_left = seconds_left
        self.failures = failures
        super().__init__(
            f"{host} failed {failures} times in a row; not retrying for "
            f"{seconds_left}s"
        )


@dataclass
class _Host:
    failures: int = 0
    opened_at: float = 0.0
    probing: bool = False


_LOCK = threading.Lock()
_HOSTS: dict[str, _Host] = {}


def host_of(url: str) -> str:
    """The netloc a URL will actually contact, lowercased. '' when unparseable."""
    try:
        return (urlparse(url or "").netloc or "").lower()
    except Exception:  # noqa: BLE001 - a bad URL is the caller's problem, not ours
        return ""


def allow(url: str, *, now: float | None = None) -> None:
    """Raise `CircuitOpen` if this host is currently short-circuited.

    After the cooldown, exactly one caller is let through as a probe; the rest
    keep failing fast until that probe reports back.
    """
    host = host_of(url)
    if not host:
        return
    now = time.monotonic() if now is None else now
    with _LOCK:
        entry = _HOSTS.get(host)
        if entry is None or entry.failures < THRESHOLD:
            return
        elapsed = now - entry.opened_at
        if elapsed >= COOLDOWN_SECONDS:
            if not entry.probing:
                entry.probing = True      # this caller is the probe
                return
            # A probe is already in flight; everyone else keeps failing fast
            # until it reports back, so a recovering host is not stampeded.
            raise CircuitOpen(host, 0, entry.failures)
        raise CircuitOpen(host, int(COOLDOWN_SECONDS - elapsed), entry.failures)


def record_failure(url: str, *, now: float | None = None) -> None:
    """Count a timeout or connection failure against this host."""
    host = host_of(url)
    if not host:
        return
    now = time.monotonic() if now is None else now
    with _LOCK:
        entry = _HOSTS.setdefault(host, _Host())
        entry.failures += 1
        entry.probing = False
        if entry.failures >= THRESHOLD:
            entry.opened_at = now


def record_success(url: str) -> None:
    """A host that answered is healthy again, whatever it answered with."""
    host = host_of(url)
    if not host:
        return
    with _LOCK:
        _HOSTS.pop(host, None)


def state_of(url: str) -> dict:
    """Introspection for the control room and for tests."""
    host = host_of(url)
    with _LOCK:
        entry = _HOSTS.get(host)
        if entry is None:
            return {"host": host, "state": "closed", "failures": 0}
        open_ = entry.failures >= THRESHOLD
        left = max(0, int(COOLDOWN_SECONDS - (time.monotonic() - entry.opened_at)))
        return {
            "host": host,
            "state": "open" if open_ and left else "closed",
            "failures": entry.failures,
            "seconds_left": left if open_ else 0,
        }


def reset(url: str | None = None) -> None:
    """Clear one host, or all of them. Used by tests and by a manual retry."""
    with _LOCK:
        if url is None:
            _HOSTS.clear()
        else:
            _HOSTS.pop(host_of(url), None)


def is_transport_failure(exc: BaseException) -> bool:
    """Did the host fail to answer at all?

    An HTTP error status means the server responded, so it does not count -
    only transport-level failures (timeout, DNS, refused, reset) do.
    """
    name = type(exc).__name__
    if "HTTPStatusError" in name:
        return False
    return any(
        marker in name
        for marker in ("Timeout", "ConnectError", "ConnectTimeout", "ReadError",
                       "NetworkError", "RemoteProtocolError", "TransportError",
                       "ProxyError", "UnsupportedProtocol")
    )


class guard:
    """Context manager: check before, and record the outcome after.

        with breaker.guard(url):
            response = await client.get(url)

    Re-raises `CircuitOpen` for the caller to turn into its own failure shape.
    """

    def __init__(self, url: str):
        self.url = url

    def __enter__(self) -> "guard":
        allow(self.url)
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        if exc is None:
            record_success(self.url)
        elif isinstance(exc, CircuitOpen):
            pass                      # already counted; do not double-count
        elif is_transport_failure(exc):
            record_failure(self.url)
        else:
            record_success(self.url)  # the host answered; the error is elsewhere
        return False                  # never swallow
