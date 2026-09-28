"""
Executable extensions (LEVEL 3) are installed packages, never files in a
skill directory.

A plugin advertises itself the standard way - an entry point in the
`friday.plugins` group of its own distribution metadata:

    [project.entry-points."friday.plugins"]
    weather = "friday_weather:PLUGIN"

Discovery reads that metadata and imports nothing. Loading is a separate,
explicit step limited to names the owner allowed (`FRIDAY_PLUGINS_ALLOW`,
comma-separated), and a loaded object must have the shape below or it is
refused. Nothing here ever imports a `.py` found under a skill; that path
does not exist in FRIDAY.

    PLUGIN = {
        "name": "weather",
        "version": "1.0.0",
        "capabilities": [...],      # what it offers, by id; policy decides use
    }
"""

from __future__ import annotations

import os
from dataclasses import asdict, dataclass, field
from importlib import metadata
from typing import Callable

GROUP = "friday.plugins"
REQUIRED_KEYS = ("name", "version", "capabilities")


@dataclass
class PluginInfo:
    name: str
    module: str
    attr: str
    distribution: str = ""
    dist_version: str = ""
    allowed: bool = False
    loaded: bool = False
    error: str = ""
    version: str = ""
    capabilities: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


def allowed_names() -> set[str]:
    raw = os.getenv("FRIDAY_PLUGINS_ALLOW", "")
    return {part.strip() for part in raw.split(",") if part.strip()}


def discover(entry_points: Callable | None = None, allow: set[str] | None = None) -> list[PluginInfo]:
    """Every advertised plugin, without importing any of them."""
    finder = entry_points or metadata.entry_points
    allow = allowed_names() if allow is None else allow
    found: list[PluginInfo] = []
    try:
        points = finder(group=GROUP)
    except TypeError:                                       # a finder without keyword support
        points = [ep for ep in finder() if getattr(ep, "group", "") == GROUP]
    for point in points:
        dist = getattr(point, "dist", None)
        found.append(PluginInfo(
            name=point.name, module=getattr(point, "module", "") or point.value.split(":")[0],
            attr=getattr(point, "attr", "") or (point.value.split(":")[1] if ":" in point.value else ""),
            distribution=getattr(dist, "name", "") if dist is not None else "",
            dist_version=getattr(dist, "version", "") if dist is not None else "",
            allowed=point.name in allow))
    found.sort(key=lambda p: p.name)
    return found


def validate(obj) -> str | None:
    """None when `obj` has the plugin shape; the reason otherwise."""
    if not isinstance(obj, dict):
        obj = {key: getattr(obj, key, None) for key in REQUIRED_KEYS}
    missing = [key for key in REQUIRED_KEYS if not obj.get(key)]
    if missing:
        return "plugin object lacks " + ", ".join(missing)
    capabilities = obj.get("capabilities")
    if callable(capabilities):
        try:
            capabilities = capabilities()
        except Exception as exc:                            # noqa: BLE001
            return f"capabilities() raised {type(exc).__name__}"
    if not isinstance(capabilities, (list, tuple)) or not all(isinstance(c, str) for c in capabilities):
        return "capabilities must be a list of ids"
    return None


def load(info: PluginInfo, entry_points: Callable | None = None, allow: set[str] | None = None) -> PluginInfo:
    """Import one allowed plugin and validate its shape. Refused otherwise."""
    allow = allowed_names() if allow is None else allow
    if info.name not in allow:
        info.error = f"not in FRIDAY_PLUGINS_ALLOW; refusing to import {info.module}"
        return info
    finder = entry_points or metadata.entry_points
    try:
        points = finder(group=GROUP)
    except TypeError:
        points = [ep for ep in finder() if getattr(ep, "group", "") == GROUP]
    point = next((p for p in points if p.name == info.name), None)
    if point is None:
        info.error = "entry point vanished"
        return info
    try:
        obj = point.load()
    except Exception as exc:                                # noqa: BLE001
        info.error = f"import failed: {type(exc).__name__}: {exc}"[:200]
        return info
    problem = validate(obj)
    if problem:
        info.error = problem
        return info
    data = obj if isinstance(obj, dict) else {key: getattr(obj, key) for key in REQUIRED_KEYS}
    capabilities = data["capabilities"]
    info.loaded = True
    info.allowed = True
    info.version = str(data["version"])
    info.capabilities = list(capabilities() if callable(capabilities) else capabilities)
    return info
