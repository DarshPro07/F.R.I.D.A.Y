"""
SEC-005 (PRD v7 section 43): the filesystem boundary of a Hermes delegation,
held below Hermes (owner ruling 2026-10-03).

The exec bridge is spawned once per Hermes profile with no session id, so a
delegation's roots reach it through the registry `HermesBridge.delegate` writes
(`friday.delegation_scope`), and every file call the bridge serves is judged by
FRIDAY's jail and then by the delegation's read or write roots. Each case here
is a refusal, or an allowed access, that the bridge could not give before: its
profile workspace is deliberately NOT the delegation's workspace.
"""
from __future__ import annotations

import json
import logging
import os
import re
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from friday import appcontainer
from friday import contracts as c
from friday import delegation_scope as S
from friday import hermes_bridge as hb
from friday import quarantine, scoped_runner
from friday.fsjail import FileJail, JailError
from friday.toolsets import files as F
from friday.tools import execution_bridge as bridge

FAKE = str(Path(__file__).parent / "fake_hermes_gateway.py")
WINDOWS = os.name == "nt"
windows_only = pytest.mark.skipif(not WINDOWS, reason="Windows path semantics")


@dataclass
class Area:
    root: Path
    workspace: Path      # the delegation's workspace and write root
    readable: Path       # a read-only root
    elsewhere: Path      # outside the envelope, inside FRIDAY's jail
    profile: Path        # the bridge's own profile workspace


def _stamp(seconds: float) -> str:
    return (datetime.now(timezone.utc) + timedelta(seconds=seconds)).isoformat()


def _envelope(area: Area, *, seconds=3600.0, read=None, write=None) -> c.Envelope:
    return c.Envelope(
        run_id="OBJ-S", objective_id="OBJ-S", provenance=c.PERSON, attended=True,
        cwd=str(area.workspace),
        read_roots=tuple(map(str, [area.readable] if read is None else read)),
        write_roots=tuple(map(str, [area.workspace] if write is None else write)),
        allowed_capabilities=("hermes_delegate",), expires_at=_stamp(seconds))


@pytest.fixture(scope="session")
def sandbox_runtime(tmp_path_factory):
    """The interpreter copy a sandbox reads is built once per session."""
    return tmp_path_factory.mktemp("scoped-runtime") / "_runtime"


@pytest.fixture
def area(tmp_path, monkeypatch, sandbox_runtime):
    here = Area(tmp_path, tmp_path / "ws", tmp_path / "readable",
                tmp_path / "elsewhere", tmp_path / "profile")
    for folder in (here.workspace, here.readable, here.elsewhere, here.profile):
        folder.mkdir()
    (here.workspace / "mine.txt").write_text("in the workspace", encoding="utf-8")
    (here.readable / "notes.txt").write_text("readable only", encoding="utf-8")
    (here.elsewhere / "other.txt").write_text("outside the envelope", encoding="utf-8")
    (here.profile / "profile.txt").write_text("the profile workspace", encoding="utf-8")
    F.reset_jail(FileJail(roots=(tmp_path,)))
    monkeypatch.setenv("FRIDAY_BRIDGE_WORKSPACE", str(here.profile))
    monkeypatch.setattr(bridge, "_env", None)
    # raising=False: these tests also run against the bridge from before its
    # sandbox existed, where only the behaviour under test may fail.
    monkeypatch.setattr(bridge, "_scoped", None, raising=False)
    monkeypatch.setattr(quarantine, "default_runtime_dir", lambda: sandbox_runtime)
    # A delegation under an envelope runs on a gateway started with the
    # profile's toolsets, so the profile has to name some.
    _profile(tmp_path, monkeypatch, "toolsets: [web]\n")
    yield here
    getattr(bridge, "close_scoped_runner", lambda: None)()
    F.reset_jail(None)
    if bridge._env is not None:
        bridge._env.terminate()
    bridge._env = None


@pytest.fixture
def held(area):
    S.register("lease-1", _envelope(area), str(area.workspace))
    yield area
    S.release("lease-1")


def _link(link: Path, target: Path) -> None:
    """A junction on Windows (no privilege needed), else a symlink."""
    if WINDOWS:
        done = subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(target)],
                              capture_output=True, text=True)
        if done.returncode:
            pytest.skip(f"cannot make a junction here: {done.stdout}{done.stderr}")
    else:
        os.symlink(target, link, target_is_directory=True)


def _refused(text: str) -> bool:
    return text.startswith(bridge.REFUSED)


# -- ordinary paths ---------------------------------------------------------------

def test_a_relative_path_starts_at_the_delegations_workspace(held):
    text = bridge.bridge_read_file("mine.txt")
    assert "in the workspace" in text, text
    assert "the profile workspace" not in text


def test_the_delegations_files_are_reachable_by_absolute_path(held):
    wrote = bridge.bridge_write_file(str(held.workspace / "sub" / "new.txt"), "made")
    assert wrote.startswith("wrote"), wrote
    assert (held.workspace / "sub" / "new.txt").read_text(encoding="utf-8") == "made"
    assert "made" in bridge.bridge_read_file("sub/new.txt")


def test_the_profile_workspace_is_not_the_delegations_to_read(held):
    text = bridge.bridge_read_file(str(held.profile / "profile.txt"))
    assert _refused(text), text


def test_a_read_root_is_readable_and_never_writable(held):
    assert "readable only" in bridge.bridge_read_file(str(held.readable / "notes.txt"))
    target = held.readable / "new.txt"
    assert _refused(bridge.bridge_write_file(str(target), "x"))
    assert not target.exists()


def test_a_path_outside_every_root_is_refused_for_read_and_write(held):
    assert _refused(bridge.bridge_read_file(str(held.elsewhere / "other.txt")))
    target = held.elsewhere / "made.txt"
    assert _refused(bridge.bridge_write_file(str(target), "x"))
    assert not target.exists()


def test_listing_and_search_show_only_what_the_delegation_may_read(held):
    (held.workspace / "deep").mkdir()
    (held.workspace / "deep" / "found.txt").write_text("needle here", encoding="utf-8")
    names = bridge.bridge_list_files(".").splitlines()
    assert {"mine.txt", str(Path("deep") / "found.txt")} <= set(names), names
    assert "needle here" in bridge.bridge_search_files("needle")
    assert _refused(bridge.bridge_list_files(str(held.elsewhere)))
    assert _refused(bridge.bridge_search_files("outside", path=str(held.elsewhere)))


# -- '..', separators, case ---------------------------------------------------------

def test_climbing_out_with_dotdot_is_judged_by_where_it_lands(held):
    assert _refused(bridge.bridge_read_file("../elsewhere/other.txt"))
    assert _refused(bridge.bridge_read_file(str(held.workspace / ".." / "elsewhere" / "other.txt")))
    assert _refused(bridge.bridge_write_file("../elsewhere/made.txt", "x"))
    assert not (held.elsewhere / "made.txt").exists()
    # and coming back in lands inside
    assert "in the workspace" in bridge.bridge_read_file("../ws/mine.txt")


def test_both_slash_separators_name_the_same_place(held):
    (held.workspace / "sub").mkdir()
    (held.workspace / "sub" / "a.txt").write_text("a", encoding="utf-8")
    for spelling in ("sub/a.txt", "sub\\a.txt", str(held.workspace).replace("\\", "/") + "/sub/a.txt"):
        assert not _refused(bridge.bridge_read_file(spelling)), spelling
    for spelling in ("../elsewhere/other.txt", "..\\elsewhere\\other.txt",
                     str(held.elsewhere).replace("\\", "/") + "/other.txt"):
        assert _refused(bridge.bridge_read_file(spelling)), spelling


@windows_only
def test_case_does_not_move_a_path_across_the_boundary(held):
    shouting = str(held.workspace / "MINE.TXT").upper()
    assert "in the workspace" in bridge.bridge_read_file(shouting)
    assert _refused(bridge.bridge_read_file(str(held.elsewhere / "OTHER.TXT").upper()))
    # a read root, however it is spelled, is still not a write root
    assert _refused(bridge.bridge_write_file(str(held.readable / "NOTES.TXT").swapcase(), "x"))


# -- links that leave a root ----------------------------------------------------------

def test_a_junction_or_symlink_out_of_the_root_is_refused(held):
    _link(held.workspace / "out", held.elsewhere)
    assert _refused(bridge.bridge_read_file("out/other.txt"))
    assert _refused(bridge.bridge_read_file(str(held.workspace / "out" / "other.txt")))
    assert _refused(bridge.bridge_write_file("out/made.txt", "x"))
    assert not (held.elsewhere / "made.txt").exists()
    assert _refused(bridge.bridge_list_files("out"))
    assert not any("other.txt" in name for name in bridge.bridge_list_files(".").splitlines())
    assert "outside the envelope" not in bridge.bridge_search_files("outside")


