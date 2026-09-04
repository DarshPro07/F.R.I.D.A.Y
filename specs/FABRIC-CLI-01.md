# FABRIC-CLI-01 — A `CLI` integration mode for command-line upstreams

**Closes:** G4, and G2 for seven upstreams
**Status:** specified, not built · **Depends on:** `FABRIC-PROC-01`,
`FABRIC-GATE-01`

## Why

`cline`, `crewai`, `strix`, `agenticseek`, `openhands`, `firstmate` and
`openworker` are command-line agents: invoke, work, exit. `INTEGRATION_MODES`
has `BUILTIN`, `ADAPTER`, `MCP`, `SKILL`, `SIDECAR`, `REFERENCE_ONLY` and
nothing that describes them. A contributor cannot write a descriptor for these
even in principle, which is why all seven sit unintegrated on disk.

This is the cheapest real execution mode: no port, no protocol, no long-lived
process. It is deliberately first after the supervisor.

## Interface

Add to `friday/fabric.py`:

```python
CLI = "CLI"                        # one-shot subprocess, exits when done
INTEGRATION_MODES = (..., CLI)
ISOLATED_MODES = frozenset({MCP, SIDECAR, SKILL, REFERENCE_ONLY, CLI})
```

`CLI` joins `ISOLATED_MODES`, which is the point of G6: a copyleft agent
becomes integrable without violating the licence invariant, because a
subprocess is a process boundary.

An adapter declaring `CLI` exposes:

```python
DESCRIPTOR = fabric.Provider(..., integration_mode=fabric.CLI, ...)

#: operation -> how to build the command line.
COMMANDS = {
    "plan": fabric_cli.Command(
        argv=("node", "dist/cli.js", "plan", "--task", "{task}"),
        timeout=120.0,
        output=fabric_cli.JSON_STDOUT,
        success_exit=(0,),
    ),
}
```

New module `friday/fabric_cli.py`:

```python
@dataclass(frozen=True)
class Command:
    argv: tuple[str, ...]           # {name} placeholders, filled from arguments
    timeout: float = 120.0
    output: str = "TEXT_STDOUT"     # TEXT_STDOUT | JSON_STDOUT | EXIT_CODE | FILE
    output_path: str = ""           # for FILE, relative to cwd, {} allowed
    success_exit: tuple[int, ...] = (0,)
    cwd: str = ""                   # default: the clone root

def run(provider_id: str, operation: str, **arguments) -> c.ActionResult
```

## Behaviour

**Argument substitution is not shell interpolation.** Each `{name}` is replaced
as one argv element, never re-split, never passed through a shell. `argv` is a
tuple and `shell=False` is not configurable. A task string containing
`; rm -rf /` becomes one harmless argument. This is the single most important
line in the spec.

**Unknown placeholders fail closed.** A `{name}` with no matching argument
raises before spawning, rather than sending the literal `{name}` to an agent.

**Timeouts kill.** On timeout the child is terminated through
`fabric_process.stop()` and the result is `failed` with the partial stdout tail
as evidence. A hung third-party agent must not hang a voice turn.

**Output contracts.**
- `TEXT_STDOUT` — stdout, stripped, capped at 100 KB.
- `JSON_STDOUT` — parsed; a parse failure is `failed`, not a silent string.
- `EXIT_CODE` — the code itself is the answer (linters, checks).
- `FILE` — read `output_path` after exit; missing file is `failed`.

**Verification is honest.** The `ActionResult` carries
`method="fabric.cli"` and evidence naming the exit code, argv (with argument
*values* redacted, keys kept), and duration. An exit code of 0 is stated as
what it is — the process did not complain — and never dressed up as a verified
outcome, matching how `fabric.call()` already labels bare return values.

**Concurrency.** One in-flight invocation per provider by default; a second
call returns `failed` with "busy" rather than queueing. Queueing is the
objective engine's job, not the fabric's.

## Bootstrapping

A CLI upstream needs its dependencies before it can run once. `Command` does
**not** install anything. Instead the adapter declares:

```python
BOOTSTRAP = fabric_cli.Bootstrap(
    check=("node", "dist/cli.js", "--version"),
    install=("npm", "ci"),
)
```

`health()` runs `check`; if it fails, the state is `AUTH_REQUIRED`-adjacent —
specifically `UNAVAILABLE` with detail `"not built: run install"`. Installation
is **never** automatic: `npm ci` on an unaudited clone is a supply-chain action
and needs the same explicit go-ahead as spending money. Expose it as an
operator command (`capability_repair`), not a side effect of a user asking a
question.

## Acceptance

Tests in `tests/test_fabric_cli.py`:

1. `{task}` containing `; whoami` reaches the child as one literal argument.
2. A missing placeholder raises before any process is spawned.
3. A command exceeding its timeout is killed and returns `failed`; no orphan.
4. `JSON_STDOUT` with malformed JSON is `failed`, not a string result.
5. A non-zero exit outside `success_exit` is `failed` with the code in evidence.
6. Evidence redacts argument values and keeps argument names.
7. A second concurrent call for the same provider returns `failed("busy")`.
8. An AGPL provider declaring `CLI` constructs successfully (G6 regression:
   `ADAPTER` must still raise).

## First adapter

`firstmate` — MIT, smallest surface, no credentials needed. It proves the mode
end to end before the riskier agents (`openhands`, `strix`) are attempted.
