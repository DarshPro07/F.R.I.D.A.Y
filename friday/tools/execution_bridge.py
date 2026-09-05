"""
The Friday execution bridge: Friday's execution layer, served over MCP.

Why this exists: Hermes's native file/terminal tools wedge inside its
Git-Bash environment probe on this Windows host (upstream #73403 shape -
`_bash_starts` → `subprocess._communicate` never returns), eating the 420s
tool ceiling per call. Rather than wait for upstream, Friday serves its OWN
execution capability to the Hermes runtime: reads, writes, listing, search
and command execution all run through `friday.execution`'s
NativeExecutionEnvironment - workspace jail (symlink-safe resolve), job
containment, credential redaction, timeouts - so what Hermes gets is
Friday's policy surface, not a second shell stack.

This is also the intended ownership split, bug or no bug: Hermes reasons
and decides; Friday touches the machine and records what happened.

Served as a stdio MCP server:

    python -m friday.tools.execution_bridge  (FRIDAY_BRIDGE_WORKSPACE set)

and registered ONLY in the dedicated `friday` Hermes profile, whose native
`file`/`terminal` toolsets are disabled while their health probe fails
(`friday/hermes_bridge.py native_tools_healthy()`).

Command policy here is deny-by-category, allow-narrow: the bridge takes a
command VECTOR (never a shell string), refuses shell interpreters and
obvious footguns outright, and lets the jailed environment contain the
rest. Friday's PolicyEngine still governs what the Friday side exposes.
"""

from __future__ import annotations

import os
import shlex
import sys
from pathlib import Path

from mcp.server.fastmcp import FastMCP

from friday import execution

#: Interpreters and destructive verbs the bridge refuses to launch at all.
#: The environment jail would contain most of this anyway; refusing early
#: gives Hermes a teachable error instead of a confusing sandbox failure.
FORBIDDEN_EXECUTABLES = {
    "powershell", "powershell.exe", "pwsh", "pwsh.exe",
    "cmd", "cmd.exe", "bash", "bash.exe", "sh", "sh.exe", "wsl", "wsl.exe",
    "format", "format.com", "shutdown", "shutdown.exe",
    "reg", "reg.exe", "regedit", "regedit.exe",
    "schtasks", "schtasks.exe", "runas", "runas.exe",
}

REFUSED = "BRIDGE_REFUSED"


def _workspace() -> Path:
    root = os.environ.get("FRIDAY_BRIDGE_WORKSPACE", "").strip()
    if not root:
        raise RuntimeError("FRIDAY_BRIDGE_WORKSPACE is not set")
    path = Path(root).resolve()
    if not path.is_dir():
        raise RuntimeError(f"workspace {path} does not exist")
    return path


_env = None


def _environment():
    """One lazily-built jailed environment for the whole server process."""
    global _env
    if _env is None:
        _env = execution.for_development(_workspace(), name="hermes-bridge")
    return _env


mcp = FastMCP("friday-execution-bridge")


@mcp.tool()
def bridge_read_file(path: str, offset: int = 1, limit: int = 2000) -> str:
    """Read a text file inside the Friday workspace.

    Args:
        path: Path relative to the workspace root.
        offset: 1-based first line to return.
        limit: Maximum number of lines to return.
    """
    try:
        text = _environment().read(path)
    except execution.ExecutionError as exc:
        return f"{REFUSED}: {exc}"
    except OSError as exc:
        return f"ERROR: {exc}"
    lines = text.splitlines()
    window = lines[max(offset - 1, 0):max(offset - 1, 0) + max(limit, 1)]
    body = "\n".join(f"{i}|{line}" for i, line
                     in enumerate(window, start=max(offset, 1)))
    return f"{path} ({len(lines)} lines total)\n{body}"


@mcp.tool()
def bridge_write_file(path: str, content: str) -> str:
    """Write a text file inside the Friday workspace (overwrites).

    Args:
        path: Path relative to the workspace root.
        content: Full file content.
    """
    try:
        target = _environment().write(path, content)
    except execution.ExecutionError as exc:
        return f"{REFUSED}: {exc}"
    except OSError as exc:
        return f"ERROR: {exc}"
    return f"wrote {len(content)} chars to {target}"


@mcp.tool()
def bridge_list_files(path: str = ".") -> str:
    """List files under a directory inside the Friday workspace.

    Args:
        path: Directory relative to the workspace root.
    """
    try:
        names = _environment().listing(path)
    except execution.ExecutionError as exc:
        return f"{REFUSED}: {exc}"
    except OSError as exc:
        return f"ERROR: {exc}"
    shown = names[:500]
    suffix = "" if len(names) <= 500 else f"\n... and {len(names)-500} more"
    return "\n".join(shown) + suffix


#: Dependency and build trees are never what a task is about, and the
#: listing is recursive: from "." a search read every file under .venv,
#: node_modules and third_party (2026-09-04 21:18: one search ran past
#: three minutes with the machine at 98% memory). Skipped, and capped.
_SEARCH_SKIP = frozenset({".git", ".venv", ".venv-verify", "node_modules",
                          "third_party", "__pycache__", "site-packages",
                          "dist", ".playwright", ".pytest_cache"})
