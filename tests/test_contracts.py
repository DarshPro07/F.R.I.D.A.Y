"""
Proof-of-work contract tests.

The headline test is test_arc_reactor_* - a permanent regression against the
agent narrating an action that never happened.
"""

from __future__ import annotations

import pytest

from friday import contracts as c
from friday import honesty
from friday.store import FACT, INFERENCE, PREFERENCE, Store

# ---------------------------------------------------------------------------
# The core invariant: success requires evidence
# ---------------------------------------------------------------------------


def test_succeeded_without_verification_is_a_construction_error():
    with pytest.raises(c.ContractError, match="without Verification"):
        c.ActionResult(
            run_id="RUN-x", tool_id="apps.open", status=c.SUCCEEDED,
            started_at=c.now_iso(),
        )


def test_succeeded_with_verification_is_allowed():
    result = c.ActionResult(
        run_id="RUN-x", tool_id="apps.open", status=c.SUCCEEDED,
        started_at=c.now_iso(),
        verification=c.Verification(
            method="process_exists", evidence="Spotify.exe pid=1234"
        ),
    )
    assert result.may_claim_completion


@pytest.mark.parametrize("status", [c.QUEUED, c.RUNNING, c.PARTIAL, c.CANCELLED])
def test_non_succeeded_statuses_may_not_claim_completion(status):
    kwargs = {"error": "stopped early"} if status == c.PARTIAL else {}
    result = c.ActionResult(
        run_id="RUN-x", tool_id="t", status=status, started_at=c.now_iso(), **kwargs
    )
    assert not result.may_claim_completion


def test_partial_cannot_claim_completion_even_with_verification():
    """Partial work is described, never claimed."""
    result = c.ActionResult(
        run_id="RUN-x", tool_id="cad.generate", status=c.PARTIAL,
        started_at=c.now_iso(), error="2 of 3 parts generated",
        verification=c.Verification(method="file_exists", evidence="2 STL files"),
    )
    assert not result.may_claim_completion


def test_verification_rejects_empty_evidence():
    with pytest.raises(c.ContractError, match="evidence"):
        c.Verification(method="process_exists", evidence="   ")
    with pytest.raises(c.ContractError, match="method"):
        c.Verification(method="", evidence="something")


def test_failed_requires_an_error_message():
    with pytest.raises(c.ContractError, match="requires an error"):
        c.ActionResult(
            run_id="RUN-x", tool_id="t", status=c.FAILED, started_at=c.now_iso()
        )


def test_unknown_status_rejected():
    with pytest.raises(c.ContractError, match="unknown status"):
        c.ActionResult(
            run_id="RUN-x", tool_id="t", status="probably_fine", started_at=c.now_iso()
        )


# ---------------------------------------------------------------------------
# Constructors
# ---------------------------------------------------------------------------


def test_constructor_flow_started_to_succeeded():
    run = c.Run.create("open spotify")
    result = c.started(run.run_id, "apps.open")
    assert result.status == c.RUNNING and result.completed_at is None

    done = c.succeeded(
        result,
        verification=c.Verification(method="process_exists", evidence="pid=99"),
    )
    assert done.status == c.SUCCEEDED
    assert done.completed_at is not None
    assert done.may_claim_completion


def test_failed_constructor_records_error():
    run = c.Run.create("open spotify")
    done = c.failed(c.started(run.run_id, "apps.open"), "not installed")
    assert done.status == c.FAILED
    assert "not installed" in done.honest_summary()


# ---------------------------------------------------------------------------
# Artifacts
# ---------------------------------------------------------------------------


def test_artifact_requires_known_type_and_path():
    verification = c.Verification(method="file_exists", evidence="4.2 KB on disk")
    with pytest.raises(c.ContractError, match="unknown artifact type"):
        c.new_artifact(run_id="RUN-x", type="hologram", title="t",
                       path_or_uri="/tmp/a", producer="cad", verification=verification)
    with pytest.raises(c.ContractError, match="path_or_uri"):
        c.new_artifact(run_id="RUN-x", type="stl", title="t",
                       path_or_uri="", producer="cad", verification=verification)


def test_artifact_must_belong_to_its_run():
    artifact = c.new_artifact(
        run_id="RUN-other", type="stl", title="gear", path_or_uri="/tmp/g.stl",
        producer="cad", verification=c.Verification(method="file_exists", evidence="ok"),
    )
    with pytest.raises(c.ContractError, match="belongs to run"):
        c.ActionResult(
            run_id="RUN-x", tool_id="cad", status=c.PARTIAL, started_at=c.now_iso(),
            error="x", artifacts=(artifact,),
        )


