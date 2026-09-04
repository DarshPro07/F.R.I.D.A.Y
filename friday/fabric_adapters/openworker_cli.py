"""
OpenWorker: a headless coding coworker, as a CLI worker.

MIT, pinned. `openworker <skill> --cwd <dir> --mode auto` runs one skill
against a workspace and exits. Friday keeps it in the `coding` family beside
Hermes as an OPTIONAL worker - Hermes remains the mandatory engine
(NON_NEGOTIABLE 2); this is a second pair of hands for a bounded, low-risk
task when the owner explicitly asks for one.

`--mode` is pinned to `plan` for `plan` and `auto` for `run`; the
`bypass-approvals` mode upstream offers is deliberately not reachable.
"""
from __future__ import annotations

from friday import fabric, fabric_cli
from friday.fabric_adapters import _cli_adapter

UPSTREAM = "openworker"

DESCRIPTOR = fabric.Provider(
    id="openworker_cli",
    family="coding",
    upstream=UPSTREAM,
    operations=("version", "plan", "run"),
    risk="medium",
    license_mode=fabric.PERMISSIVE,
    integration_mode=fabric.CLI,
    permissions=("coding.workspace_write",),
    open_operations=("version", "plan"),
    cost_class="moderate",
    model_required=True,
    commit="fb1bfc627201f1e159a9380ada25e954174271a5",
    notes=("MIT. Optional coding worker; Hermes stays the engine. `run` "
           "writes into the workspace and so needs coding.workspace_write; "
           "`plan` only reads. bypass-approvals mode is not exposed."),
)

BOOTSTRAP = fabric_cli.Bootstrap(
    check=("python", "-c", "import coworker; print(coworker.__name__)"),
    install=("uv", "sync"),
)

COMMANDS = {
    "version": fabric_cli.Command(
        argv=("python", "-c", "import coworker; print('ok')"), timeout=30.0),
    "plan": fabric_cli.Command(
        argv=("python", "-m", "coworker.cli", "{skill}",
              "--cwd", "{workspace}", "--mode", "plan"),
        timeout=600.0),
    "run": fabric_cli.Command(
        argv=("python", "-m", "coworker.cli", "{skill}",
              "--cwd", "{workspace}", "--mode", "auto"),
        timeout=1800.0),
}

start, stop, health, call = _cli_adapter.make(DESCRIPTOR, BOOTSTRAP, COMMANDS)
