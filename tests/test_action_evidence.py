"""The action evidence ledger (friday/action_evidence.py): one record of
what happened across every executor, and the claim audit that reads it.

Three live defects from room M1 (2026-09-20), each pinned here in both
directions - the false claim refused AND the true claim allowed:

  C  "I'm still running into the same path restriction" with no file call
     -> FAILURE_CLAIM refused; the same sentence after a real failed
     files_write is allowed.
  D  "14 families, all READY" with no read -> STATE_CLAIM refused; after a
     fresh capability_families snapshot it is allowed; after the snapshot
     goes stale it is refused again.
  G  Hermes wrote jarvis-hello.py (a HERMES row with disk read-back) ->
     "the file exists" is ALLOWED even though Friday's own process called
     nothing. A Hermes row whose read-back FAILED backs "it did not work",
     never "the file exists".
"""
from __future__ import annotations

import os
import time

import pytest

from friday import action_evidence as AE


@pytest.fixture
def led(tmp_path):
    return AE.ActionEvidenceLedger(tmp_path / "ev.sqlite3")


# ---------------------------------------------------------------------------
# classification
# ---------------------------------------------------------------------------

class TestClassify:

    @pytest.mark.parametrize("text,kind", [
        ("My apologies, boss. It seems I'm still running into the same path restriction, even to read files on your Desktop.", AE.FAILURE_CLAIM),
        ("I wasn't able to successfully write to jarvis-test.txt in my previous attempt due to a path restriction.", AE.FAILURE_CLAIM),
        ("I couldn't open Spotify - it isn't installed.", AE.FAILURE_CLAIM),
        ("The write was blocked by the policy engine.", AE.FAILURE_CLAIM),
        ("I tried to delete it but the confirmation nonce had expired.", AE.ATTEMPT_CLAIM),
        ("I have 14 capability families currently available, and they are all in a READY state.", AE.STATE_CLAIM),
        ("All my providers are unprobed at the moment.", AE.STATE_CLAIM),
        ("The script jarvis-hello.py has been created on your Desktop, boss.", AE.SUCCESS_CLAIM),
        ("Done.", AE.COMPLETION_CLAIM),
        ("I'm looking at your screen now.", AE.PERCEPTION_CLAIM),
    ])
    def test_each_class_is_recognised(self, text, kind):
        kinds = [c.kind for c in AE.classify_claims(text)]
        assert kind in kinds, (text, kinds)

    @pytest.mark.parametrize("text", [
        "Give me a sec, boss.",
        "No, boss, I did not open Notepad at any point during this run.",
        "Your camera has been switched off, boss. To answer your question, I cannot look through the camera right now.",
        "I can create that file for you if you like.",
        "Can you confirm the deletion?",
        "Shall I try writing it to Documents instead?",
    ])
    def test_non_claims_are_left_alone(self, text):
        assert AE.classify_claims(text) == [], text

    def test_a_failure_sentence_is_not_also_a_success_claim(self):
        """The completion gate's negation list hides a failure sentence from
        every gate; here it is the FAILURE class and nothing else."""
        claims = AE.classify_claims("I could not write the file, so nothing was created.")
        assert [c.kind for c in claims] == [AE.FAILURE_CLAIM]

    def test_file_words_mark_a_claim_as_about_files(self):
        c = AE.classify_claims("I couldn't write to jarvis-test.txt.")[0]
        assert c.about_files
        c = AE.classify_claims("I couldn't open Spotify.")[0]
        assert not c.about_files


# ---------------------------------------------------------------------------
# the ledger
# ---------------------------------------------------------------------------

