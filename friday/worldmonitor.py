"""
The World Monitor dashboard, as parameters rather than as one magic string.

What was there:

    url = "https://worldmonitor.app/"
    webbrowser.open(url)
    return "Displaying the World Monitor on your primary screen now, sir."

Three separate problems in four lines. It opened the marketing landing page
rather than the dashboard, so a request for the global intelligence view got
something that is not a view at all. It claimed success from `webbrowser.open`
returning, which reports only that a browser was launched. And the sentence it
returned was a fact about the world, asserted without checking.

The dashboard is a *view*: a place, a zoom, a span of time and a set of layers.
Storing one long URL cannot express "the last week" or "around Europe", so any
request that differs from the default has nowhere to go. Here the view is
typed, the URL is generated from it, and a URL can be parsed back into one -
which is what makes verification possible rather than aspirational.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from urllib.parse import parse_qs, urlencode, urlparse

HOST = "https://www.worldmonitor.app"

DASHBOARD_PATH = "/dashboard"

# Restored from the .pyc oracle: proven by a LOAD_CONST/STORE_NAME
# pair in the running system's bytecode, present in no source candidate.
DEFAULT_LAYERS: tuple[str, ...] = (
    'conflicts',
    'bases',
    'hotspots',
    'nuclear',
    'sanctions',
    'weather',
    'canadaAlerts',
    'economic',
    'waterways',
    'outages',
    'military',
    'natural',
)

#: A whole-world framing: mid-latitude, near the prime meridian, fully zoomed
#: out. These are the numbers the boss's own preset uses.
GLOBAL_LAT = 20.0

GLOBAL_LON = -0.18

GLOBAL_ZOOM = 1.0

VIEWS = ("global", "regional", "local")

#: Spans the dashboard understands, shortest first.
TIME_RANGES = ("24h", "7d", "30d", "90d")

#: Layer subsets for requests that are about one thing rather than everything.
#: Deliberately small and named: a request for the weather should not silently
#: draw sanctions and nuclear sites over it.
FOCUSED_LAYERS: dict[str, tuple[str, ...]] = {
    "weather": ("weather", "natural", "canadaAlerts"),
    "conflicts": ("conflicts", "military", "bases", "hotspots"),
    "economic": ("economic", "sanctions", "waterways", "outages"),
    "nuclear": ("nuclear", "military", "bases"),
}


@dataclass(frozen=True)
class WorldMonitorView:
    """Somewhere, at some zoom, over some period, showing some layers."""

    lat: float = GLOBAL_LAT
    lon: float = GLOBAL_LON
    zoom: float = GLOBAL_ZOOM
    view: str = "global"
    time_range: str = "7d"
    layers: tuple[str, ...] = DEFAULT_LAYERS

    def __post_init__(self) -> None:
        if self.view not in VIEWS:
            raise ValueError(f"unknown view {self.view!r}; known: {list(VIEWS)}")
        if self.time_range not in TIME_RANGES:
            raise ValueError(
                f"unknown time range {self.time_range!r}; "
                f"known: {list(TIME_RANGES)}")
        if not self.layers:
            raise ValueError("a dashboard with no layers shows nothing")

    def url(self) -> str:
        """The address this view lives at."""
        query = urlencode({
            "lat": f"{self.lat:.4f}",
            "lon": f"{self.lon:.4f}",
            "zoom": f"{self.zoom:.2f}",
            "view": self.view,
            "timeRange": self.time_range,
            "layers": ",".join(self.layers),
        })
        return f"{HOST}{DASHBOARD_PATH}?{query}"

    def focused_on(self, subject: str) -> "WorldMonitorView":
        """The same place and span, showing one subject's layers."""
        layers = FOCUSED_LAYERS.get(subject.strip().lower())
        return self if layers is None else replace(self, layers=layers)

    def at(self, lat: float, lon: float, *, zoom: float = 4.0,
           view: str = "regional") -> "WorldMonitorView":
        return replace(self, lat=lat, lon=lon, zoom=zoom, view=view)

    def over(self, time_range: str) -> "WorldMonitorView":
        return replace(self, time_range=time_range)


def parse(url: str) -> WorldMonitorView | None:
    """
    A URL read back as a view, or None if it is not a dashboard at all.

    None is the answer for the landing page, and that distinction is the whole
    point: `https://worldmonitor.app/` parses to nothing, so a check for "did
    the dashboard open" cannot be satisfied by the page that used to open.
    """
    parsed = urlparse(url or "")
    if parsed.path.rstrip("/") != DASHBOARD_PATH:
        return None

    query = parse_qs(parsed.query)

    def first(key: str, fallback: str) -> str:
        values = query.get(key) or []
        return values[0] if values else fallback

    layers = tuple(part for part in first("layers", "").split(",") if part)
    try:
        return WorldMonitorView(
            lat=float(first("lat", str(GLOBAL_LAT))),
            lon=float(first("lon", str(GLOBAL_LON))),
            zoom=float(first("zoom", str(GLOBAL_ZOOM))),
            view=first("view", "global"),
            time_range=first("timeRange", "7d"),
            layers=layers or DEFAULT_LAYERS,
        )
    except ValueError:
        return None


def shows(url: str, wanted: WorldMonitorView, *,
          coordinate_tolerance: float = 0.01) -> tuple[bool, str]:
    """
    Whether an observed URL is the view that was asked for, and why not.

    Compares meaning, never the query string. Parameter order is the browser's
    business and two identical views can serialise differently; asserting on
    the literal string would fail on a reordering that changed nothing and
    pass on a landing page redirect that changed everything.
    """
    seen = parse(url)
    if seen is None:
        return (False, f"{url!r} is not a dashboard view - the landing page "
                       f"and a dashboard are different destinations")

    if seen.view != wanted.view:
        return (False, f"view is {seen.view!r}, asked for {wanted.view!r}")
    if seen.time_range != wanted.time_range:
        return (False, f"time range is {seen.time_range!r}, asked for "
                       f"{wanted.time_range!r}")

    missing = [layer for layer in wanted.layers if layer not in seen.layers]
    if missing:
        return (False, f"missing layer(s): {', '.join(missing)}")

    if abs(seen.lat - wanted.lat) > coordinate_tolerance or \
            abs(seen.lon - wanted.lon) > coordinate_tolerance:
        return (False, f"centred on {seen.lat:.4f},{seen.lon:.4f} rather than "
                       f"{wanted.lat:.4f},{wanted.lon:.4f}")

    return (True, f"{seen.view} view, {seen.time_range}, "
                  f"{len(seen.layers)} layer(s), centred "
                  f"{seen.lat:.4f},{seen.lon:.4f}")
