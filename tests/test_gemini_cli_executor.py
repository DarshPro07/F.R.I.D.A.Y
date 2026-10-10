"""
FAB-002: Gemini CLI as an execution runtime. Only the CLI process is faked.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from friday.executors import claude_code as CC
from friday.executors import work_packet as wp
from friday.executors.gemini_cli import GeminiCliExecutor

FAKE = str(Path(__file__).parent / "fake_coding_cli.py")


class FakeGemini(GeminiCliExecutor):
    def __init__(self, mode: str, log: Path, creds: Path, write: str = "friday/uploader.py"):
        self.prefix = [FAKE, "gemini", mode, write, str(log)]
        self.creds = creds

    def locate(self):
        return sys.executable

    def credentials(self):
        return self.creds

    def work_command(self, path):
        return [path, *self.prefix, *super().work_command(path)[1:]]


@pytest.fixture
def packet():
    bundle = CC.TaskBundle(goal="add a TTL cache", workspace="ws", isolate=False,
                           allowed_paths=("friday/uploader.py",))
    return wp.WorkPacket(bundle, wp.Budget(usd=0.0, seconds=30.0),
                         ((sys.executable, "-c", "print('ok')"),))


@pytest.fixture
def creds(tmp_path):
    path = tmp_path / "oauth_creds.json"
    path.write_text("{}", encoding="utf-8")
    return path


def test_gemini_does_the_packet_in_the_workspace_and_returns_a_receipt(
        tmp_path, packet, creds):
    log, ws = tmp_path / "cli.json", tmp_path / "ws"
    ws.mkdir()
    receipt = FakeGemini("ok", log, creds).run(packet, str(ws))

    assert receipt.runtime == "gemini-cli" and receipt.status == wp.DONE
    assert receipt.result == "Added the cache."
    assert receipt.model == "gemini-2.5-pro"
    assert receipt.usage == {"input_tokens": 200, "output_tokens": 50,
                             "cached_tokens": 10, "reasoning_tokens": 7}
    assert (ws / "friday" / "uploader.py").is_file()
    seen = json.loads(log.read_text(encoding="utf-8"))
    assert Path(seen["cwd"]).resolve() == ws.resolve()
    assert "add a TTL cache" in seen["prompt"]
    assert "yolo" not in seen["args"]


def test_a_gemini_with_no_sign_in_is_reported_and_never_run(tmp_path, packet):
    log = tmp_path / "cli.json"
    receipt = FakeGemini("ok", log, tmp_path / "absent.json").run(packet, str(tmp_path))
    assert receipt.status == wp.UNAVAILABLE
    assert any("Login with Google" in w for w in receipt.warnings)
    assert not log.exists()


def test_a_sign_out_found_mid_run_is_unavailable_not_failed(tmp_path, packet, creds):
    receipt = FakeGemini("auth", tmp_path / "cli.json", creds).run(packet, str(tmp_path))
    assert receipt.status == wp.UNAVAILABLE


def test_a_crash_is_a_failed_receipt(tmp_path, packet, creds):
    receipt = FakeGemini("crash", tmp_path / "cli.json", creds).run(packet, str(tmp_path))
    assert receipt.status == wp.FAILED
    assert any("segfault" in w for w in receipt.warnings)


def test_a_missing_gemini_is_unavailable(tmp_path, packet):
    class Missing(GeminiCliExecutor):
        def locate(self):
            return None

    receipt = Missing().run(packet, str(tmp_path))
    assert receipt.status == wp.UNAVAILABLE
    assert "not installed" in receipt.warnings[0]
