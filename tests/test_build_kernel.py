"""The build kernel's scheduling, launch plan and watcher (no real claude, no real worktrees)."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import build_kernel as K  # noqa: E402
import prd_ledger as PL  # noqa: E402
from friday.governor import Thresholds  # noqa: E402


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


class FakeGov:
    """A governor stand-in: a fixed level and memory reading, real thresholds."""

    def __init__(self, level="NORMAL", available=8.0, total=16.0, thresholds=None):
        self.thresholds = thresholds or Thresholds()
        sample = SimpleNamespace(ram_available_gb=available, total_gb=lambda: total)
        self._pressure = SimpleNamespace(level=level, sample=sample)

    def pressure(self):
        return self._pressure


def _peak_record(runs_dir, name, peak, mtime):
    runs_dir.mkdir(parents=True, exist_ok=True)
    p = runs_dir / f"{name}.json"
    p.write_text(json.dumps({"requirement": name, "peak_rss_mb": peak}), encoding="utf-8")
    os.utime(p, (mtime, mtime))


def test_concurrency_is_zero_at_critical_however_much_ram_is_free(tmp_path):
    n, why = K.concurrency(3, gov=FakeGov("CRITICAL", available=64.0), runs_dir=tmp_path)
    assert n == 0 and "CRITICAL" in why


def test_concurrency_is_one_at_high_whatever_the_cap(tmp_path):
    assert K.concurrency(3, gov=FakeGov("HIGH", available=64.0), runs_dir=tmp_path)[0] == 1


def test_high_pressure_never_admits_a_worker_the_ram_cannot_hold(tmp_path):
    # 2026-10-02: HIGH returned 1 before the RAM slots were counted, so a
    # pressured host with 2.5 GB free was offered a worker that NORMAL refused.
    n, why = K.concurrency(3, gov=FakeGov("HIGH", available=2.5), runs_dir=tmp_path)
    assert n == 0 and "HIGH" in why


@pytest.mark.parametrize("available,n", [(2.9, 0), (4.5, 1)])
def test_high_pressure_is_a_ceiling_not_a_bypass(tmp_path, available, n):
    # Owner's negative control for bf39d3d: HIGH caps concurrency at one, and
    # every level still has to fit the worker in the RAM above the reserve.
    got, why = K.concurrency(3, gov=FakeGov("HIGH", available=available), runs_dir=tmp_path)
    assert got == n and "HIGH" in why


@pytest.mark.parametrize("available,n", [(8.0, 2), (4.5, 2), (3.1, 1), (3.0, 1), (2.9, 0), (1.0, 0)])
def test_concurrency_is_limited_by_the_ram_above_the_reserve(tmp_path, available, n):
    # reserve = ram_floor(16 GB) 0.5 + 1.0; one default worker = 1.5 GB
    got, why = K.concurrency(3, gov=FakeGov(available=available), runs_dir=tmp_path)
    assert got == n and "slots" in why


def test_concurrency_is_limited_by_the_governors_max_workers_and_the_cap(tmp_path):
    assert K.concurrency(1, gov=FakeGov(available=64.0), runs_dir=tmp_path)[0] == 1
    assert K.concurrency(9, gov=FakeGov(available=64.0), runs_dir=tmp_path)[0] == 2
    wide = FakeGov(available=64.0, thresholds=Thresholds(max_workers=5))
    assert K.concurrency(9, gov=wide, runs_dir=tmp_path)[0] == 5


def test_worker_size_is_the_largest_peak_of_the_last_ten_measured_records(tmp_path):
    assert K.worker_gb(tmp_path) == K.DEFAULT_WORKER_GB
    _peak_record(tmp_path, "OLD", 20480, 1)  # the 11th newest: out of the window
    for i in range(10):
        _peak_record(tmp_path, f"R{i}", 1024 + i * 512, 100 + i)
    (tmp_path / "NOPEAK.json").write_text(json.dumps({"requirement": "NOPEAK", "peak_rss_mb": None}))
    os.utime(tmp_path / "NOPEAK.json", (500, 500))
    assert K.worker_gb(tmp_path) == (1024 + 9 * 512) / 1024
    got, _ = K.concurrency(3, gov=FakeGov(available=4.4), runs_dir=tmp_path)
    assert got == 0  # 4.4 - 1.5 reserve = 2.9 GB < one 5.5 GB worker


@pytest.fixture(autouse=True)
def _no_pending_attempts():
    K._PENDING_ATTEMPTS.clear()
    yield
    K._PENDING_ATTEMPTS.clear()


def _peaks(runs_dir, *items):
    runs_dir.mkdir(parents=True, exist_ok=True)
    (runs_dir / K.PEAKS_FILE).write_text(
        "".join(json.dumps({"req": r, "mode": "SHIP", "budget_class": c, "peak_rss_mb": mb, "at": "t"}) + "\n"
                for r, c, mb in items), encoding="utf-8")


def _heavy(runs_dir, req="CORE-001"):
    _peaks(runs_dir, (req, "COMPLEX", 1741))  # 1.7 GB


def test_predict_prefers_the_requirements_own_last_peak(tmp_path):
    _peaks(tmp_path, ("X", "NORMAL", 3000), ("A", "NORMAL", 400), ("X", "NORMAL", 800))
    assert K.predict_gb("X", "NORMAL", "SHIP", tmp_path) == (800 / 1024, "requirement")


def test_predict_uses_the_class_p95_from_three_samples(tmp_path):
    _peaks(tmp_path, ("A", "NORMAL", 400), ("B", "NORMAL", 440), ("C", "NORMAL", 460), ("D", "COMPLEX", 1700))
    gb, source = K.predict_gb("Z", "NORMAL", "SHIP", tmp_path)
    assert source == "class" and gb == 460 / 1024  # the heavy class does not tax a light one


def test_predict_falls_back_to_all_peaks_then_the_default(tmp_path):
    _peaks(tmp_path, ("A", "NORMAL", 400), ("B", "COMPLEX", 1700))
    assert K.predict_gb("Z", "MICRO", "SHIP", tmp_path) == (1700 / 1024, "history")
    assert K.predict_gb("Z", "MICRO", "SHIP", tmp_path / "none") == (K.DEFAULT_WORKER_GB, "default")


def test_admit_charges_each_candidate_against_what_was_admitted_before_it(tmp_path):
    gov = FakeGov(available=5.0, thresholds=Thresholds(max_workers=5))  # 3.5 GB above the 1.5 GB reserve
    _peaks(tmp_path, ("A", "NORMAL", 1741), ("B", "NORMAL", 1741), ("C", "NORMAL", 1741))
    cands = [({"id": r}, "SHIP", "NORMAL") for r in "ABC"]
    got, why = K.admit(cands, 3, gov, tmp_path)
    assert [r["id"] for r, _ in got] == ["A", "B"] and "C needs 1.7 GB" in why


@pytest.mark.parametrize("available,n", [(2.9, 0), (4.5, 1)])
def test_admit_high_pressure_negative_controls(tmp_path, available, n):
    _heavy(tmp_path)
    got, _ = K.admit([({"id": "CORE-001"}, "SHIP", "COMPLEX")] * 3, 3, FakeGov("HIGH", available=available),
                     tmp_path)
    assert len(got) == n  # HIGH caps at one even with room for more, and never past the RAM


def test_admit_critical_admits_nothing(tmp_path):
    assert K.admit([({"id": "A"}, "SHIP", "NORMAL")], 3, FakeGov("CRITICAL", available=64.0), tmp_path)[0] == []


def test_a_light_job_admits_while_a_heavy_one_waits_at_2_6_gb(tmp_path):
    _peaks(tmp_path, ("LIGHT", "MICRO", 512), ("CORE-001", "COMPLEX", 1741))
    gov = FakeGov(available=2.6)  # 1.1 GB above the reserve
    assert len(K.admit([({"id": "LIGHT"}, "SHIP", "MICRO")], 3, gov, tmp_path)[0]) == 1
    got, why = K.admit([({"id": "CORE-001"}, "SHIP", "COMPLEX")], 3, gov, tmp_path)
    assert got == [] and "needs 1.7 GB" in why


def test_admit_also_needs_commit_headroom(tmp_path):
    gov = FakeGov(available=8.0)
    gov._pressure.sample.commit_available_gb = 2.0  # physical RAM is free, the commit charge is not
    assert K.admit([({"id": "A"}, "SHIP", "NORMAL")], 3, gov, tmp_path)[0] == []
    gov._pressure.sample.commit_available_gb = 4.0
    assert len(K.admit([({"id": "A"}, "SHIP", "NORMAL")], 3, gov, tmp_path)[0]) == 1


def test_two_kernels_seeing_the_same_headroom_cannot_both_spend_it(tmp_path):
    _heavy(tmp_path, "A")
    _peaks(tmp_path, ("A", "COMPLEX", 1741), ("B", "COMPLEX", 1741))
    first = K.reserve("att-1", "A", "COMPLEX", cap=3, gov=FakeGov(available=3.5), runs_dir=tmp_path)
    second = K.reserve("att-2", "B", "COMPLEX", cap=3, gov=FakeGov(available=3.5), runs_dir=tmp_path)
    assert first[0] and not second[0]
    assert [x["attempt_id"] for x in K._leases_load(tmp_path)] == ["att-1"]


def test_the_recheck_refuses_when_the_fresh_sample_no_longer_fits(tmp_path, monkeypatch):
    _heavy(tmp_path, "A")
    _peaks(tmp_path, ("A", "COMPLEX", 1741))
    gov = FakeGov(available=5.0)
    calls = iter([5.0, 2.0])  # the check sees 5 GB; the sample taken under the lease sees 2 GB

    def pressure():
        gov._pressure.sample.ram_available_gb = next(calls)
        return gov._pressure
    gov.pressure = pressure
    ok, why = K.reserve("att-1", "A", "COMPLEX", cap=3, gov=gov, runs_dir=tmp_path)
    assert not ok and why.startswith("recheck") and K._leases_load(tmp_path) == []
    assert "A" not in K._PENDING_ATTEMPTS


def test_a_lease_counts_only_what_its_process_has_not_yet_consumed(tmp_path, monkeypatch):
    lease = {"attempt_id": "x", "req": "A", "gb": 1.7, "pid": 77, "at": time.time(), "wall_s": 600}
    K._leases_store([lease], tmp_path)
    monkeypatch.setattr(K, "_pid_gone", lambda pid: False)
    monkeypatch.setattr(K, "tree_rss_mb", lambda pid: 1024.0)
    assert round(K.leased_gb(tmp_path), 3) == 0.7
    monkeypatch.setattr(K, "tree_rss_mb", lambda pid: 4096.0)
    assert K.leased_gb(tmp_path) == 0.0


def test_a_dead_pid_or_an_expired_lease_is_stale(tmp_path, monkeypatch):
    now = time.time()
    K._leases_store([{"attempt_id": "dead", "gb": 1.0, "pid": 5, "at": now, "wall_s": 600},
                     {"attempt_id": "old", "gb": 1.0, "pid": 0, "at": now - 700, "wall_s": 600},
                     {"attempt_id": "live", "gb": 1.0, "pid": 0, "at": now, "wall_s": 600}], tmp_path)
    monkeypatch.setattr(K, "_pid_gone", lambda pid: pid == 5)
    assert K.leased_gb(tmp_path) == 1.0


def test_closing_a_job_releases_its_lease_and_job_states_are_written(tmp_path):
    aid = K.job_queue("A", "NORMAL", "SHIP", tmp_path)
    assert K._read_json(K.job_path(aid, tmp_path))["state"] == "QUEUED"
    K.job_wait(aid, "no room", tmp_path)
    job = K._read_json(K.job_path(aid, tmp_path))
    assert job["state"] == "WAITING_RESOURCES" and job["reason"] == "no room"
    K._leases_store([{"attempt_id": aid, "gb": 1.0, "pid": 0, "at": time.time(), "wall_s": 600}], tmp_path)
    K.job_close(aid, "FAILED", "x", tmp_path)
    assert K._leases_load(tmp_path) == []


def test_finalize_records_the_prediction_and_its_error(tmp_path):
    wt = tmp_path / "wt"
    (wt / ".build").mkdir(parents=True)
    runs = tmp_path / "runs"
    _peaks(runs, ("A", "NORMAL", 1024))
    run = K.Running("A", SimpleNamespace(returncode=1, pid=0), wt, peak_rss_mb=1536.0)
    rec = K.finalize(run, "FAILED", runs)
    assert rec["predicted_gb"] == 1.0 and rec["error_gb"] == 0.5
    entry = K.ledger_entries(runs)[-1]
    assert entry["predicted_gb"] == 1.0 and entry["error_gb"] == 0.5


def test_host_governor_uses_a_matching_profile_and_ignores_a_moved_one(monkeypatch, tmp_path):
    from friday import hardware_profile as HP
    monkeypatch.setattr(HP, "stored", lambda path=None: SimpleNamespace(baseline=None))
    monkeypatch.setattr(HP, "_caps", lambda profile: Thresholds(max_workers=7))
    monkeypatch.setattr(HP, "changes", lambda profile, machine=None: [])
    assert K.host_governor(tmp_path / "p.json").thresholds.max_workers == 7
    monkeypatch.setattr(HP, "changes", lambda profile, machine=None: ["RAM total 16 -> 8 GB"])
    assert K.host_governor(tmp_path / "p.json").thresholds.max_workers == Thresholds().max_workers
    monkeypatch.setattr(HP, "stored", lambda path=None: None)
    assert K.host_governor(tmp_path / "p.json").thresholds.max_workers == Thresholds().max_workers


def test_qualify_cli_stores_the_build_host_profile_and_never_the_live_one(monkeypatch, tmp_path):
    from friday import hardware_profile as HP
    live = tmp_path / "live" / "hardware_profile.json"
    monkeypatch.setattr(HP, "PROFILE_PATH", live)
    seen = {}

    def fake_qualify(**kw):
        seen["qualify"] = kw
        if kw.get("save", True):
            HP.store(SimpleNamespace(to_dict=lambda: {}))  # what an unguarded call does
        return SimpleNamespace(to_dict=lambda: {"ok": 1})

    monkeypatch.setattr(HP, "qualify", fake_qualify)
    monkeypatch.setattr(HP, "store", lambda profile, path=None: seen.update(path=path) or Path(path))
    assert K.main(["qualify"]) == 0
    assert seen["qualify"] == {"cause": "build-host", "save": False}
    assert seen["path"] == K.HOST_PROFILE and not live.exists()


def test_concurrency_cli_prints_the_count_and_the_reason(monkeypatch, capsys):
    monkeypatch.setattr(K, "concurrency", lambda **kw: (2, "plenty of RAM"))
    assert K.main(["concurrency"]) == 0
    assert capsys.readouterr().out.strip() == "2 (plenty of RAM)"


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
    pid = 4242

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
    # 2026-10-02: the rule Write(.build/findings.json) never matched the absolute
    # path the Write tool uses, so DOMAIN-001's scout was denied twice. A scout
    # writes nothing: its findings are its final message.
    assert not any(t.startswith(("Write", "Edit", "NotebookEdit")) for t in tools)
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
    run = K.launch(row("R-1"), worktrees=tmp_path / "wts", repo=repo, mode="SCOUT",
                   runs_dir=tmp_path / "runs")
    assert _git(run.wt, "rev-parse", "--abbrev-ref", "HEAD") == "HEAD" and run.wall_s == 900
    assert "final message" in (run.wt / ".build" / "packet.md").read_text()


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
    assert (m["reads"], m["read_bytes"], m["reads_before_first_edit"]) == (3, 7, 2)
    assert K.read_metrics([]) == {"reads": 0, "read_bytes": 0, "reads_before_first_edit": 0,
                                  "searches": 0, "tool_calls": 0, "tokens_before_first_edit": None}


def test_read_metrics_counts_searches_tool_calls_and_tokens_before_the_first_edit():
    def turn(name, usage=None):
        msg = {"content": [{"type": "tool_use", "id": name, "name": name, "input": {}}]}
        if usage:
            msg["usage"] = usage
        return {"type": "assistant", "message": msg}

    u1 = {"input_tokens": 10, "cache_read_input_tokens": 100, "cache_creation_input_tokens": 1000}
    u2 = {"input_tokens": 20, "cache_read_input_tokens": 200}
    big = {"input_tokens": 99999}
    m = K.read_metrics([turn("Grep", u1), turn("Glob"), turn("Read", u2), turn("Bash"),
                        turn("Edit", big), turn("Grep", big), turn("Write", big)])
    assert m["searches"] == 4 and m["tool_calls"] == 7
    assert m["tokens_before_first_edit"] == 1330  # the editing turn and later ones do not count
    assert K.read_metrics([turn("Read", u1)])["tokens_before_first_edit"] is None


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


def test_finalize_commits_when_the_repo_ignores_build(repo, tmp_path):
    # The real repository ignores .build/; naming an ignored path in `git add`
    # makes git exit non-zero, which failed BH-D's first real commit.
    (repo / ".gitignore").write_text(".build/\n")
    _git(repo, "add", ".gitignore")
    _git(repo, "commit", "-q", "-m", "ignore build")
    run, wt = _done_run(repo, tmp_path)
    (wt / "new.py").write_text("x = 1\n")
    (wt / ".build" / "evidence.json").write_text(json.dumps({"status": "DONE", "summary": "s"}))
    rec = K.finalize(run, None, tmp_path / "runs")
    assert rec["state"] == "DONE" and rec["files_changed"] == ["new.py"], rec.get("commit_error")


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


# ------------------------------------------------------- worker memory (RSS)

class _NoSuchProcess(Exception):
    pass


class _AccessDenied(Exception):
    pass


def _fake_psutil(tree: dict):
    """tree: pid -> (rss bytes | exception class, [child pids])."""
    def proc(pid):
        rss, kids = tree[pid]
        def memory_info():
            if isinstance(rss, type):
                raise rss()
            return SimpleNamespace(rss=rss)
        return SimpleNamespace(memory_info=memory_info, children=lambda recursive=False: [proc(k) for k in kids])

    def Process(pid):
        if pid not in tree:
            raise _NoSuchProcess()
        return proc(pid)

    return SimpleNamespace(Process=Process, NoSuchProcess=_NoSuchProcess, AccessDenied=_AccessDenied)


MB = 1024 * 1024


def test_tree_rss_sums_the_process_and_its_descendants_and_tolerates_vanishing_ones(monkeypatch):
    tree = {1: (100 * MB, [2, 3, 4]), 2: (50 * MB, []), 3: (_NoSuchProcess, []), 4: (_AccessDenied, [])}
    monkeypatch.setattr(K, "psutil", _fake_psutil(tree))
    assert K.tree_rss_mb(1) == 150.0
    assert K.tree_rss_mb(99) is None  # the worker already exited


def test_tree_rss_is_none_without_psutil(monkeypatch):
    monkeypatch.setattr(K, "psutil", None)
    assert K.tree_rss_mb(1) is None


def test_watch_records_the_peak_of_the_sampled_tree_rss(monkeypatch, tmp_path):
    readings = iter([100.0, 300.0, 200.0])
    monkeypatch.setattr(K, "tree_rss_mb", lambda pid: next(readings, 150.0))
    run = _fake_run(tmp_path, "import time;time.sleep(0.6)")
    rec = K.watch([run], poll_s=0.05, runs_dir=tmp_path / "runs")[0]
    assert rec["peak_rss_mb"] == 300.0


def test_a_run_without_psutil_records_a_null_peak_and_does_not_crash(monkeypatch, tmp_path):
    monkeypatch.setattr(K, "psutil", None)
    run = _fake_run(tmp_path, "pass")
    rec = K.watch([run], poll_s=0.05, runs_dir=tmp_path / "runs")[0]
    assert rec["peak_rss_mb"] is None


# --------------------------------------------------------- run.json, resume

def _launched(monkeypatch, repo, tmp_path, capsule=""):
    monkeypatch.setattr(K, "_start", lambda cmd, w: _Proc(None))
    r = row("R-1", "P1")
    run = K.launch(r, worktrees=tmp_path / "wts", runs_dir=tmp_path / "runs", repo=repo, capsule=capsule)
    return r, run


def test_launch_writes_run_json_and_flags_the_capsule(monkeypatch, repo, tmp_path):
    _, run = _launched(monkeypatch, repo, tmp_path, capsule="## map")
    meta = json.loads((run.wt / ".build" / "run.json").read_text())
    assert {k: meta[k] for k in ("req", "mode", "klass", "base_sha", "branch", "model")} == {
        "req": "R-1", "mode": "SHIP", "klass": run.klass, "base_sha": run.base_sha,
        "branch": "build/r-1", "model": "sonnet"}
    assert isinstance(meta["started"], float) and run.capsule is True
    assert K.finalize(run, "TIMEOUT", tmp_path / "runs")["capsule"] is True


def test_launch_without_a_capsule_records_the_off_arm(monkeypatch, repo, tmp_path):
    _, run = _launched(monkeypatch, repo, tmp_path)
    assert run.capsule is False
    assert K.finalize(run, "TIMEOUT", tmp_path / "runs")["capsule"] is False


def _killed(repo, tmp_path, *events, name="r-1"):
    wt = tmp_path / "wts" / name
    base = K.create_worktree(wt, name, repo=repo)
    _stream(wt, *events)
    return wt, base


INIT = {"type": "system", "subtype": "init", "session_id": "S9"}


def test_resumable_needs_an_init_session_and_no_result(repo, tmp_path):
    wt, _ = _killed(repo, tmp_path, INIT, {"type": "assistant", "message": {}})
    assert K.resumable(wt) == "S9"
    _stream(wt, INIT, {"type": "result", "subtype": "success", "session_id": "S9"})
    assert K.resumable(wt) is None
    _stream(wt, {"type": "assistant", "message": {}})
    assert K.resumable(wt) is None
    _stream(wt, {"type": "system", "subtype": "init"})
    assert K.resumable(wt) is None


def test_resumable_is_only_for_ship_runs(repo, tmp_path):
    wt, _ = _killed(repo, tmp_path, INIT)
    K._write_run_meta(wt, {"mode": "SCOUT"})
    assert K.resumable(wt) is None
    K._write_run_meta(wt, {"mode": "SHIP"})
    assert K.resumable(wt) == "S9"
    scout = tmp_path / "wts" / "s-1"
    K.create_worktree(scout, "s-1", repo=repo, detach=True)  # no run.json: detached means scout
    _stream(scout, INIT)
    assert K.resumable(scout) is None


def _resume_cmds(monkeypatch):
    cmds = []
    monkeypatch.setattr(K, "_start", lambda cmd, w: cmds.append(cmd) or _Proc(None))
    return cmds


def test_resume_relaunches_in_place_at_half_limits_from_run_json(monkeypatch, repo, tmp_path):
    wt, base = _killed(repo, tmp_path, INIT)
    K._write_run_meta(wt, {"req": "R-1", "mode": "SHIP", "klass": "COMPLEX", "base_sha": base,
                           "branch": "build/r-1", "model": "opus", "started": 1.0, "capsule": True})
    cmds = _resume_cmds(monkeypatch)
    run = K.resume("R-1", worktrees=tmp_path / "wts", runs_dir=tmp_path / "runs", repo=repo)
    cmd = cmds[0]
    assert cmd[cmd.index("--resume") + 1] == "S9" and K.CONTINUE_PROMPT in cmd
    assert cmd[cmd.index("--model") + 1] == "opus"
    assert cmd[cmd.index("--max-turns") + 1] == "20" and cmd[cmd.index("--max-budget-usd") + 1] == "3.0"
    assert (run.wt, run.klass, run.base_sha, run.branch, run.continued, run.capsule) == (
        wt, "COMPLEX", base, "build/r-1", True, True)
    assert run.started > 1.0


def test_resume_without_run_json_derives_the_base_and_class(monkeypatch, repo, tmp_path):
    wt, base = _killed(repo, tmp_path, INIT)
    (wt / "new.py").write_text("x\n")
    _git(wt, "add", "-A")
    _git(wt, "-c", "user.name=t", "-c", "user.email=t", "commit", "-q", "-m", "partial")
    cmds = _resume_cmds(monkeypatch)
    r = row("R-1", "P1")
    run = K.resume("R-1", worktrees=tmp_path / "wts", runs_dir=tmp_path / "runs", repo=repo, row=r)
    assert run.base_sha == base != _git(wt, "rev-parse", "HEAD")
    assert run.klass == K.budget_class(r) and run.branch == "build/r-1" and run.continued
    assert len(cmds) == 1


def test_resume_refuses_a_run_that_finished(monkeypatch, repo, tmp_path):
    _killed(repo, tmp_path, INIT, {"type": "result", "subtype": "success", "session_id": "S9"})
    monkeypatch.setattr(K, "_start", lambda cmd, w: pytest.fail("must not relaunch"))
    with pytest.raises(K.KernelError):
        K.resume("R-1", worktrees=tmp_path / "wts", runs_dir=tmp_path / "runs", repo=repo, row=row("R-1"))
    with pytest.raises(K.KernelError):
        K.resume("R-404", worktrees=tmp_path / "wts", runs_dir=tmp_path / "runs", repo=repo)


def test_resume_cli_watches_and_prints_the_state(monkeypatch, capsys):
    monkeypatch.setattr(K, "resume", lambda req: SimpleNamespace(req=req))
    monkeypatch.setattr(K, "watch", lambda runs: [{"requirement": r.req, "state": "DONE"} for r in runs])
    assert K.main(["resume", "R-1"]) == 0
    assert capsys.readouterr().out.strip() == "R-1: DONE"


# ---------------------------------------------------------------------- roi

def _rec(runs_dir, name, **kw):
    runs_dir.mkdir(parents=True, exist_ok=True)
    (runs_dir / name).write_text(json.dumps(kw), encoding="utf-8")


def test_roi_reads_run_records_only(tmp_path):
    run = {"requirement": "A", "state": "DONE", "mode": "SHIP", "budget_class": "NORMAL",
           "capsule": True, "cost_usd": 2.0}
    _rec(tmp_path, "A.json", **run)
    _rec(tmp_path, "A.integration.json", **{**run, "cost_usd": 100.0})
    _rec(tmp_path, "A.scout.json", **{**run, "cost_usd": 100.0})
    _rec(tmp_path, "host_profile.json", cost_usd=100.0, state="DONE")
    (tmp_path / "A.stream.jsonl").write_text(json.dumps(run) + "\n")
    g = K.roi_groups(tmp_path)
    assert g["capsule"]["on"]["runs"] == 1 and "off" not in g["capsule"]
    assert g["class"]["NORMAL"]["runs"] == 1 and g["class"]["NORMAL"]["cost"] == 2.0


def test_roi_groups_by_arm_and_class_with_agent_roi(tmp_path):
    full = {"mode": "SHIP", "budget_class": "NORMAL", "turns": 10, "wall_seconds": 60,
            "reads": {"reads": 4, "searches": 2, "tokens_before_first_edit": 1000}}
    _rec(tmp_path, "A.json", requirement="A", state="DONE", capsule=True, cost_usd=1.0, **full)
    _rec(tmp_path, "B.json", requirement="B", state="FAILED", capsule=True, cost_usd=3.0, **full)
    _rec(tmp_path, "C.json", requirement="C", state="DONE", capsule=False, cost_usd=0.5,
         **{**full, "budget_class": "MICRO", "turns": 4})
    g = K.roi_groups(tmp_path)
    on = g["capsule"]["on"]
    assert (on["runs"], on["done"], on["done_share"], on["agent_roi"]) == (2, 1, 0.5, 0.25)
    assert (on["turns"], on["cost"], on["searches"], on["tok_pre_edit"]) == (10, 2.0, 2, 1000)
    assert g["capsule"]["off"]["agent_roi"] == 2.0
    assert g["class"]["MICRO"]["turns"] == 4 and g["class"]["NORMAL"]["runs"] == 2
    assert "ROI/USD" in "\n".join(K.roi_table(tmp_path))


def test_roi_counts_missing_fields_instead_of_crashing(tmp_path):
    _rec(tmp_path, "A.json", requirement="A", state="DONE")
    _rec(tmp_path, "B.json", requirement="B", state="DONE", mode="SHIP", cost_usd=1.0,
         reads={"reads": "many"}, capsule="maybe", budget_class=None)
    (tmp_path / "C.json").write_text("{not json")
    g = K.roi_groups(tmp_path)
    assert g["capsule"]["unknown"]["runs"] == 2 and g["class"]["unknown"]["runs"] == 2
    assert g["class"]["unknown"]["missing"] > 0 and g["class"]["unknown"]["turns"] is None
    assert K.roi_table(tmp_path) and K.roi_groups(tmp_path / "none") == {"capsule": {}, "class": {}}


def test_commit_uses_the_workers_subject_and_keeps_the_summary_in_the_body(repo, tmp_path):
    run, wt = _done_run(repo, tmp_path)
    (wt / "new.py").write_text("x = 1\n")
    (wt / ".build" / "evidence.json").write_text(json.dumps(
        {"status": "DONE", "summary": "Long account of what changed and why.",
         "commit_subject": "fix(router): stop retrying a path that keeps failing"}))
    K.finalize(run, None, tmp_path / "runs")
    assert _git(wt, "log", "-1", "--format=%s") == "fix(router): stop retrying a path that keeps failing"
    body = _git(wt, "log", "-1", "--format=%b")
    assert "Long account of what changed and why." in body and "Build run: R-1" in body


def test_commit_subject_falls_back_without_cutting_a_word(repo, tmp_path):
    run, wt = _done_run(repo, tmp_path)
    (wt / "new.py").write_text("x = 1\n")
    (wt / ".build" / "evidence.json").write_text(json.dumps(
        {"status": "DONE", "commit_subject": "two\nlines",
         "summary": "Added the wave controller that queues launches watches and integrates runs"}))
    K.finalize(run, None, tmp_path / "runs")
    subject = _git(wt, "log", "-1", "--format=%s")
    assert subject == "build(R-1): Added the wave controller that queues launches watches and"


# ------------------------------------------------------------------ repair loop

def _evidence_done(wt, summary="s"):
    (wt / ".build" / "evidence.json").write_text(json.dumps({"status": "DONE", "summary": summary}))


def _count(wt, base):
    return int(_git(wt, "rev-list", "--count", f"{base}..HEAD"))


def test_a_second_finalize_squashes_onto_one_commit_from_base(repo, tmp_path):
    run, wt = _done_run(repo, tmp_path)
    (wt / "a.py").write_text("a = 1\n")
    _evidence_done(wt)
    first = K.finalize(run, None, tmp_path / "runs")
    (wt / "b.py").write_text("b = 1\n")
    second = K.finalize(run, None, tmp_path / "runs")
    assert _count(wt, run.base_sha) == 1 and second["commit"] != first["commit"]
    assert _git(wt, "rev-parse", f"{second['commit']}~1") == run.base_sha
    assert sorted(second["files_changed"]) == ["a.py", "b.py"]


def test_a_second_finalize_with_no_new_change_returns_the_same_commit(repo, tmp_path):
    run, wt = _done_run(repo, tmp_path)
    (wt / "a.py").write_text("a = 1\n")
    _evidence_done(wt)
    first = K.finalize(run, None, tmp_path / "runs")
    second = K.finalize(run, None, tmp_path / "runs")
    assert second["commit"] == first["commit"] != ""
    assert second["files_changed"] == ["a.py"] and _count(wt, run.base_sha) == 1


def test_repair_prompt_carries_every_finding_and_the_rule():
    p = K.repair_prompt({"requirement": "R-1", "result": "VERIFIER_FAIL", "detail": "bad merge",
                         "verifier": {"verdict": "FAIL", "failing_criteria": ["crit one"],
                                      "evidence_refs": ["a.py:3"]}})
    for text in ("VERIFIER_FAIL", "crit one", "a.py:3", "bad merge", "never faked", ".build/evidence.json"):
        assert text in p
    q = K.repair_prompt({"requirement": "R-1", "result": "NOT_GREEN",
                         "red_green": {"result": "NOT_GREEN", "tests": ["tests/t.py"], "tail": "E assert 1"}})
    assert "tests/t.py" in q and "E assert 1" in q


def _repairable(repo, tmp_path, integration=True, session="S1", result="FAILED_INTEGRATION", **more):
    run, wt = _done_run(repo, tmp_path)
    (wt / "a.py").write_text("a = 1\n")
    _evidence_done(wt)
    runs = tmp_path / "runs"
    K.finalize(run, None, runs)
    if not session:
        rec = json.loads((runs / "R-1.json").read_text())
        (runs / "R-1.json").write_text(json.dumps({**rec, "session_id": ""}))
    K._write_run_meta(wt, {"req": "R-1", "mode": "SHIP", "klass": "NORMAL",
                           "base_sha": run.base_sha, "branch": "build/r-1", "model": "sonnet"})
    if integration:
        (runs / "R-1.integration.json").write_text(json.dumps(
            {"requirement": "R-1", "result": result, "detail": "no test touched", **more}))
    return wt, runs


def test_repair_resumes_the_recorded_session_at_half_budget(monkeypatch, repo, tmp_path):
    wt, runs = _repairable(repo, tmp_path)
    cmds = []
    monkeypatch.setattr(K, "_start", lambda cmd, w: cmds.append(cmd) or _Proc(None))
    run = K.repair("R-1", worktrees=tmp_path / "wts", runs_dir=runs, repo=repo)
    cmd = cmds[0]
    assert cmd[cmd.index("--resume") + 1] == "S1"
    assert cmd[cmd.index("--max-turns") + 1] == "12" and cmd[cmd.index("--max-budget-usd") + 1] == "1.5"
    assert any("FAILED_INTEGRATION" in c and "no test touched" in c for c in cmd)
    assert run.repairs == 1 and run.base_sha and run.wt == wt and run.kind == "REPAIR"


def _repair_cmd(monkeypatch, repo, tmp_path, **kw):
    wt, runs = _repairable(repo, tmp_path, **kw)
    cmds = []
    monkeypatch.setattr(K, "_start", lambda cmd, w: cmds.append(cmd) or _Proc(None))
    K.repair("R-1", worktrees=tmp_path / "wts", runs_dir=runs, repo=repo)
    return cmds[0], runs


def test_an_unlocalized_first_repair_is_a_fresh_session_with_a_capsule(monkeypatch, repo, tmp_path):
    cmd, _ = _repair_cmd(monkeypatch, repo, tmp_path, result="NO_TEST")
    assert "--resume" not in cmd
    prompt = cmd[cmd.index("-p") + 1]
    assert prompt.startswith("# Failure capsule: R-1 (attempt 2)") and "NO_TEST" in prompt
    assert "## Already ruled out" in prompt and "## Current diff stat" in prompt and "a.py" in prompt


@pytest.mark.parametrize("failing,resumes", [(3, True), (4, False)])
def test_not_green_resumes_only_up_to_three_failing_tests(monkeypatch, repo, tmp_path, failing, resumes):
    cmd, _ = _repair_cmd(monkeypatch, repo, tmp_path, result="NOT_GREEN",
                         failures=[f"t{i}" for i in range(failing)])
    assert ("--resume" in cmd) is resumes


def test_every_repair_from_attempt_three_is_fresh_and_never_the_transcript(monkeypatch, repo, tmp_path):
    wt, runs = _repairable(repo, tmp_path)
    K.ledger_append({"attempt_id": "x1", "req": "R-1", "kind": "REPAIR", "cost_usd": 1.0}, runs)
    cmds = []
    monkeypatch.setattr(K, "_start", lambda cmd, w: cmds.append(cmd) or _Proc(None))
    K.repair("R-1", worktrees=tmp_path / "wts", runs_dir=runs, repo=repo)
    assert "--resume" not in cmds[0] and "(attempt 3)" in cmds[0][cmds[0].index("-p") + 1]


def test_failure_capsule_names_failures_reproduction_and_ruled_out(repo, tmp_path):
    wt = tmp_path / "wts" / "r-1"
    base = K.create_worktree(wt, "r-1", repo=repo)
    (wt / "n.py").write_text("n = 1\n")
    cap = K.failure_capsule(
        "R-1", {"result": "NOT_GREEN", "failures": ["FAILED tests/t.py::test_a - boom"],
                "verifier": {"verdict": "FAIL", "failing_criteria": ["crit"], "evidence_refs": ["f.py:2"]},
                "red_green": {"tests": ["tests/t.py"], "tail": "E boom"}},
        {"evidence": {"summary": "I fixed it", "gaps": ["live"]}, "files_changed": ["n.py"]},
        wt, base, 3)
    for text in ("NOT_GREEN", "tests/t.py::test_a - boom", "crit", "f.py:2", "E boom", "I fixed it",
                 "known gap: live", "pytest_brief.py -- tests/t.py", "- n.py"):
        assert text in cap


def test_escalate_is_a_fresh_stronger_session_with_the_p0_budget(monkeypatch, repo, tmp_path):
    wt, runs = _repairable(repo, tmp_path, result="NO_TEST")
    cmds = []
    monkeypatch.setattr(K, "_start", lambda cmd, w: cmds.append(cmd) or _Proc(None))
    run = K.escalate("R-1", capsule_extra="OWNER NOTE", worktrees=tmp_path / "wts",
                     runs_dir=runs, repo=repo)
    cmd = cmds[0]
    assert "--resume" not in cmd and cmd[cmd.index("--model") + 1] == "opus"
    assert cmd[cmd.index("--max-turns") + 1] == "40" and cmd[cmd.index("--max-budget-usd") + 1] == "6.0"
    prompt = cmd[cmd.index("-p") + 1]
    assert "# Failure capsule: R-1" in prompt and prompt.endswith("OWNER NOTE")
    assert run.kind == "ESCALATE" and run.klass == "P0" and run.wt == wt


def test_escalate_makes_a_worktree_from_head_when_there_is_none(monkeypatch, repo, tmp_path):
    cmds = []
    monkeypatch.setattr(K, "_start", lambda cmd, w: cmds.append(cmd) or _Proc(None))
    run = K.escalate("R-9", model="sonnet", worktrees=tmp_path / "wts", runs_dir=tmp_path / "runs",
                     repo=repo, row=row("R-9"))
    assert (run.wt / ".build" / "packet.md").exists() and run.base_sha
    assert cmds[0][cmds[0].index("--model") + 1] == "sonnet"


def test_escalate_cli_reads_the_packet_file(monkeypatch, tmp_path, capsys):
    packet = tmp_path / "p.md"
    packet.write_text("EXTRA", encoding="utf-8")
    seen = {}
    monkeypatch.setattr(K, "escalate", lambda req, **kw: seen.update(req=req, **kw) or "run")
    monkeypatch.setattr(K, "watch", lambda runs: [{"requirement": "R-1", "state": "DONE"}])
    assert K.main(["escalate", "R-1", "--model", "sonnet", "--packet", str(packet)]) == 0
    assert seen == {"req": "R-1", "model": "sonnet", "capsule_extra": "EXTRA"}
    assert "R-1: DONE" in capsys.readouterr().out


# ------------------------------------------------------------ invocation ledger

def _attempt(wt, run, *events, offset=True):
    """Append one invocation's events to the shared stream and aim `run` at them."""
    if offset:
        run.stream_offset = K._stream_size(wt)
    with open(wt / ".build" / "stream.jsonl", "a", encoding="utf-8") as fh:
        for e in events:
            fh.write(json.dumps(e) + "\n")


