"""FAB-007: the signed-in Claude Code CLI acting through FRIDAY's MCP server.

Owner directive 2026-10-08 (b): "FRIDAY is the permanent supervisor. Models
are interchangeable intelligence resources. Claude Code ... are specialized
execution engines." When every API route is rate-limited and Hermes is absent
(`executor_router.choose_for_capability`), a typed request that needs a
capability is handed to the CLI configured with ONE tool source: FRIDAY's own
MCP server. Built-in tools are switched off (`--tools ""`), a shell is
denied by name, and only `mcp__friday` is allowed under `dontAsk`, so the CLI
can do nothing FRIDAY's server would not do - and each tool there
enforces FRIDAY's policy. A tool seen outside it fails the run.
"""
from __future__ import annotations

import asyncio
import json
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from friday.executors import cli

#: The key in the MCP config, hence the `mcp__friday__<tool>` tool prefix.
SERVER_KEY = "friday"
TOOL_PREFIX = f"mcp__{SERVER_KEY}__"
#: Never reachable, even if a built-in tool were somehow left on.
DENIED = ("Bash", "PowerShell", "Edit", "Write", "NotebookEdit",
          "WebFetch", "WebSearch")
PERMISSION_MODE = "dontAsk"
DEFAULT_TIMEOUT_S = 600.0

#: Builds the live process for a launch. Production uses `cli.Run`, which
#: registers with the emergency stop, warns on pay-as-you-go billing, writes
#: the prompt to stdin and kills the whole process tree on cancel or timeout.
RunFactory = Callable[[cli.Launch], cli.Run]


@dataclass(frozen=True)
class RouteResult:
    ok: bool
    text: str = ""
    error: str = ""
    tools_used: tuple[str, ...] = ()
    outside_friday: tuple[str, ...] = ()
    session_id: str = ""
    #: Each tool call the CLI made, in order: {name, arguments, output, is_error}, where
    #: `output` is what the tool returned. Best effort: a stream that does not pair a
    #: result with its call leaves `output` empty. The caller's evidence for what ran.
    calls: tuple[dict, ...] = ()


def write_mcp_config(directory, *, url: str | None = None) -> str:
    """Write the `--mcp-config` file pointing at the local FRIDAY MCP endpoint."""
    if url is None:
        from friday import control
        url = control.SSE_URL
    folder = Path(directory)
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / "friday-mcp.json"
    path.write_text(json.dumps(
        {"mcpServers": {SERVER_KEY: {"type": "sse", "url": url}}}),
        encoding="utf-8")
    return str(path)


def launch_for(request: str, *, config_path: str, cwd: str,
               model: str | None = None) -> cli.Launch:
    return cli.Launch(
        prompt=request, cwd=cwd, permission_mode=PERMISSION_MODE,
        mcp_config=config_path, strict_mcp=True, builtin_tools="",
        allowed_tools=(f"mcp__{SERVER_KEY}",), disallowed_tools=DENIED,
        model=model)


async def run_request(request: str, *, run_factory: RunFactory | None = None,
                      url: str | None = None, model: str | None = None,
                      timeout_s: float = DEFAULT_TIMEOUT_S) -> RouteResult:
    """Complete `request` through the CLI and FRIDAY's MCP tools. Async, and
    built on `cli.Run`, so an emergency stop reaches the worker and a timeout
    or cancellation kills its whole process tree. An unusable CLI or a failed
    run does not raise: the reason comes back in `error`."""
    make = run_factory or cli.Run
    # ignore_cleanup_errors: a straggler holding the cwd on Windows must not
    # turn a reported failure into an exception.
    with tempfile.TemporaryDirectory(prefix="friday-mcp-route-",
                                     ignore_cleanup_errors=True) as scratch:
        config = write_mcp_config(scratch, url=url)
        launch = launch_for(request, config_path=config, cwd=scratch,
                            model=model)
        try:
            launch.argv()
        except cli.ExecutorUnavailable as exc:
            return RouteResult(False, error=str(exc))
        run = make(launch)
        events: list = []
        failure = ""
        try:
            async for event in run.events(timeout=timeout_s):
                events.append(event)
        except TimeoutError:
            failure = f"the CLI did not finish within {timeout_s:.0f}s"
        except OSError as exc:
            failure = f"the CLI could not be launched: {exc}"
        finally:
            # Also when the caller is cancelled: nothing is left running.
            if run.process is not None and run.process.returncode is None:
                await asyncio.shield(run.cancel())
        stderr = await _stderr_tail(run)
        code = run.process.returncode if run.process is not None else None
    return _judge(events, failure=failure, code=code, stderr=stderr)


async def _stderr_tail(run: cli.Run) -> str:
    process = run.process
    if process is None or process.stderr is None or process.returncode is None:
        return ""
    try:
        data = await asyncio.wait_for(process.stderr.read(), timeout=5)
    except (asyncio.TimeoutError, OSError):
        return ""
    return data.decode("utf-8", errors="replace").strip()[-300:]


def _calls_of(events) -> tuple[dict, ...]:
    """The tool calls in the stream, each joined to what its tool returned."""
    calls: dict[str, dict] = {}
    seen: set[int] = set()
    for event in events:
        if event.kind not in ("tool", "tool_result") or id(event.raw) in seen:
            continue
        seen.add(id(event.raw))        # one envelope can carry several blocks
        content = (event.raw.get("message") or {}).get("content")
        for block in content if isinstance(content, list) else ():
            if not isinstance(block, dict):
                continue
            if block.get("type") == "tool_use":
                args = block.get("input")
                calls[str(block.get("id") or f"#{len(calls)}")] = {
                    "name": str(block.get("name") or ""),
                    "arguments": args if isinstance(args, dict) else {},
                    "output": "", "is_error": False}
            elif block.get("type") == "tool_result":
                call = calls.get(str(block.get("tool_use_id") or ""))
                if call is not None:
                    body = block.get("content")
                    if isinstance(body, list):
                        body = "\n".join(str(b.get("text") or "") for b in body
                                         if isinstance(b, dict))
                    call["output"] = str(body or "")
                    call["is_error"] = bool(block.get("is_error"))
    return tuple(calls.values())


def _judge(events, *, failure: str, code, stderr: str) -> RouteResult:
    tools = tuple(e.name for e in events if e.kind == "tool")
    calls = _calls_of(events)
    outside = tuple(t for t in tools if not t.startswith(TOOL_PREFIX))
    if failure:
        return RouteResult(False, tools_used=tools, outside_friday=outside,
                           error=failure, calls=calls)
    result = next((e for e in reversed(events) if e.kind == "result"), None)
    if result is None:
        return RouteResult(False, tools_used=tools, outside_friday=outside,
                           error=(f"the CLI gave no result (exit {code})"
                                  + (f": {stderr}" if stderr else "")),
                           calls=calls)
    session = result.session_id
    if result.raw.get("is_error"):
        return RouteResult(False, text=result.text, error=result.text or
                           "the CLI reported an error", tools_used=tools,
                           outside_friday=outside, session_id=session, calls=calls)
    if outside:
        return RouteResult(False, text=result.text, tools_used=tools,
                           outside_friday=outside, session_id=session, error=(
            f"the CLI used tools outside FRIDAY's MCP server: {', '.join(outside)}"), calls=calls)
    if code not in (0, None):
        return RouteResult(False, text=result.text, tools_used=tools,
                           session_id=session,
                           error=f"the CLI exited {code}: {stderr}", calls=calls)
    return RouteResult(True, text=result.text, tools_used=tools,
                       session_id=session, calls=calls)