def test_a_redirected_known_folder_is_inside_only_where_it_really_lands(held):
    """A known folder moved elsewhere (Documents on another drive) is a
    junction. As a root it is judged by its target; as a link inside a root
    that points out, by where it leaves to."""
    redirected = held.root / "moved" / "Documents"
    redirected.mkdir(parents=True)
    (redirected / "report.txt").write_text("a redirected document", encoding="utf-8")
    home_documents = held.root / "home" / "Documents"
    home_documents.parent.mkdir()
    _link(home_documents, redirected)
    S.release("lease-1")
    S.register("lease-1", _envelope(held, read=[home_documents]), str(held.workspace))
    for spelling in (home_documents / "report.txt", redirected / "report.txt"):
        assert "a redirected document" in bridge.bridge_read_file(str(spelling)), spelling
    _link(held.workspace / "Documents", redirected)
    assert _refused(bridge.bridge_write_file("Documents/new.txt", "x"))
    assert not (redirected / "new.txt").exists()


# -- UNC ---------------------------------------------------------------------------

@windows_only
@pytest.mark.parametrize("spelling", [
    "\\\\server\\share\\file.txt", "//server/share/file.txt",
    "\\\\?\\C:\\Windows\\win.ini", "\\\\.\\C:\\Windows\\win.ini", "//?/C:/Windows/win.ini"])
def test_unc_and_device_paths_are_refused_before_they_are_touched(held, monkeypatch, spelling):
    real_resolve = Path.resolve

    def spy(self, *args, **kwargs):
        assert not str(self).startswith("\\\\"), f"touched {self} before refusing it"
        return real_resolve(self, *args, **kwargs)

    monkeypatch.setattr(Path, "resolve", spy)
    assert _refused(bridge.bridge_read_file(spelling))
    assert _refused(bridge.bridge_write_file(spelling, "x"))


@windows_only
@pytest.mark.parametrize("spelling", ["//server/share/file.txt", "//127.0.0.1/c$/file.txt",
                                      "\\\\server\\share\\file.txt"])
def test_the_jail_refuses_unc_in_either_slash_before_resolving_it(area, monkeypatch, spelling):
    """`//host/share` is a UNC path to Windows; resolving it reaches out to
    the host before any root is looked at, so the jail refuses its shape."""
    real_resolve = Path.resolve

    def spy(self, *args, **kwargs):
        assert not str(self).startswith("\\\\"), f"resolved {self} before refusing it"
        return real_resolve(self, *args, **kwargs)

    monkeypatch.setattr(Path, "resolve", spy)
    with pytest.raises(JailError, match="UNC"):
        F.jail().resolve(spelling)


# -- the boundary's own conditions ---------------------------------------------------------

def test_a_refusal_is_logged(held, caplog):
    with caplog.at_level(logging.WARNING, logger="friday.delegation_scope"):
        assert _refused(bridge.bridge_read_file(str(held.elsewhere / "other.txt")))
    assert any("delegation_scope.refused" in r.getMessage() and "other.txt" in r.getMessage()
               for r in caplog.records)
    line = json.loads((Path(os.environ[S.ENV_DIR]) / S.REFUSALS).read_text().splitlines()[-1])
    assert line["runs"] == "OBJ-S" and line["path"].endswith("other.txt") and line["write"] is False


def test_an_expired_envelope_allows_nothing_and_runs_nothing(area):
    S.register("lease-x", _envelope(area, seconds=30.0), str(area.workspace))
    try:
        entry = json.loads((Path(os.environ[S.ENV_DIR]) / "lease-x.json").read_text())
        entry["envelope"]["expires_at"] = _stamp(-1)
        (Path(os.environ[S.ENV_DIR]) / "lease-x.json").write_text(json.dumps(entry))
        assert _refused(bridge.bridge_read_file("mine.txt"))
        assert _refused(bridge.bridge_run_command("python --version"))
    finally:
        S.release("lease-x")


def test_a_registry_that_cannot_be_read_refuses_everything(area):
    folder = Path(os.environ[S.ENV_DIR])
    folder.mkdir(parents=True)
    (folder / "broken.json").write_text("{not json", encoding="utf-8")
    assert _refused(bridge.bridge_read_file(str(area.profile / "profile.txt")))
    assert _refused(bridge.bridge_read_file(str(area.workspace / "mine.txt")))
    assert _refused(bridge.bridge_run_command("python --version"))


def test_an_entry_with_no_expiry_is_not_an_envelope(area):
    S.register("lease-y", _envelope(area), str(area.workspace))
    path = Path(os.environ[S.ENV_DIR]) / "lease-y.json"
    entry = json.loads(path.read_text())
    entry["envelope"]["expires_at"] = ""
    path.write_text(json.dumps(entry))
    assert _refused(bridge.bridge_read_file("mine.txt"))


def test_with_nothing_registered_the_bridge_is_the_profile_workspace_it_was(area):
    assert "the profile workspace" in bridge.bridge_read_file("profile.txt")
    assert _refused(bridge.bridge_read_file(str(area.workspace / "mine.txt")))


def test_a_released_delegation_no_longer_holds_the_bridge(area):
    S.register("lease-z", _envelope(area), str(area.workspace))
    assert "in the workspace" in bridge.bridge_read_file("mine.txt")
    S.release("lease-z")
    assert "the profile workspace" in bridge.bridge_read_file("profile.txt")


def test_a_listing_never_falls_back_to_a_workspace_when_the_delegation_has_just_ended(area):
    S.register("lease-y", _envelope(area), str(area.workspace))
    S.release("lease-y")
    with pytest.raises(JailError):
        S.listing(area.workspace)


def test_two_delegations_are_held_to_what_both_allow(area):
    inner = area.workspace / "inner"
    inner.mkdir()
    (inner / "i.txt").write_text("inner", encoding="utf-8")
    S.register("lease-a", _envelope(area), str(area.workspace))
    narrower = c.Envelope(
        run_id="OBJ-N", objective_id="OBJ-N", provenance=c.PERSON, attended=True,
        cwd=str(area.workspace), read_roots=(str(area.workspace),),
        write_roots=(str(inner),),
        allowed_capabilities=("hermes_delegate",), expires_at=_stamp(3600))
    S.register("lease-b", narrower, str(area.workspace))
    assert bridge.bridge_write_file("inner/i.txt", "changed").startswith("wrote")
    assert (inner / "i.txt").read_text(encoding="utf-8") == "changed"   # both write there
    assert "in the workspace" in bridge.bridge_read_file("mine.txt")   # both read there
    assert _refused(bridge.bridge_write_file("mine2.txt", "x"))        # b cannot write it
    assert not (area.workspace / "mine2.txt").exists()
    assert _refused(bridge.bridge_read_file(str(area.readable / "notes.txt")))   # b cannot read it


def test_a_second_workspace_cannot_share_the_bridge(area):
    S.register("lease-1", _envelope(area), str(area.workspace))
    other = area.root / "other_ws"
    other.mkdir()
    with pytest.raises(c.OutsideEnvelope, match="one workspace at a time"):
        S.register("lease-2", _envelope(area, write=[area.workspace, other]), str(other))


def test_a_workspace_outside_the_envelope_is_not_registered(area):
    with pytest.raises(c.OutsideEnvelope, match="outside this run's envelope"):
        S.register("lease-3", _envelope(area), str(area.elsewhere))
    assert not (Path(os.environ[S.ENV_DIR]) / "lease-3.json").exists()


def test_an_entry_left_by_a_dead_process_is_pruned_at_the_next_registration(area):
    S.register("lease-dead", _envelope(area), str(area.workspace))
    path = Path(os.environ[S.ENV_DIR]) / "lease-dead.json"
    entry = json.loads(path.read_text())
    entry["owner"] = "999999:1"
    path.write_text(json.dumps(entry))
    S.register("lease-live", _envelope(area), str(area.workspace))
    assert not path.exists()
    assert [e.key for e in S.active()] == ["lease-live"]


# -- a program runs for a delegation only inside the OS sandbox -----------------------------
# (owner ruling 2026-10-03c: commands come back for a scoped delegation only through the
# AppContainer runner, EXEC-SBX-001 slice 3; each denial counts only after a CONTROL of the
# same command succeeded inside the roots)

needs_boundary = pytest.mark.skipif(
    not (WINDOWS and appcontainer.available()),
    reason="needs a Windows AppContainer (the OS boundary)")


def _python_writes(control: Path, target: Path) -> str:
    return (f'python -c "open(r\'{control}\', \'w\').write(\'c\'); print(\'CONTROL_OK\'); '
            f'open(r\'{target}\', \'w\').write(\'x\'); print(\'ESCAPED\')"')


def _provisioned_interpreter_writes(control: Path, target: Path) -> str:
    # an interpreter named by absolute path - the sandbox's own copy; the venv's
    # sys.executable is a launcher the container is refused, which proves nothing
    exe = appcontainer.provision_runtime(quarantine.default_runtime_dir())
    return (f'"{exe}" -c "open(r\'{control}\', \'w\').write(\'c\'); '
            f'print(\'CONTROL_OK\'); open(r\'{target}\', \'w\').write(\'x\'); print(\'ESCAPED\')"')


