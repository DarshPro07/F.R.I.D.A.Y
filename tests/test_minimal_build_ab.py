"""
CODE-001: the minimal-build policy A/B harness (scripts/minimal_build_ab.py).

Everything here runs with the fake runner or a stub: no model is called and nothing is
spent. The git cases use a temp repository, never this one.
"""

from __future__ import annotations

import json
import subprocess
import sys
from dataclasses import replace
from datetime import datetime
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import build_kernel as K  # noqa: E402
import minimal_build_ab as M  # noqa: E402


def _git(repo: Path, *args: str) -> str:
    done = subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@example.invalid", *args],
                          cwd=str(repo), capture_output=True, text=True, timeout=60)
    assert done.returncode == 0, done.stderr
    return done.stdout.strip()


def _repo(tmp_path: Path, files: dict | None = None) -> tuple[Path, str]:
    repo = tmp_path / "repo"
    (repo / "friday").mkdir(parents=True)
    (repo / "friday" / "__init__.py").write_text("", encoding="utf-8")
    for rel, text in (files or {}).items():
        (repo / rel).parent.mkdir(parents=True, exist_ok=True)
        (repo / rel).write_text(text, encoding="utf-8", newline="\n")
    _git(repo, "init", "-q")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "base")
    return repo, _git(repo, "rev-parse", "HEAD")


def _task(task_id: str, sha: str) -> M.Task:
    return replace(next(t for t in M.CORPUS if t.id == task_id), base=sha)


def _run(task: str, arm: str, rep: int, lines: int, cost: float, *, acceptance: bool = True,
         guards: dict | None = None) -> M.Measured:
    guards = {"validation": True} if guards is None else guards
    ok = acceptance and all(guards.values())
    return M.Measured(task=task, arm=arm, rep=rep,
                      diff={"files": 1, "added": lines, "deleted": 0, "lines": lines, "new_deps": 0},
                      cost_usd=cost, turns=3, seconds=1.0,
                      quality={"acceptance": acceptance, "guards": guards, "ok": ok})


def _pairs(n: int, *, a: dict, b: dict) -> list:
    return [(_run("t", "A", i, **a), _run("t", "B", i, **b)) for i in range(n)]


# ----------------------------------------------------------------- the policy

def test_ladder_prompt_carries_all_seven_rungs_and_six_never_simplify_guards(tmp_path):
    policy = M.load_policy()
    assert len(policy.rungs) == 7
    assert len(policy.guards) == 6
    assert policy.rungs[0] == "does this need to exist?"
    assert policy.rungs[-1] == "only then create new abstraction/dependency."

    prd = M.PRD.read_text(encoding="utf-8")
    assert policy.ladder in prd and policy.never in prd, "the arm must read the PRD, not a copy of it"

    task = M.CORPUS[0]
    baseline, ladder = M.arm_prompt("A", task, policy), M.arm_prompt("B", task, policy)
    assert ladder.startswith(baseline), "the ladder is the only thing that differs between the arms"
    assert policy.ladder not in baseline and policy.never not in baseline
    for rung in policy.rungs:
        assert rung in ladder
    for guard in policy.guards:
        assert guard in ladder

    short = tmp_path / "PRD.md"
    short.write_text("Minimal implementation ladder: a? b? c?\n\nNever simplify away: validation.\n", encoding="utf-8")
    with pytest.raises(M.PolicyError):
        M.load_policy(short)


def test_the_corpus_pins_one_base_and_covers_every_guard():
    assert M.coverage_gaps(M.CORPUS) == []
    assert len({t.id for t in M.CORPUS}) == len(M.CORPUS) >= 3
    for task in M.CORPUS:
        assert task.base == M.BASE
        assert set(task.targets) == set(task.reference) == set(task.unguarded)
        assert set(task.unguarded_fails) <= set(task.probes)
        assert set(task.probes) <= set(M.GUARD_KEYS)


# ---------------------------------------------------------------- diff measure

