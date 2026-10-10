"""LEARN-001: neural/adaptor promotion is blocked on a threshold regression.

The gate reuses `friday.arena` (no second regression-gate mechanism): a new
adaptor version promotes only when its held-out score does not fall below the
active one's by more than the tolerance, and only above an absolute floor.
"""
from friday import arena
from friday import adaptor_promotion as ap

PROVENANCE = {"commit": "abc1234", "date": "2026-10-05"}


def make(tmp_path, **kw):
    return ap.AdaptorLadder(tmp_path / "adaptors.sqlite3", **kw)


def store(tmp_path):
    return arena.ArenaStore(tmp_path / "arena.json")


def test_first_adaptor_above_floor_promotes(tmp_path):
    ladder = make(tmp_path, floor=0.5)
    ladder.capture("intent-router", holdout=0.90, cases=200)
    out = ladder.promote("intent-router", store=store(tmp_path), provenance=PROVENANCE)
    assert out["status"] == "promoted" and out["version"] == 1
    assert ladder.current("intent-router")["version"] == 1


def test_below_floor_adaptor_never_promotes(tmp_path):
    ladder = make(tmp_path, floor=0.5)
    ladder.capture("intent-router", holdout=0.30, cases=200)
    out = ladder.promote("intent-router", store=store(tmp_path), provenance=PROVENANCE)
    assert out["status"] == "blocked" and "holdout_floor" in out["reason"]
    assert ladder.current("intent-router") is None


def test_promotion_blocked_on_threshold_regression(tmp_path):
    """Acceptance: a new version that regresses the active adaptor on the
    held-out metric is not promoted, and the active version keeps serving."""
    s = store(tmp_path)
    ladder = make(tmp_path, floor=0.5, tolerance=0.02)
    ladder.capture("intent-router", holdout=0.92, cases=200)
    assert ladder.promote("intent-router", store=s, provenance=PROVENANCE)["status"] == "promoted"

    ladder.capture("intent-router", holdout=0.70, cases=200)  # forgot/regressed
    out = ladder.promote("intent-router", store=s, provenance=PROVENANCE)
    assert out["status"] == "blocked"
    assert "no_regression" in out["reason"]
    assert out["active_version"] == 1
    # the bad new version never replaces the active one
    assert ladder.current("intent-router")["version"] == 1
    assert ladder.listing(state="ACTIVE") == [
        {"name": "intent-router", "version": 1, "state": "ACTIVE", "holdout": 0.92}]


def test_a_non_regressing_new_version_supersedes(tmp_path):
    s = store(tmp_path)
    ladder = make(tmp_path, floor=0.5, tolerance=0.02)
    ladder.capture("intent-router", holdout=0.90, cases=200)
    ladder.promote("intent-router", store=s, provenance=PROVENANCE)
    ladder.capture("intent-router", holdout=0.95, cases=200)
    out = ladder.promote("intent-router", store=s, provenance=PROVENANCE)
    assert out["status"] == "promoted" and out["version"] == 2 and out["previous"] == 1
    assert ladder.current("intent-router")["version"] == 2
    assert {r["version"]: r["state"] for r in ladder.listing()} == {1: "SUPERSEDED", 2: "ACTIVE"}


def test_a_dip_within_tolerance_is_not_a_regression(tmp_path):
    s = store(tmp_path)
    ladder = make(tmp_path, floor=0.5, tolerance=0.05)
    ladder.capture("intent-router", holdout=0.90, cases=200)
    ladder.promote("intent-router", store=s, provenance=PROVENANCE)
    ladder.capture("intent-router", holdout=0.87, cases=200)  # 0.03 dip, within 0.05
    out = ladder.promote("intent-router", store=s, provenance=PROVENANCE)
    assert out["status"] == "promoted" and out["version"] == 2


def test_promoting_an_absent_adaptor_is_absent(tmp_path):
    assert make(tmp_path).promote("ghost", store=store(tmp_path))["status"] == "absent"


# -- LAB-004: strict wins, an external incumbent, extra gates, rollback -------


def test_default_ladder_promotes_a_tie_but_a_strict_ladder_does_not(tmp_path):
    for strict, expected in ((False, "promoted"), (True, "blocked")):
        ladder = ap.AdaptorLadder(tmp_path / f"s{strict}.sqlite3", floor=0.0, strict=strict)
        ladder.capture("r", holdout=0.8, cases=50)
        out = ladder.promote("r", store=store(tmp_path), baseline=0.8, provenance=PROVENANCE)
        assert out["status"] == expected, out
    assert "strict_win" in out["reason"]


def test_a_strict_ladder_promotes_a_win_over_an_external_baseline(tmp_path):
    ladder = ap.AdaptorLadder(tmp_path / "a.sqlite3", floor=0.0, strict=True)
    ladder.capture("r", holdout=0.9, cases=50)
    out = ladder.promote("r", store=store(tmp_path), baseline=0.8, provenance=PROVENANCE)
    assert out["status"] == "promoted" and out["previous"] is None


def test_an_extra_gate_that_fails_blocks_promotion(tmp_path):
    ladder = ap.AdaptorLadder(tmp_path / "a.sqlite3", floor=0.0)
    ladder.capture("r", holdout=0.9, cases=50)
    out = ladder.promote("r", store=store(tmp_path), extra_gates={"no_forgetting": False},
                         provenance=PROVENANCE)
    assert out["status"] == "blocked" and "no_forgetting" in out["reason"]
    assert ladder.current("r") is None


def test_rollback_restores_the_previous_version_then_nothing(tmp_path):
    s = store(tmp_path)
    ladder = ap.AdaptorLadder(tmp_path / "a.sqlite3", floor=0.0)
    for holdout in (0.6, 0.7):
        ladder.capture("r", holdout=holdout, cases=50)
        assert ladder.promote("r", store=s, provenance=PROVENANCE)["status"] == "promoted"
    assert ladder.rollback("r") == {"status": "rolled_back", "from": 2, "to": 1}
    assert ladder.current("r")["version"] == 1
    assert ladder.rollback("r") == {"status": "rolled_back", "from": 1, "to": None}
    assert ladder.current("r") is None
    assert ladder.rollback("r")["status"] == "nothing_to_roll_back"
    assert [r["state"] for r in ladder.listing()] == ["ROLLED_BACK", "ROLLED_BACK"]