def _node_writes(control: Path, target: Path) -> str:
    return (f'node -e "const fs = require(\'fs\'); fs.writeFileSync(\'{control.as_posix()}\', \'c\'); '
            f'console.log(\'CONTROL_OK\'); fs.writeFileSync(\'{target.as_posix()}\', \'x\'); '
            f'console.log(\'ESCAPED\')"')


@needs_boundary
@pytest.mark.parametrize("command_for", [_python_writes, _provisioned_interpreter_writes, _node_writes],
                         ids=["python", "interpreter-by-path", "node"])
def test_an_interpreter_run_through_the_bridge_cannot_write_outside_the_envelope(held, command_for):
    if command_for is _node_writes and not shutil.which("node"):
        pytest.skip("node is not installed")
    control = held.workspace / "control.txt"
    escaped = held.elsewhere / "escaped.txt"
    text = bridge.bridge_run_command(command_for(control, escaped))
    assert not _refused(text), text
    assert "CONTROL_OK" in text, text                       # the in-root write worked ...
    assert control.read_text(encoding="utf-8") == "c"
    assert "ESCAPED" not in text, text                      # ... and the outside one did not
    assert not escaped.exists()
    assert bridge._env is None, "the unsandboxed environment was never used"


@needs_boundary
@pytest.mark.parametrize("command", ["python --version", "git status --short", "pytest -q"],
                         ids=["python", "git", "pytest"])
def test_a_program_runs_for_a_delegation_only_inside_the_sandbox(held, command):
    text = bridge.bridge_run_command(command)
    assert not _refused(text), text
    assert text.startswith("exit="), text            # it ran, and its own result came back
    if command == "python --version":
        assert "exit=0" in text and "Python" in text, text
    assert bridge._env is None, "the unsandboxed environment was never used"
    assert bridge._scoped is not None


@pytest.mark.parametrize("command", ["powershell -NoProfile -Command ls", "cmd /c dir"],
                         ids=["powershell", "cmd"])
def test_shells_stay_refused_for_a_delegation_by_the_executable_list(held, command):
    text = bridge.bridge_run_command(command)
    assert _refused(text) and "is not allowed through the bridge" in text, text
    assert bridge._env is None and bridge._scoped is None


def _expired(area):
    path = Path(os.environ[S.ENV_DIR]) / "lease-1.json"
    entry = json.loads(path.read_text())
    entry["envelope"]["expires_at"] = _stamp(-1)
    path.write_text(json.dumps(entry))


def _unreadable(area):
    (Path(os.environ[S.ENV_DIR]) / "broken.json").write_text("{not json", encoding="utf-8")


def _no_os_boundary(area, monkeypatch):
    monkeypatch.setattr(appcontainer, "available", lambda: False)


def _no_write_root(area):
    S.release("lease-1")
    S.register("lease-1", _envelope(area, read=[area.workspace, area.readable], write=[]),
               str(area.workspace))


@pytest.mark.parametrize("break_it", ["expired", "unreadable", "no-os-boundary", "no-write-root"])
def test_with_no_sandbox_to_run_in_nothing_runs_and_the_bridge_says_refused(
        held, monkeypatch, break_it):
    {"expired": lambda: _expired(held), "unreadable": lambda: _unreadable(held),
     "no-os-boundary": lambda: _no_os_boundary(held, monkeypatch),
     "no-write-root": lambda: _no_write_root(held)}[break_it]()
    ran = held.workspace / "ran.txt"
    text = bridge.bridge_run_command(f'python -c "open(r\'{ran}\', \'w\').write(\'x\')"')
    assert _refused(text), text
    assert not ran.exists()
    assert bridge._env is None, "no fallback to the unsandboxed environment"
    assert bridge._scoped is None, "no sandbox was opened either"


@pytest.mark.parametrize("break_it", ["expired", "unreadable", "no-os-boundary"])
def test_the_refusal_names_no_program_that_would_get_round_it(held, monkeypatch, break_it):
    {"expired": lambda: _expired(held), "unreadable": lambda: _unreadable(held),
     "no-os-boundary": lambda: _no_os_boundary(held, monkeypatch)}[break_it]()
    for command in ("python -c pass", "git status"):
        text = bridge.bridge_run_command(command)
        assert _refused(text)
        assert not re.search(r"\b(python|node|pytest|git|interpreters?|programs? directly)\b",
                             text, re.IGNORECASE), text
    # the refusal for a held delegation says why in the envelope's own terms
    if break_it == "expired":
        assert "envelope" in text


def test_a_refused_command_is_logged_with_the_delegation_that_was_refused(
        held, monkeypatch, caplog):
    monkeypatch.setattr(appcontainer, "available", lambda: False)
    with caplog.at_level(logging.WARNING, logger="friday.delegation_scope"):
        assert _refused(bridge.bridge_run_command("python --version"))
    assert any("delegation_scope.refused" in r.getMessage() and "(command)" in r.getMessage()
               for r in caplog.records)
    line = json.loads((Path(os.environ[S.ENV_DIR]) / S.REFUSALS).read_text().splitlines()[-1])
    assert line["runs"] == "OBJ-S" and line["path"] == "(command)"
    assert "OS boundary" in line["reason"]


def test_files_stay_available_to_the_delegation_beside_commands(held):
    assert bridge.bridge_write_file("made.txt", "by the file tool").startswith("wrote")
    assert (held.workspace / "made.txt").read_text(encoding="utf-8") == "by the file tool"
    assert "by the file tool" in bridge.bridge_read_file("made.txt")


def test_with_no_delegation_the_bridge_still_runs_commands_in_the_profile_workspace(area):
    out = bridge.bridge_run_command('python -c "import os; print(os.getcwd())"')
    assert not _refused(out), out
    assert str(area.profile.resolve()).lower() in out.lower(), out
    assert bridge._scoped is None


def test_commands_come_back_when_the_delegation_is_released(area):
    S.register("lease-c", _envelope(area), str(area.workspace))
    S.release("lease-c")
    out = bridge.bridge_run_command("python --version")
    assert not _refused(out) and "exit=0" in out, out


_PROBE = """
import sys
from pathlib import Path
for label, kind, path in zip(sys.argv[1::3], sys.argv[2::3], sys.argv[3::3]):
    try:
        if kind == "write":
            Path(path).write_text("x")
        else:
            Path(path).read_text()
        print(label, "OK")
    except OSError:
        print(label, "DENIED")
"""


@needs_boundary
def test_two_held_delegations_run_a_command_with_only_the_roots_both_allow(area):
    inner = area.workspace / "inner"
    inner.mkdir()
    (area.workspace / "probe.py").write_text(_PROBE, encoding="utf-8")
    S.register("lease-a", _envelope(area), str(area.workspace))
    narrower = c.Envelope(
        run_id="OBJ-N", objective_id="OBJ-N", provenance=c.PERSON, attended=True,
        cwd=str(area.workspace), read_roots=(str(area.workspace),),
        write_roots=(str(inner),),
        allowed_capabilities=("hermes_delegate",), expires_at=_stamp(3600))
    S.register("lease-b", narrower, str(area.workspace))
    try:
        text = bridge.bridge_run_command(
            f'python probe.py INNER write "{inner / "i.txt"}" '
            f'SIBLING write "{area.workspace / "mine2.txt"}" '
            f'OWN read "{area.workspace / "mine.txt"}" '
            f'ONLYA read "{area.readable / "notes.txt"}" '
            f'ELSEWHERE read "{area.elsewhere / "other.txt"}"')
    finally:
        S.release("lease-a")
        S.release("lease-b")
    assert not _refused(text), text
    assert "INNER OK" in text and (inner / "i.txt").exists()         # both write there
    assert "OWN OK" in text                                           # both read there
    assert "SIBLING DENIED" in text and not (area.workspace / "mine2.txt").exists()
    assert "ONLYA DENIED" in text                                     # b cannot read it
    assert "ELSEWHERE DENIED" in text
    assert bridge._env is None


def _acl(path: Path) -> str:
    done = subprocess.run(["icacls", str(path)], capture_output=True, text=True, timeout=60)
    assert done.returncode == 0, done.stdout + done.stderr
    return done.stdout.replace(str(path), "<path>")


@needs_boundary
def test_the_sandbox_is_reused_across_calls_and_closed_when_the_delegation_is_released(area):
    protected = area.workspace / ".env"
    protected.write_text("TOKEN=not-a-real-secret", encoding="utf-8")
    dir_before = scoped_runner._aces(scoped_runner._read_sddl(area.workspace))
    file_before = _acl(protected)
    S.register("lease-r", _envelope(area), str(area.workspace))
    first = bridge.bridge_run_command("python --version")
    runner = bridge._scoped.runner
    second = bridge.bridge_run_command("python --version")
    assert "exit=0" in first and "exit=0" in second, (first, second)
    assert bridge._scoped.runner is runner, "one profile for the same delegations"
    sid = runner.sid
    assert sid and sid in scoped_runner._read_sddl(area.workspace)    # granted while held
    S.release("lease-r")
    after = bridge.bridge_run_command("python --version")             # the held set changed
    assert "exit=0" in after, after
    assert bridge._scoped is None
    assert sid not in scoped_runner._read_sddl(area.workspace)
    assert scoped_runner._aces(scoped_runner._read_sddl(area.workspace)) == dir_before
    assert _acl(protected) == file_before