def _inv(sid, cost, turns=1, inp=10, **kw):
    return [{"type": "system", "subtype": "init", "session_id": sid},
            {"type": "result", "subtype": "success", "session_id": sid, "num_turns": turns,
             "total_cost_usd": cost, "usage": {"input_tokens": inp}, **kw}]


def _new_run(wt, repo, **kw):
    return K.Running("R-1", _Proc(0), wt, base_sha="", repo=repo, **kw)


def test_ledger_line_carries_the_attempt_identity(repo, tmp_path, monkeypatch):
    monkeypatch.setenv("FRIDAY_BUILD_RUN_ID", "run1")
    monkeypatch.setenv("FRIDAY_BUILD_WAVE_ID", "run1-w2")
    run, wt = _done_run(repo, tmp_path)
    K.finalize(run, None, tmp_path / "runs")
    (entry,) = K.ledger_entries(tmp_path / "runs")
    assert entry["attempt_id"] == run.attempt_id and entry["kind"] == "SHIP"
    assert (entry["req"], entry["program_run_id"], entry["wave_id"]) == ("R-1", "run1", "run1-w2")
    assert (entry["model"], entry["session_id"], entry["cost_usd"], entry["turns"]) == ("sonnet", "S1", 0.2, 3)
    for key in ("input_tokens", "output_tokens", "cache_read_tokens", "cache_creation_tokens",
                "wall_seconds", "peak_rss_mb", "at"):
        assert key in entry
    run2 = _new_run(wt, repo)
    assert run2.attempt_id != run.attempt_id


