"""
Hermes MODEL_GATEWAY, reachable from Friday's production path.

`friday/model_gateway.py` is the mechanism, `friday/toolsets/model_gateway.py`
the one implementation of each ability (callable by the objective engine
too); these MCP tools make it PRODUCTION_REACHABLE for the model. Three
tools, deliberately few:

    model_providers  what Hermes can broker right now (FR-071/072)
    model_infer      one bounded inference through the gateway (FR-070/076)
    model_usage      the telemetry ledger, per objective (FR-080/055)

`model_infer` is what Friday's own brains call when a request needs a
reasoning tier beyond the fast default; it is NOT a way for the model to
start Hermes work - that is `hermes_delegate`, a different door.
"""
from __future__ import annotations

import logging

from friday import contracts as c
from friday import model_gateway as mg
from friday.toolsets import model_gateway as impl

logger = logging.getLogger("friday-agent")


def _execute(request: str, fn, *args, **kwargs) -> dict:
    run = c.Run.create(request, capability="model_gateway")
    result = fn(run, *args, **kwargs)
    run.transition("completed" if run.all_succeeded else "partial",
                   None if run.all_succeeded else (result.error or "not verified"))
    return result.to_dict()


def register(mcp):
    @mcp.tool()
    def model_providers() -> dict:
        """Which model providers Hermes can broker for Friday right now, with
        their route kind (api / subscription / free_tier / local) and whether
        they are authenticated. Queried live from Hermes, never hard-coded.
        Upstream cloud providers still see the compiled context; Hermes
        brokers credentials, it does not make cloud inference local."""
        return _execute("model providers", impl.model_providers)

    @mcp.tool()
    def model_infer(prompt: str, task_class: str = mg.STANDARD,
                    quality_tier: str = "", objective_id: str = "adhoc",
                    system: str = "", escalate: bool = False) -> dict:
        """One inference-only request through the Hermes model gateway: no
        tools, no subagents, no Hermes session. Use for reasoning that
        deserves a stronger model than the fast default. task_class is one
        of TRIVIAL, SIMPLE, STANDARD, COMPLEX, LONG_RUNNING, CRITICAL and
        sets the token budget; quality_tier is fast, standard or deep."""
        return _execute(f"model infer: {prompt[:60]}", impl.model_infer, prompt,
                        task_class=task_class, quality_tier=quality_tier,
                        objective_id=objective_id, system=system,
                        escalate=escalate)

    @mcp.tool()
    def model_usage(objective_id: str = "", limit: int = 20) -> dict:
        """Gateway usage telemetry: calls, tokens and routes per objective and
        worker, plus the calls responsible for the largest token spend."""
        return _execute("model usage", impl.model_usage, objective_id, limit=limit)