@needs_boundary
def test_a_delegation_that_expires_closes_its_sandbox_and_refuses(area):
    S.register("lease-e", _envelope(area), str(area.workspace))
    try:
        assert "exit=0" in bridge.bridge_run_command("python --version")
        assert bridge._scoped is not None
        path = Path(os.environ[S.ENV_DIR]) / "lease-e.json"
        entry = json.loads(path.read_text())
        entry["envelope"]["expires_at"] = _stamp(-1)
        path.write_text(json.dumps(entry))
        assert _refused(bridge.bridge_run_command("python --version"))
        assert bridge._scoped is None
    finally:
        S.release("lease-e")


def test_the_command_envelope_is_the_intersection_of_what_every_held_delegation_allows(area):
    inner = area.workspace / "inner"
    inner.mkdir()
    S.register("lease-a", _envelope(area), str(area.workspace))
    narrower = c.Envelope(
        run_id="OBJ-N", objective_id="OBJ-N", provenance=c.PERSON, attended=True,
        cwd=str(area.workspace), read_roots=(str(area.workspace),),
        write_roots=(str(inner),),
        allowed_capabilities=("hermes_delegate",), expires_at=_stamp(1800))
    S.register("lease-b", narrower, str(area.workspace))
    try:
        envelope = S.command_envelope()
    finally:
        S.release("lease-a")
        S.release("lease-b")
    assert envelope.run_id == "OBJ-S+OBJ-N"
    assert envelope.cwd == str(area.workspace)
    assert envelope.write_roots == (str(inner),)
    assert envelope.read_roots == (str(area.workspace),)    # `readable` is only lease-a's
    assert 0 < envelope.seconds_left() <= 1800               # the earlier expiry


def test_the_command_envelope_carries_the_least_trusted_lineage_held(area):
    S.register("lease-a", _envelope(area), str(area.workspace))
    read = c.Envelope(
        run_id="OBJ-R", objective_id="OBJ-R", provenance=c.READ_MATERIAL, attended=False,
        cwd=str(area.workspace), read_roots=(str(area.workspace),),
        write_roots=(str(area.workspace),),
        allowed_capabilities=("hermes_delegate",), expires_at=_stamp(1800))
    S.register("lease-r", read, str(area.workspace))
    try:
        envelope = S.command_envelope()
    finally:
        S.release("lease-a")
        S.release("lease-r")
    assert envelope.provenance == c.READ_MATERIAL
    assert envelope.attended is False


def test_there_is_no_command_envelope_when_nothing_is_held(area):
    assert S.command_envelope() is None


# -- the supervisor holds it for the life of the run ---------------------------------------

def _supervisor(tmp_path, monkeypatch, *, hang=True, extra_env=None):
    for key in ("FAKE_HERMES_CLARIFY", "FAKE_HERMES_HANG", "FAKE_HERMES_DIE"):
        monkeypatch.delenv(key, raising=False)
    if hang:
        monkeypatch.setenv("FAKE_HERMES_HANG", "1")
    supervisor = hb.HermesSupervisor(log=hb.WorkRunLog(tmp_path / "bridge.sqlite3"),
                                     command=[sys.executable, FAKE], profile="",
                                     extra_env=extra_env)
    supervisor.READY_TIMEOUT = 20
    return supervisor


def test_a_delegation_holds_the_bridge_to_its_envelope_until_it_ends(area, monkeypatch):
    supervisor = _supervisor(area.root, monkeypatch)
    try:
        out = supervisor.delegate(hb.TaskBundle(goal="long job"), workspace=str(area.workspace),
                                  scope=_envelope(area), expires_in=3600)
        (entry,) = S.active()
        assert entry.workspace == str(area.workspace)
        assert entry.envelope.write_roots == (str(area.workspace),)
        assert _refused(bridge.bridge_read_file(str(area.elsewhere / "other.txt")))
        escaped = area.elsewhere / "escaped.txt"
        text = bridge.bridge_run_command(
            _python_writes(area.workspace / "control.txt", escaped))
        if WINDOWS and appcontainer.available():
            assert "CONTROL_OK" in text and "ESCAPED" not in text, text   # sandboxed
        else:
            assert _refused(text), text                                   # no sandbox, no run
        assert not escaped.exists()
        supervisor.interrupt(out["work_run_id"])
        deadline = time.time() + 15
        while time.time() < deadline and S.active():
            time.sleep(0.2)
        assert S.active() == []
    finally:
        supervisor.stop()
    assert S.active() == []


def test_a_delegation_without_an_envelope_registers_nothing(area, monkeypatch):
    supervisor = _supervisor(area.root, monkeypatch, hang=False)
    try:
        supervisor.delegate(hb.TaskBundle(goal="quick job"), wait=True, turn_timeout=30)
        assert S.active() == []
    finally:
        supervisor.stop()


def test_a_delegation_that_would_share_the_bridge_under_another_workspace_is_refused(area, monkeypatch):
    other = area.root / "other_ws"
    other.mkdir()
    supervisor = _supervisor(area.root, monkeypatch)
    try:
        supervisor.delegate(hb.TaskBundle(goal="first"), workspace=str(area.workspace),
                            scope=_envelope(area), expires_in=3600)
        with pytest.raises(c.OutsideEnvelope, match="one workspace at a time"):
            supervisor.delegate(hb.TaskBundle(goal="second"), workspace=str(other),
                                scope=_envelope(area, write=[area.workspace, other]),
                                expires_in=3600)
        assert [e.workspace for e in S.active()] == [str(area.workspace)]
    finally:
        supervisor.stop()


def test_a_supervisor_with_extra_env_passes_it_to_the_gateway_process(tmp_path, monkeypatch):
    dump = tmp_path / "env.json"
    monkeypatch.setenv("FAKE_HERMES_ENV_DUMP", str(dump))
    supervisor = _supervisor(tmp_path, monkeypatch, hang=False,
                              extra_env={"HERMES_TUI_TOOLSETS": "web"})
    try:
        supervisor.start()
        seen = json.loads(dump.read_text(encoding="utf-8"))
    finally:
        supervisor.stop()
    assert seen.get("HERMES_TUI_TOOLSETS") == "web"


def test_a_supervisor_with_no_extra_env_adds_none(tmp_path, monkeypatch):
    dump = tmp_path / "env.json"
    monkeypatch.setenv("FAKE_HERMES_ENV_DUMP", str(dump))
    supervisor = _supervisor(tmp_path, monkeypatch, hang=False)
    try:
        supervisor.start()
        seen = json.loads(dump.read_text(encoding="utf-8"))
    finally:
        supervisor.stop()
    assert "HERMES_TUI_TOOLSETS" not in seen


# -- Hermes' own file tools are not a way around it -----------------------------------------

def _profile(tmp_path, monkeypatch, config: str) -> None:
    home = tmp_path / "hermes-profile"
    home.mkdir(exist_ok=True)
    (home / "config.yaml").write_text(config, encoding="utf-8")
    monkeypatch.setenv(hb.ENV_PROFILE_HOME, str(home))


@pytest.mark.parametrize("config, named", [
    ("toolsets: [web, file]\n", ("file",)),
    ("platform_toolsets:\n  cli: [terminal, web]\n", ("terminal",)),
    ("agent:\n  enabled_toolsets: [File, terminal]\n", ("file", "terminal")),
    ("toolsets: [web]\nmcp_servers:\n  friday_exec: {command: python}\n", ()),
    ("disabled_toolsets: [file, terminal]\n", ()),
])
def test_the_profiles_native_file_toolsets_are_read_from_its_config(tmp_path, monkeypatch, config, named):
    _profile(tmp_path, monkeypatch, config)
    assert hb.native_file_toolsets_enabled() == named


def test_a_profile_config_that_cannot_be_read_names_nothing_known(tmp_path, monkeypatch):
    _profile(tmp_path, monkeypatch, "toolsets: [unclosed\n")
    with pytest.raises(ValueError, match="cannot be read"):
        hb.native_file_toolsets_enabled()


def test_scoped_gateway_toolsets_drops_native_ones_and_dedupes(tmp_path, monkeypatch):
    _profile(tmp_path, monkeypatch, "toolsets: [file, terminal, web, memory]\n")
    assert hb.scoped_gateway_toolsets() == ("web", "memory")


def test_scoped_gateway_toolsets_refuses_to_guess_when_the_profile_names_none(tmp_path, monkeypatch):
    _profile(tmp_path, monkeypatch, "toolsets: [file, terminal]\n")
    with pytest.raises(ValueError, match="refusing to guess"):
        hb.scoped_gateway_toolsets()


def _gateway_envs(dump: Path) -> list[dict]:
    """The HERMES_* environment of every gateway process started, in order."""
    return [json.loads(line) for line in dump.read_text(encoding="utf-8").splitlines()]


def _until(condition, seconds: float = 15.0) -> bool:
    deadline = time.time() + seconds
    while time.time() < deadline:
        if condition():
            return True
        time.sleep(0.1)
    return condition()


