"""
EVO-001: the Evolution Controller turns a concrete trigger into exactly one
deduplicated, budgeted proposal that survives a restart.

Real Store on a file (the restart test reopens it), real Governor with a dialled
sampler. Deciding and deduplicating are plain code, so no model is involved; one
test holds that by reading the module's imports.
"""
from __future__ import annotations

import ast
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import pytest

from friday import evolution_allowance as W
from friday import evolution_archive as A
from friday import evolution_controller as C
from friday import evolution_governor as E
from friday.evolution_lab import Lab, RunResult
from friday import governor as G
from friday.store import Store
from tests.test_governor import make

NOW = datetime(2026, 10, 10, 14, 0, tzinfo=timezone.utc)
FIELDS = ("benefit", "risk", "cost", "components", "acceptance_tests", "budget", "termination")
LAB_TEST = (
    "from calc import add\n\n"
    "def test_add():\n"
    "    assert add(1, 2) == 3\n"
)
LAB_EVALUATOR = "def verify():\n    return True\n"


def _gov():
    gov, _ = make()
    gov._context = lambda: G.Context(user_idle_s=3600.0, on_battery=False)
    return gov


def _missing(subject="pdf_extract", **kw):
    return C.Trigger(C.MISSING_CAPABILITY, subject, evidence={"request": "read this pdf"}, **kw)


def _git(repo: Path, *args: str) -> str:
    out = subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@example.invalid", *args],
        cwd=str(repo), capture_output=True, text=True, timeout=60, check=True)
    return out.stdout.strip()


class _LabRunner:
    def __init__(self):
        self.calls: list[list[str]] = []

    def __call__(self, command, cwd, env):
        self.calls.append(list(command))
        return RunResult(0, {"passed": 1})


def _lab_world(tmp_path):
    repo = tmp_path / "repo"
    (repo / "tests").mkdir(parents=True)
    (repo / "friday").mkdir()
    (repo / "calc.py").write_text("def add(a, b):\n    return a + b\n")
    (repo / "tests" / "test_calc.py").write_text(LAB_TEST)
    (repo / "friday" / "evaluation.py").write_text(LAB_EVALUATOR)
    _git(repo, "init", "-q")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "base")
    base = _git(repo, "rev-parse", "HEAD")
    lab = Lab(repo, tmp_path / "lab-archive")
    runner = _LabRunner()
    return lab, base, runner


def _candidate(tmp_path, name: str, base: str, score: float, *,
               hypothesis: str | None = None, mutate=None) -> C.EvolutionCandidate:
    sandbox = tmp_path / f"sandbox-{name}"
    shutil.copytree(tmp_path / "repo", sandbox, ignore=shutil.ignore_patterns(".git"))
    if mutate:
        mutate(sandbox)
    return C.EvolutionCandidate(
        candidate_version=name,
        hypothesis=hypothesis or f"{name} hypothesis",
        scope=(name,),
        files=("calc.py",),
        measurements={"score": score},
        sandbox=str(sandbox),
        base_commit=base,
        tests=("tests/test_calc.py",),
    )


def test_a_missing_capability_creates_exactly_one_complete_proposal(tmp_path):
    store = Store(tmp_path / "e.sqlite3")
    decision = C.consider(store, _missing())
    assert decision.proposed and decision.created
    proposal = decision.proposal
    for name in FIELDS:
        assert proposal[name], f"proposal has no {name}"
    assert proposal["trigger_kind"] == C.MISSING_CAPABILITY
    assert proposal["budget"]["max_usd"] > 0 and proposal["budget"]["max_wall_s"] > 0
    assert proposal["termination"]["max_wall_s"] == proposal["budget"]["max_wall_s"]
    assert len(C.proposals(store)) == 1


