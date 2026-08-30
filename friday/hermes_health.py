"""
Hermes connectivity, diagnosed by layer.

Why this module exists
----------------------

The RC1 live regression produced this pair of facts at the same moment:

- `hermes gateway status` reported a healthy Windows Scheduled Task with
  live PIDs;
- Friday told the boss "Hermes gateway is currently disconnected" and asked
  whether it should continue reading telemetry.

Both came from representing Hermes health as ONE boolean. A single stale
layer - the Friday↔Hermes bridge - collapsed into "the gateway is dead", and
because the fault was unclassified, Friday could not choose a repair and
handed the decision to the user instead. That is the behavior the autonomy
contract forbids: a recoverable internal problem is not a question.

So health here is eight signals read from the process outwards, and the
FIRST one that is down is the cause. Everything outside it is a symptom -
an unreachable WorkRun when the process is dead tells you nothing new.

Recovery is derived from the failed layer, never from the fact that
something is wrong. In particular a restart is reserved for a genuinely dead
process, because Hermes does not resume delegated children across a process
restart: an active child becomes `unknown`, since Hermes cannot prove which
side effects already happened. Restarting under live work therefore destroys
provable state, so live work forces reconcile-first.

This module diagnoses and decides. It does not execute the repair, and it
holds no process handles - callers drive the existing
`hermes gateway status|start|restart` lifecycle rather than inventing a
second daemon manager.
"""

from __future__ import annotations

from enum import Enum

# Restored from the .pyc oracle: proven by a LOAD_CONST/STORE_NAME
# pair in the running system's bytecode, present in no source candidate.
LAYERS: tuple[str, ...] = (
    'gateway_process_alive',
    'gateway_http_reachable',
    'friday_profile_registered',
    'friday_to_gateway_connected',
    'mcp_server_alive',
    'mcp_sse_connected',
    'hermes_bridge_ready',
    'active_workrun_reachable',
)


class Recovery(Enum):
    """What the failed layer says to do about itself."""

    NONE = "none"
    RESTART_GATEWAY = "restart_gateway"
    REREGISTER_PROFILE = "reregister_profile"
    RECONNECT_BRIDGE = "reconnect_bridge"
    RESTART_MCP = "restart_mcp"
    RECONNECT_SSE = "reconnect_sse"
    RECONCILE_WORKRUN = "reconcile_workrun"
    #: The process looks dead but work is in flight. Inspect side effects
    #: before restarting anything - never dispatch a mutation twice.
    RECONCILE_THEN_RESTART = "reconcile_then_restart"


#: Layer → its own repair. Note `gateway_http_reachable` maps to a restart:
#: a live process that answers nothing is wedged, not merely disconnected.
_REPAIR: dict[str, Recovery] = {
    "gateway_process_alive": Recovery.RESTART_GATEWAY,
    "gateway_http_reachable": Recovery.RESTART_GATEWAY,
    "friday_profile_registered": Recovery.REREGISTER_PROFILE,
    "friday_to_gateway_connected": Recovery.RECONNECT_BRIDGE,
    "mcp_server_alive": Recovery.RESTART_MCP,
    "mcp_sse_connected": Recovery.RECONNECT_SSE,
    "hermes_bridge_ready": Recovery.RECONNECT_BRIDGE,
    "active_workrun_reachable": Recovery.RECONCILE_WORKRUN,
}

#: The two layers whose loss actually means "the gateway itself is gone".
#: Anything further out is a connection to a gateway that is still there.
_GATEWAY_LAYERS = ("gateway_process_alive", "gateway_http_reachable")