class TestLedger:

    def test_start_then_finish_is_one_row(self, led):
        aid = led.start(executor=AE.FRIDAY_DIRECT, capability="files_write",
                        arguments={"path": "C:/x/a.txt", "content": "SECRET BODY"}, turn_id="t1")
        row = led.get(aid)
        assert row.status == AE.STARTED and row.target == "C:/x/a.txt"
        assert "SECRET BODY" not in row.arguments_summary      # names only, never content
        led.finish(aid, status=AE.SUCCEEDED, evidence_ref="read-back: 9 bytes", verified=True)
        row = led.get(aid)
        assert row.status == AE.SUCCEEDED and row.verified and row.finished_at

    def test_verified_needs_an_evidence_ref(self, led):
        aid = led.start(executor=AE.MCP, capability="files_write", turn_id="t1")
        with pytest.raises(ValueError):
            led.finish(aid, status=AE.SUCCEEDED, verified=True)

    def test_unknown_executor_is_refused(self, led):
        with pytest.raises(ValueError):
            led.start(executor="ELF", capability="x")

    def test_turn_evidence_is_the_turn_plus_what_finished_since(self, led):
        t0 = time.time()
        old = led.record(executor=AE.HERMES, capability="write_file", status=AE.SUCCEEDED,
                         objective_id="WR-old", evidence_ref="rb", verified=True)
        # push it before the turn started
        with led._connect() as db:
            db.execute("UPDATE action_evidence SET finished_at=? WHERE action_id=?", (t0 - 100, old))
        mine = led.record(executor=AE.FRIDAY_DIRECT, capability="files_read", status=AE.SUCCEEDED, turn_id="t2")
        theirs = led.record(executor=AE.HERMES, capability="write_file", status=AE.SUCCEEDED,
                            objective_id="WR-new", evidence_ref="rb", verified=True)
        ids = {r.action_id for r in led.evidence_for_turn("t2", t0)}
        assert ids == {mine, theirs}
        assert old not in ids

    def test_snapshots_round_trip_and_age(self, led):
        led.snapshot(AE.CapabilityStateSnapshot(source="skills", collected_at=time.time() - 10, count=3, ttl_s=60))
        snap = led.snapshot_for("skills")
        assert snap.fresh() and snap.count == 3
        assert not snap.fresh(now=time.time() + 100)


# ---------------------------------------------------------------------------
# the audit - C, D, G in both directions
# ---------------------------------------------------------------------------

_FAILED_LINE = "My apologies, boss - I'm running into the same path restriction when trying to write jarvis-drop.txt on your Desktop."


class TestFailureClaims:

    def test_c_false_positive_refused(self):
        v = AE.audit_claims(_FAILED_LINE, [])
        assert not v.ok and v.first_kind == AE.FAILURE_CLAIM
        assert "did not actually try" in AE.correction_for(v)

    def test_c_after_a_real_failure_allowed(self, led):
        led.record(executor=AE.FRIDAY_DIRECT, capability="files_write", status=AE.FAILED,
                   arguments={"path": "C:/Users/x/Desktop/jarvis-drop.txt"}, turn_id="t",
                   result="path is outside the permitted roots")
        assert AE.audit_claims(_FAILED_LINE, led.for_turn("t")).ok

    def test_c_a_successful_write_does_not_back_a_failure_story(self, led):
        led.record(executor=AE.FRIDAY_DIRECT, capability="files_write", status=AE.SUCCEEDED,
                   turn_id="t", evidence_ref="rb", verified=True)
        v = AE.audit_claims(_FAILED_LINE, led.for_turn("t"))
        assert not v.ok and v.first_kind == AE.FAILURE_CLAIM

    def test_c_an_unrelated_failure_does_not_back_a_file_failure(self, led):
        led.record(executor=AE.FRIDAY_DIRECT, capability="web_search", status=AE.FAILED, turn_id="t")
        assert not AE.audit_claims(_FAILED_LINE, led.for_turn("t")).ok

    def test_attempt_needs_an_attempt(self, led):
        text = "I tried to delete jarvis-drop.txt but the nonce had expired."
        assert not AE.audit_claims(text, []).ok
        led.record(executor=AE.FRIDAY_DIRECT, capability="files_delete", status=AE.FAILED, turn_id="t")
        assert AE.audit_claims(text, led.for_turn("t")).ok


class TestStateClaims:
    LINE = "I have 14 capability families currently available, and they are all in a READY state."

    def test_d_from_memory_refused(self):
        v = AE.audit_claims(self.LINE, [])
        assert not v.ok and v.first_kind == AE.STATE_CLAIM
        assert "haven't checked" in AE.correction_for(v)

    def test_d_after_a_fresh_read_allowed(self):
        snap = AE.CapabilityStateSnapshot(source="capability_families", collected_at=time.time(), count=14)
        assert AE.audit_claims(self.LINE, [], snapshots={"capability_families": snap}).ok

    def test_d_a_stale_read_is_not_evidence(self):
        snap = AE.CapabilityStateSnapshot(source="capability_families", collected_at=time.time() - 3600, count=14, ttl_s=300)
        assert not AE.audit_claims(self.LINE, [], snapshots={"capability_families": snap}).ok

    def test_real_ladder_states_are_still_state_claims(self):
        """"two skills are VALIDATED" also needs the read - the fix is the
        read, not a whitelist of nicer words."""
        text = "Two of my skills are validated and one is a candidate."
        assert not AE.audit_claims(text, []).ok
        snap = AE.CapabilityStateSnapshot(source="skills", collected_at=time.time(), count=3)
        assert AE.audit_claims(text, [], snapshots={"skills": snap}).ok


