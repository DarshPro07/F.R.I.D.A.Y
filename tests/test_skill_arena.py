"""
CAP-MKT-004: competing candidates for one capability are chosen by a Skill
Arena on the same tasks, never by their descriptions.

Every candidate runner here is an injected function: no network, no model, no
installed tool. The arena measures latency with an injected clock and grades the
outputs itself; a runner reports only the output it produced and its usage.
"""
from __future__ import annotations

import json

import pytest

from friday import arena as A
from friday import skill_arena as S

PROVENANCE = {"commit": "abc1234", "date": "2026-10-10T00:00:00+00:00"}
TASKS = ("t1", "t2", "t3", "t4")
CAP = "pdf-extract"


class Clock:
    """The arena's clock; a runner 'takes time' by advancing it."""

    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


CLOCK = Clock()


def _grader(task, output):
    """Canned outputs are (accuracy, completed, violations) triples the arena's
    grader reads; a real grader would compare the output with an expectation."""
    accuracy, completed, violations = output
    return S.Grade(accuracy=accuracy, completed=completed, security_violations=violations)


def _runner(accuracy=1.0, completed=True, tokens=1000, latency_ms=500.0, tool_calls=3,
            retries=0, security_violations=0, seen=None):
    def run(task):
        if seen is not None:
            seen.append(task)
        CLOCK.now += latency_ms / 1000.0
        return S.RunOutput(output=(accuracy, completed, security_violations), tokens=tokens,
                           tool_calls=tool_calls, retries=retries)
    return run


def _arena(tmp_path=None, store=None, **kw):
    roster = S.Roster(tmp_path / "roster.json") if tmp_path else S.Roster()
    return S.SkillArena(roster=roster, store=store or A.ArenaStore(), provenance=PROVENANCE,
                        clock=CLOCK, **kw)


def _run(arena, candidates, tasks=TASKS, cap=CAP, grader=_grader):
    return arena.run(cap, candidates, tasks, grader)


def test_every_candidate_runs_the_same_task_set():
    seen_a, seen_b = [], []
    outcome = _run(_arena(), [S.Candidate("a", _runner(seen=seen_a)),
                              S.Candidate("b", _runner(seen=seen_b))])
    assert seen_a == list(TASKS) and seen_b == list(TASKS)
    assert {r.cases for r in outcome.report.results} == {len(TASKS)}


def test_tasks_may_be_a_one_shot_iterable():
    outcome = _run(_arena(), [S.Candidate("a", _runner())], tasks=(t for t in TASKS))
    assert outcome.report.results[0].cases == len(TASKS)


def test_winner_is_chosen_by_measured_results_and_losers_are_archived_with_scores():
    good = S.Candidate("good", _runner(accuracy=0.95, tokens=1200))
    cheap_wrong = S.Candidate("cheap", _runner(accuracy=0.3, tokens=100, completed=False))
    middling = S.Candidate("mid", _runner(accuracy=0.7, tokens=900, retries=2))
    outcome = _run(_arena(), [cheap_wrong, middling, good])

    assert outcome.winner == "good"
    assert outcome.status("good") == S.ACTIVE
    assert outcome.status("cheap") == S.ARCHIVED and outcome.status("mid") == S.ARCHIVED
    for cid in ("cheap", "mid"):
        entry = outcome.entry(cid)
        assert entry["score"] < outcome.entry("good")["score"]
        assert set(entry["metrics"]) >= {"accuracy", "completion", "tokens", "latency_ms",
                                         "tool_calls", "retries", "security_violations",
                                         "maintenance"}
    assert outcome.entry("cheap")["metrics"]["completion"] == 0.0
    assert outcome.entry("mid")["metrics"]["retries"] == 2.0


def test_the_other_dimensions_break_a_tie_on_accuracy():
    lean = S.Candidate("lean", _runner(tokens=400, latency_ms=200.0, tool_calls=1))
    heavy = S.Candidate("heavy", _runner(tokens=4000, latency_ms=3000.0, tool_calls=9, retries=3))
    assert _run(_arena(), [heavy, lean]).winner == "lean"