class Report:
    """One layered diagnosis. Immutable, cheap, and safe to log."""

    def __init__(self, signals: dict[str, bool],
                 active_workruns: tuple[str, ...] = ()) -> None:
        missing = set(LAYERS) - set(signals)
        if missing:
            raise ValueError(
                f"hermes health needs all {len(LAYERS)} signals; missing "
                f"{sorted(missing)}")
        self.signals = {name: bool(signals[name]) for name in LAYERS}
        self.active_workruns = tuple(active_workruns)

    @property
    def failed_layer(self) -> str:
        """The innermost broken layer, or "" when everything is up."""
        for name in LAYERS:
            if not self.signals[name]:
                return name
        return ""

    @property
    def healthy(self) -> bool:
        return self.failed_layer == ""

    @property
    def gateway_is_dead(self) -> bool:
        """True only when the GATEWAY is gone - not when a bridge is stale.

        This is the claim the regression made wrongly, so it is a named
        property rather than something a caller re-derives.
        """
        return self.failed_layer in _GATEWAY_LAYERS

    @property
    def recovery(self) -> Recovery:
        failed = self.failed_layer
        if not failed:
            return Recovery.NONE
        if self.gateway_is_dead and self.active_workruns:
            # Restarting now would turn provable worker state into `unknown`.
            return Recovery.RECONCILE_THEN_RESTART
        return _REPAIR[failed]

    @property
    def blocks_read_only_telemetry(self) -> bool:
        """Always False, and deliberately explicit.

        Read-only telemetry is safe and already inside an accepted objective.
        The observed wrong behavior was asking the boss "should I proceed
        with trying to pull telemetry?" while a connection was unhealthy.
        No connectivity fault converts safe, already-authorised, read-only
        work into a user decision.
        """
        return False

    @property
    def summary(self) -> str:
        """A statement of what broke. Never a question to the user."""
        if self.healthy:
            return "Hermes healthy: all 8 connectivity layers up."
        parts = [f"Hermes fault at layer {self.failed_layer}",
                 f"repair={self.recovery.value}"]
        if self.gateway_is_dead:
            parts.append("gateway itself is down")
        else:
            parts.append("gateway process is up; a connection layer is stale")
        if self.active_workruns:
            parts.append(
                "work in flight: " + ", ".join(self.active_workruns)
                + " - inspect side effects before retrying")
        return "; ".join(parts) + "."

    def __repr__(self) -> str:                                # pragma: no cover
        return f"<Report failed={self.failed_layer or 'none'!r}>"


# ---------------------------------------------------------------------------
# Production adapters (agent process)
# ---------------------------------------------------------------------------
#
# The agent reaches Hermes only through the MCP server, so its view of the
# eight layers is necessarily partial: it can prove the MCP link and infer
# the bridge from tool answers, but it cannot poke the gateway process
# directly. Signals it cannot cheaply observe default to True - a layer is
# claimed dead only on evidence, because the whole point of this module is
# to stop healthy layers being reported as failures.


def live_probe_factory(mcp_base_url: str, timeout: float = 3.0):
    """A probe for the live agent: proves MCP reachability over HTTP.

    Bridge-level truth arrives separately - the CONNECTIVITY refusal text
    that triggered the probe already names the bridge - so this probe's job
    is the distinction the regression got wrong: is the MCP/gateway side
    actually gone, or is only the bridge stale?
    """
    import urllib.request

    def probe(active_workruns: tuple[str, ...] = ()) -> Report:
        signals = {name: True for name in LAYERS}
        try:
            request = urllib.request.Request(mcp_base_url, method="GET")
            with urllib.request.urlopen(request, timeout=timeout):
                pass
        except Exception:                                    # noqa: BLE001
            # Distinguishing refused/timeout is not worth another layer:
            # either way the MCP server is not answering this process.
            signals["mcp_server_alive"] = False
            signals["mcp_sse_connected"] = False
            signals["hermes_bridge_ready"] = False
            signals["active_workrun_reachable"] = False
            return Report(signals, active_workruns=active_workruns)
        # MCP answers, so the fault the caller saw is further in: the
        # bridge (or the specific WorkRun) is what is stale.
        signals["hermes_bridge_ready"] = False
        signals["active_workrun_reachable"] = False
        return Report(signals, active_workruns=active_workruns)

    return probe


def live_recover_factory(call_capability):
    """A recover for the live agent, driven by the report's decision.

    RECONNECT_BRIDGE: one `hermes_status` round-trip through the MCP port.
    The supervisor in the MCP server starts/reconnects on demand, so a
    successful answer IS the bridge back up - verified, not assumed.

    RESTART_MCP / RESTART_GATEWAY: not executed from inside an objective.
    Process lifecycle belongs to the existing `hermes gateway` tooling and
    the repo restart scripts; an executor that silently restarts servers
    would be a second daemon manager. Returns False so the caller degrades
    to the bounded retry, and the diagnosis is still recorded.

    RECONCILE_THEN_RESTART / RECONCILE_WORKRUN: reconciliation is already
    the executor's own reconcile path; nothing extra to run here.
    """
    async def recover(report: Report) -> bool:
        if report.recovery in (Recovery.RECONNECT_BRIDGE,
                               Recovery.RECONNECT_SSE):
            try:
                answer = await call_capability("hermes_status", {})
            except Exception:                                # noqa: BLE001
                return False
            if isinstance(answer, dict) and str(
                    answer.get("status") or "").lower() in (
                    "not_configured", "failed"):
                return False
            return True
        return False

    return recover
