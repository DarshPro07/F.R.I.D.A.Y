"""
Graft: conceptual code context, blast radius, and orientation.

Where `codebase_memory` answers "what exactly calls this symbol" from a
tree-sitter graph, Graft answers the question one level up - "what is this
repository about, what would I break, show me the shape of this file" - and
budgets the answer to fit a prompt. The two are deliberately both registered
in `code_intelligence`: the router picks the exact one for a symbol question
and this one for an orientation question, and either can cover for the other.

## Deterministic by default, and that is the point

`build` (tier 1), `check`, `ask`, `grep`, `map`, `callers`, `skeleton` and
`blast` are pure tree-sitter. No model, no network, no key. That is why they
are the only operations exposed here: a code question that costs nothing is
one the planner can ask freely, and NON_NEGOTIABLE 11 ("avoid duplicate model
calls") is easiest to keep when the cheap path is the only path.

`graft build --deep` adds an LLM pass over every file under a provider key.
It is deliberately NOT an operation on this provider. Deep summarisation is a
spend decision, and spend decisions belong to execution economics, not to
whatever happened to call `fabric.call`.

## Two upstream side effects this adapter refuses or declares

`graft init` detects your coding agents and rewrites their config: it drops a
statusline and hooks into `.claude/`, writes `AGENTS.md`, and registers an MCP
server in `.mcp.json`. That is the same surprise `codebase_memory` documents
refusing for `install`, and it is refused here for the same reason - Friday
does not silently edit the operator's agent configuration. We never call it.
Every query command works without it.

`graft build` appends `graft/` to the repository's `.gitignore` on its own.
That IS a write to a tracked file, it is the upstream's documented behaviour,
and there is no flag to suppress it. It is declared here rather than
discovered later in a confusing `git status`. The graph directory itself is a
regenerable cache, like `node_modules`, and is never committed.

## Telemetry

Graft posts one batched anonymous usage ping from a detached process, at most
daily. `TELEMETRY.md` upstream is an allowlist contract and carries no code,
paths, repo name, symbols or queries. We still turn it off: every subprocess
here runs with `DO_NOT_TRACK=1`, which the upstream honours, because a
capability Friday invokes on the operator's behalf should not phone home about
it. `notes` on the descriptor records that this is opt-out, not absent.
"""

from __future__ import annotations

import json
import os
import pathlib
import shutil
import subprocess

from friday import fabric

ROOT = pathlib.Path(__file__).resolve().parent.parent.parent

#: The npm package, pinned. `npx` is the fallback when nothing is installed
#: globally; pinning the version keeps that path as audited as the other one.
PACKAGE = "@nanonets/graft"
VERSION = "0.15.0"

#: Operation -> (subcommand, accepted keyword arguments). Data, not branches,
#: for the same reason the CBM adapter keeps a table: a new operation should be
#: a row.
#:
#: Deliberately absent: `init` (rewrites agent config), `build --deep` (spends
#: model budget), `viz` (opens a browser), `telemetry` (we force it off).
OPERATIONS = {
    "build": ("build", ("path", "extensions", "no_reuse")),
    "check": ("check", ("path",)),
    "ask": ("ask", ("query", "path", "no_refresh")),
    "grep": ("grep", ("query", "path", "no_refresh")),
    "map": ("map", ("path", "no_refresh")),
    "callers": ("callers", ("symbol", "path", "no_refresh")),
    "skeleton": ("skeleton", ("target", "path", "no_refresh")),
    "blast": ("blast", ("target", "path", "no_refresh")),
}

#: Flags that are passed as bare switches rather than `--key value`.
_SWITCHES = {"no_reuse": "--no-reuse", "no_refresh": "--no-refresh"}

#: Positional-argument name per subcommand, if it takes one.
_POSITIONAL = {"ask": "query", "grep": "query", "callers": "symbol",
               "skeleton": "target", "blast": "target"}

#: A cold `build` on a large tree is the slow case; queries are milliseconds.
TIMEOUT = 600


def _base_command() -> list[str] | None:
    """How to invoke graft on this machine, or None if it is not reachable."""
    found = shutil.which("graft")
    if found:
        return [found]
    npx = shutil.which("npx")
    if npx:
        return [npx, "--yes", f"{PACKAGE}@{VERSION}"]
    return None


