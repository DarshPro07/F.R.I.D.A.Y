"""
SEC-005 (PRD v7 section 43): the veto Hermes asks FRIDAY for before any tool runs.

Hermes' gateway cannot hold its own file and terminal tools to an execution
envelope, and what the gateway reports about its tools (`tools.list`,
`session.create`) is read only before the task is sent, so a tool the report
missed would run once before `HermesSupervisor._deny_native_tool` noticed.
This closes that window from the other side: Hermes runs a configured
`pre_tool_call` shell hook before every tool (agent/shell_hooks.py), and exit
code 2 blocks the call. The hook is installed in the profile's `hooks:` with
`fail_closed: true`, because Hermes otherwise lets a call through when the
hook fails, times out or cannot start.

The decision, while a delegation holds the bridge (`delegation_scope`):
Hermes' own file and terminal tools are blocked whatever they were asked,
because Hermes cannot keep them to the envelope's roots; FRIDAY's exec bridge
(`mcp__friday_exec__*`) is let through, because it judges every path itself;
any other tool that names a path is held to the envelope's write roots, the
stricter reading. With no delegation holding the bridge nothing is blocked.
An unreadable registry, input the hook cannot make sense of, or any error
inside the decision blocks: the boundary fails closed here as it does there.

Run as a script by Hermes: `python hermes_envelope_hook.py`, the call as JSON
on stdin. Stdout carries the block directive Hermes reads.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

#: The prefix Hermes gives FRIDAY's exec bridge tools (the `friday_exec` MCP server).
BRIDGE_PREFIX = "mcp__friday_exec__"
#: Arguments by which a tool names a file.
PATH_KEYS = ("path", "file_path", "filepath")
#: Hermes blocks a `pre_tool_call` on this exit code.
BLOCK_EXIT_CODE = 2
HOOK_TIMEOUT_SECONDS = 10


def decide(payload) -> str:
    """Why the call must not run, or "" when it may."""
    from friday import delegation_scope as scope
    from friday.fsjail import JailError

    if not isinstance(payload, dict):
        raise ValueError("the call is not a JSON object")
    tool = payload.get("tool_name")
    arguments = payload.get("tool_input")
    if not isinstance(tool, str) or not tool:
        raise ValueError("the call names no tool")
    if arguments is None:
        arguments = {}
    if not isinstance(arguments, dict):
        raise ValueError("the call's arguments are not an object")
    try:
        held = scope.active()
    except scope.ScopeError as exc:
        return str(exc)
    if not held:
        return ""
    if tool.startswith(BRIDGE_PREFIX):
        return ""
    from friday.hermes_bridge import _NATIVE_FILE_TOOLS
    if tool in _NATIVE_FILE_TOOLS:
        return (f"Hermes' own {tool} cannot be held to this delegation's execution "
                f"envelope; its files are reached through FRIDAY's bridge tools")
    for key in PATH_KEYS:
        raw = arguments.get(key)
        if isinstance(raw, str) and raw.strip():
            try:
                scope.resolve(raw, write=True)
            except JailError as exc:
                return f"{tool} {key} is outside this delegation's envelope: {exc}"
    return ""


def run(stdin_text: str) -> tuple[int, str]:
    """(exit code, stdout) for the call on stdin. Anything but a clear yes blocks."""
    try:
        reason = decide(json.loads(stdin_text))
    except Exception as exc:  # noqa: BLE001 - a hook that cannot decide must block, never crash open
        reason = f"the envelope hook could not judge this call ({type(exc).__name__}): {exc}"
    if not reason:
        return 0, ""
    return BLOCK_EXIT_CODE, json.dumps({"action": "block", "message": f"FRIDAY: {reason}"})


def hook_command() -> str:
    """The `command` Hermes runs: this file under the interpreter FRIDAY runs on."""
    return f'"{sys.executable}" "{Path(__file__).resolve()}"'


def ensure_hook_installed(config_path: Path) -> None:
    """
    Make sure the Hermes profile config at `config_path` runs this hook,
    fail-closed, before every tool. A config that names no `hooks:` at all
    gets the block appended as text, so its comments and order stand. One
    that already has `hooks:` is never rewritten: it is accepted only if it
    already carries this hook with `fail_closed: true`, and raises ValueError
    otherwise so the owner merges it. A missing config has nothing to install into.
    """
    if not config_path.is_file():
        return
    import yaml
    text = config_path.read_text(encoding="utf-8")
    config = yaml.safe_load(text) or {}
    if not isinstance(config, dict):
        raise ValueError(f"the profile config {config_path} is not a mapping")
    hooks = config.get("hooks")
    if hooks is None:
        block = (f"\nhooks:\n  pre_tool_call:\n    - command: {json.dumps(hook_command())}\n"
                 f"      timeout: {HOOK_TIMEOUT_SECONDS}\n      fail_closed: true\n")
        with config_path.open("a", encoding="utf-8", newline="") as fh:
            fh.write(("" if text.endswith("\n") or not text else "\n") + block)
        return
    entries = hooks.get("pre_tool_call") if isinstance(hooks, dict) else None
    for entry in entries if isinstance(entries, list) else []:
        if (isinstance(entry, dict) and "hermes_envelope_hook" in str(entry.get("command", ""))
                and entry.get("fail_closed") is True):
            return
    raise ValueError(
        f"the profile config {config_path} has its own hooks: and no fail-closed "
        f"pre_tool_call entry running {hook_command()}; add one, FRIDAY will not rewrite hooks")


def main() -> int:
    code, out = run(sys.stdin.read())
    if out:
        print(out)
    return code


if __name__ == "__main__":
    # Hermes starts this by path: the script's own folder must not shadow the
    # standard library, and the repository root is what makes `friday` importable.
    here = os.path.dirname(os.path.abspath(__file__))
    sys.path[:] = [os.path.dirname(here)] + [p for p in sys.path
                                              if os.path.abspath(p or ".") != here]
    sys.exit(main())
