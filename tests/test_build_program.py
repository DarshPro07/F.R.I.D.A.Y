"""scripts/build_program.py with a fake kernel, fake integrator and fake git: no claude, no worktree,
no real ledger or state file."""

from __future__ import annotations

import contextlib
import json
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import build_kernel as BK  # noqa: E402
import build_program as BP  # noqa: E402
import prd_ledger as PL  # noqa: E402


class KErr(Exception):
    pass


class FakeKernel:
    KernelError = KErr
    WORKER_FAILURES = BK.WORKER_FAILURES
    BUDGETS = {"SCOUT": (8, 0.75, 1), "MICRO": (12, 1.0, 1), "NORMAL": (25, 3.0, 1),
               "COMPLEX": (40, 6.0, 1), "P0": (40, 6.0, 1)}
    VENV_PY = "py"
    RUNS_DIR = Path("unused-runs")
    DEFAULT_CAP = 2
    _PRIORITY = {"P0": 0, "P1": 1}

    def __init__(self, ids=("A", "B"), modes=None, klass="NORMAL", level=2, priority="P1"):
        self.rows = [{"id": i, "status": PL.MISSING, "priority": priority} for i in ids]
        self.modes, self.klass, self.level = modes or {}, klass, level
        self.script: dict[str, list] = {}
        self.launches: list[tuple[str, str, str]] = []
        self.launch_errors: dict[str, str] = {}
        self.closed: set[str] = set()
        self.watched = 0
        self.resumable_ids: set[str] = set()  # lower-case ids whose stale worktree can be resumed
        self.resumes: list[str] = []
        self.repairs: list[str] = []
        self.repair_errors: dict[str, str] = {}
        self.ledger: list[dict] = []
        self.steps: list[str] = []  # the cascade step each repair launch took
        self.planted_jobs: list[dict] = []
        self.adopted: list[str] = []
        self.finalized: list[str] = []
        self.closed_jobs: list[tuple[str, str]] = []
        self.finished = False  # whether an adopted dead job's stream holds a result
        self.refused: set[str] = set()  # requirements whose admission or lease recheck fails
        self.over_budget: set[str] = set()  # requirements the day's spend lease refuses
        self.too_big: set[str] = set()  # requirements whose predicted memory does not fit now
        self.with_work: set[str] = set()  # requirements whose failed worker left changes on disk
        self.rebases_clean: set[str] = set()  # requirements whose branch rebases onto HEAD cleanly
        self.rebased: list[str] = []
        self.discarded: list[str] = []
        self.spend: list[tuple[str, str, float]] = []  # (req, kind, usd) of each spend lease taken
        self.released: list[str] = []  # memory leases given back
        self.events: list[tuple] = []  # job-state writes and lease attempts, in order

    def load_rows(self):
        return [r for r in self.rows]

    def load_config(self):
        return {"depends": {}, "order": []}

    def ready(self, rows, depends, order):
        return [r for r in rows if r["id"] not in self.closed]

    def waves(self, queue, cap):
        return [list(queue)] if queue else []  # as the kernel: no rows, no waves

    def concurrency(self, cap=2):
        return min(self.level, cap), "fake"

    def rebase_run(self, req, *, repo=None, runs_dir=None):
        self.rebased.append(req)
        return req in self.rebases_clean

    def discard_worktree(self, rec, *, repo=None):
        self.discarded.append(rec["requirement"])
        return True

    def has_work(self, rec):
        return rec["requirement"] in self.with_work

    def admissible(self, candidates, cap=2):
        ids = [row["id"] for row, _, _ in candidates if row["id"] not in self.too_big]
        return ids[:min(self.level, cap)], "fake"

    def job_queue(self, req, klass, mode="SHIP", runs_dir=None, reason=""):
        self.events.append(("QUEUED", req))
        return f"att-{req}"

    def reserve(self, attempt_id, req, klass, mode="SHIP", *, cap=2, runs_dir=None):
        self.events.append(("RESERVE", req))
        if req in self.refused:
            return False, f"{req} needs 1.7 GB; 0.9 GB free above the reserve"
        return True, "fake"

    def job_wait(self, attempt_id, reason, runs_dir=None):
        self.events.append(("WAITING_RESOURCES", attempt_id, reason))

    def spend_reserve(self, key, purpose, req, kind, usd, runs_dir=None):
        if req in self.over_budget:
            return False, f"SPEND_SOFT_STOP: fake day spent before {req}"
        self.spend.append((req, kind, usd))
        return True, "fake"

    launch_usd = staticmethod(BK.launch_usd)

    def lease_release(self, attempt_id, runs_dir=None):
        self.released.append(attempt_id)

    def resumable(self, wt):
        return "S1" if wt.name in self.resumable_ids else None

    def resume(self, req, *, worktrees):
        self.resumes.append(req)
        return SimpleNamespace(req=req)

    def repair(self, req, *, worktrees, advice=""):
        if req in self.repair_errors:
            raise KErr(self.repair_errors[req])
        self.repairs.append(req)
        self.steps.append("IMPLEMENTER" if advice else "REPAIR")
        return SimpleNamespace(req=req)

    def escalate(self, req, *, worktrees):
        self.steps.append("ESCALATE")
        return SimpleNamespace(req=req)

    def advise(self, req, *, worktrees):
        self.steps.append("ADVISOR")
        return SimpleNamespace(req=req)

    def load_advice(self, req):
        return f"advice for {req}"

    agent_utility = staticmethod(BK.agent_utility)

    def jobs(self, runs_dir):
        return list(self.planted_jobs)

    def adopt(self, job, *, alive, runs_dir, repo):
        self.adopted.append(job["req"])
        return SimpleNamespace(req=job["req"], wt=Path(job["worktree"]), stream_offset=0)

    def has_result(self, wt, offset=0):
        return self.finished

    def finalize(self, run, reason, runs_dir):
        self.finalized.append(run.req)
        return rec(run.req)

    def job_close(self, attempt_id, state, reason="", runs_dir=None):
        self.closed_jobs.append((attempt_id, state))

    def mode_for(self, row):
        return self.modes.get(row["id"], "SHIP")

    def budget_class(self, row):
        return "P0" if row["priority"] == "P0" else self.klass

    def code_map(self, row):
        return f"map-{row['id']}"

    def launch(self, row, *, mode, capsule=""):
        self.events.append(("SPAWN", row["id"]))
        if row["id"] in self.launch_errors:
            raise KErr(self.launch_errors[row["id"]])
        self.launches.append((row["id"], mode, capsule))
        return SimpleNamespace(req=row["id"])

    def watch(self, runs, runs_dir=None):
        self.watched += 1
        recs = [self.script[r.req].pop(0) for r in runs]
        for r in recs:  # what the real kernel's finalize appends to the invocation ledger
            self.ledger.append({"program_run_id": os.environ.get("FRIDAY_BUILD_RUN_ID", ""),
                                "wave_id": os.environ.get("FRIDAY_BUILD_WAVE_ID", ""),
                                "cost_usd": float(r.get("cost_usd") or 0.0)})
        return recs

    def wave_cost(self, wave_id):
        return sum(e["cost_usd"] for e in self.ledger if e["wave_id"] == wave_id)

    def run_cost(self, run_id):
        return sum(e["cost_usd"] for e in self.ledger if e["program_run_id"] == run_id)

    def _worktrees_root(self):
        raise AssertionError("tests pass worktrees")


class FakeIntegrator:
    def __init__(self, kernel, results=None, verifier_cost=0.0, close=True):
        self.k, self.results, self.cost, self.close = kernel, results or {}, verifier_cost, close
        self.calls: list[str] = []

    def integrate(self, req):
        self.calls.append(req)
        res = self.results.get(req, "INTEGRATED")
        if isinstance(res, list):  # one result per call, in order
            res = res.pop(0)
        if isinstance(res, Exception):
            raise res
        if res == "INTEGRATED" and self.close:
            self.k.closed.add(req)
        return {"requirement": req, "result": res, "verifier": {"cost_usd": self.cost} if self.cost else None}


