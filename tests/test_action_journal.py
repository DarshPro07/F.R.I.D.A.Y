"""ML-09/10: the action journal, and "undo that" as a verified operation.

Every mutating file op captures the prior state before touching disk and
journals afterwards; `files_undo` puts it back only when the target is
still exactly as Friday left it, and reports REVERSED only after reading
the restored state back. Each principle in `friday/action_journal.py`'s
docstring is a test here, plus the negative controls: a conflict is not
overwritten, an unknown before-state is IRREVERSIBLE and says why, a jail
escape in a journal row is refused before anything runs.

Fixtures live inside Friday's real jail (`fsjail.DEFAULT_WORKSPACE`) for the
reason `test_files_recycle.py` gives: a stubbed jail proves nothing about
the jail. Every file here is created by the test and removed by it.
"""
from __future__ import annotations

import shutil
import uuid
from pathlib import Path

import pytest

from friday import action_journal as aj
from friday import contracts as c
from friday.policy import PolicyEngine
from friday.toolsets import files as F


@pytest.fixture
def workspace():
    from friday import fsjail
    root = fsjail.DEFAULT_WORKSPACE / f"journal-gate-{uuid.uuid4().hex[:8]}"
    root.mkdir(parents=True, exist_ok=False)
    try:
        yield root
    finally:
        shutil.rmtree(root, ignore_errors=True)


@pytest.fixture
def journal(tmp_path):
    """A journal of the test's own, swapped into the toolset for the test."""
    j = aj.ActionJournal(tmp_path / "journal.sqlite3", backups=tmp_path / "backups")
    F.reset_journal(j)
    try:
        yield j
    finally:
        F.reset_journal(None)


@pytest.fixture
def engine():
    """A policy engine with the file writes pre-approved: this file tests the
    journal, not the ASK gate (test_files does that)."""
    e = PolicyEngine()
    for tool in ("files.write", "files.create", "files.edit", "files.move", "files.undo",
                 "files.actions", "files.recycle"):
        e.approve_for_session(tool)
    return e


def _run(label="journal it") -> c.Run:
    return c.Run.create(label, capability="files")


# ---------------------------------------------------------------------------
# the journal on its own
# ---------------------------------------------------------------------------

