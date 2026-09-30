"""The build kernel's scheduling, launch plan and watcher (no real claude, no real worktrees)."""

from __future__ import annotations

import json
import os
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


# ------------------------------------------------ BH-A: worktrees, modes, budgets

def _git(cwd, *args):
    return subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t", *args], cwd=cwd,
                          capture_output=True, text=True, check=True).stdout.strip()


@pytest.fixture
def repo(tmp_path):
    r = tmp_path / "repo"
    r.mkdir()
    _git(r, "init", "-q")
    (r / "a.txt").write_text("a\n")
    _git(r, "add", "-A")
    _git(r, "commit", "-q", "-m", "base")
    return r


class _Proc:
    def __init__(self, code=0):
        self.returncode = code

    def poll(self):
        return self.returncode


def _stream(wt, *events):
    (wt / ".build").mkdir(parents=True, exist_ok=True)
    (wt / ".build" / "stream.jsonl").write_text(
        "".join(json.dumps(e) + "\n" for e in events), encoding="utf-8")


def test_create_worktree_makes_a_checked_branch_at_the_base_sha(repo, tmp_path):
    wt = tmp_path / "wts" / "req-1"
    base = K.create_worktree(wt, "req-1", repo=repo)
    assert base == _git(repo, "rev-parse", "HEAD") == _git(wt, "rev-parse", "HEAD")
    assert _git(wt, "rev-parse", "--abbrev-ref", "HEAD") == "build/req-1"
    assert not (tmp_path / "wts" / K.LOCK_NAME).exists()


def test_scout_worktree_is_detached(repo, tmp_path):
    wt = tmp_path / "wts" / "req-1"
    K.create_worktree(wt, "req-1", repo=repo, detach=True)
    assert _git(wt, "rev-parse", "--abbrev-ref", "HEAD") == "HEAD"
    assert _git(repo, "branch", "--list", "build/req-1") == ""


@pytest.mark.parametrize("prefix,answer", [
    (["rev-parse", "HEAD"], "0" * 40),
    (["rev-parse", "--abbrev-ref"], "some-other-branch"),
    (["rev-parse", "--show-toplevel"], "ELSEWHERE"),
    (["rev-parse", "--git-common-dir"], "ELSEWHERE"),
])
def test_each_worktree_assertion_rejects_and_cleans_up(monkeypatch, repo, tmp_path, prefix, answer):
    wt = tmp_path / "wts" / "req-1"
    real = K._git
    bad = str(tmp_path / "elsewhere") if answer == "ELSEWHERE" else answer

    def lying(args, cwd, timeout=120):
        if Path(cwd) == wt and args[:len(prefix)] == prefix:
            return bad
        return real(args, cwd, timeout)

    monkeypatch.setattr(K, "_git", lying)
    with pytest.raises(K.KernelError):
        K.create_worktree(wt, "req-1", repo=repo)
    assert not wt.exists()
    assert real(["branch", "--list", "build/req-1"], repo) == ""
    assert not (wt.parent / K.LOCK_NAME).exists()


def test_creation_lock_times_out_and_replaces_a_stale_lock(tmp_path):
    lock = tmp_path / K.LOCK_NAME
    lock.write_text("")
    with pytest.raises(K.KernelError):
        with K.creation_lock(tmp_path, wait_s=0.3, poll_s=0.05):
            pass
    old = time.time() - K.LOCK_STALE_S - 5
    os.utime(lock, (old, old))
    with K.creation_lock(tmp_path, wait_s=0.3, poll_s=0.05):
        assert lock.exists()
    assert not lock.exists()


def test_creation_lock_serialises_holders(tmp_path):
    with K.creation_lock(tmp_path):
        with pytest.raises(K.KernelError):
            with K.creation_lock(tmp_path, wait_s=0.2, poll_s=0.05):
                pass


def test_mode_for_scouts_vague_rows_until_findings_exist(tmp_path):
    good = dict(row("R"), tests=["tests/t.py"])
    assert K.mode_for(good, tmp_path) == "SHIP"
    assert K.mode_for(dict(good, files=["docs/x.md"]), tmp_path) == "SCOUT"
    assert K.mode_for(dict(good, tests=[]), tmp_path) == "SCOUT"
    assert K.mode_for(dict(good, acceptance="  "), tmp_path) == "SCOUT"
    vague = dict(good, tests=[])
    (tmp_path / "R.scout.json").write_text(json.dumps({"gap": "g"}))
    assert K.mode_for(vague, tmp_path) == "SHIP"


