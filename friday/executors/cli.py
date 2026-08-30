"""
The Claude Code CLI as ADA sees it - every line here is a probe result.

The published docs describe a `PreToolUse` hook returning `defer` on
`AskUserQuestion`, and a `PermissionRequest` hook that can allow or deny. Both
are the right design. Neither exists in the **installed** CLI:

    claude --version                    2.1.233

    AskUserQuestion in the headless      NO - not in system/init `tools`,
    tool list                                 and Claude says so itself
    PermissionRequest hook fires in -p   NO - never invoked; the write was
                                              denied without it
    --permission-mode default            NOT A CHOICE here - `manual` is
                                              the name 2.1.233 exposes for it.
                                              Passing `manual` makes init
                                              report `permissionMode: default`,
                                              so it is the same mode under the
                                              CLI's own name, not a different
                                              one.

Installed binary is the authority, same rule that has held since Phase 0. So
the two brokers are built on what 2.1.233 actually does:

    questions    an MCP tool ADA already serves. Better than `defer`: it is
                 structured, it carries options, and it works today.
    permissions  --allowedTools / --disallowedTools computed before launch,
                 plus `permission_denials` read back from the result. The
                 decision is enforced outside the model and cannot be talked
                 around, which is the property that mattered.

Three more things the probes cost, so they are recorded rather than
rediscovered:

**The prompt goes on stdin.** `--allowedTools` and `--disallowedTools` are
variadic, so a prompt passed as a trailing positional is swallowed as another
tool name. The run produced no output at all - not an error, just silence.

**Hooks must be off.** The user's global config loads plugin hooks that fail
on this machine (no bash, no node, no python on the hook shell's PATH) and one
of them hangs long enough to blow a 300s timeout. `{"disableAllHooks": true}`
in `--settings` removes them.

**CLAUDE_CONFIG_DIR must not be moved.** It looks like the clean way to
isolate from those hooks, and it breaks authentication:
"Not logged in - Please run /login". The subscription lives in the default
config directory, and the subscription is the point.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import subprocess
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field

logger = logging.getLogger("friday-agent.executors.cli")

#: Reads are safe and constant; anything that changes the world is not here.
#: `manual` is the name this CLI exposes for the mode the docs call `default` -
#: passing `manual` makes the init event report `permissionMode: default`. Same
#: mode, CLI's own spelling.
DEFAULT_PERMISSION_MODE = "manual"

#: Never. Not as a fallback, not with a flag, not when a task is stuck.
FORBIDDEN_MODES = frozenset({"bypassPermissions"})

CLAUDE_BIN = os.getenv("ADA_CLAUDE_BIN", "claude")


class ExecutorUnavailable(RuntimeError):
    """The CLI is not installed, or refuses to start."""


#: Where the installer puts it when PATH does not say so.
#:
#: `shutil.which("claude")` returned None from inside the venv on the machine
#: this was written on, while `claude --version` worked in the shell - the
#: installer's directory is on the interactive PATH and not on the one a
#: service process inherits. Failing to find an installed CLI and reporting
#: "not installed" would be a confusing lie.
_KNOWN_LOCATIONS = (
    "~/.local/bin/claude.exe",
    "~/.local/bin/claude",
    "~/.claude/local/claude.exe",
    "~/.claude/local/claude",
    "~/AppData/Roaming/npm/claude.cmd",
)


def claude_path() -> str | None:
    found = shutil.which(CLAUDE_BIN)
    if found:
        return found
    if CLAUDE_BIN != "claude":
        # An explicit ADA_CLAUDE_BIN that does not resolve is a configuration
        # error, not an invitation to go looking somewhere else.
        return CLAUDE_BIN if os.path.isfile(CLAUDE_BIN) else None
    for candidate in _KNOWN_LOCATIONS:
        expanded = os.path.expanduser(candidate)
        if os.path.isfile(expanded):
            return expanded
    return None


def available() -> bool:
    return claude_path() is not None


def version() -> str:
    path = claude_path()
    if path is None:
        raise ExecutorUnavailable(f"{CLAUDE_BIN} is not on PATH")
    out = subprocess.run([path, "--version"], capture_output=True, text=True,
                         timeout=60)
    return (out.stdout or out.stderr or "").strip()


@dataclass
class Launch:
    """Everything needed to start one CLI run, as data rather than a string."""

    prompt: str
    cwd: str
    session_id: str | None = None
    resume: str | None = None
    worktree: str | None = None
    permission_mode: str = DEFAULT_PERMISSION_MODE
    allowed_tools: tuple[str, ...] = ()
    disallowed_tools: tuple[str, ...] = ()
    mcp_config: str | None = None
    strict_mcp: bool = True
    model: str | None = None
    settings: dict = field(default_factory=lambda: {"disableAllHooks": True})

    def argv(self) -> list[str]:
        # The refusal comes first, deliberately. Whether bypassing permissions
        # is allowed cannot depend on whether the binary happens to be
        # installed - that would make the check an accident of the machine.
        if self.permission_mode in FORBIDDEN_MODES:
            raise ValueError(
                f"{self.permission_mode!r} is never allowed on the host - "
                "the policy engine exists so that this is not a choice"
            )
        path = claude_path()
        if path is None:
            raise ExecutorUnavailable(f"{CLAUDE_BIN} is not on PATH")

        argv = [
            path, "-p",
            "--output-format", "stream-json",
            "--verbose",
            "--permission-mode", self.permission_mode,
            "--settings", json.dumps(self.settings),
        ]
        if self.model:
            argv += ["--model", self.model]
        if self.resume:
            argv += ["--resume", self.resume]
        elif self.session_id:
            argv += ["--session-id", self.session_id]
        if self.worktree:
            argv += ["--worktree", self.worktree]
        if self.mcp_config:
            argv += ["--mcp-config", self.mcp_config]
            if self.strict_mcp:
                argv.append("--strict-mcp-config")
        # Variadic flags go last, and the prompt never joins them on the
        # command line - see the module docstring. It is written to stdin.
        if self.allowed_tools:
            argv += ["--allowedTools", *self.allowed_tools]
        if self.disallowed_tools:
            argv += ["--disallowedTools", *self.disallowed_tools]
        return argv


# ---------------------------------------------------------------------------
# Events
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Event:
    """One line of stream-json, in the shape ADA cares about."""

    kind: str            # init | thinking | tool | text | tool_result | result | other
    raw: dict
    name: str = ""       # tool name, for kind == "tool"
    text: str = ""

    @property
    def session_id(self) -> str:
        return str(self.raw.get("session_id") or "")


def parse(line: str) -> list[Event]:
    """
    One JSON line to zero or more events.

    An assistant message carries several content blocks, and the interesting
    ones - which tool, what it said - are inside them rather than on the
    envelope.
    """
    try:
        payload = json.loads(line)
    except (TypeError, ValueError):
        return []
    if not isinstance(payload, dict):
        return []

    kind = payload.get("type")
    if kind == "system" and payload.get("subtype") == "init":
        return [Event("init", payload)]
    if kind == "result":
        return [Event("result", payload, text=str(payload.get("result") or ""))]

    if kind == "assistant":
        events: list[Event] = []
        for block in (payload.get("message", {}).get("content") or []):
            btype = block.get("type")
            if btype == "tool_use":
                events.append(Event("tool", payload, name=str(block.get("name") or "")))
            elif btype == "text":
                events.append(Event("text", payload, text=str(block.get("text") or "")))
            elif btype == "thinking":
                events.append(Event("thinking", payload))
        return events

    if kind == "user":
        content = payload.get("message", {}).get("content")
        if isinstance(content, list):
            return [Event("tool_result", payload) for b in content
                    if b.get("type") == "tool_result"]
        return []

    return [Event("other", payload)]


# ---------------------------------------------------------------------------
# Running
# ---------------------------------------------------------------------------


class Run:
    """
    A live CLI process.

    Cancellation kills the tree, not the parent: the CLI spawns node and
    whatever the task started, and a lone terminate leaves those running.
    """

    def __init__(self, launch: Launch, sandbox=None) -> None:
        self.launch = launch
        self.process: asyncio.subprocess.Process | None = None
        self.session_id: str = ""
        self.cancelled = False
        self.sandbox = sandbox

    async def start(self) -> None:
        argv = self.launch.argv()
        logger.info("claude: %s%s", " ".join(argv[1:8]),
                    f" [sandbox {self.sandbox.name}]" if self.sandbox else "")
        self.process = await asyncio.create_subprocess_exec(
            *argv, cwd=self.launch.cwd,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=self.sandbox.environment() if self.sandbox else None,
        )
        if self.sandbox is not None:
            # Adopted into the job object before anything else happens, so
            # that whatever the CLI spawns is bounded and ends with the run.
            self.sandbox.job.adopt(self.process.pid)
        assert self.process.stdin is not None
        self.process.stdin.write(self.launch.prompt.encode("utf-8"))
        await self.process.stdin.drain()
        self.process.stdin.close()

    async def events(self, *, timeout: float = 1800.0) -> AsyncIterator[Event]:
        if self.process is None:
            await self.start()
        assert self.process is not None and self.process.stdout is not None

        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        while True:
            remaining = deadline - loop.time()
            if remaining <= 0:
                await self.cancel()
                raise TimeoutError(f"claude exceeded {timeout:.0f}s")
            try:
                line = await asyncio.wait_for(
                    self.process.stdout.readline(), timeout=remaining)
            except asyncio.TimeoutError:
                await self.cancel()
                raise TimeoutError(f"claude exceeded {timeout:.0f}s") from None
            if not line:
                break
            for event in parse(line.decode("utf-8", errors="replace")):
                if event.kind == "init":
                    self.session_id = event.session_id
                yield event

        await self.process.wait()

    async def cancel(self) -> bool:
        """Stop the run and everything it started. Returns whether it was up."""
        if self.process is None or self.process.returncode is not None:
            return False
        self.cancelled = True
        pid = self.process.pid
        try:
            if self.sandbox is not None:
                # The job object ends the whole tree at once, which is the
                # point of having one: nothing the run started survives it
                # and nothing outside the job is touched. taskkill below is
                # the fallback for a run that had no boundary.
                self.sandbox.terminate()
            elif os.name == "nt":
                # taskkill /T: the CLI is node, and node spawned the task's own
                # children. Terminating the parent orphans them.
                await asyncio.to_thread(
                    subprocess.run, ["taskkill", "/F", "/T", "/PID", str(pid)],
                    capture_output=True)
            else:
                self.process.terminate()
        except Exception:
            logger.exception("could not stop the claude process tree")
        try:
            await asyncio.wait_for(self.process.wait(), timeout=15)
        except asyncio.TimeoutError:
            logger.warning("claude did not exit after being killed")
        return True


async def stream(launch: Launch, *, on_event: Callable[[Event], None] | None = None,
                 timeout: float = 1800.0) -> tuple[Run, list[Event]]:
    """Run to completion, collecting events. `on_event` sees them as they land."""
    run = Run(launch)
    collected: list[Event] = []
    async for event in run.events(timeout=timeout):
        collected.append(event)
        if on_event is not None:
            try:
                on_event(event)
            except Exception:
                logger.exception("progress callback failed; the run continues")
    return run, collected