class FakeGit:
    def __init__(self, dirty="", ledger_rc=0, changed="docs/implementation/X.md"):
        self.dirty, self.ledger_rc, self.changed = dirty, ledger_rc, changed
        self.cmds: list[list[str]] = []

    def __call__(self, cmd, cwd, timeout=60):
        self.cmds.append(cmd)
        out, rc = "", 0
        if cmd[:2] == ["git", "status"]:
            out = self.dirty
        elif cmd[:2] == ["git", "rev-parse"]:
            out = "abc1234"
        elif cmd[:2] == ["git", "branch"]:
            out = "build/bh-d"
        elif cmd[:2] == ["git", "diff"]:
            out = self.changed
        elif "prd_ledger.py" in " ".join(cmd):
            rc = self.ledger_rc
        return subprocess.CompletedProcess(cmd, rc, out, "render failed" if rc else "")

    def commits(self):
        return [c for c in self.cmds if "commit" in c]


def rec(req, state="DONE", mode="SHIP", cost=0.5, blockers=None, kind=""):
    return {"requirement": req, "state": state, "mode": mode, "cost_usd": cost, "kind": kind,
            "evidence": {"blockers": blockers or []}}


@pytest.fixture
def env(tmp_path):
    wt = tmp_path / "worktrees"
    wt.mkdir()
    return SimpleNamespace(state=tmp_path / "data" / "build_state.json",
                           doc=tmp_path / "PRD_CURRENT_STATE.md", wt=wt, git=FakeGit())


def go(env, k, bi=None, decision=None, **kw):
    bi = bi or FakeIntegrator(k)
    if decision:
        env.state.parent.mkdir(parents=True, exist_ok=True)
        env.state.write_text(json.dumps({"capsule_decision": decision}), encoding="utf-8")
    kw.setdefault("max_waves", 5)
    kw.setdefault("max_usd", 100.0)
    kw.setdefault("checkout_turn", lambda label: contextlib.nullcontext())
    res = BP.run_program(repo=Path("."), state_file=env.state, state_doc=env.doc, worktrees=env.wt,
                         k=k, bi=bi, runner=env.git, out=lambda *_: None, **kw)
    return res, bi


def state_of(env):
    return json.loads(env.state.read_text(encoding="utf-8"))


# ------------------------------------------------------------------ preflight

def test_dirty_tree_stops_and_touches_nothing(env):
    env.git.dirty = " M friday/x.py"
    k = FakeKernel()
    res, _ = go(env, k)
    assert res["stop_reason"] == "dirty tree"
    assert not k.launches and not env.state.exists() and not env.doc.exists()


class CleansOnCommit(FakeGit):
    """A git whose tree is clean once something is committed."""

    def __call__(self, cmd, cwd, timeout=60):
        res = super().__call__(cmd, cwd, timeout)
        if "commit" in cmd:
            self.dirty = ""
        return res


def test_only_the_programs_own_renders_dirty_are_committed_and_the_run_goes_on(env):
    # 2026-10-05: a stale .git/index.lock failed finish()'s docs commit; the renders it
    # left made every later run stop on "dirty tree" for hours.
    env.git = CleansOnCommit(dirty=" M docs/implementation/PRD_CURRENT_STATE.md\n"
                                   " M docs/implementation/PRD_V7_LEDGER.md")
    res, _ = go(env, FakeKernel(ids=()))
    assert res["stop_reason"] == "no ready work"
    first = env.git.commits()[0]
    assert "commit the renders an earlier run could not" in " ".join(first)
    assert ["git", "add", "-u", "--", "docs/implementation"] in env.git.cmds


@pytest.mark.parametrize("dirty", [
    " M friday/x.py",
    " M docs/implementation/PRD_V7_LEDGER.md\n M scripts/build_kernel.py",
    "A  docs/implementation/NEW.md",
    " M docs/PRD_V7.md",
])
def test_anything_else_dirty_still_stops_and_commits_nothing(env, dirty):
    env.git = CleansOnCommit(dirty=dirty)
    k = FakeKernel()
    res, _ = go(env, k)
    assert res["stop_reason"] == "dirty tree"
    assert not env.git.commits() and not k.launches


def test_a_dry_run_never_commits_the_renders(env):
    env.git = CleansOnCommit(dirty=" M docs/implementation/PRD_V7_LEDGER.md")
    res, _ = go(env, FakeKernel(), dry_run=True)
    assert res["stop_reason"] == "dirty tree" and not env.git.commits()


# ------------------------------------------------------------------ stop reasons

def test_no_ready_work_stop(env):
    res, _ = go(env, FakeKernel(ids=()))
    assert res["stop_reason"] == "no ready work"
    assert state_of(env)["stop_reason"] == "no ready work"


def test_pressure_stop(env):
    k = FakeKernel(level=0)
    res, _ = go(env, k)
    assert res["stop_reason"] == "pressure" and not k.launches
    # Not a failed run: the program is waiting for resources, and says why.
    st = state_of(env)
    assert st["status"] == "WAITING_RESOURCES" and st["resources"] == "fake"
    # 2026-10-02: every pressure stop committed the state doc (only a timestamp
    # changed) - five empty commits in five minutes. A run with no wave commits nothing.
    assert not env.git.commits() and not env.doc.exists()


def test_a_job_is_queued_before_admission_and_admission_before_spawn(env):
    k = FakeKernel(ids=("A",))
    k.script = {"A": [rec("A")]}
    go(env, k, max_waves=1)
    assert k.events[:3] == [("QUEUED", "A"), ("RESERVE", "A"), ("SPAWN", "A")]


def test_a_refused_job_waits_with_its_reason_and_spawns_nothing(env):
    k = FakeKernel(ids=("A",))
    k.refused = {"A"}
    res, _ = go(env, k)
    assert res["stop_reason"] == "pressure" and not k.launches
    assert ("WAITING_RESOURCES", "att-A", "A needs 1.7 GB; 0.9 GB free above the reserve") in k.events
    assert "SPAWN" not in [e[0] for e in k.events]
    assert state_of(env)["status"] == "WAITING_RESOURCES"


def test_a_refused_job_does_not_stop_the_admitted_one_beside_it(env):
    k = FakeKernel(ids=("A", "B"))
    k.refused = {"A"}
    k.script = {"B": [rec("B")]}
    go(env, k, max_waves=1)
    assert [x[0] for x in k.launches] == ["B"]


def test_a_job_the_day_budget_refuses_gives_its_memory_back_and_stops_the_run(env):
    # 2026-10-03 owner: the spend lease is part of the scheduler. Memory frees up
    # within a day; the day's budget does not, so the run stops as "day budget".
    k = FakeKernel(ids=("A",), priority="P0")
    k.over_budget = {"A"}
    res, _ = go(env, k)
    assert res["stop_reason"] == "day budget" and not k.launches
    assert k.released == ["att-A"] and "A" not in state_of(env)["parked"]
    assert state_of(env)["budget"].startswith("SPEND_SOFT_STOP")


def test_a_worker_that_fails_with_work_on_disk_enters_the_repair_cascade(env):
    # 2026-10-03: SEC-005 ran out of turns with its work on disk. Counted as a plain
    # attempt, the next run could only park its worktree as stale.
    k = FakeKernel(ids=("A",), priority="P0")
    k.with_work = {"A"}
    k.script = {"A": [rec("A", state="FAILED")]}
    go(env, k, max_waves=1)
    st = state_of(env)
    assert "A" in st["repair_queue"] and "A" not in st["parked"] and not st["attempts"].get("A")


def test_a_worker_that_fails_with_nothing_on_disk_is_an_attempt(env):
    k = FakeKernel(ids=("A",), priority="P0")
    k.script = {"A": [rec("A", state="FAILED")]}
    go(env, k, max_waves=1)
    st = state_of(env)
    assert st["attempts"]["A"] == 1 and "A" not in st["repair_queue"]
    # Its empty worktree is dropped, so the retry launches instead of parking "stale worktree".
    assert k.discarded == ["A"] and "A" not in st["parked"]


def test_a_run_whose_only_admissible_job_is_an_unaffordable_repair_stops_on_spend(env):
    # 2026-10-03 (chain 8, 19:37): admission let only CTX-002's repair through, the run's
    # budget could not pay for it, and pick_wave crashed on the empty filtered queue.
    k = FakeKernel(ids=("B",), priority="P0", level=1)
    k.too_big = {"B"}
    state = {"repair_queue": {"A": {"at": "1"}}, "repairs": {}, "cascade": {}, "parked": {}}
    env.state.parent.mkdir(parents=True, exist_ok=True)
    env.state.write_text(json.dumps(state), encoding="utf-8")
    res, _ = go(env, k, max_usd=0.5)
    assert res["stop_reason"] == "spend" and not k.repairs and not k.launches


def test_pick_wave_on_an_empty_queue_is_empty():
    assert BP.pick_wave([], 2, 1, 5.0, BK) == []


