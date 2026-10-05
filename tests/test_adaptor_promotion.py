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
