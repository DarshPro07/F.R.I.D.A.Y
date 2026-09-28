"""
A coding worker is compiled from its run's execution envelope (PRD v7
SEC-005, Slice 2 step 8).

The capability runtime holds FRIDAY's own actions to the envelope. A coding
CLI is a separate process with its own tools, so the envelope has to become
that process's own permissions: the launch is compiled from it, not merely
described to the model. These drive the real `launch_for` and `execute` and
read the real argv; only the CLI process itself is faked.
"""
from __future__ import annotations

import asyncio
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path, PurePosixPath

import pytest

from friday import contracts as c
from friday.executors import brokers as B
from friday.executors import claude_code as CC
from friday.executors import cli


def _stamp(seconds: float) -> str:
    return (datetime.now(timezone.utc) + timedelta(seconds=seconds)).isoformat()


def _envelope(workspace: Path, *, read=(), write=None, network=None,
              provenance=c.PERSON, seconds=3600.0, tool_call_limit=0):
    return c.Envelope(
        run_id="OBJ-TEST", provenance=provenance, attended=True,
        cwd=str(workspace),
        read_roots=tuple(str(r) for r in read),
        write_roots=tuple(str(w) for w in ((workspace,) if write is None else write)),
        network=network or c.NetworkScope(),
        allowed_capabilities=(c.ANY_CAPABILITY,),
        tool_call_limit=tool_call_limit,
        expires_at=_stamp(seconds))


def _rule_path(path: Path) -> str:
    """The CLI's own spelling of an absolute path in a permission rule."""
    resolved = Path(path).resolve()
    if resolved.drive:
        rest = PurePosixPath(*resolved.parts[1:])
        return f"//{resolved.drive[0].lower()}/{rest}"
    return f"/{resolved.as_posix()}"


@pytest.fixture
def workspace(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "README.md").write_text("# project\n", encoding="utf-8")
    return ws


@pytest.fixture
def executor(tmp_path):
    from friday.store import Store
    return CC.ClaudeCodeExecutor(Store(tmp_path / "ada.sqlite3"))


def _bundle(workspace, **extra):
    return CC.TaskBundle(goal="fix the failing test", workspace=str(workspace),
                         isolate=False, **extra)


def _argv(launch, monkeypatch):
    monkeypatch.setattr(cli, "claude_path", lambda: "claude")
    return launch.argv()


def _flag_values(argv, flag):
    """Every value after a variadic flag, up to the next flag."""
    if flag not in argv:
        return []
    out = []
    for token in argv[argv.index(flag) + 1:]:
        if token.startswith("--"):
            break
        out.append(token)
    return out


# -- no envelope: nothing changes --------------------------------------------

def test_without_an_envelope_the_launch_is_what_it_was(executor, workspace):
    launch = executor.launch_for(_bundle(workspace), profile=B.BUILD)
    assert launch.permission_mode == cli.DEFAULT_PERMISSION_MODE
    assert launch.allowed_tools == B.BUILD.allowed
    assert launch.disallowed_tools == B.BUILD.disallowed
    assert launch.add_dirs == ()


# -- refusals before anything starts -----------------------------------------

def test_an_expired_envelope_launches_nothing(executor, workspace, monkeypatch):
    started = []
    monkeypatch.setattr(cli, "available", lambda: True)
    monkeypatch.setattr(cli, "Run", lambda *a, **k: started.append(a) or None)
    result = asyncio.run(executor.execute(
        _bundle(workspace), profile=B.BUILD,
        envelope=_envelope(workspace, seconds=-1)))
    assert result.status == c.NOT_PERMITTED
    assert "expired" in result.error
    assert started == [], "a process was built for an expired envelope"


def test_the_workspace_must_lie_inside_the_envelope(executor, workspace, tmp_path):
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    with pytest.raises(B.OutsideEnvelope, match="outside"):
        executor.launch_for(_bundle(workspace), profile=B.EXPLORE,
                            envelope=_envelope(workspace, read=[elsewhere], write=()))


def test_a_writing_run_needs_its_workspace_in_a_write_root(executor, workspace):
    read_only = _envelope(workspace, read=[workspace], write=())
    with pytest.raises(B.OutsideEnvelope, match="write root"):
        executor.launch_for(_bundle(workspace), profile=B.BUILD, envelope=read_only)
    # Reading the same place is inside the envelope.
    executor.launch_for(_bundle(workspace), profile=B.EXPLORE, envelope=read_only)