def _conclude(archive, proposal, promoted, **over):
    kw = dict(parent_version="v1", candidate_version="v2", baseline={"failures": 3.0},
              candidate={"failures": 0.0 if promoted else 3.0},
              evaluation={"verifier": "independent", "passed": promoted}, promoted=promoted)
    kw.update(over)
    return C.conclude(archive, proposal, **kw)


def test_a_concluded_experiment_is_archived_as_promoted_or_rejected(tmp_path):
    store, archive = Store(tmp_path / "e.sqlite3"), A.EvolutionArchive(tmp_path / "arc")
    won = C.consider(store, _missing("pdf_extract")).proposal
    lost = C.consider(store, _missing("docx_extract")).proposal

    kept, failed = _conclude(archive, won, True), _conclude(archive, lost, False)

    assert (kept["decision"], failed["decision"]) == (A.PROMOTE, A.REJECT)
    assert kept["objective"] == won["proposal_id"] and kept["rollback_version"] == "v1"
    assert kept["scope"] == sorted(won["components"])
    assert archive.get(failed["id"])["candidate_version"] == "v2"


def test_a_candidate_that_lost_is_refused_before_the_same_experiment_runs_again(tmp_path):
    store, archive = Store(tmp_path / "e.sqlite3"), A.EvolutionArchive(tmp_path / "arc")
    allowance = W.EvolutionAllowance(tmp_path / "allowance", W.AllowanceLimits(usd=2.0, ram_gb_s=100.0, wall_s=60.0))
    proposal = C.consider(store, _missing()).proposal
    step = dict(objective_id="exp-2", trigger="missing_capability", parent_version="v1",
                candidate_version="v3", hypothesis=C.hypothesis_of(proposal),
                scope=proposal["components"], now=NOW)
    assert allowance.authorize_step(archive, **step).allowed       # untried: it may run

    failed = _conclude(archive, proposal, False)

    got = allowance.authorize_step(archive, **step)                # tried and lost: it may not
    assert not got.allowed and got.archived == failed["id"]


def test_repeats_of_the_same_trigger_deduplicate(tmp_path):
    store = Store(tmp_path / "e.sqlite3")
    first = C.consider(store, _missing("pdf_extract"))
    again = C.consider(store, _missing("  PDF_Extract "))
    third = C.consider(store, C.Trigger(C.MISSING_CAPABILITY, "pdf_extract",
                                        evidence={"request": "another file"}))
    assert first.created and not again.created and not third.created
    assert {first.proposal["proposal_id"], again.proposal["proposal_id"],
            third.proposal["proposal_id"]} == {first.proposal["proposal_id"]}
    rows = C.proposals(store)
    assert len(rows) == 1 and rows[0]["occurrences"] == 3


def test_a_different_subject_or_kind_is_a_different_proposal(tmp_path):
    store = Store(tmp_path / "e.sqlite3")
    C.consider(store, _missing("pdf_extract"))
    C.consider(store, _missing("ocr_scan"))
    C.consider(store, C.Trigger(C.SLOW_PATH, "pdf_extract", observed=C.SLOW_MS * 2, count=5))
    assert len(C.proposals(store)) == 3


def test_a_proposal_survives_a_restart_and_is_not_proposed_again(tmp_path):
    path = tmp_path / "e.sqlite3"
    first = C.consider(Store(path), _missing())
    reopened = Store(path)
    rows = C.proposals(reopened)
    assert [r["proposal_id"] for r in rows] == [first.proposal["proposal_id"]]
    assert rows[0]["acceptance_tests"] == first.proposal["acceptance_tests"]
    again = C.consider(reopened, _missing())
    assert not again.created and len(C.proposals(reopened)) == 1


def test_no_trigger_means_no_proposal(tmp_path):
    store = Store(tmp_path / "e.sqlite3")
    decision = C.consider(store, None)
    assert not decision.proposed and "trigger" in decision.reason
    assert C.proposals(store) == []


