"""
A request that names its file and its words is carried out AS SPOKEN.

Room M1 (2026-09-20), step 3: "Create jarvis-test.txt on my Desktop
containing exactly the words: version one. Then read the file back" was
admitted as an objective whose planner invented the path
`friday-jarvis-test-txt-on-my-desktop.txt` and the content "Created by
Friday for: ...", then refused the model's own correct files_write as
"already part of the objective". Three fixes, each pinned here:

* `friday.literals` - a filename, dictated content, URL, email, phone,
  time, branch or version spoken by the owner is a LiteralConstraint the
  planner may not rename or rewrite (A2).
* `friday.direct_action` - an exact file chain runs step by step through
  `CapabilityRuntime`, never through admission (A1), with one evidence
  row per step and the runtime's own words in the reply.
* `admit_objective` declines such a request; `_spoken_path` places a bare
  name where the owner said it lives (the real Desktop, not cwd).
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from friday import contracts as c
from friday import literals as L
from friday import planner as P

# --------------------------------------------------------------------------
# literals: every kind the same planner defect could corrupt
# --------------------------------------------------------------------------


@pytest.mark.parametrize("text, kind, value", [
    ("Create jarvis-test.txt on my Desktop containing exactly the words: version one.", L.PATH, "jarvis-test.txt"),
    ("Create jarvis-test.txt on my Desktop containing exactly the words: version one.", L.CONTENT, "version one"),
    ("write 'hello, world. bye' to C:/Users/x/Desktop/note.md", L.CONTENT, "hello, world. bye"),
    ("write 'hello, world. bye' to C:/Users/x/Desktop/note.md", L.PATH, "C:/Users/x/Desktop/note.md"),
    ("open https://example.com/a.b?c=1 and read it", L.URL, "https://example.com/a.b?c=1"),
    ("email darsh@example.com the report", L.EMAIL, "darsh@example.com"),
    ("call +91 98765 43210 at 4.30 pm", L.PHONE, "+91 98765 43210"),
    ("call +91 98765 43210 at 4.30 pm", L.TIME, "4.30 pm"),
    ("push to branch release/1.2 tonight", L.BRANCH, "release/1.2"),
    ("use opus 4.8 for this", L.VERSION, "4.8"),
    (r"read C:\Users\x\Desktop\jarvis-test.txt now", L.PATH, r"C:\Users\x\Desktop\jarvis-test.txt"),
])
def test_every_literal_kind_is_found_verbatim(text, kind, value):
    assert L.first(L.find(text), kind) == value


def test_protect_and_restore_round_trip_exactly():
    text = ("email darsh@example.com the report at 4.30 pm and open https://example.com/a.b?c=1 "
            "then push to branch release/1.2 using opus 4.8 and write 'x. y' to a.txt")
    protected, mapping = L.protect(text)
    assert "." not in "".join(m for m in mapping)  # tokens carry no splitter punctuation
    assert L.restore(protected, mapping) == text


def test_a_filename_does_not_swallow_the_verb():
    """The first defect: the splitter saw the dot, and a greedy filename
    pattern with spaces would have read "Create jarvis-test.txt" as one
    filename."""
    lits = L.find("Create jarvis-test.txt on my Desktop")
    assert [(l.kind, l.value) for l in lits] == [(L.PATH, "jarvis-test.txt")]


def test_a_version_is_not_a_filename_and_a_host_is_not_a_file():
    lits = L.find("the version is 3.5 and the site is example.com")
    kinds = {l.kind for l in lits}
    assert L.PATH not in kinds
    assert L.VERSION in kinds and L.URL in kinds


def test_plain_speech_has_no_literals():
    assert L.find("check my computer and open Paint") == []


# --------------------------------------------------------------------------
# planner fidelity (A2)
# --------------------------------------------------------------------------

DESK = None


@pytest.fixture(autouse=True)
def fake_desktop(tmp_path, monkeypatch):
    """The owner's Desktop is a known folder the SHELL names; tests pin it.
    The files toolset caches its jail per process, so it is reset to the
    pinned root here and cleared afterwards."""
    global DESK
    DESK = tmp_path / "Desktop"
    DESK.mkdir()
    monkeypatch.setenv("ADA_KNOWN_FOLDER_DESKTOP", str(DESK))
    monkeypatch.setenv("ADA_FILE_ROOTS", str(DESK))
    from friday import known_folders
    from friday.toolsets import files
    known_folders.reset_cache()
    files.reset_jail(None)
    yield
    known_folders.reset_cache()
    files.reset_jail(None)


STEP3 = ("Create jarvis-test.txt on my Desktop containing exactly the words: version one. "
         "Then read the file back and tell me exactly what it contains.")
STEP5 = ("Overwrite jarvis-test.txt on my Desktop with the words: version two. "
         "Then undo that write by its action id and read the file back.")
STEP7 = ("Start an objective that waits for a file called jarvis-drop.txt to appear on my "
         "Desktop and then reads it and repeats its first line to me.")


def test_the_planner_keeps_the_spoken_filename_and_content():
    plan = P.resolve(P.interpret(STEP3))
    first = plan.goals[0]
    assert first.capability == "files_create"
    args = P.arguments_for(first)
    assert args["path"] == str(DESK / "jarvis-test.txt")
    assert args["content"] == "version one"
    assert Path(args["path"]).name == "jarvis-test.txt"   # not a derived friday-*.txt name
    assert "Created by Friday" not in args["content"]


def test_read_back_reads_the_file_that_was_written():
    plan = P.resolve(P.interpret(STEP3))
    reads = [g for g in plan.goals if g.capability == "files_read"]
    assert reads, [g.capability for g in plan.goals]
    for goal in reads:
        assert P.arguments_for(goal)["path"] == str(DESK / "jarvis-test.txt")
        assert goal.depends_on


def test_overwrite_then_undo_is_three_steps_not_four():
    """"undo that write by its action id" is ONE step: "that write" is a
    noun. It used to split into "undo that" + "write by its action id",
    and the second half became a CREATE of nothing."""
    plan = P.resolve(P.interpret(STEP5))
    assert [g.capability for g in plan.goals] == ["files_write", "files_undo", "files_read"]
    assert plan.unresolved == []
    assert P.arguments_for(plan.goals[0]) == {"path": str(DESK / "jarvis-test.txt"),
                                              "content": "version two"}


def test_wait_then_read_stays_on_the_file():
    """"then reads it" after a file wait is about the file - not, via
    example-phrasing inference, contract_pending_questions."""
    plan = P.resolve(P.interpret(STEP7))
    assert [g.capability for g in plan.goals] == ["files_wait", "files_read"]
    assert P.arguments_for(plan.goals[1])["path"] == str(DESK / "jarvis-drop.txt")


def test_a_bare_filename_is_placed_where_the_owner_said():
    plan = P.resolve(P.interpret("Create jarvis-test.txt on my Desktop containing exactly the words: hi"))
    assert P.arguments_for(plan.goals[0])["path"] == str(DESK / "jarvis-test.txt")


def test_an_absolute_path_is_left_exactly_as_spoken():
    target = DESK / "deep" / "note.txt"
    plan = P.resolve(P.interpret(f"Create {target} containing exactly the words: hi"))
    assert P.arguments_for(plan.goals[0])["path"] == str(target)


def test_a_request_with_no_spoken_file_still_gets_a_derived_note_name():
    """The old behaviour survives for the case it was written for."""
    plan = P.resolve(P.interpret("make a note about the meeting"))
    args = P.arguments_for(plan.goals[0])
    assert args.get("path", "").startswith("friday-")


# --------------------------------------------------------------------------
# direct action (A1)
# --------------------------------------------------------------------------


def test_direct_action_runs_the_exact_chain_and_the_file_is_on_disk(tmp_path, monkeypatch):
    from friday import action_evidence as AE
    from friday import direct_action as DA
    from friday.toolsets import files
    monkeypatch.setenv("FRIDAY_ACTION_JOURNAL", str(tmp_path / "journal.sqlite3"))
    files.reset_journal(None)
    AE.reset_ledger(AE.ActionEvidenceLedger(tmp_path / "ev.sqlite3"))

    done = DA.run(STEP3, turn_id="t1")
    assert done is not None and done.ok, [(s.capability, s.status, s.spoken) for s in done.steps]
    assert (DESK / "jarvis-test.txt").read_text(encoding="utf-8") == "version one"
    assert done.capabilities[0] == "files_create"
    assert "version one" in done.spoken()
    # one evidence row per step, each verified by the runtime's read-back
    rows = AE.ledger().for_turn("t1")
    assert [r.capability for r in rows] == list(done.capabilities)
    assert all(r.status == "succeeded" and r.verified for r in rows)


def test_direct_action_overwrite_undo_readback_restores_version_one(tmp_path, monkeypatch):
    from friday import action_evidence as AE
    from friday import direct_action as DA
    from friday.toolsets import files
    monkeypatch.setenv("FRIDAY_ACTION_JOURNAL", str(tmp_path / "journal.sqlite3"))
    files.reset_journal(None)
    AE.reset_ledger(AE.ActionEvidenceLedger(tmp_path / "ev.sqlite3"))

    assert DA.run(STEP3).ok
    done = DA.run(STEP5)
    assert done.ok, [(s.capability, s.status, s.spoken) for s in done.steps]
    assert [s.capability for s in done.steps] == ["files_write", "files_undo", "files_read"]
    assert (DESK / "jarvis-test.txt").read_text(encoding="utf-8") == "version one"
    assert "version one" in done.steps[-1].spoken


def test_direct_action_stops_at_the_first_failure_and_says_the_runtime_error(tmp_path, monkeypatch):
    """Negative control: the file already exists, so files_create must
    fail, the chain must stop, and the spoken line carries the error."""
    from friday import action_evidence as AE
    from friday import direct_action as DA
    from friday.toolsets import files
    monkeypatch.setenv("FRIDAY_ACTION_JOURNAL", str(tmp_path / "journal.sqlite3"))
    files.reset_journal(None)
    AE.reset_ledger(AE.ActionEvidenceLedger(tmp_path / "ev.sqlite3"))
    (DESK / "jarvis-test.txt").write_text("already here", encoding="utf-8")

    done = DA.run(STEP3, turn_id="t2")
    assert done is not None and not done.ok
    assert len(done.steps) == 1
    assert done.steps[0].status == c.FAILED
    assert "already exists" in done.steps[0].spoken
    rows = AE.ledger().for_turn("t2")
    assert len(rows) == 1 and rows[0].status == "failed" and not rows[0].verified


@pytest.mark.parametrize("text", [
    "check my computer and open Paint",
    "make a note about the meeting and read it back",       # derived name, not spoken
    "research the top three game engines and write a summary to notes.txt",  # research step
    "delete jarvis-test.txt on my Desktop but do not delete anything else",  # safety clause
])
def test_direct_action_declines_anything_that_is_not_fully_spoken_exact(text):
    from friday import direct_action as DA
    assert DA.plan_for(text) is None


def test_admission_declines_a_direct_file_chain(monkeypatch, caplog):
    """The objective engine no longer gets the request the planner corrupted."""
    import agent_friday as AF
    monkeypatch.setattr(AF, "COMPOUND_TASKS", 2)
    started = []
    from friday.toolsets import objectives as OT
    monkeypatch.setattr(OT, "objective_start", lambda run, text: started.append(text))
    with caplog.at_level("INFO", logger="friday-agent"):
        assert AF.admit_objective(STEP3) is None
    assert started == []
    assert "reason=direct_file_chain" in caplog.text