class TestJournal:
    def test_a_write_over_an_existing_file_is_reversible_and_verified(self, journal, tmp_path):
        target = tmp_path / "note.txt"
        target.write_text("first\n")
        before, backup, reversible, reason = journal.before_file_write(target)
        target.write_text("second\n")
        e = journal.record_file_write(target, before, backup, reversible, reason, run_id="r1")
        assert e.reversible and e.state == aj.RECORDED
        r = journal.reverse(e.action_id)
        assert r.ok, r.evidence
        assert target.read_text() == "first\n"
        assert journal.get(e.action_id).state == aj.REVERSED
        assert "read back" in r.evidence

    def test_a_created_file_is_undone_by_removal(self, journal, tmp_path):
        target = tmp_path / "new.txt"
        captured = journal.before_file_write(target)
        assert captured[0] == {"exists": False} and captured[2] is True
        target.write_text("hello")
        e = journal.record_file_write(target, *captured, operation=aj.FILE_CREATE)
        r = journal.reverse(e.action_id)
        assert r.ok and not target.exists()

    def test_before_content_lives_in_the_backup_store_not_the_row(self, journal, tmp_path):
        target = tmp_path / "big.txt"
        target.write_text("x" * 5000)
        before, backup, *_ = journal.before_file_write(target)
        e = journal.record_file_write(target, before, backup, True, "")
        assert "x" * 100 not in (str(e.before) + str(e.after))
        assert Path(backup).exists() and Path(backup).stat().st_size == 5000
        assert Path(backup).parent == journal.backups

    def test_above_the_cap_the_write_is_journaled_irreversible_and_says_why(self, journal, tmp_path, monkeypatch):
        monkeypatch.setattr(aj, "MAX_BACKUP_BYTES", 10)
        target = tmp_path / "video.bin"
        target.write_bytes(b"0" * 100)
        before, backup, reversible, reason = journal.before_file_write(target)
        assert reversible is False and "backup cap" in reason and backup == ""
        assert "sha256" not in before, "an unhashable state must not pretend to a hash"
        e = journal.record_file_write(target, before, backup, reversible, reason)
        assert e.state == aj.IRREVERSIBLE
        assert journal.reverse(e.action_id).state == aj.IRREVERSIBLE
        assert journal.last_reversible() is None

    def test_a_conflict_is_refused_and_nothing_is_touched(self, journal, tmp_path):
        """Someone edited the file after Friday did. Restoring Friday's
        earlier state would destroy their later work; that is not an undo."""
        target = tmp_path / "shared.txt"
        target.write_text("original")
        captured = journal.before_file_write(target)
        target.write_text("friday's version")
        e = journal.record_file_write(target, *captured)
        target.write_text("the user's later edit")
        r = journal.reverse(e.action_id)
        assert r.state == aj.CONFLICT and not r.ok
        assert target.read_text() == "the user's later edit"
        assert "not as Friday left it" in r.evidence
        assert journal.get(e.action_id).state == aj.CONFLICT

    def test_a_move_is_reversed_to_its_source_and_refused_if_the_source_reappeared(self, journal, tmp_path):
        src, dst = tmp_path / "a" / "f.txt", tmp_path / "b" / "f.txt"
        src.parent.mkdir(); src.write_text("moved me")
        before_source = aj._file_state(src)
        dst.parent.mkdir(); shutil.move(str(src), str(dst))
        e = journal.record_file_move(src, dst, before_source=before_source, run_id="r2")
        assert e.reversible and e.before["source"] == str(src)
        # source reappears: the undo would overwrite it - refused
        src.write_text("something new here")
        r = journal.reverse(e.action_id)
        assert r.state == aj.CONFLICT and dst.exists() and src.read_text() == "something new here"
        # entry is now CONFLICT; a fresh move reverses cleanly
        src.unlink()
        src2, dst2 = tmp_path / "a" / "g.txt", tmp_path / "b" / "g.txt"
        src2.write_text("second"); bs = aj._file_state(src2); shutil.move(str(src2), str(dst2))
        e2 = journal.record_file_move(src2, dst2, before_source=bs)
        r2 = journal.reverse(e2.action_id)
        assert r2.ok and src2.read_text() == "second" and not dst2.exists()

    def test_reversal_failure_is_reported_when_read_back_does_not_match(self, journal, tmp_path, monkeypatch):
        """The read-back is the verification. Make the restore silently
        write the wrong bytes and the entry must NOT say REVERSED."""
        target = tmp_path / "t.txt"
        target.write_text("before")
        captured = journal.before_file_write(target)
        target.write_text("after")
        e = journal.record_file_write(target, *captured)
        real_copy = shutil.copyfile

        def wrong_copy(src, dst, *a, **k):
            Path(dst).write_text("corrupted restore")
            return dst
        monkeypatch.setattr(aj.shutil, "copyfile", wrong_copy)
        r = journal.reverse(e.action_id)
        monkeypatch.setattr(aj.shutil, "copyfile", real_copy)
        assert r.state == aj.REVERSAL_FAILED and not r.ok
        assert "read-back" in r.evidence

    def test_an_already_reversed_entry_is_not_reversed_twice(self, journal, tmp_path):
        target = tmp_path / "once.txt"
        target.write_text("v1"); cap = journal.before_file_write(target); target.write_text("v2")
        e = journal.record_file_write(target, *cap)
        assert journal.reverse(e.action_id).ok
        target.write_text("v3")
        r = journal.reverse(e.action_id)
        assert r.state == aj.REVERSED and not r.ok and "already" in r.evidence
        assert target.read_text() == "v3"

    def test_a_group_rolls_back_newest_first_and_stops_at_the_first_conflict(self, journal, tmp_path):
        a, b, cc = (tmp_path / n for n in ("a.txt", "b.txt", "c.txt"))
        for p in (a, b, cc):
            p.write_text("old " + p.name)
        entries = []
        for p in (a, b, cc):
            cap = journal.before_file_write(p); p.write_text("new " + p.name)
            entries.append(journal.record_file_write(p, *cap, run_id="grp", group_id="grp"))
        b.write_text("user touched b")          # b conflicts
        results = journal.reverse_group("grp")
        assert [r.action_id for r in results] == [entries[2].action_id, entries[1].action_id]
        assert results[0].ok and results[1].state == aj.CONFLICT
        assert cc.read_text() == "old c.txt" and b.read_text() == "user touched b"
        assert a.read_text() == "new a.txt", "stopped at the conflict; a untouched and still RECORDED"
        assert journal.get(entries[0].action_id).state == aj.RECORDED

    def test_last_reversible_skips_irreversible_and_settled_entries(self, journal, tmp_path):
        p = tmp_path / "p.txt"; p.write_text("1"); cap = journal.before_file_write(p); p.write_text("2")
        e1 = journal.record_file_write(p, *cap)
        e2 = journal.record(capability="files", operation="file.write", target=str(p), before={}, after={},
                            reversible=False, reason="unknown before-state")
        assert e2.state == aj.IRREVERSIBLE
        assert journal.last_reversible().action_id == e1.action_id

    def test_the_journal_survives_reopening(self, tmp_path):
        j = aj.ActionJournal(tmp_path / "j.sqlite3", backups=tmp_path / "b")
        p = tmp_path / "d.txt"; p.write_text("x"); cap = j.before_file_write(p); p.write_text("y")
        e = j.record_file_write(p, *cap, run_id="R")
        j2 = aj.ActionJournal(tmp_path / "j.sqlite3", backups=tmp_path / "b")
        got = j2.get(e.action_id)
        assert got is not None and got.run_id == "R" and got.backup_path == e.backup_path
        assert j2.reverse(e.action_id).ok and p.read_text() == "x"


# ---------------------------------------------------------------------------
# wired into the file toolset
# ---------------------------------------------------------------------------

