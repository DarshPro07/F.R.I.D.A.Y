"""
EVOLAB-003: the Release Boundary decides what an evolution candidate may do alone.

Real Book (confirmation), real Evolution Archive and real policy zones on a tmp
directory. The clock is injected; nothing reads the machine or the network.
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from friday import confirmation
from friday import evolution_archive as A
from friday import release_boundary as R

ROOT = Path(__file__).resolve().parent.parent
NOW = datetime(2026, 10, 12, 9, 0, tzinfo=timezone.utc)
SHA = "a" * 40
OTHER_SHA = "b" * 40
HASH = "c" * 64
OWNER = "owner"
MEASURE = {"score": 1.0}


@pytest.fixture
def rb(tmp_path):
    return R.ReleaseBoundary(tmp_path / "rb", archive=A.EvolutionArchive(tmp_path / "arch"))


def skill(**kw):
    base = dict(kind=R.SKILL, paths=("skills/weather/SKILL.md",), built_by="builder-1",
                verified_by="verifier-1", rollback="revert:abc1234",
                parent_version="v1", candidate_version="v2-weather",
                objective="OBJ-1", hypothesis="a weather skill helps",
                baseline=MEASURE, measurements=MEASURE,
                manifest={"permissions": [], "data_access": [], "network": []},
                baseline_manifest={"permissions": [], "data_access": [], "network": []})
    base.update(kw)
    return R.Candidate(**base)


def adapter(**kw):
    base = dict(kind=R.ADAPTER, paths=("friday/fabric_adapters/newthing.py",),
                hypothesis="a replaceable adapter helps", candidate_version="v2-adapter")
    base.update(kw)
    return skill(**base)


def core(**kw):
    base = dict(kind=R.CORE, paths=("friday/store.py",), commit=SHA, candidate_hash=HASH,
                hypothesis="a core change helps", candidate_version="v2-core",
                manifest=None, baseline_manifest=None)
    base.update(kw)
    return skill(**base)


def report(sha=SHA, *, verdicts=None, dirty=False, utc=NOW - timedelta(hours=1)):
    verdicts = verdicts or {}
    gates = [{"gate": name, "verdict": verdicts.get(name, "PASS"), "mandatory": True,
              "exit_code": 0} for name in R.REQUIRED_GATES]
    return {"ready": all(g["verdict"] == "PASS" for g in gates),
            "commit": {"sha": sha, "dirty": dirty},
            "host": {"utc": utc.isoformat(timespec="seconds")}, "gates": gates}


def owner_yes(candidate, *, target=None, commit=None):
    """The owner's real yes for one core candidate, from a real Book."""
    book = confirmation.Book()
    asked = book.ask(candidate.candidate_version, R.CORE_ACTION,
                     target or candidate.candidate_hash, "release this core?",
                     {"commit": commit or candidate.commit}, seconds=3600)
    assert book.approve(asked.nonce).ok
    return book, asked.nonce


def archived(rb):
    return rb.archive.query()


# -- a skill or adapter that widens nothing ---------------------------------

def test_low_risk_skill_promotes_without_a_prompt_while_authority_is_live(rb):
    rb.grant(scope=[R.SKILL, R.ADAPTER], expires_at=NOW + timedelta(days=7), granted_by=OWNER, now=NOW)
    d = rb.decide(skill(), now=NOW)
    assert d.outcome == R.PROMOTE, d.reasons
    assert d.authority_id


def test_low_risk_adapter_promotes_under_the_same_authority(rb):
    rb.grant(scope=[R.ADAPTER], expires_at=NOW + timedelta(days=7), granted_by=OWNER, now=NOW)
    assert rb.decide(adapter(), now=NOW).outcome == R.PROMOTE


def test_promotion_stops_once_the_authority_expires(rb):
    rb.grant(scope=[R.SKILL], expires_at=NOW + timedelta(days=1), granted_by=OWNER, now=NOW)
    assert rb.decide(skill(), now=NOW + timedelta(hours=23)).outcome == R.PROMOTE
    d = rb.decide(skill(candidate_version="v3"), now=NOW + timedelta(days=1))
    assert d.outcome == R.WAIT_OWNER
    assert any("authority" in r for r in d.reasons)


