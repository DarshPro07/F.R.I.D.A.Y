"""
The arena framework (EVAL-004, PRD arenas for research, skills, compression and
capability paths).

EVAL-003 gave model routes one empirical promotion gate and hard-wired it to
that one kind of candidate. These tests pin the same rule for every kind: a
candidate set is compared by a scorer, the comparison is stored as a report, and
a candidate is promoted only by a passing report from the same arena. A
declaration, a flag or a sentence of free text promotes nothing.
"""
from __future__ import annotations

import json

import pytest

from friday import arena as A

PROVENANCE = {"commit": "abc1234", "date": "2026-10-05T00:00:00+00:00"}


def _score(*, ok: bool = True, cases: int = 20, score: float = 0.9) -> A.Score:
    return A.Score(gates={"success_rate": ok, "minimum_cases": cases >= 10},
                   cases=cases, score=score, metrics={"success_rate": score})


def _scorer(table: dict[str, A.Score]):
    return lambda candidate_id: table[candidate_id]


@pytest.fixture
def store(tmp_path):
    return A.ArenaStore(tmp_path / "arena_reports.json")


# --- a candidate set of each kind is compared -------------------------------

@pytest.mark.parametrize("kind", list(A.ArenaKind))
def test_a_candidate_set_of_any_kind_is_compared_and_the_best_passing_one_wins(kind):
    table = {"a": _score(score=0.7), "b": _score(score=0.95), "c": _score(score=0.8)}
    report = A.compare(kind, ["a", "b", "c"], _scorer(table), provenance=PROVENANCE)
    assert report.kind is kind
    assert report.winner == "b"
    assert [r.candidate_id for r in report.results] == ["a", "b", "c"]
    assert (report.commit, report.date) == ("abc1234", "2026-10-05T00:00:00+00:00")
    assert report.result("b").cases == 20 and report.result("b").score == 0.95


def test_a_higher_scoring_candidate_that_failed_a_gate_cannot_win():
    table = {"fast": _score(ok=False, score=0.99), "sound": _score(score=0.6)}
    report = A.compare(A.ArenaKind.COMPRESSION, ["fast", "sound"], _scorer(table),
                       provenance=PROVENANCE)
    assert report.winner == "sound"
    assert not report.result("fast").passed
    assert report.result("fast").failed_gates == ("success_rate",)


def test_when_nothing_passes_there_is_no_winner():
    table = {"a": _score(ok=False), "b": _score(ok=False)}
    report = A.compare(A.ArenaKind.SKILL, ["a", "b"], _scorer(table), provenance=PROVENANCE)
    assert report.winner == ""


def test_a_tie_goes_to_the_candidate_listed_first():
    """The incumbent is listed first, so an equal challenger does not displace it."""
    table = {"incumbent": _score(score=0.8), "challenger": _score(score=0.8)}
    report = A.compare(A.ArenaKind.SKILL, ["incumbent", "challenger"], _scorer(table),
                       provenance=PROVENANCE)
    assert report.winner == "incumbent"


def test_pass_is_derived_from_the_gate_table_and_names_every_missed_gate():
    score = A.Score(gates={"b_gate": False, "a_gate": True, "c_gate": False}, cases=12)
    report = A.build_report(A.ArenaKind.SKILL, {"x": score}, provenance=PROVENANCE)
    assert not report.result("x").passed
    assert report.result("x").failed_gates == ("b_gate", "c_gate")


def test_a_scorer_that_raises_fails_that_candidate_and_the_rest_still_compete(caplog):
    def scorer(candidate_id):
        if candidate_id == "broken":
            raise RuntimeError("boom")
        return _score(score=0.5)
    report = A.compare(A.ArenaKind.COMPRESSION, ["broken", "fine"], scorer, provenance=PROVENANCE)
    assert report.winner == "fine"
    assert report.result("broken").failed_gates == ("scored",)
    assert "RuntimeError" in caplog.text


# --- refusals ---------------------------------------------------------------

def test_a_score_with_no_gates_is_refused():
    """"No gate failed" over zero gates proves nothing."""
    with pytest.raises(ValueError):
        A.build_report(A.ArenaKind.SKILL, {"x": A.Score(gates={}, cases=50)},
                       provenance=PROVENANCE)