def test_ledger_defaults_run_and_wave_to_empty_without_a_program(repo, tmp_path, monkeypatch):
    monkeypatch.delenv("FRIDAY_BUILD_RUN_ID", raising=False)
    monkeypatch.delenv("FRIDAY_BUILD_WAVE_ID", raising=False)
    run, _ = _done_run(repo, tmp_path)
    K.finalize(run, None, tmp_path / "runs")
    (entry,) = K.ledger_entries(tmp_path / "runs")
    assert entry["program_run_id"] == "" and entry["wave_id"] == ""


def test_case1_an_earlier_invocations_cost_in_the_stream_is_not_charged_again(repo, tmp_path):
    run, wt = _done_run(repo, tmp_path)
    _attempt(wt, run, {"type": "result", "session_id": "S1", "num_turns": 9, "total_cost_usd": 3.0, "usage": {}})
    run.stream_offset = K._stream_size(wt)
    _attempt(wt, run, *_inv("S1", 1.0, turns=2), offset=False)
    K.finalize(run, None, tmp_path / "runs")
    assert K.invocation_cost(run.attempt_id, tmp_path / "runs") == 1.0


def test_case2_each_of_three_resumes_owns_only_its_events(repo, tmp_path):
    runs = tmp_path / "runs"
    run, wt = _done_run(repo, tmp_path)
    K.finalize(run, None, runs)  # the first invocation owns the $0.2 already in the stream
    owned = []
    for i, cost in enumerate((1.0, 2.0, 4.0)):
        nxt = _new_run(wt, repo, kind="RESUME")
        _attempt(wt, nxt, *_inv("S1", cost, turns=i + 5))
        K.finalize(nxt, None, runs)
        owned.append(K.invocation_cost(nxt.attempt_id, runs))
    assert owned == [1.0, 2.0, 4.0] and K.lifetime_cost("R-1", runs) == 7.2