def test_latency_is_measured_by_the_arena_not_reported_by_the_runner():
    slow = S.Candidate("slow", _runner(latency_ms=2000.0))
    fast = S.Candidate("fast", _runner(latency_ms=100.0))
    outcome = _run(_arena(), [slow, fast])
    assert outcome.entry("slow")["metrics"]["latency_ms"] == pytest.approx(2000.0)
    assert outcome.entry("fast")["metrics"]["latency_ms"] == pytest.approx(100.0)
    assert outcome.winner == "fast"


def test_a_runner_has_no_way_to_claim_accuracy_or_a_clean_record():
    for claim in ({"accuracy": 1.0}, {"completed": True}, {"latency_ms": 0.0},
                  {"security_violations": 0}):
        with pytest.raises(TypeError):
            S.RunOutput(output="x", **claim)


def test_a_candidate_is_judged_by_the_graders_reading_of_its_output():
    """Both candidates say they are perfect; only one is right."""
    expected = {"t1": "A", "t2": "B", "t3": "C", "t4": "D"}

    def grade(task, output):
        return S.Grade(accuracy=1.0 if output["answer"] == expected[task] else 0.0,
                       completed=output["answer"] is not None)

    def liar(task):
        CLOCK.now += 0.0     # claims to be instant and perfect
        return S.RunOutput(output={"answer": "wrong", "accuracy": 1.0, "latency_ms": 0})

    def honest(task):
        CLOCK.now += 3.0
        return S.RunOutput(output={"answer": expected[task]}, tokens=5000)

    outcome = _run(_arena(), [S.Candidate("liar", liar), S.Candidate("honest", honest)],
                   grader=grade)
    assert outcome.winner == "honest"
    assert outcome.entry("liar")["metrics"]["accuracy"] == 0.0


def test_a_security_violation_disqualifies_the_most_accurate_candidate():
    risky = S.Candidate("risky", _runner(accuracy=1.0, security_violations=1))
    safe = S.Candidate("safe", _runner(accuracy=0.6))
    outcome = _run(_arena(), [risky, safe])
    assert outcome.winner == "safe"
    assert "security_clean" in outcome.report.result(S.qualified_id(CAP, "risky")).failed_gates
    assert outcome.status("risky") == S.ARCHIVED


def test_a_crashing_runner_is_a_failed_candidate_and_does_not_stop_the_arena():
    def broken(task):
        raise RuntimeError("boom")
    outcome = _run(_arena(), [S.Candidate("broken", broken),
                              S.Candidate("fine", _runner(accuracy=0.5))])
    assert outcome.winner == "fine"
    assert outcome.entry("broken")["metrics"]["completion"] == 0.0
    assert outcome.entry("broken")["metrics"]["errors"] == len(TASKS)


def test_a_grader_that_cannot_read_an_output_fails_that_task_only():
    def grade(task, output):
        if output is None:
            raise KeyError("no answer")
        return _grader(task, output)

    def silent(task):
        return S.RunOutput(output=None)
    outcome = _run(_arena(), [S.Candidate("silent", silent),
                              S.Candidate("ok", _runner(accuracy=0.4))], grader=grade)
    assert outcome.winner == "ok"
    assert outcome.entry("silent")["metrics"]["errors"] == len(TASKS)


def test_grades_and_usage_are_validated():
    for bad in ({"accuracy": 1.5}, {"accuracy": float("nan")}, {"security_violations": -1},
                {"completed": 1}):
        with pytest.raises(ValueError):
            S.Grade(**{"accuracy": 1.0, "completed": True, **bad})
    for bad in ({"tokens": -1}, {"tool_calls": 1.5}, {"retries": True}):
        with pytest.raises(ValueError):
            S.RunOutput(output="x", **bad)


def test_a_runner_returning_junk_counts_as_a_failed_task():
    outcome = _run(_arena(), [S.Candidate("junk", lambda task: {"accuracy": 1.0}),
                              S.Candidate("ok", _runner(accuracy=0.4))])
    assert outcome.winner == "ok"
    assert outcome.entry("junk")["metrics"]["errors"] == len(TASKS)