_SEARCH_MAX_FILES = 4000


@mcp.tool()
def bridge_search_files(pattern: str, path: str = ".",
                        limit: int = 50) -> str:
    """Search file CONTENTS in the Friday workspace (plain substring).

    Args:
        pattern: Substring to look for.
        path: Directory relative to the workspace root.
        limit: Maximum matching lines to return.
    """
    env = _environment()
    try:
        names = env.listing(path)
    except execution.ExecutionError as exc:
        return f"{REFUSED}: {exc}"
    hits: list[str] = []
    scanned = 0
    for name in names:
        if len(hits) >= limit:
            break
        if set(name.replace("\\", "/").split("/")[:-1]) & _SEARCH_SKIP:
            continue
        scanned += 1
        if scanned > _SEARCH_MAX_FILES:
            hits.append(f"... stopped after {_SEARCH_MAX_FILES} files; "
                        f"narrow `path` to search further")
            break
        try:
            text = env.read(name, limit=2_000_000)
        except (execution.ExecutionError, OSError):
            continue
        for number, line in enumerate(text.splitlines(), start=1):
            if pattern in line:
                hits.append(f"{name}:{number}: {line.strip()[:200]}")
                if len(hits) >= limit:
                    break
    return "\n".join(hits) if hits else f"no matches for {pattern!r}"


@mcp.tool()
def bridge_brain_recall(query: str = "", entity: str = "",
                        budget: str = "bounded") -> str:
    """What the shared Friday/Hermes brain already knows. Check BEFORE
    re-reading files or re-researching: durable facts with provenance,
    packed server-side to a token budget (trivial/bounded/project/deep).

    Args:
        query: Free-text question over stored knowledge.
        entity: Optional scope: friday / hermes / project-<id>.
        budget: Token budget class; bounded is right for most tasks.
    """
    import json as _json

    try:
        from friday.brain import SharedBrainAdapter

        answer = SharedBrainAdapter().recall(query, entity=entity,
                                             budget=budget)
    except Exception as exc:                                 # noqa: BLE001
        return (f"shared brain unreachable ({type(exc).__name__}); "
                f"proceed from files instead")
    return _json.dumps(answer.compact(), default=str)


@mcp.tool()
def bridge_brain_remember(fact: str, provenance: str,
                          entity: str = "") -> str:
    """Save ONE durable verified fact to the shared Friday/Hermes brain.
    Only verified findings with provenance - never execution status,
    transient detail, or anything secret/banking-shaped (refused before
    ingestion).

    Args:
        fact: The verified claim, one per call.
        provenance: Where it came from (file:line, WorkRun id, doc).
        entity: Optional scope: friday / hermes / project-<id>.
    """
    try:
        from friday.brain import AdmissionRefused, SharedBrainAdapter

        out = SharedBrainAdapter().remember(fact, provenance=provenance,
                                            entity=entity)
    except Exception as exc:                                 # noqa: BLE001
        if type(exc).__name__ == "AdmissionRefused":
            return f"refused: {exc}"
        return (f"shared brain unreachable ({type(exc).__name__}); the "
                f"fact was NOT saved - report this rather than claiming "
                f"it was")
    return f"{out.get('status')} id={out.get('id')}"






@mcp.tool()
def bridge_run_command(command: str, timeout: int = 300) -> str:
    """Run one program inside Friday's contained workspace environment.

    The command is parsed as a VECTOR (no shell). Shell interpreters and
    system-mutating executables are refused. Output is captured, truncated
    and credential-redacted by Friday's execution layer.

    Args:
        command: The program and its arguments, e.g. "git status --short".
        timeout: Seconds before the process is killed.
    """
    try:
        vector = shlex.split(command, posix=False)
    except ValueError as exc:
        return f"{REFUSED}: unparseable command: {exc}"
    # posix=False keeps Windows backslash paths intact but also keeps the
    # surrounding quotes on quoted tokens; strip them so `python -c "..."`
    # passes the code, not a quoted string literal.
    vector = [part[1:-1] if len(part) >= 2 and part[0] == part[-1]
              and part[0] in "\"'" else part for part in vector]
    if not vector:
        return f"{REFUSED}: empty command"
    executable = Path(vector[0]).name.lower()
    if executable in FORBIDDEN_EXECUTABLES:
        return (f"{REFUSED}: {executable!r} is not allowed through the "
                f"bridge. Run programs directly (git, python, pytest, "
                f"node...) - no shells, no system mutation.")
    result = _environment().run(vector, timeout=float(min(timeout, 600)))
    head = (f"exit={result.exit_code} seconds={result.seconds:.1f}"
            f"{' TIMED_OUT' if result.timed_out else ''}")
    out = result.stdout[-20_000:]
    err = result.stderr[-5_000:]
    return f"{head}\n--- stdout ---\n{out}\n--- stderr ---\n{err}"


def main() -> None:
    _workspace()          # fail fast, loudly, before serving
    mcp.run()             # stdio transport


if __name__ == "__main__":
    main()
