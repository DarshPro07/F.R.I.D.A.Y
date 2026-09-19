"""RuntimeSelfModel: what Friday ACTUALLY has, assembled from runtime state.

PRD brief §5 (Mark-LIV): "Do NOT maintain a static prompt claiming
abilities." Before this module, `voice_brain.PERSONA` hard-coded "you can
... look at his screen or through the camera" and `agent_friday.SYSTEM_PROMPT`
hard-coded "You have 169 tools" - both true the day they were written and
neither read from anything that could change.

This is the one place a capability claim comes from:

    fabric.report()          upstream provider families + state
    provider_health.assess   which model routes have CURRENT evidence
    vision toolset deps      screen (mss + a display) and camera (cv2)
    switches                 what the operator deliberately turned off
        |
        v
    Snapshot                 modalities / families / routes / limits
        |
        v
    describe()               the paragraph both prompt paths inject

Rules the snapshot keeps:

* A modality is claimed only if its dependency imports AND, for the screen,
  a display is present. "snapshot only" is said as such - there is no live
  vision here and the model must not imply there is (brief §25).
* A provider route is "healthy" only with current probe evidence. The
  absence of a ledger is UNPROBED, never healthy (FR-034).
* A switched-off capability is DISABLED and is described as unavailable,
  with the reason. Turning it back on changes the description without a
  prompt edit (golden journey §87).
* Nothing here calls a model, opens a device, or touches the network. The
  snapshot is what the runtime already knows about itself; probing is a
  separate, attributed action.
"""
from __future__ import annotations

import importlib.util
import json
import logging
import os
import platform
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger("friday.self_model")

# --------------------------------------------------------------------------
# switches: the operator's deliberate off-state, durable across restarts
# --------------------------------------------------------------------------

#: Things the operator can switch off by name. Modalities and fabric
#: families share one namespace because the user talks about both the same
#: way ("turn off the camera", "disable the browser").
SWITCHABLE = ("screen", "camera", "browser", "desktop", "web", "hermes")

_lock = threading.Lock()


def _switch_path() -> Path:
    override = os.getenv("FRIDAY_SELF_MODEL_SWITCHES")
    if override:
        return Path(override)
    from friday.config import DATA_DIR
    return Path(DATA_DIR) / "self_model_switches.json"


def switches() -> dict[str, str]:
    """name -> reason, for everything currently switched off."""
    path = _switch_path()
    try:
        raw = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    except Exception as exc:  # noqa: BLE001 - a corrupt file is "nothing off", logged
        logger.warning("self-model switches unreadable (%s); treating as none", exc)
        return {}
    return {str(k): str(v) for k, v in (raw or {}).items() if k in SWITCHABLE}


def disable(name: str, reason: str = "switched off by the operator") -> dict[str, str]:
    if name not in SWITCHABLE:
        raise ValueError(f"{name!r} is not switchable; choose from {SWITCHABLE}")
    with _lock:
        current = switches()
        current[name] = reason or "switched off by the operator"
        _write(current)
    return current


def enable(name: str) -> dict[str, str]:
    if name not in SWITCHABLE:
        raise ValueError(f"{name!r} is not switchable; choose from {SWITCHABLE}")
    with _lock:
        current = switches()
        current.pop(name, None)
        _write(current)
    return current


def _write(current: dict[str, str]) -> None:
    path = _switch_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(current, indent=2, sort_keys=True), encoding="utf-8")
    os.replace(tmp, path)


# --------------------------------------------------------------------------
# the snapshot
# --------------------------------------------------------------------------

AVAILABLE, DEGRADED, DISABLED, UNAVAILABLE, UNPROBED = (
    "AVAILABLE", "DEGRADED", "DISABLED", "UNAVAILABLE", "UNPROBED")


@dataclass(frozen=True)
class Modality:
    name: str                 # screen | camera | microphone | speaker
    state: str
    detail: str = ""          # "snapshot only", "no display", "cv2 missing", ...


@dataclass(frozen=True)
class Family:
    name: str
    state: str
    providers: int = 0
    detail: str = ""


@dataclass(frozen=True)
class Route:
    provider: str
    state: str
    reason: str = ""