# ---------------------------------------------------------------------------
# Run state machine
# ---------------------------------------------------------------------------


def test_run_cannot_complete_with_an_unverified_action():
    run = c.Run.create("build a gear")
    run.record(c.failed(c.started(run.run_id, "cad.generate"), "build123d missing"))
    with pytest.raises(c.ContractError, match="cannot complete"):
        run.transition("completed")


def test_run_cannot_complete_with_no_actions_at_all():
    run = c.Run.create("build a gear")
    with pytest.raises(c.ContractError, match="cannot complete"):
        run.transition("completed")


def test_run_completes_when_every_action_succeeded():
    run = c.Run.create("open spotify")
    run.record(c.succeeded(
        c.started(run.run_id, "apps.open"),
        verification=c.Verification(method="process_exists", evidence="pid=7"),
    ))
    run.transition("completed")
    assert run.state == "completed"


# ---------------------------------------------------------------------------
# A verb needs evidence of ITS OWN kind
#
# Measured: asked to process a catalogue, the model searched "read a file",
# read the CSV with files_read - which genuinely succeeded and was genuinely
# verified - and reported "the product catalogue has been successfully
# processed". Nothing had been processed and no run existed. "Any succeeded
# action backs any claim" is what let a true sentence about a read become a
# false sentence about processing.
# ---------------------------------------------------------------------------


def _verified(run, tool_id: str):
    return run.record(c.succeeded(
        c.started(run.run_id, tool_id),
        verification=c.Verification(method="test", evidence="it happened")))


def test_reading_the_file_does_not_back_having_processed_it():
    run = c.Run.create("process the product catalogue")
    _verified(run, "files_read")
    verdict = honesty.audit(
        "The product catalogue has been successfully processed, boss.", run)
    assert not verdict.ok
    assert "files_read" in verdict.reason


def test_processing_it_does_back_having_processed_it():
    run = c.Run.create("process the product catalogue")
    _verified(run, "product_process")
    assert honesty.audit(
        "The product catalogue has been successfully processed, boss.", run).ok


def test_a_verb_with_no_capability_behind_it_can_never_be_claimed():
    """
    Friday cannot post anything. Until it can, "I posted it" is a sentence no
    amount of successful other work should be able to support.
    """
    run = c.Run.create("share this")
    _verified(run, "web_search")
    _verified(run, "files_write")
    verdict = honesty.audit("Posted it for you, boss.", run)
    assert not verdict.ok
    assert "nothing in Friday can back 'posted'" in verdict.reason


def test_a_run_that_is_still_going_does_not_back_a_past_tense_claim():
    """
    A tool call returning SUCCEEDED means the CALL succeeded. The work it
    started can still be running, or have been interrupted halfway - and
    "processed the catalogue" is a claim about the work.
    """
    run = c.Run.create("process the product catalogue")
    run.record(c.succeeded(
        c.started(run.run_id, "product_process"),
        output={"execution_state": "RUNNING", "outcome": "PENDING"},
        verification=c.Verification(method="test", evidence="started")))
    verdict = honesty.audit("The catalogue has been processed, boss.", run)
    assert not verdict.ok
    assert "RUNNING" in verdict.reason


def test_an_interrupted_run_does_not_back_it_either():
    run = c.Run.create("process the product catalogue")
    run.record(c.succeeded(
        c.started(run.run_id, "product_process"),
        output={"execution_state": "INTERRUPTED", "outcome": "PARTIAL"},
        verification=c.Verification(method="test", evidence="crashed")))
    assert honesty.audit("Processed it, boss.", run).ok, (
        "INTERRUPTED is terminal - it stopped, and saying so is honest; what "
        "must not pass is claiming completion while work is still under way")


def test_a_completed_run_backs_it():
    run = c.Run.create("process the product catalogue")
    run.record(c.succeeded(
        c.started(run.run_id, "product_process"),
        output={"execution_state": "COMPLETED", "outcome": "PARTIAL"},
        verification=c.Verification(method="test", evidence="ran")))
    assert honesty.audit("The catalogue has been processed, boss.", run).ok


def test_a_tool_that_reports_no_state_is_unchanged():
    """Absence is not evidence of an unfinished run."""
    run = c.Run.create("process the product catalogue")
    _verified(run, "product_process")
    assert honesty.audit("Processed it, boss.", run).ok


def test_the_generic_rule_still_holds_for_verbs_with_no_entry():
    run = c.Run.create("open spotify")
    _verified(run, "apps_open")
    assert honesty.audit("Opened Spotify for you.", run).ok


