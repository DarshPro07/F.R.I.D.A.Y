"""
The shared shape of a CLI-mode adapter: invoke, work, exit.

Every command-line upstream (strix, openworker, cline, agenticseek, ...)
needs the same four things - a descriptor, a bootstrap check, a table of
commands, and start/health/call that defer to `friday.fabric_cli`. Writing
those four by hand per adapter is how they drift; this is the one copy.

`start()` runs nothing: a CLI provider has no long-lived process. `health()`
runs the upstream's own version check and reports UNAVAILABLE with the
install command when the clone is not built - installation is never a side
effect of a question (FABRIC-CLI-01). `call()` runs exactly one declared
command through the no-shell, placeholder-as-one-argv-element path.

None of these upstreams are visible in Friday's UI. Friday reaches them
through `capability_use(family, operation)`; the operator sees a family and a
result, never the module.
"""
from __future__ import annotations

from friday import fabric_cli


def make(descriptor, bootstrap: fabric_cli.Bootstrap, commands: dict):
    """(start, stop, health, call) for a CLI adapter module."""

    def start():
        return None

    def stop(handle=None):
        return None

    def health(handle=None) -> dict:
        return fabric_cli.health(descriptor, bootstrap)

    def call(operation: str, handle=None, *, run_id: str = "", **arguments):
        # `secrets=` arrives from fabric.call() for providers that declare
        # any; a CLI upstream gets them as environment, never as argv, so
        # they can never appear in the redacted evidence line.
        secrets = arguments.pop("secrets", None) or {}
        table = commands
        if secrets:
            table = {
                name: fabric_cli.Command(
                    argv=cmd.argv, timeout=cmd.timeout, output=cmd.output,
                    output_path=cmd.output_path, success_exit=cmd.success_exit,
                    cwd=cmd.cwd, env={**cmd.env, **secrets})
                for name, cmd in commands.items()}
        return fabric_cli.run(descriptor, operation, table, run_id=run_id,
                              **arguments)

    return start, stop, health, call