def test_a_job_too_big_for_the_memory_waits_while_the_next_one_runs(env):
    # Owner v12: park only the branch that needs RAM; continue the independent work.
    k = FakeKernel(ids=("A", "B"), priority="P0", level=1)
    k.too_big = {"A"}
    k.script = {"B": [rec("B")]}
    go(env, k, max_waves=1)
    assert [x[0] for x in k.launches] == ["B"] and "A" not in state_of(env)["parked"]


def test_nothing_admissible_is_a_pressure_stop_with_its_reason(env):
    k = FakeKernel(ids=("A",), priority="P0")
    k.too_big = {"A"}
    lines = []
    BP.run_program(repo=Path("."), state_file=env.state, state_doc=env.doc, worktrees=env.wt, k=k,
                   bi=FakeIntegrator(k), runner=env.git, out=lines.append, dry_run=True,
                   checkout_turn=lambda label: contextlib.nullcontext())
    assert lines[-1] == "nothing to plan: pressure (fake)"


def test_each_launch_reserves_its_worker_and_verifier_budget(env):
    k = FakeKernel(ids=("A",), priority="P0")
    k.script = {"A": [rec("A")]}
    go(env, k, max_waves=1)
    assert k.spend == [("A", "SHIP", BK.launch_usd("P0", "SHIP"))]


def test_a_repair_refused_by_the_day_budget_stays_queued(env):
    k = FakeKernel(ids=())
    k.over_budget = {"A"}
    state = {"repair_queue": {"A": {"at": "1"}}, "repairs": {}, "cascade": {}, "parked": {}}
    assert BP.launch_repairs([("A", "NORMAL")], state, k, env.wt) == []
    assert "A" in state["repair_queue"] and state["repairs"] == {} and not k.repairs


def test_a_refused_repair_stays_queued_and_consumes_no_attempt(env):
    k = FakeKernel(ids=())
    k.refused = {"A"}
    state = {"repair_queue": {"A": {"at": "1"}}, "repairs": {}, "cascade": {}, "parked": {}}
    assert BP.launch_repairs([("A", "NORMAL")], state, k, env.wt) == []
    assert "A" in state["repair_queue"] and state["repairs"] == {} and not k.repairs


def test_adopt_jobs_closes_queued_and_waiting_jobs_nothing_spawned_for(env):
    k = FakeKernel()
    k.planted_jobs = [{"attempt_id": "q1", "req": "A", "state": "QUEUED"},
                      {"attempt_id": "w1", "req": "B", "state": "WAITING_RESOURCES"}]
    live, done = BP.adopt_jobs(k, env.wt)
    assert (live, done) == ([], []) and k.closed_jobs == [("q1", "FAILED"), ("w1", "FAILED")]
    assert not k.adopted


def test_a_wave_is_capped_by_its_own_budget(env):
    k = FakeKernel(ids=("A", "B"))
    k.script = {i: [rec(i)] for i in "AB"}
    go(env, k, max_usd=100.0, max_wave_usd=4.0, max_waves=1)
    assert [x[0] for x in k.launches] == ["A"]  # NORMAL is $3: A fits, A+B ($6) does not


def test_a_wave_cap_below_one_worker_still_lets_one_run(env):
    # With the default $10 run the half-run wave cap is $5, below a P0 worker's
    # $6: the cap must limit extra workers, never block the first.
    k = FakeKernel(ids=("A", "B"), priority="P0")
    k.script = {i: [rec(i)] for i in "AB"}
    go(env, k, max_usd=10.0, max_waves=1)
    assert [x[0] for x in k.launches] == ["A"]


def test_a_costly_wave_without_progress_stops_the_program(env):
    # A wave that spends a real share of the budget without closing a
    # requirement, queueing a repair or naming a blocker stops to replan.
    k = FakeKernel(ids=("A", "B"))
    k.script = {"A": [rec("A", "FAILED", cost=5.0)], "B": [rec("B")]}
    res, _ = go(env, k, max_usd=15.0, cap=1)
    assert res["stop_reason"] == "costly wave without progress"
    assert len(state_of(env)["waves"]) == 1


def test_max_waves_stop(env):
    k = FakeKernel(ids=("A", "B", "C"))
    k.script = {i: [rec(i)] for i in "ABC"}
    res, _ = go(env, k, max_waves=1, cap=1)
    assert res["stop_reason"] == "max waves"
    assert [w["reqs"] for w in state_of(env)["waves"]] == [["A"]]


def test_spend_stop_after_a_wave_reaches_the_limit(env):
    k = FakeKernel(ids=("A", "B"))
    k.script = {i: [rec(i, cost=4.0)] for i in "AB"}
    res, _ = go(env, k, cap=1, max_usd=3.0, decision="on")
    assert res["stop_reason"] == "spend"
    assert k.launches == [("A", "SHIP", "map-A")]


def test_no_progress_after_two_waves_of_worker_failures(env):
    # "No progress" means the workers themselves keep failing - a broken
    # environment - not that requirements keep getting parked.
    k = FakeKernel(ids=("A", "B", "C"))
    k.script = {i: [rec(i, "FAILED"), rec(i, "FAILED")] for i in "ABC"}
    res, _ = go(env, k, cap=1)
    assert res["stop_reason"] == "no progress"
    assert len(state_of(env)["waves"]) == 2


def test_a_progress_wave_resets_the_idle_count(env):
    k = FakeKernel(ids=("A", "B"))
    k.script = {"A": [rec("A", "FAILED"), rec("A")], "B": [rec("B", "FAILED"), rec("B", "FAILED")]}
    res, _ = go(env, k, cap=1)
    assert res["stop_reason"] == "no progress"
    assert len(state_of(env)["waves"]) == 4


@pytest.mark.parametrize("outcome", ["BLOCKED", "VERIFIER_FAIL"])
def test_parked_requirements_do_not_stop_the_program(env, outcome):
    # 2026-10-01: CORE-001 was rejected twice and parked, and the program
    # stopped with 19 P0s untried. A parked blocker is persisted, and the
    # program goes on with independent work.
    k = FakeKernel(ids=("A", "B", "C"))
    if outcome == "BLOCKED":
        k.script = {i: [rec(i, "BLOCKED")] for i in "ABC"}
        bi = FakeIntegrator(k)
    else:
        # the build, then repair, escalation, advisor and advised implementer
        k.script = {i: [rec(i), rec(i), rec(i), rec(i, kind="ADVISOR"), rec(i)] for i in "ABC"}
        bi = FakeIntegrator(k, results={i: "VERIFIER_FAIL" for i in "ABC"})
    res, _ = go(env, k, bi=bi, cap=1, max_waves=20)
    assert res["stop_reason"] == "no ready work"
    assert set(state_of(env)["parked"]) == {"A", "B", "C"}


# ------------------------------------------------------------------ outcomes

def test_scout_then_ship_across_waves(env):
    k = FakeKernel(ids=("A",), modes={"A": "SCOUT"})
    k.script = {"A": [rec("A", mode="SCOUT"), rec("A")]}
    orig = k.launch

    def launch(row, *, mode, capsule=""):
        run = orig(row, mode=mode, capsule=capsule)
        k.modes["A"] = "SHIP"  # the scout's findings now exist
        return run

    k.launch = launch
    res, bi = go(env, k, decision="on")
    assert k.launches == [("A", "SCOUT", ""), ("A", "SHIP", "map-A")]
    assert bi.calls == ["A"]
    assert [w["results"] for w in state_of(env)["waves"]] == [{"A": "SCOUT_DONE"}, {"A": "INTEGRATED"}]


def test_scout_done_is_never_integrated(env):
    k = FakeKernel(ids=("A",), modes={"A": "SCOUT"})
    k.script = {"A": [rec("A", mode="SCOUT")]}
    _, bi = go(env, k, max_waves=1)
    assert bi.calls == []


def test_integrated_is_recorded_and_not_relaunched_even_if_the_ledger_is_stale(env):
    k = FakeKernel(ids=("A",))
    k.script = {"A": [rec("A"), rec("A")]}
    res, bi = go(env, k, bi=FakeIntegrator(k, close=False))
    assert bi.calls == ["A"] and len(k.launches) == 1
    assert res["stop_reason"] == "no ready work"