@pytest.mark.parametrize("ids", [[], ["a", "a"], ["a", ""]])
def test_an_empty_duplicate_or_nameless_candidate_set_is_refused(ids):
    with pytest.raises(ValueError):
        A.compare(A.ArenaKind.SKILL, ids, lambda cid: _score(), provenance=PROVENANCE)


def test_an_unknown_kind_is_refused():
    with pytest.raises(ValueError):
        A.compare("telepathy", ["a"], lambda cid: _score(), provenance=PROVENANCE)


def test_a_score_that_is_not_a_number_is_refused():
    with pytest.raises(ValueError):
        A.build_report(A.ArenaKind.SKILL, {"x": _score(score=float("nan"))},
                       provenance=PROVENANCE)


def test_provenance_defaults_to_the_golden_provenance(monkeypatch):
    from friday import golden
    monkeypatch.setattr(golden, "provenance",
                        lambda: {"commit": "feedbee", "date": "2026-10-06T00:00:00+00:00"})
    report = A.compare(A.ArenaKind.SKILL, ["a"], lambda cid: _score())
    assert (report.commit, report.date) == ("feedbee", "2026-10-06T00:00:00+00:00")


# --- the store --------------------------------------------------------------

def _report(table, kind=A.ArenaKind.SKILL, **prov):
    return A.compare(kind, list(table), _scorer(table), provenance={**PROVENANCE, **prov})


def test_a_recorded_report_is_found_by_id_and_by_candidate(store):
    report = store.record(_report({"a": _score(), "b": _score(score=0.5)}))
    assert store.report(report.report_id) == report
    assert store.latest(A.ArenaKind.SKILL, "b") == report
    assert store.latest(A.ArenaKind.COMPRESSION, "b") is None


def test_reports_survive_a_new_store_on_the_same_file(store):
    report = store.record(_report({"a": _score()}))
    assert A.ArenaStore(store.path).latest(A.ArenaKind.SKILL, "a") == report


def test_the_latest_report_replaces_an_earlier_one_but_the_earlier_stays_citable(store):
    first = store.record(_report({"a": _score()}, date="2026-10-01"))
    second = store.record(_report({"a": _score(ok=False)}, date="2026-10-02"))
    assert store.latest(A.ArenaKind.SKILL, "a") == second
    assert not store.latest(A.ArenaKind.SKILL, "a").result("a").passed
    assert store.report(first.report_id) == first


def test_the_same_candidate_name_in_two_kinds_does_not_collide(store):
    skill = store.record(_report({"x": _score()}, kind=A.ArenaKind.SKILL))
    comp = store.record(_report({"x": _score(ok=False)}, kind=A.ArenaKind.COMPRESSION))
    assert store.latest(A.ArenaKind.SKILL, "x") == skill
    assert store.latest(A.ArenaKind.COMPRESSION, "x") == comp


def test_an_unreadable_store_file_raises_instead_of_reading_as_empty(store):
    store.path.write_text("{not json", encoding="utf-8")
    with pytest.raises(A.ArenaStoreError):
        store.latest(A.ArenaKind.SKILL, "a")


def test_a_report_edited_after_it_was_recorded_is_not_evidence(store):
    """The report id is a hash of its content, so changing a result or the winner
    by hand is caught on read instead of promoting."""
    report = store.record(_report({"a": _score(ok=False)}))
    data = json.loads(store.path.read_text(encoding="utf-8"))
    data["reports"][report.report_id]["results"][0]["passed"] = True
    store.path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(A.ArenaStoreError):
        store.report(report.report_id)


def test_a_report_is_a_json_file_with_its_provenance(store):
    report = store.record(_report({"a": _score(score=0.9), "b": _score(score=0.4)}))
    row = json.loads(store.path.read_text(encoding="utf-8"))["reports"][report.report_id]
    assert (row["kind"], row["commit"], row["date"], row["winner"]) == (
        "skill", "abc1234", "2026-10-05T00:00:00+00:00", "a")
    assert {r["candidate_id"]: r["cases"] for r in row["results"]} == {"a": 20, "b": 20}


