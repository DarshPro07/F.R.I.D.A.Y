"""The build integrator on real temp git repos (no real `claude`, no real ledger)."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import build_integrate as bi  # noqa: E402
import build_kernel  # noqa: E402
from build_kernel import KernelError  # noqa: E402

ID = ("-c", "user.name=t", "-c", "user.email=t@t")
OLD_TEST = "from calc import add\n\n\ndef test_old():\n    assert add(1, 1) == 2\n"
LEDGER_SRC = '''VERIFIED = "VERIFIED"
PARTIAL = "PARTIAL"

PROGRESS_V7: list[tuple[str, dict]] = [
    ("REQ-0", {"status": VERIFIED}),
]


def _load_v7():
    return [{"id": "REQ-0", "status": "MISSING", "current_implementation": "", "next_action": "", "evidence": "",
             "tests": [], "files": []},
            {"id": "REQ-1", "status": "MISSING", "current_implementation": "", "next_action": "", "evidence": "",
             "tests": [], "files": []}]


def apply_progress(rows, progress):
    by_id = {r["id"]: dict(r) for r in rows}
    for rid, ch in progress:
        unknown = set(ch) - set(by_id[rid])
        if unknown:
            raise KeyError(unknown)
        by_id[rid].update(ch)
    return list(by_id.values())
'''


def sh(repo: Path, *args: str) -> str:
    res = subprocess.run(["git", *ID, *args], cwd=repo, capture_output=True, text=True, check=True)
    return res.stdout.strip()


def write(repo: Path, rel: str, text: str) -> None:
    p = repo / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(text.encode())


def make_repo(tmp_path: Path, files: dict[str, str], *, ledger: str = LEDGER_SRC,
              base_extra: dict[str, str] | None = None) -> tuple[Path, str]:
    repo = tmp_path / "repo"
    repo.mkdir()
    sh(repo, "init", "-q", "-b", "main")
    write(repo, "calc.py", "def add(a, b):\n    return a + b\n")
    write(repo, "tests/test_old.py", OLD_TEST)
    for rel, text in (base_extra or {}).items():
        write(repo, rel, text)
    write(repo, "scripts/prd_ledger.py", ledger)
    sh(repo, "add", "-A")
    sh(repo, "commit", "-q", "-m", "base")
    base = sh(repo, "rev-parse", "HEAD")
    sh(repo, "checkout", "-q", "-b", "build/req-1")
    for rel, text in files.items():
        write(repo, rel, text)
    sh(repo, "add", "-A")
    sh(repo, "commit", "-q", "-m", "work")
    commit = sh(repo, "rev-parse", "HEAD")
    sh(repo, "checkout", "-q", "main")
    return repo, base + " " + commit


GOOD = {"calc.py": "def add(a, b):\n    return a + b\n\n\ndef twice(x):\n    return x * 2\n",
        "tests/test_twice.py": "from calc import twice\n\n\ndef test_twice():\n    assert twice(2) == 4\n"}


def setup(tmp_path, files=None, *, state="DONE", files_changed=None, priority="P1", acceptance_met=True,
          base_extra=None):
    repo, shas = make_repo(tmp_path, files or GOOD, base_extra=base_extra)
    base, commit = shas.split()
    runs = tmp_path / "runs"
    runs.mkdir()
    run = {"requirement": "REQ-1", "state": state, "mode": "SHIP", "budget_class": "S", "base_sha": base,
           "branch": "build/req-1", "commit": commit,
           "files_changed": files_changed or sorted((files or GOOD)), "cost_usd": 1.0, "turns": 3,
           "worktree": "",
           "evidence": {"requirement": "REQ-1", "status": "DONE", "summary": "adds twice", "files_changed": [],
                        "tests": {"command": "", "passed": 1, "failed": 0}, "blockers": [],
                        "acceptance_met": acceptance_met, "gaps": []}}
    (runs / "REQ-1.json").write_text(json.dumps(run), encoding="utf-8")
    rows = [{"id": "REQ-1", "priority": priority, "summary": "twice", "acceptance": "twice doubles"}]
    return repo, runs, run, rows


def add_run(repo: Path, runs: Path, req: str, base: str, files: dict[str, str], *, priority="P0") -> dict:
    sh(repo, "checkout", "-q", "-b", f"build/{req.lower()}", base)
    for rel, text in files.items():
        write(repo, rel, text)
    sh(repo, "add", "-A")
    sh(repo, "commit", "-q", "-m", f"work {req}")
    commit = sh(repo, "rev-parse", "HEAD")
    sh(repo, "checkout", "-q", "main")
    run = {"requirement": req, "state": "DONE", "mode": "SHIP", "budget_class": "S", "base_sha": base,
           "branch": f"build/{req.lower()}", "commit": commit, "files_changed": sorted(files),
           "cost_usd": 1.0, "turns": 3, "worktree": "",
           "evidence": {"requirement": req, "status": "DONE", "summary": f"adds {req}",
                        "files_changed": [], "tests": {"command": "", "passed": 1, "failed": 0},
                        "blockers": [], "acceptance_met": True, "gaps": []}}
    (runs / f"{req}.json").write_text(json.dumps(run), encoding="utf-8")
    return run


def go(repo, runs, rows, **kw):
    return bi.integrate("REQ-1", repo=repo, runs_dir=runs, ledger_path=repo / "scripts" / "prd_ledger.py",
                        python=sys.executable, rows=rows, **kw)


def head(repo: Path) -> str:
    return sh(repo, "rev-parse", "HEAD")


def pass_runner(verdict="PASS"):
    def runner(cmd, prompt, cwd):
        return json.dumps({"result": f'ok {{"verdict": "{verdict}", "requirement_ids": ["REQ-1"]}}'})
    return runner


def boom_runner(cmd, prompt, cwd):
    raise AssertionError("verifier must not run")


# ---------------------------------------------------------------- happy path

def test_integrated_with_ledger(tmp_path):
    repo, runs, run, rows = setup(tmp_path)
    before = head(repo)
    out = go(repo, runs, rows)
    assert out["result"] == "INTEGRATED"
    assert out["red_green"]["result"] == "OK" and out["red_green"]["red_kind"] == "collection"
    assert out["ledger_status"] == "VERIFIED"
    assert "twice" in (repo / "calc.py").read_text()
    log = sh(repo, "log", "--format=%s", f"{before}..HEAD").splitlines()
    assert log == ["docs(ledger): REQ-1 -> VERIFIED from build run", "work"]
    assert sh(repo, "log", "-1", "--format=%an", "HEAD") == "DarshPro07"
    assert "('REQ-1', {" in (repo / "scripts" / "prd_ledger.py").read_text()
    # prd_ledger --check refuses VERIFIED without evidence: the entry carries
    # the proofs this integration actually ran.
    module = bi.load_ledger(repo / "scripts" / "prd_ledger.py")
    row = {r["id"]: r for r in module.apply_progress(module._load_v7(), module.PROGRESS_V7)}["REQ-1"]
    assert "RED (collection)" in row["evidence"] and out["integrated_commit"][:9] in row["evidence"]
    assert "affected tests" in row["evidence"]
    # 2026-10-02: --check refuses a VERIFIED v7 row that names no test; the entry
    # cites the RED/GREEN tests and the changed files.
    assert row["tests"] == ["tests/test_twice.py::test_twice"] and "calc.py" in row["files"]
    saved = json.loads((runs / "REQ-1.integration.json").read_text())
    assert saved["result"] == "INTEGRATED" and saved["integrated_commit"]
    assert sh(repo, "branch", "--list", "build/req-1") == ""  # worker branch removed on success


def test_verified_without_evidence_is_refused_and_the_ledger_is_untouched(tmp_path):
    path = tmp_path / "prd_ledger.py"
    path.write_text(LEDGER_SRC, encoding="utf-8")
    before = path.read_bytes()
    with pytest.raises(KernelError):
        bi.append_progress(path, "REQ-1", "VERIFIED", "impl", "", evidence=" ")
    assert path.read_bytes() == before
    with pytest.raises(KernelError):  # evidence but no named test
        bi.append_progress(path, "REQ-1", "VERIFIED", "impl", "", evidence="RED/GREEN", extra={"tests": []})
    assert path.read_bytes() == before


def test_temp_worktrees_are_removed(tmp_path):
    repo, runs, run, rows = setup(tmp_path)
    bi.red_green(run, bi.commit_test_files(run, repo), repo, sys.executable)
    assert len(sh(repo, "worktree", "list").splitlines()) == 1


def test_dry_run_changes_nothing(tmp_path):
    repo, runs, run, rows = setup(tmp_path)
    before = head(repo)
    out = go(repo, runs, rows, dry_run=True)
    assert out["result"] == "DRY_RUN" and head(repo) == before
    assert not (runs / "REQ-1.integration.json").exists()


# ---------------------------------------------------------------- refusals

@pytest.mark.parametrize("state", ["BLOCKED", "FAILED", "TIMEOUT", "STALLED"])
def test_refuses_a_run_that_is_not_done(tmp_path, state):
    repo, runs, run, rows = setup(tmp_path, state=state)
    with pytest.raises(KernelError, match="not DONE"):
        go(repo, runs, rows)


def test_refuses_without_a_commit(tmp_path):
    # A worker that claims the acceptance is already met is confirmed on HEAD instead
    # (test_a_run_that_changed_nothing_is_confirmed_on_head); one that does not is refused.
    repo, runs, run, rows = setup(tmp_path, acceptance_met=False)
    run["commit"] = ""
    (runs / "REQ-1.json").write_text(json.dumps(run), encoding="utf-8")
    with pytest.raises(KernelError, match="no commit"):
        go(repo, runs, rows)


def test_refuses_a_dirty_checkout_but_ignores_untracked(tmp_path):
    repo, runs, run, rows = setup(tmp_path)
    write(repo, "stray.txt", "x")
    assert bi.refuse_reason(run, repo) == ""
    write(repo, "calc.py", "# edited\n")
    with pytest.raises(KernelError, match="tracked changes"):
        go(repo, runs, rows)


# ---------------------------------------------------------------- collision

def test_overlap_since_base_is_rebase_required_and_leaves_tree_untouched(tmp_path):
    repo, runs, run, rows = setup(tmp_path)
    write(repo, "calc.py", "def add(a, b):\n    return b + a\n")
    sh(repo, "commit", "-q", "-am", "moved")
    before = head(repo)
    out = go(repo, runs, rows)
    assert out["result"] == "REBASE_REQUIRED" and "calc.py" in out["detail"]
    assert head(repo) == before and sh(repo, "status", "--porcelain") == ""
    assert json.loads((runs / "REQ-1.integration.json").read_text())["result"] == "REBASE_REQUIRED"


def test_a_base_that_is_not_an_ancestor_is_rebase_required_not_an_error(tmp_path):
    repo, runs, run, rows = setup(tmp_path)
    (runs / "REQ-1.json").write_text(json.dumps(dict(run, base_sha=run["commit"])), encoding="utf-8")
    before = head(repo)
    out = go(repo, runs, rows)
    assert out["result"] == "REBASE_REQUIRED" and "not an ancestor" in out["detail"]
    assert head(repo) == before


def test_a_rebase_required_candidate_keeps_its_worktree(tmp_path, monkeypatch):
    repo, runs, run, rows = setup(tmp_path)
    write(repo, "calc.py", "def add(a, b):\n    return b + a\n")
    sh(repo, "commit", "-q", "-am", "moved")
    removed = []
    monkeypatch.setattr(bi, "remove_worker", lambda r, repo=None: removed.append(r))
    assert go(repo, runs, rows)["result"] == "REBASE_REQUIRED" and removed == []


# ---------------------------------------------------------------- merge queue

def test_the_merge_queue_is_fifo_by_completion_time(tmp_path):
    q = bi.MergeQueue(tmp_path)
    q.enqueue("B", 200.0)
    q.enqueue("A", 100.0)
    q.enqueue("C", 300.0)
    assert [e["req"] for e in q.pending()] == ["A", "B", "C"]
    q.finish("A", "INTEGRATED")
    assert [e["req"] for e in q.pending()] == ["B", "C"]
    q.enqueue("B", 1.0)  # already waiting: not queued twice
    assert [e["req"] for e in q.pending()] == ["B", "C"]


def test_a_candidate_waits_for_the_head_of_the_queue(tmp_path):
    q = bi.MergeQueue(tmp_path, wait_s=0.2, poll_s=0.05)
    q.enqueue("A", 1.0)
    q.enqueue("B", 2.0)
    with pytest.raises(KernelError, match="waited"):
        with q.turn("B"):
            pytest.fail("B must not run before A")
    with q.turn("A"):
        assert (tmp_path / bi.QUEUE_LOCK).exists()
    assert not (tmp_path / bi.QUEUE_LOCK).exists()


def test_a_held_lock_blocks_the_head_and_a_dead_holder_is_replaced(tmp_path, monkeypatch):
    q = bi.MergeQueue(tmp_path, wait_s=0.2, poll_s=0.05)
    q.enqueue("A", 1.0)
    (tmp_path / bi.QUEUE_LOCK).write_text(json.dumps({"pid": 4242}))
    monkeypatch.setattr(bi.MergeQueue, "_alive", staticmethod(lambda pid: True))
    with pytest.raises(KernelError):
        with q.turn("A"):
            pytest.fail("the lock is held")
    monkeypatch.setattr(bi.MergeQueue, "_alive", staticmethod(lambda pid: pid != 4242))
    with q.turn("A"):
        pass


def test_a_dead_process_drops_out_of_the_queue(tmp_path, monkeypatch):
    q = bi.MergeQueue(tmp_path)
    q.enqueue("A", 1.0)
    monkeypatch.setattr(bi.MergeQueue, "_alive", staticmethod(lambda pid: False))
    assert q.pending() == []


def test_integrate_goes_through_the_queue_and_leaves_it_empty(tmp_path):
    repo, runs, run, rows = setup(tmp_path)
    out = go(repo, runs, rows, runner=pass_runner())
    assert out["result"] == "INTEGRATED"
    events = [json.loads(ln) for ln in (runs / bi.QUEUE_FILE).read_text().splitlines()]
    assert [e["event"] for e in events] == ["ENQUEUED", "DONE"] and events[1]["result"] == "INTEGRATED"
    assert bi.MergeQueue(runs).pending() == [] and not (runs / bi.QUEUE_LOCK).exists()


def test_integrate_blocked_behind_an_earlier_candidate_does_not_cut_in(tmp_path):
    repo, runs, run, rows = setup(tmp_path)
    q = bi.MergeQueue(runs, wait_s=0.2, poll_s=0.05)
    q.enqueue("OTHER", 0.0)  # finished earlier than REQ-1
    before = head(repo)
    with pytest.raises(bi.MergeQueueBusy, match="waited"):  # transient - a KernelError, distinguishable
        go(repo, runs, rows, queue=q)
    assert head(repo) == before
    assert [e["req"] for e in q.pending()] == ["OTHER"]


def test_the_turn_is_not_held_during_premerge_verifier(tmp_path):
    repo, runs, run, rows = setup(tmp_path, priority="P0")
    seen = []

    def runner(cmd, prompt, cwd):
        seen.append((runs / bi.QUEUE_LOCK).exists())
        return json.dumps({"result": '{"verdict": "PASS", "requirement_ids": ["REQ-1"]}',
                           "total_cost_usd": 0.2})

    assert go(repo, runs, rows, runner=runner)["result"] == "INTEGRATED"
    assert seen == [False]


def test_a_clash_between_prepare_and_land_rebases_inside_the_turn(tmp_path):
    repo, runs, run, rows = setup(tmp_path)
    prepared = bi._prepare("REQ-1", repo=repo, runs_dir=runs, ledger_path=repo / "scripts" / "prd_ledger.py",
                           python=sys.executable, rows=rows, runner=boom_runner, dry_run=False, no_ledger=True)
    assert "prepared" in prepared
    write(repo, "calc.py", "def add(a, b):\n    return b + a\n")
    sh(repo, "commit", "-q", "-am", "moved while preparing")
    before = head(repo)
    q = bi.MergeQueue(runs, repo=repo)
    q.enqueue("REQ-1", time.time())
    with q.turn("REQ-1"):
        out = bi._land(prepared["prepared"], repo=repo, runs_dir=runs,
                       ledger_path=repo / "scripts" / "prd_ledger.py", python=sys.executable,
                       no_ledger=True)
    assert out["result"] == "REBASE_REQUIRED" and "calc.py" in out["detail"]
    assert head(repo) == before


def test_a_tree_dirtied_between_prepare_and_land_is_refused_inside_the_turn(tmp_path):
    repo, runs, run, rows = setup(tmp_path)
    prepared = bi._prepare("REQ-1", repo=repo, runs_dir=runs, ledger_path=repo / "scripts" / "prd_ledger.py",
                           python=sys.executable, rows=rows, runner=boom_runner, dry_run=False, no_ledger=True)
    assert "prepared" in prepared
    write(repo, "calc.py", "# the owner's uncommitted work\n")
    before = head(repo)
    q = bi.MergeQueue(runs, repo=repo)
    q.enqueue("REQ-1", time.time())
    with q.turn("REQ-1"):
        with pytest.raises(KernelError, match="tracked changes"):
            bi._land(prepared["prepared"], repo=repo, runs_dir=runs,
                     ledger_path=repo / "scripts" / "prd_ledger.py", python=sys.executable, no_ledger=True)
    assert head(repo) == before
    assert (repo / "calc.py").read_text(encoding="utf-8") == "# the owner's uncommitted work\n"


def test_prepare_does_not_refuse_a_tree_another_landing_is_dirtying(tmp_path):
    repo, runs, run, rows = setup(tmp_path)
    write(repo, "calc.py", "# another run is mid-landing\n")
    prepared = bi._prepare("REQ-1", repo=repo, runs_dir=runs, ledger_path=repo / "scripts" / "prd_ledger.py",
                           python=sys.executable, rows=rows, runner=boom_runner, dry_run=False, no_ledger=True)
    assert "prepared" in prepared


def test_integrate_many_reports_one_failure_without_dropping_the_others(tmp_path):
    repo, base_commit = make_repo(tmp_path, {"seed.py": "x = 1\n"})
    base, _ = base_commit.split()
    runs = tmp_path / "runs"
    runs.mkdir()
    add_run(repo, runs, "REQ-A", base, {"one.py": "def one():\n    return 1\n",
                                        "tests/test_one.py": "from one import one\n\n\ndef test_one():\n    assert one() == 1\n"})
    bad = add_run(repo, runs, "REQ-B", base, {"two.py": "def two():\n    return 2\n"})
    (runs / "REQ-B.json").write_text(json.dumps(dict(bad, state="FAILED")), encoding="utf-8")
    rows = [{"id": "REQ-A", "priority": "P1", "summary": "one", "acceptance": "one"},
            {"id": "REQ-B", "priority": "P1", "summary": "two", "acceptance": "two"}]
    out = bi.integrate_many(["REQ-B", "REQ-A"], repo=repo, runs_dir=runs,
                            ledger_path=repo / "scripts" / "prd_ledger.py", python=sys.executable,
                            rows=rows, no_ledger=True)
    assert [r["requirement"] for r in out] == ["REQ-B", "REQ-A"]
    assert out[0]["result"] == "ERROR" and "not DONE" in out[0]["detail"]
    assert out[1]["result"] == "INTEGRATED"


@pytest.mark.parametrize("files, priority, result", [
    ({"tests/test_bad.py": "from calc import add\n\n\ndef test_bad():\n    assert add(2, 3) == 6\n"}, "P1", "NOT_GREEN"),
    (GOOD, "P0", "VERIFIER_FAIL"),
])
def test_terminal_prepare_results_do_not_enqueue(tmp_path, files, priority, result):
    repo, runs, run, rows = setup(tmp_path, files, priority=priority)
    out = go(repo, runs, rows, runner=pass_runner("FAIL"))
    assert out["result"] == result
    assert not (runs / bi.QUEUE_FILE).exists()


def test_two_integrations_prepare_in_parallel_but_merge_serially(tmp_path, monkeypatch):
    repo, base_commit = make_repo(tmp_path, {"seed.py": "x = 1\n"})
    base, _ = base_commit.split()
    runs = tmp_path / "runs"
    runs.mkdir()
    add_run(repo, runs, "REQ-A", base, {"one.py": "def one():\n    return 1\n",
                                        "tests/test_one.py": "from one import one\n\n\ndef test_one():\n    assert one() == 1\n"})
    add_run(repo, runs, "REQ-B", base, {"two.py": "def two():\n    return 2\n",
                                        "tests/test_two.py": "from two import two\n\n\ndef test_two():\n    assert two() == 2\n"})
    rows = [{"id": "REQ-A", "priority": "P0", "summary": "one", "acceptance": "one"},
            {"id": "REQ-B", "priority": "P0", "summary": "two", "acceptance": "two"}]
    verifier_windows, merge_events, active_merges = [], [], 0
    lock = threading.Lock()
    both_verifiers = threading.Event()
    real_merge = bi.merge

    def runner(cmd, prompt, cwd):
        start = time.monotonic()
        with lock:
            verifier_windows.append((start, 0.0))
            if len(verifier_windows) == 2:
                both_verifiers.set()
        both_verifiers.wait(5)
        time.sleep(0.4)
        end = time.monotonic()
        with lock:
            for n, (seen, _) in enumerate(verifier_windows):
                if seen == start:
                    verifier_windows[n] = (start, end)
                    break
        return json.dumps({"result": '{"verdict": "PASS", "requirement_ids": ["REQ"]}'})

    def merge_probe(*args, **kw):
        nonlocal active_merges
        with lock:
            active_merges += 1
            merge_events.append(("start", active_merges))
        try:
            return real_merge(*args, **kw)
        finally:
            with lock:
                merge_events.append(("end", active_merges))
                active_merges -= 1

    monkeypatch.setattr(bi, "merge", merge_probe)
    out = bi.integrate_many(["REQ-A", "REQ-B"], repo=repo, runs_dir=runs,
                            ledger_path=repo / "scripts" / "prd_ledger.py", python=sys.executable,
                            rows=rows, runner=runner, no_ledger=True)
    assert [r["requirement"] for r in out] == ["REQ-A", "REQ-B"]
    assert all(r["result"] == "INTEGRATED" for r in out)
    assert verifier_windows[0][0] < verifier_windows[1][1] and verifier_windows[1][0] < verifier_windows[0][1]
    assert [n for event, n in merge_events if event == "start"] == [1, 1]


def test_premerge_slots_limit_parallel_prepare_and_replace_dead_pid(tmp_path, monkeypatch):
    counts, active = [], 0
    lock = threading.Lock()

    def hold_slot():
        nonlocal active
        with bi.premerge_slot(tmp_path):
            with lock:
                active += 1
                counts.append(active)
            time.sleep(0.2)
            with lock:
                active -= 1

    threads = [threading.Thread(target=hold_slot) for _ in range(3)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert max(counts) == bi.PREMERGE_SLOTS
    (tmp_path / "premerge.0.lock").write_text(json.dumps({"pid": 4242}), encoding="utf-8")
    monkeypatch.setattr(bi.MergeQueue, "_alive", staticmethod(lambda pid: pid != 4242))
    with bi.premerge_slot(tmp_path):
        holder = json.loads((tmp_path / "premerge.0.lock").read_text(encoding="utf-8"))["pid"]
        assert holder == os.getpid()
    assert not (tmp_path / "premerge.0.lock").exists()


def test_integrate_many_returns_results_in_input_order(tmp_path):
    repo, base_commit = make_repo(tmp_path, {"seed.py": "x = 1\n"})
    base, _ = base_commit.split()
    runs = tmp_path / "runs"
    runs.mkdir()
    add_run(repo, runs, "REQ-A", base, {"one.py": "def one():\n    return 1\n",
                                        "tests/test_one.py": "from one import one\n\n\ndef test_one():\n    assert one() == 1\n"})
    add_run(repo, runs, "REQ-B", base, {"two.py": "def two():\n    return 2\n",
                                        "tests/test_two.py": "from two import two\n\n\ndef test_two():\n    assert two() == 2\n"})
    rows = [{"id": "REQ-A", "priority": "P1", "summary": "one", "acceptance": "one"},
            {"id": "REQ-B", "priority": "P1", "summary": "two", "acceptance": "two"}]
    out = bi.integrate_many(["REQ-B", "REQ-A"], repo=repo, runs_dir=runs,
                            ledger_path=repo / "scripts" / "prd_ledger.py", python=sys.executable,
                            rows=rows, no_ledger=True)
    assert [r["requirement"] for r in out] == ["REQ-B", "REQ-A"]


def _killed_landing(tmp_path):
    """main as a killed integrator leaves it: the run's pick on HEAD, marked by a dead process."""
    repo, runs, run, rows = setup(tmp_path)
    pre = head(repo)
    sh(repo, "cherry-pick", run["commit"])
    bi.mark_landing(runs, "REQ-1", pre, head(repo), pid=4242)
    return repo, runs, pre