def test_promotion_stops_once_the_authority_is_revoked(rb):
    grant = rb.grant(scope=[R.SKILL], expires_at=NOW + timedelta(days=7), granted_by=OWNER, now=NOW)
    assert rb.decide(skill(), now=NOW).outcome == R.PROMOTE
    rb.revoke(grant["id"], revoked_by=OWNER, now=NOW + timedelta(hours=1))
    assert rb.decide(skill(candidate_version="v3"), now=NOW + timedelta(hours=2)).outcome == R.WAIT_OWNER


def test_authority_is_scoped_to_the_kinds_it_names(rb):
    rb.grant(scope=[R.SKILL], expires_at=NOW + timedelta(days=7), granted_by=OWNER, now=NOW)
    assert rb.decide(adapter(), now=NOW).outcome == R.WAIT_OWNER


def test_authority_survives_a_new_boundary_on_the_same_root(tmp_path):
    root, arch = tmp_path / "rb", A.EvolutionArchive(tmp_path / "arch")
    R.ReleaseBoundary(root, archive=arch).grant(
        scope=[R.SKILL], expires_at=NOW + timedelta(days=7), granted_by=OWNER, now=NOW)
    assert R.ReleaseBoundary(root, archive=arch).decide(skill(), now=NOW).outcome == R.PROMOTE


def test_core_can_never_be_a_standing_authority(rb):
    with pytest.raises(R.BoundaryError):
        rb.grant(scope=[R.CORE], expires_at=NOW + timedelta(days=7), granted_by=OWNER, now=NOW)


@pytest.mark.parametrize("expires", [NOW, NOW - timedelta(days=1), datetime(2027, 1, 1)])
def test_authority_needs_a_future_timezone_aware_expiry(rb, expires):
    with pytest.raises(R.BoundaryError):
        rb.grant(scope=[R.SKILL], expires_at=expires, granted_by=OWNER, now=NOW)


# -- widening is not low-risk -------------------------------------------------

@pytest.mark.parametrize("field", ["permissions", "data_access", "network"])
@pytest.mark.parametrize("make", [skill, adapter])
def test_a_manifest_that_widens_waits_for_the_owner(rb, make, field):
    rb.grant(scope=[R.SKILL, R.ADAPTER], expires_at=NOW + timedelta(days=7), granted_by=OWNER, now=NOW)
    manifest = {"permissions": [], "data_access": [], "network": []}
    manifest[field] = ["new-scope"]
    d = rb.decide(make(manifest=manifest), now=NOW)
    assert d.outcome == R.WAIT_OWNER
    assert any(field in r for r in d.reasons)


def test_a_manifest_that_narrows_or_keeps_scope_stays_low_risk(rb):
    rb.grant(scope=[R.ADAPTER], expires_at=NOW + timedelta(days=7), granted_by=OWNER, now=NOW)
    base = {"permissions": ["read"], "data_access": ["notes"], "network": ["api.example.com"]}
    narrower = {"permissions": ["read"], "data_access": [], "network": ["API.example.com "]}
    assert rb.decide(adapter(manifest=narrower, baseline_manifest=base), now=NOW).outcome == R.PROMOTE


@pytest.mark.parametrize("manifest", [None, {}, {"permissions": []}, {"permissions": "read",
                         "data_access": [], "network": []}])
def test_a_missing_or_malformed_manifest_is_not_low_risk(rb, manifest):
    rb.grant(scope=[R.SKILL], expires_at=NOW + timedelta(days=7), granted_by=OWNER, now=NOW)
    assert rb.decide(skill(manifest=manifest), now=NOW).outcome == R.WAIT_OWNER


def test_a_missing_baseline_manifest_is_not_low_risk(rb):
    rb.grant(scope=[R.SKILL], expires_at=NOW + timedelta(days=7), granted_by=OWNER, now=NOW)
    assert rb.decide(skill(baseline_manifest=None), now=NOW).outcome == R.WAIT_OWNER


def test_the_builder_cannot_verify_its_own_skill(rb):
    rb.grant(scope=[R.SKILL], expires_at=NOW + timedelta(days=7), granted_by=OWNER, now=NOW)
    d = rb.decide(skill(verified_by="builder-1"), now=NOW)
    assert d.outcome == R.REFUSED