def test_every_prefix_the_claim_table_names_matches_a_real_capability():
    """
    A typo here is silent and dangerous in one direction: the claim becomes
    permanently unbackable, and Friday starts refusing to say it did something
    it did. An empty tuple means that on purpose; a misspelled prefix means it
    by accident.
    """
    from friday import capabilities

    known = list(capabilities.CAPABILITIES)
    for word, prefixes in honesty.CLAIM_EVIDENCE.items():
        for prefix in prefixes:
            assert any(name.startswith(prefix) for name in known), (
                f"{word!r} requires {prefix!r}, which matches no capability")


def test_a_verb_with_no_capability_is_deliberate_not_forgotten():
    """
    The empty entries are the interesting ones. If one of them ever gains a
    capability, this fails and the table has to be updated on purpose.
    """
    from friday import capabilities

    should_be_impossible = {"posted", "published", "uploaded", "subscribed",
                            "emailed", "tweeted", "printed"}
    assert should_be_impossible <= set(honesty.CLAIM_EVIDENCE)
    for word in should_be_impossible:
        assert honesty.CLAIM_EVIDENCE[word] == (), (
            f"{word} now has evidence declared; check it is really backed")
    assert not any(name.startswith(("mail_", "post_", "publish_", "upload_"))
                   for name in capabilities.CAPABILITIES), \
        "a posting or sending capability exists now - CLAIM_EVIDENCE is stale"


def test_an_honest_sentence_about_reading_is_never_flagged():
    run = c.Run.create("what is in this csv")
    _verified(run, "files_read")
    for text in ("I read the file - it lists five products.",
                 "I haven't processed it, only read it.",
                 "I can process it properly if you want."):
        assert honesty.audit(text, run).ok, text


# ---------------------------------------------------------------------------
# §28 ARC REACTOR — permanent regression against fake action narration
# ---------------------------------------------------------------------------


def test_arc_reactor_claim_is_rejected_when_nothing_ran():
    run = c.Run.create("Let's build an Arc Reactor design.")
    verdict = honesty.audit("Boss, the Arc Reactor design is ready.", run)
    assert not verdict.ok
    assert "no action succeeded" in verdict.reason


def test_arc_reactor_claim_is_rejected_when_the_tool_failed():
    run = c.Run.create("Let's build an Arc Reactor design.")
    run.record(c.failed(c.started(run.run_id, "cad.generate"), "no CAD capability"))
    assert not honesty.audit("I've created the design.", run).ok


def test_arc_reactor_honest_refusal_is_permitted():
    """The truthful sentence must never be flagged."""
    run = c.Run.create("Let's build an Arc Reactor design.")
    for text in (
        "I don't yet have a working CAD capability connected.",
        "I couldn't create the design - CAD isn't wired up.",
        "I can create the project structure, but not the CAD model yet.",
        "Shall I open the design workspace?",
    ):
        assert honesty.audit(text, run).ok, text


def test_arc_reactor_progress_narration_is_permitted():
    """"Working on it" is allowed while a run is open; "done" is not."""
    run = c.Run.create("Let's build an Arc Reactor design.")
    run.record(c.started(run.run_id, "cad.generate"))
    assert honesty.audit("I'm building it now.", run).ok
    assert honesty.audit("Starting the CAD model.", run).ok
    assert not honesty.audit("The design is ready.", run).ok


def test_arc_reactor_claim_allowed_once_an_artifact_verifiably_exists():
    run = c.Run.create("Let's build an Arc Reactor design.")
    artifact = c.new_artifact(
        run_id=run.run_id, type="stl", title="Arc Reactor",
        path_or_uri="/tmp/arc.stl", producer="cad.generate",
        verification=c.Verification(method="file_exists", evidence="/tmp/arc.stl, 41 KB"),
    )
    run.record(c.succeeded(
        c.started(run.run_id, "cad.generate"),
        verification=c.Verification(method="file_exists", evidence="/tmp/arc.stl, 41 KB"),
        artifacts=(artifact,),
    ))
    verdict = honesty.audit("Boss, the Arc Reactor design is ready.", run)
    assert verdict.ok
    assert verdict.evidence and "arc.stl" in verdict.evidence[0]
    assert len(run.artifacts) == 1


def test_safe_alternative_never_invents_progress():
    assert "haven't actually done that" in honesty.safe_alternative(None)
    run = c.Run.create("build it")
    assert "haven't actually done that" in honesty.safe_alternative(run)