def test_a_turn_takes_back_a_pick_a_killed_integrator_left_ungated(tmp_path, monkeypatch):
    # 2026-10-09: the program landing BRAIN-001 was killed mid-gate; its pick (b71a2ee) stayed
    # on main, un-gated and out of the ledger, under five later integrations.
    repo, runs, pre = _killed_landing(tmp_path)
    monkeypatch.setattr(bi.MergeQueue, "_alive", staticmethod(lambda pid: pid != 4242))
    q = bi.MergeQueue(runs, repo=repo)
    q.enqueue("NEXT", 1.0)
    q.enqueue("REQ-1", 2.0)  # the killed run, queued again
    with q.turn("NEXT"):
        assert head(repo) == pre
    assert not (runs / bi.LANDING_FILE).exists()
    ev = [json.loads(ln) for ln in (runs / bi.QUEUE_FILE).read_text().splitlines()][-1]
    assert ev["event"] == "LANDING_RECOVERED" and ev["outcome"] == "RESET" and ev["pre_head"] == pre
    assert [e["req"] for e in q.pending()] == ["NEXT", "REQ-1"]  # the recovery drops no entry


def test_a_landing_main_has_moved_past_is_left_and_dropped(tmp_path, monkeypatch):
    repo, runs, pre = _killed_landing(tmp_path)
    write(repo, "later.py", "x = 1\n")
    sh(repo, "add", "later.py")
    sh(repo, "commit", "-q", "-m", "built on top")
    moved = head(repo)
    monkeypatch.setattr(bi.MergeQueue, "_alive", staticmethod(lambda pid: pid != 4242))
    assert bi.MergeQueue(runs, repo=repo).recover_landing() == "LEFT"
    assert head(repo) == moved and not (runs / bi.LANDING_FILE).exists()


