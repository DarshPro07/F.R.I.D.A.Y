"""Phase 1 - evidence CORRELATION. A shared ledger is not enough: the row
must be about the same deed the sentence claims.

Five negative controls from the master PRD, each proving the auditor
rejects "any recent success" as backing:

1. Hermes wrote foo.txt last turn; Friday says "I created bar.txt"   -> REJECT
2. foo.txt write failed;          Friday says "I couldn't write bar.txt" -> REJECT
3. browser click succeeded in a previous objective; "I just clicked submit" -> REJECT
4. stale snapshot;                "All capabilities are READY"        -> REJECT
5. success with the right operation but the wrong target             -> REJECT

Plus the positives each control must not break (same file -> accept;
historical wording -> older rows allowed; declared listing -> never
authoritative).
"""
from __future__ import annotations

import time

import pytest

from friday import action_evidence as AE


@pytest.fixture
def led(tmp_path):
    ledger = AE.ActionEvidenceLedger(tmp_path / "ev.sqlite3")
    AE.reset_ledger(ledger)
    yield ledger
    AE.reset_ledger(None)


def _row(cap, target, status=AE.SUCCEEDED, executor=AE.FRIDAY_DIRECT, verified=True, turn="t1", ago=0.0):
    now = time.time() - ago
    return AE.ActionEvidence(action_id=f"EV-{cap}-{target}-{ago}", executor=executor, capability=cap,
                             status=status, started_at=now - 0.1, finished_at=now, turn_id=turn,
                             target=target, evidence_type="runtime_verification" if verified else "",
                             evidence_ref="read-back" if verified else "", verified=verified)


# --------------------------------------------------------------------------
# classification carries target + operation + tense
# --------------------------------------------------------------------------

def test_claims_carry_the_named_target_and_operation():
    (c,) = AE.classify_claims("I created bar.txt on your Desktop.")
    assert c.kind == AE.SUCCESS_CLAIM and c.target == "bar.txt" and c.operation == "write"
    (c,) = AE.classify_claims("I couldn't write bar.txt - the path was blocked.")
    assert c.kind == AE.FAILURE_CLAIM and c.target == "bar.txt" and c.operation == "write"
    (c,) = AE.classify_claims("I deleted notes.md for you.")
    assert c.operation == "delete" and c.target == "notes.md"
    (c,) = AE.classify_claims("I've opened Notepad for you, sir.")
    assert c.operation == "open" and c.target == "notepad"


def test_historical_wording_is_recognised():
    (c,) = AE.classify_claims("I created bar.txt earlier, sir.")
    assert c.historical
    (c,) = AE.classify_claims("I created bar.txt.")
    assert not c.historical


# --------------------------------------------------------------------------
# the five negative controls
# --------------------------------------------------------------------------

def test_nc1_hermes_wrote_foo_does_not_back_i_created_bar():
    hermes_foo = _row("write_file", "C:/Users/x/Desktop/foo.txt", executor=AE.HERMES)
    v = AE.audit_claims("I created bar.txt.", [hermes_foo])
    assert not v.ok and v.first_kind == AE.SUCCESS_CLAIM
    assert "about 'bar.txt'" in v.reasons[0] and "about something else" in v.reasons[0]
    # and the positive: the same file IS backed
    assert AE.audit_claims("I created foo.txt.", [hermes_foo]).ok


def test_nc2_foo_write_failure_does_not_back_i_couldnt_write_bar():
    failed_foo = _row("files_write", "C:/Users/x/Desktop/foo.txt", status=AE.FAILED, verified=False)
    v = AE.audit_claims("I couldn't write bar.txt - the path was blocked.", [failed_foo])
    assert not v.ok and v.first_kind == AE.FAILURE_CLAIM
    assert "about something else" in v.reasons[0]
    assert AE.audit_claims("I couldn't write foo.txt - the path was blocked.", [failed_foo]).ok


def test_nc3_a_click_from_a_previous_objective_does_not_back_i_just_clicked(led):
    old_click = _row("browser_click", "submit", executor=AE.BROWSER, turn="t0", ago=600)
    this_turn: list = []
    v = AE.audit_claims("I just clicked submit.", this_turn, history=[old_click])
    assert not v.ok and v.first_kind == AE.SUCCESS_CLAIM
    # the historical phrasing may cite it
    assert AE.audit_claims("I clicked submit earlier.", this_turn, history=[old_click]).ok


def test_nc4_a_stale_snapshot_does_not_back_all_ready():
    stale = AE.CapabilityStateSnapshot(source="skills", collected_at=time.time() - 3600, count=14,
                                       health="READY", ttl_s=300, scope="skills")
    v = AE.audit_claims("All 14 skill families are READY.", [], snapshots={"skills": stale})
    assert not v.ok and v.first_kind == AE.STATE_CLAIM and "stale" in v.reasons[0]
    fresh = AE.CapabilityStateSnapshot(source="skills", collected_at=time.time(), count=14, health="READY",
                                       scope="skills")
    assert AE.audit_claims("All 14 skill families are READY.", [], snapshots={"skills": fresh}).ok