def test_a_skill_that_touches_core_paths_is_refused_not_promoted(rb):
    rb.grant(scope=[R.SKILL, R.ADAPTER], expires_at=NOW + timedelta(days=7), granted_by=OWNER, now=NOW)
    assert rb.decide(skill(paths=("skills/a/SKILL.md", "friday/store.py")), now=NOW).outcome == R.REFUSED
    assert rb.decide(adapter(paths=("friday/fabric_adapters/a.py", "friday/store.py")), now=NOW).outcome == R.REFUSED


# -- trust roots ----------------------------------------------------------------

@pytest.mark.parametrize("path", ["friday/policy.py", "friday/confirmation.py", "FRIDAY\\Secret_Broker.py",
                                  "friday/release_boundary.py".replace("release_boundary", "trust")])
def test_trust_roots_are_refused_whatever_authority_exists(rb, path):
    rb.grant(scope=[R.SKILL, R.ADAPTER], expires_at=NOW + timedelta(days=7), granted_by=OWNER, now=NOW)
    assert rb.decide(skill(paths=("skills/a/SKILL.md", path)), now=NOW).outcome == R.REFUSED
    assert rb.decide(adapter(paths=(path,)), now=NOW).outcome == R.REFUSED
    candidate = core(paths=(path,))
    book, nonce = owner_yes(candidate)
    d = rb.decide(candidate, report=report(), book=book, nonce=nonce, now=NOW)
    assert d.outcome == R.REFUSED
    assert any("trust root" in r for r in d.reasons)


def test_an_unknown_kind_is_refused(rb):
    assert rb.decide(skill(kind="plugin"), now=NOW).outcome == R.REFUSED


# -- core -------------------------------------------------------------------------

def test_core_promotes_with_a_passing_report_and_the_owner_naming_the_hash(rb):
    candidate = core()
    book, nonce = owner_yes(candidate)
    d = rb.decide(candidate, report=report(), book=book, nonce=nonce, now=NOW)
    assert d.outcome == R.PROMOTE, d.reasons


def test_core_is_not_promoted_by_a_standing_authority(rb):
    rb.grant(scope=[R.SKILL, R.ADAPTER], expires_at=NOW + timedelta(days=7), granted_by=OWNER, now=NOW)
    d = rb.decide(core(), report=report(), now=NOW)
    assert d.outcome == R.WAIT_OWNER


def test_core_with_a_good_report_but_no_owner_yes_waits(rb):
    assert rb.decide(core(), report=report(), now=NOW).outcome == R.WAIT_OWNER


def test_core_is_refused_when_the_owner_yes_names_a_different_hash(rb):
    candidate = core()
    book, nonce = owner_yes(candidate, target="d" * 64)
    d = rb.decide(candidate, report=report(), book=book, nonce=nonce, now=NOW)
    assert d.outcome == R.REFUSED
    assert any("owner" in r for r in d.reasons)


def test_core_is_refused_when_the_owner_yes_names_a_different_commit(rb):
    candidate = core()
    book, nonce = owner_yes(candidate, commit=OTHER_SHA)
    assert rb.decide(candidate, report=report(), book=book, nonce=nonce, now=NOW).outcome == R.REFUSED


def test_an_owner_yes_is_spent_once(rb):
    candidate = core()
    book, nonce = owner_yes(candidate)
    assert rb.decide(candidate, report=report(), book=book, nonce=nonce, now=NOW).outcome == R.PROMOTE
    assert rb.decide(candidate, report=report(), book=book, nonce=nonce, now=NOW).outcome == R.REFUSED


def test_a_bad_report_does_not_spend_the_owner_yes(rb):
    candidate = core()
    book, nonce = owner_yes(candidate)
    assert rb.decide(candidate, report=report(OTHER_SHA), book=book, nonce=nonce, now=NOW).outcome == R.REFUSED
    assert rb.decide(candidate, report=report(), book=book, nonce=nonce, now=NOW).outcome == R.PROMOTE


