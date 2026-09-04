"""
friday/fabric_cli.py -- the CLI integration mode: invoke, work, exit.

Seven of the unintegrated upstreams (cline, crewai, strix, agenticseek,
openhands, firstmate, openworker) are command-line agents. The mode vocabulary
had BUILTIN, ADAPTER, MCP, SKILL, SIDECAR and REFERENCE_ONLY, and none of them
describes a program you run once. A contributor could not write a descriptor
for these even in principle, which is why all seven sat on disk unreachable.

This is the cheapest real execution mode - no port, no protocol, no long-lived
process - which is why it is first after the supervisor.

The single most important line in this module is that `argv` is a tuple and
`shell=False` is not configurable. A `{placeholder}` is replaced as ONE argv
element, never re-split and never handed to a shell, so a task string
containing `; rm -rf /` arrives as one harmless argument.
"""
from __future__ import annotations

import json
import pathlib
import re
import subprocess
import threading
import time
from dataclasses import dataclass, field

from friday import contracts as c
from friday import fabric_process

#: Output contracts. What the caller gets back, and what counts as a failure.
TEXT_STDOUT = "TEXT_STDOUT"
JSON_STDOUT = "JSON_STDOUT"
EXIT_CODE = "EXIT_CODE"
FILE = "FILE"

MAX_OUTPUT = 100_000

#: Identifier-shaped only. A JSON literal on the command line is not a
#: placeholder, and treating it as one refused every command that passes one.
_PLACEHOLDER = re.compile(r"\{([A-Za-z_][A-Za-z0-9_]*)\}")


class CliError(RuntimeError):
    """A command could not be built. Raised before anything is spawned."""


@dataclass(frozen=True)
class Command:
    """One operation, as a command line. Declared by an adapter."""

    argv: tuple[str, ...]
    timeout: float = 120.0
    output: str = TEXT_STDOUT
    output_path: str = ""
    success_exit: tuple[int, ...] = (0,)
    cwd: str = ""
    env: dict = field(default_factory=dict)


@dataclass(frozen=True)
class Bootstrap:
    """How to tell whether an upstream is built, and how it would be built.

    `install` is never run automatically. `npm ci` on an unaudited clone is a
    supply-chain action and needs the same explicit go-ahead as spending money,
    so it is exposed as an operator command rather than a side effect of a user
    asking a question.
    """

    check: tuple[str, ...]
    install: tuple[str, ...] = ()
    cwd: str = ""


#: One in-flight invocation per provider. Queueing is the objective engine's
#: job; a fabric that queues silently turns "it is slow" into "it is stuck".
_BUSY: dict[str, float] = {}
_LOCK = threading.Lock()


def _fill(argv, arguments: dict) -> list[str]:
    """Substitute {name} placeholders, one argv element each.

    Only identifier-shaped braces are placeholders. Anything else is left
    exactly as written, because argv legitimately contains braces: a JSON
    literal like `{"ok": true}` was being read as a placeholder named
    `"ok": true` and refused, which broke every command that passes JSON on the
    command line - most of them.

    An unknown placeholder raises rather than passing the literal `{name}` down
    to an agent, which would look like a working call and produce nonsense.
    """
    out = []
    for part in argv:
        def swap(match):
            key = match.group(1)
            if key not in arguments:
                raise CliError(
                    f"no argument {key!r} for placeholder in {part!r}; "
                    f"given {sorted(arguments)}")
            return str(arguments[key])
        out.append(_PLACEHOLDER.sub(swap, part))
    return out


def _redacted(argv, arguments: dict) -> str:
    """The command line for evidence, with argument VALUES removed.

    Names are kept because "which arguments were passed" is a debugging
    question; values are dropped because one of them is eventually a token.
    """
    values = {str(v) for v in arguments.values() if str(v)}
    parts = []
    for part in argv:
        parts.append("<arg>" if part in values else part)
    return " ".join(parts)


def _root(provider) -> pathlib.Path:
    base = fabric_process.ROOT / "third_party" / "upstream"
    return base / provider.upstream if provider.upstream else fabric_process.ROOT


