"""ACT-001 end to end: the live gates audit a spoken claim against what the
turn really did - target, kind, failure attempts and the correlation key - and
not against a single 'something acted' flag."""
from __future__ import annotations

import json

import agent_friday as A
from friday import contracts as c
from friday import turn_ledger as T
from friday import voice_brain as V

WROTE = '{"path": "foo.txt", "sha256:0123456789abcdef0123": "ok"} wrote foo.txt'


def _ledger_after_write():
    ledger = T.TurnLedger(executor="browser_brain")
    ledger.record("files_write", {"path": "foo.txt"}, WROTE, operation="files.write")
    return ledger


class _Turn:
    def __init__(self, ledger, acted=("files_write",)):
        self._acted_this_turn = tuple(acted)
        self._ledger = ledger


def _voice_gate(ledger, said):
    turn = _Turn(ledger)
    return A.FridayAgent._refuse_unbacked_claim(turn, said)


class TestBrowserPath:
    def test_a_claim_about_another_file_is_refused(self):
        said = V._honest_about_acting("I created bar.txt.", [("files", "write")], 1,
                                      _ledger_after_write())
        assert "bar.txt" not in said or "can't stand behind" in said
        assert said != "I created bar.txt."

    def test_a_claim_about_the_written_file_passes(self):
        said = V._honest_about_acting("I created foo.txt.", [("files", "write")], 1,
                                      _ledger_after_write())
        assert said == "I created foo.txt."

    def test_a_failure_claim_with_no_attempt_on_record_is_refused(self):
        ledger = T.TurnLedger(executor="browser_brain")
        said = V._honest_about_acting("The upload failed.", [], 0, ledger)
        assert said != "The upload failed."

    def test_a_failure_claim_matching_a_recorded_failure_passes(self):
        ledger = T.TurnLedger(executor="browser_brain")
        ledger.record("files_delete", {"path": "old.txt"},
                      error="permission denied", operation="files.delete")
        said = V._honest_about_acting("The delete failed.", [], 1, ledger)
        assert said == "The delete failed."

    def test_a_failure_claim_about_a_different_target_is_refused(self):
        ledger = T.TurnLedger(executor="browser_brain")
        ledger.record("files_delete", {"path": "old.txt"},
                      error="permission denied", operation="files.delete")
        said = V._honest_about_acting("Deleting report.docx failed.", [], 1, ledger)
        assert said != "Deleting report.docx failed."


class TestVoicePath:
    def test_a_claim_about_another_file_is_refused(self):
        assert _voice_gate(_ledger_after_write(), "I created bar.txt.") is not None

    def test_a_claim_about_the_written_file_passes(self):
        assert _voice_gate(_ledger_after_write(), "I created foo.txt.") is None

    def test_a_failure_claim_with_no_attempt_is_refused(self):
        turn = _Turn(T.TurnLedger(executor="voice_agent"), acted=())
        assert A.FridayAgent._refuse_unbacked_claim(turn, "The upload failed.") is not None


class TestCorrelationIsRecordedLive:
    def test_every_recorded_call_carries_the_full_key(self):
        res = _ledger_after_write().run.results[0]
        corr = res.correlation
        for key in ("workspace", "objective", "version", "action", "target",
                    "executor", "operation", "timestamp"):
            assert getattr(corr, key), key
        assert corr.target == "foo.txt"

    def test_evidence_recorded_for_another_objective_is_not_counted(self):
        ledger = _ledger_after_write()
        other = T.TurnLedger(executor="browser_brain")
        stray = other.record("files_write", {"path": "bar.txt"}, WROTE.replace("foo", "bar"))
        ledger.run.results.append(stray)       # evidence from a different turn
        assert not ledger.audit("I created bar.txt.").ok


def _failed_delete_turn():
    ledger = T.TurnLedger(executor="voice_agent")
    ledger.record("files_delete", {"path": "old.txt"}, error="permission denied")
    return _Turn(ledger, acted=())