def test_budget_table_and_class_choice():
    assert K.BUDGETS == {"SCOUT": (8, 0.75, 900), "MICRO": (12, 1.0, 1200),
                         "NORMAL": (25, 3.0, 2700), "COMPLEX": (40, 6.0, 4500),
                         "P0": (40, 6.0, 4500)}
    many = ["friday/a.py", "friday/b.py", "friday/c.py", "friday/d.py"]
    assert K.budget_class(row("R", "P0")) == "P0"
    assert K.budget_class(row("R", "P1", files=many)) == "COMPLEX"
    assert K.budget_class(row("R", "P1", files=["friday/a.py"])) == "MICRO"
    assert K.budget_class(row("R", "P1", files=many[:2])) == "NORMAL"
    assert K.budget_class(row("R", "P1", files=many[:2]), {"budget_class": "COMPLEX"}) == "COMPLEX"


def test_packet_states_read_budget_schema_findings_and_code_map():
    text = K.packet_text(row("R", "P1"), {"gap": "the gap", "risk": "LOW"}, "MICRO", "MAP-LINE")
    assert "at most 6 files" in text and "400 lines" in text
    assert '"acceptance_met"' in text and '"gaps"' in text and "Never commit" in text
    assert "the gap" in text and "## Code map\nMAP-LINE" in text
    assert "## Code map" not in K.packet_text(row("R"))
    assert "at most 12 files" in K.packet_text(row("R"), klass="P0")


def test_scout_launch_plan_is_read_only_and_detached(tmp_path):
    plan = K.launch(row("R", "P1"), worktrees=tmp_path, dry_run=True, mode="SCOUT")
    tools = plan["command"][plan["command"].index("--allowedTools") + 1].split(",")
    assert plan["mode"] == "SCOUT" and plan["class"] == "SCOUT" and plan["branch"] == ""
    assert not {"Edit", "Write", f"Bash({K.VENV_PY}:*)"} & set(tools)
    assert "Write(.build/findings.json)" in tools
    # acceptEdits would auto-approve Edit/Write anywhere in the worktree, allowlist or not.
    assert plan["command"][plan["command"].index("--permission-mode") + 1] == "default"
    ship = K.launch(row("S", "P1"), worktrees=tmp_path, dry_run=True)["command"]
    assert ship[ship.index("--permission-mode") + 1] == "acceptEdits"
    assert plan["command"][plan["command"].index("--max-turns") + 1] == "8"
    assert plan["command"][plan["command"].index("--max-budget-usd") + 1] == "0.75"


def test_launch_ship_uses_class_budget_and_records_base(monkeypatch, repo, tmp_path):
    started = []
    monkeypatch.setattr(K, "_start", lambda cmd, wt: started.append(cmd) or _Proc(None))
    run = K.launch(row("R-1", "P1"), worktrees=tmp_path / "wts", repo=repo, runs_dir=tmp_path / "r")
    assert run.klass == "MICRO" and run.branch == "build/r-1"
    assert run.base_sha == _git(repo, "rev-parse", "HEAD") and run.wall_s == 1200
    assert started[0][started[0].index("--max-turns") + 1] == "12"
    assert "at most 6 files" in (run.wt / ".build" / "packet.md").read_text()


def test_launch_scout_runs_detached_with_a_scout_packet(monkeypatch, repo, tmp_path):
    monkeypatch.setattr(K, "_start", lambda cmd, wt: _Proc(None))
    run = K.launch(row("R-1"), worktrees=tmp_path / "wts", repo=repo, mode="SCOUT")
    assert _git(run.wt, "rev-parse", "--abbrev-ref", "HEAD") == "HEAD" and run.wall_s == 900
    assert "findings.json" in (run.wt / ".build" / "packet.md").read_text()


