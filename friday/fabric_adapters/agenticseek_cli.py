"""
agenticSeek: a fully local autonomous agent, as an isolated CLI worker.

GPL-3.0. This is the case G6 in the integration gap audit was about: a
copyleft upstream had NO compliant path in, because the only working modes
imported code into Friday's process. CLI is an isolated mode - a subprocess
is a process boundary - so `Provider.__post_init__` accepts the descriptor
while still refusing ADAPTER for the same upstream (test in
test_fabric_execution: a copyleft provider may declare CLI but not ADAPTER).

It drives its own browser and its own local model, so it is `moderate` risk
and `paid` in compute even when it costs no API key.
"""
from __future__ import annotations

from friday import fabric, fabric_cli
from friday.fabric_adapters import _cli_adapter

UPSTREAM = "agenticseek"

DESCRIPTOR = fabric.Provider(
    id="agenticseek_cli",
    family="orchestration",
    upstream=UPSTREAM,
    operations=("version", "ask"),
    risk="medium",
    license_mode=fabric.COPYLEFT,
    integration_mode=fabric.CLI,
    permissions=("orchestration.local_agent",),
    open_operations=("version",),
    cost_class="paid",
    model_required=True,
    commit="ae57a2357745a9706cb12d0fd76d954c84d166fa",
    notes=("GPL-3.0, so isolated by construction: CLI subprocess only, never "
           "imported. Runs a local model and its own browser; gated behind "
           "orchestration.local_agent."),
)

BOOTSTRAP = fabric_cli.Bootstrap(
    check=("python", "-c", "import sources.agents; print('ok')"),
    install=("uv", "sync"),
)

COMMANDS = {
    "version": fabric_cli.Command(
        argv=("python", "-c", "import sources.agents; print('ok')"), timeout=30.0),
    "ask": fabric_cli.Command(
        argv=("python", "cli.py", "--query", "{query}"), timeout=900.0),
}

start, stop, health, call = _cli_adapter.make(DESCRIPTOR, BOOTSTRAP, COMMANDS)
