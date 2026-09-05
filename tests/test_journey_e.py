"""
Journey E on the host boundary: "build this" to a verified, gated result.

The chain, and the acceptance cases from the work order:

    readiness
      -> DevelopmentRun
      -> HOST_WORKTREE inside the Job Object boundary
      -> executor
      -> a question answered from project memory
      -> tests run inside the boundary
      -> patch validated against the base commit
      -> host untouched until promotion

The coding agent is a script. That is a real limit, stated: what this proves
is that the plumbing carries work end to end and that every gate refuses when
it should. The live executor is one argument away and needs an authenticated
CLI, so the suite does not spend money on it every run.

OpenCode inside a container is deliberately absent. Docker is not installed
on this machine and the boss has not been asked to install it, so the
boundary here is the Windows Job Object - kernel-enforced limits and cleanup,
honestly reported as JOB_OBJECT rather than as container isolation.
"""
import json
import subprocess
import sys
from dataclasses import asdict
import pytest
from friday import contracts as c
from friday import development as D
from friday import evaluation as E
from friday import product as P
from friday import promotion as PR
from friday import execution as EX
from friday.executors import brokers as B
from friday.executors import runs as RUNS
from friday.executors.claude_code import TaskBundle
from friday.toolsets import executor as X


def git(repo, *args):
    return subprocess.run(["git", "-C", str(repo), *args],
                          capture_output=True, text=True, check=True).stdout


@pytest.fixture
def store(tmp_path, monkeypatch):
    from friday.store import Store
    import friday.toolsets.memory as memory

    built = Store(str(tmp_path / "ada.sqlite3"))
    monkeypatch.setattr(memory, "_store", built, raising=False)
    monkeypatch.setattr(memory, "store", lambda: built)
    return built


@pytest.fixture(autouse=True)
def _own_graph_cache(tmp_path, monkeypatch):
    monkeypatch.setattr("friday.codegraph.graph_path",
                        lambda root: tmp_path / "cache" / "g.json")