def test_read_metrics_counts_reads_bytes_and_reads_before_first_edit():
    def use(i, name):
        return {"type": "assistant", "message": {"content": [
            {"type": "tool_use", "id": i, "name": name, "input": {}}]}}

    def res(i, content):
        return {"type": "user", "message": {"content": [
            {"type": "tool_result", "tool_use_id": i, "content": content}]}}

    m = K.read_metrics([use("1", "Read"), res("1", "abcd"), use("2", "Read"),
                        res("2", [{"type": "text", "text": "xyz"}]), use("3", "Edit"),
                        res("3", "ignored"), use("4", "Read"), {"type": "result"}, {"x": 1}])
    assert m == {"reads": 3, "read_bytes": 7, "reads_before_first_edit": 2}
    assert K.read_metrics([]) == {"reads": 0, "read_bytes": 0, "reads_before_first_edit": 0}


def _done_run(repo, tmp_path, mode="SHIP"):
    wt = tmp_path / "wts" / "r-1"
    base = K.create_worktree(wt, "r-1", repo=repo, detach=mode == "SCOUT")
    _stream(wt, {"type": "system", "session_id": "S1"},
            {"type": "result", "subtype": "success", "session_id": "S1", "num_turns": 3,
             "total_cost_usd": 0.2, "usage": {}})
    return K.Running("R-1", _Proc(0), wt, mode=mode, base_sha=base,
                     branch="" if mode == "SCOUT" else "build/r-1", repo=repo), wt


def test_finalize_commits_a_done_diff_on_the_build_branch(repo, tmp_path):
    run, wt = _done_run(repo, tmp_path)
    (wt / "new.py").write_text("x = 1\n")
    (wt / ".build" / "evidence.json").write_text(json.dumps(
        {"status": "DONE", "summary": "y" * 90, "acceptance_met": True, "gaps": []}))
    rec = K.finalize(run, None, tmp_path / "runs")
    assert rec["state"] == "DONE" and rec["commit"] == _git(wt, "rev-parse", "HEAD")
    assert rec["files_changed"] == ["new.py"] and rec["branch"] == "build/r-1"
    assert _git(wt, "log", "-1", "--format=%s") == "build(R-1): " + "y" * 60
    assert _git(wt, "log", "-1", "--format=%an") == "DarshPro07"
    assert ".build" not in _git(wt, "show", "--name-only", "--format=", "HEAD")
    assert rec["evidence"]["acceptance_met"] is True and rec["continued"] is False
    assert rec["session_id"] == "S1" and rec["reads"]["reads"] == 0 and rec["mode"] == "SHIP"
    assert (tmp_path / "runs" / "R-1.stream.jsonl").exists()


def test_finalize_does_not_commit_failed_or_unchanged_runs(repo, tmp_path):
    run, wt = _done_run(repo, tmp_path)
    (wt / "new.py").write_text("x = 1\n")
    assert K.finalize(run, None, tmp_path / "runs")["commit"] == ""  # no evidence: FAILED
    (wt / ".build" / "evidence.json").write_text(json.dumps({"status": "DONE", "summary": "s"}))
    (wt / "new.py").unlink()
    assert K.finalize(run, None, tmp_path / "runs")["commit"] == ""  # DONE, nothing changed


def test_finalize_scout_keeps_findings_and_removes_the_worktree(repo, tmp_path):
    run, wt = _done_run(repo, tmp_path, mode="SCOUT")
    found = {"requirement": "R-1", "gap": "g", "files": [], "tests": [],
             "acceptance_plan": "p", "risk": "LOW", "budget_class": "MICRO"}
    (wt / ".build" / "findings.json").write_text(json.dumps(found))
    rec = K.finalize(run, None, tmp_path / "runs")
    assert rec["state"] == "DONE" and rec["mode"] == "SCOUT" and rec["commit"] == ""
    assert json.loads((tmp_path / "runs" / "R-1.scout.json").read_text()) == found
    assert (tmp_path / "runs" / "R-1.scout.stream.jsonl").exists() and not wt.exists()
    assert len(K.status_table(tmp_path / "runs")) == 2  # header + the one run record


def test_finalize_scout_without_valid_findings_fails(repo, tmp_path):
    run, wt = _done_run(repo, tmp_path, mode="SCOUT")
    (wt / ".build" / "findings.json").write_text(json.dumps({"gap": "g", "risk": "EXTREME"}))
    assert K.finalize(run, None, tmp_path / "runs")["state"] == "FAILED"
    assert not (tmp_path / "runs" / "R-1.scout.json").exists()