@pytest.mark.parametrize("bad", [
    pytest.param(lambda: report(OTHER_SHA), id="other-commit"),
    pytest.param(lambda: report(SHA[:12]), id="short-sha"),
    pytest.param(lambda: report(utc=NOW - timedelta(days=3)), id="stale"),
    pytest.param(lambda: report(utc=NOW + timedelta(days=1)), id="from-the-future"),
    pytest.param(lambda: report(dirty=True), id="dirty-tree"),
    pytest.param(lambda: report(verdicts={"Soak": "NOT_RUN"}), id="soak-not-run"),
    pytest.param(lambda: report(verdicts={"Audit": "FAIL"}), id="gate-failed"),
    pytest.param(lambda: {**report(), "gates": report()["gates"][:-1]}, id="gate-missing"),
    pytest.param(lambda: {**report(), "gates": []}, id="no-gates"),
    pytest.param(lambda: {**report(verdicts={"Budget": "FAIL"}), "ready": True}, id="ready-flag-lies"),
    pytest.param(lambda: {**report(), "host": {"utc": "yesterday"}}, id="unparseable-time"),
    pytest.param(lambda: None, id="no-report"),
    pytest.param(lambda: "PASS", id="not-a-mapping"),
])
def test_core_is_refused_without_a_good_release_report_for_that_commit(rb, bad):
    candidate = core()
    book, nonce = owner_yes(candidate)
    d = rb.decide(candidate, report=bad(), book=book, nonce=nonce, now=NOW)
    assert d.outcome == R.REFUSED, d.reasons
    assert any("report" in r for r in d.reasons)


def test_a_gate_marked_non_mandatory_in_the_report_cannot_hide_a_failure(rb):
    candidate = core()
    rep = report(verdicts={"Soak": "FAIL"})
    for g in rep["gates"]:
        g["mandatory"] = False
    book, nonce = owner_yes(candidate)
    assert rb.decide(candidate, report=rep, book=book, nonce=nonce, now=NOW).outcome == R.REFUSED


@pytest.mark.parametrize("kw", [{"commit": SHA[:7]}, {"commit": ""}, {"candidate_hash": ""}])
def test_core_needs_a_full_commit_and_a_candidate_hash(rb, kw):
    candidate = core(**kw)
    book, nonce = owner_yes(candidate, target=candidate.candidate_hash or "x", commit=candidate.commit or "x")
    assert rb.decide(candidate, report=report(), book=book, nonce=nonce, now=NOW).outcome == R.REFUSED


# -- archive ------------------------------------------------------------------------

def test_every_decision_is_archived_with_its_evidence(rb):
    rb.grant(scope=[R.SKILL], expires_at=NOW + timedelta(days=7), granted_by=OWNER, now=NOW)
    promoted = rb.decide(skill(), now=NOW)
    waited = rb.decide(skill(candidate_version="v3", manifest={"permissions": ["fs"], "data_access": [],
                                                              "network": []}), now=NOW)
    refused = rb.decide(skill(candidate_version="v4", paths=("friday/policy.py",)), now=NOW)
    rows = {r["candidate_version"]: r for r in archived(rb)}
    assert len(rows) == 3
    assert rows["v2-weather"]["decision"] == A.PROMOTE
    assert rows["v3"]["decision"] == A.REFUSED and rows["v4"]["decision"] == A.REFUSED
    for d, version in ((promoted, "v2-weather"), (waited, "v3"), (refused, "v4")):
        ev = rows[version]["evaluation"]
        assert ev["outcome"] == d.outcome and ev["reasons"] == list(d.reasons)
        assert ev["kind"] == R.SKILL and ev["zone"]
        assert d.experiment_id == rows[version]["id"]
    assert rows["v2-weather"]["evaluation"]["authority_id"]
    json.dumps(archived(rb), allow_nan=False)


def test_a_core_decision_archives_the_report_it_relied_on(rb):
    candidate = core()
    book, nonce = owner_yes(candidate)
    rb.decide(candidate, report=report(), book=book, nonce=nonce, now=NOW)
    ev = archived(rb)[0]["evaluation"]
    assert ev["candidate_hash"] == HASH and ev["commit"] == SHA
    assert ev["report"]["sha"] == SHA and ev["report"]["gates"]["Soak"] == "PASS"
    assert nonce not in json.dumps(ev)


