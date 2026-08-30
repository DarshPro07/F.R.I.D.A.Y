"""MCP adapter for the Capability Fabric (friday/fabric.py).

Thin by rule: the registry, routing, lifecycle and honesty live in
`friday.fabric`; this translates them into something a voice assistant can say.

The split between the two listing tools is the whole user-facing argument of
the fabric and is not cosmetic:

    capability_families   what the boss hears. Outcomes - "browser", "memory",
                          "media" - and whether each one works right now.
    capability_providers  the diagnostic surface. Brands, pinned commits,
                          licenses, integration modes. Asked for, never
                          volunteered.

"List what you can do" answering with forty-seven raw provider names is the
failure this shape exists to prevent, and it is the same ruling the connector
plane already follows.
"""

from __future__ import annotations

import logging

logger = logging.getLogger("friday-agent.tools.fabric")


def register(mcp):

    @mcp.tool()
    def capability_families() -> dict:
        """
        The kinds of work Friday can currently take on, and whether each one
        is working - coding, browser, scraping, research, code intelligence,
        memory, media, voice, social, security and so on.

        Answers "what can you do" and "what is broken right now" without
        naming any internal tool, provider or repository. Use
        `capability_providers` when the boss explicitly asks which
        implementation is behind a family.
        """
        from friday import fabric

        families = fabric.family_report()
        working = [f["family"] for f in families
                   if f["state"] in (fabric.READY, fabric.DEGRADED)]
        blocked = [f for f in families
                   if f["state"] not in (fabric.READY, fabric.DEGRADED)]
        return {
            "families": families,
            "working": working,
            "needs_attention": blocked,
            "say": (f"{len(working)} capability families are available"
                    + (f"; {len(blocked)} need attention" if blocked else "")),
        }

    @mcp.tool()
    def capability_providers(family: str = "") -> dict:
        """
        Diagnostic: which upstream implementation sits behind each capability
        family, the exact commit it is pinned to, its license mode and whether
        it is running. Optionally filtered to one family.

        This is the answer to "which tool did you actually use" and to a
        license or supply-chain question. It is not the answer to "what can
        you do" - that is `capability_families`.
        """
        from friday import fabric

        rows = fabric.report()
        if family:
            rows = [r for r in rows if r["family"] == family]
        return {"providers": rows, "count": len(rows),
                "families": list(fabric.families())}

    @mcp.tool()
    def capability_health(provider: str) -> dict:
        """
        Probe one capability provider and report what it actually said -
        READY, DEGRADED, AUTH_REQUIRED or UNAVAILABLE - with the reason.

        Activates the provider if it was dormant, which is the only way to
        learn anything real about it.
        """
        from friday import fabric

        try:
            activation = fabric.activate(provider)
        except fabric.FabricError as exc:
            return {"provider": provider, "state": "UNKNOWN", "error": str(exc)}
        return {"provider": provider, "state": activation.state,
                "detail": activation.detail,
                "say": f"{provider} is {activation.state.lower()}"}

    @mcp.tool()
    def capability_processes() -> dict:
        """
        Whether any capability provider that owns an OS process is running
        twice. A duplicate is the stale-restart case, and it is reported as
        data rather than left for somebody to notice via a port conflict.
        """
        from friday import fabric

        result = fabric.processes()
        duplicates = result.get("duplicates", [])
        return {**result,
                "say": ("no duplicate capability processes" if not duplicates
                        else f"{len(duplicates)} provider(s) running twice")}

    @mcp.tool()
    def capability_reload() -> dict:
        """
        Re-scan for capability providers without restarting Friday.

        The fabric discovers adapters once and caches the registry, so a
        provider added to `friday/fabric_adapters/` after the server booted is
        invisible until the cache is dropped. That was a full process restart -
        which, for the MCP server the whole voice agent talks to, means a
        window where Friday can do nothing. This drops the cache and
        re-discovers, so a newly-added capability becomes reachable in place.

        Returns what changed, by id, so the caller can see a provider actually
        appeared rather than trusting a count.
        """
        from friday import fabric

        before = set(fabric.registry())
        after = set(fabric.reload())
        added = sorted(after - before)
        removed = sorted(before - after)
        return {
            "providers": len(after),
            "added": added,
            "removed": removed,
            "say": (f"reloaded: {len(after)} providers"
                    + (f", +{len(added)}" if added else "")
                    + (f", -{len(removed)}" if removed else "")),
        }

    @mcp.tool()
    def capability_use(family: str, operation: str,
                       arguments: dict | None = None) -> dict:
        """
        Do one piece of work through whichever provider in a capability family
        is cheapest, least risky and actually up, falling back down the chain
        if the first one cannot answer.

        The boss names the outcome - a family and an operation - and Friday
        picks the backend. A failure here names the layer that failed and does
        not fail whatever objective asked for it.
        """
        from friday import contracts as c
        from friday import fabric

        # The model names the family, so it can name one that does not exist.
        # call_with_fallback raises FabricError for an unknown family or
        # operation; catch it here so a wrong name is a failed result that
        # names the layer, not an exception that fails the whole objective -
        # which is exactly what this tool's contract promises.
        try:
            result = fabric.call_with_fallback(family, operation,
                                               **(arguments or {}))
        except fabric.FabricError as exc:
            started = c.started("capability.use", "capability.use")
            result = c.failed(started, str(exc))
        return {
            "status": result.status,
            "output": result.output,
            "error": result.error,
            "fell_back": [s for s in result.side_effects
                          if s.startswith("fell back")],
            "verified_by": (result.verification.method
                            if result.verification else None),
        }