class TestVoicePathReportsRaisedFailures:
    """A capability that raised is never in `_acted_this_turn`; saying it
    failed is the honest report and must not be replaced by the refusal."""

    def test_saying_the_recorded_failure_failed_passes(self):
        turn = _failed_delete_turn()
        assert A.FridayAgent._refuse_unbacked_claim(turn, "The delete failed.") is None

    def test_a_failure_about_another_target_is_still_refused(self):
        turn = _failed_delete_turn()
        said = "Deleting report.docx failed."
        assert A.FridayAgent._refuse_unbacked_claim(turn, said) is not None


class TestTheToolsOwnResultIsTheEvidence:
    REPORTED_FAILURE = {"status": "failed", "error": "denied"}

    def _reported_failure(self, executor):
        ledger = T.TurnLedger(executor=executor)
        ledger.record("files_write", {"path": "foo.txt"}, self.REPORTED_FAILURE)
        return ledger

    def test_a_reported_failure_cannot_back_a_completion_claim(self):
        said = V._honest_about_acting("I created foo.txt.", [("files", "write")], 1,
                                      self._reported_failure("browser_brain"))
        assert said != "I created foo.txt."
        assert _voice_gate(self._reported_failure("voice_agent"),
                           "I created foo.txt.") is not None

    def test_a_reported_failure_is_an_attempt_that_went_wrong(self):
        said = V._honest_about_acting("Writing foo.txt failed.", [("files", "write")], 1,
                                      self._reported_failure("browser_brain"))
        assert said == "Writing foo.txt failed."
        assert _voice_gate(self._reported_failure("voice_agent"),
                           "Writing foo.txt failed.") is None

    def test_a_reported_failure_arriving_as_json_text_is_parsed(self):
        ledger = T.TurnLedger(executor="voice_agent")
        res = ledger.record("files_write", {"path": "foo.txt"},
                            json.dumps(self.REPORTED_FAILURE))
        assert res.status == c.FAILED and res.error == "denied"

    def test_a_typed_success_keeps_its_own_verification_and_artifacts(self):
        tool_run = c.Run.create("write foo.txt", capability="files")
        proof = c.Verification(method="write_readback",
                               evidence="foo.txt: 12 bytes, sha256:0123456789abcdef")
        typed = c.ActionResult(
            run_id=tool_run.run_id, tool_id="files_write", status=c.SUCCEEDED,
            started_at=c.now_iso(), output={"path": "foo.txt", "bytes": 12},
            verification=proof,
            artifacts=(c.new_artifact(run_id=tool_run.run_id, type="file",
                                      title="foo.txt", path_or_uri="foo.txt",
                                      producer="files_write", verification=proof),))
        ledger = T.TurnLedger(executor="voice_agent")
        res = ledger.record("files_write", {"path": "foo.txt"}, typed.to_dict())
        assert res.verification.method == "write_readback"
        assert res.verification.evidence == proof.evidence
        assert [a.run_id for a in res.artifacts] == [ledger.run.run_id]
        assert _voice_gate(ledger, "I created foo.txt.") is None

    def test_a_success_without_verification_is_partial(self):
        ledger = T.TurnLedger(executor="voice_agent")
        res = ledger.record("files_write", {"path": "foo.txt"},
                            {"status": "succeeded", "output": {"path": "foo.txt"}})
        assert res.status == c.PARTIAL and res.verification is None


class TestStrictProofOnTheLivePath:
    def test_a_plain_text_write_does_not_back_a_claim(self):
        ledger = T.TurnLedger(executor="voice_agent")
        ledger.record("files_write", {"path": "foo.txt"}, "wrote foo.txt")
        assert _voice_gate(ledger, "I created foo.txt.") is not None

    def _sent(self, result):
        ledger = T.TurnLedger(executor="voice_agent")
        ledger.record("email_send", {"to": "sam@example.com"}, result)
        return _voice_gate(ledger, "I sent the email to sam@example.com.")

    def test_an_email_without_a_message_id_does_not_back_a_claim(self):
        assert self._sent({"sent": True}) is not None

    def test_an_email_with_a_message_id_backs_the_claim(self):
        assert self._sent({"sent": True, "message_id": "abc123"}) is None