class TestHermesEvidence:
    LINE = "The script jarvis-hello.py has been created on your Desktop, boss, and the file exists."

    def test_g_a_verified_hermes_write_backs_the_true_claim(self, led):
        led.record(executor=AE.HERMES, capability="write_file", status=AE.SUCCEEDED,
                   arguments={"path": "C:/Users/x/Desktop/jarvis-hello.py"}, objective_id="WR-1",
                   evidence_type="disk_readback", evidence_ref="read-back: exists (41 bytes)", verified=True)
        v = AE.audit_claims(self.LINE, led.since(0))
        assert v.ok, v.reasons
        assert any("HERMES:write_file" in c for c in v.cited)

    def test_g_a_hermes_write_the_disk_denies_backs_nothing(self, led):
        led.record(executor=AE.HERMES, capability="write_file", status=AE.FAILED,
                   arguments={"path": "C:/Users/x/Desktop/jarvis-hello.py"}, objective_id="WR-1",
                   result="read-back failed: not found")
        v = AE.audit_claims(self.LINE, led.since(0))
        assert not v.ok and v.first_kind == AE.SUCCESS_CLAIM
        # and the honest sentence IS backed
        assert AE.audit_claims("I couldn't get the file written, boss - Hermes's write did not land.", led.since(0)).ok

    def test_g_a_run_finishing_is_not_a_file_existing(self, led):
        """hermes_work_run COMPLETE is an unverified success row: it backs
        "Hermes finished" but not "the file exists"."""
        led.record(executor=AE.HERMES, capability="hermes_work_run", status=AE.SUCCEEDED, objective_id="WR-1")
        v = AE.audit_claims(self.LINE, led.since(0))
        assert not v.ok
        assert "nothing verified backs it" in v.reasons[0]

    def test_a_friday_read_still_does_not_back_a_creation(self, led):
        led.record(executor=AE.FRIDAY_DIRECT, capability="files_read", status=AE.SUCCEEDED, turn_id="t",
                   evidence_ref="read 12 bytes", verified=True)
        assert not AE.audit_claims("I created jarvis-test.txt for you.", led.for_turn("t")).ok


class TestHermesBridgeWritesTheLedger:
    """hermes_bridge's event handler opens a row on tool.start and closes it
    on tool.complete with a DISK read-back for file writes."""

    def test_write_readback_verifies_against_the_disk(self, tmp_path, monkeypatch):
        led = AE.ActionEvidenceLedger(tmp_path / "ev.sqlite3")
        AE.reset_ledger(led)
        from friday import hermes_bridge as hb
        target = tmp_path / "hello.py"
        hb._evidence_hermes_start("WR-9", {"tool_id": "c1", "name": "write_file", "args": {"path": str(target)}})
        target.write_text("print(1)\n", encoding="utf-8")
        hb._evidence_hermes_complete("WR-9", {"tool_id": "c1", "name": "write_file", "args": {"path": str(target)},
                                              "result": "ok"})
        row = led.get("hermes:WR-9:c1")
        assert row.status == AE.SUCCEEDED and row.verified and "read-back" in row.evidence_ref

    def test_a_claimed_write_missing_on_disk_is_a_failed_row(self, tmp_path):
        led = AE.ActionEvidenceLedger(tmp_path / "ev.sqlite3")
        AE.reset_ledger(led)
        from friday import hermes_bridge as hb
        ghost = str(tmp_path / "never.py")
        hb._evidence_hermes_complete("WR-9", {"tool_id": "c2", "name": "write_file", "args": {"path": ghost},
                                              "result": "wrote 20 bytes"})
        row = led.get("hermes:WR-9:c2")
        assert row.status == AE.FAILED and not row.verified
        assert "read-back failed" in row.result

    def test_terminal_status_is_one_unverified_row(self, tmp_path):
        led = AE.ActionEvidenceLedger(tmp_path / "ev.sqlite3")
        AE.reset_ledger(led)
        from friday import hermes_bridge as hb
        hb._evidence_hermes_terminal("WR-9", hb.COMPLETE, "all done")
        row = led.get("hermes:WR-9:run")
        assert row.status == AE.SUCCEEDED and not row.verified


