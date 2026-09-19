"""The Claude prompt must never ride on the command line.

Two independent reasons, and the second one is the sharp edge:

1. `--allowedTools` / `--disallowedTools` are variadic, so a trailing
   positional prompt is swallowed as a tool name (the reason the stdin
   transport was chosen originally - see cli.py's module docstring).

2. Windows `CreateProcess` caps the command line at 32,767 characters.
   Measured on this host: an argv prompt of 32 KB fails with
   `FileNotFoundError: [WinError 206] The filename or extension is too long`
   - an error that says nothing about length and reads like a missing
   binary. A realistic Claude work packet (objective + requirement +
   context + file excerpts + evidence) runs 30-60 KB; one carrying a diff
   can be 200 KB. So argv would silently kill every serious delegation.

These tests drive the REAL `Runner.start()` against a stand-in `claude`
that echoes back what it received, at a size argv provably cannot carry.
"""
from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

sys.path.insert(0, ".")

from friday.executors import cli

#: Above the Windows CreateProcess limit by a wide margin. If the prompt
#: ever moves onto argv, this cannot be launched on Windows at all.
BIG_PACKET_BYTES = 256 * 1024


def _fake_claude(tmp_path: Path) -> Path:
    """A `claude` that reports the byte-length of its stdin and its argv,
    as one JSON event line, then exits. Runs under the current
    interpreter so it works on every platform the suite runs on."""
    script = tmp_path / "fake_claude.py"
    script.write_text(textwrap.dedent("""
        import json, sys
        data = sys.stdin.buffer.read()
        print(json.dumps({
            "type": "result",
            "stdin_bytes": len(data),
            "argv_total_chars": sum(len(a) for a in sys.argv),
            "prompt_on_argv": any(len(a) > 4096 for a in sys.argv),
        }))
    """))
    if os.name == "nt":
        wrapper = tmp_path / "claude.cmd"
        wrapper.write_text(f'@"{sys.executable}" "{script}" %*\n')
    else:
        wrapper = tmp_path / "claude"
        wrapper.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{script}" "$@"\n')
        wrapper.chmod(0o755)
    return wrapper


def _launch_and_read(monkeypatch, tmp_path, prompt: str) -> dict:
    fake = _fake_claude(tmp_path)
    monkeypatch.setattr(cli, "claude_path", lambda: str(fake))
    launch = cli.Launch(prompt=prompt, cwd=str(tmp_path))

    async def go():
        runner = cli.Run(launch)
        await runner.start()
        assert runner.process is not None
        out, err = await asyncio.wait_for(runner.process.communicate(), timeout=60)
        assert runner.process.returncode == 0, err.decode(errors="replace")[-500:]
        line = [l for l in out.decode().splitlines() if l.startswith("{")][-1]
        return json.loads(line)

    return asyncio.run(go())


class TestThePromptTravelsOnStdin:
    def test_a_256kb_work_packet_arrives_intact(self, monkeypatch, tmp_path):
        """The realistic-large case the Gawkbot issue is about."""
        prompt = ("packet line %06d " % 0).ljust(63) + "\n"
        prompt = prompt * (BIG_PACKET_BYTES // len(prompt))
        seen = _launch_and_read(monkeypatch, tmp_path, prompt)
        assert seen["stdin_bytes"] == len(prompt.encode("utf-8"))
        assert seen["prompt_on_argv"] is False

    def test_the_command_line_stays_small_regardless_of_prompt(self, monkeypatch, tmp_path):
        """The argv the CLI is launched with must not scale with the prompt
        at all - otherwise some packet size will cross the limit."""
        small = _launch_and_read(monkeypatch, tmp_path, "x")
        big = _launch_and_read(monkeypatch, tmp_path, "x" * BIG_PACKET_BYTES)
        assert big["argv_total_chars"] == small["argv_total_chars"]

    def test_unicode_survives_the_pipe(self, monkeypatch, tmp_path):
        """Work packets carry file excerpts, which carry whatever the repo
        does; a transport that mangles non-ASCII corrupts the task."""
        prompt = "objective: fix résumé parsing — 日本語 test → ✓\n" * 2000
        seen = _launch_and_read(monkeypatch, tmp_path, prompt)
        assert seen["stdin_bytes"] == len(prompt.encode("utf-8"))


class TestArgvIsNotAnOption:
    """Negative control: prove the failure the transport guards against is
    real on this platform, so the guard is tested against a fact and not a
    story."""

    @pytest.mark.skipif(os.name != "nt", reason="Windows CreateProcess limit")
    def test_windows_refuses_a_32kb_argv_prompt(self):
        prompt = "x" * (32 * 1024)
        with pytest.raises((OSError, ValueError)) as excinfo:
            subprocess.run([sys.executable, "-c", "pass", prompt],
                           capture_output=True, timeout=30)
        # WinError 206 - and note it is NOT a length error by name, which is
        # exactly why an argv transport would fail confusingly in production.
        assert getattr(excinfo.value, "winerror", None) == 206 or "too long" in str(excinfo.value)

    def test_the_launch_argv_never_contains_the_prompt(self, monkeypatch):
        """Static check on the argv builder itself, independent of the
        subprocess: the prompt string must not appear in any argument."""
        monkeypatch.setattr(cli, "claude_path", lambda: "claude")
        marker = "THIS-IS-THE-PROMPT-" + "z" * 100
        argv = cli.Launch(prompt=marker, cwd=".").argv()
        assert all(marker not in a for a in argv)
        assert all(len(a) < 4096 for a in argv), "an oversized argument slipped into argv"
