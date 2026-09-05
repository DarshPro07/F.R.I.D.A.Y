"""
The orchestrator, and the thing it exists to prevent.

Five modules were built, tested, and called by nothing. That is decoration,
and it is the same failure as `text_input_callback` calling `prepare_turn`
and nothing else: not broken, not connected. These tests exist mostly to
prove the connections, so the next person who adds a stage cannot quietly
leave it unwired.

The coding agent is injected, so this drives the whole pipeline without an
authenticated CLI and without spending money on every suite run.
"""
import subprocess
import sys
import pytest
from friday import development as D
from friday import evaluation as E
from friday import promotion as P


def git(repo, *args):
    return subprocess.run(["git", "-C", str(repo), *args],
                          capture_output=True, text=True, check=True).stdout


@pytest.fixture
def project(tmp_path):
    repo = tmp_path / "widget"
    (repo / "src").mkdir(parents=True)
    (repo / "tests").mkdir()
    (repo / "src" / "core.py").write_text(
        "class Widget:\n"
        "    def __init__(self, size=1):\n"
        "        self.size = size\n"
        "\n"
        "    def grow(self, by):\n"
        "        self.size += by\n"
        "        return self.size\n"
        "\n"
        "\n"
        "def build(size):\n"
        "    return Widget(size)\n",
        encoding="utf-8")
    (repo / "tests" / "test_core.py").write_text(
        "import sys, pathlib\n"
        "sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))\n"
        "from src.core import Widget, build\n"
        "\n"
        "def test_it_grows():\n"
        "    assert Widget(1).grow(2) == 3\n"
        "\n"
        "def test_it_builds():\n"
        "    assert build(4).size == 4\n",
        encoding="utf-8")
    (repo / ".gitignore").write_text("__pycache__/\n*.pyc\n.pytest_cache/\n",
                                     encoding="utf-8")
    git(repo.parent, "init", "--quiet", str(repo))
    git(repo, "config", "user.email", "t@t.invalid")
    git(repo, "config", "user.name", "t")
    git(repo, "add", "-A")
    git(repo, "commit", "--quiet", "-m", "initial")
    return repo
PYTEST = (sys.executable, '-m', 'pytest', '-q', 'tests/')


class _Agent:
    """A coding agent, as a script. Speaks the existing executor contract."""

    def __init__(self, edit=None, raises=None):
        self.edit = edit
        self.raises = raises
        self.saw = None

    async def execute(self, bundle, *, timeout=1800.0, **kwargs):
        self.saw = bundle
        if self.raises:
            raise self.raises
        if self.edit:
            self.edit(bundle.workspace)
        return type("R", (), {"status": "succeeded", "output": {}})()


@pytest.fixture(autouse=True)
def _own_cache(tmp_path, monkeypatch):
    """Never touch the developer's real graph cache."""
    monkeypatch.setattr("friday.codegraph.graph_path",
                        lambda root: tmp_path / "cache" / "g.json")


def test_it_maps_the_repository(project):
    run = D.for_goal("add a shrink method", project)
    assert run.graph is not None
    assert any(s.name == "grow" for s in run.graph.symbols)


def test_a_project_with_no_code_is_not_mapped_and_still_runs(tmp_path):
    empty = tmp_path / "fresh"
    empty.mkdir()
    (empty / "README.md").write_text("# new\n", encoding="utf-8")

    run = D.for_goal("start the project", empty)
    assert run.graph is None, "an empty repo produced a graph"
    assert run.team is not None, "no graph must not cost the run its team"


def test_a_broken_graph_costs_the_shortcut_not_the_run(project, monkeypatch):
    def _explode(*a, **k):
        raise RuntimeError("disk on fire")

    monkeypatch.setattr("friday.codegraph.ensure", _explode)
    run = D.DevelopmentRun(goal="x", project="widget", root=str(project))
    run.understand()
    assert run.graph is None      # and did not raise


def test_the_context_is_a_summary_not_the_whole_map(project):
    """Putting the entire graph in the prompt recreates the problem."""
    run = D.for_goal("add a shrink method", project)
    context = run.context_for(limit=3)
    assert context
    assert len(context) <= 4
    assert "symbols" in context[0]


def test_no_graph_means_no_context_rather_than_a_crash(tmp_path):
    empty = tmp_path / "fresh"
    empty.mkdir()
    run = D.for_goal("start", empty)
    assert run.context_for() == ()


def test_it_chooses_a_team_and_says_why(project):
    run = D.for_goal("add authentication to the widget endpoint", project)
    assert run.team.roles
    assert all(run.team.because.get(r.id) for r in run.team.roles)