def test_diff_measure_counts_files_added_deleted_and_new_dependencies_on_a_temp_git_repo(tmp_path):
    repo, base = _repo(tmp_path, {
        "app.py": "a\nb\nc\n",
        "gone.txt": "x\ny\n",
        "pyproject.toml": '[project]\nname = "t"\nversion = "0"\ndependencies = ["requests>=2"]\n',
    })
    (repo / "app.py").write_text("a\nB\nc\nd\n", encoding="utf-8", newline="\n")
    (repo / "gone.txt").unlink()
    (repo / "new.py").write_text("1\n2\n3\n4\n", encoding="utf-8", newline="\n")
    (repo / "pyproject.toml").write_text(
        '[project]\nname = "t"\nversion = "0"\ndependencies = ["requests>=2", "httpx", "rich[extra]>=1"]\n',
        encoding="utf-8", newline="\n")
    (repo / "requirements.txt").write_text("# pins\nnumpy==1\n", encoding="utf-8", newline="\n")

    stat = M.diff_stat(repo, base)

    assert stat.files == 5
    assert stat.added == 2 + 4 + 1 + 2
    assert stat.deleted == 1 + 2 + 1
    assert stat.lines == 13
    assert stat.new_deps == 3, "httpx, rich and numpy are new; requests was already there"
    assert stat.notes == []
    assert sorted(stat.paths) == ["app.py", "gone.txt", "new.py", "pyproject.toml", "requirements.txt"]


@pytest.mark.parametrize("ignored", [True, False])
def test_the_packet_in_build_is_never_diff_whether_or_not_it_is_ignored(tmp_path, ignored):
    # 2026-10-08: the first live A/B arm died in diff_stat - git refuses the exclude
    # pathspec ":(exclude).build" once .gitignore ignores .build/ (since 8617c31).
    repo, base = _repo(tmp_path, {".gitignore": ".build/\n"} if ignored else None)
    (repo / ".build").mkdir()
    (repo / ".build" / "packet.md").write_text("the task\n", encoding="utf-8", newline="\n")
    (repo / "new.py").write_text("1\n", encoding="utf-8", newline="\n")
    stat = M.diff_stat(repo, base)
    assert stat.paths == ["new.py"] and stat.files == 1


def test_an_unreadable_manifest_is_noted_not_fatal(tmp_path):
    repo, base = _repo(tmp_path)
    (repo / "package.json").write_text("{not json", encoding="utf-8")
    stat = M.diff_stat(repo, base)
    assert stat.files == 1 and stat.new_deps == 0
    assert len(stat.notes) == 1 and "package.json" in stat.notes[0]


# --------------------------------------------------------------------- verdict

def test_verdict_rejects_a_smaller_diff_that_fails_the_acceptance_or_guard_check():
    a = dict(lines=40, cost=1.0)
    guard_dropped = M.decide(_pairs(3, a=a, b=dict(lines=10, cost=0.4, guards={"validation": False})))
    assert guard_dropped.outcome == "REJECT"
    assert "t:validation" in guard_dropped.reason
    assert guard_dropped.diff_delta_pct == -75.0, "it was smaller, and that did not save it"

    acceptance_failed = M.decide(_pairs(3, a=a, b=dict(lines=10, cost=0.4, acceptance=False)))
    assert acceptance_failed.outcome == "REJECT"
    assert "t:acceptance" in acceptance_failed.reason

    mixed = [(_run("t", "A", 0, 40, 1.0), _run("t", "B", 0, 10, 0.4, guards={"validation": False})),
             (_run("t", "A", 1, 40, 1.0, acceptance=False), _run("t", "B", 1, 10, 0.4)),
             (_run("t", "A", 2, 40, 1.0), _run("t", "B", 2, 10, 0.4))]
    assert M.decide(mixed).outcome == "REJECT", "a guard regression cannot hide behind a failure elsewhere"


def test_verdict_adopts_only_on_quality_parity_with_diff_and_cost_not_worse():
    base = dict(lines=40, cost=1.0)
    smaller = M.decide(_pairs(3, a=base, b=dict(lines=30, cost=0.8)))
    assert smaller.outcome == "ADOPT"
    assert smaller.diff_delta_pct == -25.0 and smaller.cost_delta_pct == -20.0

    assert M.decide(_pairs(3, a=base, b=dict(lines=40, cost=1.0))).outcome == "ADOPT", "equal is not worse"

    bigger = M.decide(_pairs(3, a=base, b=dict(lines=60, cost=0.5)))
    assert bigger.outcome == "REJECT" and "diff" in bigger.reason

    dearer = M.decide(_pairs(3, a=base, b=dict(lines=20, cost=1.5)))
    assert dearer.outcome == "REJECT" and "cost" in dearer.reason


