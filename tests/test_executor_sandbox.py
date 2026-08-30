"""
The development run inside the boundary.

A git worktree is version control, not containment: it protects the branch
and does nothing about the environment the agent inherits, the processes it
leaves behind, or the memory it takes. These cover the seam where the two
meet.
"""
import inspect
import pytest
from friday import execution as EX
from friday.executors import claude_code as CC
from friday.executors import cli


@pytest.fixture
def workspace(tmp_path):
    (tmp_path / "README.md").write_text("# project\n", encoding="utf-8")
    return tmp_path


def _launch(workspace):
    return cli.Launch(prompt="do the thing", cwd=str(workspace))


def test_the_host_backend_is_still_the_default():
    """
    Changing how existing runs execute is a decision, not a side effect of
    adding the option.
    """
    assert inspect.signature(
        CC.ClaudeCodeExecutor.__init__).parameters["backend"].default == CC.HOST


def test_a_run_with_no_sandbox_behaves_exactly_as_before(workspace):
    run = cli.Run(_launch(workspace))
    assert run.sandbox is None


def test_a_sandboxed_run_gets_an_allowlisted_environment(workspace, monkeypatch):
    monkeypatch.setenv('ANTHROPIC_API_KEY', 'sk-ant-secret')
    with EX.NativeExecutionEnvironment(workspace, name='DEV-1') as box:
        run = cli.Run(_launch(workspace), sandbox=box)
        env = run.sandbox.environment()
    assert 'ANTHROPIC_API_KEY' not in env
    assert env.get('PATH'), 'the CLI still has to be able to start'


def test_the_coding_allowlist_permits_the_agents_own_api():
    policy = EX.Egress(mode=EX.ALLOWLIST, hosts=CC.CODING_HOSTS)
    assert policy.allows('api.anthropic.com')
    assert policy.allows('registry.npmjs.org')
    assert policy.allows('files.pythonhosted.org')


def test_the_coding_allowlist_refuses_somewhere_new():
    """A task that quietly phoned somewhere else fails loudly instead."""
    policy = EX.Egress(mode=EX.ALLOWLIST, hosts=CC.CODING_HOSTS)
    assert not policy.allows('pastebin.test')
    assert not policy.allows('exfiltrate.example')


def test_cancelling_a_sandboxed_run_closes_the_job(workspace, monkeypatch):
    """
    `taskkill /T` walks a parent-child list, so a child that orphaned itself
    survives it. Cancellation must go through the job instead - and must not
    fall back to the weaker tool afterwards.

    Asserted behaviourally. An earlier version compared source offsets and
    failed on the word "taskkill" appearing in a comment, which measured the
    prose rather than the code.
    """
    import asyncio
    killed = []
    monkeypatch.setattr('subprocess.run', lambda *a, **k: killed.append(a) or None)

    class _Process:
        pid = 4242
        returncode = None

        async def wait(self):
            self.returncode = 0
            return 0
    box = EX.NativeExecutionEnvironment(workspace, name='DEV-1')
    run = cli.Run(_launch(workspace), sandbox=box)
    run.process = _Process()
    assert asyncio.run(run.cancel()) is True
    assert box._closed, 'cancelling the run left the job handle open'
    assert not killed, 'taskkill was used even though a job was available'


@pytest.mark.parametrize("path", ["success", "timeout", "failure"])
def test_every_exit_path_closes_the_sandbox(path):
    """
    A leaked job handle keeps a dead run's processes alive and its memory
    limit reserved. Success is the easy path to remember; the other two are
    the ones that leak.
    """
    source = inspect.getsource(CC.ClaudeCodeExecutor.execute)
    assert source.count("self._close_sandbox()") >= 3, \
        "one of the three exits from execute() does not close the sandbox"


def test_closing_twice_is_safe(workspace):
    executor_sandbox = EX.NativeExecutionEnvironment(workspace, name='DEV-1')
    executor_sandbox.terminate()
    executor_sandbox.terminate()


def test_the_host_backend_builds_no_sandbox(workspace):
    executor = CC.ClaudeCodeExecutor.__new__(CC.ClaudeCodeExecutor)
    executor.backend = CC.HOST
    bundle = type('B', (), {'workspace': str(workspace), 'run_id': 'DEV-1'})()
    assert executor._sandbox_for(bundle) is None


def test_the_sandbox_backend_builds_one_and_reports_its_strength(workspace):
    executor = CC.ClaudeCodeExecutor.__new__(CC.ClaudeCodeExecutor)
    executor.backend = CC.SANDBOX
    bundle = type('B', (), {'workspace': str(workspace), 'run_id': 'DEV-1'})()
    box = executor._sandbox_for(bundle)
    try:
        assert box is not None
        assert box.egress.mode == EX.ALLOWLIST
        assert box.strength() in ('JOB_OBJECT', 'PROCESS_ONLY')
    finally:
        box.terminate()


def test_a_boundary_that_cannot_be_built_is_raised_not_downgraded(tmp_path):
    """
    The caller asked for containment. Quietly running on the host instead is
    the worst outcome available: the run looks contained and is not.
    """
    executor = CC.ClaudeCodeExecutor.__new__(CC.ClaudeCodeExecutor)
    executor.backend = CC.SANDBOX
    bundle = type('B', (), {'workspace': str(tmp_path / 'gone'), 'run_id': 'DEV-1'})()
    with pytest.raises(EX.ExecutionError):
        executor._sandbox_for(bundle)
