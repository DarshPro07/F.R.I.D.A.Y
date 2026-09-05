"""
MCP adapter for the Phase 1A system toolset.

Thin on purpose. All logic lives in friday/toolsets/system.py and returns
ActionResult; this layer only creates a Run per call, persists it, and
serialises the result. Keeping the adapter dumb is what lets the Cloud/Edge
split later swap the implementation without changing the tool contract.

APPROVAL
--------
ASK-gated tools return a CANCELLED result carrying APPROVAL_REQUIRED. They are
deliberately NOT self-approvable: there is no MCP tool that grants permission,
because the agent could then call it unprompted and approve itself. Until a
real user-approval channel exists, pre-approval is explicit and
out-of-band:

    ADA_PREAPPROVED_TOOLS=apps.close,clipboard.write

That is a decision the human makes in configuration, not one the model can
make mid-conversation.
"""

from __future__ import annotations

import os

from friday import contracts as c
from friday.policy import PolicyEngine, PolicyError
from friday.store import DEFAULT_DB, Store
from friday.toolsets import system as S

_store: Store | None = None
_engine: PolicyEngine | None = None


def _get_store() -> Store:
    global _store
    if _store is None:
        _store = Store(os.getenv("ADA_DB") or DEFAULT_DB)
    return _store


def _get_engine() -> PolicyEngine:
    """Engine pre-loaded from configuration only - never from user memory."""
    global _engine
    if _engine is None:
        _engine = PolicyEngine()
        raw = os.getenv("ADA_PREAPPROVED_TOOLS", "")
        for tool_id in (t.strip() for t in raw.split(",") if t.strip()):
            try:
                _engine.approve_for_session(tool_id)
            except PolicyError:
                # An unknown or DENY tool in config is ignored, not honoured.
                continue
    return _engine


def _execute(request: str, capability: str, fn, *args, **kwargs) -> dict:
    """Run one capability inside a fresh Run, persist it, return the wire dict."""
    run = c.Run.create(request, capability=capability)
    result = fn(run, *args, engine=_get_engine(), **kwargs)
    run.transition("completed" if run.all_succeeded else "partial",
                   None if run.all_succeeded else (result.error or "not verified"))
    try:
        _get_store().save_run(run)
    except Exception:  # persistence must never turn a good action into a failure
        pass
    return result.to_dict()


def register(mcp):

    @mcp.tool()
    def system_get_info() -> dict:
        """
        Information about the USER'S OWN COMPUTER: OS, CPU cores, total RAM,
        uptime, hostname. Use for "what is this machine", "how much RAM do I
        have". Distinct from get_system_info, which describes the agent runtime.
        """
        return _execute("system info", "system", S.system_get_info)

    @mcp.tool()
    def system_list_processes(top: int = 10, sort_by: str = "memory") -> dict:
        """
        Real running processes on the user's computer, sorted by 'memory',
        'cpu' or 'name'. Use for "what's using the most RAM", "what's running".
        """
        return _execute("list processes", "system", S.system_list_processes,
                        top=top, sort_by=sort_by)

    @mcp.tool()
    def system_resource_usage() -> dict:
        """Current CPU, memory and disk usage of the user's computer."""
        return _execute("resource usage", "system", S.system_resource_usage)

    @mcp.tool()
    def system_pressure() -> dict:
        """The resource governor's view: pressure level (NORMAL / ELEVATED /
        HIGH / CRITICAL) with the measured reasons, active workers and
        browsers, queue depth, caps, and the banner to show when
        concurrency was reduced to protect the machine."""
        from friday.toolsets import model_gateway as MG
        return _execute("resource pressure", "system", MG.system_pressure)

    @mcp.tool()
    def system_diagnostics(sections: str = "") -> dict:
        """One operational view of Friday (PRD 12.3): build identity, objective
        and memory store health, provider status, Hermes/worker health,
        browser connection, voice gateway, MCP/capability health, queue
        depth, resource pressure and the most recent critical failures.
        Secrets are redacted. `sections` narrows it, comma-separated."""
        from friday.toolsets import model_gateway as MG
        return _execute("diagnostics", "system", MG.system_diagnostics, sections=sections)

    @mcp.tool()
    def system_wifi_status() -> dict:
        """Wi-Fi interface state, SSID and signal strength on the user's computer."""
        return _execute("wifi status", "system", S.system_wifi_status)

    @mcp.tool()
    def apps_open(name: str) -> dict:
        """
        Open an application on the user's computer, e.g. "spotify", "chrome",
        "vs code", "calculator", "file explorer".

        Only claim the app opened when may_claim_completion is true. A
        'partial' status means the launch was attempted but no matching
        process appeared - say that, do not say it opened.
        """
        return _execute(f"open {name}", "system", S.apps_open, name)

    @mcp.tool()
    def apps_close(name: str) -> dict:
        """
        Close an application. Requires approval; may return APPROVAL_REQUIRED,
        in which case tell the user it needs their go-ahead.
        """
        return _execute(f"close {name}", "system", S.apps_close, name)

    @mcp.tool()
    def apps_focus(name: str) -> dict:
        """Bring an already-open window to the foreground by title match."""
        return _execute(f"focus {name}", "system", S.apps_focus, name)

    @mcp.tool()
    def apps_list_known() -> dict:
        """Applications discoverable on this machine (registry + Start Menu)."""
        return _execute("list known apps", "system", S.apps_list_known)

    @mcp.tool()
    def volume_get() -> dict:
        """Current master volume percentage and mute state."""
        return _execute("get volume", "system", S.volume_get)

    @mcp.tool()
    def volume_set(level: int) -> dict:
        """Set master volume to a percentage 0-100, then read it back to confirm."""
        return _execute(f"set volume {level}", "system", S.volume_set, level)

    @mcp.tool()
    def clipboard_read() -> dict:
        """Read the user's clipboard contents."""
        return _execute("read clipboard", "system", S.clipboard_read)

    @mcp.tool()
    def clipboard_write(text: str) -> dict:
        """
        Replace the user's clipboard contents. Requires approval; may return
        APPROVAL_REQUIRED.
        """
        return _execute("write clipboard", "system", S.clipboard_write, text)