def test_a_landing_whose_process_lives_is_not_touched(tmp_path, monkeypatch):
    repo, runs, pre = _killed_landing(tmp_path)
    pick = head(repo)
    monkeypatch.setattr(bi.MergeQueue, "_alive", staticmethod(lambda pid: True))
    assert bi.MergeQueue(runs, repo=repo).recover_landing() == ""
    assert head(repo) == pick and (runs / bi.LANDING_FILE).exists()


def test_the_pick_is_marked_while_its_gate_runs_and_cleared_once_ledgered(tmp_path, monkeypatch):
    repo, runs, run, rows = setup(tmp_path)
    pre, seen = head(repo), {}

    def gate(python, cwd, tests):
        seen.update(json.loads((runs / bi.LANDING_FILE).read_text()))
        return 0, ["ok"]

    monkeypatch.setattr(bi, "gate_run", gate)
    out = go(repo, runs, rows, runner=pass_runner())
    assert out["result"] == "INTEGRATED"
    assert seen["req"] == "REQ-1" and seen["pre_head"] == pre and seen["commit"] == out["integrated_commit"]
    assert not (runs / bi.LANDING_FILE).exists()


# ---------------------------------------------------------------- wide impact

def test_a_wide_affected_set_is_recorded_with_its_chunk_count(tmp_path, monkeypatch):
    repo, runs, run, rows = setup(tmp_path)
    many = [f"tests/test_w{i}.py" for i in range(bi.WIDE_IMPACT_FILES + 1)]
    for t in many:
        write(repo, t, "def test_x():\n    pass\n")
    monkeypatch.setattr(bi, "affected", lambda run, extra, repo=None: many)
    monkeypatch.setattr(bi, "gate_run", lambda python, cwd, tests: (0, ["ok"]))
    # gate_workers asks the host governor for memory slots: pin it, or the count follows the
    # machine's free memory (2026-10-05: it failed MODEL-012's gate with more memory free).
    monkeypatch.setattr(bi, "gate_slots", lambda *a, **kw: (1, "pinned"))
    out = go(repo, runs, rows, runner=pass_runner())
    assert out["impact"] == "WIDE" and out["gate_chunks"] == 2 and out["gate_workers"] == 1