def test_a_delegation_under_an_envelope_runs_on_a_gateway_without_hermes_file_tools(area, tmp_path, monkeypatch):
    _profile(tmp_path, monkeypatch, "toolsets: [file, terminal, web, memory]\n")
    dump = tmp_path / "env.jsonl"
    monkeypatch.setenv("FAKE_HERMES_ENV_DUMP", str(dump))
    supervisor = _supervisor(area.root, monkeypatch)
    try:
        supervisor.delegate(hb.TaskBundle(goal="edit"), workspace=str(area.workspace),
                            scope=_envelope(area), expires_in=3600)
        (env,) = _gateway_envs(dump)
        assert env[hb.ENV_GATEWAY_TOOLSETS] == "web,memory"
        assert not supervisor.alive()               # the shared gateway was not used
        assert len(S.active()) == 1
    finally:
        supervisor.stop()


def test_a_delegation_without_an_envelope_stays_on_the_shared_gateway_with_no_override(area, tmp_path, monkeypatch):
    _profile(tmp_path, monkeypatch, "toolsets: [file, terminal, web]\n")
    dump = tmp_path / "env.jsonl"
    monkeypatch.setenv("FAKE_HERMES_ENV_DUMP", str(dump))
    supervisor = _supervisor(area.root, monkeypatch, hang=False)
    try:
        supervisor.delegate(hb.TaskBundle(goal="quick job"), wait=True, turn_timeout=30)
        (env,) = _gateway_envs(dump)
        assert hb.ENV_GATEWAY_TOOLSETS not in env
        assert supervisor._scoped_gateway is None
    finally:
        supervisor.stop()


def test_scoped_and_unscoped_delegations_run_on_gateways_of_their_own(area, tmp_path, monkeypatch):
    dump = tmp_path / "env.jsonl"
    monkeypatch.setenv("FAKE_HERMES_ENV_DUMP", str(dump))
    supervisor = _supervisor(area.root, monkeypatch, hang=False)
    try:
        supervisor.delegate(hb.TaskBundle(goal="scoped"), workspace=str(area.workspace),
                            scope=_envelope(area), expires_in=3600, wait=True, turn_timeout=30)
        supervisor.delegate(hb.TaskBundle(goal="unscoped"), wait=True, turn_timeout=30)
        scoped_env, shared_env = _gateway_envs(dump)
        assert scoped_env[hb.ENV_GATEWAY_TOOLSETS] == "web"
        assert hb.ENV_GATEWAY_TOOLSETS not in shared_env
        # the scoped gateway did not take the log's terminal hook from the
        # shared one: the unscoped run's worker slot was freed at its end
        assert supervisor.log.on_terminal_hook == supervisor._release_lease
        assert supervisor._leases == {}
        assert S.active() == []
    finally:
        supervisor.stop()


def test_the_scoped_gateway_is_stopped_once_no_run_holds_it(area, monkeypatch):
    supervisor = _supervisor(area.root, monkeypatch, hang=False)
    try:
        supervisor.delegate(hb.TaskBundle(goal="scoped"), workspace=str(area.workspace),
                            scope=_envelope(area), expires_in=3600, wait=True, turn_timeout=30)
        assert supervisor._scoped_gateway is None
        (ran_on,) = set(supervisor._scoped_runs.values())
        assert not ran_on.alive()
        assert S.active() == []
    finally:
        supervisor.stop()


def test_runs_under_an_envelope_share_one_gateway_and_the_last_to_end_stops_it(area, tmp_path, monkeypatch):
    dump = tmp_path / "env.jsonl"
    monkeypatch.setenv("FAKE_HERMES_ENV_DUMP", str(dump))
    supervisor = _supervisor(area.root, monkeypatch)
    try:
        first = supervisor.delegate(hb.TaskBundle(goal="first"), workspace=str(area.workspace),
                                    scope=_envelope(area), expires_in=3600)["work_run_id"]
        second = supervisor.delegate(hb.TaskBundle(goal="second"), workspace=str(area.workspace),
                                     scope=_envelope(area), expires_in=3600)["work_run_id"]
        assert len(_gateway_envs(dump)) == 1
        gateway = supervisor._scoped_gateway
        assert gateway is not None and gateway.alive()
        # calls about a run reach the gateway the run is on, not the shared one
        assert supervisor.steer(first, "go left")["status"] == "queued"
        supervisor.interrupt(first)
        assert _until(lambda: supervisor.log.get(first)["status"] in hb.TERMINAL)
        assert gateway.alive()                      # the second run still holds it
        supervisor.interrupt(second)
        assert _until(lambda: not gateway.alive())
        assert supervisor._scoped_gateway is None
        assert S.active() == []
    finally:
        supervisor.stop()


def test_health_reports_the_scoped_gateway_only_while_a_run_holds_it(area, monkeypatch):
    supervisor = _supervisor(area.root, monkeypatch)
    try:
        assert "scoped" not in supervisor.health()
        run = supervisor.delegate(hb.TaskBundle(goal="edit"), workspace=str(area.workspace),
                                  scope=_envelope(area), expires_in=3600)["work_run_id"]
        gateway = supervisor._scoped_gateway
        report = supervisor.health()
        assert report["alive"] is False             # the shared gateway was never started
        assert report["scoped"] == {"alive": True, "state": gateway.state, "runs": 1,
                                    "responsive": True}
        supervisor.interrupt(run)
        assert _until(lambda: not gateway.alive())
        assert _until(lambda: "scoped" not in supervisor.health())
    finally:
        supervisor.stop()


def test_a_scoped_gateway_that_is_up_but_does_not_answer_is_not_reported_responsive(area, monkeypatch):
    supervisor = _supervisor(area.root, monkeypatch)
    try:
        supervisor.delegate(hb.TaskBundle(goal="edit"), workspace=str(area.workspace),
                            scope=_envelope(area), expires_in=3600)
        gateway = supervisor._scoped_gateway

        def silent(method, params, *, timeout=None):
            raise TimeoutError(f"{method} unanswered after {timeout:.0f}s")
        monkeypatch.setattr(gateway, "request", silent)
        scoped = supervisor.health()["scoped"]
        assert scoped["alive"] is True and scoped["responsive"] is False
        assert "session.list unanswered" in scoped["error"]
    finally:
        supervisor.stop()


def test_hermes_status_shows_the_scoped_gateway_when_it_is_the_only_one_up(area, monkeypatch):
    from friday import policy as P
    from friday.tools import hermes_control as hc

    class FakeMCP:
        tools: dict = {}

        def tool(self):
            def register(fn):
                self.tools[fn.__name__] = fn
                return fn
            return register

    supervisor = _supervisor(area.root, monkeypatch)
    monkeypatch.setattr(hc, "_engine", P.PolicyEngine(autonomy=P.FULL))
    monkeypatch.setattr(hb, "native_tools_healthy", lambda *a, **k: True)
    mcp = FakeMCP()
    hc.register(mcp)
    hc.configure(supervisor)
    try:
        supervisor.delegate(hb.TaskBundle(goal="edit"), workspace=str(area.workspace),
                            scope=_envelope(area), expires_in=3600)
        gateway = mcp.tools["hermes_status"]()["gateway"]
        assert gateway["alive"] is False
        assert gateway["scoped"]["alive"] is True and gateway["scoped"]["runs"] == 1
    finally:
        hc.configure(None)
        supervisor.stop()


def test_a_run_under_an_envelope_is_stopped_at_its_end_without_its_timer(area, monkeypatch):
    supervisor = _supervisor(area.root, monkeypatch)
    try:
        run = supervisor.delegate(hb.TaskBundle(goal="long job"), workspace=str(area.workspace),
                                  scope=_envelope(area), expires_in=3600)["work_run_id"]
        past = (datetime.now(timezone.utc) - timedelta(seconds=5)).isoformat()
        supervisor.log.update(run, expires_at=past)
        assert supervisor.expire_overdue() == [run]
        assert _until(lambda: S.active() == [])
    finally:
        supervisor.stop()


# -- a gateway that does not honour its toolsets is caught, not trusted ---------------------

def test_a_gateway_that_honours_its_toolsets_is_not_mistaken_for_one_that_does_not(area, tmp_path, monkeypatch):
    # The scoped run reads through the exec bridge (an MCP-prefixed name), the
    # shared one through Hermes' own read_file: neither is a breach.
    _profile(tmp_path, monkeypatch, "toolsets: [file, terminal, web]\n")
    supervisor = _supervisor(area.root, monkeypatch, hang=False)
    try:
        scoped = supervisor.delegate(hb.TaskBundle(goal="scoped"), workspace=str(area.workspace),
                                     scope=_envelope(area), expires_in=3600, wait=True,
                                     turn_timeout=30)["work_run_id"]
        shared = supervisor.delegate(hb.TaskBundle(goal="shared"), wait=True,
                                     turn_timeout=30)["work_run_id"]
        assert supervisor.log.get(scoped)["status"] == hb.COMPLETE
        assert supervisor.log.get(shared)["status"] == hb.COMPLETE
        assert supervisor._scoped_unsound == ""
    finally:
        supervisor.stop()


