"""
"What's running?" and "what did Hermes finish?" come from the run ledger,
never from the conversation (2026-09-04 17:12: "Hermes is still working"
forty minutes after the ledger showed nothing active).
"""
import json
import time

from friday import progress_digest as pd
from friday import voice_brain as vb


class _Log:
    def __init__(self, rows):
        self.rows = rows

    def recent(self, limit=10):
        return self.rows[:limit]

    def active(self):
        return [r for r in self.rows if r["status"] not in pd.TERMINAL]

    def sweep_orphans(self):
        return []


class _Sup:
    def __init__(self, rows):
        self.log = _Log(rows)

    def progress(self, wid):
        return {"work_run_id": wid, "seq": 2, "tools": 2,
                "line": "Hermes is reading policy.py - step 2, 40s in."}


def _patch(monkeypatch, rows):
    import friday.tools.hermes_control as hc
    monkeypatch.setattr(hc, "supervisor", lambda: _Sup(rows))


def test_finished_question_answers_from_the_ledger_not_the_chat(monkeypatch):
    rows = [{"work_run_id": "w1", "status": "FAILED", "failure_kind": "LOST",
             "origin": "production", "task": "self-check job", "handoff": "",
             "result": "lost: Friday restarted before this run finished",
             "model": "claude-haiku-4-5-20251001",
             "route_reason": "economy: tiny bounded change",
             "last_event_at": time.time() - 600}]
    _patch(monkeypatch, rows)
    out = vb.reply("what did hermes just finish and why that model?")
    assert out["action"] == "hermes.outcome"
    assert "lost in a restart" in out["reply"]
    assert "claude-haiku-4-5-20251001 (economy: tiny bounded change)" in out["reply"]


def test_no_record_is_said_plainly(monkeypatch):
    _patch(monkeypatch, [])
    out = vb.reply("Friday, what did Hermes finish?")
    assert out["reply"].startswith("I have no finished Hermes job on record")


def test_gate_and_test_runs_never_count_as_her_work(monkeypatch):
    _patch(monkeypatch, [{"work_run_id": "g", "status": "COMPLETE", "origin": "golden_gate",
                          "task": "gate", "model": "m", "route_reason": "r",
                          "last_event_at": time.time()}])
    out = vb.reply("what did hermes finish?")
    assert out["reply"].startswith("I have no finished Hermes job on record")


def test_running_question_reads_the_work_log(monkeypatch):
    _patch(monkeypatch, [{"work_run_id": "w2", "status": "WORKING", "origin": "production",
                          "task": "t", "model": "m", "route_reason": "r",
                          "last_event_at": time.time()}])
    out = vb.reply("what's running?")
    assert out["action"] == "work.status" and "did 2 tools" in out["reply"]


def test_nothing_running_is_said_plainly(monkeypatch):
    _patch(monkeypatch, [])
    assert "nothing running" in vb.reply("Friday, what's running?")["reply"].lower()


def test_what_did_you_just_do_is_not_hijacked():
    assert vb._grounded_work_answer("what did you just do") is None


def test_outcome_line_complete_with_handoff_summary():
    rec = {"status": "COMPLETE", "task": "add a docstring to _busy",
           "handoff": json.dumps({"summary": "Docstring added; tests green"}),
           "model": "claude-haiku-4-5-20251001", "route_reason": "economy",
           "last_event_at": 1000.0}
    line = pd.outcome_line(rec, now=1120.0)
    assert line.startswith("Hermes finished 2 minutes ago: add a docstring to _busy.")
    assert "Outcome: Docstring added; tests green." in line
    assert line.endswith("It ran on claude-haiku-4-5-20251001 (economy).")


def test_a_model_question_without_hermes_is_left_to_the_model():
    assert vb._grounded_work_answer("why that model for the image classifier?") is None


def test_a_delegation_claim_without_the_tool_is_replaced():
    said = "Hermes has it, sir, economy tier. I'll let you know when it's finished."
    out = vb._honest_about_hermes(said, [])
    assert out.startswith("I have not handed anything to Hermes")
    assert vb._honest_about_hermes(said, ["hermes"]) == said
    plain = "The weather is fine, sir."
    assert vb._honest_about_hermes(plain, []) == plain
    assert vb._honest_about_hermes(out, []) == out          # the correction itself is not a claim