def test_a_narrow_set_is_not_wide(tmp_path):
    repo, runs, run, rows = setup(tmp_path)
    out = go(repo, runs, rows, runner=pass_runner())
    assert out["impact"] == "NORMAL" and out["gate_chunks"] >= 1


def test_gate_workers_never_exceed_the_gate_slots(monkeypatch):
    monkeypatch.setattr(bi, "gate_slots", lambda: (1, "one slot"))
    assert bi.gate_workers(500) == 1
    monkeypatch.setattr(bi, "gate_slots", lambda: (3, "ok"))
    assert bi.gate_workers(500) == 3 and bi.gate_workers(bi.WIDE_IMPACT_FILES) == 1
    assert bi.gate_workers(bi.WIDE_IMPACT_FILES + 1) == 2  # only two chunks exist
    monkeypatch.setattr(bi, "gate_slots", lambda: (0, "pressure"))
    assert bi.gate_workers(500) == 1  # the gate still has to run


class _Limits:
    max_workers = 3

    @staticmethod
    def ram_floor(total_gb):
        return 1.0


class _Gov:
    """A governor reading of the shape admit() uses: level, sample with available RAM and commit."""
    thresholds = _Limits()

    def __init__(self, level, available_gb, commit_gb=None):
        sample = SimpleNamespace(ram_available_gb=available_gb, total_gb=lambda: 16.0)
        if commit_gb is not None:
            sample.commit_available_gb = commit_gb
        self._p = SimpleNamespace(level=level, sample=sample)

    def pressure(self):
        return self._p


def test_gate_slots_size_chunks_by_a_pytest_chunk_not_a_build_worker(tmp_path, monkeypatch):
    # 2026-10-12: sized by a build worker's predicted peak, a WIDE gate ran its chunks one after
    # another for 50 minutes with 3 GB free. Reserve here is ram_floor 1.0 + 1.0 = 2.0 GB.
    monkeypatch.setattr(build_kernel, "leased_gb", lambda runs_dir=None: 0.0)
    assert bi.gate_slots(_Gov("NORMAL", 2.0 + 3 * bi.GATE_CHUNK_GB), tmp_path)[0] == 3
    assert bi.gate_slots(_Gov("NORMAL", 2.0 + 2.5 * bi.GATE_CHUNK_GB), tmp_path)[0] == 2
    assert bi.gate_slots(_Gov("NORMAL", 99.0), tmp_path)[0] == bi.GATE_MAX_WORKERS
    assert bi.gate_slots(_Gov("NORMAL", 2.5), tmp_path)[0] == 1  # never below one: the gate has to run


def test_gate_slots_respect_pressure_commit_and_live_leases(tmp_path, monkeypatch):
    monkeypatch.setattr(build_kernel, "leased_gb", lambda runs_dir=None: 0.0)
    assert bi.gate_slots(_Gov("HIGH", 10.0), tmp_path)[0] == 1
    assert bi.gate_slots(_Gov("CRITICAL", 10.0), tmp_path)[0] == 1
    assert bi.gate_slots(_Gov("NORMAL", 10.0, commit_gb=2.0 + bi.GATE_CHUNK_GB), tmp_path)[0] == 1
    monkeypatch.setattr(build_kernel, "leased_gb", lambda runs_dir=None: 2 * bi.GATE_CHUNK_GB)
    assert bi.gate_slots(_Gov("NORMAL", 2.0 + 3 * bi.GATE_CHUNK_GB), tmp_path)[0] == 1


def test_needs_verifier_goes_through_agent_utility():
    assert bi.needs_verifier({"priority": "P0"}, []) is True
    assert bi.needs_verifier({"priority": "P1"}, ["friday/secrets_x.py"]) is True
    assert bi.needs_verifier({"priority": "P1"}, ["calc.py"]) is False


def test_base_must_be_an_ancestor(tmp_path):
    repo, runs, run, rows = setup(tmp_path)
    # the worker commit lives on its own branch, so it is not an ancestor of main's HEAD
    with pytest.raises(KernelError, match="not an ancestor"):
        bi.collision(dict(run, base_sha=run["commit"]), repo)


def test_cherry_pick_conflict_is_aborted(tmp_path):
    repo, runs, run, rows = setup(tmp_path, {"calc.py": "def add(a, b):\n    return a * b\n",
                                              "tests/test_twice.py": GOOD["tests/test_twice.py"]})
    write(repo, "calc.py", "def add(a, b):\n    return b + a\n")
    sh(repo, "commit", "-q", "-am", "moved")
    before = head(repo)
    # the record under-reports files_changed, so the collision gate passes and the pick itself conflicts
    out = bi.merge(dict(run, files_changed=["tests/test_twice.py"]), [], repo, sys.executable)
    assert out["result"] == "CONFLICT"
    assert head(repo) == before and sh(repo, "status", "--porcelain") == ""
    assert not (repo / ".git" / "CHERRY_PICK_HEAD").exists()


# ---------------------------------------------------------------- acceptance fast path

def test_no_test_blocks(tmp_path):
    repo, runs, run, rows = setup(tmp_path, {"calc.py": GOOD["calc.py"]})
    out = go(repo, runs, rows)
    assert out["result"] == "NO_TEST" and "twice" not in (repo / "calc.py").read_text()


def test_not_red_when_the_test_already_passes_at_base(tmp_path):
    repo, runs, run, rows = setup(tmp_path, {"tests/test_add.py": "from calc import add\n\n\ndef test_add():\n    assert add(2, 3) == 5\n"})
    before = head(repo)
    out = go(repo, runs, rows)
    assert out["result"] == "NOT_RED" and head(repo) == before


def test_not_green_when_the_test_fails_with_the_commit(tmp_path):
    repo, runs, run, rows = setup(tmp_path, {"tests/test_bad.py": "from calc import add\n\n\ndef test_bad():\n    assert add(2, 3) == 6\n"})
    out = go(repo, runs, rows)
    # the test fails at the base too, so RED holds and the commit is the blocker
    assert out["result"] == "NOT_GREEN"
    assert out["red_green"]["red_kind"] == "assertion"


def test_assertion_red_is_recorded(tmp_path):
    files = dict(GOOD, **{"tests/test_twice.py": "import calc\n\n\ndef test_twice():\n    assert calc.twice(2) == 4\n"})
    repo, runs, run, rows = setup(tmp_path, files)
    rg = bi.red_green(run, bi.commit_test_files(run, repo), repo, sys.executable)
    assert rg["result"] == "OK" and rg["red_kind"] == "assertion"
    # 2026-10-08: a VERIFIED write cited the test FILE and the ledger check refused it;
    # the tests that failed at the base are the ones that prove the change.
    assert rg["proving"] == ["tests/test_twice.py::test_twice"]


