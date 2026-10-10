"""
FAB-002: Codex as an execution runtime. Only the CLI process is faked
(`tests/fake_coding_cli.py`); locating it, reading its sign-in, building the
command, the worker environment and the receipt are the real code.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from friday.executors import claude_code as CC
from friday.executors import work_packet as wp
from friday.executors.codex import CodexExecutor

FAKE = str(Path(__file__).parent / "fake_coding_cli.py")


class FakeCodex(CodexExecutor):
    """The real executor with its binary swapped for the fake CLI."""

    def __init__(self, mode: str, log: Path, write: str = "friday/uploader.py"):
        self.prefix = [FAKE, "codex", mode, write, str(log)]

    def locate(self):
        return sys.executable

    def status_command(self, path):
        return [path, *self.prefix, "login", "status"]

    def work_command(self, path, model):
        return [path, *self.prefix, *super().work_command(path, model)[1:]]


@pytest.fixture
def packet():
    bundle = CC.TaskBundle(goal="add a TTL cache", workspace="ws", isolate=False,
                           allowed_paths=("friday/uploader.py",),
                           acceptance=("entries expire",))
    return wp.WorkPacket(bundle, wp.Budget(usd=0.0, seconds=30.0),
                         ((sys.executable, "-c", "print('ok')"),))


def test_codex_does_the_packet_in_the_workspace_and_returns_a_receipt(tmp_path, packet):
    log = tmp_path / "cli.json"
    ws = tmp_path / "ws"
    ws.mkdir()
    receipt = FakeCodex("ok", log).run(packet, str(ws))

    assert receipt.runtime == "codex" and receipt.status == wp.DONE
    assert receipt.result == "Added the cache."
    assert receipt.model == "gpt-5.5"
    assert receipt.cost_usd == 0.0          # a ChatGPT sign-in has no per-call price
    assert receipt.usage == {"input_tokens": 120, "output_tokens": 30,
                             "cached_tokens": 40, "reasoning_tokens": 5}
    assert (ws / "friday" / "uploader.py").is_file()

    seen = json.loads(log.read_text(encoding="utf-8"))
    assert Path(seen["cwd"]).resolve() == ws.resolve()
    assert "add a TTL cache" in seen["prompt"]            # the packet rode on stdin
    assert "friday/uploader.py" in seen["prompt"]
    assert seen["args"][seen["args"].index("-s") + 1] == "workspace-write"
    assert "danger-full-access" not in seen["args"]


def test_a_signed_out_codex_is_reported_and_never_run(tmp_path, packet):
    log = tmp_path / "cli.json"
    receipt = FakeCodex("signed_out", log).run(packet, str(tmp_path))
    assert receipt.status == wp.UNAVAILABLE
    assert any("codex login" in w for w in receipt.warnings)
    assert not log.exists(), "a signed-out CLI must not be launched on the packet"


def test_a_sign_out_found_mid_run_is_unavailable_not_failed(tmp_path, packet):
    receipt = FakeCodex("auth", tmp_path / "cli.json").run(packet, str(tmp_path))
    assert receipt.status == wp.UNAVAILABLE
    assert any("401" in w for w in receipt.warnings)


def test_a_crash_is_a_failed_receipt_with_the_reason(tmp_path, packet):
    receipt = FakeCodex("crash", tmp_path / "cli.json").run(packet, str(tmp_path))
    assert receipt.status == wp.FAILED
    assert any("segfault" in w for w in receipt.warnings)


def test_the_time_budget_stops_a_hung_run(tmp_path, packet):
    short = wp.WorkPacket(packet.bundle, wp.Budget(usd=0.0, seconds=1.5), packet.checks)
    receipt = FakeCodex("hang", tmp_path / "cli.json").run(short, str(tmp_path))
    assert receipt.status == wp.FAILED
    assert any("did not finish within" in w for w in receipt.warnings)


def test_a_missing_codex_is_unavailable(tmp_path, packet):
    class Missing(CodexExecutor):
        def locate(self):
            return None

    receipt = Missing().run(packet, str(tmp_path))
    assert receipt.status == wp.UNAVAILABLE
    assert "not installed" in receipt.warnings[0]