def test_the_controller_runs_no_model():
    tree = ast.parse(Path(C.__file__).read_text(encoding="utf-8"))
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            imported.add(node.module or "")
            imported.update(f"{node.module}.{a.name}" for a in node.names)
    text = " ".join(imported).lower()
    for model_word in ("model_gateway", "anthropic", "openai", "gemini", "llm", "requests", "httpx"):
        assert model_word not in text


@pytest.mark.parametrize("count,proposed", [(1, False), (2, False), (3, True), (10, True)])
def test_a_failure_must_repeat_before_it_is_worth_a_proposal(tmp_path, count, proposed):
    store = Store(tmp_path / "e.sqlite3")
    decision = C.consider(store, C.Trigger(C.REPEATED_FAILURE, "fs.read_text", count=count))
    assert decision.proposed is proposed
    assert len(C.proposals(store)) == (1 if proposed else 0)
    if proposed:
        assert decision.proposal["klass"] == E.RELIABILITY_REPAIR
    else:
        assert "repeat" in decision.reason


def test_slow_and_costly_paths_need_to_cross_their_threshold(tmp_path):
    store = Store(tmp_path / "e.sqlite3")
    assert not C.consider(store, C.Trigger(C.SLOW_PATH, "route.plan", observed=100, count=9)).proposed
    assert not C.consider(store, C.Trigger(C.COSTLY_PATH, "route.plan", observed=0.01, count=9)).proposed
    slow = C.consider(store, C.Trigger(C.SLOW_PATH, "route.plan",
                                       observed=C.SLOW_MS * 2, count=C.MIN_REPEATS))
    costly = C.consider(store, C.Trigger(C.COSTLY_PATH, "route.plan",
                                         observed=C.COSTLY_USD * 2, count=C.MIN_REPEATS))
    assert slow.proposed and costly.proposed
    assert slow.proposal["klass"] == costly.proposal["klass"] == E.APPROVED_IMPROVEMENT


def test_a_malformed_trigger_is_refused_not_guessed(tmp_path):
    store = Store(tmp_path / "e.sqlite3")
    with pytest.raises(ValueError):
        C.consider(store, C.Trigger("vibes", "x"))
    with pytest.raises(ValueError):
        C.consider(store, C.Trigger(C.MISSING_CAPABILITY, "   "))
    with pytest.raises(ValueError):
        C.consider(store, C.Trigger(C.MISSING_CAPABILITY, "x", evidence={"blob": "a" * 100_000}))
    assert C.proposals(store) == []


def test_a_trust_root_is_proposal_only_and_is_never_admitted(tmp_path):
    store, gov = Store(tmp_path / "e.sqlite3"), _gov()
    decision = C.consider(store, C.Trigger(C.REPEATED_FAILURE, "verifier",
                                           count=5, component="friday/promotion.py"))
    assert decision.proposal["zone"] == "Z4" and decision.proposal["proposal_only"] is True
    admission = C.admit(store, gov, decision.proposal["proposal_id"], "run-z4",
                        mode=E.TRIGGER, now=NOW)
    assert not admission.admitted and "proposal" in admission.reason.lower()


def test_an_ordinary_proposal_is_admitted_through_the_evolution_governor(tmp_path):
    store, gov = Store(tmp_path / "e.sqlite3"), _gov()
    decision = C.consider(store, _missing())
    assert decision.proposal["zone"] == "Z1" and not decision.proposal["proposal_only"]
    pid = decision.proposal["proposal_id"]
    admission = C.admit(store, gov, pid, "run-1", mode=E.TRIGGER, now=NOW)
    assert admission.admitted, admission.reason
    assert [r["status"] for r in C.proposals(store)] == [C.ADMITTED]
    assert [r["proposal_id"] for r in C.proposals(store, status=C.ADMITTED)] == [pid]


