"""Provider worker shim tests: stand-in CLIs, never real Codex/OpenCode."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
PY = "F:/F.R.I.D.A.Y/.venv-build/Scripts/python.exe"

import build_integrate as bi  # noqa: E402


def _cli(tmp_path: Path, body: str) -> Path:
    path = tmp_path / "standin.py"
    path.write_text("""
import sys
if sys.argv[1:3] == ["login", "status"]:
    print("Using ChatGPT")
    raise SystemExit(0)
""" + body, encoding="utf-8")
    return path


def _run_worker(tmp_path: Path, monkeypatch, provider: str, model: str, cli: Path, *extra: str):
    wt = tmp_path / "wt"
    (wt / ".build").mkdir(parents=True)
    subprocess.run(["git", "init", "-q"], cwd=wt, check=True)
    (wt / "base.txt").write_text("base\n", encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=wt, check=True)
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t", "commit", "-q", "-m", "base"],
                   cwd=wt, check=True)
    env = os.environ.copy()
    env["FRIDAY_CODEX_ARGV"] = json.dumps([sys.executable, str(cli)])
    env["FRIDAY_OPENCODE_ARGV"] = json.dumps([sys.executable, str(cli)])
    monkeypatch.setenv("FRIDAY_CODEX_ARGV", env["FRIDAY_CODEX_ARGV"])
    monkeypatch.setenv("FRIDAY_OPENCODE_ARGV", env["FRIDAY_OPENCODE_ARGV"])
    monkeypatch.setattr("build_providers.codex_on_plan", lambda: True)
    return subprocess.run([PY, str(ROOT / "scripts" / "build_worker.py"), provider, model,
                           "--prompt", "do it", *extra],
                          cwd=wt, env=env, capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=30)


def _events(stdout: str) -> list[dict]:
    return [json.loads(line) for line in stdout.splitlines() if line.strip()]


def test_codex_success_wraps_events_and_usage(tmp_path, monkeypatch):
    cli = _cli(tmp_path, """
import json, sys
for ev in [
 {"type":"thread.started","thread_id":"t1"},
 {"type":"item.completed","item":{"type":"agent_message","text":"done"}},
 {"type":"turn.completed","usage":{"input_tokens":3,"cached_input_tokens":2,"output_tokens":5}},
]:
 print(json.dumps(ev), flush=True)
""")
    out = _run_worker(tmp_path, monkeypatch, "codex", "gpt-5", cli)
    events = _events(out.stdout)
    assert out.returncode == 0
    assert events[0]["subtype"] == "init" and events[0]["provider"] == "codex"
    assert any(e["type"] == "provider_event" and e["event"]["type"] == "thread.started" for e in events)
    res = events[-1]
    assert res["subtype"] == "success" and res["result"] == "done"
    assert res["usage"] == {"input_tokens": 3, "cache_read_input_tokens": 2,
                            "cache_creation_input_tokens": 0, "output_tokens": 5}
    assert res["num_turns"] == 1 and res["total_cost_usd"] == 0.0


def test_codex_limit_exits_three_and_carries_limit(tmp_path, monkeypatch):
    cli = _cli(tmp_path, """
import json
print(json.dumps({"type":"turn.failed","error":{"message":"usage_limit_reached: You've hit your usage limit, try again in 2 hours"}}))
""")
    out = _run_worker(tmp_path, monkeypatch, "codex", "gpt-5", cli)
    res = _events(out.stdout)[-1]
    assert out.returncode == 3
    assert res["subtype"] == "error_limit" and res["is_error"] is True
    assert res["limit"]["state"] == "QUOTA_EXHAUSTED"
    assert "usage_limit_reached" in res["result"]


def test_read_only_tamper_is_error(tmp_path, monkeypatch):
    cli = _cli(tmp_path, """
import json, pathlib
pathlib.Path("changed.txt").write_text("bad")
print(json.dumps({"type":"turn.completed","usage":{"input_tokens":1,"output_tokens":1}}))
""")
    out = _run_worker(tmp_path, monkeypatch, "codex", "gpt-5", cli, "--read-only")
    assert _events(out.stdout)[-1]["subtype"] == "error_tampered"


def test_opencode_success_uses_slim_env(tmp_path, monkeypatch):
    cli = _cli(tmp_path, """
import json, os, pathlib
assert os.environ["OPENCODE_PURE"] == "1"
assert pathlib.Path(os.environ["XDG_DATA_HOME"]) == pathlib.Path.cwd() / ".build" / "opencode-data"
for ev in [
 {"type":"text","sessionID":"s","part":{"text":"ok"}},
 {"type":"step_finish","sessionID":"s","part":{"tokens":{"input":4,"output":6,"reasoning":1,"cache":{"read":2,"write":3}},"cost":0.9,"reason":"stop"}},
]:
 print(json.dumps(ev), flush=True)