# ---------------------------------------------------------------------------
# Claim detection
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("text", [
    "I created the file.",
    "Spotify is open now.",
    "Done.",
    "All set.",
    "I've saved it to disk.",
    "The report is complete.",
])
def test_completion_claims_detected(text):
    assert honesty.find_claims(text), text


@pytest.mark.parametrize("text", [
    "I can open Spotify for you.",
    "I could not create the file.",
    "I haven't opened it yet.",
    "Want me to create that?",
    "I'm working on it.",
    "That isn't done yet.",
    "I don't have that capability connected.",
])
def test_non_claims_not_flagged(text):
    assert not honesty.find_claims(text), text


def test_audit_with_no_run_rejects_any_claim():
    assert not honesty.audit("I opened Spotify.", None).ok
    assert honesty.audit("I can open Spotify.", None).ok


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------


def test_memory_survives_restart_with_provenance(tmp_path):
    """§13 golden test, at the storage layer."""
    db = tmp_path / "ada.sqlite3"
    store = Store(db)
    store.remember(
        "Project Arc Reactor.language", "Python",
        kind=FACT, source="user said so", confidence=1.0,
    )
    store.close()

    reopened = Store(db)  # full restart
    rows = reopened.recall("Project Arc Reactor.language")
    assert len(rows) == 1
    assert rows[0]["value"] == "Python"
    assert rows[0]["kind"] == FACT
    assert rows[0]["source"] == "user said so"
    reopened.close()


def test_inference_never_becomes_a_fact(tmp_path):
    store = Store(tmp_path / "a.db")
    store.remember("user.timezone", "IST", kind=INFERENCE,
                   source="guessed from message times", confidence=0.6)
    row = store.recall("user.timezone")[0]
    assert row["kind"] == INFERENCE
    assert row["confidence"] == 0.6
    # A FACT with the same subject is a separate row, not an upgrade.
    store.remember("user.timezone", "IST", kind=FACT, source="user confirmed")
    kinds = {r["kind"] for r in store.recall("user.timezone")}
    assert kinds == {INFERENCE, FACT}
    store.close()


def test_memory_requires_provenance(tmp_path):
    store = Store(tmp_path / "a.db")
    with pytest.raises(ValueError, match="source"):
        store.remember("k", "v", kind=FACT, source="  ")
    with pytest.raises(ValueError, match="unknown memory kind"):
        store.remember("k", "v", kind="VIBES", source="x")
    with pytest.raises(ValueError, match="confidence"):
        store.remember("k", "v", kind=FACT, source="x", confidence=1.5)
    store.close()


def test_superseding_marks_but_never_deletes(tmp_path):
    """Mark-L trimmed memory to fit a prompt; history must stay auditable."""
    store = Store(tmp_path / "a.db")
    store.remember("project.db", "JSON files", kind=FACT, source="v1")
    store.remember("project.db", "SQLite", kind=FACT, source="v2")
    assert [r["value"] for r in store.recall("project.db")] == ["SQLite"]
    assert len(store.recall("project.db", include_superseded=True)) == 2
    store.close()


def test_raw_utterance_is_never_overwritten_by_correction(tmp_path):
    """§14: raw and normalized are separate columns."""
    store = Store(tmp_path / "a.db")
    uid = store.record_utterance(
        "start plot code", normalized="start Claude Code",
        reason="STT confusion", evidence="no tool named 'plot code'",
        confidence=0.82,
    )
    row = store.get_utterance(uid)
    assert row["raw"] == "start plot code"
    assert row["normalized"] == "start Claude Code"
    assert row["correction_confidence"] == 0.82
    store.close()


def test_run_and_artifacts_round_trip(tmp_path):
    store = Store(tmp_path / "a.db")
    run = c.Run.create("make a cube", capability="design")
    artifact = c.new_artifact(
        run_id=run.run_id, type="stl", title="cube", path_or_uri="/tmp/cube.stl",
        producer="cad.generate",
        verification=c.Verification(method="file_exists", evidence="1.2 KB"),
    )
    run.record(c.succeeded(
        c.started(run.run_id, "cad.generate"),
        verification=c.Verification(method="file_exists", evidence="1.2 KB"),
        artifacts=(artifact,),
    ))
    run.transition("completed")
    store.save_run(run)
    store.close()

    reopened = Store(tmp_path / "a.db")
    loaded = reopened.load_run(run.run_id)
    assert loaded["state"] == "completed"
    assert loaded["results"][0]["verify_evidence"] == "1.2 KB"
    assert loaded["artifacts"][0]["path_or_uri"] == "/tmp/cube.stl"
    reopened.close()