def test_a_higher_class_waiting_holds_a_lower_one_back(tmp_path):
    store, gov = Store(tmp_path / "e.sqlite3"), _gov()
    repair = C.consider(store, C.Trigger(C.REPEATED_FAILURE, "fs.read_text", count=4)).proposal
    improve = C.consider(store, _missing()).proposal
    assert improve["klass"] == E.APPROVED_IMPROVEMENT
    held = C.admit(store, gov, improve["proposal_id"], "run-i", mode=E.TRIGGER, now=NOW)
    assert not held.admitted and held.blocked_by == E.RELIABILITY_REPAIR
    first = C.admit(store, gov, repair["proposal_id"], "run-r", mode=E.TRIGGER, now=NOW)
    assert first.admitted, first.reason


def test_admitting_an_unknown_or_already_admitted_proposal_is_refused(tmp_path):
    store, gov = Store(tmp_path / "e.sqlite3"), _gov()
    assert not C.admit(store, gov, "evo-nope", "run-x", mode=E.TRIGGER, now=NOW).admitted
    pid = C.consider(store, _missing()).proposal["proposal_id"]
    assert C.admit(store, gov, pid, "run-1", mode=E.TRIGGER, now=NOW).admitted
    again = C.admit(store, gov, pid, "run-2", mode=E.TRIGGER, now=NOW)
    assert not again.admitted and "admitted" in again.reason


def test_a_later_trust_root_trigger_escalates_the_stored_zone_and_is_never_admitted(tmp_path):
    store, gov = Store(tmp_path / "e.sqlite3"), _gov()
    low = C.consider(store, C.Trigger(C.REPEATED_FAILURE, "verifier", count=3,
                                      component="skills/x/"))
    assert low.proposal["zone"] == "Z1"
    high = C.consider(store, C.Trigger(C.REPEATED_FAILURE, "verifier", count=3,
                                       component="friday/promotion.py"))
    assert not high.created
    assert high.proposal["proposal_id"] == low.proposal["proposal_id"]
    assert high.proposal["zone"] == "Z4" and high.proposal["proposal_only"] is True
    assert high.proposal["risk"]["zone"] == "Z4"
    assert set(high.proposal["components"]) == {"skills/x/", "friday/promotion.py"}
    # a later, lower-zone repeat never lowers it again
    low_again = C.consider(store, C.Trigger(C.REPEATED_FAILURE, "verifier", count=3,
                                            component="skills/x/"))
    assert low_again.proposal["zone"] == "Z4"
    assert [r["zone"] for r in C.proposals(store)] == ["Z4"]
    admission = C.admit(store, gov, low.proposal["proposal_id"], "run-z4",
                        mode=E.TRIGGER, now=NOW)
    assert not admission.admitted and "proposal" in admission.reason.lower()


def test_a_path_like_subject_counts_toward_the_zone_even_with_a_component(tmp_path):
    store = Store(tmp_path / "e.sqlite3")
    got = C.consider(store, C.Trigger(C.REPEATED_FAILURE, "friday/policy.py",
                                      count=3, component="skills/x/"))
    assert got.proposal["zone"] == "Z4"


@pytest.mark.parametrize("trigger", [
    C.Trigger(C.MISSING_CAPABILITY, "x", evidence={"x": {1}}),
    C.Trigger(C.MISSING_CAPABILITY, "x", evidence=["not", "a", "dict"]),
    C.Trigger(C.MISSING_CAPABILITY, "x", evidence={"n": float("nan")}),
    C.Trigger(C.SLOW_PATH, "x", count=3, observed="9999"),
    C.Trigger(C.SLOW_PATH, "x", count=3, observed=float("inf")),
    C.Trigger(C.SLOW_PATH, "x", count=3, observed=True),
    C.Trigger(C.MISSING_CAPABILITY, "x", component="c" * 100_000),
    C.Trigger(C.MISSING_CAPABILITY, "x", component=5),
    C.Trigger(C.MISSING_CAPABILITY, 5),
    C.Trigger(C.MISSING_CAPABILITY, "x", count=2.5),
])
def test_every_malformed_trigger_raises_value_error(tmp_path, trigger):
    store = Store(tmp_path / "e.sqlite3")
    with pytest.raises(ValueError):
        C.consider(store, trigger)
    assert C.proposals(store) == []