def test_a_run_that_uses_hermes_file_tools_on_a_scoped_gateway_is_stopped_and_recorded(area, tmp_path, monkeypatch, caplog):
    _profile(tmp_path, monkeypatch, "toolsets: [file, terminal, web]\n")
    monkeypatch.setenv("FAKE_HERMES_IGNORE_TOOLSETS", "1")
    supervisor = _supervisor(area.root, monkeypatch, hang=False)
    try:
        with caplog.at_level(logging.WARNING, logger=hb.logger.name):
            run = supervisor.delegate(hb.TaskBundle(goal="edit"), workspace=str(area.workspace),
                                      scope=_envelope(area), expires_in=3600)["work_run_id"]
            assert _until(lambda: supervisor.log.get(run)["status"] in hb.TERMINAL)
        record = supervisor.log.get(run)
        assert record["status"] == hb.PARTIAL           # stopped, never announced as done
        assert record["failure_kind"] == hb.HermesSupervisor.ENVELOPE_BREACHED
        assert "hermes.envelope_breached" in caplog.text and "read_file" in caplog.text
        assert _until(lambda: S.active() == [])         # its hold on the bridge is released
        assert _until(lambda: supervisor._scoped_gateway is None)   # and so is its gateway
    finally:
        supervisor.stop()


def test_no_delegation_under_an_envelope_gets_a_gateway_after_one_ignored_its_toolsets(area, tmp_path, monkeypatch):
    _profile(tmp_path, monkeypatch, "toolsets: [file, terminal, web]\n")
    monkeypatch.setenv("FAKE_HERMES_IGNORE_TOOLSETS", "1")
    dump = tmp_path / "env.jsonl"
    monkeypatch.setenv("FAKE_HERMES_ENV_DUMP", str(dump))
    supervisor = _supervisor(area.root, monkeypatch, hang=False)
    try:
        run = supervisor.delegate(hb.TaskBundle(goal="edit"), workspace=str(area.workspace),
                                  scope=_envelope(area), expires_in=3600)["work_run_id"]
        assert _until(lambda: supervisor.log.get(run)["status"] in hb.TERMINAL)
        with pytest.raises(c.OutsideEnvelope, match="does not honour HERMES_TUI_TOOLSETS"):
            supervisor.delegate(hb.TaskBundle(goal="again"), workspace=str(area.workspace),
                                scope=_envelope(area), expires_in=3600)
        assert len(_gateway_envs(dump)) == 1            # no second gateway was started
        assert S.active() == []
    finally:
        supervisor.stop()


def test_every_run_on_a_gateway_that_ignored_its_toolsets_is_stopped(area, monkeypatch):
    supervisor = _supervisor(area.root, monkeypatch)    # both runs hang, none has used a tool
    try:
        runs = [supervisor.delegate(hb.TaskBundle(goal=goal), workspace=str(area.workspace),
                                    scope=_envelope(area), expires_in=3600)["work_run_id"]
                for goal in ("first", "second")]
        supervisor._scoped_gateway._deny_native_tool("terminal")
        assert _until(lambda: all(supervisor.log.get(r)["status"] in hb.TERMINAL for r in runs))
        assert {supervisor.log.get(r)["failure_kind"] for r in runs} == {
            hb.HermesSupervisor.ENVELOPE_BREACHED}
        assert supervisor._scoped_unsound
    finally:
        supervisor.stop()


@pytest.mark.parametrize("name", ["mcp__friday_exec__bridge_read_file", "bridge_run_command",
                                  "web_search", "todo", ""])
def test_a_tool_that_is_not_one_of_hermes_file_or_terminal_tools_is_not_a_breach(area, monkeypatch, name):
    supervisor = _supervisor(area.root, monkeypatch)
    try:
        run = supervisor.delegate(hb.TaskBundle(goal="edit"), workspace=str(area.workspace),
                                  scope=_envelope(area), expires_in=3600)["work_run_id"]
        supervisor._scoped_gateway._deny_native_tool(name)
        assert supervisor._scoped_unsound == ""
        assert supervisor.log.get(run)["status"] not in hb.TERMINAL
        assert supervisor.log.get(run)["failure_kind"] == ""
    finally:
        supervisor.stop()


def test_the_shared_gateway_may_use_hermes_file_tools(area, monkeypatch):
    # Only a gateway started for a delegation under an envelope is held to
    # not having them; the shared one keeps the profile's own toolsets.
    supervisor = _supervisor(area.root, monkeypatch, hang=False)
    try:
        supervisor.start()
        supervisor._deny_native_tool("read_file")
        assert supervisor._scoped_unsound == ""
    finally:
        supervisor.stop()


# -- ... and, when it says what it holds, caught before the task is sent --------------------

def _calls(log: Path) -> list[str]:
    return log.read_text(encoding="utf-8").split() if log.exists() else []


def test_a_gateway_that_reports_hermes_file_tools_is_refused_before_any_task_is_sent(area, tmp_path, monkeypatch, caplog):
    _profile(tmp_path, monkeypatch, "toolsets: [file, terminal, web]\n")
    monkeypatch.setenv("FAKE_HERMES_IGNORE_TOOLSETS", "1")
    monkeypatch.setenv("FAKE_HERMES_REPORT_TOOLS", "1")
    calls = tmp_path / "calls.log"
    monkeypatch.setenv("FAKE_HERMES_CALL_LOG", str(calls))
    supervisor = _supervisor(area.root, monkeypatch, hang=False)
    try:
        with caplog.at_level(logging.WARNING, logger=hb.logger.name):
            with pytest.raises(c.OutsideEnvelope, match="nothing was sent"):
                supervisor.delegate(hb.TaskBundle(goal="edit"), workspace=str(area.workspace),
                                    scope=_envelope(area), expires_in=3600)
        assert "session.create" in _calls(calls)
        assert "prompt.submit" not in _calls(calls)         # the task never reached Hermes
        assert "session.interrupt" in _calls(calls)         # the session it was given was stopped
        [run] = supervisor.log.recent(1)
        assert run["hermes_session_id"]                     # ... by the id, which the stop reads
        assert run["status"] == hb.PARTIAL
        assert run["failure_kind"] == hb.HermesSupervisor.ENVELOPE_BREACHED
        assert "hermes.envelope_breached" in caplog.text
        assert S.active() == []                             # its hold on the bridge is released
        assert supervisor._scoped_unsound
        assert _until(lambda: supervisor._scoped_gateway is None)
    finally:
        supervisor.stop()


def test_a_refused_session_that_the_gateway_cannot_stop_still_ends_the_run(area, tmp_path, monkeypatch):
    _profile(tmp_path, monkeypatch, "toolsets: [file, terminal, web]\n")
    monkeypatch.setenv("FAKE_HERMES_IGNORE_TOOLSETS", "1")
    monkeypatch.setenv("FAKE_HERMES_REPORT_TOOLS", "1")
    monkeypatch.setenv("FAKE_HERMES_REFUSE_INTERRUPT", "1")
    calls = tmp_path / "calls.log"
    monkeypatch.setenv("FAKE_HERMES_CALL_LOG", str(calls))
    supervisor = _supervisor(area.root, monkeypatch, hang=False)
    try:
        with pytest.raises(c.OutsideEnvelope, match="nothing was sent"):
            supervisor.delegate(hb.TaskBundle(goal="edit"), workspace=str(area.workspace),
                                scope=_envelope(area), expires_in=3600)
        assert "session.interrupt" in _calls(calls)         # it was asked, and said no
        assert "prompt.submit" not in _calls(calls)         # the task never reached Hermes
        [run] = supervisor.log.recent(1)
        assert run["status"] == hb.PARTIAL                  # not left CANCELLING for ever
        assert run["failure_kind"] == hb.HermesSupervisor.ENVELOPE_BREACHED
        assert S.active() == []
        assert [d for d in supervisor.log.pending_deliveries()
                if d["work_run_id"] == run["work_run_id"]] == []
        assert _until(lambda: supervisor._scoped_gateway is None)
    finally:
        supervisor.stop()


def test_no_delegation_under_an_envelope_gets_a_gateway_after_one_reported_hermes_file_tools(area, tmp_path, monkeypatch):
    _profile(tmp_path, monkeypatch, "toolsets: [file, terminal, web]\n")
    monkeypatch.setenv("FAKE_HERMES_IGNORE_TOOLSETS", "1")
    monkeypatch.setenv("FAKE_HERMES_REPORT_TOOLS", "1")
    dump = tmp_path / "env.jsonl"
    monkeypatch.setenv("FAKE_HERMES_ENV_DUMP", str(dump))
    supervisor = _supervisor(area.root, monkeypatch, hang=False)
    try:
        with pytest.raises(c.OutsideEnvelope, match="nothing was sent"):
            supervisor.delegate(hb.TaskBundle(goal="edit"), workspace=str(area.workspace),
                                scope=_envelope(area), expires_in=3600)
        with pytest.raises(c.OutsideEnvelope, match="does not honour HERMES_TUI_TOOLSETS"):
            supervisor.delegate(hb.TaskBundle(goal="again"), workspace=str(area.workspace),
                                scope=_envelope(area), expires_in=3600)
        assert len(_gateway_envs(dump)) == 1                # no second gateway was started
    finally:
        supervisor.stop()


