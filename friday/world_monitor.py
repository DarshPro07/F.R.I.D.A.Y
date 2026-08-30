"""Canonical World Monitor dashboard URLs and observed-destination checks."""
from __future__ import annotations
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from urllib.parse import parse_qs, urlencode, urlparse
DEFAULT_LAYERS = ('conflicts', 'bases', 'hotspots', 'nuclear', 'sanctions', 'weather', 'canadaAlerts', 'economic', 'waterways', 'outages', 'military', 'natural')


@dataclass(frozen=True)
class WorldMonitorView:
    lat: str = '20.0000'
    lon: str = '-0.1800'
    zoom: str = '1.00'
    view: str = 'global'
    time_range: str = '7d'
    layers: tuple[str, ...] = DEFAULT_LAYERS

    def url(self) -> str:
        query = urlencode({'lat': self.lat, 'lon': self.lon, 'zoom': self.zoom, 'view': self.view, 'timeRange': self.time_range, 'layers': ','.join(self.layers)})
        return f"https://www.worldmonitor.app/dashboard?{query}"


@dataclass(frozen=True)
class DestinationVerification:
    ok: bool
    reason: str
    requested_url: str
    observed_url: str


def build_world_monitor_url(*, lat: str | float | None = None, lon: str | float | None = None, zoom: str | float | None = None, view: str | None = None, time_range: str | None = None, layers: tuple[str, ...] | list[str] | str | None = None) -> str:
    defaults = WorldMonitorView()
    if isinstance(layers, str):
        selected_layers = tuple((part.strip() for part in layers.split(',') if part.strip()))
    elif layers is None:
        selected_layers = defaults.layers
    else:
        selected_layers = tuple((str(part).strip() for part in layers if str(part).strip()))
    if not selected_layers:
        raise ValueError('World Monitor requires at least one layer')
    return WorldMonitorView(lat=defaults.lat if lat is None else str(lat), lon=defaults.lon if lon is None else str(lon), zoom=defaults.zoom if zoom is None else str(zoom), view=defaults.view if view is None else str(view), time_range=defaults.time_range if time_range is None else str(time_range), layers=selected_layers).url()


def _one(query: dict[str, list[str]], key: str) -> str | None:
    values = query.get(key, [])
    return values[0] if len(values) == 1 else None


def _same_number(left: str | None, right: str | None) -> bool:
    if left is None or right is None:
        return False
    try:
        return Decimal(left) == Decimal(right)
    except InvalidOperation:
        return False


def verify_world_monitor_destination(requested_url: str, observed_url: str) -> DestinationVerification:
    requested = urlparse(requested_url)
    observed = urlparse(observed_url)
    if observed.scheme != 'https' or observed.hostname not in {'worldmonitor.app', 'www.worldmonitor.app'}:
        return DestinationVerification(False, 'observed host is not World Monitor', requested_url, observed_url)
    if observed.path.rstrip('/') != '/dashboard':
        return DestinationVerification(False, 'observed destination is not /dashboard', requested_url, observed_url)
    expected_query = parse_qs(requested.query, keep_blank_values=True)
    observed_query = parse_qs(observed.query, keep_blank_values=True)
    for key in ('lat', 'lon', 'zoom'):
        if not _same_number(_one(expected_query, key), _one(observed_query, key)):
            return DestinationVerification(False, f"observed {key} differs from the request", requested_url, observed_url)
    for key in ('view', 'timeRange'):
        if _one(expected_query, key) != _one(observed_query, key):
            return DestinationVerification(False, f"observed {key} differs from the request", requested_url, observed_url)
    expected_layers = set((_one(expected_query, 'layers') or '').split(','))
    observed_layers = set((_one(observed_query, 'layers') or '').split(','))
    if expected_layers != observed_layers or '' in observed_layers:
        return DestinationVerification(False, 'observed layer set differs from the request', requested_url, observed_url)
    return DestinationVerification(True, 'observed dashboard parameters match', requested_url, observed_url)