def test_the_proposal_id_is_the_whole_dedupe_key(tmp_path):
    store = Store(tmp_path / "e.sqlite3")
    got = C.consider(store, _missing()).proposal
    assert got["proposal_id"] == "evo-" + got["dedupe_key"] and len(got["dedupe_key"]) == 32


def test_an_admitted_proposal_keeps_its_run_and_lease_across_a_restart(tmp_path):
    path = tmp_path / "e.sqlite3"
    store, gov = Store(path), _gov()
    pid = C.consider(store, _missing()).proposal["proposal_id"]
    admission = C.admit(store, gov, pid, "run-keep", mode=E.TRIGGER, now=NOW)
    assert admission.admitted and admission.lease
    store.close() if hasattr(store, "close") else None
    row = C.proposals(Store(path), status=C.ADMITTED)[0]
    assert row["run_id"] == "run-keep" and row["lease"] == admission.lease
    # a refused admission leaves no link behind
    other = C.consider(Store(path), _missing("other_cap")).proposal["proposal_id"]
    refused = C.admit(Store(path), gov, other, "run-no", mode=E.TRIGGER, now=NOW)
    assert not refused.admitted
    assert C.proposals(Store(path), status=C.PENDING)[0]["run_id"] == ""


# -- EVOLAB-004: one controlled evolution run ---------------------------------

def test_evolab_run_rejects_canaries_promotes_winner_and_rolls_back_on_health(tmp_path):
    archive = A.EvolutionArchive(tmp_path / "arc")
    lab, base, lab_runner = _lab_world(tmp_path)
    promoted: list[str] = []
    rolled_back: list[str] = []
    benchmarked: list[str] = []

    def benchmark(candidate):
        benchmarked.append(candidate.candidate_version)
        return candidate.measurements

    def promote(candidate):
        promoted.append(candidate.candidate_version)
        return C.PromotionRecord(version=candidate.candidate_version, rollback_handle="v1")

    def rollback(record):
        rolled_back.append(record.version)

    candidates = [
        C.EvolutionCandidate(
            candidate_version="unsafe-kernel",
            hypothesis="speed parser",
            scope=("friday/policy.py",),
            files=("friday/policy.py",),
            measurements={"score": 0.99},
        ),
        C.EvolutionCandidate(
            candidate_version="unsafe-dependency",
            hypothesis="speed parser",
            scope=("parser",),
            files=("pyproject.toml",),
            diff="+requests = \"^2.0\"\n",
            measurements={"score": 0.99},
        ),
        C.EvolutionCandidate(
            candidate_version="unsafe-secret",
            hypothesis="speed parser",
            scope=("parser",),
            files=("friday/parser.py",),
            diff="+OPENAI_API_KEY = \"sk-1234567890abcdef\"\n",
            measurements={"score": 0.99},
        ),
        C.EvolutionCandidate(
            candidate_version="unsafe-receipt",
            hypothesis="speed parser",
            scope=("parser",),
            files=("friday/parser.py",),
            sandbox=str(tmp_path / "missing-sandbox"),
            base_commit=base,
            tests=("tests/test_calc.py",),
            measurements={"score": 0.99},
        ),
        _candidate(tmp_path, "inferior", base, 1.01, hypothesis="inferior parser"),
        _candidate(tmp_path, "no-gain", base, 1.0, hypothesis="steady parser"),
        _candidate(tmp_path, "runner-up", base, 1.20, hypothesis="runner-up parser"),
        _candidate(tmp_path, "better", base, 1.25, hypothesis="best parser"),
    ]

    first = C.run_controlled_evolution(
        objective_id="EVOLAB-004",
        parent_version="v1",
        candidates=candidates,
        baseline={"score": 1.0},
        tolerance=0.05,
        archive=archive,
        lab=lab,
        lab_runner=lab_runner,
        benchmark=benchmark,
        promote=promote,
        rollback=rollback,
        health_check=lambda record: False,
        allowance=C.InMemoryEvolutionAllowance(),
    )

    by_version = {r.candidate_version: r.decision for r in first.results}
    assert by_version == {
        "unsafe-kernel": C.UNSAFE,
        "unsafe-dependency": C.UNSAFE,
        "unsafe-secret": C.UNSAFE,
        "unsafe-receipt": C.UNSAFE,
        "inferior": C.DOES_NOT_BEAT_BASELINE,
        "no-gain": C.DOES_NOT_BEAT_BASELINE,
        "runner-up": C.NOT_MEASURED_WINNER,
        "better": C.ROLLED_BACK,
    }
    assert promoted == ["better"]
    assert rolled_back == ["better"]
    assert benchmarked == ["inferior", "no-gain", "runner-up", "better"]
    assert all(row["candidate_version"] in by_version for row in archive.query(""))
    assert len(archive.query("")) == len(by_version)
    assert all(r.rollback_version == "v1" for r in first.results)
    assert not first.promoted
    assert first.allowance_spent and all(row["objective_id"] == "EVOLAB-004" for row in first.allowance_spent)

    second = C.run_controlled_evolution(
        objective_id="EVOLAB-004",
        parent_version="v1",
        candidates=candidates,
        baseline={"score": 1.0},
        tolerance=0.05,
        archive=archive,
        lab=lab,
        lab_runner=lab_runner,
        benchmark=benchmark,
        promote=promote,
        rollback=rollback,
        health_check=lambda record: True,
        allowance=C.InMemoryEvolutionAllowance(),
    )
    assert [r.decision for r in second.results] == [C.ALREADY_REJECTED] * len(candidates)
    assert promoted == ["better"]