def test_nc4b_a_declared_listing_is_never_authoritative():
    """Configured != healthy, installed != usable: a manifest that lists 14
    families says nothing about their state right now."""
    declared = AE.CapabilityStateSnapshot(source="skills", collected_at=time.time(), count=14, health="READY",
                                          scope="skills", authority_level=AE.AUTHORITY_DECLARED)
    v = AE.audit_claims("All 14 skill families are READY.", [], snapshots={"skills": declared})
    assert not v.ok and "not authoritative" in v.reasons[0]


def test_nc5_right_operation_wrong_target_is_rejected():
    wrote_other = _row("files_write", "C:/Users/x/Desktop/other.txt")
    v = AE.audit_claims("I wrote jarvis-test.txt for you.", [wrote_other])
    assert not v.ok and "about 'jarvis-test.txt'" in v.reasons[0]


def test_right_target_wrong_operation_is_rejected():
    """A READ of the file does not back "I deleted it"; a WRITE does not
    back "I deleted it" either."""
    read_it = _row("files_read", "C:/Users/x/Desktop/jarvis-test.txt")
    wrote_it = _row("files_write", "C:/Users/x/Desktop/jarvis-test.txt")
    assert not AE.audit_claims("I deleted jarvis-test.txt.", [read_it]).ok
    assert not AE.audit_claims("I deleted jarvis-test.txt.", [wrote_it]).ok
    deleted_it = _row("files_delete", "C:/Users/x/Desktop/jarvis-test.txt")
    assert AE.audit_claims("I deleted jarvis-test.txt.", [deleted_it]).ok


def test_a_claim_naming_nothing_still_needs_a_deed_of_the_right_kind():
    """"I've saved it" with no filename: any verified write this turn backs
    it; a read alone does not."""
    read_it = _row("files_read", "C:/x/a.txt")
    wrote_it = _row("files_write", "C:/x/a.txt")
    assert not AE.audit_claims("I've saved it for you.", [read_it]).ok
    assert AE.audit_claims("I've saved it for you.", [wrote_it]).ok


def test_target_matching_is_by_file_name_across_path_shapes():
    assert AE._same_target(r"C:\Users\marke\Desktop\jarvis-test.txt", "jarvis-test.txt")
    assert AE._same_target("C:/Users/marke/Desktop/jarvis-test.txt", r"desktop\jarvis-test.txt")
    assert not AE._same_target("C:/Users/marke/Desktop/jarvis-test.txt", "jarvis-test2.txt")
    # a row that recorded no target is COMPATIBLE (the operation check still
    # applies) - it must never read as "a success about something else"
    assert AE._same_target("", "jarvis-test.txt")


# --------------------------------------------------------------------------
# the ledger keeps the new snapshot columns, including on a pre-migration file
# --------------------------------------------------------------------------

def test_snapshot_scope_and_authority_round_trip(led):
    led.snapshot(AE.CapabilityStateSnapshot(source="skills", collected_at=time.time(), count=3,
                                            scope="skills", authority_level=AE.AUTHORITY_DECLARED))
    got = led.snapshot_for("skills")
    assert got.scope == "skills" and got.authority_level == AE.AUTHORITY_DECLARED and not got.authoritative


def test_a_pre_migration_ledger_is_altered_at_open(tmp_path):
    import sqlite3
    path = tmp_path / "old.sqlite3"
    old = AE.SCHEMA.replace(",\n    scope         TEXT NOT NULL DEFAULT '',\n    authority_level TEXT NOT NULL DEFAULT 'observed'", "")
    assert "authority_level" not in old
    con = sqlite3.connect(path)
    con.executescript(old)
    con.execute("INSERT INTO state_snapshots (source, collected_at) VALUES ('skills', 1.0)")
    con.commit(); con.close()
    ledger = AE.ActionEvidenceLedger(path)
    got = ledger.snapshot_for("skills")
    assert got is not None and got.authority_level == AE.AUTHORITY_OBSERVED and got.scope == ""


def test_evidence_before_turn_returns_only_older_rows(led):
    t0 = time.time()
    a = led.record(executor=AE.FRIDAY_DIRECT, capability="files_write", status=AE.SUCCEEDED,
                   arguments={"path": "old.txt"}, turn_id="t0")
    time.sleep(0.05)
    cut = time.time()
    time.sleep(0.05)
    led.record(executor=AE.FRIDAY_DIRECT, capability="files_write", status=AE.SUCCEEDED,
               arguments={"path": "new.txt"}, turn_id="t1")
    older = led.evidence_before_turn(cut)
    assert [r.action_id for r in older] == [a]
