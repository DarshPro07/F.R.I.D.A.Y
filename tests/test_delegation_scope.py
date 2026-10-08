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
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from friday import contracts as c
from friday import delegation_scope as S
from friday import hermes_bridge as hb
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


@pytest.fixture
def area(tmp_path, monkeypatch):
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
    # A delegation under an envelope runs on a gateway started with the
    # profile's toolsets, so the profile has to name some.
    _profile(tmp_path, monkeypatch, "toolsets: [web]\n")
    yield here
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


# -- a program is not confined by file roots, so under an envelope none runs ----------------
# (owner ruling 2026-10-03c: commands come back only behind an OS sandbox, EXEC-SBX-001)

def _python_writes(target: Path) -> str:
    return f'python -c "open(r\'{target}\', \'w\').write(\'x\')"'


def _this_interpreter_writes(target: Path) -> str:
    return f'"{sys.executable}" -c "open(r\'{target}\', \'w\').write(\'x\')"'


def _node_writes(target: Path) -> str:
    return f'node -e "require(\'fs\').writeFileSync(\'{target.as_posix()}\', \'x\')"'


@pytest.mark.parametrize("command_for", [_python_writes, _this_interpreter_writes, _node_writes],
                         ids=["python", "this-interpreter", "node"])
def test_an_interpreter_cannot_write_outside_the_envelope_through_the_bridge(held, command_for):
    escaped = held.elsewhere / "escaped.txt"
    text = bridge.bridge_run_command(command_for(escaped))
    assert _refused(text), text
    assert not escaped.exists()
    assert bridge._env is None, "nothing was launched, not even a contained environment"


@pytest.mark.parametrize("command", [
    "git status --short", "pytest -q", "python --version", "node --version",
    "powershell -NoProfile -Command ls", "cmd /c dir"],
    ids=["git", "pytest", "python", "node", "powershell", "cmd"])
def test_no_program_at_all_runs_for_a_delegation_under_an_envelope(held, command):
    assert _refused(bridge.bridge_run_command(command))
    assert bridge._env is None


def test_the_refusal_names_no_program_that_would_get_round_it(held):
    # the forbidden-shell refusal ("run programs directly (git, python, node...)")
    # is a different message; a delegation under an envelope must get this one
    for command in ("powershell -c ls", "python -c pass", "git status"):
        text = bridge.bridge_run_command(command)
        assert _refused(text)
        assert "envelope" in text
        assert not re.search(r"\b(python|node|pytest|git|interpreters?|programs? directly)\b",
                             text, re.IGNORECASE), text


def test_a_refused_command_is_logged_with_the_delegation_that_was_refused(held, caplog):
    with caplog.at_level(logging.WARNING, logger="friday.delegation_scope"):
        assert _refused(bridge.bridge_run_command("python --version"))
    assert any("delegation_scope.refused" in r.getMessage() and "(command)" in r.getMessage()
               for r in caplog.records)
    line = json.loads((Path(os.environ[S.ENV_DIR]) / S.REFUSALS).read_text().splitlines()[-1])
    assert line["runs"] == "OBJ-S" and line["path"] == "(command)"


def test_files_stay_available_to_the_delegation_while_commands_are_off(held):
    assert bridge.bridge_write_file("made.txt", "by the file tool").startswith("wrote")
    assert (held.workspace / "made.txt").read_text(encoding="utf-8") == "by the file tool"
    assert "by the file tool" in bridge.bridge_read_file("made.txt")


def test_with_no_delegation_the_bridge_still_runs_commands_in_the_profile_workspace(area):
    out = bridge.bridge_run_command('python -c "import os; print(os.getcwd())"')
    assert not _refused(out), out
    assert str(area.profile.resolve()).lower() in out.lower(), out


def test_commands_come_back_when_the_delegation_is_released(area):
    S.register("lease-c", _envelope(area), str(area.workspace))
    assert _refused(bridge.bridge_run_command("python --version"))
    S.release("lease-c")
    out = bridge.bridge_run_command("python --version")
    assert not _refused(out) and "exit=0" in out, out


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
        assert _refused(bridge.bridge_run_command(_python_writes(escaped)))
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
