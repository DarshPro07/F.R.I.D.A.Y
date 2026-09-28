"""
PRD-OWN-WINPROMPT: a worker's prompt must never ride the Windows command
line.

Windows caps a process's command line and environment block at roughly
32,767 characters, and this repository has already been crash-looped by that
limit once - AGENTS.md's operational notes record dev mode's `watchfiles`
overflowing the Windows env-var limit on a large change set. A work packet
(`TaskBundle.prompt()`) for a real engineering task, with real context and
acceptance criteria, is easily tens of thousands of characters, so if it ever
rode argv instead of stdin the failure would be the same shape: silent past a
certain size, then a hard crash past the limit.

`friday/executors/cli.py` already sends the prompt over stdin and never
touches argv with it - see its module docstring ("The prompt goes on
stdin.") and `Run.start()`, which writes `launch.prompt` to the child's stdin
and only then closes it. This file is the regression test PRD-OWN-WINPROMPT
asked for: a realistic, 40,000+ character work packet, run through the real
transport code against a fake CLI (never the installed one), proving both
that no argv element could carry it and that the whole thing actually arrives.
"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

from friday.executors import cli
from friday.executors.claude_code import TaskBundle

FAKE_CLI = Path(__file__).parent / "fake_claude_cli.py"

#: No single argv element may approach the ~32,767-char Windows command-line
#: limit. 30,000 leaves headroom for the rest of the command line (the other
#: flags, quoting) the way PRD-OWN-WINPROMPT specifies it.
MAX_ARGV_ELEMENT = 30_000


def _make_fake_claude_shim(tmp_path: Path) -> Path:
    """
    A `.cmd` shim - the same shape a real npm install's `claude.cmd` takes
    (see cli.py's `_KNOWN_LOCATIONS`). Windows runs `.cmd` files directly
    without `shell=True`, which is what lets this stand in for whatever
    `claude_path()` resolves to. Spiked separately to confirm `%*` forwards a
    quoted, space-and-quote-bearing argument (like `--settings <json>`)
    unchanged through the cmd.exe hop.
    """
    shim = tmp_path / "fake_claude.cmd"
    shim.write_text(
        f'@echo off\r\n"{sys.executable}" "{FAKE_CLI}" %*\r\n',
        encoding="utf-8")
    return shim


def _huge_work_packet() -> TaskBundle:
    """
    Realistic in shape, not a synthetic wall of one repeated character: the
    kind of context and acceptance-criteria list a long-running engineering
    task actually accumulates, built through the real `TaskBundle.prompt()`
    method rather than a hand-written string.
    """
    context = tuple(
        f"- friday/module_{i:03d}.py exists and exports handler_{i:03d}(); "
        f"it currently has no retry policy and no timeout, and the ticket "
        f"asks for both plus a regression test that exercises the timeout "
        f"path under a simulated slow backend."
        for i in range(220)
    )
    acceptance = tuple(
        f"module_{i:03d}: a test that failed before the fix and passes after"
        for i in range(80)
    )
    return TaskBundle(
        goal="Add a bounded retry with jittered backoff to every handler "
             "in the batch, without changing any public signature.",
        workspace=".", project="soak", context=context, acceptance=acceptance,
        constraints=("no new dependencies", "keep the existing logging shape"))


def test_the_work_packet_is_realistically_large():
    """Sanity check on the fixture itself, so the regression below is real."""
    assert len(_huge_work_packet().prompt()) >= 40_000


def test_no_argv_element_could_carry_the_prompt(monkeypatch, tmp_path):
    """
    Static check on `Launch.argv()`: whatever it emits, none of it is - or
    could be mistaken for - the prompt.
    """
    shim = _make_fake_claude_shim(tmp_path)
    monkeypatch.setattr(cli, "claude_path", lambda: str(shim))

    prompt = _huge_work_packet().prompt()
    argv = cli.Launch(prompt=prompt, cwd=str(tmp_path)).argv()

    assert argv, "argv() produced nothing to check"
    assert max(len(a) for a in argv) <= MAX_ARGV_ELEMENT
    assert not any(prompt in a for a in argv)


def test_a_huge_prompt_reaches_the_cli_over_stdin(monkeypatch, tmp_path):
    """
    The regression PRD-OWN-WINPROMPT asked for: run the real transport code
    (`cli.Launch` + `cli.Run`, via `cli.stream`) against a fake CLI with a
    40,000+ character work packet, and prove two things at once - the
    invocation succeeds (a real process, a real parsed result), and the fake
    CLI's own argv/stdin log shows the packet arrived whole over stdin and
    never touched argv.
    """
    shim = _make_fake_claude_shim(tmp_path)
    monkeypatch.setattr(cli, "claude_path", lambda: str(shim))

    log_path = tmp_path / "fake_claude_log.json"
    monkeypatch.setenv("FAKE_CLAUDE_LOG", str(log_path))
    monkeypatch.setenv("FAKE_CLAUDE_SESSION_ID", "transport-test-session")
    monkeypatch.delenv("FAKE_CLAUDE_EXIT", raising=False)

    prompt = _huge_work_packet().prompt()
    assert len(prompt) >= 40_000
    # The fake reads its settings from these; a worker only receives the
    # variables a launch names (V7-D18: never FRIDAY's own secrets).
    launch = cli.Launch(prompt=prompt, cwd=str(tmp_path),
                        env_passthrough=("FAKE_CLAUDE_LOG", "FAKE_CLAUDE_SESSION_ID",
                                         "FAKE_CLAUDE_EXIT"))

    run, events = asyncio.run(cli.stream(launch, timeout=60))

    # The invocation succeeded: a real child process ran to completion and
    # its stream-json was parsed into real events, including a result.
    assert run.process is not None
    assert run.process.returncode == 0
    assert any(e.kind == "result" for e in events)
    assert run.session_id == "transport-test-session"

    logged = json.loads(log_path.read_text(encoding="utf-8"))
    assert logged["stdin_len"] == len(prompt), (
        "the fake CLI did not receive the whole prompt over stdin")
    assert all(len(a) <= MAX_ARGV_ELEMENT for a in logged["argv"]), (
        "an argv element the child actually received exceeded the limit")
    assert not any(prompt in a for a in logged["argv"])