def test_case3_refinalizing_the_same_attempt_does_not_double_count(repo, tmp_path):
    runs = tmp_path / "runs"
    run, wt = _done_run(repo, tmp_path)
    K.finalize(run, None, runs)
    K.finalize(run, None, runs)  # a controller restart mid-attempt finalizes it again
    assert len(K.ledger_entries(runs)) == 1 and K.lifetime_cost("R-1", runs) == 0.2
    assert K.ledger_append(dict(K.ledger_entries(runs)[0]), runs) is False


def test_case4_a_duplicate_result_event_is_counted_once(repo, tmp_path):
    run, wt = _done_run(repo, tmp_path)
    _attempt(wt, run, *_inv("S1", 1.0, turns=4), _inv("S1", 1.0, turns=4)[1])
    rec = K.finalize(run, None, tmp_path / "runs")
    assert rec["cost_usd"] == 1.0 and rec["turns"] == 4
    run2 = _new_run(wt, repo)
    _attempt(wt, run2, {"type": "result", "uuid": "u1", "total_cost_usd": 2.0, "usage": {}},
             {"type": "result", "uuid": "u1", "total_cost_usd": 2.0, "usage": {}})
    assert K.finalize(run2, None, tmp_path / "runs")["cost_usd"] == 2.0


def test_case5_another_requirements_cost_in_the_stream_is_ignored(repo, tmp_path):
    run, wt = _done_run(repo, tmp_path)
    _attempt(wt, run, *_inv("S1", 1.0), {"type": "result", "session_id": "OTHER", "num_turns": 3,
                                         "total_cost_usd": 5.0, "usage": {}})
    assert K.finalize(run, None, tmp_path / "runs")["cost_usd"] == 1.0