def test_evolab_run_rejects_weakened_tests_and_evaluator_tampering_before_benchmark(tmp_path):
    archive = A.EvolutionArchive(tmp_path / "arc")
    lab, base, lab_runner = _lab_world(tmp_path)
    benchmarked: list[str] = []
    promoted: list[str] = []
    candidates = [
        _candidate(
            tmp_path, "weakened-tests", base, 1.5,
            mutate=lambda sandbox: (sandbox / "tests" / "test_calc.py").write_text(
                LAB_TEST.replace("    assert add(1, 2) == 3\n", ""))),
        _candidate(
            tmp_path, "edited-evaluator", base, 1.5,
            mutate=lambda sandbox: (sandbox / "friday" / "evaluation.py").write_text(
                LAB_EVALUATOR + "# candidate edit\n")),
    ]

    result = C.run_controlled_evolution(
        objective_id="EVOLAB-004",
        parent_version="v1",
        candidates=candidates,
        baseline={"score": 1.0},
        tolerance=0.05,
        archive=archive,
        lab=lab,
        lab_runner=lab_runner,
        benchmark=lambda candidate: benchmarked.append(candidate.candidate_version) or candidate.measurements,
        promote=lambda candidate: promoted.append(candidate.candidate_version) or C.PromotionRecord(candidate.candidate_version, "v1"),
        rollback=lambda record: None,
        health_check=lambda record: True,
        allowance=C.InMemoryEvolutionAllowance(),
    )

    by_version = {r.candidate_version: r for r in result.results}
    assert by_version["weakened-tests"].decision == C.UNSAFE
    assert "WEAKENED" in by_version["weakened-tests"].reason
    assert by_version["edited-evaluator"].decision == C.UNSAFE
    assert "TAMPERED" in by_version["edited-evaluator"].reason
    assert benchmarked == []
    assert promoted == []
    assert len(lab_runner.calls) == 0
    assert len(archive.query("")) == 2