def test_nobody_wins_when_every_candidate_fails_a_gate():
    arena = _arena()
    outcome = _run(arena, [S.Candidate("x", _runner(security_violations=2))])
    assert outcome.winner == ""
    assert outcome.status("x") == S.ARCHIVED
    assert arena.roster.active(CAP) == ""


def test_maintenance_is_a_scored_dimension():
    stale = S.Candidate("stale", _runner(), maintenance=0.1)
    kept = S.Candidate("kept", _runner(), maintenance=0.9)
    assert _run(_arena(), [stale, kept]).winner == "kept"


def test_a_later_better_candidate_replaces_the_winner():
    arena = _arena()
    first = _run(arena, [S.Candidate("v1", _runner(accuracy=0.7))])
    assert first.winner == "v1" and arena.roster.active(CAP) == "v1"

    second = _run(arena, [S.Candidate("v1", _runner(accuracy=0.7)),
                          S.Candidate("v2", _runner(accuracy=0.95))])
    assert second.winner == "v2"
    assert arena.roster.active(CAP) == "v2"
    assert arena.roster.entry(CAP, "v1")["status"] == S.ARCHIVED
    assert arena.roster.entry(CAP, "v1")["score"] > 0
    assert second.replaced == "v1"


def test_a_challenger_inside_the_margin_does_not_displace_the_incumbent():
    arena = _arena()
    _run(arena, [S.Candidate("v1", _runner(accuracy=0.80))])
    outcome = _run(arena, [S.Candidate("v1", _runner(accuracy=0.80)),
                           S.Candidate("v2", _runner(accuracy=0.801))])
    assert outcome.winner == "v1" and outcome.replaced == ""
    assert arena.roster.active(CAP) == "v1"
    assert arena.roster.entry(CAP, "v2")["status"] == S.ARCHIVED


def test_the_incumbent_must_run_the_same_tasks_as_the_challenger():
    arena = _arena()
    _run(arena, [S.Candidate("v1", _runner())])
    with pytest.raises(S.SkillArenaError, match="incumbent"):
        _run(arena, [S.Candidate("v2", _runner())])


def test_a_failing_incumbent_is_replaced_by_any_passing_challenger():
    arena = _arena()
    _run(arena, [S.Candidate("v1", _runner(accuracy=0.9))])
    outcome = _run(arena, [S.Candidate("v1", _runner(accuracy=0.9, security_violations=1)),
                           S.Candidate("v2", _runner(accuracy=0.2))])
    assert outcome.winner == "v2" and arena.roster.active(CAP) == "v2"


def test_a_run_whose_incumbent_changed_underneath_it_is_refused():
    """Two runs for one capability: the second must not apply a decision made
    against an incumbent that the first has since replaced."""
    arena = _arena()
    _run(arena, [S.Candidate("v1", _runner(accuracy=0.5))])

    def interloper(task):
        if not interloper.done:
            interloper.done = True
            _run(arena, [S.Candidate("v1", _runner(accuracy=0.5)),
                         S.Candidate("v2", _runner(accuracy=0.99))])
        return _runner(accuracy=0.5)(task)
    interloper.done = False

    with pytest.raises(S.SkillArenaError, match="changed"):
        _run(arena, [S.Candidate("v1", interloper), S.Candidate("v3", _runner(accuracy=0.6))])
    assert arena.roster.active(CAP) == "v2"


def test_the_comparison_is_stored_and_only_the_winner_promotes():
    store = A.ArenaStore()
    arena = _arena(store=store)
    outcome = _run(arena, [S.Candidate("w", _runner(accuracy=0.9)),
                           S.Candidate("l", _runner(accuracy=0.4))])
    assert store.report(outcome.report.report_id) is not None
    assert outcome.report.kind is A.ArenaKind.SKILL
    A.promote(A.ArenaKind.SKILL, S.qualified_id(CAP, "w"), report_id=outcome.report.report_id,
              store=store, exclusive=True)
    with pytest.raises(A.PromotionRefused):
        A.promote(A.ArenaKind.SKILL, S.qualified_id(CAP, "l"),
                  report_id=outcome.report.report_id, store=store, exclusive=True)


