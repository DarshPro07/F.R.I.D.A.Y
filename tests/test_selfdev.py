"""
Controlled self-development (PRD v3.1 FR-047, FR-048, FR-049, FR-050, FR-051).

Every test runs against a REAL throwaway git repository so the sandbox,
promotion and rollback claims are proven with actual git state, not with
a mock that returns "promoted".

    FR-047  candidate links to measured evidence
    FR-048  main runtime unchanged until the promotion gate passes
    FR-049  promotion impossible while required tests fail
    FR-050  promotion record includes before/after; regression refuses
    FR-051  simulated post-promotion failure restores prior known-good
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from friday import adversarial as A
from friday import selfdev as SD


def git(repo: Path, *args: str) -> str:
    out = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True,
                         timeout=60, check=True)
    return out.stdout.strip()


@pytest.fixture
def repo(tmp_path) -> Path:
    root = tmp_path / "repo"
    (root / "pkg").mkdir(parents=True)
    (root / "friday").mkdir()
    (root / "pkg" / "mod.py").write_text("VALUE = 1\n", encoding="utf-8")
    (root / "friday" / "policy.py").write_text("# kernel\n", encoding="utf-8")
    # The real repo ignores its worktree directory; the sandbox must not
    # show up as an untracked change in the live checkout.
    (root / ".gitignore").write_text(".claude/worktrees/\n", encoding="utf-8")
    git(tmp_path, "init", "-q", "-b", "main", str(root))
    git(root, "config", "user.email", "t@t")
    git(root, "config", "user.name", "t")
    git(root, "add", ".")
    git(root, "commit", "-qm", "base")
    return root


def confirming(system, user, *, worker, objective_id):
    return "VERDICT: CONFIRMED\nFINDING: NONE\nCLAIM_MATCHES_DIFF: yes\nCONFIDENCE: 90"


def disputing(system, user, *, worker, objective_id):
    return "VERDICT: DISPUTED\nFINDING: pkg/mod.py: VALUE=2 breaks the caller\nCLAIM_MATCHES_DIFF: partly\nCONFIDENCE: 80"


def runner_passing(tests, cwd):
    return True, "1 passed"


def runner_failing(tests, cwd):
    return False, "1 failed"


def loop(repo, tmp_path, runner=runner_passing) -> SD.SelfDevelopment:
    return SD.SelfDevelopment(repo, journal=tmp_path / "journal.jsonl", runner=runner)


def bump(root: Path) -> None:
    (root / "pkg" / "mod.py").write_text("VALUE = 2\n", encoding="utf-8")


def drive_to(sd, cand, stage, *, infer=confirming):
    """Walk the loop up to `stage` with a passing everything."""
    order = [("propose", lambda: sd.propose(cand, "bump VALUE", ["pkg/mod.py"],
                                           tests=["tests/test_mod.py"], regression=["tests"])),
             ("sandbox", lambda: sd.sandbox(cand)),
             ("implement", lambda: sd.implement(cand, bump)),
             ("test", lambda: sd.test(cand)),
             ("review", lambda: sd.review(cand, infer=infer)),
             ("regression", lambda: sd.regression(cand)),
             ("benchmark", lambda: sd.benchmark(cand))]
    for name, step in order:
        step()
        if name == stage or cand.state == SD.REJECTED:
            break
    return cand


# -- FR-047 -----------------------------------------------------------------


def test_candidate_needs_measured_evidence(repo, tmp_path):
    sd = loop(repo, tmp_path)
    with pytest.raises(SD.GateRefused):
        sd.observe("c1", "routing feels slow", {"note": "seems slow"})
    cand = sd.observe("c1", "routing p95 too high", {"p95_ms": 900, "samples": 40})
    assert cand.state == SD.OBSERVED and cand.evidence["p95_ms"] == 900


# -- FR-048 -----------------------------------------------------------------


def test_change_happens_in_a_sandbox_and_live_checkout_is_unchanged(repo, tmp_path):
    sd = loop(repo, tmp_path)
    cand = sd.observe("c2", "w", {"n": 1})
    drive_to(sd, cand, "benchmark")
    assert cand.state == SD.BENCHMARKED, cand.rejected_because
    # The sandbox has the change; the live checkout does not.
    assert (Path(cand.sandbox_path) / "pkg" / "mod.py").read_text() == "VALUE = 2\n"
    assert (repo / "pkg" / "mod.py").read_text() == "VALUE = 1\n"
    assert git(repo, "rev-parse", "HEAD") == cand.base_commit
    assert git(repo, "status", "--porcelain") == ""
    assert cand.worktree.startswith("selfdev-") and Path(cand.sandbox_path).is_dir()
    assert Path(cand.sandbox_path).resolve() != repo.resolve()


def test_kernel_surfaces_are_refused_before_any_sandbox_exists(repo, tmp_path):
    sd = loop(repo, tmp_path)
    cand = sd.observe("c3", "w", {"n": 1})
    sd.propose(cand, "loosen policy", ["friday/policy.py"],
               tests=["tests/test_policy.py"], regression=[])
    assert cand.state == SD.REJECTED and "kernel surface" in cand.rejected_because
    assert cand.worktree == "" and not (repo / ".claude" / "worktrees").exists()


def test_a_change_outside_the_proposal_is_rejected(repo, tmp_path):
    sd = loop(repo, tmp_path)
    cand = sd.observe("c4", "w", {"n": 1})
    sd.propose(cand, "bump", ["pkg/mod.py"], tests=["t"], regression=[])
    sd.sandbox(cand)
    sd.implement(cand, lambda root: (root / "pkg" / "other.py").write_text("x\n"))
    assert cand.state == SD.REJECTED and "outside the proposal" in cand.rejected_because


# -- FR-049 -----------------------------------------------------------------


def test_promotion_is_impossible_while_tests_fail(repo, tmp_path):
    sd = loop(repo, tmp_path, runner=runner_failing)
    cand = sd.observe("c5", "w", {"n": 1})
    drive_to(sd, cand, "benchmark")
    assert cand.state == SD.REJECTED and "subsystem tests failed" in cand.rejected_because
    with pytest.raises(SD.GateRefused):
        sd.promote(cand, approved=True)
    assert (repo / "pkg" / "mod.py").read_text() == "VALUE = 1\n"


def test_there_is_no_path_around_the_gates(repo, tmp_path):
    sd = loop(repo, tmp_path)
    cand = sd.observe("c6", "w", {"n": 1})
    drive_to(sd, cand, "implement")
    assert cand.state == SD.IMPLEMENTED
    for skip in (lambda: sd.promote(cand, approved=True),
                 lambda: sd.regression(cand),
                 lambda: sd.benchmark(cand),
                 lambda: sd.review(cand, infer=confirming)):
        with pytest.raises(SD.GateRefused):
            skip()
    assert cand.state == SD.IMPLEMENTED


def test_regression_baseline_failure_rejects_after_review(repo, tmp_path):
    calls = []

    def runner(tests, cwd):
        calls.append(list(tests))
        return (False, "3 failed") if tests == ["tests"] else (True, "ok")

    sd = loop(repo, tmp_path, runner=runner)
    cand = sd.observe("c7", "w", {"n": 1})
    drive_to(sd, cand, "benchmark")
    assert cand.state == SD.REJECTED and "regression baseline failed" in cand.rejected_because
    assert calls == [["tests/test_mod.py"], ["tests"]]


def test_a_disputed_independent_review_rejects(repo, tmp_path):
    sd = loop(repo, tmp_path)
    cand = sd.observe("c8", "w", {"n": 1})
    drive_to(sd, cand, "benchmark", infer=disputing)
    assert cand.state == SD.REJECTED and "disputed" in cand.rejected_because
    assert cand.review["verdict"] == A.DISPUTED and cand.review["independent"]


# -- FR-050 -----------------------------------------------------------------


def test_benchmark_regression_refuses_and_record_has_before_after(repo, tmp_path):
    sd = loop(repo, tmp_path)
    cand = sd.observe("c9", "w", {"n": 1})
    drive_to(sd, cand, "regression")
    sd.benchmark(cand, before={"p95_ms": 100}, measure=lambda root: {"p95_ms": 130},
                 lower_is_better=("p95_ms",))
    assert cand.state == SD.REJECTED and "benchmark regressed" in cand.rejected_because
    assert cand.benchmark["regressions"] == ["p95_ms: 100 -> 130"]

    cand2 = sd.observe("c9b", "w", {"n": 1})
    cand2.id = "c9b"
    drive_to(sd, cand2, "regression")
    sd.benchmark(cand2, before={"p95_ms": 100}, measure=lambda root: {"p95_ms": 80},
                 lower_is_better=("p95_ms",))
    assert cand2.state == SD.BENCHMARKED
    assert cand2.benchmark == {"before": {"p95_ms": 100}, "after": {"p95_ms": 80},
                               "regressions": []}


# -- promote + FR-051 rollback -----------------------------------------------


def test_promote_merges_and_rollback_restores_prior_known_good(repo, tmp_path):
    sd = loop(repo, tmp_path)
    cand = sd.observe("c10", "w", {"n": 1})
    drive_to(sd, cand, "benchmark")
    base = git(repo, "rev-parse", "HEAD")
    sd.promote(cand, approved=False)
    assert cand.state == SD.REJECTED and "not approved" in cand.rejected_because
    assert git(repo, "rev-parse", "HEAD") == base

    cand = sd.observe("c11", "w", {"n": 1})
    drive_to(sd, cand, "benchmark")
    sd.promote(cand, approved=True)
    assert cand.state == SD.PROMOTED, cand.rejected_because
    assert (repo / "pkg" / "mod.py").read_text() == "VALUE = 2\n"
    merge = git(repo, "rev-parse", "HEAD")
    assert merge != base and cand.promotion["merge_commit"] == merge
    assert cand.promotion["base_commit"] == base

    # Simulated post-promotion failure -> automatic, deterministic rollback.
    sd.monitor(cand, health=lambda: False)
    assert cand.state == SD.ROLLED_BACK
    assert (repo / "pkg" / "mod.py").read_text() == "VALUE = 1\n"
    assert git(repo, "diff", base, "--", "pkg/mod.py") == ""
    # History kept: the merge and its revert are both there.
    log = git(repo, "log", "--oneline")
    assert "Revert" in log and "selfdev c11" in log
    assert git(repo, "status", "--porcelain") == ""


def test_healthy_monitor_keeps_the_promotion_and_manual_rollback_still_works(repo, tmp_path):
    sd = loop(repo, tmp_path)
    cand = sd.observe("c12", "w", {"n": 1})
    drive_to(sd, cand, "benchmark")
    sd.promote(cand, approved=True)
    sd.monitor(cand, health=lambda: True)
    assert cand.state == SD.MONITORED
    assert (repo / "pkg" / "mod.py").read_text() == "VALUE = 2\n"
    sd.rollback(cand, reason="operator decided against it")
    assert cand.state == SD.ROLLED_BACK
    assert (repo / "pkg" / "mod.py").read_text() == "VALUE = 1\n"


def test_every_transition_is_journaled_and_audited(repo, tmp_path):
    from friday import trust as T
    sd = loop(repo, tmp_path)
    cand = sd.observe("c13", "w", {"n": 1})
    drive_to(sd, cand, "benchmark")
    sd.promote(cand, approved=True)
    sd.monitor(cand, health=lambda: False)
    journal = (tmp_path / "journal.jsonl").read_text(encoding="utf-8")
    for step in (SD.OBSERVED, SD.PROPOSED, SD.SANDBOXED, SD.IMPLEMENTED, SD.TESTED,
                 SD.REVIEWED, SD.REGRESSION_PASSED, SD.BENCHMARKED, SD.PROMOTED, SD.ROLLED_BACK):
        assert step in journal, step
    rows = T.audit().query(objective_id="c13", min_tier=T.R3)
    actions = {r["action"] for r in rows}
    assert {"selfdev.sandboxed", "selfdev.promoted", "selfdev.rolled_back"} <= actions
    assert T.audit().verify_chain()["ok"]
    assert [h["to"] for h in cand.history] == [
        SD.PROPOSED, SD.SANDBOXED, SD.IMPLEMENTED, SD.TESTED, SD.REVIEWED,
        SD.REGRESSION_PASSED, SD.BENCHMARKED, SD.PROMOTED, SD.ROLLED_BACK]


def test_rejected_candidates_keep_their_sandbox_as_evidence(repo, tmp_path):
    sd = loop(repo, tmp_path, runner=runner_failing)
    cand = sd.observe("c14", "w", {"n": 1})
    drive_to(sd, cand, "benchmark")
    assert cand.state == SD.REJECTED
    report = sd.cleanup(cand)
    assert report["kept"] and Path(cand.sandbox_path).is_dir()
    ok = loop(repo, tmp_path)
    cand2 = ok.observe("c15", "w", {"n": 1})
    drive_to(ok, cand2, "benchmark")
    report = ok.cleanup(cand2)
    assert report["removed"] and not Path(cand2.sandbox_path).exists()


# -- the tool face, end to end on a real repo --------------------------------


def test_toolset_runs_gates_promotes_on_approval_and_rolls_back(repo, tmp_path, monkeypatch):
    from friday import contracts as c
    from friday.toolsets import selfdev as ST

    monkeypatch.setattr(ST, "_repo", lambda: repo)
    monkeypatch.setattr(ST, "_loop", lambda: SD.SelfDevelopment(
        repo, journal=tmp_path / "j.jsonl", runner=runner_passing))
    ST._CANDIDATES.clear()
    patch = ("--- a/pkg/mod.py\n+++ b/pkg/mod.py\n@@ -1 +1 @@\n-VALUE = 1\n+VALUE = 2\n")
    run = c.Run.create("selfdev", capability="selfdev_run")
    out = ST.selfdev_run(run, "t1", "slow", {"p95_ms": 500}, "bump VALUE", ["pkg/mod.py"],
                         patch, ["tests/test_mod.py"], ["tests"], infer=confirming)
    assert out.status == c.SUCCEEDED, out.error
    assert out.output["candidate"]["state"] == SD.BENCHMARKED
    assert "live checkout untouched" in out.verification.evidence
    assert (repo / "pkg" / "mod.py").read_text() == "VALUE = 1\n"

    refused = ST.selfdev_promote(run, "t1", approved=False)
    assert refused.status == c.FAILED and "not approved" in refused.error
    assert (repo / "pkg" / "mod.py").read_text() == "VALUE = 1\n"

    # A fresh candidate (the refused one is REJECTED, and a REJECTED
    # candidate has no path back).
    out = ST.selfdev_run(run, "t2", "slow", {"p95_ms": 500}, "bump VALUE", ["pkg/mod.py"],
                         patch, ["tests/test_mod.py"], [], infer=confirming)
    assert out.status == c.SUCCEEDED, out.error
    landed = ST.selfdev_promote(run, "t2", approved=True, health=lambda: True)
    assert landed.status == c.SUCCEEDED, landed.error
    assert (repo / "pkg" / "mod.py").read_text() == "VALUE = 2\n"
    undone = ST.selfdev_rollback(run, "t2", "changed my mind")
    assert undone.status == c.SUCCEEDED, undone.error
    assert (repo / "pkg" / "mod.py").read_text() == "VALUE = 1\n"
    status = ST.selfdev_status(run)
    assert status.output["t2"]["state"] == SD.ROLLED_BACK
    assert status.output["t1"]["state"] == SD.REJECTED


def test_toolset_rejects_a_patch_that_does_not_apply(repo, tmp_path, monkeypatch):
    from friday import contracts as c
    from friday.toolsets import selfdev as ST

    monkeypatch.setattr(ST, "_loop", lambda: SD.SelfDevelopment(
        repo, journal=tmp_path / "j.jsonl", runner=runner_passing))
    ST._CANDIDATES.clear()
    run = c.Run.create("selfdev", capability="selfdev_run")
    bad = "--- a/pkg/mod.py\n+++ b/pkg/mod.py\n@@ -1 +1 @@\n-VALUE = 7\n+VALUE = 8\n"
    out = ST.selfdev_run(run, "t3", "w", {"n": 1}, "p", ["pkg/mod.py"], bad, ["t"], [])
    assert out.status == c.FAILED and "patch did not apply" in out.error
    assert (repo / "pkg" / "mod.py").read_text() == "VALUE = 1\n"


# -- FR-050: the benchmark gate can fail ---------------------------------------


def test_toolset_benchmarks_a_performance_claim_and_rejects_a_regression(repo, tmp_path, monkeypatch):
    """A change to a perf-sensitive file is measured in its sandbox against
    the live tree; a regression past the tolerance is rejected, with the
    before/after numbers in the record. The measurement is injected here
    (the real one runs the perf probe in a subprocess and takes ~1 min a
    side); what is under test is that the loop calls it and honours it."""
    from friday import contracts as c
    from friday import selfdev_benchmark as B
    from friday.toolsets import selfdev as ST

    monkeypatch.setattr(ST, "_repo", lambda: repo)
    monkeypatch.setattr(ST, "_loop", lambda: SD.SelfDevelopment(
        repo, journal=tmp_path / "j.jsonl", runner=runner_passing))
    ST._CANDIDATES.clear()
    measured_roots = []

    def slower(root):
        measured_roots.append(Path(root))
        return {"memory_p95_ms": 140.0, "router_top1": 0.96}

    monkeypatch.setattr(B, "baseline", lambda root, refresh=False: {"memory_p95_ms": 100.0, "router_top1": 0.96})
    monkeypatch.setattr(B, "measure", slower)
    patch = ("--- a/pkg/mod.py\n+++ b/pkg/mod.py\n@@ -1 +1 @@\n-VALUE = 1\n+VALUE = 2\n")
    run = c.Run.create("selfdev", capability="selfdev_run")
    # The candidate names a perf-sensitive file, so it carries a claim.
    out = ST.selfdev_run(run, "b1", "slow", {"p95_ms": 500}, "bump VALUE",
                         ["friday/planner.py", "pkg/mod.py"],
                         patch, ["tests/test_mod.py"], [], infer=confirming)
    assert out.status == c.FAILED, out.output
    assert "benchmark regressed" in out.error
    cand = ST._CANDIDATES["b1"]
    assert cand.state == SD.REJECTED
    assert cand.benchmark["regressions"] == ["memory_p95_ms: 100.0 -> 140.0"]
    assert measured_roots and measured_roots[0] != repo.resolve(), \
        "the sandbox must be measured, not the live tree"
    assert (repo / "pkg" / "mod.py").read_text() == "VALUE = 1\n"


def test_toolset_records_no_claim_for_a_docs_only_change(repo, tmp_path, monkeypatch):
    from friday import contracts as c
    from friday import selfdev_benchmark as B
    from friday.toolsets import selfdev as ST

    monkeypatch.setattr(ST, "_repo", lambda: repo)
    monkeypatch.setattr(ST, "_loop", lambda: SD.SelfDevelopment(
        repo, journal=tmp_path / "j.jsonl", runner=runner_passing))
    ST._CANDIDATES.clear()
    monkeypatch.setattr(B, "measure", lambda root, **kw: (_ for _ in ()).throw(AssertionError("must not measure")))
    patch = ("--- a/pkg/mod.py\n+++ b/pkg/mod.py\n@@ -1 +1 @@\n-VALUE = 1\n+VALUE = 2\n")
    run = c.Run.create("selfdev", capability="selfdev_run")
    out = ST.selfdev_run(run, "d1", "typo", {"typos": 3}, "fix docs", ["pkg/mod.py"],
                         patch, ["tests/test_mod.py"], [], infer=confirming)
    assert out.status == c.SUCCEEDED, out.error
    assert out.output["candidate"]["benchmark"] == {"skipped": "no performance claim"}


def test_benchmark_claims_and_failed_measurement_shape():
    from friday import selfdev_benchmark as B
    assert B.claims(["friday/planner.py"]) and B.claims(["E:/x/friday/store.py"])
    assert not B.claims(["docs/a.md", "tests/test_b.py", "pkg/mod.py"])
    failed = B.measure("C:/no-such-checkout-for-friday")
    assert failed["failed"].startswith("no such checkout")
    assert failed["memory_p95_ms"] == float("inf") and failed["router_top1"] == 0.0


# -- A-043: the loop cannot rewrite its own judge -------------------------------


@pytest.mark.parametrize("path", [
    "friday/policy.py", "friday/trust.py", "friday/confirmation.py",
    "friday/promotion.py", "friday/evaluation.py", "friday/honesty.py",
    "friday/adversarial.py", "friday/golden.py", "friday/selfdev.py",
    "friday/selfdev_benchmark.py", "friday/toolsets/selfdev.py",
    "docs/golden/objectives.jsonl", "docs/golden/failures.jsonl",
    ".github/workflows/verify.yml", "tests/test_trust.py",
    "tests/test_selfdev.py", "tests/conftest.py",
    r"E:\some\worktree\friday\trust.py",            # absolute, Windows separators
    "./friday/policy.py",
])
def test_every_judge_surface_is_kernel(path):
    from friday import self_upgrade as SU
    assert SU.is_kernel_path(path), path


@pytest.mark.parametrize("path", [
    "friday/planner.py", "friday/toolsets/files.py", "tests/test_planner.py",
    "docs/architecture/MODULE_MAP.md", "friday/policy_helpers_not_kernel.py",
    "scripts/other.py",
])
def test_ordinary_surfaces_are_not_kernel(path):
    from friday import self_upgrade as SU
    assert SU.is_kernel_path(path) is None, path


def test_a_proposal_naming_the_judge_is_refused_before_a_sandbox_exists(repo, tmp_path):
    sd = SD.SelfDevelopment(repo, journal=tmp_path / "j.jsonl", runner=runner_passing)
    for judge in ("friday/promotion.py", "tests/test_trust.py", "docs/golden/objectives.jsonl",
                  ".github/workflows/verify.yml"):
        cand = sd.observe(f"k-{judge.replace('/', '-')}", "make the gate lenient", {"n": 1})
        sd.propose(cand, "loosen it", ["pkg/mod.py", judge], tests=["tests/test_mod.py"], regression=[])
        assert cand.state == SD.REJECTED, judge
        assert "kernel surface" in cand.rejected_because, cand.rejected_because
        assert cand.sandbox_path in (None, ""), "no sandbox may exist for a refused proposal"