def test_the_team_reaches_the_agent(project):
    """A team nobody tells the agent about is a team that did nothing."""
    run = D.for_goal("fix the crash in grow", project)
    agent = _Agent()
    import asyncio
    asyncio.run(run.execute(agent))

    context = "\n".join(agent.saw.context)
    assert any(role.title in context for role in run.team.roles)


def test_the_map_reaches_the_agent(project):
    run = D.for_goal("add a shrink method", project)
    agent = _Agent()
    import asyncio
    asyncio.run(run.execute(agent))
    assert any("symbols" in line for line in agent.saw.context)


def test_a_run_that_fails_keeps_what_it_learned(project):
    """
    A run that died still has its graph and its team, and saying so is more
    useful than an exception with nothing attached.
    """
    import asyncio

    run = D.for_goal("add a shrink method", project)
    result = asyncio.run(run.execute(_Agent(raises=RuntimeError("cli gone"))))

    assert result is None
    assert run.stage == D.FAILED
    assert "cli gone" in run.error
    assert run.graph is not None
    assert run.report()["team"] is not None


def test_scope_reaches_the_agent_as_a_constraint(project):
    """
    The gate refuses out-of-scope files afterwards. Telling the agent up
    front is what stops that being a surprise it could have avoided.
    """
    import asyncio
    run = D.for_goal('add a shrink method', project)
    agent = _Agent()
    asyncio.run(run.execute(agent, allowed_paths=('src/', 'tests/')))
    constraints = ' '.join(agent.saw.constraints)
    assert 'src/' in constraints
    assert 'tests/' in constraints


def test_no_scope_means_no_invented_constraints(project):
    import asyncio
    run = D.for_goal('add a shrink method', project)
    agent = _Agent()
    asyncio.run(run.execute(agent))
    assert agent.saw.constraints == ()


def test_passing_work_verifies(project):
    run = D.for_goal("add a shrink method", project)
    run.verify(E.Verifier(command=PYTEST), agent="scripted")
    assert run.attempt.passed
    assert run.stage == D.VERIFIED


def test_broken_work_does_not_verify(project):
    (project / "src" / "core.py").write_text("def broken(:\n", encoding="utf-8")
    run = D.for_goal("break it", project)
    run.verify(E.Verifier(command=PYTEST), agent="scripted")
    assert not run.attempt.passed


def test_verification_runs_with_the_network_off(project):
    run = D.for_goal("add a shrink method", project)
    run.verify(E.Verifier(command=PYTEST), agent="scripted")
    assert run.boundary["egress"] == "DENY_ALL"


def test_a_verifier_that_cannot_run_is_inconclusive(project):
    run = D.for_goal("x", project)
    run.verify(E.Verifier(command=("not-a-real-binary-xyz",)), agent="s")
    assert run.attempt.verdict == E.INCONCLUSIVE


def test_the_attempt_is_recorded_for_later_routing(project):
    record = E.Record()
    run = D.for_goal("add a shrink method", project)
    run.verify(E.Verifier(command=PYTEST), agent="claude", model="opus",
               record=record)
    assert len(record.attempts) == 1
    assert record.attempts[0].model == "opus"


def test_verified_and_approved_work_may_land(project):
    run = D.for_goal("add a shrink method", project)
    run.verify(E.Verifier(command=PYTEST), agent="scripted")
    decision = run.gate(["src/core.py"], allowed_paths=("src/",), approved=True)
    assert decision.allowed


def test_unverified_work_may_not_land(project):
    """The gate is the whole point; it must not be skippable."""
    run = D.for_goal("add a shrink method", project)
    decision = run.gate(["src/core.py"], approved=True)
    assert not decision.allowed
    assert decision.reason == P.NOT_VERIFIED


def test_the_gate_decides_but_does_not_act(project):
    """
    Promotion is the worktree manager's job and, before that, a person's.
    Nothing here may move a branch.
    """
    before = git(project, "rev-parse", "HEAD").strip()
    run = D.for_goal("add a shrink method", project)
    run.verify(E.Verifier(command=PYTEST), agent="scripted")
    run.gate(["src/core.py"], allowed_paths=("src/",), approved=True)
    assert git(project, "rev-parse", "HEAD").strip() == before