def test_case6_projections_rebuilt_from_the_ledger_equal_the_incremental_totals(repo, tmp_path, monkeypatch):
    runs = tmp_path / "runs"
    run, wt = _done_run(repo, tmp_path)
    monkeypatch.setenv("FRIDAY_BUILD_RUN_ID", "r1")
    incremental = {"r1-w1": 0.0, "r1-w2": 0.0}
    for wave, cost in (("r1-w1", 0.5), ("r1-w1", 0.25), ("r1-w2", 1.0)):
        monkeypatch.setenv("FRIDAY_BUILD_WAVE_ID", wave)
        nxt = _new_run(wt, repo)
        _attempt(wt, nxt, *_inv("S1", cost, turns=int(cost * 100)))
        rec = K.finalize(nxt, None, runs)
        incremental[wave] += rec["cost_usd"]
    assert K.wave_cost("r1-w1", runs) == incremental["r1-w1"] == 0.75
    assert K.wave_cost("r1-w2", runs) == incremental["r1-w2"] == 1.0
    assert K.run_cost("r1", runs) == 1.75 and K.wave_cost("", runs) == 0.0
    assert K.lifetime_cost("R-1", runs) == 1.75


def test_retry_context_growth_is_input_plus_cache_over_the_previous_attempt(repo, tmp_path):
    runs = tmp_path / "runs"
    run, wt = _done_run(repo, tmp_path)
    first = _new_run(wt, repo)
    _attempt(wt, first, *_inv("S1", 1.0, turns=11, inp=100))
    K.finalize(first, None, runs)
    for kind, inp, turns in (("REPAIR", 150, 12), ("RESUME", 300, 13)):
        nxt = _new_run(wt, repo, kind=kind)
        _attempt(wt, nxt, *_inv("S1", 1.0, turns=turns, inp=inp))
        K.finalize(nxt, None, runs)
    ledger = K.ledger_entries(runs)
    assert "retry_context_growth" not in ledger[0]
    assert [e["retry_context_growth"] for e in ledger[1:]] == [1.5, 2.0]


