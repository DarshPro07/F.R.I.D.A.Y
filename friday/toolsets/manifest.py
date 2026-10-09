"""
Capability manifest as a capability-runtime implementation (PRD 9.6).

`friday/tools/fabric_control.py::capability_manifest` is the MCP face; this
is the `run`-first function the objective engine binds to. Read-only:
nothing is activated to build the manifest.
"""
from __future__ import annotations

from friday import contracts as c
from friday import manifest as M
from friday.policy import PolicyEngine, default_engine


def capability_manifest(run: c.Run, capability_id: str = "", *, probe: bool = False,
                        engine: PolicyEngine = default_engine) -> c.ActionResult:
    """`probe` is the one opt-in that does work: it runs every capability's
    health probe (and each executor's `--version`) to name what is installed
    but failing. A named id is always probed, so `probe` only matters without one."""
    started = c.started(run.run_id, "capability.manifest")
    if capability_id:
        entry = M.describe(capability_id)
        if entry is None:
            return run.record(c.failed(started, f"nothing is registered as {capability_id!r}"))
        return run.record(started.finish(status=c.OBSERVED, output=entry))
    if probe:
        return run.record(started.finish(status=c.OBSERVED, output=M.health_report()))
    return run.record(started.finish(status=c.OBSERVED, output=M.summary()))