def test_a_refused_decision_never_counts_as_a_rejection(rb):
    c = skill(paths=("friday/policy.py",))
    rb.decide(c, now=NOW)
    assert rb.archive.find_rejected(parent_version=c.parent_version, hypothesis=c.hypothesis,
                                    scope=c.paths) is None


# -- drift guard ----------------------------------------------------------------------

def test_required_gates_are_the_gates_release_gate_actually_runs():
    source = (ROOT / "scripts" / "release_gate.py").read_text(encoding="utf-8")
    for name in R.REQUIRED_GATES:
        assert re.search(rf'"{re.escape(name)}"', source), f"release_gate has no gate named {name!r}"


# -- a decision that cannot be archived must not spend anything -------------------------

@pytest.mark.parametrize("field", ["objective", "hypothesis", "parent_version", "candidate_version"])
def test_a_core_candidate_the_archive_would_refuse_leaves_the_owner_yes_unspent(rb, field):
    candidate = core()
    book, nonce = owner_yes(candidate)
    broken = R.Candidate(**{**candidate.__dict__, field: ""})
    with pytest.raises(R.BoundaryError):
        rb.decide(broken, report=report(), book=book, nonce=nonce, now=NOW)
    assert archived(rb) == []
    # the yes was never spent: the same owner yes still releases the fixed candidate
    assert rb.decide(candidate, report=report(), book=book, nonce=nonce, now=NOW).outcome == R.PROMOTE


@pytest.mark.parametrize("bad", [
    pytest.param(dict(kind="mystery", candidate_version=""), id="unknown-kind-no-version"),
    pytest.param(dict(baseline={}), id="empty-baseline"),
    pytest.param(dict(measurements={"score": float("nan")}), id="non-finite-measurement"),
    pytest.param(dict(parent_version="", rollback=""), id="no-rollback-at-all"),
])
def test_an_invalid_candidate_is_a_clean_boundary_error_never_a_raw_archive_error(rb, bad):
    with pytest.raises(R.BoundaryError):
        rb.decide(skill(**bad), now=NOW)
    assert archived(rb) == []


def test_a_refused_decision_with_a_complete_record_is_still_returned_and_archived(rb):
    decision = rb.decide(skill(kind="mystery"), now=NOW)
    assert decision.outcome == R.REFUSED and len(archived(rb)) == 1


# -- a damaged authority file fails closed with the documented error ---------------------

@pytest.mark.parametrize("line", [
    pytest.param('{"event":"revoke"}', id="revoke-without-id"),
    pytest.param('{"event":"grant","id":"AUTH-x","scope":["skill"]}', id="grant-without-times"),
    pytest.param('{"event":"grant","id":"AUTH-x","scope":"skill","granted_at":"2026-10-12T09:00:00+00:00",'
                 '"expires_at":"2026-10-20T09:00:00+00:00"}', id="scope-not-a-list"),
    pytest.param('{"event":"grant","id":"AUTH-x","scope":["skill"],"granted_at":"yesterday",'
                 '"expires_at":"2026-10-20T09:00:00+00:00"}', id="unparseable-time"),
    pytest.param('{"event":"grant","id":"AUTH-x","scope":["skill"],"granted_at":"2026-10-12T09:00:00",'
                 '"expires_at":"2026-10-20T09:00:00"}', id="naive-time"),
    pytest.param('{"event":"grant","id":7,"scope":["skill"],"granted_at":"2026-10-12T09:00:00+00:00",'
                 '"expires_at":"2026-10-20T09:00:00+00:00"}', id="id-not-a-string"),
])
def test_a_malformed_authority_event_is_a_boundary_error_not_a_bare_exception(rb, line):
    rb.root.mkdir(parents=True, exist_ok=True)
    rb.path.write_text(line + "\n", encoding="utf-8")
    with pytest.raises(R.BoundaryError):
        rb.live_authority(R.SKILL, NOW)
    with pytest.raises(R.BoundaryError):
        rb.revoke("AUTH-x", revoked_by=OWNER, now=NOW)
    with pytest.raises(R.BoundaryError):
        rb.decide(skill(), now=NOW)