def test_verdict_is_inconclusive_below_minimum_paired_runs():
    good = dict(lines=10, cost=0.1)
    assert M.decide(_pairs(M.MIN_PAIRS - 1, a=dict(lines=40, cost=1.0), b=good)).outcome == "INCONCLUSIVE"
    assert M.decide([]).outcome == "INCONCLUSIVE"
    assert M.decide(_pairs(2, a=dict(lines=40, cost=1.0), b=good), minimum=2).outcome == "ADOPT"


def test_recommendation_separates_global_per_task_off_and_undetermined():
    adopt, reject = M.Verdict("ADOPT", "", 3), M.Verdict("REJECT", "", 3)
    unknown = M.Verdict("INCONCLUSIVE", "", 1)
    assert M.recommend({"x": adopt, "y": adopt})["scope"] == "global"
    rec = M.recommend({"x": adopt, "y": reject})
    assert rec["scope"] == "per-task" and rec["tasks"] == ["x"]
    assert M.recommend({"x": reject, "y": unknown})["scope"] == "off"
    assert M.recommend({"x": unknown})["scope"] == "undetermined"


# --------------------------------------------------------- fake runner, report

def test_fake_runner_end_to_end_writes_report_with_diff_cost_quality_columns(tmp_path):
    repo, sha = _repo(tmp_path)
    task = _task("duration-parse", sha)
    policy = M.load_policy()
    runner = M.FakeRunner(costs={"A": 0.3, "B": 0.2})

    measured = M.run_experiment([task], runner, repeats=1, policy=policy, repo=repo)

    assert sorted(m.arm for m in measured) == ["A", "B"]
    for m in measured:
        assert m.diff["paths"] == ["friday/durations.py"], "the held-out tests are not part of the diff"
        assert m.diff["files"] == 1 and m.diff["lines"] > 0
        assert m.quality == {"acceptance": True, "guards": {"validation": True}, "ok": True}
    assert {m.arm: m.cost_usd for m in measured} == {"A": 0.3, "B": 0.2}
    assert _git(repo, "worktree", "list").count("\n") == 0, "every arm's worktree is removed"

    report = M.build_report(measured, mode="fake", policy=policy, tasks=[task], repeats=1, base=sha)
    assert report["evidence"] is False and report["banner"] == M.SYNTHETIC
    assert report["overall"]["verdict"]["outcome"] == "INCONCLUSIVE", "a synthetic run never carries a verdict"
    assert report["recommendation"]["scope"] == "undetermined"
    row = report["tasks"][0]
    assert row["A"]["quality_ok"] == row["B"]["quality_ok"] == 1
    assert row["B"]["cost_usd"] < row["A"]["cost_usd"]

    out = tmp_path / "report" / "MINIMAL_BUILD_AB.md"
    md, js = M.write_report(report, out)
    text = md.read_text(encoding="utf-8")
    for column in ("| Files |", "| +Lines |", "| New deps |", "| Cost USD |", "| Quality (acceptance + guards) |"):
        assert column in text
    assert "SYNTHETIC RUN" in text and "Hypothesis, not a result" in text and policy.ladder in text
    assert json.loads(js.read_text(encoding="utf-8"))["evidence"] is False

    js.write_text(json.dumps({"evidence": True}), encoding="utf-8")
    with pytest.raises(M.ReportError):
        M.write_report(report, out)
    assert json.loads(js.read_text(encoding="utf-8")) == {"evidence": True}, "a live measurement survives"
    M.write_report(report, out, force=True)


def test_a_live_report_carries_the_verdict_and_the_recommendation():
    runs = [_run("t", "A", i, 40, 1.0) for i in range(3)] + [_run("t", "B", i, 30, 0.8) for i in range(3)]
    task = replace(M.CORPUS[0], id="t")
    report = M.build_report(runs, mode="live", policy=M.load_policy(), tasks=[task], repeats=3, budget_usd=6.0)
    assert report["evidence"] is True and report["banner"] is None
    assert report["overall"]["verdict"]["outcome"] == "ADOPT"
    assert report["recommendation"]["scope"] == "global"
    assert "**ADOPT**" in M.render_markdown(report)