def test_the_ledger_cites_named_tests_only():
    assert bi.named_tests(["tests\\t.py::test_a[1 2]", "tests/t.py::test_a[3]", "tests/t.py", "tests/u.py::test_b"]) == [
        "tests/t.py::test_a", "tests/u.py::test_b"]
    assert bi.named_tests(["tests/t.py::test_%d" % i for i in range(20)], limit=3) == [
        "tests/t.py::test_0", "tests/t.py::test_1", "tests/t.py::test_2"]


# ---------------------------------------------------------------- merge fast path

BREAKING = {"calc.py": "def add(a, b):\n    return a + b + 1\n\n\ndef twice(x):\n    return x * 2\n",
            "tests/test_twice.py": GOOD["tests/test_twice.py"]}


def test_failing_affected_tests_reset_to_pre_head(tmp_path):
    repo, runs, run, rows = setup(tmp_path, BREAKING)
    before = head(repo)
    out = go(repo, runs, rows)
    assert out["result"] == "FAILED_INTEGRATION"
    assert head(repo) == before and "add(a, b):\n    return a + b\n" in (repo / "calc.py").read_text()
    assert out["ledger_status"] == ""
    # The repair worker is shown what broke, not just that something did.
    assert any("tests/test_old.py::test_old" in f for f in out["failures"])


def test_live_tests_are_not_part_of_the_integration_gate(tmp_path):
    # 2026-10-02: CORE-001 was rejected twice because an affected @live test
    # needs a running Hermes gateway; the project's own gate is
    # -m "not live and not slow".
    live = {"tests/test_net.py": "import pytest\nfrom calc import add\n\n\n@pytest.mark.live\n"
                                 "def test_net():\n    assert False\n"}
    repo, runs, run, rows = setup(tmp_path, base_extra=live)
    out = go(repo, runs, rows)
    assert out["result"] == "INTEGRATED"


def test_a_failure_that_predates_the_change_is_recorded_not_blamed(tmp_path):
    broken = {"tests/test_broken.py": "from calc import add\n\n\ndef test_broken():\n    assert add(1, 1) == 3\n"}
    repo, runs, run, rows = setup(tmp_path, base_extra=broken)
    out = go(repo, runs, rows)
    assert out["result"] == "INTEGRATED"
    assert out["pre_existing_failures"] == ["tests/test_broken.py::test_broken"]


def test_reset_is_skipped_when_head_parent_is_not_pre_head(tmp_path, monkeypatch):
    repo, runs, run, rows = setup(tmp_path, BREAKING)
    real = bi.git

    def fake(r, *args, **kw):
        if args == ("rev-parse", "HEAD^"):
            return subprocess.CompletedProcess(args, 0, stdout="deadbeef\n", stderr="")
        return real(r, *args, **kw)
    monkeypatch.setattr(bi, "git", fake)
    out = bi.merge(run, bi.commit_test_files(run, repo), repo, sys.executable, runs_dir=runs)
    assert out["result"] == "FAILED_INTEGRATION" and "NOT reset" in out["reset"]
    assert head(repo) != out["pre_head"]
    assert not (runs / bi.LANDING_FILE).exists()  # reported for a person, not left for a turn to undo


# ---------------------------------------------------------------- review economics

@pytest.mark.parametrize("row,files,expected", [
    ({"priority": "P0"}, ["x.py"], True),
    ({"priority": "P1"}, ["friday/policy.py"], True),
    ({"priority": "P1"}, ["friday\\honesty.py"], True),
    ({"priority": "P2"}, ["friday/secrets_store.py"], True),
    ({"priority": "P2"}, ["friday/credential_broker.py"], True),
    ({"priority": "P1"}, ["friday/planner.py", "tests/test_x.py"], False),
])
def test_needs_verifier(row, files, expected):
    assert bi.needs_verifier(row, files) is expected


def test_verifier_not_run_when_not_required(tmp_path):
    repo, runs, run, rows = setup(tmp_path)
    assert go(repo, runs, rows, runner=boom_runner)["result"] == "INTEGRATED"


def test_verifier_required_and_failing_blocks_integration(tmp_path):
    repo, runs, run, rows = setup(tmp_path, priority="P0")
    before = head(repo)
    out = go(repo, runs, rows, runner=pass_runner("FAIL"))
    assert out["result"] == "VERIFIER_FAIL" and out["verifier"]["verdict"] == "FAIL" and head(repo) == before


def test_verifier_required_and_passing_integrates(tmp_path):
    repo, runs, run, rows = setup(tmp_path, priority="P0")
    out = go(repo, runs, rows, runner=pass_runner("PASS"))
    assert out["result"] == "INTEGRATED" and out["ledger_status"] == "VERIFIED"


def test_verifier_that_cannot_run_is_insufficient(tmp_path):
    repo, runs, run, rows = setup(tmp_path, priority="P0")

    def missing(cmd, prompt, cwd):
        raise FileNotFoundError("claude")
    out = go(repo, runs, rows, runner=missing)
    assert out["result"] == "VERIFIER_FAIL" and out["verifier"]["verdict"] == "INSUFFICIENT_EVIDENCE"


def test_a_large_change_gets_a_verifier_sized_to_read_it():
    # 2026-10-03: CTX-002's 19-file, 3,774-line change got the fixed 15-turn verifier,
    # which "ran out of turns before it reached any code" (INSUFFICIENT_EVIDENCE).
    files = ([f"friday/m{i}.py" for i in range(8)] + [f"scripts/s{i}.py" for i in range(3)]
             + ["third_party/UPSTREAM_LOCK.json"] + [f"tests/test_{i}.py" for i in range(4)]
             + ["docs/plans/x/PLAN.md", "docs/plans/x/bench.json", "docs/plans/x/labels.json"])
    cmd = bi.verifier_command(1.8, files, exe="claude")
    assert int(cmd[cmd.index("--max-turns") + 1]) == 15 + 2 * (12 - 3)
    assert cmd[cmd.index("--max-budget-usd") + 1] == "1.80"  # 12 code files at $0.15
    small = bi.verifier_command(1.0, ["friday/a.py", "tests/test_a.py"], exe="claude")
    assert small[small.index("--max-turns") + 1] == "15" and small[small.index("--max-budget-usd") + 1] == "1.00"
    huge = bi.verifier_command(9.0, [f"friday/m{i}.py" for i in range(60)], exe="claude")
    assert int(huge[huge.index("--max-turns") + 1]) == build_kernel_mod().VERIFIER_MAX_TURNS
    assert float(huge[huge.index("--max-budget-usd") + 1]) == build_kernel_mod().VERIFIER_MAX_USD


def build_kernel_mod():
    import build_kernel
    return build_kernel


def test_a_partial_step_is_judged_against_its_slice_not_the_whole_acceptance(tmp_path):
    # 2026-10-04/05: CTX-002 and MODEL-012 slices were failed for acceptance items their own
    # next action deferred to later slices ("this is ledger slice 2/3/4"), three times.
    repo, runs, run, rows = setup(tmp_path)
    partial = bi.verifier_prompt(rows[0], {**run, "evidence": {**run["evidence"], "acceptance_met": False}},
                                 {"result": "OK"}, repo)
    assert "integrates as PARTIAL" in partial and "later slices are not failures" in partial
    full = bi.verifier_prompt(rows[0], run, {"result": "OK"}, repo)  # setup's evidence claims acceptance_met
    assert "judge the change against all of it" in full and "later slices" not in full


def test_verifier_prompt_and_command(tmp_path):
    repo, runs, run, rows = setup(tmp_path)
    prompt = bi.verifier_prompt(rows[0], run, {"result": "OK"}, repo)
    assert "twice doubles" in prompt and "adds twice" in prompt and "calc.py" in prompt and "def twice" in prompt
    # A quarter of the implementer's cost, but never below the floor: the
    # verifier's own system prompt costs ~27k tokens before it reads a line,
    # and a $0.25 cap ran out before BROW-001's verifier could answer.
    cmd = bi.verifier_command(8.0, exe="claude")
    assert cmd[cmd.index("--max-budget-usd") + 1] == "2.00"
    low = bi.verifier_command(0.3, exe="claude")
    assert low[low.index("--max-budget-usd") + 1] == f"{bi.VERIFIER_FLOOR_USD:.2f}" and bi.VERIFIER_FLOOR_USD >= 1.0
    assert cmd[cmd.index("--max-turns") + 1] == "15" and "--strict-mcp-config" in cmd
    assert cmd[cmd.index("--allowedTools") + 1] == bi.VERIFIER_TOOLS and "Edit" not in bi.VERIFIER_TOOLS
    # Opus adjudicates trust-boundary changes; other P0 work gets a fresh Sonnet.
    assert cmd[cmd.index("--model") + 1] == "sonnet"
    sec = bi.verifier_command(1.0, files=["friday/policy.py"], exe="claude")
    assert sec[sec.index("--model") + 1] == "opus"