def health(provider, bootstrap: Bootstrap) -> dict:
    """READY only when the upstream actually answers its own version check."""
    from friday import fabric

    cwd = _root(provider) / bootstrap.cwd if bootstrap.cwd else _root(provider)
    if not cwd.is_dir():
        return {"state": fabric.UNAVAILABLE, "detail": f"not cloned: {cwd}"}
    try:
        done = subprocess.run(  # noqa: S603 - argv, no shell
            list(bootstrap.check), cwd=str(cwd), capture_output=True,
            text=True, timeout=30, shell=False,
            env=fabric_process._environment(fabric_process.Spec(argv=()), 0))
    except FileNotFoundError as exc:
        return {"state": fabric.UNAVAILABLE, "detail": f"not built: {exc}"}
    except subprocess.TimeoutExpired:
        return {"state": fabric.UNAVAILABLE, "detail": "version check timed out"}
    if done.returncode != 0:
        detail = (done.stderr or done.stdout or "").strip()[:200]
        install = " ".join(bootstrap.install) or "see the upstream README"
        return {"state": fabric.UNAVAILABLE,
                "detail": f"not built ({detail}); run: {install}"}
    return {"state": fabric.READY,
            "detail": (done.stdout or "").strip()[:120] or "check passed"}


def run(provider, operation: str, commands: dict, *, run_id: str = "",
        **arguments) -> c.ActionResult:
    """Invoke one command and return an honest envelope."""
    tool_id = f"fabric.{provider.id}.{operation}"
    result = c.started(run_id or c.new_run_id(), tool_id)

    command = commands.get(operation)
    if command is None:
        return c.failed(result, f"{provider.id} has no CLI command for {operation!r}")

    with _LOCK:
        if provider.id in _BUSY:
            since = time.monotonic() - _BUSY[provider.id]
            return c.failed(
                result, f"{provider.id} is busy ({since:.0f}s in); try again")
        _BUSY[provider.id] = time.monotonic()

    try:
        try:
            argv = _fill(command.argv, arguments)
        except CliError as exc:
            return c.failed(result, str(exc))

        cwd = _root(provider) / command.cwd if command.cwd else _root(provider)
        if not cwd.is_dir():
            return c.failed(result, f"{provider.id}: not cloned at {cwd}")

        spec = fabric_process.Spec(argv=tuple(argv), cwd=cwd, env=command.env)
        started = time.monotonic()
        try:
            done = subprocess.run(  # noqa: S603 - argv, no shell
                argv, cwd=str(cwd),
                env=fabric_process._environment(spec, 0),
                capture_output=True, text=True, shell=False,
                timeout=command.timeout)
        except subprocess.TimeoutExpired as exc:
            partial = (exc.stdout or "")[-1000:] if exc.stdout else ""
            return c.failed(
                result,
                f"{provider.id}.{operation} exceeded {command.timeout:.0f}s "
                f"and was killed; last output: {partial}")
        except FileNotFoundError as exc:
            return c.failed(result, f"{provider.id}: {exc}")
        elapsed = time.monotonic() - started

        if done.returncode not in command.success_exit:
            detail = (done.stderr or done.stdout or "").strip()[-600:]
            return c.failed(
                result,
                f"{provider.id}.{operation} exited {done.returncode}: {detail}")

        stdout = (done.stdout or "")[:MAX_OUTPUT]
        if command.output == JSON_STDOUT:
            try:
                value = json.loads(stdout)
            except json.JSONDecodeError as exc:
                return c.failed(
                    result,
                    f"{provider.id}.{operation} promised JSON and gave "
                    f"{exc}; first 200 chars: {stdout[:200]}")
        elif command.output == EXIT_CODE:
            value = done.returncode
        elif command.output == FILE:
            try:
                target = cwd / _fill((command.output_path,), arguments)[0]
            except CliError as exc:
                return c.failed(result, str(exc))
            if not target.is_file():
                return c.failed(
                    result, f"{provider.id}.{operation} wrote no {target}")
            value = target.read_text(encoding="utf-8", errors="replace")[:MAX_OUTPUT]
        else:
            value = stdout.strip()

        # An exit code of 0 is stated as what it is - the process did not
        # complain - and never dressed up as a verified outcome. Same rule
        # `fabric.call()` already applies to a bare return value.
        return c.succeeded(
            result,
            verification=c.Verification(
                method="fabric.cli",
                evidence=(f"{_redacted(argv, arguments)} exited "
                          f"{done.returncode} in {elapsed:.1f}s; exit code is "
                          f"the only claim, not a check of the work"),
            ),
            output=value,
        )
    finally:
        with _LOCK:
            _BUSY.pop(provider.id, None)
