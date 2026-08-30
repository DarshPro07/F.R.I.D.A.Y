"""MCP adapter for the ConnectorControlPlane (friday/connectors/).

Thin by rule: logic lives in friday.connectors.plane; this registers the
tools and translates FlowSteps into speakable dicts. No secret value can
appear in any return - the plane only ever handles opaque refs.
"""

from __future__ import annotations

import logging
import threading

logger = logging.getLogger("friday-agent.tools.connectors")

_lock = threading.Lock()
_plane = None


def plane():
    from friday.connectors.plane import ConnectorControlPlane

    global _plane
    with _lock:
        if _plane is None:
            _plane = ConnectorControlPlane()
        return _plane


def configure(new_plane) -> None:
    """Test seam. Production never calls this."""
    global _plane
    with _lock:
        _plane = new_plane


def _spoken(step) -> dict:
    return {"status": step.action, "say": step.say, **step.detail}


def register(mcp):

    @mcp.tool()
    def connector_list() -> dict:
        """
        Every AI provider/connector Hermes can currently reach: name, auth
        type, whether it is already authenticated, available models, and
        the ONE human step (if any) connecting it would need. Discovered
        live from the Hermes registry - new provider plugins appear here
        automatically.
        """
        found = plane().discover_connectors()
        # Present intent-first (boss ruling): a normal user should never
        # face 47 raw registry entries. Connected + featured first, the
        # long tail grouped, the registry unchanged underneath.
        ready = [c for c in found if c["authenticated"]]
        needs_key = [c for c in found if not c["authenticated"]
                     and c["auth_type"] == "api_key"]
        needs_signin = [c for c in found if not c["authenticated"]
                        and c["auth_type"] != "api_key"]
        return {
            "connected": ready,
            "available_with_api_key": [c["connector"] for c in needs_key],
            "available_with_signin": [c["connector"] for c in needs_signin],
            "count": len(found),
            "presentation_hint": (
                "Offer the connected ones and ask what the boss wants to "
                "add - never recite the whole catalog."),
        }

    @mcp.tool()
    def connector_describe(connector: str) -> dict:
        """
        Everything known about one connector: registry metadata, auth
        type, stored connection state (opaque credential ref only), and
        the human step a connection would require. Use before connecting.
        """
        return plane().describe_connector(connector)

    @mcp.tool()
    def connector_connect(connector: str, model: str = "") -> dict:
        """
        Connect a provider so Hermes can use it. Chooses the flow from the
        provider's own auth type: API key -> a secure entry window opens
        on the user's screen (Friday cannot read the field; the key goes
        straight into Windows Credential Manager); OAuth/subscription ->
        the provider's official sign-in is the only human step. Configures
        Hermes, selects the model, verifies, and reports readiness. Speak
        the returned `say` to the boss.
        """
        return _spoken(plane().begin_connection(connector, model=model))

    @mcp.tool()
    def connector_verify(connector: str, model: str = "") -> dict:
        """
        Re-check one connector against the live Hermes registry: is it
        authenticated, is the model selected, is it healthy. Use after
        the boss completes a sign-in, or when a provider call failed with
        an auth error. Also the resume trigger: a run waiting at an auth
        boundary continues once this reports connected.
        """
        step = plane().verify_connection(connector, expected_model=model)
        spoken = _spoken(step)
        if step.action == "done":
            # The recoverable sub-objective is finished: wake every run
            # parked at an auth boundary. Deterministic - no model call.
            try:
                from friday import objective_cli
                from friday.continuous import resume_after_auth

                resumed = resume_after_auth(
                    objective_cli._db(), reason=f"{connector} verified")
                if resumed:
                    spoken["resumed_runs"] = resumed
                    spoken["say"] += (" I'm resuming the task that was "
                                      "waiting on it.")
            except Exception:                                # noqa: BLE001
                logger.exception("auth-resume hook failed (non-fatal)")
        return spoken

    @mcp.tool()
    def connector_status() -> dict:
        """
        The connector dashboard: every stored connection with status,
        default model, health, and live authenticated state. Credential
        values never appear - only opaque references.
        """
        return plane().status()

    @mcp.tool()
    def connector_smoke(connector: str, model: str = "") -> dict:
        """
        The READY gate: run one REAL inference through the production
        Hermes path and verify the EFFECTIVE provider/model (a silent
        fallback fails it). Authenticated does not mean working - only
        this check promotes a connector to READY. Speak the returned
        `say`.
        """
        return _spoken(plane().smoke_test(connector, model=model))

    @mcp.tool()
    def connector_repair(connector: str) -> dict:
        """
        Automatic connector repair after a provider failure: inspects the
        live registry, distinguishes a stale/absent credential from a
        transient fault, re-opens the correct auth flow only if genuinely
        needed, and never restarts processes for a credential problem.
        The parent objective stays parked and resumes after repair.
        """
        return _spoken(plane().repair(connector))