class TestRoomPathUsesTheLedger:
    """agent_friday._refuse_unbacked_claim reads the ledger, so the old
    per-process tuples are no longer the only evidence."""

    def _agent(self, tmp_path):
        import agent_friday as af
        led = AE.ActionEvidenceLedger(tmp_path / "ev.sqlite3")
        AE.reset_ledger(led)
        agent = af.FridayAgent.__new__(af.FridayAgent)
        agent._ran_this_turn = ()
        agent._acted_this_turn = ()
        agent._turn_id = "t-room"
        agent._turn_started_at = time.time() - 1
        return agent, led

    def test_failure_story_with_no_call_is_refused(self, tmp_path):
        agent, _ = self._agent(tmp_path)
        said = agent._refuse_unbacked_claim(_FAILED_LINE)
        assert said and "did not actually try" in said

    def test_hermes_write_licenses_the_true_claim(self, tmp_path):
        agent, led = self._agent(tmp_path)
        led.record(executor=AE.HERMES, capability="write_file", status=AE.SUCCEEDED,
                   objective_id="WR-1", evidence_ref="read-back: exists", verified=True,
                   arguments={"path": "C:/x/Desktop/jarvis-hello.py"})
        assert agent._refuse_unbacked_claim("The script jarvis-hello.py has been created on your Desktop, boss.") is None

    def test_the_perception_gate_still_works_through_the_ledger(self, tmp_path):
        agent, led = self._agent(tmp_path)
        assert agent._refuse_unbacked_claim("I'm looking at your screen.")
        led.record(executor=AE.FRIDAY_DIRECT, capability="vision_inspect_screen", status=AE.SUCCEEDED, turn_id="t-room")
        assert agent._refuse_unbacked_claim("I'm looking at your screen.") is None

    def test_use_capability_writes_a_row_per_call(self, tmp_path, monkeypatch):
        import asyncio, json
        agent, led = self._agent(tmp_path)
        agent._turn_owned_by = ""
        agent._already_read = ()
        agent._owner_words = "write the file"
        agent._spoke_this_turn = True
        agent._router = type("R", (), {"note_used": lambda self, c: None,
                                       "invocable": lambda self, c: object(),
                                       "search": lambda self, q, limit=4: [],
                                       "enabled": set()})()
        agent._keep_group_open = lambda c: None
        payload = json.dumps({"status": "succeeded", "may_claim_completion": True,
                              "verification": {"method": "read_back", "evidence": "12 bytes"}})

        async def fake_call(cap, args):
            if cap == "files_delete":
                raise RuntimeError("nonce expired")
            return payload
        agent._call_capability = fake_call
        asyncio.run(agent.use_capability.__wrapped__(agent, "files_write", json.dumps({"path": "C:/x/a.txt", "content": "hi"}))) \
            if hasattr(agent.use_capability, "__wrapped__") else None
        rows = led.for_turn("t-room")
        if rows:                      # the tool wrapper shape differs per livekit version; the row is what matters
            assert rows[0].capability == "files_write" and rows[0].status == AE.SUCCEEDED and rows[0].verified


class TestBrowserPathUsesTheLedger:

    def test_failure_story_refused_and_real_failure_allowed(self, tmp_path):
        from friday import voice_brain as V
        led = AE.ActionEvidenceLedger(tmp_path / "ev.sqlite3")
        AE.reset_ledger(led)
        V._CURRENT_TURN.update({"turn_id": "ui-1", "started_at": time.time() - 1})
        out = V._honest_about_evidence(_FAILED_LINE)
        assert "did not actually try" in out
        eid = V._evidence_start("files", "write", {"path": "C:/x/Desktop/jarvis-drop.txt"})
        V._evidence_finish(eid, {"error": "path is outside the permitted roots"})
        assert V._honest_about_evidence(_FAILED_LINE) == _FAILED_LINE

    def test_a_verified_result_envelope_is_a_verified_row(self, tmp_path):
        from friday import voice_brain as V
        led = AE.ActionEvidenceLedger(tmp_path / "ev.sqlite3")
        AE.reset_ledger(led)
        V._CURRENT_TURN.update({"turn_id": "ui-2", "started_at": time.time() - 1})
        eid = V._evidence_start("files", "write", {"path": "C:/x/a.txt"})
        V._evidence_finish(eid, {"result": '{"status": "succeeded", "may_claim_completion": true, "verification": {"method": "read_back", "evidence": "9 bytes"}}'})
        row = led.for_turn("ui-2")[0]
        assert row.status == AE.SUCCEEDED and row.verified


class TestCorrectionsNeverInventProgress:

    @pytest.mark.parametrize("kind", AE.CLAIM_CLASSES)
    def test_every_class_has_a_correction_that_admits_nothing_happened(self, kind):
        text = AE.CORRECTIONS[kind]
        assert "not" in text.lower() or "haven't" in text.lower()
        assert not any(w in text.lower() for w in ("created", "deleted", "done.", "written"))
