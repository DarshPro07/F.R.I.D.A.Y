"""MCP adapter for the SharedBrainAdapter (friday/brain.py).

Thin by rule. The brain answers "what do we already know?" - it never
owns tasks (ObjectiveRun), execution (WorkRun), or procedures (Skills).
synthesize is deliberately NOT exposed: recalled evidence goes to the
already-selected model.
"""

from __future__ import annotations

import logging
import threading

logger = logging.getLogger("friday-agent.tools.brain")

_lock = threading.Lock()
_brain = None


def brain():
    from friday.brain import SharedBrainAdapter

    global _brain
    with _lock:
        if _brain is None:
            _brain = SharedBrainAdapter()
        return _brain


def configure(new_brain) -> None:
    """Test seam. Production never calls this."""
    global _brain
    with _lock:
        _brain = new_brain


def register(mcp):

    @mcp.tool()
    def brain_recall(query: str = "", entity: str = "",
                     budget: str = "bounded") -> dict:
        """
        What do we already know? Check BEFORE re-reading files or
        re-researching: durable facts + document snippets from the shared
        Friday/Hermes brain, server-side packed to a token budget
        (trivial/bounded/project/deep). Returns evidence with provenance
        for you to reason over - never a synthesized answer.
        """
        try:
            answer = brain().recall(query, entity=entity, budget=budget)
        except Exception as exc:                             # noqa: BLE001
            # GJ6: the brain being down never blocks work.
            logger.warning("brain_recall degraded: %s", exc)
            return {"status": "unavailable",
                    "note": "shared brain unreachable; proceed without "
                            "it and rely on files/search"}
        return {"status": "ok", **answer.compact()}

    @mcp.tool()
    def brain_remember(fact: str, provenance: str, entity: str = "",
                       kind: str = "fact", ttl: str = "") -> dict:
        """
        Save ONE durable fact to the shared Friday/Hermes brain: verified
        project facts, architecture decisions, root-cause lessons,
        research findings - always with provenance. NOT for execution
        status, transient chatter, or anything secret/banking-shaped
        (those are refused before ingestion). kind: fact | event |
        preference | commitment | belief. Optional ttl like '30d'.
        """
        from friday.brain import AdmissionRefused

        try:
            out = brain().remember(fact, provenance=provenance,
                                   entity=entity, kind=kind, ttl=ttl)
        except AdmissionRefused as refusal:
            return {"status": "refused", "reason": str(refusal)}
        except Exception as exc:                             # noqa: BLE001
            logger.warning("brain_remember degraded: %s", exc)
            return {"status": "unavailable",
                    "note": "shared brain unreachable; the fact was NOT "
                            "saved - say so rather than pretending"}
        return {"status": out.get("status", "ok"),
                "fact_id": out.get("id")}

    @mcp.tool()
    def brain_entity(name: str) -> dict:
        """
        One known person/company/project card from the shared brain -
        zero model calls, sub-second. Misses return suggestions, never
        errors.
        """
        try:
            return brain().entity(name)
        except Exception as exc:                             # noqa: BLE001
            logger.warning("brain_entity degraded: %s", exc)
            return {"status": "unavailable"}

    @mcp.tool()
    def brain_forget(fact_id: str, reason: str = "") -> dict:
        """
        Expire one remembered fact by its fact_id (from brain_recall).
        Audit trail kept; use when a fact is superseded or was wrong.
        """
        try:
            return brain().forget(fact_id, reason=reason)
        except Exception as exc:                             # noqa: BLE001
            logger.warning("brain_forget degraded: %s", exc)
            return {"status": "unavailable"}