def _maxed_run(repo, tmp_path):
    run, wt = _done_run(repo, tmp_path)
    _stream(wt, {"type": "result", "subtype": "error_max_turns", "session_id": "S1",
                 "num_turns": 25, "total_cost_usd": 1.0, "usage": {"output_tokens": 5}})
    return run, wt


def test_continuation_resumes_once_at_half_limits(monkeypatch, repo, tmp_path):
    run, wt = _maxed_run(repo, tmp_path)
    (wt / "partial.py").write_text("x\n")
    cmds = []
    monkeypatch.setattr(K, "_start", lambda cmd, w: cmds.append(cmd) or _Proc(None))
    assert K.continuation(run) is True and run.continued is True
    cmd = cmds[0]
    assert cmd[cmd.index("--resume") + 1] == "S1"
    assert cmd[cmd.index("--max-turns") + 1] == "12" and cmd[cmd.index("--max-budget-usd") + 1] == "1.5"
    assert K.CONTINUE_PROMPT in cmd
    assert K.continuation(run) is False and len(cmds) == 1  # never twice


@pytest.mark.parametrize("why", ["no_diff", "done", "blocked", "not_max", "no_session"])
def test_continuation_declines_when_not_warranted(monkeypatch, repo, tmp_path, why):
    run, wt = _maxed_run(repo, tmp_path)
    if why != "no_diff":
        (wt / "partial.py").write_text("x\n")
    if why in ("done", "blocked"):
        (wt / ".build" / "evidence.json").write_text(json.dumps({"status": why.upper()}))
    if why == "not_max":
        _stream(wt, {"type": "result", "subtype": "success", "session_id": "S1"})
    if why == "no_session":
        _stream(wt, {"type": "result", "subtype": "error_max_turns"})
    monkeypatch.setattr(K, "_start", lambda cmd, w: pytest.fail("must not relaunch"))
    assert K.continuation(run) is False


def test_finalize_sums_the_initial_and_continued_results(repo, tmp_path):
    run, wt = _maxed_run(repo, tmp_path)
    _stream(wt, {"type": "result", "subtype": "error_max_turns", "session_id": "S1",
                 "num_turns": 25, "total_cost_usd": 1.0, "usage": {"output_tokens": 5}},
            {"type": "result", "subtype": "success", "session_id": "S1", "num_turns": 10,
             "total_cost_usd": 0.5, "usage": {"output_tokens": 2}})
    rec = K.finalize(run, None, tmp_path / "runs")
    assert (rec["turns"], rec["cost_usd"], rec["output_tokens"]) == (35, 1.5, 7)


def test_watch_uses_each_runs_own_wall_limit(tmp_path):
    run = _fake_run(tmp_path, "import time;time.sleep(60)")
    run.wall_s = 0.3
    rec = K.watch([run], poll_s=0.05, runs_dir=tmp_path / "runs")[0]
    assert rec["state"] == "TIMEOUT"


def _cli_launch(monkeypatch, r, *argv):
    seen = {}
    monkeypatch.setattr(K, "load_rows", lambda: [r])
    monkeypatch.setattr(K, "load_config", lambda *a: {"depends": {}, "order": []})
    monkeypatch.setattr(K, "launch", lambda row, **kw: seen.update(kw) or {})
    assert K.main(["launch", r["id"], "--dry-run", *argv]) == 0
    return seen


def test_cli_ship_packet_carries_the_code_map_of_its_surface(monkeypatch):
    r = row("R-9", "P1", files=("scripts/test_impact.py:79-100",))
    r["tests"] = ["tests/test_test_impact.py"]
    seen = _cli_launch(monkeypatch, r)
    assert seen["mode"] == "SHIP"
    assert "## scripts/test_impact.py" in seen["capsule"] and "def affected_tests" in seen["capsule"]


def test_cli_scout_gets_no_code_map(monkeypatch):
    seen = _cli_launch(monkeypatch, row("R-8", "P1", files=()))
    assert seen["mode"] == "SCOUT" and seen["capsule"] == ""