def test_roi_is_rebuilt_from_the_ledger(repo, tmp_path):
    runs = tmp_path / "runs"
    for i, (state, cost) in enumerate((("DONE", 1.0), ("FAILED", 3.0))):
        K.ledger_append({"attempt_id": f"a{i}", "req": f"R-{i}", "kind": "SHIP", "mode": "SHIP",
                         "state": state, "cost_usd": cost, "capsule": True,
                         "budget_class": "NORMAL", "turns": 4}, runs)
    _rec(runs, "R-9.json", requirement="R-9", state="DONE", capsule=False, cost_usd=0.5, mode="SHIP")
    _rec(runs, "R-0.json", requirement="R-0", state="DONE", capsule=False, cost_usd=99.0, mode="SHIP")
    g = K.roi_groups(runs)
    assert g["capsule"]["on"]["runs"] == 2 and g["capsule"]["on"]["cost"] == 2.0
    assert g["capsule"]["off"]["runs"] == 1  # only R-9, which predates the ledger


@pytest.mark.parametrize("missing", ["worktree", "session", "integration"])
def test_repair_refuses_without_worktree_session_or_integration(monkeypatch, repo, tmp_path, missing):
    wt, runs = _repairable(repo, tmp_path, integration=missing != "integration",
                           session="" if missing == "session" else "S1")
    root = tmp_path / ("elsewhere" if missing == "worktree" else "wts")
    monkeypatch.setattr(K, "_start", lambda cmd, w: pytest.fail("must not relaunch"))
    with pytest.raises(K.KernelError):
        K.repair("R-1", worktrees=root, runs_dir=runs, repo=repo)


def test_repair_prompt_names_the_tests_that_failed_integration():
    text = K.repair_prompt({"requirement": "R-1", "result": "FAILED_INTEGRATION", "detail": "reset to pre_head",
                            "failures": ["FAILED tests/test_x.py::test_y - AssertionError: boom"]})
    assert "tests/test_x.py::test_y - AssertionError: boom" in text


def test_a_scouts_findings_can_be_its_final_message(repo, tmp_path):
    run, wt = _done_run(repo, tmp_path, mode="SCOUT")
    found = {"requirement": "R-1", "gap": "g", "files": ["friday/x.py"], "tests": [],
             "acceptance_plan": "p", "risk": "MEDIUM", "budget_class": "NORMAL"}
    _stream(wt, {"type": "system", "subtype": "init", "session_id": "S1"},
            {"type": "result", "subtype": "success", "session_id": "S1", "num_turns": 4,
             "total_cost_usd": 0.1, "usage": {}, "result": "Findings:\n" + json.dumps(found)})
    rec = K.finalize(run, None, tmp_path / "runs")
    assert rec["state"] == "DONE" and rec["evidence"] == found
    assert json.loads((tmp_path / "runs" / "R-1.scout.json").read_text()) == found


def test_the_scout_packet_asks_for_the_findings_as_the_final_message():
    text = K.scout_packet_text({"id": "R-1", "priority": "P0", "summary": "s"})
    assert "final message" in text and ".build/findings.json" not in text


# ------------------------------------------------------------------ per-run accounting

