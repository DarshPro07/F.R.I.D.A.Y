"""
The execution boundary, proven rather than described.

The claims worth testing are the ones that would otherwise be comments:
a secret does not cross, a path does not escape, a process does not outlive
the block, and a runaway does not take the machine.
"""
import os
import subprocess
import sys
import time

import pytest

from friday import sandbox as S


@pytest.fixture
def workspace(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "main.py").write_text("print('hello')\n",
                                              encoding="utf-8")
    return tmp_path


# --- the environment ------------------------------------------------------

def test_a_secret_does_not_cross_the_boundary(workspace, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-secret")
    monkeypatch.setenv("GITHUB_TOKEN", "ghp_secret")
    with S.Sandbox(workspace, name="T") as box:
        env = box.environment()
    assert "ANTHROPIC_API_KEY" not in env
    assert "GITHUB_TOKEN" not in env


def test_the_environment_is_an_allowlist_not_a_denylist(workspace, monkeypatch):
    """
    A denylist is a list of the secrets somebody remembered. This repository
    adds providers faster than such a list gets updated, so an unknown
    variable must not cross by default.
    """
    monkeypatch.setenv("SOME_FUTURE_PROVIDER_KEY", "shhh")
    with S.Sandbox(workspace, name="T") as box:
        assert "SOME_FUTURE_PROVIDER_KEY" not in box.environment()


def test_the_process_still_gets_what_it_needs_to_run(workspace):
    with S.Sandbox(workspace, name="T") as box:
        env = box.environment()
    assert env.get("PATH"), "a process with no PATH does not run at all"


def test_an_inherited_proxy_does_not_survive(workspace, monkeypatch):
    """
    An inherited proxy is a hole straight through an egress policy that only
    inspects URLs.
    """
    monkeypatch.setenv("HTTPS_PROXY", "http://corporate.proxy:8080")
    with S.Sandbox(workspace, name="T",
                   egress=S.Egress(mode=S.ALLOW_ALL)) as box:
        assert box.environment().get("HTTPS_PROXY") != "http://corporate.proxy:8080"


def test_injected_credentials_reach_the_process(workspace):
    with S.Sandbox(workspace, name="T",
                   credentials={"TASK_TOKEN": "abc123xyz789"}) as box:
        assert box.environment()["TASK_TOKEN"] == "abc123xyz789"


def test_injected_credentials_are_redacted_from_output(workspace):
    with S.Sandbox(workspace, name="T",
                   credentials={"TASK_TOKEN": "abc123xyz789"}) as box:
        assert "[redacted]" in box.redacted("leaked abc123xyz789 here")
        assert "abc123xyz789" not in box.redacted("leaked abc123xyz789 here")


def test_credentials_are_dropped_when_the_block_ends(workspace):
    box = S.Sandbox(workspace, name="T", credentials={"TASK_TOKEN": "abc123xyz789"})
    with box:
        pass
    assert box._credentials == {}, "a closed sandbox still held a secret"


def test_the_report_names_credentials_without_carrying_them(workspace):
    with S.Sandbox(workspace, name="T",
                   credentials={"TASK_TOKEN": "abc123xyz789"}) as box:
        report = box.report()
    assert report["credentials_injected"] == ["TASK_TOKEN"]
    assert "abc123xyz789" not in repr(report)


# --- egress policy --------------------------------------------------------

def test_deny_all_is_the_default():
    assert S.Egress().mode == S.DENY_ALL
    assert not S.Egress().allows("pypi.org")


def test_an_allowlist_permits_only_what_it_names():
    policy = S.Egress(mode=S.ALLOWLIST, hosts=("pypi.org", "registry.npmjs.org"))
    assert policy.allows("pypi.org")
    assert policy.allows("PyPI.org"), "host matching is case-insensitive"
    assert not policy.allows("evil.test")


def test_a_leading_dot_covers_subdomains():
    policy = S.Egress(mode=S.ALLOWLIST, hosts=(".npmjs.org",))
    assert policy.allows("registry.npmjs.org")
    assert policy.allows("npmjs.org")
    assert not policy.allows("npmjs.org.evil.test")


def test_an_empty_allowlist_is_refused_rather_than_silently_denying():
    """
    `ALLOWLIST` with no hosts behaves exactly like `DENY_ALL`, which makes it
    a policy that reads as permissive and behaves as closed. Say which.
    """
    with pytest.raises(ValueError):
        S.Egress(mode=S.ALLOWLIST, hosts=())


def test_an_unknown_mode_is_refused():
    with pytest.raises(ValueError):
        S.Egress(mode="MOSTLY")


def test_development_defaults_to_no_network(workspace):
    with S.for_development(workspace, name="DEV-1") as box:
        assert box.egress.mode == S.DENY_ALL


def test_development_with_named_hosts_is_an_allowlist(workspace):
    with S.for_development(workspace, name="DEV-1", network=True,
                           hosts=("pypi.org",)) as box:
        assert box.egress.mode == S.ALLOWLIST
        assert box.egress.allows("pypi.org")


# --- the filesystem -------------------------------------------------------

def test_a_path_cannot_climb_out(workspace):
    with S.Sandbox(workspace, name="T") as box:
        with pytest.raises(S.SandboxError):
            box.resolve("../../etc/passwd")


def test_an_absolute_path_cannot_reach_out(workspace):
    with S.Sandbox(workspace, name="T") as box:
        with pytest.raises(S.SandboxError):
            box.resolve("C:/Windows/System32/drivers/etc/hosts")


@pytest.mark.parametrize("path", [
    "C:/Windows/System32/drivers/etc/hosts",   # Windows drive, forward slashes
    "C:\\Windows\\win.ini",                    # Windows drive, backslashes
    "/etc/passwd",                             # POSIX root
    "//server/share/secret",                   # UNC
])
def test_an_absolute_path_is_refused_on_every_host(workspace, path):
    """Both flavours, on both hosts. `workspace / "C:/Windows/x"` on Linux is
    a RELATIVE join that lands inside the workspace, so the containment
    check alone passed the exact input the Windows job refused (2026-09-05).
    """
    with S.Sandbox(workspace, name="T") as box:
        with pytest.raises(S.SandboxError, match="absolute"):
            box.resolve(path)


def test_reading_and_writing_inside_is_fine(workspace):
    with S.Sandbox(workspace, name="T") as box:
        box.write("notes/todo.txt", "one thing")
        assert box.read("notes/todo.txt") == "one thing"
        assert "src\\main.py" in box.listing() or "src/main.py" in box.listing()


def test_export_copies_one_named_file(workspace, tmp_path):
    out = tmp_path / "artifacts"
    with S.Sandbox(workspace, name="T") as box:
        box.write("dist/app.js", "console.log(1)")
        copied = box.export("dist/app.js", into=out)
    assert copied.read_text(encoding="utf-8") == "console.log(1)"


def test_export_refuses_a_path_outside_the_sandbox(workspace, tmp_path):
    with S.Sandbox(workspace, name="T") as box:
        with pytest.raises(S.SandboxError):
            box.export("../escape.txt", into=tmp_path / "artifacts")


def test_export_refuses_something_too_large(workspace, tmp_path):
    limits = S.Limits(artifact_bytes=16)
    with S.Sandbox(workspace, name="T", limits=limits) as box:
        box.write("big.bin", "x" * 100)
        with pytest.raises(S.SandboxError):
            box.export("big.bin", into=tmp_path / "artifacts")


def test_a_missing_workspace_is_refused_before_anything_runs(tmp_path):
    with pytest.raises(S.SandboxError):
        S.Sandbox(tmp_path / "nope", name="T")


# --- running --------------------------------------------------------------

def test_a_command_runs_in_the_workspace(workspace):
    with S.Sandbox(workspace, name="T") as box:
        result = box.run([sys.executable, "-c",
                          "import os; print(os.getcwd())"])
    assert result.ok
    assert str(workspace).lower() in result.stdout.strip().lower()


def test_a_failing_command_reports_its_code(workspace):
    with S.Sandbox(workspace, name="T") as box:
        result = box.run([sys.executable, "-c", "raise SystemExit(3)"])
    assert result.exit_code == 3
    assert not result.ok


def test_a_command_that_hangs_is_killed(workspace):
    with S.Sandbox(workspace, name="T") as box:
        result = box.run([sys.executable, "-c",
                          "import time; time.sleep(30)"], timeout=2)
    assert result.timed_out
    assert not result.ok


def test_a_secret_is_not_visible_to_the_process(workspace, monkeypatch):
    """The end-to-end version: not just absent from a dict, absent in there."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-do-not-leak")
    with S.Sandbox(workspace, name="T") as box:
        result = box.run([sys.executable, "-c",
                          "import os; print(os.environ.get('ANTHROPIC_API_KEY', 'ABSENT'))"])
    assert result.stdout.strip() == "ABSENT"


def test_output_carrying_an_injected_credential_is_redacted(workspace):
    with S.Sandbox(workspace, name="T",
                   credentials={"TASK_TOKEN": "tok-abcdefgh12345"}) as box:
        result = box.run([sys.executable, "-c",
                          "import os; print(os.environ['TASK_TOKEN'])"])
    assert "tok-abcdefgh12345" not in result.stdout
    assert "[redacted]" in result.stdout


def test_a_closed_sandbox_refuses_to_run_anything(workspace):
    box = S.Sandbox(workspace, name="T")
    with box:
        pass
    with pytest.raises(S.SandboxError):
        box.run([sys.executable, "-c", "print(1)"])


def test_terminate_is_idempotent(workspace):
    box = S.Sandbox(workspace, name="T")
    with box:
        pass
    box.terminate()          # must not raise on the second call


# --- the guarantee that makes it a boundary -------------------------------

@pytest.mark.skipif(not S.WINDOWS, reason="job objects are a Windows thing")
def test_the_job_object_is_available_here(workspace):
    """
    If this fails the whole module degrades to PROCESS_ONLY, which is a real
    difference in what is being promised - so it is asserted, not assumed.
    """
    with S.Sandbox(workspace, name="T") as box:
        assert box.strength() == "JOB_OBJECT"


@pytest.mark.skipif(not S.WINDOWS, reason="job objects are a Windows thing")
def test_a_process_that_orphans_itself_still_dies_with_the_sandbox(workspace):
    """
    The property `taskkill /T` cannot give.

    A child that detaches from its parent escapes a parent-child walk. It
    cannot escape a job: membership is inherited and cannot be renounced, so
    closing the handle kills it too.

    The grandchild here writes a file, then sleeps far longer than the test.
    If the job works, it is dead before it can write the second file.
    """
    marker = workspace / "started.txt"
    survived = workspace / "survived.txt"
    script = (
        "import pathlib, time, sys;"
        f"pathlib.Path(r'{marker}').write_text('up');"
        "time.sleep(20);"
        f"pathlib.Path(r'{survived}').write_text('still here')"
    )

    box = S.Sandbox(workspace, name="ORPHAN")
    with box:
        process = subprocess.Popen(
            [sys.executable, "-c", script], cwd=str(workspace),
            creationflags=subprocess.CREATE_NO_WINDOW
            | subprocess.DETACHED_PROCESS)
        assert box.job.adopt(process.pid), "the process was never sandboxed"
        for _ in range(100):                       # wait for it to be alive
            if marker.exists():
                break
            time.sleep(0.05)
        assert marker.exists(), "the child never started; test proves nothing"

    # The block has ended. The kernel should have killed it.
    for _ in range(60):
        if process.poll() is not None:
            break
        time.sleep(0.05)

    assert process.poll() is not None, \
        "a detached process outlived the sandbox that contained it"
    assert not survived.exists(), \
        "the process lived long enough to finish its work after cleanup"


@pytest.mark.skipif(not S.WINDOWS, reason="job objects are a Windows thing")
def test_a_fork_bomb_hits_the_process_limit_not_the_machine(workspace):
    """
    Bounded, and bounded by the kernel. The point is not that the command
    fails - it is that the machine is still usable while it does.
    """
    limits = S.Limits(processes=3, seconds=30)
    spawn = (
        "import subprocess, sys;"
        "[subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(5)'])"
        " for _ in range(25)]"
    )
    with S.Sandbox(workspace, name="BOMB", limits=limits) as box:
        result = box.run([sys.executable, "-c", spawn], timeout=20)

    # Either the spawner died hitting the limit, or it survived but the
    # kernel refused most of the children. Both are the limit working; what
    # must not happen is 25 processes existing.
    assert not result.ok or "error" in result.stderr.lower() \
        or result.exit_code != 0, \
        "the process limit did not bite"


def test_the_report_says_what_it_actually_guarantees(workspace):
    with S.Sandbox(workspace, name="T") as box:
        box.run([sys.executable, "-c", "print(1)"])
        report = box.report()
    assert report["strength"] in ("JOB_OBJECT", "PROCESS_ONLY")
    assert report["egress"] == "DENY_ALL"
    assert report["commands"][0]["exit_code"] == 0