def test_claude_verifier_command_regression(monkeypatch):
    monkeypatch.setattr(bi.shutil, "which", lambda name: None)
    cmd = bi.verifier_command(1.0, ["friday/a.py"], exe="claude")
    assert cmd == [
        "claude", "-p", "--model", "sonnet", "--allowedTools", bi.VERIFIER_TOOLS,
        "--strict-mcp-config", "--settings", '{"disableAllHooks": true}',
        "--max-turns", "15", "--max-budget-usd", "1.00", "--output-format", "json",
    ]


def test_verifier_command_falls_back_to_codex_when_claude_is_out(monkeypatch, tmp_path):
    health = tmp_path / "health.json"
    monkeypatch.setattr(bi.P, "HEALTH_FILE", health)
    monkeypatch.setattr(bi.P, "CODEX_CONFIG", tmp_path / "config.toml")
    (tmp_path / "config.toml").write_text('model = "gpt-5"\n', encoding="utf-8")
    monkeypatch.setattr(bi.P, "codex_on_plan", lambda: True)
    bi.P.mark("claude", bi.P.QUOTA, until=9999999999, path=health)
    cmd = bi.verifier_command(1.0)
    assert cmd == [build_kernel_mod().VENV_PY, str(bi.ROOT / "scripts" / "build_worker.py"),
                   "codex", "gpt-5", "--verify", "--read-only"]


def test_verifier_command_raises_when_no_route_can_verify(monkeypatch, tmp_path):
    health = tmp_path / "health.json"
    monkeypatch.setattr(bi.P, "HEALTH_FILE", health)
    monkeypatch.setattr(bi.P, "codex_on_plan", lambda: False)
    bi.P.mark("claude", bi.P.QUOTA, until=9999999999, path=health)
    with pytest.raises(bi.VerifierUnavailable):
        bi.verifier_command(1.0)


def test_tampered_verifier_receipt_is_never_pass():
    out = json.dumps({"type": "result", "subtype": "error_tampered", "is_error": True,
                      "result": '{"verdict":"PASS"}', "total_cost_usd": 0.0})
    receipt = bi.parse_receipt(out)
    assert receipt["verdict"] == "INSUFFICIENT_EVIDENCE"
    assert any("error_tampered" in c for c in receipt["failing_criteria"])


def test_patch_is_capped(tmp_path):
    big = {"calc.py": GOOD["calc.py"] + "# " + "x" * 100 + "\n" * 1, "tests/test_twice.py": GOOD["tests/test_twice.py"],
           "big.txt": "y" * (bi.PATCH_CAP * 2)}
    repo, runs, run, rows = setup(tmp_path, big)
    prompt = bi.verifier_prompt(rows[0], run, {}, repo)
    assert prompt.count("y") <= bi.PATCH_CAP + 50
    # 2026-10-02: CORE-001's verifier judged a cut-off patch without reading
    # the rest. It is told the patch is truncated and which files to read.
    assert "TRUNCATED" in prompt and "big.txt" in prompt.split("TRUNCATED", 1)[1]


def test_the_verifier_sees_the_owner_rulings_in_the_next_action(tmp_path):
    # CORE-001's verifier called the case the owner had ruled on "a policy call
    # the material does not settle": the ruling lives in the row's next action.
    repo, runs, run, rows = setup(tmp_path)
    rows[0]["next_action"] = "OWNER RULING 2026-10-02: derive a check from the acceptance"
    prompt = bi.verifier_prompt(rows[0], run, {}, repo)
    assert "OWNER RULING 2026-10-02: derive a check from the acceptance" in prompt


@pytest.mark.parametrize("text,verdict", [
    ('{"verdict": "PASS", "requirement_ids": ["A"], "failing_criteria": [], "evidence_refs": ["f:1"]}', "PASS"),
    (json.dumps({"result": 'noise {"a": 1} then {"verdict": "FAIL", "failing_criteria": ["c"]}'}), "FAIL"),
    ("total garbage {not json", "INSUFFICIENT_EVIDENCE"),
    ('{"verdict": "MAYBE"}', "INSUFFICIENT_EVIDENCE"),
    ("", "INSUFFICIENT_EVIDENCE"),
])
def test_parse_receipt(text, verdict):
    receipt = bi.parse_receipt(text)
    assert receipt["verdict"] == verdict
    assert {"verdict", "requirement_ids", "failing_criteria", "evidence_refs"} <= set(receipt)


def test_a_verifier_that_ran_out_of_budget_says_so_and_its_cost_is_kept():
    out = json.dumps({"type": "result", "subtype": "error_max_budget_usd", "is_error": True,
                      "result": None, "total_cost_usd": 0.31, "errors": ["Reached maximum budget ($0.25)"]})
    receipt = bi.parse_receipt(out)
    assert receipt["verdict"] == "INSUFFICIENT_EVIDENCE" and receipt["cost_usd"] == 0.31
    assert receipt["stop"] == "error_max_budget_usd"
    assert any("error_max_budget_usd" in c for c in receipt["failing_criteria"])
    ok = bi.parse_receipt(json.dumps({"subtype": "success", "total_cost_usd": 0.4,
                                      "result": '{"verdict": "PASS"}'}))
    assert ok["verdict"] == "PASS" and ok["cost_usd"] == 0.4 and ok["stop"] == "success"


# ---------------------------------------------------------------- ledger

def test_status_is_partial_with_gaps(tmp_path):
    repo, runs, run, rows = setup(tmp_path, acceptance_met=False)
    out = go(repo, runs, rows)
    assert out["result"] == "INTEGRATED" and out["ledger_status"] == "PARTIAL"
    assert "GAPS: implementer did not claim acceptance met" in (repo / "scripts" / "prd_ledger.py").read_text()


def test_a_verified_row_keeps_its_curated_citations_and_gains_new_files():
    # 2026-10-08: MCP-001 went VERIFIED and its twelve firewall citations were replaced
    # with the one file the slice touched; the test that follows them broke.
    row = {"files": ["friday/mcp_firewall.py:149,225", "agent_friday.py:739,1330"]}
    assert bi.cited_files(row, ["agent_friday.py", "friday\\new.py", "tests/test_x.py"]) == [
        "friday/mcp_firewall.py:149,225", "agent_friday.py:739,1330", "friday/new.py"]
    assert bi.cited_files({}, ["a.py", "tests/t.py"]) == ["a.py"]


def test_decide_status_needs_every_proof():
    run = {"evidence": {"acceptance_met": True, "gaps": []}}
    ok, merged = {"result": "OK"}, {"result": "INTEGRATED"}
    assert bi.decide_status(run, ok, merged, False, None)[0] == "VERIFIED"
    assert bi.decide_status(run, ok, merged, True, {"verdict": "PASS"})[0] == "VERIFIED"
    assert bi.decide_status(run, ok, merged, True, {"verdict": "FAIL"})[0] == "PARTIAL"
    assert bi.decide_status(run, {"result": "NOT_RED"}, merged, False, None)[0] == "PARTIAL"
    assert bi.decide_status(run, ok, {"result": "FAILED_INTEGRATION"}, False, None)[0] == "PARTIAL"
    assert bi.decide_status({"evidence": {"acceptance_met": True, "gaps": ["g"]}}, ok, merged, False, None)[0] == "PARTIAL"


def test_no_ledger_skips_the_entry(tmp_path):
    repo, runs, run, rows = setup(tmp_path)
    before = (repo / "scripts" / "prd_ledger.py").read_bytes()
    out = go(repo, runs, rows, no_ledger=True)
    assert out["result"] == "INTEGRATED" and out["ledger_status"] == ""
    assert (repo / "scripts" / "prd_ledger.py").read_bytes() == before


def test_append_progress_adds_one_entry_that_loads(tmp_path):
    path = tmp_path / "prd_ledger.py"
    path.write_text(LEDGER_SRC, encoding="utf-8")
    bi.append_progress(path, "REQ-1", "VERIFIED", "done it's \"quoted\"", "", evidence="RED/GREEN 'x'",
                       extra={"tests": ["tests/test_x.py"]})
    module = bi.load_ledger(path)
    assert [rid for rid, _ in module.PROGRESS_V7] == ["REQ-0", "REQ-1"]
    row = {r["id"]: r for r in module.apply_progress(module._load_v7(), module.PROGRESS_V7)}["REQ-1"]
    assert row["status"] == "VERIFIED" and row["current_implementation"] == "done it's \"quoted\""
    assert row["evidence"] == "RED/GREEN 'x'"


def test_append_progress_keeps_crlf(tmp_path):
    path = tmp_path / "prd_ledger.py"
    path.write_bytes(LEDGER_SRC.replace("\n", "\r\n").encode())
    bi.append_progress(path, "REQ-1", "PARTIAL", "x", "GAPS: y")
    raw = path.read_bytes()
    assert raw.count(b"\n") == raw.count(b"\r\n")