@pytest.mark.parametrize("result", ["CONFLICT", "NOT_RED", "VERIFIER_FAIL", "FAILED_INTEGRATION"])
def test_non_integrated_result_parks_with_escalation(env, result):
    k = FakeKernel(ids=("A",))
    k.script = {"A": [rec("A")]}
    env.state.parent.mkdir(parents=True, exist_ok=True)
    env.state.write_text(json.dumps({"repairs": {"A": 4}}), encoding="utf-8")  # the whole cascade is spent
    go(env, k, bi=FakeIntegrator(k, {"A": result}))
    st = state_of(env)
    assert st["parked"] == {"A": result}
    esc = st["escalations"][0]
    assert esc["req"] == "A" and esc["kind"] == "integration" and esc["at"]


def test_integrator_refusal_parks(env):
    k = FakeKernel(ids=("A",))
    k.script = {"A": [rec("A")]}
    go(env, k, bi=FakeIntegrator(k, {"A": KErr("no evidence")}))
    st = state_of(env)
    assert st["parked"] == {"A": "integration refused"}
    assert "no evidence" in st["escalations"][0]["detail"]


def test_blocked_parks_with_the_blockers(env):
    k = FakeKernel(ids=("A",))
    k.script = {"A": [rec("A", "BLOCKED", blockers=["needs key", "no access"])]}
    go(env, k)
    st = state_of(env)
    assert st["parked"] == {"A": "blocked"}
    assert "needs key" in st["escalations"][0]["detail"] and "no access" in st["escalations"][0]["detail"]


@pytest.mark.parametrize("bad", ["FAILED", "TIMEOUT", "STALLED"])
def test_failure_twice_parks_once_does_not(env, bad):
    k = FakeKernel(ids=("A",))
    k.script = {"A": [rec("A", bad), rec("A", bad)]}
    go(env, k, max_waves=1)
    st = state_of(env)
    assert st["attempts"] == {"A": 1} and st["parked"] == {}
    k.script = {"A": [rec("A", bad)]}
    go(env, k, max_waves=1)
    st = state_of(env)
    assert st["attempts"] == {"A": 2} and "A" in st["parked"]
    assert st["escalations"][0]["kind"] == "attempts"


def test_parked_ids_from_the_previous_state_are_skipped(env):
    env.state.parent.mkdir(parents=True)
    env.state.write_text(json.dumps({"parked": {"A": "blocked"}}), encoding="utf-8")
    k = FakeKernel(ids=("A", "B"))
    k.script = {"B": [rec("B")]}
    go(env, k)
    assert [x[0] for x in k.launches] == ["B"]


def test_stale_worktree_is_parked_not_launched(env):
    (env.wt / "a").mkdir()
    k = FakeKernel(ids=("A", "B"))
    k.script = {"B": [rec("B")]}
    go(env, k)
    st = state_of(env)
    assert st["parked"]["A"] == "stale worktree"
    assert [x[0] for x in k.launches] == ["B"]
    assert not k.resumes


def test_resumable_stale_worktree_is_resumed_not_parked_or_relaunched(env):
    (env.wt / "a").mkdir()
    k = FakeKernel(ids=("A",))
    k.resumable_ids = {"a"}
    k.script = {"A": [rec("A")]}
    go(env, k)
    st = state_of(env)
    assert k.resumes == ["A"] and not k.launches
    assert "A" not in st["parked"] and st["attempts"]["A"] == 1


def test_a_stale_worktree_is_resumed_at_most_once_per_run(env):
    (env.wt / "a").mkdir()
    k = FakeKernel(ids=("A",))
    k.resumable_ids = {"a"}
    state = BP.load_state(env.state)
    assert [r["id"] for r in BP.queue_for(k.rows, state, set(), env.wt, None, k, resumed=set())] == ["A"]
    assert not state["parked"]
    assert BP.queue_for(k.rows, state, set(), env.wt, None, k, resumed={"A"}) == []
    assert state["parked"]["A"] == "stale worktree"


def test_a_resumed_run_that_fails_is_parked_by_its_attempts(env):
    (env.wt / "a").mkdir()
    k = FakeKernel(ids=("A",))
    k.resumable_ids = {"a"}
    k.script = {"A": [rec("A", "FAILED")]}
    go(env, k, cap=1)
    assert k.resumes == ["A"] and state_of(env)["parked"]["A"] == "failed x2"


def test_the_capsule_arm_is_stable_and_splits_ids():
    state = {"capsule_decision": None}
    arms = {i: BP.capsule_arm(i, state) for i in (f"R-{n}" for n in range(40))}
    assert arms == {i: BP.capsule_arm(i, dict(state)) for i in arms}
    assert set(arms.values()) == {True, False}


@pytest.mark.parametrize("decision,expect", [("on", True), ("off", False)])
def test_a_capsule_decision_overrides_the_arm(decision, expect):
    assert {BP.capsule_arm(f"R-{n}", {"capsule_decision": decision}) for n in range(20)} == {expect}


def test_the_ship_capsule_follows_the_arm(env):
    ids = tuple(f"R-{n}" for n in range(8))
    k = FakeKernel(ids=ids)
    k.script = {i: [rec(i)] for i in ids}
    go(env, k, cap=1, max_waves=20)
    got = {rid: capsule for rid, _, capsule in k.launches}
    assert got == {i: (f"map-{i}" if BP.capsule_arm(i, {}) else "") for i in ids}


def test_launch_kernel_error_parks_with_escalation(env):
    k = FakeKernel(ids=("A", "B"))
    k.launch_errors["A"] = "worktree exists"
    k.script = {"B": [rec("B")]}
    go(env, k)
    st = state_of(env)
    assert st["parked"] == {"A": "launch failed"}
    assert st["escalations"][0]["detail"] == "worktree exists"
    assert [x[0] for x in k.launches] == ["B"]


def test_priority_filter(env):
    k = FakeKernel(ids=("A",), priority="P1")
    res, _ = go(env, k, priority="P0")
    assert res["stop_reason"] == "no ready work" and not k.launches


# ------------------------------------------------------------------ spend

def test_wave_is_trimmed_to_the_remaining_budget(env):
    k = FakeKernel(ids=("A", "B", "C"), klass="NORMAL")  # 3.0 each
    k.script = {i: [rec(i, cost=0.0)] for i in "ABC"}
    go(env, k, max_waves=1, cap=2, max_usd=4.0)
    assert [x[0] for x in k.launches] == ["A"]


def test_scout_rows_are_priced_at_the_scout_class(env):
    k = FakeKernel(ids=("A", "B"), modes={"A": "SCOUT", "B": "SCOUT"})
    k.script = {i: [rec(i, mode="SCOUT", cost=0.0)] for i in "AB"}
    go(env, k, max_waves=1, cap=2, max_usd=1.6, max_wave_usd=1.6)  # 0.75 * 2 fits, NORMAL would not
    assert len(k.launches) == 2


def test_nothing_fits_the_budget_stops_on_spend(env):
    k = FakeKernel(ids=("A",), klass="COMPLEX")
    res, _ = go(env, k, max_usd=1.0)
    assert res["stop_reason"] == "spend" and not k.launches


def test_spend_adds_verifier_cost(env):
    k = FakeKernel(ids=("A",))
    k.script = {"A": [rec("A", cost=0.5)]}
    go(env, k, bi=FakeIntegrator(k, verifier_cost=0.25), max_waves=1)
    st = state_of(env)
    assert st["spend_usd_total"] == 0.75 and st["waves"][0]["spend"] == 0.75


# ------------------------------------------------------------------ state

def test_state_fields_and_initial_last_full_suite(env):
    k = FakeKernel(ids=("A",))
    k.script = {"A": [rec("A")]}
    go(env, k)
    st = state_of(env)
    assert st["last_full_suite"] == {"commit": "258e3c0", "result": "4,753 passed, 0 failed"}
    for key in ("updated", "head", "branch", "waves", "parked", "attempts", "escalations",
                "spend_usd_total", "counts", "unresolved_p0", "next_ready", "stop_reason"):
        assert key in st
    assert st["head"] == "abc1234" and st["branch"] == "build/bh-d"
    assert st["waves"][0]["n"] == 1 and st["waves"][0]["reqs"] == ["A"]


def test_last_full_suite_is_preserved(env):
    env.state.parent.mkdir(parents=True)
    suite = {"commit": "deadbee", "result": "5,000 passed, 0 failed"}
    env.state.write_text(json.dumps({"last_full_suite": suite}), encoding="utf-8")
    k = FakeKernel(ids=("A",))
    k.script = {"A": [rec("A")]}
    go(env, k)
    assert state_of(env)["last_full_suite"] == suite