def test_a_gateway_that_reports_only_the_exec_bridge_runs_the_task(area, tmp_path, monkeypatch):
    _profile(tmp_path, monkeypatch, "toolsets: [file, terminal, web]\n")
    monkeypatch.setenv("FAKE_HERMES_REPORT_TOOLS", "1")     # it honours its toolsets
    supervisor = _supervisor(area.root, monkeypatch, hang=False)
    try:
        run = supervisor.delegate(hb.TaskBundle(goal="scoped"), workspace=str(area.workspace),
                                  scope=_envelope(area), expires_in=3600, wait=True,
                                  turn_timeout=30)["work_run_id"]
        assert supervisor.log.get(run)["status"] == hb.COMPLETE
        assert supervisor._scoped_unsound == ""
    finally:
        supervisor.stop()


def test_the_shared_gateway_is_not_refused_for_reporting_hermes_file_tools(area, monkeypatch):
    # No scope, so no envelope to hold it to: it keeps the profile's own toolsets.
    monkeypatch.setenv("FAKE_HERMES_REPORT_TOOLS", "1")
    supervisor = _supervisor(area.root, monkeypatch, hang=False)
    try:
        run = supervisor.delegate(hb.TaskBundle(goal="shared"), wait=True,
                                  turn_timeout=30)["work_run_id"]
        assert supervisor.log.get(run)["status"] == hb.COMPLETE
        assert supervisor._scoped_unsound == ""
    finally:
        supervisor.stop()


@pytest.mark.parametrize("info, expected", [
    ({"tools": {"file": ["read_file", "write_file"], "web": ["web_search"]}},
     ("read_file", "write_file", "file toolset")),
    ({"tools": {"terminal": ["terminal"]}}, ("terminal", "terminal toolset")),
    ({"tools": {"File": ["patch"]}}, ("patch", "File toolset")),
    ({"tools": {"web": ["search_files"]}}, ("search_files",)),
    ({"tools": ["web_search", "patch"]}, ("patch",)),
    ({"tools": [{"name": "write_file"}, {"name": "todo"}]}, ("write_file",)),
])
def test_the_native_tools_a_session_reports_are_found_in_any_shape_hermes_lists_them(info, expected):
    assert hb.native_tools_in_session_info(info) == expected


@pytest.mark.parametrize("info", [
    {"tools": {"mcp-friday_exec": ["mcp__friday_exec__bridge_read_file", "bridge_run_command"],
               "web": ["web_search"], "todo": ["todo"]}},
    {"tools": {"file": [], "terminal": []}},            # a native toolset that holds nothing
    {"tools": {}}, {"tools": []}, {"tools": None}, {"tools": "read_file"},
    {"model": "m"}, {}, None, "read_file",
])
def test_a_session_that_reports_no_native_tool_is_not_called_a_breach(info):
    assert hb.native_tools_in_session_info(info) == ()


def test_a_gateway_that_does_not_report_its_tools_is_still_caught_by_its_first_native_call(area, tmp_path, monkeypatch):
    # Nothing in the session info to refuse on, so the run starts; the
    # tool.start backstop stops it (the same behaviour as before the check).
    _profile(tmp_path, monkeypatch, "toolsets: [file, terminal, web]\n")
    monkeypatch.setenv("FAKE_HERMES_IGNORE_TOOLSETS", "1")
    supervisor = _supervisor(area.root, monkeypatch, hang=False)
    try:
        run = supervisor.delegate(hb.TaskBundle(goal="edit"), workspace=str(area.workspace),
                                  scope=_envelope(area), expires_in=3600)["work_run_id"]
        assert _until(lambda: supervisor.log.get(run)["status"] in hb.TERMINAL)
        assert supervisor.log.get(run)["failure_kind"] == hb.HermesSupervisor.ENVELOPE_BREACHED
        assert supervisor._scoped_unsound
    finally:
        supervisor.stop()


def test_a_delegation_under_an_envelope_is_refused_when_its_gateway_would_still_have_file_tools(area, monkeypatch):
    monkeypatch.setattr(hb, "scoped_gateway_toolsets", lambda profile="": ("web", "File"))
    supervisor = _supervisor(area.root, monkeypatch)
    try:
        with pytest.raises(c.OutsideEnvelope, match="File"):
            supervisor.delegate(hb.TaskBundle(goal="edit"), workspace=str(area.workspace),
                                scope=_envelope(area), expires_in=3600)
        assert S.active() == []
        assert supervisor._scoped_gateway is None
        assert supervisor.state == hb.DISCONNECTED      # nothing was started
    finally:
        supervisor.stop()


def test_a_delegation_under_an_envelope_is_refused_when_the_profile_names_no_other_toolsets(area, tmp_path, monkeypatch):
    _profile(tmp_path, monkeypatch, "toolsets: [file, terminal]\n")
    supervisor = _supervisor(area.root, monkeypatch)
    try:
        with pytest.raises(c.OutsideEnvelope, match="refusing to guess"):
            supervisor.delegate(hb.TaskBundle(goal="edit"), workspace=str(area.workspace),
                                scope=_envelope(area), expires_in=3600)
        assert S.active() == []
        assert supervisor._scoped_gateway is None
        assert supervisor.state == hb.DISCONNECTED      # nothing was started
    finally:
        supervisor.stop()


def test_a_profile_that_cannot_be_verified_delegates_nothing_under_an_envelope(area, tmp_path, monkeypatch):
    _profile(tmp_path, monkeypatch, "toolsets: [unclosed\n")
    supervisor = _supervisor(area.root, monkeypatch)
    try:
        with pytest.raises(c.OutsideEnvelope, match="cannot be verified"):
            supervisor.delegate(hb.TaskBundle(goal="edit"), workspace=str(area.workspace),
                                scope=_envelope(area), expires_in=3600)
    finally:
        supervisor.stop()


def test_the_delegate_tool_reports_the_refusal_not_a_crash(area, tmp_path, monkeypatch):
    from friday import policy as P
    from friday.tools import hermes_control as hc

    class Refusing:
        log = None

        def delegate(self, bundle, **kwargs):
            raise c.OutsideEnvelope("the profile's tools cannot be verified")

    class FakeMCP:
        tools: dict = {}

        def tool(self):
            def register(fn):
                self.tools[fn.__name__] = fn
                return fn
            return register

    monkeypatch.setattr(hc, "_engine", P.PolicyEngine(autonomy=P.FULL))
    mcp = FakeMCP()
    hc.register(mcp)
    hc.configure(Refusing())
    try:
        out = mcp.tools["hermes_delegate"](goal="edit", wait_seconds=0)
    finally:
        hc.configure(None)
    assert out["status"] == "failed" and out["error"].startswith("ENVELOPE:"), out


# -- Hermes' own seams: tools.list names the tools, a fail-closed hook vetoes a call ---------

def _toolsets(*rows) -> str:
    return json.dumps([{"name": name, "description": "", "tool_count": len(tools),
                        "enabled": enabled, "tools": list(tools)}
                       for name, tools, enabled in rows])


def test_a_tool_that_tools_list_puts_in_hermes_file_toolset_is_refused_though_nothing_else_names_it(area, tmp_path, monkeypatch):
    assert "fs_swap" not in hb._NATIVE_FILE_TOOLS       # the constant has never heard of it
    _profile(tmp_path, monkeypatch, "toolsets: [file, terminal, web]\n")
    monkeypatch.setenv("FAKE_HERMES_IGNORE_TOOLSETS", "1")
    monkeypatch.setenv("FAKE_HERMES_TOOLS_LIST", _toolsets(
        ("file", ["fs_swap"], True), ("web", ["web_search"], True)))
    calls = tmp_path / "calls.log"
    monkeypatch.setenv("FAKE_HERMES_CALL_LOG", str(calls))
    supervisor = _supervisor(area.root, monkeypatch, hang=False)
    try:
        with pytest.raises(c.OutsideEnvelope, match="fs_swap"):
            supervisor.delegate(hb.TaskBundle(goal="edit"), workspace=str(area.workspace),
                                scope=_envelope(area), expires_in=3600)
        assert "tools.list" in _calls(calls)
        assert "prompt.submit" not in _calls(calls)         # the task never reached Hermes
        assert "session.interrupt" in _calls(calls)
        [run] = supervisor.log.recent(1)
        assert run["failure_kind"] == hb.HermesSupervisor.ENVELOPE_BREACHED
        assert S.active() == []
        assert supervisor._scoped_unsound
    finally:
        supervisor.stop()


