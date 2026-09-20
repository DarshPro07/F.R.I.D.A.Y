"""D-20 - a STATE figure must agree with the reading it cites.

Probe D (2026-09-20 17:07): Friday read the fabric (helpers_list, 32
providers over 14 families), then said "I have 12 skill families, all of
which are currently in a 'REGISTERED' state" and listed fourteen names. The
STATE audit passed the sentence because a fresh OBSERVED snapshot existed.
A true-looking source under a wrong number is the subtler lie. Now the
number is checked against every fresh snapshot that counts that noun.

Harness rule: the tests plant the snapshot and the sentence separately; the
audit is never given the answer.
"""
from __future__ import annotations

import time

import pytest

from friday import action_evidence as AE


def _snap(source, count, *, age_s=0.0, authority=AE.AUTHORITY_OBSERVED):
    return AE.CapabilityStateSnapshot(source=source, collected_at=time.time() - age_s, count=count,
                                      health="listed", evidence="test", ttl_s=300.0,
                                      authority_level=authority)


PROBE_D = ("I have 12 skill families, all of which are currently in a 'REGISTERED' state: "
           "code_intelligence, coding, commerce, diagnostic, media, memory, orchestration, "
           "presentation, research, roles, scraping, security, social, and writing.")


def test_probe_d_sentence_is_a_state_claim_with_a_figure():
    claims = AE.classify_claims(PROBE_D)
    assert any(c.kind == AE.STATE_CLAIM for c in claims)
    assert AE._stated_count(PROBE_D) == 12


def test_wrong_figure_over_a_fresh_reading_is_held_and_corrected_with_the_real_one():
    v = AE.audit_claims(PROBE_D, [], snapshots={"capability_families": _snap("capability_families", 14)})
    assert not v.ok
    assert v.unbacked[0].kind == AE.STATE_CLAIM
    assert v.unbacked[0].expected_count == 14
    assert "said 12" in v.reasons[0] and "14" in v.reasons[0]
    said = AE.correction_for(v)
    assert "14" in said and "12" in said and "recited" in said


def test_right_figure_over_the_same_reading_passes():
    ok = PROBE_D.replace("12 skill families", "14 skill families")
    v = AE.audit_claims(ok, [], snapshots={"capability_families": _snap("capability_families", 14)})
    assert v.ok, v.reasons
    assert any(c.startswith("snapshot:capability_families") for c in v.cited)


def test_number_words_count_too():
    v = AE.audit_claims("Fourteen families are ready, sir.", [],
                        snapshots={"capability_families": _snap("capability_families", 14)})
    assert v.ok, v.reasons
    v2 = AE.audit_claims("Twelve families are ready, sir.", [],
                         snapshots={"capability_families": _snap("capability_families", 14)})
    assert not v2.ok and v2.unbacked[0].expected_count == 14


def test_a_snapshot_of_a_different_noun_does_not_decide_the_figure():
    # 32 providers read; the sentence puts a number on FAMILIES. The provider
    # count cannot contradict it, and (negative control) it cannot vouch for
    # the figure either - the claim still passes only because a fresh
    # authoritative snapshot exists, as before this change.
    v = AE.audit_claims("I have 12 skill families and they are all registered.", [],
                        snapshots={"providers": _snap("providers", 32)})
    assert v.ok, v.reasons
    assert not any("said 12" in r for r in v.reasons)


def test_stale_reading_does_not_argue_with_the_figure():
    v = AE.audit_claims(PROBE_D, [], snapshots={"capability_families": _snap("capability_families", 14, age_s=900)})
    assert not v.ok
    assert v.unbacked[0].expected_count is None            # held for staleness, not for the number
    assert "stale" in v.reasons[0]


def test_declared_reading_does_not_argue_with_the_figure():
    v = AE.audit_claims(PROBE_D, [], snapshots={
        "capability_families": _snap("capability_families", 14, authority=AE.AUTHORITY_DECLARED)})
    assert not v.ok and v.unbacked[0].expected_count is None
    assert "not authoritative" in v.reasons[0]


def test_two_fresh_readings_one_agreeing_is_enough():
    # ui_families counts the surface (22 incl. Friday's own families); the
    # fabric count is 14. "14 families" agrees with one reading -> passes.
    snaps = {"ui_families": _snap("ui_families", 22), "capability_families": _snap("capability_families", 14)}
    assert AE.audit_claims("I have 14 skill families, all registered.", [], snapshots=snaps).ok
    v = AE.audit_claims("I have 12 skill families, all registered.", [], snapshots=snaps)
    assert not v.ok and v.unbacked[0].expected_count == 14


def test_helpers_listing_records_provider_and_family_counts(tmp_path, monkeypatch):
    from friday import voice_brain as V
    monkeypatch.setattr(AE, "_LEDGER", None, raising=False)
    monkeypatch.setattr(AE, "LEDGER_PATH", tmp_path / "ae.sqlite3", raising=False)
    led = AE.ActionEvidenceLedger(tmp_path / "ae.sqlite3")
    monkeypatch.setattr(AE, "ledger", lambda: led)
    compact = [{"id": f"p{i}", "family": fam, "state": "REGISTERED"}
               for i, fam in enumerate(["coding", "coding", "media", "research"])]
    V._snapshot_helpers(compact)
    fams = led.snapshot_for("capability_families")
    provs = led.snapshot_for("providers")
    assert fams is not None and fams.count == 3 and fams.authoritative
    assert provs is not None and provs.count == 4 and provs.authoritative


def test_helpers_listing_hands_the_model_the_counts(monkeypatch):
    """The figure is computed by code, not counted by the model over the
    list (the model said 12 over a 14-family list twice, probes D/E)."""
    import json
    from friday import voice_brain as V, fabric
    rows = [{"provider": f"p{i}", "family": fam, "state": "REGISTERED" if i else "UNAVAILABLE"}
            for i, fam in enumerate(["coding", "coding", "media", "research", "media"])]
    monkeypatch.setattr(fabric, "report", lambda: rows)
    monkeypatch.setattr(V, "_snapshot_helpers", lambda compact: None)
    d = json.loads(V._run_helpers("list", {})["result"])
    assert d["counts"] == {"providers": 5, "families": 3, "family_names": ["coding", "media", "research"],
                           "by_state": {"UNAVAILABLE": 1, "REGISTERED": 4}}
    assert len(d["providers"]) == 5


def test_a_plain_sentence_with_a_number_but_no_state_is_not_a_state_claim():
    assert not any(c.kind == AE.STATE_CLAIM for c in AE.classify_claims("Two files on the Desktop, sir."))


def test_a_subset_figure_is_not_compared_with_the_total():
    # "Two of my skills are validated" counts a part; the skills snapshot
    # counts the whole (3). Not a contradiction (regression of
    # test_action_evidence::test_real_ladder_states_are_still_state_claims).
    text = "Two of my skills are validated and one is a candidate."
    assert AE._stated_count(text) is None
    assert AE.audit_claims(text, [], snapshots={"skills": _snap("skills", 3)}).ok