""")
    out = _run_worker(tmp_path, monkeypatch, "opencode", "opencode/step-5-preview-free", cli)
    res = _events(out.stdout)[-1]
    assert out.returncode == 0 and res["subtype"] == "success"
    assert res["result"] == "ok" and res["usage"]["input_tokens"] == 4
    assert res["usage"]["cache_read_input_tokens"] == 2
    assert res["num_turns"] == 1


def test_unapproved_opencode_refused_before_process_starts(tmp_path, monkeypatch):
    cli = _cli(tmp_path, "raise SystemExit('must not start')\n")
    out = _run_worker(tmp_path, monkeypatch, "opencode", "opencode/mimo-v2.6-flash-free", cli)
    res = _events(out.stdout)[-1]
    assert out.returncode == 2
    assert res["subtype"] == "error_refused" and "zero-retention" in res["result"]


def test_wall_clock_kill_is_error_wall(tmp_path, monkeypatch):
    cli = _cli(tmp_path, """
import json, time
print(json.dumps({"type":"turn.completed","usage":{"input_tokens":1,"output_tokens":1}}), flush=True)
time.sleep(30)
""")
    out = _run_worker(tmp_path, monkeypatch, "codex", "gpt-5", cli, "--wall", "1")
    res = _events(out.stdout)[-1]
    assert out.returncode == 1
    assert res["subtype"] == "error_wall" and res["is_error"] is True


def test_provider_silence_streams_first_event_then_kills_tree_as_stall(tmp_path, monkeypatch):
    marker = tmp_path / "child_survived.txt"
    cli = _cli(tmp_path, f"""
import json, pathlib, subprocess, sys, time
marker = {str(marker)!r}
subprocess.Popen([sys.executable, "-c",
    "import pathlib,time; time.sleep(3); pathlib.Path(" + repr(marker) + ").write_text('alive')"])
print(json.dumps({{"type":"turn.completed","usage":{{"input_tokens":1,"output_tokens":1}}}}), flush=True)
time.sleep(30)
""")
    wt = tmp_path / "wt"
    (wt / ".build").mkdir(parents=True)
    subprocess.run(["git", "init", "-q"], cwd=wt, check=True)
    env = os.environ.copy()
    env["FRIDAY_CODEX_ARGV"] = json.dumps([sys.executable, str(cli)])
    monkeypatch.setenv("FRIDAY_CODEX_ARGV", env["FRIDAY_CODEX_ARGV"])
    monkeypatch.setattr("build_providers.codex_on_plan", lambda: True)
    proc = subprocess.Popen([PY, str(ROOT / "scripts" / "build_worker.py"), "codex", "gpt-5",
                             "--prompt", "do it", "--wall", "20", "--stall", "1"],
                            cwd=wt, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            text=True, encoding="utf-8", errors="replace")
    assert proc.stdout is not None
    first = json.loads(proc.stdout.readline())
    second = json.loads(proc.stdout.readline())
    assert first["subtype"] == "init"
    assert second["type"] == "provider_event"
    assert second["event"]["type"] == "turn.completed"
    rest, err = proc.communicate(timeout=10)
    assert proc.returncode == 1, err
    events = [second, *[json.loads(line) for line in rest.splitlines() if line.strip()]]
    assert events[-1]["subtype"] == "error_stall"
    time.sleep(3.5)
    assert not marker.exists()


def test_verify_mode_prints_one_receipt_object(tmp_path, monkeypatch):
    cli = _cli(tmp_path, """
import json
print(json.dumps({"type":"item.completed","item":{"type":"agent_message",
  "text":'{"verdict":"PASS","requirement_ids":["R-1"],"failing_criteria":[],"evidence_refs":[]}'}}), flush=True)
""")
    wt = tmp_path / "wt"
    (wt / ".build").mkdir(parents=True)
    subprocess.run(["git", "init", "-q"], cwd=wt, check=True)
    env = os.environ.copy()
    env["FRIDAY_CODEX_ARGV"] = json.dumps([sys.executable, str(cli)])
    monkeypatch.setenv("FRIDAY_CODEX_ARGV", env["FRIDAY_CODEX_ARGV"])
    monkeypatch.setattr("build_providers.codex_on_plan", lambda: True)
    out = subprocess.run([PY, str(ROOT / "scripts" / "build_worker.py"), "codex", "gpt-5",
                          "--verify", "--read-only"],
                         input="judge the change", cwd=wt, env=env, capture_output=True, text=True,
                         encoding="utf-8", errors="replace", timeout=30)
    lines = [ln for ln in out.stdout.splitlines() if ln.strip()]
    assert out.returncode == 0 and len(lines) == 1
    res = json.loads(lines[0])
    assert res["subtype"] == "success" and res["is_error"] is False
    assert set(res) == {"type", "subtype", "is_error", "result", "num_turns",
                        "total_cost_usd", "session_id", "provider", "route"}
    # parse_receipt works unchanged on the shim's verify output
    receipt = bi.parse_receipt(out.stdout)
    assert receipt["verdict"] == "PASS"
