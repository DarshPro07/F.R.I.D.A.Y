"""The build kernel's scheduling, launch plan and watcher (no real claude, no real worktrees)."""

from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import build_kernel as K  # noqa: E402
import prd_ledger as PL  # noqa: E402


def row(id_, priority="P0", status="MISSING", files=("a.py",)):
    return {"id": id_, "priority": priority, "status": status, "files": list(files),
            "summary": "s", "tests": [], "current_implementation": "c",
            "acceptance": "acc", "next_action": "n"}


def test_ready_needs_terminal_deps_and_sorts_priority_then_id():
    term = next(iter(PL.TERMINAL))
    rows = [row("B", "P1"), row("A", "P1"), row("Z", "P0"), row("C", "P0"),
            row("D0", status=term), row("D1", status="MISSING")]
    deps = {"C": ["D0"], "Z": ["D1"], "A": ["D0"]}
    # Z waits on the open D1; C and A wait on the terminal D0 and are free.
    assert [r["id"] for r in K.ready(rows, deps)] == ["C", "D1", "A", "B"]


def test_unknown_dependency_id_is_an_error():
    with pytest.raises(K.KernelError):
        K.ready([row("A")], {"A": ["NOPE"]})


def test_shipped_dependencies_resolve_against_the_real_ledger():
    K.ready(K.load_rows(), K.load_deps(), K.load_config()["order"])


def test_surface_strips_lines_and_keeps_only_code_paths():
    files = ["friday/governor.py:84,343", "tests/t.py", "root.py:2", "ui/a.js:1-2",
             "scripts/prd_ledger.py", "scripts/prd_v7_table.py:3", "data/x.json", "docs/y.md:4"]
    assert K.surface(row("A", files=files)) == {"friday/governor.py", "tests/t.py",
                                                "root.py", "ui/a.js"}
    assert K.surface(row("B", files=["data/x.json", "docs/y.md"])) == frozenset()


def test_ready_uses_owner_order_after_priority():
    rows = [row("A"), row("B"), row("C"), row("P1", "P1")]
    assert [r["id"] for r in K.ready(rows, {}, ["C", "B"])] == ["C", "B", "A", "P1"]


def test_owner_rows_excluded_unless_asked():
    rows = [row("A"), dict(row("W"), next_action="OWNER: decide")]
    assert [r["id"] for r in K.ready(rows, {})] == ["A"]
    assert [r["id"] for r in K.ready(rows, {}, include_owner=True)] == ["A", "W"]


def test_config_has_order_and_depends():
    cfg = K.load_config()
    assert cfg["order"][0] == "BROW-001" and cfg["depends"]["MODEL-010"] == ["MODEL-009"]


def test_waves_disjoint_unknown_alone_and_cap():
    rows = [row("A", files=["a.py"]), row("B", files=["a.py"]), row("C", files=["c.py"]),
            row("U", files=["data/u.json"]), row("D", files=["d.py"])]
    got = [[r["id"] for r in w] for w in K.waves(rows, cap=2)]
    assert got == [["A", "C"], ["B", "D"], ["U"]]
    assert [[r["id"] for r in w] for w in K.waves(rows, cap=1)][0] == ["A"]


def test_unknown_surface_leading_row_runs_alone():
    got = [[r["id"] for r in w] for w in K.waves([row("U", files=[]), row("A")], cap=3)]
    assert got == [["U"], ["A"]]


@pytest.mark.parametrize("level,n", [("NORMAL", 2), ("ELEVATED", 1), ("HIGH", 0), ("CRITICAL", 0)])
def test_concurrency_follows_governor_level(level, n):
    assert K.concurrency(level) == n
    assert K.concurrency(level, cap=3) == (3 if level == "NORMAL" else n)


def test_launch_dry_run_has_no_side_effects(tmp_path):
    wt_root = tmp_path / "wts"
    plan = K.launch(row("REQ-1"), worktrees=wt_root, dry_run=True)
    assert not wt_root.exists()
    assert Path(plan["packet"]) == wt_root / "req-1" / ".build" / "packet.md"
    cmd = plan["command"]
    assert "stream-json" in cmd and "acceptEdits" in cmd
    assert "bypassPermissions" not in cmd
    assert cmd[cmd.index("--model") + 1] == "sonnet"