@dataclass
class Snapshot:
    taken_at: float
    host: str
    modalities: list[Modality] = field(default_factory=list)
    families: list[Family] = field(default_factory=list)
    routes: list[Route] = field(default_factory=list)
    switched_off: dict[str, str] = field(default_factory=dict)
    tool_count: int | None = None      # None = the MCP inventory was not readable
    limits: list[str] = field(default_factory=list)

    # -- queries -------------------------------------------------------------

    def modality(self, name: str) -> Modality | None:
        return next((m for m in self.modalities if m.name == name), None)

    def family(self, name: str) -> Family | None:
        return next((f for f in self.families if f.name == name), None)

    def can(self, name: str) -> bool:
        """Is this modality/family usable right now? DEGRADED counts as
        usable-with-caveat; DISABLED/UNAVAILABLE do not."""
        item = self.modality(name) or self.family(name)
        return bool(item) and item.state in (AVAILABLE, DEGRADED)

    def healthy_routes(self) -> list[str]:
        return [r.provider for r in self.routes if r.state == "HEALTHY"]

    # -- rendering -----------------------------------------------------------

    def describe(self) -> str:
        """The paragraph the prompts inject. Spoken register, no brands for
        families (the user asks for outcomes), exact about limits."""
        parts: list[str] = []
        can_see = [m for m in self.modalities if m.name in ("screen", "camera") and m.state in (AVAILABLE, DEGRADED)]
        cannot = [m for m in self.modalities if m.name in ("screen", "camera") and m.state not in (AVAILABLE, DEGRADED)]
        if can_see:
            what = " and ".join(("his screen" if m.name == "screen" else "the camera") for m in can_see)
            parts.append(f"You can look at {what} - a single snapshot when asked, never a live feed; "
                         f"say 'snapshot' if he asks how you see.")
        for m in cannot:
            label = "the screen" if m.name == "screen" else "the camera"
            parts.append(f"You cannot look at {label} right now ({m.detail or m.state.lower()}); say so if asked.")
        ready = sorted(f.name for f in self.families if f.state in (AVAILABLE, DEGRADED))
        off = [(f.name, f.detail) for f in self.families if f.state == DISABLED]
        down = [(f.name, f.detail) for f in self.families if f.state == UNAVAILABLE]
        if ready:
            parts.append("Capability areas up now: " + ", ".join(ready) + ".")
        for name, why in off:
            parts.append(f"{name} is switched off ({why}); do not offer it.")
        for name, why in down:
            parts.append(f"{name} is not reachable right now" + (f" ({why})" if why else "") + ".")
        if self.tool_count is not None:
            parts.append(f"You have {self.tool_count} tools on the MCP surface; search_capabilities finds the exact one.")
        else:
            parts.append("The MCP tool inventory is not readable right now; do not recite a tool count.")
        healthy = self.healthy_routes()
        if healthy:
            parts.append("Model routes with current evidence: " + ", ".join(sorted(healthy)) + ".")
        else:
            parts.append("No model route has current health evidence; a route is only healthy after it answers.")
        # The rest of the ledger, by state, so "which providers are unprobed /
        # stale" is answered from here and never guessed: the live answer to
        # that question was "none" with seven STALE rows on disk.
        other: dict[str, list[str]] = {}
        for r in self.routes:
            if r.state != "HEALTHY":
                other.setdefault(r.state.lower(), []).append(r.provider)
        for state, names in sorted(other.items()):
            parts.append(f"Model routes {state}: " + ", ".join(sorted(names)) + ".")
        for lim in self.limits:
            parts.append(lim)
        return " ".join(parts)

    def to_dict(self) -> dict:
        return {
            "taken_at": self.taken_at, "host": self.host,
            "modalities": [m.__dict__ for m in self.modalities],
            "families": [f.__dict__ for f in self.families],
            "routes": [r.__dict__ for r in self.routes],
            "switched_off": dict(self.switched_off),
            "tool_count": self.tool_count, "limits": list(self.limits),
        }


# --------------------------------------------------------------------------
# assembly
# --------------------------------------------------------------------------

def _has(module: str) -> bool:
    try:
        return importlib.util.find_spec(module) is not None
    except (ImportError, ValueError):
        return False


def _display_present() -> tuple[bool, str]:
    """Is there a display to capture? Windows: always (the session has a
    desktop). Elsewhere: DISPLAY/WAYLAND_DISPLAY must be set - the ubuntu CI
    runner has mss installed and no display (vision.py, 2026-09-05)."""
    if os.name == "nt":
        return True, ""
    if os.getenv("DISPLAY") or os.getenv("WAYLAND_DISPLAY"):
        return True, ""
    return False, "no display on this host"


