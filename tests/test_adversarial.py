"""
Adversarial reasoning (PRD v3.1 FR-008, FR-012).

    FR-008  decision output preserves disagreements, evidence and
            uncertainty rather than fabricated consensus
    FR-012  the worker that implemented a consequential change is not the
            sole authority certifying it

The panel's rules (what happens to opinions) are tested with a scripted
`infer`; one @live test runs the real five roles through the Hermes
MODEL_GATEWAY.
"""
from __future__ import annotations

import pytest

from friday import adversarial as A
from friday import contracts as c
from friday import evaluation as E
from friday import promotion as PR


def scripted(answers: dict[str, str]):
    """An `infer` that answers by role (worker attribution)."""
    calls: list[dict] = []

    def infer(system, user, *, worker, objective_id):
        calls.append({"worker": worker, "system": system[:40], "user_len": len(user),
                      "objective_id": objective_id})
        role = worker.split(":", 1)[1]
        if role in answers and isinstance(answers[role], Exception):
            raise answers[role]
        return answers[role]
    infer.calls = calls
    return infer


PANEL = {
    A.PROPOSER: ("POSITION: Adopt SQLite for the ledger.\nARGUMENT: zero ops\n"
                 "ARGUMENT: already in the tree\nEVIDENCE: [E1] [E2]\nCONFIDENCE: 80"),
    A.CONTRARIAN: ("POSITION: Do not adopt.\nOBJECTION: single-writer lock will stall "
                   "concurrent workers under load\nOBJECTION: no network replication "
                   "story for remote operation\nEVIDENCE: [E3]\nCONFIDENCE: 65"),
    A.FAILURE_ANALYST: ("FAILURE: database is locked errors during parallel Hermes runs\n"
                        "FAILURE: ledger corruption after a hard kill\n"
                        "EARLY_SIGNAL: busy timeouts in the log\n"
                        "EARLY_SIGNAL: integrity_check on boot\nCONFIDENCE: 55"),
    A.EVIDENCE_CHECKER: ("SUPPORTED: [E1] benchmark shows 10k writes/s\n"
                         "SUPPORTED: [E2]\nUNSUPPORTED: [E3] the blog post is about "
                         "Postgres, not SQLite\nMISSING: a measurement under two "
                         "concurrent writers\nCONFIDENCE: 70"),
    A.JUDGE: ("VERDICT: ADOPT\nREASON: single-writer lock is acceptable because the "
              "governor caps workers at two; replication is out of V1 scope.\n"
              "UNRESOLVED: remote replication story for remote operation\n"
              "UNCERTAINTY: behaviour under a hard kill has not been measured\n"
              "CONFIDENCE: 60"),
}

EVIDENCE = ["benchmark: 10k writes/s on this laptop", "SQLite is already a dependency",
            "blog post: databases lock under load"]


# -- FR-008 -----------------------------------------------------------------


def test_deliberation_preserves_disagreements_evidence_and_uncertainty():
    infer = scripted(PANEL)
    d = A.deliberate("Use SQLite for the objective ledger?", EVIDENCE,
                     options=["sqlite", "postgres"], objective_id="obj-d", infer=infer)
    assert d.verdict == "ADOPT" and d.confidence == 60
    assert not d.consensus
    # The contrarian's lock objection was addressed by the judge; the
    # replication objection is in UNRESOLVED; the failure analyst's two
    # failure modes were not addressed by the judge and must survive.
    assert any("corruption after a hard kill" in x for x in d.disagreements)
    assert any("database is locked" in x for x in d.disagreements)
    assert d.unresolved == ("remote replication story for remote operation",)
    assert d.uncertainty == ("behaviour under a hard kill has not been measured",)
    assert d.evidence == {"E1": EVIDENCE[0], "E2": EVIDENCE[1], "E3": EVIDENCE[2]}
    assert d.evidence_supported == ("E1", "E2")
    assert d.evidence_unsupported == ("E3",)
    assert d.evidence_missing == ("a measurement under two concurrent writers",)
    # Every role's raw words are on the record, and each was one bounded
    # inference attributed to its role (FR-055/080).
    assert set(d.positions) == set(A.ROLES)
    assert [call["worker"] for call in infer.calls] == [f"panel:{r}" for r in A.ROLES]
    assert all(call["objective_id"] == "obj-d" for call in infer.calls)
    # The judge saw the other four.
    assert infer.calls[-1]["user_len"] > max(c_["user_len"] for c_ in infer.calls[:-1])


def test_a_judge_that_ignores_an_objection_does_not_erase_it():
    answers = dict(PANEL)
    answers[A.JUDGE] = "VERDICT: ADOPT\nREASON: it is fine.\nUNRESOLVED: NONE\nCONFIDENCE: 95"
    d = A.deliberate("q", EVIDENCE, infer=scripted(answers))
    assert d.verdict == "ADOPT" and d.unresolved == ()
    assert len(d.disagreements) == 4          # 2 objections + 2 failure modes
    assert not d.consensus