def test_state_is_persisted_after_every_wave(env):
    k = FakeKernel(ids=("A", "B"))
    k.script = {"A": [rec("A")], "B": [rec("B")]}
    seen = []
    orig = k.watch

    def watch(runs):
        if env.state.exists():
            seen.append(len(state_of(env)["waves"]))
        return orig(runs)

    k.watch = watch
    go(env, k, cap=1)
    assert seen == [1]  # when wave 2 starts, wave 1 is already on disk


def test_state_write_is_atomic(env, monkeypatch):
    calls = []
    real = BP.os.replace
    monkeypatch.setattr(BP.os, "replace", lambda a, b: (calls.append((Path(a).name, Path(b).name)), real(a, b)))
    k = FakeKernel(ids=("A",))
    k.script = {"A": [rec("A")]}
    go(env, k)
    assert ("build_state.json.tmp", "build_state.json") in calls
    assert not list(env.state.parent.glob("*.tmp"))


# ------------------------------------------------------------------ doc block

def test_marker_block_appended_and_outside_bytes_unchanged_crlf(env):
    before = b"# Hand written\r\n\r\nline two\r\n"
    env.doc.write_bytes(before)
    k = FakeKernel(ids=("A",))
    k.script = {"A": [rec("A")]}
    go(env, k)
    after = env.doc.read_bytes()
    assert after.startswith(before)
    tail = after[len(before):]
    assert tail.startswith(BP.BEGIN) and tail.rstrip().endswith(BP.END)
    assert b"\n" not in tail.replace(b"\r\n", b"")


def test_marker_block_replaced_in_place(env):
    head, foot = b"top\nkeep\n", b"\nbottom\n"
    env.doc.write_bytes(head + BP.BEGIN + b"\nstale\n" + BP.END + foot)
    k = FakeKernel(ids=("A",))
    k.script = {"A": [rec("A")]}
    go(env, k)
    after = env.doc.read_bytes()
    assert after.startswith(head + BP.BEGIN) and after.endswith(BP.END + foot)
    assert b"stale" not in after and after.count(BP.BEGIN) == 1


def test_marker_block_within_40_lines_and_lists_the_facts(env):
    k = FakeKernel(ids=("A",))
    k.script = {"A": [rec("A", "BLOCKED", blockers=["x" * 500])]}
    go(env, k)
    text = env.doc.read_text(encoding="utf-8")
    block = text[text.index(BP.BEGIN.decode()):text.index(BP.END.decode())].splitlines()
    assert len(block) <= 40
    for needle in ("HEAD: abc1234", "Unresolved P0", "Last full suite", "Parked: A (blocked)",
                   "Open escalations", "Next ready"):
        assert needle in text


def test_block_is_capped_even_with_many_escalations():
    snap = {"head": "h", "branch": "b", "updated": "u", "counts": {}, "unresolved_p0": 0,
            "last_full_suite": {"commit": "c", "result": "r"}, "waves": [], "next_ready": [],
            "parked": {f"R{i}": "blocked" for i in range(100)},
            "escalations": [{"req": f"R{i}", "kind": "k", "detail": "d", "at": "t"} for i in range(100)]}
    assert len(BP.block_lines(snap)) <= 40


# ------------------------------------------------------------------ end of run

def test_end_renders_ledger_then_commits_docs_with_identity_and_message(env):
    k = FakeKernel(ids=("A",))
    k.script = {"A": [rec("A")]}
    go(env, k)
    cmds = env.git.cmds
    render = next(i for i, c in enumerate(cmds) if "prd_ledger.py" in " ".join(c))
    (commit,) = env.git.commits()
    assert cmds.index(commit) > render
    assert commit[:5] == ["git", "-c", "user.name=DarshPro07", "-c", "user.email=DarshPro07"]
    assert commit[-1] == "docs(state): build program stopped after wave 1 (no ready work)"
    assert ["git", "add", "-u", "--", "docs/implementation"] in cmds


def test_ledger_render_failure_records_escalation_and_commits_nothing(env):
    env.git.ledger_rc = 1
    k = FakeKernel(ids=("A",))
    k.script = {"A": [rec("A")]}
    go(env, k)
    assert env.git.commits() == []
    esc = state_of(env)["escalations"]
    assert esc[-1]["kind"] == "ledger_render" and "render failed" in esc[-1]["detail"]


def test_nothing_changed_means_no_commit(env):
    env.git.changed = ""
    k = FakeKernel(ids=("A",))
    k.script = {"A": [rec("A")]}
    go(env, k)
    assert env.git.commits() == []


# ------------------------------------------------------------------ dry run

def test_dry_run_prints_first_wave_and_writes_nothing(env):
    k = FakeKernel(ids=("A", "B"), modes={"B": "SCOUT"}, klass="MICRO")
    lines = []
    res = BP.run_program(dry_run=True, cap=2, repo=Path("."), state_file=env.state, state_doc=env.doc,
                         worktrees=env.wt, k=k, bi=FakeIntegrator(k), runner=env.git, out=lines.append)
    assert lines == ["A SHIP MICRO", "B SCOUT SCOUT"]
    assert res["stop_reason"] == "dry run"
    assert not k.launches and not k.watched
    assert not env.state.exists() and not env.doc.exists()
    assert env.git.commits() == [] and not any("prd_ledger" in " ".join(c) for c in env.git.cmds)


def test_dry_run_does_not_park_stale_worktrees(env):
    (env.wt / "a").mkdir()
    k = FakeKernel(ids=("A",))
    BP.run_program(dry_run=True, repo=Path("."), state_file=env.state, state_doc=env.doc, worktrees=env.wt,
                   k=k, bi=FakeIntegrator(k), runner=env.git, out=lambda *_: None)
    assert not env.state.exists()


def test_cli_arguments(monkeypatch):
    seen = {}
    monkeypatch.setattr(BP, "run_program", lambda **kw: seen.update(kw) or {"stop_reason": "x"})
    assert BP.main(["--max-waves", "3", "--max-usd", "7", "--cap", "1", "--priority", "P0", "--dry-run"]) == 0
    assert seen == {"max_waves": 3, "max_usd": 7.0, "cap": 1, "priority": "P0", "dry_run": True,
                    "max_wave_usd": None}


# ------------------------------------------------------------ real-git hazards

def test_tracked_state_doc_is_untouched_until_the_program_finishes(env):
    # build_integrate refuses a checkout with tracked changes. Rewriting the
    # tracked PRD_CURRENT_STATE.md after wave 1 made every later wave refused.
    env.doc.write_bytes(b"# hand-written\n")
    k = FakeKernel(ids=("A", "B", "C"))
    k.script = {i: [rec(i)] for i in ("A", "B", "C")}
    seen = []

    class Watching(FakeIntegrator):
        def integrate(self, req):
            seen.append(env.doc.read_bytes())
            return super().integrate(req)

    _, bi = go(env, k, bi=Watching(k))
    assert bi.calls == ["A", "B", "C"] and len(state_of(env)["waves"]) == 2
    assert seen == [b"# hand-written\n"] * 3
    assert b"build-program:begin" in env.doc.read_bytes()


def test_a_requirement_left_partial_twice_is_parked_across_runs(env):
    # PARTIAL rows stay in the ready queue; without a cap every program run
    # would pay for the same unclosable gap again.
    k = FakeKernel(ids=("A",))
    k.script = {"A": [rec("A"), rec("A"), rec("A")]}

    class Partial(FakeIntegrator):
        def integrate(self, req):
            return {**super().integrate(req), "ledger_status": "PARTIAL"}

    for _ in range(3):
        go(env, k, bi=Partial(k, close=False))
    assert [x[0] for x in k.launches] == ["A", "A"]
    assert "A" in state_of(env)["parked"]


@pytest.mark.parametrize("progress, slices", [(True, 4), (False, 2)])
def test_a_partial_row_that_makes_progress_runs_on_and_one_that_stalls_parks(env, progress, slices):
    # 2026-10-08: parking at two PARTIAL slices made 25 of 55 parks, most of them rows
    # that were advancing. Different gaps each time is progress, up to MAX_PARTIALS.
    k = FakeKernel(ids=("A",))
    k.script = {"A": [rec("A")] * 6}
    calls = []

    class Partial(FakeIntegrator):
        def integrate(self, req):
            calls.append(req)
            gaps = [f"gap {len(calls)}"] if progress else ["the same gap"]
            return {**super().integrate(req), "ledger_status": "PARTIAL", "gaps": gaps}

    for _ in range(6):
        go(env, k, bi=Partial(k, close=False))
    assert [x[0] for x in k.launches] == ["A"] * slices
    assert state_of(env)["parked"]["A"] == f"partial x{slices}"
    assert BP.MAX_PARTIALS == 4