def test_two_capabilities_sharing_a_candidate_id_keep_independent_evidence():
    store = A.ArenaStore()
    arena = _arena(store=store)
    a = _run(arena, [S.Candidate("shared", _runner(accuracy=0.9))], cap="capability-a")
    b = _run(arena, [S.Candidate("shared", _runner(accuracy=0.9))], cap="capability-b")
    assert a.report.report_id != b.report.report_id
    for cap, outcome in (("capability-a", a), ("capability-b", b)):
        A.promote(A.ArenaKind.SKILL, S.qualified_id(cap, "shared"),
                  report_id=outcome.report.report_id, store=store, exclusive=True)
    assert arena.roster.active("capability-a") == "shared"
    assert arena.roster.active("capability-b") == "shared"


def test_the_outcome_is_a_copy_not_a_handle_on_the_roster():
    arena = _arena()
    outcome = _run(arena, [S.Candidate("a", _runner(accuracy=0.9)),
                           S.Candidate("b", _runner(accuracy=0.2))])
    outcome.entries["a"]["status"] = S.ARCHIVED
    arena.roster.entry(CAP, "b")["status"] = S.ACTIVE
    assert arena.roster.entry(CAP, "a")["status"] == S.ACTIVE
    assert arena.roster.entry(CAP, "b")["status"] == S.ARCHIVED


def test_the_roster_survives_a_restart(tmp_path):
    arena = _arena(tmp_path)
    _run(arena, [S.Candidate("w", _runner(accuracy=0.9)),
                 S.Candidate("l", _runner(accuracy=0.4))])
    reloaded = S.Roster(tmp_path / "roster.json")
    assert reloaded.active(CAP) == "w"
    assert reloaded.entry(CAP, "l")["status"] == S.ARCHIVED
    assert reloaded.entry(CAP, "l")["report_id"].startswith("arena-")
    json.loads((tmp_path / "roster.json").read_text(encoding="utf-8"))


def test_the_default_roster_is_durable_next_to_the_arena_reports(tmp_path, monkeypatch):
    monkeypatch.setenv("FRIDAY_ARENA_REPORTS", str(tmp_path / "arena_reports.json"))
    first = S.SkillArena(store=A.ArenaStore(), provenance=PROVENANCE, clock=CLOCK)
    _run(first, [S.Candidate("v1", _runner())])
    restarted = S.SkillArena(store=A.ArenaStore(), provenance=PROVENANCE, clock=CLOCK)
    assert restarted.roster.active(CAP) == "v1"
    with pytest.raises(S.SkillArenaError, match="incumbent"):
        _run(restarted, [S.Candidate("v2", _runner())])


@pytest.mark.parametrize("margin", [float("nan"), float("inf"), -0.1, "0.1", True])
def test_the_margin_is_a_finite_non_negative_number(margin):
    with pytest.raises(ValueError):
        S.SkillArena(roster=S.Roster(), store=A.ArenaStore(), margin=margin)


def test_a_roster_file_that_is_not_roster_evidence_is_refused(tmp_path):
    path = tmp_path / "roster.json"
    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(S.SkillArenaError):
        S.Roster(path).active(CAP)


@pytest.mark.parametrize("candidates,tasks", [
    ([], TASKS),
    ([S.Candidate("a", _runner())], ()),
    ([S.Candidate("a", _runner()), S.Candidate("a", _runner())], TASKS),
])
def test_an_arena_needs_distinct_candidates_and_at_least_one_task(candidates, tasks):
    with pytest.raises(ValueError):
        _run(_arena(), candidates, tasks)


@pytest.mark.parametrize("cap", ["", "  ", "a/b"])
def test_a_capability_is_a_plain_name(cap):
    with pytest.raises(ValueError):
        _run(_arena(), [S.Candidate("a", _runner())], cap=cap)
