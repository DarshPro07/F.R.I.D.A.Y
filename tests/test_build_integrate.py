"""The build integrator on real temp git repos (no real `claude`, no real ledger)."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import build_integrate as bi  # noqa: E402
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
    assert row["tests"] == ["tests/test_twice.py"] and "calc.py" in row["files"]
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
    repo, runs, run, rows = setup(tmp_path)
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
    with pytest.raises(KernelError, match="waited"):
        go(repo, runs, rows, queue=q)
    assert head(repo) == before
    assert [e["req"] for e in q.pending()] == ["OTHER"]


# ---------------------------------------------------------------- wide impact

def test_a_wide_affected_set_is_recorded_with_its_chunk_count(tmp_path, monkeypatch):
    repo, runs, run, rows = setup(tmp_path)
    many = [f"tests/test_w{i}.py" for i in range(bi.WIDE_IMPACT_FILES + 1)]
    for t in many:
        write(repo, t, "def test_x():\n    pass\n")
    monkeypatch.setattr(bi, "affected", lambda run, extra, repo=None: many)
    monkeypatch.setattr(bi, "gate_run", lambda python, cwd, tests: (0, ["ok"]))
    out = go(repo, runs, rows, runner=pass_runner())
    assert out["impact"] == "WIDE" and out["gate_chunks"] == 2 and out["gate_workers"] == 1


def test_a_narrow_set_is_not_wide(tmp_path):
    repo, runs, run, rows = setup(tmp_path)
    out = go(repo, runs, rows, runner=pass_runner())
    assert out["impact"] == "NORMAL" and out["gate_chunks"] >= 1


def test_gate_workers_never_exceed_the_governor(monkeypatch):
    monkeypatch.setattr(bi, "concurrency", lambda: (1, "one slot"))
    assert bi.gate_workers(500) == 1
    monkeypatch.setattr(bi, "concurrency", lambda: (3, "ok"))
    assert bi.gate_workers(500) == 3 and bi.gate_workers(bi.WIDE_IMPACT_FILES) == 1
    assert bi.gate_workers(bi.WIDE_IMPACT_FILES + 1) == 2  # only two chunks exist
    monkeypatch.setattr(bi, "concurrency", lambda: (0, "pressure"))
    assert bi.gate_workers(500) == 1  # the gate still has to run


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
    out = bi.merge(run, bi.commit_test_files(run, repo), repo, sys.executable)
    assert out["result"] == "FAILED_INTEGRATION" and "NOT reset" in out["reset"]
    assert head(repo) != out["pre_head"]


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
        seen.append(cmd[cmd.index("--") + 3:])
        assert kw["timeout"] == bi.TEST_S
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
    merged = bi.merge(run, ["tests/test_twice.py"], repo, sys.executable)
    assert merged["result"] == "TEST_TIMEOUT" and merged["reset"] == "reset to pre_head"
    assert head(repo) == before and "chunk 1/1" in merged["detail"]
    assert merged["affected"]


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