def test_the_result_event_is_read_from_the_last_line_of_a_stream():
    stream = "\n".join(["not json", json.dumps({"type": "system"}),
                        json.dumps({"type": "result", "total_cost_usd": 0.1, "num_turns": 2}),
                        json.dumps({"type": "result", "total_cost_usd": 0.4, "num_turns": 5, "duration_ms": 9000})])
    event = M.parse_result(stream)
    assert event["total_cost_usd"] == 0.4 and event["num_turns"] == 5
    assert M.parse_result("") == {}


# ------------------------------------------------------------------- preflight

def test_preflight_proves_the_acceptance_fails_on_base_and_the_probe_catches_the_shortcut(tmp_path):
    repo, sha = _repo(tmp_path)
    policy = M.load_policy()
    assert M.preflight([_task("duration-parse", sha)], policy, repo) == []

    vacuous = replace(_task("duration-parse", sha), acceptance="def test_acceptance_nothing():\n    assert True\n")
    assert any("passes on the untouched base" in p for p in M.preflight([vacuous], policy, repo))

    toothless = replace(_task("duration-parse", sha), unguarded=_task("duration-parse", sha).reference)
    assert any("validation probe passes the shortcut" in p for p in M.preflight([toothless], policy, repo))


# ------------------------------------------------------------------ spend safety

def _today() -> str:
    return datetime.now().astimezone().date().isoformat()


def test_live_mode_refuses_without_budget_and_never_bypasses_the_spend_ledger(tmp_path, monkeypatch):
    def forbidden(*a, **k):
        raise AssertionError("a refused live run must not reach the experiment")

    monkeypatch.setattr(M, "run_experiment", forbidden)

    with pytest.raises(SystemExit) as no_budget:
        M.main(["--live"])
    assert no_budget.value.code == 2
    with pytest.raises(SystemExit):
        M.main(["--budget-usd", "5"])

    ledger = tmp_path / "runs"
    too_thin = M.main(["--live", "--budget-usd", "1", "--runs-dir", str(ledger)])
    assert too_thin == 2
    assert K._spend_load(ledger) == [], "an unfundable run reserves nothing"

    ledger.mkdir()
    (ledger / K.DAY_BUDGET_FILE).write_text(
        json.dumps({_today(): {"soft": 5.0, "hard": 6.0}}), encoding="utf-8")
    over_cap = M.main(["--live", "--budget-usd", "30", "--tasks", "duration-parse", "--repeats", "1",
                       "--runs-dir", str(ledger)])
    assert over_cap == 2
    assert K._spend_load(ledger) == [], "a refused reservation leaves no lease"
    assert K.day_cost(datetime.now().astimezone().date(), ledger) == 0.0


def test_live_run_reserves_first_records_each_cost_and_stops_before_a_pair_that_would_pass_the_budget(tmp_path):
    repo, sha = _repo(tmp_path)
    task = _task("duration-parse", sha)
    ledger = tmp_path / "runs"
    seen = {"leases": [], "per_run": []}

    class Stub(M.FakeRunner):
        def run(self, task, arm, prompt, workdir, rep):
            seen["leases"].append(K._spend_load(ledger))
            result = super().run(task, arm, prompt, workdir, rep)
            return M.RunResult(cost_usd=0.9)

    def factory(per_run):
        seen["per_run"].append(per_run)
        return Stub()

    measured = M.run_live([task], repeats=3, budget_usd=2.0, policy=M.load_policy(), runner_factory=factory,
                          repo=repo, runs_dir=ledger)

    assert seen["per_run"] == [round(2.0 / 6, 4)]
    assert all(len(leases) == 1 and leases[0]["usd"] == 2.0 and leases[0]["purpose"] == "minimal_build_ab"
               for leases in seen["leases"]), "the whole budget was reserved before each worker started"
    assert len(measured) == 2, "after one pair $1.80 is spent, and another pair would pass $2.00"
    assert K.day_cost(datetime.now().astimezone().date(), ledger) == 1.8, "each cost is in the day ledger"
    assert K._spend_load(ledger) == [], "the lease is released at the end"
