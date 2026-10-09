"""
The hybrid action-path router (PRD v7 §48, BROW-001).

When several capabilities can take the same step, the most direct path goes
first: a native API, then an official scripting interface, then a
structured tool or MCP, then the DOM or accessibility tree, then semantic
vision, and screen coordinates last. A tool path and a GUI path can both
serve one objective; this only decides which is tried first for a step.

The order adapts to measured reliability (§48): a path that has kept
failing - at least `MIN_ATTEMPTS` tries, fewer than half succeeding - moves
behind the ones that have not, keeping its place among the other failing
ones.

WebMCP (WEBMCP-001) is an experimental first tier: a page that offers
structured browser-native tools is used before anything else, but only when
the caller vouches that the tool is trusted. Nothing is trusted by default,
so an absent or untrusted WebMCP tool leaves the route as it was.
"""
from __future__ import annotations

from collections.abc import Callable

WEBMCP = "webmcp"
NATIVE_API = "native_api"
SCRIPTING = "scripting"
STRUCTURED = "structured_tool"
DOM = "dom"
VISION = "vision"
COORDINATES = "coordinates"

#: Most direct first.
ORDER = (WEBMCP, NATIVE_API, SCRIPTING, STRUCTURED, DOM, VISION, COORDINATES)

#: What each family of capability acts through. Anything else is one of
#: FRIDAY's own structured tools.
_BY_PREFIX = (
    ("webmcp_", WEBMCP),            # a page's own WebMCP tools (experimental)
    ("connector_", NATIVE_API),      # the provider's own API, via the connector plane
    ("web_", NATIVE_API),            # HTTP fetch and search APIs
    ("apps_", SCRIPTING),            # the operating system's process launch and focus
    ("browser_", DOM),               # Playwright, through the page's DOM
    ("vision_", VISION),             # a model reading the screen or camera
    ("screen_point", VISION),        # locates a control by vision to point at it
    ("desktop_", COORDINATES),       # a takeover clicks at absolute screen coordinates
)

#: Tries before a failure rate counts, and the success rate below which a
#: path moves behind the others.
MIN_ATTEMPTS = 5
MIN_SUCCESS_RATE = 0.5


def path_of(capability_id: str) -> str:
    """The path a capability acts through."""
    for prefix, path in _BY_PREFIX:
        if capability_id.startswith(prefix):
            return path
    return STRUCTURED


def _failing(capability_id: str, reliability: dict) -> bool:
    successes, attempts = reliability.get(capability_id, (0, 0))
    return attempts >= MIN_ATTEMPTS and successes / attempts < MIN_SUCCESS_RATE


def combine(*tallies: dict) -> dict:
    """Sum several capability -> (successes, attempts) tallies, e.g. the
    routing outcomes for a request shape and the recorded execution outcomes."""
    out: dict[str, tuple[int, int]] = {}
    for tally in tallies:
        for cap, (good, total) in (tally or {}).items():
            seen_good, seen_total = out.get(cap, (0, 0))
            out[cap] = (seen_good + good, seen_total + total)
    return out


def rank(candidates, *, reliability: dict | None = None,
         down: frozenset[str] = frozenset(),
         trusted: Callable[[str], bool] | None = None) -> list[str]:
    """`candidates` for one step, most direct path first. `reliability`
    maps a capability to (successes, attempts) for this kind of step; `down`
    is the paths with no backend up (`computer_use.unavailable_paths`).
    `trusted` says whether a WebMCP capability may be used; a WebMCP
    candidate it does not vouch for (all of them, by default) is left out."""
    measured = reliability or {}
    usable = [cap for cap in candidates
              if path_of(cap) != WEBMCP or (trusted is not None and trusted(cap))]
    return sorted(usable, key=lambda cap: order_key(cap, measured, down))


def order_key(capability_id: str, reliability: dict | None = None,
              down: frozenset[str] = frozenset()) -> tuple[bool, int]:
    """Where a capability's path stands in §48's order, for tie-breaking: a
    path that keeps failing for this kind of step, or whose backend is not up,
    sorts behind the rest."""
    path = path_of(capability_id)
    return (_failing(capability_id, reliability or {}) or path in down,
            ORDER.index(path))