def test_append_progress_restores_the_file_when_it_does_not_load(tmp_path):
    path = tmp_path / "prd_ledger.py"
    path.write_bytes(LEDGER_SRC.encode())
    before = path.read_bytes()
    with pytest.raises(KernelError, match="restored"):
        bi.append_progress(path, "REQ-1", "NOT_A_STATUS", "x", "")
    assert path.read_bytes() == before
    with pytest.raises(KernelError, match="restored"):
        bi.append_progress(path, "REQ-UNKNOWN", "VERIFIED", "x", "", evidence="e", extra={"tests": ["t.py"]})
    assert path.read_bytes() == before


# ---------------------------------------------------------------- chunked gate

class _Done:
    def __init__(self, rc):
        self.returncode, self.stdout = rc, ""


def test_gate_run_chunks_the_tests_and_aggregates(monkeypatch, tmp_path):
    seen = []
    codes = iter([0, 5, 1, 0])

    def fake_run(cmd, **kw):
        seen.append(cmd[cmd.index("--") + 1 + len(bi.GATE):])
        assert kw["timeout"] == bi.TEST_S
        # A hung test fails by name under the per-test limit; the chunk limit is only a backstop.
        assert f"--timeout={bi.GATE_TEST_S}" in cmd and bi.TEST_S > bi.GATE_TEST_S
        return _Done(next(codes))

    monkeypatch.setattr(bi.subprocess, "run", fake_run)
    monkeypatch.setattr(bi, "gate_workers", lambda n: 1)  # the host's slots must not reorder chunks
    tests = [f"tests/test_{i}.py" for i in range(bi.GATE_CHUNK * 3 + 1)]
    rc, _ = bi.gate_run("py", tmp_path, tests)
    assert [len(c) for c in seen] == [bi.GATE_CHUNK] * 3 + [1]
    assert sum(seen, []) == tests
    assert rc == 1  # one failing chunk fails the run
    codes = iter([0, 5])
    assert bi.gate_run("py", tmp_path, tests[:bi.GATE_CHUNK + 1])[0] == 0


def test_a_chunk_timeout_is_a_return_value_naming_the_chunk(monkeypatch, tmp_path):
    def fake_run(cmd, **kw):
        raise subprocess.TimeoutExpired(cmd, kw["timeout"])

    monkeypatch.setattr(bi.subprocess, "run", fake_run)
    rc, lines = bi.gate_run("py", tmp_path, [f"t{i}.py" for i in range(bi.GATE_CHUNK + 1)])
    assert rc == bi.TIMEOUT and "chunk 1/2" in lines[-1]


def test_merge_resets_the_pick_when_a_chunk_outlives_the_timeout(tmp_path, monkeypatch):
    repo, runs, run, rows = setup(tmp_path)
    slow = tmp_path / "slow_brief.py"
    slow.write_text("import time\ntime.sleep(60)\n", encoding="utf-8")
    monkeypatch.setattr(bi, "PYTEST_BRIEF", slow)
    monkeypatch.setattr(bi, "TEST_S", 1)
    before = head(repo)
    merged = bi.merge(run, ["tests/test_twice.py"], repo, sys.executable, runs_dir=runs)
    assert merged["result"] == "TEST_TIMEOUT" and merged["reset"] == "reset to pre_head"
    assert head(repo) == before and "chunk 1/1" in merged["detail"]
    assert merged["affected"] and not (runs / bi.LANDING_FILE).exists()


def test_integrate_records_a_test_timeout_with_detail_and_affected(tmp_path, monkeypatch):
    repo, runs, run, rows = setup(tmp_path)
    monkeypatch.setattr(bi, "gate_run", lambda python, cwd, tests: (bi.TIMEOUT, ["TIMEOUT: chunk 2/3 (40 files)"]))
    before = head(repo)
    out = go(repo, runs, rows, runner=pass_runner())
    assert out["result"] == "TEST_TIMEOUT" and head(repo) == before
    assert "chunk 2/3" in out["detail"] and out["affected"]
    assert json.loads((runs / "REQ-1.integration.json").read_text())["result"] == "TEST_TIMEOUT"


def test_a_paid_verifier_is_a_line_in_the_invocation_ledger(tmp_path):
    # 2026-10-02: the day closed at $49.66 against the owner's $45 cap partly
    # because verifier calls (at least $1 each) were in no ledger a day total read.
    import build_kernel
    repo, runs, run, rows = setup(tmp_path, priority="P0")

    def paid(cmd, prompt, cwd):
        return json.dumps({"result": '{"verdict": "FAIL", "requirement_ids": ["REQ-1"]}',
                           "total_cost_usd": 1.25})
    go(repo, runs, rows, runner=paid)
    lines = [e for e in build_kernel.ledger_entries(runs) if e["kind"] == "VERIFIER"]
    assert len(lines) == 1 and lines[0]["req"] == "REQ-1" and lines[0]["cost_usd"] == 1.25
    assert lines[0]["model"] in ("sonnet", "opus") and lines[0]["at"]
    # The program adds verifier cost to its wave and run totals itself: never twice.
    assert not lines[0].get("wave_id") and not lines[0].get("program_run_id")


def _attempted(runs, run, attempt_id="att-1"):
    (runs / "REQ-1.json").write_text(json.dumps({**run, "attempt_id": attempt_id}), encoding="utf-8")


def test_a_verifier_that_ran_out_of_turns_gets_one_more_run_with_twice_the_turns(tmp_path):
    # RES-001, 2026-10-08: the verifier stopped on error_max_turns before judging anything,
    # and INSUFFICIENT_EVIDENCE refused a change whose RED/GREEN had passed.
    import build_kernel
    repo, runs, run, rows = setup(tmp_path, priority="P0")
    _attempted(runs, run)
    turns = []

    def runner(cmd, prompt, cwd):
        turns.append(int(cmd[cmd.index("--max-turns") + 1]))
        if len(turns) == 1:
            return json.dumps({"subtype": "error_max_turns", "is_error": True, "total_cost_usd": 0.3})
        return json.dumps({"result": '{"verdict": "PASS", "requirement_ids": ["REQ-1"]}', "total_cost_usd": 0.4})

    receipt = bi.run_verifier(rows[0], run, {"result": "OK"}, repo=repo, runner=runner, runs_dir=runs)
    assert receipt["verdict"] == "PASS"
    assert turns == [turns[0], min(build_kernel.VERIFIER_MAX_TURNS, 2 * turns[0])]

    turns.clear()                                   # a verifier that finished is not run again

    def judged(cmd, prompt, cwd):
        turns.append(1)
        return json.dumps({"result": '{"verdict": "FAIL", "failing_criteria": ["x"]}', "total_cost_usd": 0.2})
    assert bi.run_verifier(rows[0], run, {"result": "OK"}, repo=repo, runner=judged, runs_dir=runs)["verdict"] == "FAIL"
    assert turns == [1]


def test_the_verifier_runs_on_the_budget_its_launch_held_then_gives_it_back(tmp_path):
    # 2026-10-03 owner: the spend lease covers verifiers too. A build launch holds its
    # verifier's budget; the verifier uses it, records the actual cost, releases the rest.
    import build_kernel
    repo, runs, run, rows = setup(tmp_path, priority="P0")
    _attempted(runs, run)
    build_kernel.spend_settle("att-1", "REQ-1", build_kernel.verifier_budget(1.0), runs)
    # Past the soft stop: new work could not start, but this verifier was already paid for.
    build_kernel.ledger_append({"attempt_id": "w", "req": "X", "kind": "SHIP", "cost_usd": 41.0,
                                "at": build_kernel._iso()}, runs)

    def paid(cmd, prompt, cwd):
        return json.dumps({"result": '{"verdict": "PASS", "requirement_ids": ["REQ-1"]}', "total_cost_usd": 0.4})
    assert go(repo, runs, rows, runner=paid)["result"] == "INTEGRATED"
    assert build_kernel.spend_committed(runs) == (41.4, 0.0)


def test_a_verifier_with_no_budget_left_does_not_run(tmp_path):
    import build_kernel
    repo, runs, run, rows = setup(tmp_path, priority="P0")
    _attempted(runs, run)
    build_kernel.ledger_append({"attempt_id": "w", "req": "X", "kind": "SHIP", "cost_usd": 44.5,
                                "at": build_kernel._iso()}, runs)
    before = head(repo)
    with pytest.raises(build_kernel.SpendCapReached):
        go(repo, runs, rows, runner=boom_runner)
    assert head(repo) == before and build_kernel.spend_committed(runs) == (44.5, 0.0)



def test_failed_ids_use_forward_slashes_whatever_pytest_printed():
    # Release gate on bd7c026: under the gate's basetemp pytest printed tests\\test_broken.py,
    # so a failure that predated the change was compared by the other spelling and blamed on it.
    lines = ["FAILED tests\\test_broken.py::test_broken - assert 2 == 3",
             "FAILED tests/test_old.py::test_old"]
    assert bi.failed_ids(lines) == ["tests/test_broken.py::test_broken", "tests/test_old.py::test_old"]