def _env() -> dict:
    """Subprocess environment with telemetry off. See the module docstring."""
    return {**os.environ, "DO_NOT_TRACK": "1", "GRAFT_NO_UPDATE_CHECK": "1"}


def _run(arguments: list[str], timeout: int = 120) -> subprocess.CompletedProcess:
    base = _base_command()
    if base is None:
        raise FileNotFoundError(
            "graft is not installed: no `graft` on PATH and no `npx` to fall "
            f"back to. Install with `npm install -g {PACKAGE}@{VERSION}`.")
    return subprocess.run([*base, *arguments], capture_output=True, text=True,
                          timeout=timeout, encoding="utf-8", errors="replace",
                          cwd=str(ROOT), env=_env())


def start():
    """
    Nothing to launch. Graft has no daemon; the graph is files on disk.

    We only prove the CLI is reachable, so an unreachable one fails here at
    activation rather than inside the first real question.
    """
    base = _base_command()
    if base is None:
        raise FileNotFoundError(
            f"graft not reachable; install `{PACKAGE}@{VERSION}` or provide npx")
    return {"command": base}


def stop(handle) -> None:
    """No process is held, so there is nothing to retire."""


def health(handle) -> dict:
    if _base_command() is None:
        return {"state": fabric.UNAVAILABLE,
                "detail": f"neither `graft` nor `npx` found; "
                          f"npm install -g {PACKAGE}@{VERSION}"}
    graph = ROOT / "graft"
    if not graph.exists():
        # The CLI is there and every command would work - each one refreshes
        # the graph first - but the first call pays a full build. Saying so is
        # what lets the router prefer the warm provider without refusing this
        # one outright.
        return {"state": fabric.DEGRADED,
                "detail": "no graft/ graph yet; first query pays a full build"}
    return {"state": fabric.READY, "detail": f"graph at {graph}"}


def call(operation: str, handle, **arguments):
    subcommand, allowed = OPERATIONS[operation]
    args = [subcommand]

    positional = _POSITIONAL.get(operation)
    if positional:
        value = arguments.get(positional)
        if not value:
            raise ValueError(f"graft {subcommand} needs {positional!r}")
        args.append(str(value))

    for key in allowed:
        if key == positional:
            continue
        value = arguments.get(key)
        if value in (None, "", False):
            continue
        if key in _SWITCHES:
            args.append(_SWITCHES[key])
        elif key == "path":
            args.append(str(value))
        elif isinstance(value, (list, tuple)):
            args += [f"--{key.replace('_', '-')}", *map(str, value)]
        else:
            args += [f"--{key.replace('_', '-')}", str(value)]

    result = _run(args, timeout=TIMEOUT)
    if result.returncode != 0:
        raise RuntimeError(
            f"graft {subcommand} exited {result.returncode}: "
            f"{(result.stderr or result.stdout or '')[-400:]}")

    text = (result.stdout or "").strip()
    # Graft prints human-readable text for most commands. Return parsed JSON
    # when it is JSON and the raw text otherwise, rather than forcing a shape
    # onto output whose contract is 'readable'.
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return {"output": text}


DESCRIPTOR = fabric.Provider(
    id="graft",
    family="code_intelligence",
    upstream="graft",
    operations=tuple(OPERATIONS),
    risk="low",
    license_mode=fabric.PERMISSIVE,
    integration_mode=fabric.SIDECAR,
    cost_class="free",
    model_required=False,
    commit="268e30d750b50df39d7f13873f696c4523fea6b1",
    version=f"v{VERSION}",
    owns_process=False,
    fallbacks=("codebase_memory",),
    notes=(
        "MIT. Tier-1 graph is deterministic tree-sitter: no model, no network, "
        "no key. `build --deep` (LLM pass) is deliberately not exposed - that "
        "is a spend decision. `init` is never run: it rewrites .claude/, "
        "AGENTS.md and .mcp.json. `build` appends graft/ to .gitignore, which "
        "is upstream behaviour with no opt-out flag. Telemetry is opt-out and "
        "forced off here with DO_NOT_TRACK=1."
    ),
)