def test_a_native_toolset_that_tools_list_reports_disabled_holds_nothing(area, tmp_path, monkeypatch):
    _profile(tmp_path, monkeypatch, "toolsets: [file, terminal, web]\n")
    monkeypatch.setenv("FAKE_HERMES_TOOLS_LIST", _toolsets(
        ("file", ["read_file"], False), ("terminal", ["terminal"], False),
        ("mcp-friday_exec", ["mcp__friday_exec__bridge_read_file"], True)))
    supervisor = _supervisor(area.root, monkeypatch, hang=False)
    try:
        run = supervisor.delegate(hb.TaskBundle(goal="scoped"), workspace=str(area.workspace),
                                  scope=_envelope(area), expires_in=3600, wait=True,
                                  turn_timeout=30)["work_run_id"]
        assert supervisor.log.get(run)["status"] == hb.COMPLETE
        assert supervisor._scoped_unsound == ""
    finally:
        supervisor.stop()


@pytest.mark.parametrize("result, expected", [
    ({"toolsets": [{"name": "file", "enabled": True, "tools": ["read_file", "fs_swap"]},
                   {"name": "Terminal", "enabled": True, "tools": ["terminal"]},
                   {"name": "web", "enabled": True, "tools": ["web_search"]}]},
     ("read_file", "fs_swap", "terminal")),
    ({"toolsets": [{"name": "file", "enabled": False, "tools": ["read_file"]}]}, ()),
    ({"toolsets": [{"name": "file", "tools": ["read_file"]}]}, ("read_file",)),   # silent on enabled: held
    ({"toolsets": [{"name": "file", "enabled": True, "tools": [3, None, "patch"]}]}, ("patch",)),
    ({"toolsets": []}, ()), ({"toolsets": None}, ()), ({}, ()), (None, ()), ("file", ()),
])
def test_the_native_tools_tools_list_reports_are_those_of_the_enabled_file_and_terminal_toolsets(result, expected):
    assert hb.native_tools_in_tools_list(result) == expected


def test_a_scoped_session_is_refused_when_hermes_safe_mode_would_switch_the_hook_off(area, tmp_path, monkeypatch):
    dump = tmp_path / "env.jsonl"
    monkeypatch.setenv("FAKE_HERMES_ENV_DUMP", str(dump))
    monkeypatch.setenv("HERMES_SAFE_MODE", "1")
    supervisor = _supervisor(area.root, monkeypatch, hang=False)
    try:
        with pytest.raises(c.OutsideEnvelope, match="HERMES_SAFE_MODE"):
            supervisor.delegate(hb.TaskBundle(goal="edit"), workspace=str(area.workspace),
                                scope=_envelope(area), expires_in=3600)
        assert not dump.exists()                            # no gateway was started
        assert S.active() == []
    finally:
        supervisor.stop()


def test_safe_mode_set_for_the_gateway_alone_also_refuses_a_scoped_session(area, monkeypatch):
    supervisor = _supervisor(area.root, monkeypatch, hang=False,
                             extra_env={"HERMES_SAFE_MODE": "true"})
    try:
        with pytest.raises(c.OutsideEnvelope, match="HERMES_SAFE_MODE"):
            supervisor.delegate(hb.TaskBundle(goal="edit"), workspace=str(area.workspace),
                                scope=_envelope(area), expires_in=3600)
    finally:
        supervisor.stop()


def test_safe_mode_does_not_stop_a_delegation_with_no_envelope(area, monkeypatch):
    monkeypatch.setenv("HERMES_SAFE_MODE", "1")
    supervisor = _supervisor(area.root, monkeypatch, hang=False)
    try:
        run = supervisor.delegate(hb.TaskBundle(goal="shared"), wait=True,
                                  turn_timeout=30)["work_run_id"]
        assert supervisor.log.get(run)["status"] == hb.COMPLETE
    finally:
        supervisor.stop()


def test_the_scoped_gateway_is_launched_accepting_the_profiles_hooks(area, tmp_path, monkeypatch):
    dump = tmp_path / "env.jsonl"
    monkeypatch.setenv("FAKE_HERMES_ENV_DUMP", str(dump))
    monkeypatch.delenv("HERMES_ACCEPT_HOOKS", raising=False)
    supervisor = _supervisor(area.root, monkeypatch, hang=False)
    try:
        supervisor.delegate(hb.TaskBundle(goal="edit"), workspace=str(area.workspace),
                            scope=_envelope(area), expires_in=3600)
        (env,) = _gateway_envs(dump)
        assert env["HERMES_ACCEPT_HOOKS"] == "1"
    finally:
        supervisor.stop()


def _hook_entries(tmp_path) -> list[dict]:
    import yaml
    config = yaml.safe_load((tmp_path / "hermes-profile" / "config.yaml").read_text(encoding="utf-8"))
    return config["hooks"]["pre_tool_call"]


def test_a_scoped_session_puts_the_fail_closed_envelope_hook_in_the_profile(area, tmp_path, monkeypatch):
    supervisor = _supervisor(area.root, monkeypatch, hang=False)
    try:
        supervisor.delegate(hb.TaskBundle(goal="edit"), workspace=str(area.workspace),
                            scope=_envelope(area), expires_in=3600)
        [entry] = _hook_entries(tmp_path)
        assert entry["fail_closed"] is True                 # Hermes fails open otherwise
        assert "hermes_envelope_hook" in entry["command"]
        assert "matcher" not in entry                       # every tool, not a name pattern
        supervisor.stop()
        supervisor.delegate(hb.TaskBundle(goal="again"), workspace=str(area.workspace),
                            scope=_envelope(area), expires_in=3600)
        assert len(_hook_entries(tmp_path)) == 1            # installed once, not per delegation
    finally:
        supervisor.stop()


def test_a_profile_with_its_own_hooks_is_not_rewritten_and_refuses_the_scoped_session(area, tmp_path, monkeypatch):
    config = "toolsets: [web]\nhooks:\n  post_tool_call:\n    - command: audit\n"
    _profile(tmp_path, monkeypatch, config)
    supervisor = _supervisor(area.root, monkeypatch, hang=False)
    try:
        with pytest.raises(c.OutsideEnvelope, match="hooks"):
            supervisor.delegate(hb.TaskBundle(goal="edit"), workspace=str(area.workspace),
                                scope=_envelope(area), expires_in=3600)
        assert (tmp_path / "hermes-profile" / "config.yaml").read_text(encoding="utf-8") == config
    finally:
        supervisor.stop()


# -- the hook itself: the veto Hermes asks FRIDAY for before any tool runs -----------------

def _run_hook(stdin: str):
    from friday import hermes_envelope_hook as hook
    return subprocess.run([sys.executable, hook.__file__], input=stdin, capture_output=True,
                          text=True, timeout=60, env=dict(os.environ))


def _call(tool: str, **tool_input) -> str:
    return json.dumps({"hook_event_name": "pre_tool_call", "tool_name": tool,
                       "tool_input": tool_input, "session_id": "s", "cwd": "."})


def test_the_hook_allows_everything_while_no_delegation_holds_the_bridge(area):
    done = _run_hook(_call("write_file", path=str(area.elsewhere / "x.txt")))
    assert done.returncode == 0, done.stderr


@pytest.mark.parametrize("tool", sorted(hb._NATIVE_FILE_TOOLS))
def test_the_hook_exits_2_for_a_hermes_file_or_terminal_tool_under_an_envelope(held, tool):
    done = _run_hook(_call(tool, path=str(held.elsewhere / "x.txt")))
    assert done.returncode == 2
    assert json.loads(done.stdout)["action"] == "block"


def test_the_hook_exits_2_for_a_path_outside_the_envelope_and_0_inside_it(held):
    outside = _run_hook(_call("some_new_tool", path=str(held.elsewhere / "other.txt")))
    assert outside.returncode == 2
    assert json.loads(outside.stdout)["message"]
    assert _run_hook(_call("web_search", query="no path at all")).returncode == 0


def test_the_hook_lets_a_path_inside_the_envelope_through(held):
    # in this process: the jail the area fixture set is not the one a child process builds
    from friday import hermes_envelope_hook as hook
    assert hook.run(_call("some_new_tool", path=str(held.workspace / "mine.txt"))) == (0, "")


def test_the_hook_lets_the_exec_bridge_through_because_the_bridge_judges_its_own_paths(held):
    done = _run_hook(_call("mcp__friday_exec__bridge_read_file", path=str(held.elsewhere / "other.txt")))
    assert done.returncode == 0


def test_the_hook_blocks_when_the_registry_cannot_be_read(area):
    S._folder().mkdir(parents=True, exist_ok=True)
    (S._folder() / "broken.json").write_text("{not json", encoding="utf-8")
    assert _run_hook(_call("web_search", query="q")).returncode == 2


@pytest.mark.parametrize("stdin", ["", "not json", "[]", "null", "{}", '{"tool_name": 7}'])
def test_a_hook_that_cannot_make_sense_of_its_input_blocks_instead_of_crashing_open(held, stdin):
    assert _run_hook(stdin).returncode == 2


def test_a_hook_that_crashes_inside_the_decision_blocks(held, monkeypatch):
    from friday import hermes_envelope_hook as hook
    monkeypatch.setattr(S, "active", lambda: 1 / 0)
    code, out = hook.run(_call("web_search", query="q"))
    assert code == 2 and json.loads(out)["action"] == "block"