# --- promotion --------------------------------------------------------------

def test_a_candidate_is_promoted_by_a_passing_report_from_the_same_arena(store):
    report = store.record(_report({"a": _score(), "b": _score(score=0.5)}))
    promotion = A.promote(A.ArenaKind.SKILL, "b", report_id=report.report_id, store=store)
    assert (promotion.kind, promotion.candidate_id) == (A.ArenaKind.SKILL, "b")
    assert (promotion.report_id, promotion.commit) == (report.report_id, "abc1234")


@pytest.mark.parametrize("claim", ["", "   ", "validated by hand", "arena-0000000000000000"])
def test_a_declaration_or_free_text_claim_alone_cannot_promote(store, claim):
    store.record(_report({"a": _score()}))
    with pytest.raises(A.PromotionRefused):
        A.promote(A.ArenaKind.SKILL, "a", report_id=claim, store=store)


def test_a_candidate_that_failed_its_gates_is_not_promoted(store):
    report = store.record(_report({"a": _score(ok=False)}))
    with pytest.raises(A.PromotionRefused, match="success_rate"):
        A.promote(A.ArenaKind.SKILL, "a", report_id=report.report_id, store=store)


def test_a_report_from_another_arena_does_not_promote(store):
    report = store.record(_report({"a": _score()}, kind=A.ArenaKind.COMPRESSION))
    with pytest.raises(A.PromotionRefused, match="compression"):
        A.promote(A.ArenaKind.SKILL, "a", report_id=report.report_id, store=store)


def test_a_report_that_never_listed_the_candidate_does_not_promote_it(store):
    report = store.record(_report({"a": _score()}))
    with pytest.raises(A.PromotionRefused):
        A.promote(A.ArenaKind.SKILL, "stranger", report_id=report.report_id, store=store)


def test_a_superseded_report_no_longer_promotes(store):
    first = store.record(_report({"a": _score()}, date="2026-10-01"))
    store.record(_report({"a": _score(ok=False)}, date="2026-10-02"))
    with pytest.raises(A.PromotionRefused, match="superseded"):
        A.promote(A.ArenaKind.SKILL, "a", report_id=first.report_id, store=store)


def test_an_exclusive_promotion_needs_the_winner_not_just_a_pass(store):
    report = store.record(_report({"a": _score(score=0.9), "b": _score(score=0.6)}))
    A.promote(A.ArenaKind.SKILL, "b", report_id=report.report_id, store=store)
    with pytest.raises(A.PromotionRefused, match="winner"):
        A.promote(A.ArenaKind.SKILL, "b", report_id=report.report_id, store=store,
                  exclusive=True)
    assert A.promote(A.ArenaKind.SKILL, "a", report_id=report.report_id, store=store,
                     exclusive=True).candidate_id == "a"


def test_an_unreadable_store_never_promotes(store):
    report = store.record(_report({"a": _score()}))
    store.path.write_text("{not json", encoding="utf-8")
    with pytest.raises(A.PromotionRefused, match="unreadable"):
        A.promote(A.ArenaKind.SKILL, "a", report_id=report.report_id, store=store)


def test_a_store_with_no_file_promotes_nothing(tmp_path):
    empty = A.ArenaStore(tmp_path / "never_written.json")
    with pytest.raises(A.PromotionRefused):
        A.promote(A.ArenaKind.SKILL, "a", report_id="arena-0000000000000000", store=empty)


# --- the shared replay scorer -----------------------------------------------

def test_a_replay_scorer_gates_on_success_rate_and_case_count():
    scorer = A.replay_scorer(list(range(12)), lambda cid, case: cid == "good" or case < 6,
                             minimum_cases=10, success_target=0.9)
    good, poor = scorer("good"), scorer("poor")
    assert all(good.gates.values()) and good.cases == 12 and good.score == 1.0
    assert poor.gates == {"minimum_cases": True, "success_rate": False}
    assert poor.score == 0.5


def test_a_replay_below_the_case_minimum_cannot_pass_however_well_it_scored():
    scorer = A.replay_scorer([1, 2], lambda cid, case: True, minimum_cases=10)
    assert scorer("x").gates["minimum_cases"] is False