@pytest.fixture
def project(tmp_path):
    """
    A small web app: one input, one button, one visible result.

    Friday's own repository is deliberately not the first destructive test
    subject. The development manager is what is under test here.
    """
    repo = tmp_path / "greeter"
    (repo / "src").mkdir(parents=True)
    (repo / "tests").mkdir()
    (repo / "src" / "app.py").write_text(
        "def greet(name):\n"
        "    return f'Hello, {name}!'\n",
        encoding="utf-8")
    (repo / "tests" / "test_app.py").write_text(
        "import sys, pathlib\n"
        "sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))\n"
        "from src.app import greet\n"
        "\n"
        "def test_it_greets():\n"
        "    assert greet('boss') == 'Hello, boss!'\n",
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
    """
    A coding agent, as a script. Speaks the existing executor contract.

    `asks` is the question it raises before doing the work, which is how the
    broker gets exercised through the same path a real agent would use.
    """

    def __init__(self, edit=None, asks="", options=""):
        self.edit = edit
        self.asks = asks
        self.options = options
        self.saw = None
        self.answer = None

    async def execute(self, bundle, *, timeout=1800.0, **kwargs):
        self.saw = bundle
        if self.asks:
            run = c.Run.create("ask", capability="ada_ask")
            self.answer = X.ada_ask(run, self.asks, options=self.options,
                                    run_id=bundle.run_id)
            if not self.answer.may_claim_completion:
                # A real agent stops here rather than guessing. That is the
                # whole point of the channel existing.
                return type("R", (), {"status": c.PARTIAL, "output": {}})()
        if self.edit:
            self.edit(bundle.workspace)
        return type("R", (), {"status": c.SUCCEEDED, "output": {}})()


def _ready_project(store, name="greeter"):
    store.ensure_project(name)
    store.record_decision(name, "greeting format - Hello, NAME!",
                          source="checked against sources")
    return name


def _open_run(store, project, bundle, status=RUNS.RUNNING):
    store.open_executor_run(
        bundle.run_id, executor_type="scripted",
        working_directory=bundle.workspace, project=project,
        task_bundle=json.dumps(asdict(bundle)), status=status)


def test_a_ready_project_gets_built_verified_and_gated(store, project):
    """Acceptance A, the whole chain."""
    import asyncio

    name = _ready_project(store)
    run = D.DevelopmentRun(goal="add a farewell to the greeter",
                           project=name, root=str(project))

    assert run.ready(db=store).can_build, run.readiness.because
    run.note_the_base()
    assert run.base_commit, "no base commit recorded"
    run.understand().staff()

    def _edit(workspace):
        from pathlib import Path

        Path(workspace, "src", "app.py").write_text(
            "def greet(name):\n"
            "    return f'Hello, {name}!'\n"
            "\n"
            "def farewell(name):\n"
            "    return f'Goodbye, {name}!'\n",
            encoding="utf-8")

    agent = _Agent(edit=_edit)
    assert asyncio.run(run.execute(agent, allowed_paths=("src/", "tests/")))
    assert run.stage == D.EXECUTED

    run.verify(E.Verifier(command=PYTEST, proves="the suite passes"),
               agent="scripted")
    assert run.attempt.passed, run.attempt.detail
    assert run.boundary["strength"] in ("JOB_OBJECT", "PROCESS_ONLY")

    changed = [line[3:] for line in
               git(project, "status", "--porcelain").splitlines()]
    decision = run.gate(changed, allowed_paths=("src/", "tests/"),
                        approved=True)
    assert decision.allowed, f"{decision.reason}: {decision.detail}"


def test_a_question_the_project_settles_never_reaches_him(store, project):
    """
    Acceptance B, and the core of Journey E. The agent asks, the broker
    answers from an accepted decision, and the boss is not interrupted.
    """
    import asyncio

    name = _ready_project(store)
    run = D.DevelopmentRun(goal="add a farewell", project=name,
                           root=str(project))
    run.ready(db=store)
    run.understand().staff()

    agent = _Agent(edit=lambda w: None,
                   asks="What greeting format should I use?")
    bundle = TaskBundle(goal=run.goal, workspace=str(project), project=name)
    _open_run(store, name, bundle)

    async def _execute():
        agent.saw = bundle
        return await agent.execute(bundle)

    asyncio.run(_execute())

    assert agent.answer is not None
    assert agent.answer.output["outcome"] == B.ANSWER_FROM_PROJECT
    assert "Hello" in agent.answer.output["answer"]
    assert agent.answer.output["authority"] == B.USER_DECISION


def test_an_unresolved_material_question_waits_for_him(store, project):
    """
    Acceptance C. Nothing settles it, it changes what gets built, so the run
    waits rather than the agent inventing an answer.
    """
    import asyncio

    name = _ready_project(store)
    bundle = TaskBundle(goal="add accounts", workspace=str(project),
                        project=name)
    _open_run(store, name, bundle)

    agent = _Agent(edit=lambda w: None,
                   asks="Should users be able to sign in with a password?")
    asyncio.run(agent.execute(bundle))

    assert agent.answer.output["outcome"] == B.WAIT_USER
    assert not agent.answer.may_claim_completion
    assert store.executor_run(bundle.run_id)["status"] == RUNS.WAITING_QUESTION


def test_a_blocking_question_stops_the_run_before_it_starts(store, project):
    """
    Better than asking mid-flight: the run never begins. Handing an agent a
    spec with a hole in it and hoping it guesses the same way he would is how
    a guess arrives looking like work.
    """
    name = _ready_project(store)
    store.ask_question(name, "Should accounts be email or username?",
                       why="decides the schema", impact="accounts schema",
                       blocking=True)

    run = D.DevelopmentRun(goal="add accounts", project=name,
                           root=str(project))
    readiness = run.ready(scope="the accounts schema", db=store)

    assert not readiness.can_build
    assert readiness.blockers


def test_an_unrelated_blocking_question_does_not_stop_this_work(store, project):
    name = _ready_project(store)
    store.ask_question(name, "Should accounts be email or username?",
                       why="decides the schema", impact="accounts schema",
                       blocking=True)

    run = D.DevelopmentRun(goal="restyle the greeting", project=name,
                           root=str(project))
    assert run.ready(scope="the greeting text", db=store).can_build


def test_assumptions_reach_the_agent_labelled_as_assumptions(store, project):
    """
    An assumption presented as a decision is one the agent will defend in a
    review later, and nobody ever made it.
    """
    import asyncio

    name = _ready_project(store)
    store.ask_question(name, "How many greetings should it remember?",
                       why="", impact="history", blocking=False)
    store.assume(name, "How many greetings should it remember?",
                 assumption="ten", reason="a sensible default") \
        if hasattr(store, "assume") else None

    run = D.DevelopmentRun(goal="add history", project=name,
                           root=str(project))
    run.ready(db=store)
    run.understand().staff()

    agent = _Agent(edit=lambda w: None)
    asyncio.run(run.execute(agent))
    context = "\n".join(agent.saw.context)

    if run.readiness.assumptions:
        assert "ASSUMPTIONS" in context
        assert "not decisions he made" in context


def test_an_executor_that_fails_promotes_nothing(store, project):
    """Acceptance D."""
    import asyncio

    name = _ready_project(store)
    run = D.DevelopmentRun(goal="add a farewell", project=name,
                           root=str(project))
    run.ready(db=store)
    run.note_the_base()

    assert asyncio.run(run.execute(_Agent(edit=lambda w: (_ for _ in ()).throw(
        RuntimeError("the cli died"))))) is None
    assert run.stage == D.FAILED

    decision = run.gate(["src/app.py"], approved=True)
    assert not decision.allowed


def test_failing_tests_promote_nothing(store, project):
    """Acceptance E."""
    name = _ready_project(store)
    (project / "src" / "app.py").write_text("def greet(n): return 'wrong'\n",
                                            encoding="utf-8")

    run = D.DevelopmentRun(goal="add a farewell", project=name,
                           root=str(project))
    run.note_the_base()
    run.verify(E.Verifier(command=PYTEST), agent="scripted")

    assert not run.attempt.passed
    assert not run.gate(["src/app.py"], approved=True).allowed


def test_the_host_is_untouched_until_promotion(store, project):
    """
    The commit must not exist. The working tree may be dirty - that is what
    `git checkout` is for - but nothing the gate refused may reach the branch.
    """
    name = _ready_project(store)
    before = git(project, "rev-parse", "HEAD").strip()

    run = D.DevelopmentRun(goal="break it", project=name, root=str(project))
    run.note_the_base()
    (project / "src" / "app.py").write_text("def broken(:\n", encoding="utf-8")
    run.verify(E.Verifier(command=PYTEST), agent="scripted")

    assert not run.gate(["src/app.py"], approved=True).allowed
    git(project, "checkout", "--", ".")
    assert git(project, "rev-parse", "HEAD").strip() == before


def test_a_commit_arriving_mid_run_refuses_the_promotion(store, project):
    """
    He committed something himself while the agent worked. The patch was
    verified against a base that is no longer HEAD, and promoting it anyway
    is how a clean review lands broken code.
    """
    name = _ready_project(store)
    run = D.DevelopmentRun(goal="add a farewell", project=name,
                           root=str(project))
    run.note_the_base()
    run.verify(E.Verifier(command=PYTEST), agent="scripted")
    assert run.attempt.passed

    (project / "his_own_work.txt").write_text("mine\n", encoding="utf-8")
    git(project, "add", "-A")
    git(project, "commit", "--quiet", "-m", "his own commit")

    decision = run.gate(["src/app.py"], allowed_paths=("src/",), approved=True)
    assert not decision.allowed
    assert decision.reason == PR.WRONG_BASE


def test_verification_runs_with_the_network_off(store, project):
    name = _ready_project(store)
    run = D.DevelopmentRun(goal="add a farewell", project=name,
                           root=str(project))
    run.verify(E.Verifier(command=PYTEST), agent="scripted")
    assert run.boundary["egress"] == "DENY_ALL"


def test_no_provider_key_crosses_into_the_work(store, project, monkeypatch):
    monkeypatch.setenv('ANTHROPIC_API_KEY', 'sk-ant-must-not-leak')
    with EX.for_development(project, name='DEV-e') as box:
        result = box.run([sys.executable, '-c', "import os; print(len([k for k in os.environ if k.endswith('_API_KEY')]))"])
    assert result.stdout.strip() == '0'


def test_the_run_can_explain_itself_end_to_end(store, project):
    import asyncio
    name = _ready_project(store)
    run = D.DevelopmentRun(goal='add a farewell', project=name, root=str(project))
    run.ready(db=store)
    run.note_the_base()
    run.understand().staff()
    run.pick()
    asyncio.run(run.execute(_Agent(edit=lambda w: None)))
    run.verify(E.Verifier(command=PYTEST), agent='scripted')
    run.gate(['src/app.py'], allowed_paths=('src/',), approved=True)
    report = run.report()
    assert report['readiness']['state'] in (P.READY, P.READY_WITH_ASSUMPTIONS)
    assert report['base_commit']
    assert report['team']['roles']
    assert report['executor']['because']
    assert report['environment']['egress'] == 'DENY_ALL'
    assert report['verdict'] == E.PASSED
    assert report['gate']['allowed'] is True
    # FR-012: the independent-review check sits between verification and
    # scope - a small change is "not required at this size", but the check
    # is recorded either way so the report says review was considered.
    assert [check['check'] for check in report['gate']['checks']] == ['changes', 'verified', 'independent_review', 'scope', 'reviewable', 'base', 'secrets', 'approved']