def _result(cost, turns=1):
    return {"type": "result", "subtype": "success", "session_id": "S1", "num_turns": turns,
            "total_cost_usd": cost, "usage": {"output_tokens": 10}}


def test_finalize_counts_only_the_events_after_the_runs_stream_offset(repo, tmp_path):
    run, wt = _done_run(repo, tmp_path)  # the stream holds one earlier attempt: $0.2, 3 turns
    run.stream_offset = K._stream_size(wt)
    with open(wt / ".build" / "stream.jsonl", "a", encoding="utf-8") as fh:
        fh.write(json.dumps(_result(0.5, 2)) + "\n")
        fh.write(json.dumps(_result(0.25, 1)) + "\n")  # a continuation inside the same run counts
    (wt / "a.py").write_text("a = 1\n")
    _evidence_done(wt)
    rec = K.finalize(run, None, tmp_path / "runs")
    assert rec["cost_usd"] == 0.75 and rec["turns"] == 3 and rec["output_tokens"] == 20
    assert rec["cumulative_cost_usd"] == 0.75
    again = K.finalize(run, None, tmp_path / "runs")
    assert again["cumulative_cost_usd"] == 0.75  # the same attempt re-finalized is not counted twice


def test_stream_size_marks_where_a_run_starts(tmp_path):
    wt = tmp_path / "r-1"
    _stream(wt, _result(1.0))
    size = K._stream_size(wt)
    assert size > 0 and K._stream_size(tmp_path / "missing") == 0
    assert len(K._events(wt / ".build" / "stream.jsonl", size)) == 0
    assert len(K._events(wt / ".build" / "stream.jsonl")) == 1


def test_peak_history_survives_a_run_record_overwrite(repo, tmp_path):
    runs = tmp_path / "runs"
    run, wt = _done_run(repo, tmp_path)
    run.peak_rss_mb = 1700.0
    K.finalize(run, None, runs)
    run.peak_rss_mb = 300.0  # a lighter run overwrites R-1.json
    K.finalize(run, None, runs)
    assert json.loads((runs / "R-1.json").read_text())["peak_rss_mb"] == 300.0
    assert K.worker_gb(runs) == 1700.0 / 1024
    entry = json.loads((runs / K.PEAKS_FILE).read_text().splitlines()[0])
    assert entry["req"] == "R-1" and entry["peak_rss_mb"] == 1700.0
    assert {"mode", "budget_class", "at"} <= set(entry)
    assert runs / K.PEAKS_FILE not in K.run_records(runs)


def test_peak_history_window_is_the_last_entries_and_records_are_the_fallback(tmp_path):
    (tmp_path / K.PEAKS_FILE).write_text(
        "".join(json.dumps({"peak_rss_mb": 9000.0 if i == 0 else 1024.0}) + "\n"
                for i in range(K.RSS_WINDOW + 1)) + "not json\n", encoding="utf-8")
    assert K.worker_gb(tmp_path) == 1.0  # the 9000 entry fell out of the window
    other = tmp_path / "other"
    _peak_record(other, "R", 2048, 1)
    assert K.worker_gb(other) == 2.0


# ------------------------------------------------------- sparse agent fabric

def _full_row(**more):
    return {**row("R-1", "P1", files=("friday/a.py",)), "tests": ["tests/test_a.py"], **more}


def test_agent_utility_decides_every_axis_from_the_row_and_context():
    decision, why = K.agent_utility(_full_row(), {})
    assert decision == {"orient": "SHIP", "verify": "SKIP_VERIFY", "session": "FRESH",
                        "parallel": "SEQUENTIAL", "escalate": "NONE", "step": "REPAIR"} and why
    assert K.agent_utility(row("R-1", files=()), {})[0]["orient"] == "SCOUT"
    assert K.agent_utility(row("R-1", files=()), {"has_findings": True})[0]["orient"] == "SHIP"
    assert K.agent_utility(_full_row(priority="P0"), {})[0]["verify"] == "VERIFY"
    assert K.agent_utility(_full_row(), {"protected": True})[0]["verify"] == "VERIFY"
    assert K.agent_utility(_full_row(), {"slots": 2})[0]["parallel"] == "PARALLEL"
    assert K.agent_utility(_full_row(), {"slots": 2, "overlap": True})[0]["parallel"] == "SEQUENTIAL"


def test_agent_utility_resumes_only_a_localized_second_attempt_with_modest_growth():
    resume = {"attempt": 2, "localized": True}
    assert K.agent_utility(_full_row(), resume)[0]["session"] == "RESUME"
    assert K.agent_utility(_full_row(), {**resume, "retry_context_growth": 1.3})[0]["session"] == "RESUME"
    decision, why = K.agent_utility(_full_row(), {**resume, "retry_context_growth": 1.31})
    assert decision["session"] == "FRESH" and any("1.31" in w for w in why)
    assert K.agent_utility(_full_row(), {"attempt": 3, "localized": True})[0]["session"] == "FRESH"


@pytest.mark.parametrize("failures,step,escalate", [
    (0, "REPAIR", "NONE"), (1, "ADVISOR", "ESCALATE"), (2, "IMPLEMENTER", "NONE"),
    (3, "PARK", "PARK"), (9, "PARK", "PARK")])
def test_agent_utility_walks_the_cascade_then_parks(failures, step, escalate):
    decision, _ = K.agent_utility(_full_row(), {"failures": failures})
    assert (decision["step"], decision["escalate"]) == (step, escalate)


def test_mode_for_goes_through_agent_utility(monkeypatch, tmp_path):
    seen = []
    real = K.agent_utility
    monkeypatch.setattr(K, "agent_utility", lambda r, c=None: seen.append(c) or real(r, c))
    assert K.mode_for(row("R-1", files=()), tmp_path) == "SCOUT"
    assert seen == [{"has_findings": False}]


def test_launch_writes_a_started_job_and_finalize_closes_it(monkeypatch, repo, tmp_path):
    monkeypatch.setattr(K, "_start", lambda cmd, wt: _Proc(None))
    runs = tmp_path / "runs"
    run = K.launch(_full_row(), worktrees=tmp_path / "wts", runs_dir=runs, repo=repo)
    job = json.loads((runs / "jobs" / f"{run.attempt_id}.json").read_text())
    assert job["state"] == "STARTED" and job["role"] == "IMPLEMENTER" and job["pid"] == _Proc.pid
    assert job["req"] == "R-1" and job["worktree"] == str(run.wt) and job["started"] and not job["ended"]
    run.proc = _Proc(0)
    _stream(run.wt, {"type": "system", "session_id": "S1"},
            {"type": "result", "subtype": "success", "session_id": "S1", "num_turns": 1,
             "total_cost_usd": 0.1, "usage": {}})
    K.finalize(run, "TIMEOUT", runs)
    done = K.jobs(runs)[0]
    assert done["state"] == "FAILED" and done["ended"] and "TIMEOUT" in done["reason"]


def test_repair_escalate_and_advise_each_write_their_own_job(monkeypatch, repo, tmp_path):
    wt, runs = _repairable(repo, tmp_path)
    monkeypatch.setattr(K, "_start", lambda cmd, w: _Proc(None))
    for fn in (K.repair, K.escalate, K.advise):
        fn("R-1", worktrees=tmp_path / "wts", runs_dir=runs, repo=repo)
    got = {j["role"]: j for j in K.jobs(runs)}
    assert set(got) == {"REPAIR", "ESCALATE", "ADVISOR"}
    assert got["ESCALATE"]["model"] == "opus" and got["REPAIR"]["state"] == "STARTED"
    assert "no edits" in got["ADVISOR"]["reason"]


def test_a_repair_goes_fresh_when_retry_context_outgrew_its_predecessor(monkeypatch, repo, tmp_path):
    wt, runs = _repairable(repo, tmp_path)
    K.ledger_append({"attempt_id": "x", "req": "R-1", "kind": "SCOUT", "retry_context_growth": 1.5}, runs)
    cmds = []
    monkeypatch.setattr(K, "_start", lambda cmd, w: cmds.append(cmd) or _Proc(None))
    K.repair("R-1", worktrees=tmp_path / "wts", runs_dir=runs, repo=repo)
    assert "--resume" not in cmds[0]
    assert K.jobs(runs)[0]["reason"].startswith("FRESH")