def _no_change(runs, run, rows, tests, next_action=""):
    run = dict(run, commit="", files_changed=[],
               evidence=dict(run["evidence"], acceptance_met=True, summary="No code change needed"))
    (runs / "REQ-1.json").write_text(json.dumps(run), encoding="utf-8")
    return run, [dict(rows[0], tests=tests, files=["calc.py"], next_action=next_action)]


def test_a_run_that_changed_nothing_is_confirmed_on_head(tmp_path):
    # 2026-10-09: four rows in one night were refused "run has no commit" and parked,
    # although each worker had found the acceptance already met.
    repo, runs, run, rows = setup(tmp_path)
    run, rows = _no_change(runs, run, rows, ["tests/test_old.py::test_old"])
    before = head(repo)
    asked = []

    def paid(cmd, prompt, cwd):
        asked.append(prompt)
        return json.dumps({"result": '{"verdict": "PASS", "requirement_ids": ["REQ-1"]}', "total_cost_usd": 0.2})

    out = go(repo, runs, rows, runner=paid)
    assert out["result"] == "CONFIRMED" and out["ledger_status"] == "VERIFIED"
    assert "ALREADY met" in asked[0] and "tests/test_old.py::test_old" in asked[0]
    module = bi.load_ledger(repo / "scripts" / "prd_ledger.py")
    row = {r["id"]: r for r in module.apply_progress(module._load_v7(), module.PROGRESS_V7)}["REQ-1"]
    assert row["status"] == "VERIFIED" and row["tests"] == ["tests/test_old.py::test_old"]
    assert sh(repo, "log", "--format=%s", f"{before}..HEAD").splitlines() == [
        "docs(ledger): REQ-1 -> VERIFIED from build run"]


def test_a_row_citing_no_test_is_confirmed_with_its_own_build_commits_tests(tmp_path):
    # 2026-10-09: BRAIN-001 was PARTIAL - a PARTIAL row cites no tests - when a worker found its
    # acceptance met; the confirm was refused "names no test" although the row's own build commit
    # on main had added tests/test_project_brain.py, proven RED/GREEN when it was integrated.
    repo, runs, run, rows = setup(tmp_path)
    sh(repo, "cherry-pick", run["commit"])
    sh(repo, "commit", "-q", "--amend", "-m", "build(REQ-1): twice\n\nBuild run: REQ-1")
    run, rows = _no_change(runs, run, rows, [])
    asked = []

    def paid(cmd, prompt, cwd):
        asked.append(prompt)
        return json.dumps({"result": '{"verdict": "PASS", "requirement_ids": ["REQ-1"]}', "total_cost_usd": 0.2})

    out = go(repo, runs, rows, runner=paid)
    assert out["result"] == "CONFIRMED" and out["ledger_status"] == "VERIFIED"
    assert "tests/test_twice.py::test_twice" in asked[0]
    assert bi.row_build_tests("REQ-10", sys.executable, repo, repo) == []  # no prefix match


def test_a_confirm_cites_only_the_tests_the_rows_build_commits_touched(tmp_path):
    # 2026-10-09: BG-001 was confirmed citing the first twelve tests of tests/test_autolearn.py -
    # payload parsing - because one of its commits had touched that file.
    base = "def test_edited():\n    assert True\n\n\ndef test_left_alone():\n    assert True\n"
    work = ("def test_edited():\n    assert 1 + 1 == 2\n\n\ndef test_left_alone():\n    assert True\n\n\n"
            "def test_added():\n    from calc import twice\n    assert twice(twice(1)) == 4\n")
    repo, runs, run, rows = setup(tmp_path, dict(GOOD, **{"tests/test_mixed.py": work}),
                                  base_extra={"tests/test_mixed.py": base})
    sh(repo, "cherry-pick", run["commit"])
    sh(repo, "commit", "-q", "--amend", "-m", "build(REQ-1): twice\n\nBuild run: REQ-1")
    run, rows = _no_change(runs, run, rows, [])
    verdict = json.dumps({"result": '{"verdict": "PASS", "requirement_ids": ["REQ-1"]}', "total_cost_usd": 0.2})
    out = go(repo, runs, rows, runner=lambda cmd, prompt, cwd: verdict)
    assert out["result"] == "CONFIRMED" and out["tests"] == [
        "tests/test_mixed.py::test_edited", "tests/test_mixed.py::test_added", "tests/test_twice.py::test_twice"]


SLOW_TEST = "import pytest\n\n\n@pytest.mark.slow\ndef test_slow():\n    assert True\n"


def test_named_tests_the_gate_never_runs_give_way_to_the_rows_build_tests(tmp_path):
    # 2026-10-09: BG-001 cites one @slow test; the gate deselected it, pytest exited 5, and the
    # confirm reported "the named tests fail at HEAD".
    repo, runs, run, rows = setup(tmp_path, base_extra={"tests/test_slow.py": SLOW_TEST})
    sh(repo, "cherry-pick", run["commit"])
    sh(repo, "commit", "-q", "--amend", "-m", "build(REQ-1): twice\n\nBuild run: REQ-1")
    run, rows = _no_change(runs, run, rows, ["tests/test_slow.py::test_slow"])
    asked = []

    def paid(cmd, prompt, cwd):
        asked.append(prompt)
        return json.dumps({"result": '{"verdict": "PASS", "requirement_ids": ["REQ-1"]}', "total_cost_usd": 0.2})

    out = go(repo, runs, rows, runner=paid)
    assert out["result"] == "CONFIRMED" and out["tests"] == ["tests/test_twice.py::test_twice"]
    assert "tests/test_twice.py::test_twice" in asked[0]


def test_named_tests_the_gate_never_runs_are_not_reported_as_failing(tmp_path):
    repo, runs, run, rows = setup(tmp_path, base_extra={"tests/test_slow.py": SLOW_TEST})
    run, rows = _no_change(runs, run, rows, ["tests/test_slow.py::test_slow"])
    out = go(repo, runs, rows, runner=lambda *a: pytest.fail("no verifier when no test ran"))
    assert out["result"] == "NOT_CONFIRMED" and "ran none of the named tests" in out["detail"]


@pytest.mark.parametrize("tests, next_action, verdict, why", [
    (["tests/test_old.py::test_old"], "RULING: keep this extra scope inside the row", "PASS", "extra scope"),
    ([], "", "PASS", "names no test"),
    (["tests/test_old.py::test_old"], "", "FAIL", "verifier did not find"),
])
def test_a_no_change_run_that_cannot_be_proven_is_not_confirmed(tmp_path, tests, next_action, verdict, why):
    repo, runs, run, rows = setup(tmp_path)
    run, rows = _no_change(runs, run, rows, tests, next_action)
    before = head(repo)

    def judged(cmd, prompt, cwd):
        return json.dumps({"result": '{"verdict": "%s", "failing_criteria": ["x"]}' % verdict,
                           "total_cost_usd": 0.1})

    out = go(repo, runs, rows, runner=judged)
    assert out["result"] == "NOT_CONFIRMED" and why in out["detail"]
    assert head(repo) == before


def test_a_partial_integration_carries_the_owner_rulings_into_its_gap_list(tmp_path):
    # 2026-10-09: rewritten as a bare "GAPS: ...", next_action dropped the owner ruling
    # that kept CYBER-002's extra scope - and with it the guard against closing the row.
    assert bi.carried_rulings("slice text | RULING 2026-10-08e (owner): keep this extra scope inside the row "
                              "| more") == "RULING 2026-10-08e (owner): keep this extra scope inside the row"
    assert bi.carried_rulings("packet\nRULING x: after this slice the probe stays") == (
        "RULING x: after this slice the probe stays")
    assert bi.carried_rulings("no rulings here") == ""
    repo, runs, run, rows = setup(tmp_path, acceptance_met=False)
    rows = [dict(rows[0], next_action="the slice | RULING 2026-10-08e (owner): keep extra scope inside the row")]
    out = go(repo, runs, rows)
    assert out["result"] == "INTEGRATED" and out["ledger_status"] == "PARTIAL"
    module = bi.load_ledger(repo / "scripts" / "prd_ledger.py")
    row = {r["id"]: r for r in module.apply_progress(module._load_v7(), module.PROGRESS_V7)}["REQ-1"]
    assert row["next_action"].startswith("GAPS: ") and "RULING 2026-10-08e (owner): keep extra scope" in row["next_action"]


def test_the_keep_scope_ruling_is_recognised_in_either_wording(tmp_path):
    repo, runs, run, rows = setup(tmp_path)
    run, rows = _no_change(runs, run, rows, ["tests/test_old.py::test_old"],
                           "RULING (owner): keep extra scope inside the row")
    out = go(repo, runs, rows, runner=lambda cmd, prompt, cwd: "")
    assert out["result"] == "NOT_CONFIRMED" and "extra scope" in out["detail"]