@pytest.mark.skipif(__import__("sys").platform != "win32", reason="jail roots are per-platform")
class TestFilesToolset:
    def test_files_write_journals_and_files_undo_restores(self, workspace, journal, engine):
        target = workspace / "memo.txt"
        target.write_text("draft one\n", encoding="utf-8")
        res = F.files_write(_run(), str(target), "draft two\n", engine=engine)
        assert res.status == c.SUCCEEDED, res.error
        assert res.output["action_id"] and res.output["reversible"] is True
        assert target.read_text(encoding="utf-8") == "draft two\n"

        listed = F.files_actions(_run(), engine=engine)
        assert listed.status == c.SUCCEEDED
        assert listed.output["undoable"] == 1
        assert listed.output["actions"][0]["action_id"] == res.output["action_id"]

        undone = F.files_undo(_run(), engine=engine)          # no id: the last reversible
        assert undone.status == c.SUCCEEDED, undone.error
        assert undone.output["state"] == aj.REVERSED
        assert undone.verification.method == "state_read_back_equals_before"
        assert target.read_text(encoding="utf-8") == "draft one\n"

    def test_files_create_is_undone_by_removal(self, workspace, journal, engine):
        target = workspace / "fresh.txt"
        res = F.files_create(_run(), str(target), "hello", engine=engine)
        assert res.status == c.SUCCEEDED, res.error
        assert target.exists()
        undone = F.files_undo(_run(), res.output["action_id"], engine=engine)
        assert undone.status == c.SUCCEEDED, undone.error
        assert not target.exists()

    def test_files_move_is_undone_back_to_the_source(self, workspace, journal, engine):
        src, dst = workspace / "src.txt", workspace / "sub" / "dst.txt"
        src.write_text("carry me", encoding="utf-8")
        res = F.files_move(_run(), str(src), str(dst), engine=engine)
        assert res.status == c.SUCCEEDED, res.error
        assert res.output["reversible"] is True and dst.exists() and not src.exists()
        undone = F.files_undo(_run(), res.output["action_id"], engine=engine)
        assert undone.status == c.SUCCEEDED, undone.error
        assert src.read_text(encoding="utf-8") == "carry me" and not dst.exists()

    def test_undo_refuses_a_conflict_through_the_toolset(self, workspace, journal, engine):
        target = workspace / "c.txt"
        target.write_text("A", encoding="utf-8")
        res = F.files_write(_run(), str(target), "B", engine=engine)
        target.write_text("C - the user", encoding="utf-8")
        undone = F.files_undo(_run(), res.output["action_id"], engine=engine)
        assert undone.status == c.FAILED
        assert "not undone" in undone.error and undone.output["state"] == aj.CONFLICT
        assert target.read_text(encoding="utf-8") == "C - the user"

    def test_undo_re_resolves_the_journaled_path_through_the_jail(self, workspace, journal, engine, tmp_path):
        """A journal row naming a path outside the jail must be refused
        BEFORE the journal is consulted - the jail is the boundary for undo
        too. The row is planted directly, as a tampered database would."""
        outside = tmp_path / "outside.txt"
        outside.write_text("do not touch")
        e = journal.record(capability="files", operation=aj.FILE_WRITE, target=str(outside),
                           before={"exists": False}, after=aj._file_state(outside), reversible=True)
        undone = F.files_undo(_run(), e.action_id, engine=engine)
        assert undone.status == c.FAILED and "path refused" in undone.error
        assert outside.read_text() == "do not touch"
        assert journal.get(e.action_id).state == aj.RECORDED, "never reached the journal"

    def test_a_journal_failure_never_fails_the_write(self, workspace, engine, monkeypatch):
        """The write happened and was read back; a bookkeeping error must
        not report it as failed (that would be a false failure)."""
        class Broken:
            def before_file_write(self, *_a, **_k):
                raise RuntimeError("journal disk gone")
        F.reset_journal(Broken())
        try:
            target = workspace / "still-written.txt"
            res = F.files_write(_run(), str(target), "content", engine=engine)
        finally:
            F.reset_journal(None)
        assert res.status == c.SUCCEEDED, res.error
        assert res.output["action_id"] == "" and res.output["reversible"] is False
        assert target.read_text(encoding="utf-8") == "content"

    def test_undo_with_an_empty_journal_says_so(self, journal, engine):
        res = F.files_undo(_run(), engine=engine)
        assert res.status == c.FAILED and "nothing" in res.error

    def test_undo_is_gated_like_a_write(self, journal):
        """files.undo shares FILE_WRITE's policy category, so whatever the
        owner decides for writes governs undo too. Deny the category and the
        call is cancelled at the gate before the journal is even consulted."""
        class Untouchable:
            def get(self, *_a, **_k):
                raise AssertionError("gate must run before any journal read")
            last_reversible = get
        F.reset_journal(Untouchable())
        try:
            res = F.files_undo(_run(), engine=PolicyEngine({"FILE_WRITE": "DENY"}))
        finally:
            F.reset_journal(None)
        assert res.status == c.CANCELLED
        assert PolicyEngine().category_of("files.undo") == PolicyEngine().category_of("files.write")
