"""
UCW-001: which model-gateway failures are worth one more try on the SAME route.

A transient provider error or a worker that did not answer is often gone a
moment later; a rate limit, an exhausted quota or credit, a safeguard refusal
and a bad request are not, and trying again only repeats them (and the limit
ones would lengthen a cooldown). This module only decides: `ModelGateway.infer`
owns the call, the attempts list and the usage guard.

    classify(code, exc) -> TRANSIENT | STALLED | PERMANENT
    RetryPolicy         -> how many tries, how long to wait before each, and
                           how much of the request deadline each may use

The default is no retry: `ModelGatewayRequest.transient_retries` is 0.
"""
from __future__ import annotations

from dataclasses import dataclass

TRANSIENT = "TRANSIENT"        # the provider answered with a passing error
STALLED = "STALLED"            # the worker did not answer within the time given
PERMANENT = "PERMANENT"        # anything else: fail over as before, never retry

#: Error codes worth another try on the same route.
_TRANSIENT_CODES = frozenset({"PROVIDER_ERROR"})
#: What `ModelGatewayWorker._request` says when its backstop timeout passes.
_STALL_MARKER = "did not answer"


def classify(code: str = "", exc: BaseException | None = None) -> str:
    """Class of one failed try. `exc` is the exception the transport raised
    (None when the provider answered with an error code)."""
    if exc is not None:
        return STALLED if _STALL_MARKER in str(exc) else PERMANENT
    return TRANSIENT if code in _TRANSIENT_CODES else PERMANENT


@dataclass(frozen=True)
class RetryPolicy:
    """`max_retries` extra tries; the wait before retry n (from 0) is
    min(cap_s, base_s * 2**n) and is never allowed to reach the deadline."""

    max_retries: int = 0
    base_s: float = 0.5
    cap_s: float = 4.0

    def delay(self, n: int) -> float:
        return min(self.cap_s, self.base_s * (2 ** n))

    def plan(self, *, done: int, remaining_s: float) -> float | None:
        """Seconds to wait before the next retry, or None when it is not allowed:
        the retries are spent, or the wait alone would use up what is left."""
        if done >= self.max_retries:
            return None
        wait = self.delay(done)
        return wait if wait < remaining_s else None

    def try_timeout(self, *, timeout_s: float, remaining_s: float, done: int) -> float:
        """Time one try may take. With retries still to come it gets an equal share
        of what is left, so a stall can be noticed with time left to retry in;
        the last try gets all of it."""
        left = self.max_retries - done
        return remaining_s / (left + 1) if left > 0 else min(timeout_s, remaining_s)