def test_an_advised_implementer_is_fresh_on_the_normal_model_with_the_packet(monkeypatch, repo, tmp_path):
    wt, runs = _repairable(repo, tmp_path)
    cmds = []
    monkeypatch.setattr(K, "_start", lambda cmd, w: cmds.append(cmd) or _Proc(None))
    K.repair("R-1", worktrees=tmp_path / "wts", runs_dir=runs, repo=repo, advice="THE DIAGNOSIS")
    cmd = cmds[0]
    assert "--resume" not in cmd and "THE DIAGNOSIS" in cmd and cmd[cmd.index("--model") + 1] == "sonnet"


def test_the_advisor_is_read_only_and_its_final_message_is_the_packet(monkeypatch, repo, tmp_path):
    wt, runs = _repairable(repo, tmp_path)
    before = (runs / "R-1.json").read_text()
    cmds = []
    monkeypatch.setattr(K, "_start", lambda cmd, w: cmds.append(cmd) or _Proc(None))
    run = K.advise("R-1", worktrees=tmp_path / "wts", runs_dir=runs, repo=repo)
    cmd = cmds[0]
    tools = cmd[cmd.index("--allowedTools") + 1]
    assert cmd[cmd.index("--model") + 1] == "opus" and run.kind == "ADVISOR"
    assert tools == K.SCOUT_TOOLS and not {"Edit", "Write"} & set(tools.split(","))
    run.proc, run.stream_offset = _Proc(0), 0  # _stream rewrites the log, so nothing precedes it
    _stream(wt, {"type": "system", "session_id": "S2"},
            {"type": "result", "subtype": "success", "session_id": "S2", "num_turns": 2,
             "total_cost_usd": 0.4, "usage": {}, "result": "root cause: stale cache in a.py:3"})
    rec = K.finalize(run, None, runs)
    assert rec["state"] == "DONE" and rec["kind"] == "ADVISOR" and not rec["commit"]
    assert "stale cache" in K.load_advice("R-1", runs)
    assert (runs / "R-1.json").read_text() == before
    assert [j["state"] for j in K.jobs(runs) if j["role"] == "ADVISOR"] == ["COMPLETE"]


def test_an_advisor_with_an_empty_final_message_fails(monkeypatch, repo, tmp_path):
    wt, runs = _repairable(repo, tmp_path)
    monkeypatch.setattr(K, "_start", lambda cmd, w: _Proc(None))
    run = K.advise("R-1", worktrees=tmp_path / "wts", runs_dir=runs, repo=repo)
    run.proc, run.stream_offset = _Proc(0), 0
    _stream(wt, {"type": "system", "session_id": "S2"},
            {"type": "result", "subtype": "success", "session_id": "S2", "num_turns": 1,
             "total_cost_usd": 0.1, "usage": {}, "result": "  "})
    assert K.finalize(run, None, runs)["state"] == "FAILED" and K.load_advice("R-1", runs) == ""


def test_adopt_rebuilds_a_running_from_a_job_and_the_run_meta(repo, tmp_path):
    run, wt = _done_run(repo, tmp_path)
    K._write_run_meta(wt, {"req": "R-1", "mode": "SHIP", "klass": "COMPLEX", "base_sha": run.base_sha,
                           "branch": "build/r-1", "model": "sonnet", "started": 123.0, "stream_offset": 0})
    job = {"attempt_id": "a1", "req": "R-1", "role": "REPAIR", "model": "sonnet", "pid": 77,
           "worktree": str(wt), "reason": "why"}
    got = K.adopt(job, alive=lambda pid: pid == 77, runs_dir=tmp_path / "runs", repo=repo)
    assert got.attempt_id == "a1" and got.kind == "REPAIR" and got.klass == "COMPLEX" and got.started == 123.0
    assert got.proc.poll() is None and got.proc.pid == 77
    got.proc._alive = lambda pid: False
    assert got.proc.poll() == 0
    assert K.has_result(wt) is True and K.has_result(tmp_path / "nowhere") is False


def test_admit_itself_counts_a_live_lease(tmp_path):
    # A mutation that dropped the lease from admit() survived: the two-kernel
    # test passed through reserve()'s recheck. A wave must not be planned on
    # headroom another job has already leased.
    _heavy(tmp_path, "A")
    _peaks(tmp_path, ("A", "COMPLEX", 1741), ("B", "COMPLEX", 1741))
    assert K.reserve("att-1", "A", "COMPLEX", cap=3, gov=FakeGov(available=3.5), runs_dir=tmp_path)[0]
    got, why = K.admit([({"id": "B"}, "SHIP", "COMPLEX")], cap=3, gov=FakeGov(available=3.5), runs_dir=tmp_path)
    assert got == [] and "leased" in why


def test_a_row_parked_by_the_owner_is_owed_but_never_scheduled():
    # Owner, 2026-10-02: CORE-001 is parked - not cancelled, not verified, and
    # it must not keep re-entering the ready queue.
    assert PL.PARKED_BY_OWNER in PL.WORKING and PL.PARKED_BY_OWNER not in PL.TERMINAL
    parked, other = row("CORE-001", status=PL.PARKED_BY_OWNER), row("ACT-001")
    assert [r["id"] for r in K.ready([parked, other], {})] == ["ACT-001"]
    assert K.waiting_on_owner(parked)
    assert "CORE-001" in [r["id"] for r in K.ready([parked, other], {}, include_owner=True)]


def test_every_invocation_starts_without_the_previous_runs_evidence(tmp_path, monkeypatch):
    # 2026-10-02: ACT-001's escalation hit max turns, but the repair before it had
    # left .build/evidence.json saying DONE - the stale verdict blocked the one
    # continuation and could pass for the new run's own. A run's verdict must be its own.
    wt = tmp_path / "wt"
    (wt / ".build").mkdir(parents=True)
    (wt / ".build" / "evidence.json").write_text(json.dumps({"status": "DONE", "summary": "old"}))
    monkeypatch.setattr(K.subprocess, "Popen", lambda *a, **kw: _Proc(None))
    K._start(["claude"], wt)
    assert not (wt / ".build" / "evidence.json").exists()
    kept = list((wt / ".build").glob("evidence.prev-*.json"))
    assert len(kept) == 1 and json.loads(kept[0].read_text())["summary"] == "old"



def test_an_advisor_out_of_turns_gets_one_write_it_now_continuation(tmp_path, monkeypatch):
    # 2026-10-02: EVAL-001's Opus advisor read for all 15 turns and hit the cap
    # with no diagnosis written ($0.68 lost). It resumes once, briefly, to write it.
    wt = tmp_path / "wt"
    _stream(wt, {"type": "system", "subtype": "init", "session_id": "ADV1"},
            {"type": "result", "subtype": "error_max_turns", "is_error": True, "session_id": "ADV1",
             "num_turns": 16, "total_cost_usd": 0.68, "result": ""})
    started = []
    monkeypatch.setattr(K, "_start", lambda cmd, w: started.append(cmd) or _Proc(None))
    run = K.Running("EVAL-001", _Proc(1), wt, mode="SHIP", base_sha="abc", branch="build/eval-001",
                    model="opus", kind="ADVISOR")
    assert K.continuation(run) is True and run.continued
    cmd = started[0]
    assert cmd[cmd.index("--resume") + 1] == "ADV1"
    assert int(cmd[cmd.index("--max-turns") + 1]) <= K.WRITE_NOW_TURNS
    assert cmd[cmd.index("--allowedTools") + 1] == K.SCOUT_TOOLS
    assert K.WRITE_NOW_PROMPT in cmd
    assert K.continuation(run) is False  # once only



def test_a_repair_never_inherits_the_strong_model_of_the_job_before_it(monkeypatch, repo, tmp_path):
    # 2026-10-02: EVAL-001's and ACT-001's advised implementers ran on Opus because
    # repair() took the model recorded by the Opus advisor before them (owner v12
    # section 8: normal coding work runs on the normal model).
    wt, runs = _repairable(repo, tmp_path)
    meta = json.loads((wt / ".build" / "run.json").read_text())
    K._write_run_meta(wt, {**meta, "model": "opus"})
    cmds = []
    monkeypatch.setattr(K, "_start", lambda cmd, w: cmds.append(cmd) or _Proc(None))
    run = K.repair("R-1", worktrees=tmp_path / "wts", runs_dir=runs, repo=repo)
    assert cmds[0][cmds[0].index("--model") + 1] == "sonnet" and run.model == "sonnet"
    # A fresh relaunch is a new invocation: its one continuation is still unused.
    assert run.continued is False