def test_launch_refuses_existing_worktree(tmp_path):
    (tmp_path / "req-1").mkdir()
    with pytest.raises(K.KernelError):
        K.launch(row("REQ-1"), worktrees=tmp_path)


def test_packet_carries_rules_and_evidence_contract():
    text = K.packet_text(row("REQ-1"))
    assert "Never commit" in text and "evidence.json" in text and "acc" in text


def _fake_run(tmp_path, code, name="R-1"):
    wt = tmp_path / name
    (wt / ".build").mkdir(parents=True)
    proc = subprocess.Popen([sys.executable, "-c", code], cwd=wt)
    return K.Running(name, proc, wt)


def test_watch_records_done_from_result_event_and_evidence(tmp_path):
    result = {"type": "result", "num_turns": 7, "total_cost_usd": 0.5, "is_error": False,
              "usage": {"input_tokens": 1, "cache_read_input_tokens": 2,
                        "cache_creation_input_tokens": 3, "output_tokens": 4}}
    ev = {"requirement": "R-1", "status": "DONE", "summary": "ok"}
    code = ("import json;open('.build/stream.jsonl','w').write(json.dumps(%r)+'\\n');"
            "open('.build/evidence.json','w').write(json.dumps(%r))" % (result, ev))
    run = _fake_run(tmp_path, code)
    out = K.watch([run], poll_s=0.05, runs_dir=tmp_path / "runs")
    rec = json.loads((tmp_path / "runs" / "R-1.json").read_text())
    assert out[0]["state"] == rec["state"] == "DONE"
    assert (rec["turns"], rec["input_tokens"], rec["cache_read_tokens"],
            rec["cache_creation_tokens"], rec["output_tokens"], rec["cost_usd"]) == (7, 1, 2, 3, 4, 0.5)
    assert any("R-1" in line and "DONE" in line for line in K.status_table(tmp_path / "runs"))


def test_watch_marks_failed_without_result(tmp_path):
    run = _fake_run(tmp_path, "pass")
    assert K.watch([run], poll_s=0.05, runs_dir=tmp_path / "runs")[0]["state"] == "FAILED"


def test_watch_kills_on_wall_timeout(tmp_path):
    run = _fake_run(tmp_path, "import time;time.sleep(60)")
    t0 = time.time()
    rec = K.watch([run], wall_s=0.3, poll_s=0.05, runs_dir=tmp_path / "runs")[0]
    assert rec["state"] == "TIMEOUT" and time.time() - t0 < 30 and run.proc.poll() is not None


def test_watch_kills_on_stall(tmp_path):
    run = _fake_run(tmp_path, "import time;time.sleep(60)")
    rec = K.watch([run], stall_s=0.3, poll_s=0.05, runs_dir=tmp_path / "runs")[0]
    assert rec["state"] == "STALLED"


def test_allowed_tools_are_narrow_and_bash_is_always_patterned():
    cmd = K.launch(row("REQ-1"), worktrees=Path("wts"), dry_run=True)["command"]
    tools = cmd[cmd.index("--allowedTools") + 1].split(",")
    assert tools == ["Read", "Edit", "Write", "Glob", "Grep", "Bash(git status:*)",
                     "Bash(git diff:*)", "Bash(git log:*)", f"Bash({K.VENV_PY}:*)"]
    assert "Bash" not in tools


def test_worker_env_points_state_inside_the_worktree(tmp_path):
    env = K.worker_env(tmp_path / "wt", {"KEEP": "1"})
    home = tmp_path / "wt" / ".build" / "home"
    assert home.is_dir() and env["KEEP"] == "1"
    assert Path(env["FRIDAY_HOME"]) == home
    assert Path(env["ADA_DB"]) == home / "ada.sqlite3"
    assert Path(env["ADA_AUDIT_DB"]) == home / "audit.sqlite3"


def test_a_worker_starts_without_mcp_servers_or_hooks():
    cmd = K.worker_command("sonnet", 5, 1.0)
    assert "--strict-mcp-config" in cmd
    assert cmd[cmd.index("--settings") + 1] == '{"disableAllHooks": true}'
    assert "bypassPermissions" not in " ".join(cmd)
