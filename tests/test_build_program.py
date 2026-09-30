"""scripts/build_program.py with a fake kernel, fake integrator and fake git: no claude, no worktree,
no real ledger or state file."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import build_program as BP  # noqa: E402
import prd_ledger as PL  # noqa: E402


class KErr(Exception):
    pass


class FakeKernel:
    KernelError = KErr
    BUDGETS = {"SCOUT": (8, 0.75, 1), "MICRO": (12, 1.0, 1), "NORMAL": (25, 3.0, 1),
               "COMPLEX": (40, 6.0, 1), "P0": (40, 6.0, 1)}
    VENV_PY = "py"
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

    def load_rows(self):
        return [r for r in self.rows]

    def load_config(self):
        return {"depends": {}, "order": []}

    def ready(self, rows, depends, order):
        return [r for r in rows if r["id"] not in self.closed]

    def waves(self, queue, cap):
        return [list(queue)]

    def concurrency(self, cap=2):
        return min(self.level, cap)

    def mode_for(self, row):
        return self.modes.get(row["id"], "SHIP")

    def budget_class(self, row):
        return "P0" if row["priority"] == "P0" else self.klass

    def code_map(self, row):
        return f"map-{row['id']}"

    def launch(self, row, *, mode, capsule=""):
        if row["id"] in self.launch_errors:
            raise KErr(self.launch_errors[row["id"]])
        self.launches.append((row["id"], mode, capsule))
        return SimpleNamespace(req=row["id"])

    def watch(self, runs):
        self.watched += 1
        return [self.script[r.req].pop(0) for r in runs]

    def _worktrees_root(self):
        raise AssertionError("tests pass worktrees")


class FakeIntegrator:
    def __init__(self, kernel, results=None, verifier_cost=0.0, close=True):
        self.k, self.results, self.cost, self.close = kernel, results or {}, verifier_cost, close
        self.calls: list[str] = []

    def integrate(self, req):
        self.calls.append(req)
        res = self.results.get(req, "INTEGRATED")
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


def rec(req, state="DONE", mode="SHIP", cost=0.5, blockers=None):
    return {"requirement": req, "state": state, "mode": mode, "cost_usd": cost,
            "evidence": {"blockers": blockers or []}}


@pytest.fixture
def env(tmp_path):
    wt = tmp_path / "worktrees"
    wt.mkdir()
    return SimpleNamespace(state=tmp_path / "data" / "build_state.json",
                           doc=tmp_path / "PRD_CURRENT_STATE.md", wt=wt, git=FakeGit())


def go(env, k, bi=None, **kw):
    bi = bi or FakeIntegrator(k)
    kw.setdefault("max_waves", 5)
    kw.setdefault("max_usd", 100.0)
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


# ------------------------------------------------------------------ stop reasons

def test_no_ready_work_stop(env):
    res, _ = go(env, FakeKernel(ids=()))
    assert res["stop_reason"] == "no ready work"
    assert state_of(env)["stop_reason"] == "no ready work"


def test_pressure_stop(env):
    k = FakeKernel(level=0)
    res, _ = go(env, k)
    assert res["stop_reason"] == "pressure" and not k.launches


def test_max_waves_stop(env):
    k = FakeKernel(ids=("A", "B", "C"))
    k.script = {i: [rec(i)] for i in "ABC"}
    res, _ = go(env, k, max_waves=1, cap=1)
    assert res["stop_reason"] == "max waves"
    assert [w["reqs"] for w in state_of(env)["waves"]] == [["A"]]


def test_spend_stop_after_a_wave_reaches_the_limit(env):
    k = FakeKernel(ids=("A", "B"))
    k.script = {i: [rec(i, cost=4.0)] for i in "AB"}
    res, _ = go(env, k, cap=1, max_usd=3.0)
    assert res["stop_reason"] == "spend"
    assert k.launches == [("A", "SHIP", "map-A")]


def test_no_progress_after_two_idle_waves(env):
    k = FakeKernel(ids=("A", "B", "C"))
    k.script = {i: [rec(i, "BLOCKED")] for i in "ABC"}
    res, _ = go(env, k, cap=1)
    assert res["stop_reason"] == "no progress"
    assert len(state_of(env)["waves"]) == 2
    assert k.watched == 2


def test_a_progress_wave_resets_the_idle_count(env):
    k = FakeKernel(ids=("A", "B", "C", "D"))
    k.script = {"A": [rec("A", "BLOCKED")], "B": [rec("B")], "C": [rec("C", "BLOCKED")],
                "D": [rec("D", "BLOCKED")]}
    res, _ = go(env, k, cap=1)
    assert res["stop_reason"] == "no progress"
    assert len(state_of(env)["waves"]) == 4


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
    res, bi = go(env, k)
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
    go(env, k, max_waves=1, cap=2, max_usd=1.6)  # 0.75 * 2 fits, NORMAL would not
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
    assert seen == {"max_waves": 3, "max_usd": 7.0, "cap": 1, "priority": "P0", "dry_run": True}