def _modalities(off: dict[str, str]) -> list[Modality]:
    out: list[Modality] = []
    # screen
    if "screen" in off:
        out.append(Modality("screen", DISABLED, off["screen"]))
    elif not _has("mss"):
        out.append(Modality("screen", UNAVAILABLE, "screen capture library missing"))
    else:
        ok, why = _display_present()
        out.append(Modality("screen", AVAILABLE, "snapshot only") if ok
                   else Modality("screen", UNAVAILABLE, why))
    # camera
    if "camera" in off:
        out.append(Modality("camera", DISABLED, off["camera"]))
    elif not _has("cv2"):
        out.append(Modality("camera", UNAVAILABLE, "camera library missing"))
    else:
        # cv2 present says a camera CAN be opened; whether one is attached is
        # only known by opening it, which is an attributed action, not a
        # snapshot. Say "if attached" rather than promise.
        out.append(Modality("camera", AVAILABLE, "snapshot only, if a camera is attached"))
    return out


def _families(off: dict[str, str]) -> list[Family]:
    out: list[Family] = []
    try:
        from friday import fabric
        rank = {fabric.READY: AVAILABLE, fabric.DEGRADED: DEGRADED,
                fabric.AUTH_REQUIRED: UNAVAILABLE, fabric.UNAVAILABLE: UNAVAILABLE,
                fabric.DISABLED: DISABLED, fabric.REFERENCE_ONLY: UNAVAILABLE}
        for row in fabric.family_report():
            name = row["family"]
            if name in off:
                out.append(Family(name, DISABLED, row.get("providers", 0), off[name]))
                continue
            state = rank.get(row["state"], UNAVAILABLE)
            detail = "" if state == AVAILABLE else row["state"].lower().replace("_", " ")
            out.append(Family(name, state, row.get("providers", 0), detail))
    except Exception as exc:  # noqa: BLE001 - the fabric not loading is a fact to report, not a crash
        logger.warning("self-model: fabric unreadable: %s", exc)
        out.append(Family("fabric", UNAVAILABLE, 0, f"registry unreadable: {exc}"[:120]))
    # Friday's own surfaces (voice_brain._surface adds these outside the
    # fabric): the web, the desktop, Hermes. They are switchable too.
    for own in ("web", "desktop", "browser", "hermes"):
        if own in off:
            out.append(Family(own, DISABLED, 0, off[own]))
    return out


def _routes(telemetry=None, providers: list[str] | None = None) -> list[Route]:
    """Route verdicts from the gateway ledger. No ledger, no providers ->
    empty, which describe() renders as 'no current evidence'.

    `providers=None` means "whatever the ledger has seen": the callers that
    matter (the two prompt assemblers, `self_model_snapshot`) do not know
    the provider list, and passing nothing used to yield an empty route
    list - so the description said "No model route has current health
    evidence" with seven providers in the ledger, and the spoken answer to
    "which providers are unprobed" was invented. An explicit empty list
    still means "ask about nobody"."""
    try:
        if telemetry is None:
            from friday.model_gateway import GatewayTelemetry
            telemetry = GatewayTelemetry()
        if providers is None:
            from friday import provider_health as ph
            providers = sorted(ph.latest_by_provider(telemetry.recent(limit=500)))
        if not providers:
            return []
        from friday import provider_health as ph
        verdicts = ph.assess(telemetry, providers)
        return [Route(p, v.state, getattr(v, "reason", "") or "") for p, v in sorted(verdicts.items())]
    except Exception as exc:  # noqa: BLE001
        logger.warning("self-model: provider health unreadable: %s", exc)
        return [Route(p, UNPROBED, f"health unreadable: {exc}"[:120]) for p in (providers or [])]


def snapshot(*, tool_count: int | None = None, providers: list[str] | None = None,
             telemetry=None) -> Snapshot:
    """Assemble the self-model from what the runtime knows now.

    `tool_count` is what the caller can see of the MCP surface (the room
    agent's router; None when the server is not answering). `providers` are
    the model providers the caller routes through; their health comes from
    the gateway ledger, never from configuration.
    """
    off = switches()
    snap = Snapshot(taken_at=time.time(),
                    host=f"{platform.system()} {platform.release()}".strip(),
                    modalities=_modalities(off), families=_families(off),
                    routes=_routes(telemetry, providers), switched_off=off,
                    tool_count=tool_count)
    snap.limits.append("What you cannot do from here yet, say in one line, then say what you can do instead.")
    return snap
