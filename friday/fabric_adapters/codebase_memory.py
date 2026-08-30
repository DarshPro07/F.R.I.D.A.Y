"""
codebase-memory-mcp: the exact structural graph of a repository.

A tree-sitter knowledge graph of functions, classes, call chains and routes,
answering "where does X live and what calls it" in one query instead of a grep
that returns forty files and a read that costs four thousand tokens. Friday's
7,763 nodes and 39,540 edges index in 37 seconds.

## Why the `cli` subcommand and not the MCP server

The binary speaks MCP on stdio, which would make it a long-lived child holding
a pipe. It also has `cli <tool>`, which runs one tool and exits. The second is
what a durable objective actually wants: a call that either answers or fails,
with no session to lose across a Friday restart. The graph itself is persistent
on disk, so nothing is recomputed by exiting.

## Why `start()` launches a daemon anyway

Cold, every `cli` call spends ~14s standing up a temporary daemon. Warm, the
same query costs ~4.5s. So `start()` brings up the shared daemon once and
`stop()` retires it, which is exactly what the fabric's lazy activation is for:
the daemon exists while a code-intelligence task is running and not otherwise.

## Known upstream limitation - see BUG_LEDGER B-002

`daemon start` rewrites `ui_enabled` to true and binds 127.0.0.1:9749 whatever
the persisted config says, and rejects `--ui=false` as an unknown option. There
is no supported way to run the daemon without its HTTP UI. Lazy activation
bounds the exposure to the life of a code-intelligence task; it does not remove
it. Loopback only.

We never run `install`: it edits agent config files across 43 client surfaces,
which is precisely the surprise MCP_VERIFICATION_PLAN M0 forbids. Verified
after use that `~/.claude.json` was untouched.
"""

from __future__ import annotations

import json
import pathlib
import subprocess

from friday import fabric

ROOT = pathlib.Path(__file__).resolve().parent.parent.parent
BINARY = ROOT / "third_party" / "bin" / "cbm" / "codebase-memory-mcp.exe"

#: What `processes()` looks for when checking nothing is running twice.
PROCESS_MARKER = "codebase-memory-mcp"

#: The project name Friday's own tree is indexed under.
PROJECT = "friday-core"

#: Operation -> (upstream tool, required flags). Kept as data so a new
#: operation is a table row rather than another branch.
OPERATIONS = {
    "index": ("index_repository", ("repo_path", "name", "mode")),
    "search": ("search_graph", ("project", "name_pattern", "node_type",
                                "file_pattern", "limit")),
    "trace": ("trace_path", ("project", "from", "to", "limit")),
    "architecture": ("get_architecture", ("project", "aspects")),
    "snippet": ("get_code_snippet", ("project", "qualified_name")),
    "projects": ("list_projects", ()),
    "status": ("index_status", ("project",)),
}

#: Long enough for a full index of a large tree, short enough that a hung
#: daemon fails the call rather than the objective.
TIMEOUT = 900


def _run(args: list[str], timeout: int = 120) -> subprocess.CompletedProcess:
    return subprocess.run([str(BINARY), *args], capture_output=True, text=True,
                          timeout=timeout, encoding="utf-8", errors="replace")


def start():
    if not BINARY.exists():
        raise FileNotFoundError(
            f"codebase-memory-mcp binary not found at {BINARY}; "
            f"see docs/architecture/CAPABILITY_FABRIC.md for the pinned download")
    _run(["daemon", "start"], timeout=120)
    return {"binary": str(BINARY)}


def stop(handle) -> None:
    """Retire the daemon. Without this it is 'permanent' by its own word."""
    _run(["daemon", "stop"], timeout=60)


def health(handle) -> dict:
    if not BINARY.exists():
        return {"state": fabric.UNAVAILABLE, "detail": f"binary missing: {BINARY}"}
    result = _run(["daemon", "status"], timeout=60)
    text = (result.stdout or "") + (result.stderr or "")
    if "daemon: active" not in text:
        # The CLI still works without a daemon, just slowly. That is degraded,
        # not down, and saying so is the difference between routing around it
        # and refusing a question we could still answer.
        return {"state": fabric.DEGRADED,
                "detail": "no warm daemon; cli calls cost ~14s each"}
    pid = ""
    for line in text.splitlines():
        if line.strip().startswith("pid:"):
            pid = line.split(":", 1)[1].strip()
    return {"state": fabric.READY,
            "detail": f"daemon active pid {pid}", "pid": pid}


def call(operation: str, handle, **arguments):
    tool, allowed = OPERATIONS[operation]
    args = ["cli", "--json", tool]
    for key in allowed:
        value = arguments.get(key)
        if value in (None, ""):
            continue
        args += [f"--{key.replace('_', '-')}",
                 ",".join(map(str, value)) if isinstance(value, (list, tuple))
                 else str(value)]

    result = _run(args, timeout=TIMEOUT)
    if result.returncode != 0:
        raise RuntimeError(
            f"{tool} exited {result.returncode}: "
            f"{(result.stderr or result.stdout or '')[-400:]}")

    # The binary writes structured logs to the same stream as the payload.
    # The payload is the one line that parses as an object with a `content`
    # key; picking it by shape rather than by position survives a new log line.
    for line in reversed((result.stdout or "").splitlines()):
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            parsed = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(parsed, dict) or "content" not in parsed:
            continue
        if parsed.get("isError"):
            raise RuntimeError(
                f"{tool}: {parsed.get('structuredContent', {}).get('error', 'failed')}")
        return parsed.get("structuredContent") or parsed["content"]
    raise RuntimeError(f"{tool} returned no parseable result")


DESCRIPTOR = fabric.Provider(
    id='codebase_memory',
    family='code_intelligence',
    upstream='codebase-memory-mcp',
    operations=tuple(OPERATIONS),
    risk='low',
    license_mode=fabric.PERMISSIVE,
    integration_mode=fabric.MCP,
    cost_class='free',
    model_required=False,
    commit='e678722746d452c41644095a0c3a0aaefc4461b3',
    version='0.10.8',
    owns_process=True,
    notes='Native binary, no runtime or API key. Indexes locally; nothing leaves the machine. Binds 127.0.0.1:9749 while the daemon runs (upstream defect B-002).',
)