def test_an_unavailable_role_is_recorded_not_invented():
    answers = dict(PANEL)
    answers[A.CONTRARIAN] = RuntimeError("gateway failed: QUOTA_EXCEEDED")
    d = A.deliberate("q", EVIDENCE, infer=scripted(answers))
    assert d.roles_unavailable == (A.CONTRARIAN,)
    assert not d.positions[A.CONTRARIAN].available
    assert "QUOTA_EXCEEDED" in d.positions[A.CONTRARIAN].error
    assert d.positions[A.CONTRARIAN].raw == ""
    # The failure analyst's positions still stand as disagreements.
    assert any("hard kill" in x for x in d.disagreements)


def test_unavailable_judge_yields_no_verdict():
    answers = dict(PANEL)
    answers[A.JUDGE] = RuntimeError("down")
    d = A.deliberate("q", EVIDENCE, infer=scripted(answers))
    assert d.verdict == "UNAVAILABLE" and d.confidence is None


def test_tagged_parse_is_structural():
    parsed = A.parse_tagged("VERDICT: ADOPT because\n  it is cheap\nUNRESOLVED: NONE\n"
                            "CONFIDENCE: about 70 percent")
    assert parsed["VERDICT"] == ["ADOPT because it is cheap"]
    assert parsed["UNRESOLVED"] == []
    assert A.confidence_of(parsed) == 70
    # The shape the live model actually produces: an empty tag line, then
    # one item per line, sometimes bulleted. Preamble stays under '_'.
    parsed = A.parse_tagged("thinking aloud first\nOBJECTION:\n- lock contention\n"
                            "2) no replication\nplain third item\nEVIDENCE:\n[E1] x\n[E2] y")
    assert parsed["_"] == ["thinking aloud first"]
    assert parsed["OBJECTION"] == ["lock contention", "no replication", "plain third item"]
    assert parsed["EVIDENCE"] == ["[E1] x", "[E2] y"]
    assert A.cited(parsed, "EVIDENCE") == ("E1", "E2")


def test_deliberate_tool_is_reachable_and_carries_verification():
    from friday.toolsets import adversarial as AT
    run = c.Run.create("deliberate", capability="decision_deliberate")
    out = AT.decision_deliberate(run, "q", EVIDENCE, infer=scripted(PANEL))
    assert out.status == c.SUCCEEDED
    assert "5 of 5 roles answered" in out.verification.evidence
    assert out.output["disagreements"]
    empty = AT.decision_deliberate(run, "   ", [], infer=scripted(PANEL))
    assert empty.status == c.FAILED


# -- FR-012 -----------------------------------------------------------------

CHANGE = A.ChangeUnderReview(
    goal="add a retry to the uploader", claim="added exponential backoff with 3 retries",
    diff="--- a/up.py\n+++ b/up.py\n@@ -1 +1,3 @@\n-send()\n+for _ in range(3):\n+    send()\n",
    verifier_result="PASS exit=0", implemented_by="hermes:friday",
    changed_files=("up.py",))


def test_reviewer_disputes_a_claim_the_diff_does_not_implement():
    infer = scripted({"model_gateway": (
        "VERDICT: DISPUTED\nFINDING: up.py retries 3 times with no backoff; the claim "
        "says exponential backoff\nCLAIM_MATCHES_DIFF: partly\nCONFIDENCE: 85")})
    r = A.independent_review(CHANGE, infer=infer)
    assert r.verdict == A.DISPUTED and r.independent
    assert r.findings and "no backoff" in r.findings[0]
    assert r.reviewed_by == "reviewer:model_gateway" and r.independent_of == "hermes:friday"


def test_findings_override_a_self_contradicting_confirmed():
    infer = scripted({"model_gateway": (
        "VERDICT: CONFIRMED\nFINDING: the loop never sleeps between attempts\n"
        "CLAIM_MATCHES_DIFF: no\nCONFIDENCE: 60")})
    assert A.independent_review(CHANGE, infer=infer).verdict == A.DISPUTED


def test_empty_diff_is_inconclusive_and_unavailable_reviewer_is_honest():
    infer = scripted({"model_gateway": "VERDICT: CONFIRMED\nFINDING: NONE\nCLAIM_MATCHES_DIFF: yes\nCONFIDENCE: 90"})
    blank = A.ChangeUnderReview(goal="g", claim="c", diff="   ", verifier_result="",
                                implemented_by="w")
    r = A.independent_review(blank, infer=infer)
    assert r.verdict == A.INCONCLUSIVE and "no diff" in r.findings[-1]
    down = A.independent_review(CHANGE, infer=scripted({"model_gateway": RuntimeError("down")}))
    assert down.verdict == A.INCONCLUSIVE and down.error == "down"