# -- what the CLI is told ------------------------------------------------------

def test_anything_the_envelope_does_not_allow_is_denied_not_asked(
        executor, workspace, monkeypatch):
    """`dontAsk`: a call no rule allows is refused by the CLI itself instead
    of waiting on a prompt nobody can answer."""
    launch = executor.launch_for(_bundle(workspace), profile=B.BUILD,
                                 envelope=_envelope(workspace))
    argv = _argv(launch, monkeypatch)
    assert argv[argv.index("--permission-mode") + 1] == "dontAsk"


def test_file_tools_are_held_to_the_roots_not_opened_everywhere(
        executor, workspace, tmp_path, monkeypatch):
    docs = tmp_path / "docs"
    docs.mkdir()
    launch = executor.launch_for(
        _bundle(workspace), profile=B.BUILD,
        envelope=_envelope(workspace, read=[docs], write=[workspace]))
    argv = _argv(launch, monkeypatch)
    allowed = _flag_values(argv, "--allowedTools")
    # A bare file-tool rule matches every path on the machine.
    for bare in ("Read", "Edit", "Write", "NotebookEdit", "Glob", "Grep"):
        assert bare not in allowed, f"bare {bare} would reach outside the roots"
    assert f"Edit({_rule_path(workspace)}/**)" in allowed
    assert not any(rule.startswith("Edit(") and _rule_path(docs) in rule
                   for rule in allowed), "a read root became writable"
    assert str(docs.resolve()) in _flag_values(argv, "--add-dir")
    settings = json.loads(argv[argv.index("--settings") + 1])
    assert settings["permissions"]["blockReadsOutsideWorkingDirectories"] is True
    assert settings["disableAllHooks"] is True


def test_an_exploring_run_under_an_envelope_has_no_file_writers(
        executor, workspace, monkeypatch):
    launch = executor.launch_for(_bundle(workspace), profile=B.EXPLORE,
                                 envelope=_envelope(workspace))
    argv = _argv(launch, monkeypatch)
    assert not any(r.startswith("Edit(") for r in _flag_values(argv, "--allowedTools"))
    for tool in ("Write", "Edit", "NotebookEdit"):
        assert tool in _flag_values(argv, "--disallowedTools")


def test_a_task_cannot_widen_the_roots_it_was_given(
        executor, workspace, tmp_path, monkeypatch):
    """The bundle's own paths are the plan's words; the envelope decides."""
    outside = tmp_path / "outside"
    outside.mkdir()
    launch = executor.launch_for(
        _bundle(workspace, allowed_paths=(str(outside),)), profile=B.BUILD,
        envelope=_envelope(workspace))
    argv = _argv(launch, monkeypatch)
    assert str(outside.resolve()) not in _flag_values(argv, "--add-dir")
    assert not any(_rule_path(outside) in r for r in _flag_values(argv, "--allowedTools"))


@pytest.mark.parametrize("scope, allowed, denied", [
    (c.NetworkScope(), (), ("WebFetch", "WebSearch")),
    (c.NetworkScope(mode=c.NET_PUBLIC_WEB_READ, expires_at=_stamp(600)),
     ("WebFetch", "WebSearch"), ()),
    (c.NetworkScope(mode=c.NET_SELECTED_HOSTS, hosts=(".example.com", "api.test.org"),
                    expires_at=_stamp(600)),
     ("WebFetch(domain:example.com)", "WebFetch(domain:*.example.com)",
      "WebFetch(domain:api.test.org)"), ("WebSearch",)),
    (c.NetworkScope(mode=c.NET_SELECTED_HOSTS, hosts=("api.test.org",),
                    expires_at=_stamp(-5)),
     (), ("WebFetch", "WebSearch")),
    (c.NetworkScope(mode=c.NET_SECURITY_TARGETS, hosts=("10.0.0.5",),
                    expires_at=_stamp(600)),
     (), ("WebFetch", "WebSearch")),
], ids=["none", "public", "selected", "expired-scope", "security-targets"])
def test_web_tools_follow_the_network_scope(executor, workspace, monkeypatch,
                                            scope, allowed, denied):
    launch = executor.launch_for(_bundle(workspace), profile=B.EXPLORE,
                                 envelope=_envelope(workspace, network=scope))
    argv = _argv(launch, monkeypatch)
    allow = _flag_values(argv, "--allowedTools")
    deny = _flag_values(argv, "--disallowedTools")
    for rule in allowed:
        assert rule in allow
    for rule in denied:
        assert rule in deny
    if scope.mode != c.NET_PUBLIC_WEB_READ:
        assert "WebFetch" not in allow, "a bare WebFetch allows every host"


