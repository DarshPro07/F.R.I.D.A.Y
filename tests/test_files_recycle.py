"""
Deleting a file the way a person means it.

`files.py` said for a long time that there was "deliberately no delete tool",
and while the only thing on offer was `os.remove` that was the right call. A
capability that destroys a file permanently, named after the word people use
when they expect the Recycle Bin, is a trap with a friendly label.

So this is a recycle and it is called one. The distinction it protects:

    recycle      the boss can change their mind
    delete       the boss cannot

Fixtures here are created by the test and belong to it. No test in this file
touches a file it did not write - a rule this project learned the hard way,
when a live gate moved and minimised a window holding somebody else's work.
"""
from __future__ import annotations
import sys
import pytest
from friday import contracts as c
from friday.toolsets import files as F
pytestmark = pytest.mark.skipif(sys.platform != 'win32', reason='Recycle Bin semantics are per-platform')


@pytest.fixture
def workspace():
    """
    A directory this test creates inside Friday's real jail, and removes.

    Deliberately not `tmp_path` with the jail patched to allow it. The first
    version did that and the patch did not take, which was lucky: it would
    have exercised a jail this test had invented rather than the one that
    protects the boss's files. A recycle test whose jail is a stub proves
    nothing about the jail.
    """
    import shutil
    import uuid
    from friday import fsjail
    root = fsjail.DEFAULT_WORKSPACE / f"recycle-gate-{uuid.uuid4().hex[:8]}"
    root.mkdir(parents=True, exist_ok=False)
    try:
        yield root
    finally:
        shutil.rmtree(root, ignore_errors=True)


def _run(label: str = "recycle it") -> c.Run:
    return c.Run.create(label, capability="files")


def test_a_file_this_test_created_goes_to_the_recycle_bin(workspace):
    target = workspace / "recycle-me.txt"
    target.write_text("friday recycle gate, safe to delete\n", encoding="utf-8")
    assert target.exists()

    result = F.files_recycle(_run(), str(target))

    assert result.status == c.SUCCEEDED, result.error
    assert not target.exists(), "the file is still on disk"
    assert result.output["recycled"] is True
    assert result.output["restorable"] is True
    assert "Recycle Bin" in result.verification.evidence


def test_the_evidence_is_the_path_being_gone_not_the_call_returning(workspace):
    """
    `send2trash` returning is not the file being gone, for exactly the reason
    `TerminateProcess` returning is not the process being gone. This module
    has been bitten by that shape before, so the check is a read-back.
    """
    target = workspace / "read-back.txt"
    target.write_text("x", encoding="utf-8")

    result = F.files_recycle(_run(), str(target))

    assert result.verification.method == "path_absent_after_recycle"
    assert str(target) in result.verification.evidence or \
        target.name in result.verification.evidence


def test_a_file_that_is_not_there_is_not_a_success(workspace):
    result = F.files_recycle(_run(), str(workspace / "never-existed.txt"))
    assert result.status == c.FAILED
    assert "no such file" in result.error


def test_a_directory_is_refused_rather_than_emptied(workspace):
    folder = workspace / "a-folder"
    folder.mkdir()
    (folder / "inside.txt").write_text("still here", encoding="utf-8")

    result = F.files_recycle(_run(), str(folder))

    assert result.status == c.FAILED
    assert "directory" in result.error
    assert (folder / "inside.txt").exists(), "it recycled the contents anyway"


def test_a_path_outside_the_jail_is_refused(tmp_path):
    """
    The jail is the point. A recycle that escapes it is a delete of somebody
    else's file with a gentler name.

    `tmp_path` is under the system temp directory, which is not one of the
    permitted roots - so this is a real refusal by the real jail, not a
    configured one.
    """
    outside = tmp_path / 'not-ours.txt'
    outside.write_text('belongs to someone else', encoding='utf-8')
    result = F.files_recycle(_run(), str(outside))
    assert result.status == c.FAILED
    assert 'outside the permitted roots' in result.error
    assert outside.exists(), 'a file outside the workspace was recycled'


def test_it_is_reachable_from_a_durable_objective():
    """
    An objective that is asked to clean up after itself must be able to.
    Before this existed, "create and clean up a temporary note" compiled the
    cleanup step to UNMAPPED and failed it immediately.
    """
    from friday import capability_runtime as R

    assert "files_recycle" in R.reachable()


def test_permanent_deletion_is_not_hiding_behind_this_name():
    """
    §9. Recycling is reversible and sits with the other file writes. If an
    irreversible delete is ever added it does not get to share this door -
    hiding permanence behind a word people use for the Recycle Bin is how
    somebody loses work they believed was recoverable.
    """
    import ast
    import inspect
    from friday import policy as p
    assert p.TOOL_CATEGORIES['files.recycle'] == p.FILE_WRITE
    tree = ast.parse(inspect.getsource(F.files_recycle))
    called = {node.func.attr for node in ast.walk(tree) if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)}
    assert 'unlink' not in called and 'remove' not in called, 'files_recycle deletes permanently'
    names = {node.func.id for node in ast.walk(tree) if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)}
    assert 'send2trash' in names, 'it is not going through the Recycle Bin'
