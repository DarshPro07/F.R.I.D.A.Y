"""
Phase 1C: filesystem jail and files toolset.

Most of this file is escape attempts. The jail is the security boundary for
every file capability, so it gets tested adversarially rather than happily.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

from friday import contracts as c
from friday import policy as p
from friday.fsjail import FileJail, JailError
from friday.toolsets import files as F
from friday.toolsets.system import needs_approval

WINDOWS = sys.platform == "win32"


@pytest.fixture
def root(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    return workspace


@pytest.fixture
def jail(root):
    j = FileJail(roots=(root,))
    F.reset_jail(j)
    yield j
    F.reset_jail(None)


@pytest.fixture
def run():
    return c.Run.create("test", capability="files")


@pytest.fixture
def writer():
    """Engine with writes pre-approved, as a user would have granted."""
    engine = p.PolicyEngine()
    for tool in ("files.create", "files.write", "files.edit",
                 "files.copy", "files.move"):
        engine.approve_for_session(tool)
    return engine


# ---------------------------------------------------------------------------
# Jail: escape attempts
# ---------------------------------------------------------------------------


def test_parent_traversal_is_refused(jail, root):
    for attempt in ("../outside.txt", "../../outside.txt",
                    "subdir/../../outside.txt"):
        with pytest.raises(JailError, match="outside the permitted roots"):
            jail.resolve(str(root / attempt))


def test_absolute_path_outside_root_is_refused(jail):
    outside = "C:/Windows/System32/drivers/etc/hosts" if WINDOWS else "/etc/passwd"
    with pytest.raises(JailError, match="outside the permitted roots"):
        jail.resolve(outside)


def test_home_expansion_cannot_escape(jail):
    with pytest.raises(JailError):
        jail.resolve("~/.ssh/id_rsa")


def test_symlink_pointing_outside_is_refused(jail, root, tmp_path):
    """
    The reason resolve() runs before the containment check: the literal path
    is inside the root, but what it points at is not.
    """
    secret = tmp_path / "outside_secret.txt"
    secret.write_text("classified")
    link = root / "innocent.txt"
    try:
        os.symlink(secret, link)
    except (OSError, NotImplementedError):
        pytest.skip("symlink creation not permitted on this machine")

    assert link.exists()  # the link itself is inside the root
    with pytest.raises(JailError, match="outside the permitted roots"):
        jail.resolve(str(link))


def test_denylisted_files_are_refused_even_inside_the_root(jail, root):
    """Reads are AUTO inside roots, so this is the SECRET_READ boundary."""
    for name in (".env", ".env.local", "id_rsa", "server.pem", "credentials",
                 "secrets.json", "keystore.jks"):
        (root / name).write_text("sensitive")
        with pytest.raises(JailError, match="protected pattern"):
            jail.resolve(str(root / name))


def test_git_and_ssh_directories_are_refused(jail, root):
    for relative in (".git/config", ".ssh/known_hosts", ".aws/credentials"):
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("x")
        with pytest.raises(JailError, match="protected pattern"):
            jail.resolve(str(target))


@pytest.mark.skipif(not WINDOWS, reason="Windows path semantics")
def test_alternate_data_stream_is_refused(jail, root):
    with pytest.raises(JailError, match="alternate data stream"):
        jail.resolve(str(root / "notes.txt") + ":hidden")


@pytest.mark.skipif(not WINDOWS, reason="Windows device names")
def test_reserved_device_names_are_refused(jail, root):
    for name in ("CON", "nul", "COM1", "lpt9.txt"):
        with pytest.raises(JailError, match="reserved device name"):
            jail.resolve(str(root / name))


@pytest.mark.skipif(not WINDOWS, reason="UNC paths")
def test_unc_paths_are_refused(jail):
    with pytest.raises(JailError, match="UNC"):
        jail.resolve(r"\\server\share\file.txt")


def test_empty_and_null_paths_refused(jail):
    with pytest.raises(JailError, match="empty path"):
        jail.resolve("   ")
    with pytest.raises(JailError, match="null byte"):
        jail.resolve("file\x00.txt")


def test_legitimate_paths_are_allowed(jail, root):
    (root / "notes.txt").write_text("hello")
    assert jail.resolve(str(root / "notes.txt")).name == "notes.txt"
    nested = root / "a" / "b"
    nested.mkdir(parents=True)
    assert jail.resolve(str(nested / "new.txt")).name == "new.txt"  # need not exist


def test_default_root_is_not_the_project_or_home():
    """A default that included the project would expose .env to AUTO reads."""
    from friday.fsjail import DEFAULT_WORKSPACE

    resolved = Path(DEFAULT_WORKSPACE).resolve()
    assert resolved != Path.cwd()
    assert resolved != Path.home()
    assert "workspace" in str(resolved).lower()


# ---------------------------------------------------------------------------
# Reads
# ---------------------------------------------------------------------------


def test_read_returns_content_with_hash_evidence(jail, root, run):
    (root / "note.txt").write_text("hello jarvis", encoding="utf-8")
    result = F.files_read(run, str(root / "note.txt"))
    assert result.status == c.SUCCEEDED
    assert result.output["text"] == "hello jarvis"
    assert "sha256:" in result.verification.evidence


def test_read_missing_file_fails(jail, root, run):
    result = F.files_read(run, str(root / "nope.txt"))
    assert result.status == c.FAILED
    assert not result.may_claim_completion


def test_read_refuses_escape(jail, root, run):
    result = F.files_read(run, str(root / ".." / "outside.txt"))
    assert result.status == c.FAILED
    assert "path refused" in result.error


def test_read_binary_is_partial_not_success(jail, root, run):
    (root / "blob.bin").write_bytes(b"\xff\xfe\x00\x01binary")
    result = F.files_read(run, str(root / "blob.bin"))
    assert result.status == c.PARTIAL
    assert not result.may_claim_completion


def test_list_and_info(jail, root, run):
    (root / "a.txt").write_text("a")
    (root / "sub").mkdir()
    listing = F.files_list(run, str(root))
    assert listing.status == c.SUCCEEDED
    assert {e["name"] for e in listing.output["entries"]} == {"a.txt", "sub"}

    info = F.files_info(run, str(root / "a.txt"))
    assert info.status == c.SUCCEEDED
    assert info.output["size_bytes"] == 1
    assert info.output["is_dir"] is False


def test_search_by_glob_and_content(jail, root, run):
    (root / "one.txt").write_text("alpha beta")
    (root / "two.txt").write_text("gamma delta")
    (root / "three.md").write_text("alpha only")

    globbed = F.files_search(run, "*.txt", root=str(root))
    assert {Path(h["path"]).name for h in globbed.output["results"]} == {"one.txt", "two.txt"}

    filtered = F.files_search(run, "*", root=str(root), contains="alpha")
    assert {Path(h["path"]).name for h in filtered.output["results"]} == {"one.txt", "three.md"}


def test_a_search_that_finds_everything_says_it_was_complete(jail, root, run):
    (root / "one.txt").write_text("alpha")
    result = F.files_search(run, "*", root=str(root))
    assert result.output["complete"] is True
    assert result.output["stopped_at"] == ""
    assert "whole tree searched" in result.verification.evidence


def test_a_search_stops_on_a_budget_and_admits_it(jail, root, run, monkeypatch):
    """
    Measured: "search my project for the word reactor" ran for **300.6
    seconds** - the MCP session ceiling - because MAX_SEARCH_HITS caps hits
    and `contains` means almost nothing becomes a hit. It held the server
    long enough that the next agent session's handshake timed out, so one
    slow tool took the process down with it.

    A truncated search that reads like a complete one is the worse half of
    that bug: "no matches" and "no matches yet" are different answers.
    """
    for index in range(50):
        (root / f"file{index}.txt").write_text("nothing of interest")
    monkeypatch.setattr(F, "SEARCH_SECONDS", 0.0)

    result = F.files_search(run, "*", root=str(root), contains="reactor")
    assert result.status == c.SUCCEEDED, "a budget is not a failure"
    assert result.output["complete"] is False
    assert "0 seconds" in result.output["stopped_at"]
    assert "there may be more" in result.verification.evidence


def test_the_hit_cap_is_reported_the_same_way(jail, root, run, monkeypatch):
    monkeypatch.setattr(F, "MAX_SEARCH_HITS", 3)
    for index in range(10):
        (root / f"file{index}.txt").write_text("x")
    result = F.files_search(run, "*", root=str(root))
    assert len(result.output["results"]) == 3
    assert result.output["complete"] is False
    assert "3 matches" in result.output["stopped_at"]


def test_the_virtualenv_is_not_the_project(jail, root, run):
    """
    Where the three hundred seconds went. `.venv` alone is tens of thousands
    of files, and it is never what he means by "search my project".
    """
    (root / "mine.py").write_text("reactor")
    noise = root / ".venv" / "Lib" / "site-packages"
    noise.mkdir(parents=True)
    (noise / "theirs.py").write_text("reactor")

    result = F.files_search(run, "*.py", root=str(root), contains="reactor")
    names = {Path(h["path"]).name for h in result.output["results"]}
    assert names == {"mine.py"}, names
    assert result.output["complete"] is True


def test_search_excludes_protected_files_from_results(jail, root, run):
    (root / "ok.txt").write_text("fine")
    (root / ".env").write_text("SECRET=x")
    result = F.files_search(run, "*", root=str(root))
    names = {Path(h["path"]).name for h in result.output["results"]}
    assert ".env" not in names
    assert result.output["skipped_protected"] >= 1
    assert "protected" in result.verification.evidence


# ---------------------------------------------------------------------------
# Writes: gating and read-back verification
# ---------------------------------------------------------------------------


def test_writes_are_ask_gated(jail, root, run):
    engine = p.PolicyEngine(autonomy=p.GUARDED)
    for result in (
        F.files_create(run, str(root / "x.txt"), "x", engine=engine),
        F.files_write(run, str(root / "x.txt"), "x", engine=engine),
        F.files_edit(run, str(root / "x.txt"), "a", "b", engine=engine),
        F.files_copy(run, str(root / "x.txt"), str(root / "y.txt"), engine=engine),
        F.files_move(run, str(root / "x.txt"), str(root / "z.txt"), engine=engine),
    ):
        assert needs_approval(result), result.tool_id
        assert not result.may_claim_completion


def test_create_writes_and_verifies_by_readback(jail, root, run, writer):
    target = root / "made.txt"
    result = F.files_create(run, str(target), "content here", engine=writer)
    assert result.status == c.SUCCEEDED
    assert target.read_text() == "content here"
    assert "read back identically" in result.verification.evidence


def test_create_produces_an_artifact(jail, root, run, writer):
    result = F.files_create(run, str(root / "made.txt"), "x", engine=writer)
    assert len(result.artifacts) == 1
    artifact = result.artifacts[0]
    assert artifact.type == "file"
    assert Path(artifact.path_or_uri).exists()
    assert artifact.run_id == run.run_id


def test_create_refuses_to_overwrite(jail, root, run, writer):
    (root / "exists.txt").write_text("original")
    result = F.files_create(run, str(root / "exists.txt"), "new", engine=writer)
    assert result.status == c.FAILED
    assert "already exists" in result.error
    assert (root / "exists.txt").read_text() == "original"


def test_write_replaces_and_flags_overwrite(jail, root, run, writer):
    (root / "f.txt").write_text("old")
    result = F.files_write(run, str(root / "f.txt"), "new", engine=writer)
    assert result.status == c.SUCCEEDED
    assert result.output["overwrote_existing"] is True
    assert (root / "f.txt").read_text() == "new"


def test_edit_replaces_a_unique_snippet(jail, root, run, writer):
    (root / "cfg.txt").write_text("host = localhost\nport = 8000\n")
    result = F.files_edit(run, str(root / "cfg.txt"), "port = 8000", "port = 9000",
                          engine=writer)
    assert result.status == c.SUCCEEDED
    assert "port = 9000" in (root / "cfg.txt").read_text()


def test_edit_refuses_ambiguous_match(jail, root, run, writer):
    (root / "dup.txt").write_text("x = 1\nx = 1\n")
    result = F.files_edit(run, str(root / "dup.txt"), "x = 1", "x = 2", engine=writer)
    assert result.status == c.FAILED
    assert "appears 2 times" in result.error
    assert (root / "dup.txt").read_text() == "x = 1\nx = 1\n"  # untouched


def test_edit_refuses_when_text_absent(jail, root, run, writer):
    (root / "f.txt").write_text("hello")
    result = F.files_edit(run, str(root / "f.txt"), "goodbye", "hi", engine=writer)
    assert result.status == c.FAILED
    assert "not found" in result.error


def test_copy_verifies_destination_exists(jail, root, run, writer):
    (root / "src.txt").write_text("payload")
    result = F.files_copy(run, str(root / "src.txt"), str(root / "dst.txt"),
                          engine=writer)
    assert result.status == c.SUCCEEDED
    assert (root / "dst.txt").read_text() == "payload"
    assert (root / "src.txt").exists()  # copy leaves the source


def test_move_verifies_source_is_gone(jail, root, run, writer):
    (root / "src.txt").write_text("payload")
    result = F.files_move(run, str(root / "src.txt"), str(root / "moved.txt"),
                          engine=writer)
    assert result.status == c.SUCCEEDED
    assert (root / "moved.txt").read_text() == "payload"
    assert not (root / "src.txt").exists()
    assert "source no longer exists" in result.verification.evidence


def test_transfer_refuses_existing_destination(jail, root, run, writer):
    (root / "a.txt").write_text("a")
    (root / "b.txt").write_text("b")
    result = F.files_copy(run, str(root / "a.txt"), str(root / "b.txt"), engine=writer)
    assert result.status == c.FAILED
    assert (root / "b.txt").read_text() == "b"


def test_transfer_refuses_destination_outside_the_jail(jail, root, run, writer, tmp_path):
    (root / "a.txt").write_text("a")
    result = F.files_copy(run, str(root / "a.txt"), str(tmp_path / "escaped.txt"),
                          engine=writer)
    assert result.status == c.FAILED
    assert "path refused" in result.error
    assert not (tmp_path / "escaped.txt").exists()


def test_every_file_result_declares_local_machine_scope(jail, root, run, writer):
    (root / "f.txt").write_text("x")
    for result in (
        F.files_read(run, str(root / "f.txt")),
        F.files_info(run, str(root / "f.txt")),
        F.files_list(run, str(root)),
        F.files_write(run, str(root / "f.txt"), "y", engine=writer),
    ):
        assert result.output["execution_scope"] == "local_machine"