def test_read_material_gets_no_shell(executor, workspace, monkeypatch):
    launch = executor.launch_for(
        _bundle(workspace), profile=B.BUILD,
        envelope=_envelope(workspace, provenance=c.READ_MATERIAL))
    argv = _argv(launch, monkeypatch)
    allow = _flag_values(argv, "--allowedTools")
    assert not any(r.startswith(("Bash(", "PowerShell(")) for r in allow)
    assert {"Bash", "PowerShell"} <= set(_flag_values(argv, "--disallowedTools"))


# -- while it runs -------------------------------------------------------------

class _FakeRun:
    """The CLI process, faked: yields the given events, records what it was
    asked, and can be cancelled."""

    instances: list["_FakeRun"] = []

    def __init__(self, launch, sandbox=None, events=()):
        self.launch = launch
        self.sandbox = sandbox
        self.process = None
        self.session_id = ""
        self.timeout = None
        self.cancelled = False
        self._events = list(events)
        _FakeRun.instances.append(self)

    async def events(self, *, timeout: float = 1800.0):
        self.timeout = timeout
        for event in self._events:
            if self.cancelled:
                return
            yield event

    async def cancel(self):
        self.cancelled = True
        return True


def _fake_cli(monkeypatch, events=()):
    _FakeRun.instances = []
    monkeypatch.setattr(cli, "available", lambda: True)
    monkeypatch.setattr(cli, "Run", lambda launch, sandbox=None: _FakeRun(
        launch, sandbox, events))


def test_the_run_ends_when_its_envelope_does(executor, workspace, monkeypatch):
    _fake_cli(monkeypatch)
    asyncio.run(executor.execute(_bundle(workspace), profile=B.EXPLORE,
                                 envelope=_envelope(workspace, seconds=30),
                                 timeout=1800))
    (run,) = _FakeRun.instances
    assert run.timeout is not None and run.timeout <= 30


def test_the_run_is_stopped_past_its_tool_call_limit(executor, workspace, monkeypatch):
    tool = cli.Event("tool", {"type": "assistant"}, name="Read")
    _fake_cli(monkeypatch, events=[tool, tool, tool, tool])
    result = asyncio.run(executor.execute(
        _bundle(workspace), profile=B.EXPLORE,
        envelope=_envelope(workspace, tool_call_limit=2)))
    (run,) = _FakeRun.instances
    assert run.cancelled, "the run went on past its tool-call limit"
    assert result.status == c.NOT_PERMITTED
    assert "tool-call limit" in result.error


def test_how_strongly_each_part_holds_is_reported(executor, workspace):
    """On the host the CLI's own tools are held; a subprocess it is allowed
    to run (pytest) is not, and the launch says so rather than implying it."""
    launch = executor.launch_for(_bundle(workspace), profile=B.BUILD,
                                 envelope=_envelope(workspace))
    assert launch.strength["tools"] == B.ENFORCED
    assert launch.strength["expiry"] == B.ENFORCED
    assert launch.strength["file_roots"] == B.ADVISORY
    assert launch.strength["network"] == B.ADVISORY


# -- the fabric, and sub-workers ------------------------------------------------