def test_a_failed_docs_commit_is_an_escalation_not_silence(env):
    k = FakeKernel(ids=("A",))
    k.script = {"A": [rec("A")]}

    class CommitFails(FakeGit):
        def __call__(self, cmd, cwd, timeout=60):
            res = super().__call__(cmd, cwd, timeout)
            if "commit" in cmd:
                return subprocess.CompletedProcess(cmd, 1, "", "index.lock exists")
            return res

    env.git = CommitFails()
    go(env, k)
    esc = state_of(env)["escalations"]
    assert esc and esc[-1]["kind"] == "docs_commit" and "index.lock" in esc[-1]["detail"]


# ------------------------------------------------------------------ repair loop

class Sequence(FakeIntegrator):
    """integrate() answers each call from a per-requirement list, the last one repeating."""

    def __init__(self, kernel, seq):
        super().__init__(kernel)
        self.seq = {r: list(v) for r, v in seq.items()}

    def integrate(self, req):
        self.results[req] = self.seq[req].pop(0) if len(self.seq[req]) > 1 else self.seq[req][0]
        return super().integrate(req)


@pytest.mark.parametrize("verdict", ["VERIFIER_FAIL", "NOT_RED", "NOT_GREEN", "NO_TEST", "FAILED_INTEGRATION"])
def test_a_rejected_change_is_repaired_once_then_integrated(env, verdict):
    k = FakeKernel(ids=("A",))
    k.script = {"A": [rec("A"), rec("A")]}
    res, bi = go(env, k, bi=Sequence(k, {"A": [verdict, "INTEGRATED"]}))
    st = state_of(env)
    assert k.repairs == ["A"] and [x[0] for x in k.launches] == ["A"]
    assert bi.calls == ["A", "A"] and "A" not in st["parked"]
    assert st["repairs"] == {"A": 1} and st["repair_queue"] == {}


def test_repeated_failure_climbs_the_cascade_then_parks(env):
    k = FakeKernel(ids=("A",))
    k.script = {"A": [rec("A"), rec("A"), rec("A", kind="ADVISOR"), rec("A")]}
    _, bi = go(env, k, bi=Sequence(k, {"A": ["VERIFIER_FAIL"]}))
    st = state_of(env)
    # Owner v12 section 9: a strong-model advisor diagnoses, a normal implementer applies it.
    assert k.steps == ["REPAIR", "ADVISOR", "IMPLEMENTER"]
    assert bi.calls == ["A"] * 3  # the advisor's diagnosis is never integrated
    assert st["parked"]["A"] == "VERIFIER_FAIL" and st["repair_queue"] == {}
    assert st["repairs"] == {"A": 3} and st["cascade"] == {"A": {"step": "IMPLEMENTER", "n": 3}}


def test_the_cascade_position_is_recorded_per_requirement(env):
    k = FakeKernel(ids=("A",))
    k.script = {"A": [rec("A"), rec("A")]}
    go(env, k, bi=Sequence(k, {"A": ["VERIFIER_FAIL", "INTEGRATED"]}))
    assert state_of(env)["cascade"] == {"A": {"step": "REPAIR", "n": 1}}


def test_a_failed_advisor_parks_instead_of_building_blind(env):
    k = FakeKernel(ids=("A",))
    k.script = {"A": [rec("A"), rec("A"), rec("A", "FAILED", kind="ADVISOR")]}
    go(env, k, bi=Sequence(k, {"A": ["VERIFIER_FAIL"]}))
    st = state_of(env)
    assert k.steps == ["REPAIR", "ADVISOR"] and st["parked"]["A"] == "advisor failed"
    assert any(e["kind"] == "advisor" for e in st["escalations"])


def test_a_clean_rebase_is_done_by_the_program_and_integrated(env):
    # 2026-10-03/04: SEC-005 and CTX-002 finished, then needed only a clean rebase onto main
    # (the ledger or a neighbour's file had moved), and each sat parked until the controller did it.
    k = FakeKernel(ids=("A",))
    k.script = {"A": [rec("A")]}
    k.rebases_clean = {"A"}
    bi = FakeIntegrator(k, {"A": ["REBASE_REQUIRED", "INTEGRATED"]})
    go(env, k, bi=bi, max_waves=1)
    assert bi.calls == ["A", "A"] and k.rebased == ["A"] and "A" in k.closed
    assert "A" not in state_of(env)["parked"]


def test_a_rebase_required_candidate_is_parked_with_a_rebase_escalation(env):
    k = FakeKernel(ids=("A",))
    k.script = {"A": [rec("A")]}
    go(env, k, bi=FakeIntegrator(k, {"A": "REBASE_REQUIRED"}))
    st = state_of(env)
    assert k.repairs == [] and st["parked"] == {"A": "REBASE_REQUIRED"}
    assert st["escalations"][0]["kind"] == "rebase"


def planted(attempt_id="job-1", req="A", pid=None):
    return {"attempt_id": attempt_id, "req": req, "role": "IMPLEMENTER", "state": "STARTED",
            "pid": os.getpid() if pid is None else pid, "worktree": "wt"}


def test_a_started_job_with_a_live_pid_is_watched_not_relaunched(env, tmp_path):
    k = FakeKernel(ids=("A",))
    k.planted_jobs = [planted()]
    k.script = {"A": [rec("A")]}
    res, bi = go(env, k, runs_dir=tmp_path / "runs")
    assert k.adopted == ["A"] and k.launches == [] and bi.calls == ["A"]
    assert k.finalized == []


def test_a_dead_job_with_a_finished_stream_is_finalized_not_relaunched(env, tmp_path, monkeypatch):
    monkeypatch.setattr(BP, "pid_alive", lambda pid: False)
    k = FakeKernel(ids=("A",))
    k.planted_jobs, k.finished = [planted(pid=99999999)], True
    _, bi = go(env, k, runs_dir=tmp_path / "runs")
    assert k.finalized == ["A"] and bi.calls == ["A"] and k.launches == []


def _stranded(env, tmp_path, *, parked=None, integrated_after=False):
    """A finished run with a commit and its worktree, as a program killed mid-integration leaves it."""
    runs, wt = tmp_path / "runs", env.wt / "a"
    runs.mkdir()
    wt.mkdir()
    path = runs / "A.json"
    path.write_text(json.dumps(dict(rec("A"), commit="c0ffee", worktree=str(wt))), encoding="utf-8")
    os.utime(path, (1000, 1000))
    if integrated_after:
        (runs / "A.integration.json").write_text("{}", encoding="utf-8")
        os.utime(runs / "A.integration.json", (2000, 2000))
    if parked:
        env.state.parent.mkdir(parents=True, exist_ok=True)
        env.state.write_text(json.dumps({"parked": {"A": parked}}), encoding="utf-8")
    return runs


def test_a_merge_queue_timeout_parks_the_run_as_busy_not_refused(env):
    # 2026-10-09: MEM-003 waited 1800 s behind a 35-minute gate and was parked "integration
    # refused", leaving a finished run for a controller to re-integrate by hand.
    k = FakeKernel(ids=("A",))
    k.script = {"A": [rec("A")]}
    busy = BP.build_integrate.MergeQueueBusy("merge queue: A waited 1800s behind B")
    go(env, k, bi=FakeIntegrator(k, {"A": busy}))
    assert state_of(env)["parked"]["A"] == BP.QUEUE_BUSY


@pytest.mark.parametrize("parked", [None, "stale worktree", "merge queue busy"])
def test_a_finished_run_whose_integration_never_ended_is_integrated_at_start(env, tmp_path, parked):
    # 2026-10-09: the program integrating BRAIN-001 died with the tool call that launched it; the next
    # program found the worktree, could not resume a finished worker, and parked it "stale worktree"
    # until a controller re-integrated it by hand.
    k = FakeKernel(ids=("A",))
    _, bi = go(env, k, runs_dir=_stranded(env, tmp_path, parked=parked), max_waves=0)
    assert bi.calls == ["A"] and k.launches == [] and "A" not in state_of(env)["parked"]