def test_the_report_covers_every_stage(project):
    import asyncio
    run = D.for_goal('add a shrink method', project)
    asyncio.run(run.execute(_Agent()))
    run.verify(E.Verifier(command=PYTEST), agent='scripted')
    run.gate(['src/core.py'], allowed_paths=('src/',), approved=True)
    report = run.report()
    assert report['stage'] == D.DECIDED
    assert report['graph']['symbols'] > 0
    assert report['team']['roles']
    assert report['environment']['strength'] in ('JOB_OBJECT', 'PROCESS_ONLY')
    assert report['verdict'] == E.PASSED
    assert report['gate']['allowed'] is True


def test_a_run_that_never_started_still_reports(project):
    run = D.DevelopmentRun(goal="x", project="widget", root=str(project))
    assert run.report()["stage"] == "NOT_STARTED"


def test_it_explains_a_refusal_in_words(project):
    run = D.for_goal("add a shrink method", project)
    run.gate(["src/core.py"], approved=True)
    assert "not promoted" in run.explain()


def test_it_explains_success_in_words(project):
    run = D.for_goal("add a shrink method", project)
    run.verify(E.Verifier(command=PYTEST), agent="scripted")
    run.gate(["src/core.py"], allowed_paths=("src/",), approved=True)
    assert "ready to promote" in run.explain()


def test_every_module_is_actually_reached():
    """
    The test this file exists for. A module that is built, tested and called
    by nothing is decoration - and this suite would still be green with the
    orchestrator deleted, unless something asserts the wiring.
    """
    import inspect
    source = inspect.getsource(D)
    for module in ('codegraph', 'roles', 'execution', 'evaluation', 'promotion'):
        assert f"{module}." in source, f"{module} is imported and never used"


def test_it_picks_an_executor_and_records_why(project):
    run = D.for_goal('add a shrink method', project)
    choice = run.pick()
    assert choice.because
    assert run.report()['executor']['because'] == choice.because


def test_a_default_choice_is_labelled_as_one(project):
    """
    "Why did claude get this?" is asked weeks later. "It was the default" is
    a perfectly good answer that has to survive to be given.
    """
    run = D.for_goal('add a shrink method', project)
    run.pick()
    assert run.choice.from_evidence is False


def test_evidence_changes_the_pick(project, monkeypatch):
    from friday import evaluation as EV
    from friday import executor_router as R
    both = (R.Executor(id='claude', binary='claude', buildable=True), R.Executor(id='opencode', binary='opencode', buildable=True))
    monkeypatch.setattr(R, 'KNOWN', both)
    monkeypatch.setattr(R, 'BY_ID', {e.id: e for e in both})
    monkeypatch.setattr(R.shutil, 'which', lambda name: f"/usr/bin/{name}")
    record = EV.Record()
    for _ in range(3):
        record.add(EV.Attempt(task='widget', agent='opencode', verdict=EV.PASSED, exit_code=0))
        record.add(EV.Attempt(task='widget', agent='claude', verdict=EV.FAILED, exit_code=1))
    run = D.for_goal('add a shrink method', project)
    run.project = 'widget'
    choice = run.pick(record=record)
    assert choice.executor == 'opencode'
    assert choice.from_evidence


def test_the_router_is_reached_from_the_orchestrator():
    """The wiring test again. A router nothing calls routes nothing."""
    import inspect
    assert 'executor_router.' in inspect.getsource(D)


def test_team_done_on_the_kanban_is_not_verification(project, monkeypatch):
    """
    ADR-001: a profile reporting status=done is not proof the work works.
    Before this fix `_execute_via_team` fabricated a Verification from that
    self-report; now it must return verification=None so the caller runs
    `verify()`/`evaluation.Verifier`, same as the single-worker path.
    """
    from friday import hermes_team
    from friday.executors.claude_code import TaskBundle

    run = D.for_goal('add a shrink method', project)
    bundle = TaskBundle(goal=run.goal, workspace=str(project))
    board_ref = {"objective_task_id": bundle.run_id}
    monkeypatch.setattr(hermes_team, "submit", lambda **kw: board_ref)
    monkeypatch.setattr(hermes_team, "gateway_for", lambda profile: None)
    monkeypatch.setattr(hermes_team, "poll",
                        lambda ref: {"dev": {"status": "done"}})

    result = run._execute_via_team(bundle, ("dev",))
    assert result.status == "partial"  # not "succeeded" - not verified yet
    assert result.verification is None


def test_team_poll_does_not_block_the_event_loop(project, monkeypatch):
    """
    The poll loop used `time.sleep(5)` inside `async def execute`, which
    blocks the whole voice event loop for up to 30 minutes. It must run
    off-thread via `asyncio.to_thread`.
    """
    import inspect
    assert 'asyncio.to_thread' in inspect.getsource(D.DevelopmentRun.execute)