def test_no_capability_reaches_the_fabric_without_carrying_its_envelope():
    """
    Fabric sidecars are shared processes, started once and reused by every
    caller, so a run's envelope cannot shape one's spawn; it holds at the
    call. Today no toolset calls the fabric, so no durable objective reaches
    a provider. A toolset that starts to must hold that call to
    `run.envelope` first (SEC-005 step 8c); this names the file when it does.
    """
    import ast

    import friday.toolsets as toolsets

    reaching = []
    for directory in toolsets.__path__:
        for source in sorted(Path(directory).glob("*.py")):
            tree = ast.parse(source.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                names = []
                if isinstance(node, ast.Import):
                    names = [alias.name for alias in node.names]
                elif isinstance(node, ast.ImportFrom):
                    names = [f"{node.module or ''}.{alias.name}" for alias in node.names]
                if any(".fabric" in f".{name}" for name in names):
                    reaching.append(source.name)
    assert reaching == [], f"these toolsets reach the fabric: {sorted(set(reaching))}"


class _Executor:
    """A coding executor, faked: records the envelope it was handed."""

    def __init__(self):
        self.envelopes = []

    async def execute(self, bundle, *, timeout=0.0, envelope=None, **kwargs):
        self.envelopes.append(envelope)
        return c.ActionResult(run_id=bundle.run_id, tool_id="fake", status=c.SUCCEEDED,
                              started_at=c.now_iso())


def _development(workspace):
    from friday import development as D
    return D.DevelopmentRun(goal="fix the parser", project="p", root=str(workspace))


def test_a_sub_worker_gets_a_child_envelope_no_wider_than_its_parent(workspace, monkeypatch):
    from friday import hermes_team
    from friday import development as D
    monkeypatch.setattr(hermes_team, "plan_team", lambda *a, **k: ("coder", "reviewer"))
    staffed = []
    monkeypatch.setattr(D.DevelopmentRun, "_execute_via_team",
                        lambda self, bundle, team: staffed.append(team) or "team")
    src = workspace / "src"
    src.mkdir()
    run = _development(workspace)
    executor = _Executor()
    parent = _envelope(workspace)
    asyncio.run(run.execute(executor, allowed_paths=("src",), envelope=parent))
    (child,) = executor.envelopes
    assert child is not None and child.run_id == run.run_id
    assert child.write_roots == (os.path.normpath(str(src)),)
    assert child.wider_than(parent) == []
    assert staffed == [], "the team path, which cannot carry an envelope, was taken"


def test_a_sub_worker_cannot_be_given_more_than_its_parent(workspace, tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    run = _development(workspace)
    executor = _Executor()
    result = asyncio.run(run.execute(executor, allowed_paths=(str(outside),),
                                     envelope=_envelope(workspace)))
    assert result is None and executor.envelopes == []
    assert run.error.startswith("ENVELOPE") and "write root" in run.error



# -- path spellings and isolated runs (review, 2026-09-27) -----------------------

@pytest.mark.skipif(os.name != "nt", reason="drive letters are Windows")
def test_a_drive_root_is_spelled_as_the_cli_matches_it():
    drive = Path.cwd().drive
    assert B.rule_path(drive + "\\") == f"//{drive[0].lower()}"


def test_a_network_path_root_is_refused_before_it_is_touched(executor, workspace):
    envelope = _envelope(workspace, read=["\\\\server\\share\\docs"])
    with pytest.raises(B.OutsideEnvelope, match="network path"):
        executor.launch_for(_bundle(workspace), profile=B.EXPLORE, envelope=envelope)


def test_a_linked_root_is_allowed_under_both_its_spellings(executor, tmp_path, monkeypatch):
    real = tmp_path / "real"
    real.mkdir()
    link = tmp_path / "link"
    try:
        if os.name == "nt":
            import _winapi
            _winapi.CreateJunction(str(real), str(link))
        else:
            link.symlink_to(real, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"cannot make a link here: {exc}")
    launch = executor.launch_for(_bundle(link), profile=B.BUILD,
                                 envelope=_envelope(link, write=[link]))
    allowed = _flag_values(_argv(launch, monkeypatch), "--allowedTools")
    assert f"Edit({_rule_path(real)}/**)" in allowed
    if os.name == "nt":
        unresolved = f"//{str(link)[0].lower()}/{Path(str(link)).as_posix()[3:]}"
    else:
        unresolved = f"/{Path(str(link)).as_posix()}"
    assert f"Edit({unresolved}/**)" in allowed


def _git_repo(path: Path) -> Path:
    import subprocess
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q", str(path)], check=True, capture_output=True)
    return path


def test_an_isolated_run_needs_its_worktree_inside_a_write_root(executor, tmp_path):
    repo = _git_repo(tmp_path / "repo")
    sub = repo / "pkg"
    sub.mkdir()
    bundle = CC.TaskBundle(goal="fix it", workspace=str(sub), isolate=True)
    with pytest.raises(B.OutsideEnvelope, match="worktree"):
        executor.launch_for(bundle, profile=B.BUILD, envelope=_envelope(sub, write=[sub]))
    # With the repository's own top level writable, the worktree is inside.
    top = CC.TaskBundle(goal="fix it", workspace=str(repo), isolate=True)
    executor.launch_for(top, profile=B.BUILD, envelope=_envelope(repo, write=[repo]))