@pytest.mark.parametrize("parked, integrated_after", [(None, True), ("blocked", False)])
def test_a_finished_run_already_integrated_or_parked_otherwise_is_left(env, tmp_path, parked, integrated_after):
    k = FakeKernel(ids=("A",))
    _, bi = go(env, k, runs_dir=_stranded(env, tmp_path, parked=parked, integrated_after=integrated_after),
               max_waves=0)
    assert bi.calls == []


def test_a_dead_job_without_a_result_is_marked_failed_for_the_resume_path(env, tmp_path, monkeypatch):
    monkeypatch.setattr(BP, "pid_alive", lambda pid: False)
    k = FakeKernel(ids=("A",))
    k.planted_jobs = [planted(pid=99999999)]
    go(env, k, runs_dir=tmp_path / "runs", max_waves=0)
    assert k.closed_jobs == [("job-1", "FAILED")] and k.finalized == [] and k.adopted == ["A"]


@pytest.mark.parametrize("verdict", ["CONFLICT", "REFUSED"])
def test_a_conflict_or_refusal_is_never_repaired(env, verdict):
    k = FakeKernel(ids=("A",))
    k.script = {"A": [rec("A")]}
    res = "CONFLICT" if verdict == "CONFLICT" else KErr("refused")
    go(env, k, bi=FakeIntegrator(k, {"A": res}))
    st = state_of(env)
    assert k.repairs == [] and "A" in st["parked"] and st["repair_queue"] == {}


def test_a_queued_repair_survives_a_restart(env):
    k = FakeKernel(ids=("A",))
    k.script = {"A": [rec("A"), rec("A")]}
    go(env, k, bi=Sequence(k, {"A": ["NO_TEST", "INTEGRATED"]}), max_waves=1)
    queued = state_of(env)["repair_queue"]
    assert list(queued) == ["A"] and queued["A"]["result"] == "NO_TEST" and queued["A"]["at"]
    (env.wt / "a").mkdir()  # the rejected worktree is still on disk: not a stale one
    go(env, k, bi=Sequence(k, {"A": ["INTEGRATED"]}))
    assert k.repairs == ["A"] and state_of(env)["repair_queue"] == {} and "A" not in state_of(env)["parked"]


def test_a_failed_repair_launch_parks_with_an_escalation(env):
    k = FakeKernel(ids=("A",))
    k.script = {"A": [rec("A")]}
    k.repair_errors = {"A": "no session"}
    go(env, k, bi=Sequence(k, {"A": ["VERIFIER_FAIL"]}))
    st = state_of(env)
    assert st["parked"]["A"] == "repair failed" and st["repair_queue"] == {}
    assert any(e["kind"] == "repair" and "no session" in e["detail"] for e in st["escalations"])


def test_repairs_launch_first_and_count_toward_concurrency(env):
    k = FakeKernel(ids=("A", "B"), level=1)
    k.script = {"A": [rec("A"), rec("A")], "B": [rec("B")]}
    go(env, k, bi=Sequence(k, {"A": ["VERIFIER_FAIL", "INTEGRATED"], "B": ["INTEGRATED"]}), max_waves=2)
    st = state_of(env)
    assert [w["reqs"] for w in st["waves"]] == [["A"], ["A"]]  # the repair took the only slot
    assert k.repairs == ["A"] and [x[0] for x in k.launches] == ["A"]


def test_load_state_keeps_repairs_and_the_repair_queue(tmp_path):
    f = tmp_path / "s.json"
    f.write_text(json.dumps({"repairs": {"A": 1}, "repair_queue": {"B": {"result": "NO_TEST", "at": "t"}}}))
    st = BP.load_state(f)
    assert st["repairs"] == {"A": 1} and st["repair_queue"]["B"]["result"] == "NO_TEST"
    assert BP.load_state(tmp_path / "none.json")["repair_queue"] == {}


# ------------------------------------------------------------------ integrator crash

class CrashGit(FakeGit):
    """HEAD moves when the 'integrator' commits; `git reset --hard` puts it back."""

    def __init__(self, parent_ok=True, reset_rc=0):
        super().__init__()
        self.head, self.parent, self.parent_ok, self.reset_rc = "base0", "base0", parent_ok, reset_rc

    def __call__(self, cmd, cwd, timeout=60):
        if cmd[:2] == ["git", "rev-parse"]:
            self.cmds.append(cmd)
            out = self.head if cmd[2] == "HEAD" else (self.parent if self.parent_ok else "other")
            return subprocess.CompletedProcess(cmd, 0, out, "")
        if cmd[:3] == ["git", "reset", "--hard"]:
            self.cmds.append(cmd)
            if self.reset_rc == 0:
                self.head = cmd[3]
            return subprocess.CompletedProcess(cmd, self.reset_rc, "", "locked" if self.reset_rc else "")
        return super().__call__(cmd, cwd, timeout)


class CommitThenCrash(FakeIntegrator):
    def __init__(self, kernel, git, exc):
        super().__init__(kernel)
        self.git, self.exc = git, exc

    def integrate(self, req):
        self.calls.append(req)
        self.git.head = "picked1"  # the cherry-pick landed, then the gate blew up
        raise self.exc


def test_an_integrator_crash_after_the_pick_resets_the_branch_and_parks(env):
    env.git = CrashGit()
    k = FakeKernel(ids=("A",))
    k.script = {"A": [rec("A")]}
    bi = CommitThenCrash(k, env.git, subprocess.TimeoutExpired("pytest", 900))
    res, _ = go(env, k, bi)
    assert env.git.head == "base0" and ["git", "reset", "--hard", "base0"] in env.git.cmds
    esc = state_of(env)["escalations"]
    assert esc[0]["kind"] == "integration_crash" and "TimeoutExpired" in esc[0]["detail"]
    assert "A" in state_of(env)["parked"] and res["stop_reason"] != "integration crash"


def test_an_unrecoverable_integrator_crash_stops_the_run(env):
    env.git = CrashGit(parent_ok=False)  # HEAD moved, and HEAD^ is not the recorded head
    k = FakeKernel(ids=("A", "B"))
    k.script = {"A": [rec("A")], "B": [rec("B")]}
    bi = CommitThenCrash(k, env.git, RuntimeError("boom"))
    res, _ = go(env, k, bi)
    assert res["stop_reason"] == "integration crash"
    assert state_of(env)["escalations"][0]["kind"] == "integration_crash_unrecovered"
    assert not any(c[:3] == ["git", "reset", "--hard"] for c in env.git.cmds)
    assert bi.calls == ["A"]  # nothing else is integrated on top of an unverified commit


def test_a_failed_reset_after_a_crash_is_unrecovered(env):
    env.git = CrashGit(reset_rc=1)
    k = FakeKernel(ids=("A",))
    k.script = {"A": [rec("A")]}
    res, _ = go(env, k, CommitThenCrash(k, env.git, RuntimeError("boom")))
    assert res["stop_reason"] == "integration crash"
    assert state_of(env)["escalations"][0]["kind"] == "integration_crash_unrecovered"


def test_a_test_timeout_parks_as_test_timeout_and_is_not_repaired(env):
    k = FakeKernel(ids=("A",))
    k.script = {"A": [rec("A")]}
    go(env, k, FakeIntegrator(k, results={"A": "TEST_TIMEOUT"}))
    st = state_of(env)
    assert st["escalations"][0]["kind"] == "test_timeout" and not st["repair_queue"]


# ------------------------------------------------------- ledger-based spend

class LedgerKernel(FakeKernel):
    """The ledger says what each invocation cost, whatever the run record claims."""

    def __init__(self, *a, ledger_cost=0.5, **kw):
        super().__init__(*a, **kw)
        self.ledger_cost, self.seen_env = ledger_cost, []

    def watch(self, runs):
        recs = [self.script[r.req].pop(0) for r in runs]
        self.seen_env.append((os.environ.get("FRIDAY_BUILD_RUN_ID"), os.environ.get("FRIDAY_BUILD_WAVE_ID")))
        for _ in recs:
            self.ledger.append({"program_run_id": os.environ["FRIDAY_BUILD_RUN_ID"],
                                "wave_id": os.environ["FRIDAY_BUILD_WAVE_ID"],
                                "cost_usd": self.ledger_cost})
        return recs


def test_spend_comes_from_the_ledger_not_the_run_record(env):
    k = LedgerKernel(ids=("A",))
    k.script = {"A": [rec("A", cost=99.0)]}
    go(env, k, bi=FakeIntegrator(k, verifier_cost=0.25), max_waves=1)
    st = state_of(env)
    assert st["spend_usd_total"] == 0.75 and st["waves"][0]["spend"] == 0.75