def _passing_attempt():
    return E.Attempt(task="t", agent="hermes", verdict=E.PASSED, exit_code=0, detail="ok")


def test_promotion_refuses_a_consequential_change_without_independent_review(tmp_path):
    changed = tuple(f"src/m{i}.py" for i in range(PR.CONSEQUENTIAL_FILES + 1))
    d = PR.decide(tmp_path, changed, attempt=_passing_attempt(), approved=True)
    assert not d.allowed and d.reason == PR.NOT_INDEPENDENTLY_REVIEWED
    # A small change does not need the second authority.
    small = PR.decide(tmp_path, ("src/m0.py",), attempt=_passing_attempt(), approved=True)
    assert small.checks[2] == {"check": "independent_review", "ok": True,
                               "detail": "not required at this size"}


def test_promotion_refuses_a_disputed_review_and_accepts_a_confirmed_one(tmp_path):
    changed = tuple(f"src/m{i}.py" for i in range(5))
    disputed = A.ReviewEvidence(verdict=A.DISPUTED, findings=("m2.py breaks on empty input",),
                                claim_matches_diff="partly", confidence=80,
                                reviewed_by="reviewer:model_gateway", independent_of="hermes")
    d = PR.decide(tmp_path, changed, attempt=_passing_attempt(), approved=True, review=disputed)
    assert not d.allowed and d.reason == PR.REVIEW_DISPUTED
    assert "empty input" in d.detail
    confirmed = A.ReviewEvidence(verdict=A.CONFIRMED, findings=(), claim_matches_diff="yes",
                                 confidence=85, reviewed_by="reviewer:model_gateway",
                                 independent_of="hermes")
    for f in changed:
        (tmp_path / f).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / f).write_text("x = 1\n", encoding="utf-8")
    d = PR.decide(tmp_path, changed, attempt=_passing_attempt(), approved=True,
                  review=confirmed)
    assert d.allowed, d.detail
    assert {"check": "independent_review", "ok": True,
            "detail": "CONFIRMED by reviewer:model_gateway"} in d.checks


def test_the_implementer_reviewing_itself_does_not_count(tmp_path):
    changed = tuple(f"src/m{i}.py" for i in range(5))
    self_review = A.ReviewEvidence(verdict=A.CONFIRMED, findings=(), claim_matches_diff="yes",
                                   confidence=99, reviewed_by="hermes", independent_of="hermes")
    assert not self_review.independent
    d = PR.decide(tmp_path, changed, attempt=_passing_attempt(), approved=True,
                  review=self_review)
    assert not d.allowed and d.reason == PR.NOT_INDEPENDENTLY_REVIEWED
    assert any(ch["check"] == "independent_review" and "implementer" in ch["detail"]
               for ch in d.checks)


def test_development_run_reviews_the_real_diff(tmp_path):
    """End to end on a real git repo: the run reads the diff against its
    base commit and the reviewer's verdict reaches the gate."""
    import subprocess
    from friday import development as D

    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "config", "user.email", "t@t"], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "config", "user.name", "t"], check=True)
    (tmp_path / "a.py").write_text("x = 1\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(tmp_path), "add", "."], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "commit", "-qm", "base"], check=True)
    base = subprocess.run(["git", "-C", str(tmp_path), "rev-parse", "HEAD"],
                          capture_output=True, text=True, check=True).stdout.strip()
    (tmp_path / "a.py").write_text("x = 2\n", encoding="utf-8")

    seen = {}

    def infer(system, user, *, worker, objective_id):
        seen["user"] = user
        return "VERDICT: CONFIRMED\nFINDING: NONE\nCLAIM_MATCHES_DIFF: yes\nCONFIDENCE: 80"

    run = D.DevelopmentRun(goal="bump x", project="p", root=str(tmp_path), base_commit=base)
    run.attempt = _passing_attempt()
    run.review(("a.py",), claim="x is now 2", infer=infer)
    assert "-x = 1" in seen["user"] and "+x = 2" in seen["user"]
    assert "IMPLEMENTED_BY: worker" in seen["user"]
    assert run.review_evidence.verdict == A.CONFIRMED
    assert run.report()["review"]["independent"] is True


@pytest.mark.live
def test_live_panel_runs_through_the_model_gateway():
    d = A.deliberate(
        "Should Friday store its objective ledger in SQLite or Postgres for V1?",
        ["Friday already ships SQLite; the resource governor caps workers at 2",
         "remote operation is a P1 requirement"],
        options=["sqlite", "postgres"], objective_id="live-panel")
    assert d.verdict in ("ADOPT", "REJECT", "DEFER")
    assert set(d.positions) == set(A.ROLES)
    assert all(p.available for p in d.positions.values()), d.roles_unavailable
    assert d.positions[A.CONTRARIAN].parsed.get("OBJECTION")