def test_the_run_stops_on_ledger_spend_not_record_spend(env):
    k = LedgerKernel(ids=("A", "B", "C"), ledger_cost=6.0)
    k.script = {i: [rec(i, cost=0.0)] for i in "ABC"}
    res, _ = go(env, k, max_usd=10.0, cap=1)
    assert res["stop_reason"] == "spend" and len(state_of(env)["waves"]) == 2


def test_waves_carry_the_run_and_wave_ids_and_the_env_is_restored(env, monkeypatch):
    monkeypatch.delenv("FRIDAY_BUILD_RUN_ID", raising=False)
    monkeypatch.delenv("FRIDAY_BUILD_WAVE_ID", raising=False)
    k = LedgerKernel(ids=("A", "B"))
    k.script = {"A": [rec("A")], "B": [rec("B")]}
    go(env, k, cap=1)
    (r1, w1), (r2, w2) = k.seen_env
    assert r1 == r2 and w1 == f"{r1}-w1" and w2 == f"{r1}-w2"
    assert "FRIDAY_BUILD_RUN_ID" not in os.environ and "FRIDAY_BUILD_WAVE_ID" not in os.environ


# ----------------------------------------------------------------- program lock

def _dead_pid():
    p = subprocess.Popen([sys.executable, "-c", "pass"])
    p.wait()
    return p.pid


def test_a_held_lock_refuses_the_program_and_is_left_alone(env):
    lock = env.state.parent / "build_program.lock"
    lock.parent.mkdir(parents=True)
    lock.write_text(json.dumps({"pid": os.getpid(), "started": "now"}), encoding="utf-8")
    k = FakeKernel(ids=("A",))
    res, _ = go(env, k)
    assert res == {"stop_reason": "another program is running", "waves": []}
    assert k.launches == [] and lock.exists()
    assert json.loads(lock.read_text())["pid"] == os.getpid()


def test_a_stale_lock_is_replaced_and_released_afterwards(env):
    lock = env.state.parent / "build_program.lock"
    lock.parent.mkdir(parents=True)
    lock.write_text(json.dumps({"pid": _dead_pid(), "started": "then"}), encoding="utf-8")
    k = FakeKernel(ids=("A",))
    k.script = {"A": [rec("A")]}
    res, _ = go(env, k)
    assert res["stop_reason"] != "another program is running" and k.launches
    assert not lock.exists()


def test_the_lock_is_held_during_the_run_and_released_when_it_raises(env):
    lock = env.state.parent / "build_program.lock"
    held = []

    class Boom(FakeKernel):
        def load_rows(self):
            held.append(json.loads(lock.read_text())["pid"])
            raise RuntimeError("boom")

    with pytest.raises(RuntimeError):
        go(env, Boom(ids=("A",)))
    assert held == [os.getpid()] and not lock.exists()


def test_an_unreadable_lock_is_stale(tmp_path):
    lock = tmp_path / "x.lock"
    lock.write_text("{half", encoding="utf-8")
    assert BP.acquire_lock(lock) is True
    assert BP.acquire_lock(lock) is False  # now held by this live process
    BP.release_lock(lock)
    assert not lock.exists()


def test_main_exits_3_when_another_program_is_running(monkeypatch, capsys):
    monkeypatch.setattr(BP, "run_program", lambda **kw: {"stop_reason": BP.BUSY, "waves": []})
    assert BP.main([]) == 3
    assert "another program is running" in capsys.readouterr().out


class RecordingLock:
    """Records when the checkout turn is held, for the race test below."""

    def __init__(self):
        self.events: list[str] = []

    def __call__(self, label):
        lock = self

        class _Turn:
            def __enter__(self):
                lock.events.append(f"enter:{label}")

            def __exit__(self, *exc):
                lock.events.append(f"exit:{label}")
                return False
        return _Turn()


def test_the_program_writes_tracked_files_only_inside_a_checkout_turn(env):
    # 2026-10-02: ACT-001's integration got its queue turn while the program's
    # finish() was re-rendering the ledger docs - outside the queue - saw the
    # half-written tree and refused. The program's tracked writes take the same turn.
    k = FakeKernel(ids=("A",))
    k.script = {"A": [rec("A")]}
    lock = RecordingLock()
    calls = []

    class Recording(FakeGit):
        def __call__(self, cmd, cwd, timeout=60):
            if "commit" in cmd or "prd_ledger.py" in " ".join(cmd):
                calls.append(("held" if lock.events and lock.events[-1].startswith("enter") else "free", cmd[1]))
            return super().__call__(cmd, cwd, timeout)

    env.git = Recording()
    BP.run_program(repo=Path("."), state_file=env.state, state_doc=env.doc, worktrees=env.wt, k=k,
                   bi=FakeIntegrator(k), runner=env.git, out=lambda *_: None, max_waves=1, max_usd=100.0,
                   checkout_turn=lock)
    assert calls and all(where == "held" for where, _ in calls), calls
    assert lock.events[0].startswith("enter:program") and lock.events[-1].startswith("exit:program")



def test_a_cascade_step_whose_worker_fails_hands_on_to_the_next_step(env):
    # 2026-10-02: EVAL-001's escalation worker hit its turn cap (FAILED) and the
    # requirement fell out of the cascade. A failed step queues the next one.
    k = FakeKernel(ids=("A",))
    k.script = {"A": [rec("A"), rec("A", "FAILED"), rec("A", kind="ADVISOR"), rec("A")]}
    go(env, k, bi=Sequence(k, {"A": ["VERIFIER_FAIL"]}))
    st = state_of(env)
    assert k.steps == ["REPAIR", "ADVISOR", "IMPLEMENTER"]
    assert st["parked"]["A"] == "VERIFIER_FAIL"



def test_the_controllers_inbox_is_applied_under_the_lock_and_archived(env, tmp_path):
    # 2026-10-04: a waiter that edited the state file between runs died to a quoting bug
    # and to memory reaping; the program now applies the controller's edits itself.
    runs = tmp_path / "runs"
    runs.mkdir()
    (runs / BP.INBOX).write_text(
        json.dumps({"action": "requeue", "req": "A", "step": 2, "why": "diagnosed"}) + "\n"
        + json.dumps({"action": "unpark", "req": "B"}) + "\n" + "not json\n", encoding="utf-8")
    env.state.parent.mkdir(parents=True, exist_ok=True)
    env.state.write_text(json.dumps({"parked": {"A": "TEST_TIMEOUT", "B": "blocked"}}), encoding="utf-8")
    k = FakeKernel(ids=(), level=0)
    go(env, k, runs_dir=runs)
    st = state_of(env)
    assert st["parked"] == {} and st["repairs"]["A"] == 2 and "A" in st["repair_queue"]
    assert not (runs / BP.INBOX).exists() and list(runs.glob("controller_inbox.applied-*.jsonl"))


def test_a_dry_run_plans_with_the_inbox_and_leaves_it_queued(env, tmp_path):
    # 2026-10-08: the chain's readiness check is a dry run. Blind to the inbox, it saw only a
    # 4 GB P0 that could not fit, so the real run that applies 37 queued unparks never started.
    runs = tmp_path / "runs"
    runs.mkdir()
    (runs / BP.INBOX).write_text(json.dumps({"action": "unpark", "req": "B"}) + "\n", encoding="utf-8")
    env.state.parent.mkdir(parents=True, exist_ok=True)
    env.state.write_text(json.dumps({"parked": {"B": "partial x2"}}), encoding="utf-8")
    res, _ = go(env, FakeKernel(ids=("B",)), runs_dir=runs, dry_run=True)
    assert res["stop_reason"] == "dry run"                       # B is planned, not "no ready work"
    assert (runs / BP.INBOX).exists()                            # still queued for the real run
    assert state_of(env)["parked"] == {"B": "partial x2"}         # a dry run writes no state


def test_a_confirmed_row_is_progress_not_a_park(env):
    # 2026-10-09: a run that changed nothing because the acceptance was already met is
    # confirmed on HEAD (VERIFIED) - progress, never a park.
    k = FakeKernel(ids=("A",))
    k.script = {"A": [rec("A")]}

    class Confirms(FakeIntegrator):
        def integrate(self, req):
            self.k.closed.add(req)
            return {"requirement": req, "result": "CONFIRMED", "verifier": None}

    res, _ = go(env, k, bi=Confirms(k))
    assert "A" not in state_of(env)["parked"] and [x[0] for x in k.launches] == ["A"]
    assert res["stop_reason"] != "no progress"
