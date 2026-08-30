"""
The whole chain, once, on a real project.

    graph the repo
    -> choose a team
    -> work inside the boundary
    -> verify objectively
    -> pass the gate
    -> promote

Each piece has its own tests. This is the one that proves they fit together,
because a pipeline made of individually-correct parts is exactly how the
typed-input bug happened: every component worked and nothing connected them.

The coding agent here is a script rather than the Claude CLI. That is a real
limit and it is stated rather than hidden - what this proves is that the
plumbing carries work from one end to the other and that the gate refuses
when it should. Swapping in the live executor is one call
(`ClaudeCodeExecutor(backend=SANDBOX)`) and needs an authenticated CLI, so it
is not something a test suite should do on every run.
"""
import subprocess
import sys
from pathlib import Path
import pytest
from friday import codegraph as G
from friday import evaluation as E
from friday import promotion as P
from friday import roles as R
from friday import execution as EX


def git(repo, *args):
    return subprocess.run(["git", "-C", str(repo), *args],
                          capture_output=True, text=True, check=True).stdout


@pytest.fixture
def project(tmp_path):
    """A small real repository, with a test that passes."""
    repo = tmp_path / 'dronegame'
    (repo / 'src').mkdir(parents=True)
    (repo / 'tests').mkdir()
    (repo / 'src' / 'drone.py').write_text('class Drone:\n    def __init__(self, hull=100):\n        self.hull = hull\n\n    def hit(self, damage):\n        self.hull -= damage\n        return self.hull\n', encoding='utf-8')
    (repo / 'src' / 'world.py').write_text('class Arena:\n    def __init__(self, width=64, height=64):\n        self.width = width\n        self.height = height\n\n    def contains(self, x, y):\n        return 0 <= x < self.width and 0 <= y < self.height\n\n\ndef spawn_point(arena):\n    return arena.width // 2, arena.height // 2\n', encoding='utf-8')
    (repo / 'tests' / 'test_drone.py').write_text('import sys, pathlib\nsys.path.insert(0, str(pathlib.Path(__file__).parent.parent))\nfrom src.drone import Drone\nfrom src.world import Arena, spawn_point\n\ndef test_a_hit_reduces_the_hull():\n    assert Drone(100).hit(30) == 70\n\ndef test_the_arena_knows_its_bounds():\n    assert Arena().contains(*spawn_point(Arena()))\n', encoding='utf-8')
    (repo / '.gitignore').write_text('__pycache__/\n*.pyc\n.pytest_cache/\n', encoding='utf-8')
    git(repo.parent, 'init', '--quiet', str(repo))
    git(repo, 'config', 'user.email', 'test@test.invalid')
    git(repo, 'config', 'user.name', 'test')
    git(repo, 'add', '-A')
    git(repo, 'commit', '--quiet', '-m', 'initial')
    return repo
PYTEST = (sys.executable, '-m', 'pytest', '-q', 'tests/')


def test_the_whole_chain_promotes_verified_work(project, tmp_path):
    goal = 'add a shield to the drone so a hit is absorbed first'
    graph = G.CodeGraph.build(project)
    assert graph.worth_building()
    assert [s.name for s in graph.api_of('src/drone.py')] == ['Drone', 'hit']
    team = R.compile_team(goal, files=len(graph.fingerprints))
    assert team.roles
    assert all((team.because.get(r.id) for r in team.roles))
    with EX.for_development(project, name='DEV-slice') as box:
        assert box.strength() in ('JOB_OBJECT', 'PROCESS_ONLY')
        box.write('src/drone.py', 'class Drone:\n    def __init__(self, hull=100, shield=0):\n        self.hull = hull\n        self.shield = shield\n\n    def hit(self, damage):\n        absorbed = min(self.shield, damage)\n        self.shield -= absorbed\n        self.hull -= damage - absorbed\n        return self.hull\n')
        attempt = E.graded('feature', 'scripted', project, E.Verifier(command=PYTEST, proves='the suite passes'), sandbox=box)
    assert attempt.passed, attempt.detail
    changed = [line[3:] for line in git(project, 'status', '--porcelain').splitlines()]
    decision = P.decide(project, changed, attempt=attempt, allowed_paths=('src/',), approved=True)
    assert decision.allowed, f"{decision.reason}: {decision.detail}"
    git(project, 'add', '-A')
    git(project, 'commit', '--quiet', '-m', 'shield')
    assert 'shield' in (project / 'src' / 'drone.py').read_text(encoding='utf-8')
    graph.refresh()
    assert graph.stale() == []


def test_work_that_breaks_the_tests_is_not_promoted(project):
    """The whole reason the gate exists."""
    with EX.for_development(project, name='DEV-bad') as box:
        box.write('src/drone.py', "class Drone:\n    def __init__(self, hull=100):\n        self.hull = hull\n\n    def hit(self, damage):\n        return 'oops'\n")
        attempt = E.graded('feature', 'scripted', project, E.Verifier(command=PYTEST), sandbox=box)
    assert not attempt.passed
    changed = [line[3:] for line in git(project, 'status', '--porcelain').splitlines()]
    decision = P.decide(project, changed, attempt=attempt, approved=True)
    assert not decision.allowed
    assert decision.reason == P.NOT_VERIFIED


def test_work_that_wandered_out_of_scope_is_not_promoted(project):
    with EX.for_development(project, name='DEV-wide') as box:
        box.write('src/drone.py', (project / 'src' / 'drone.py').read_text(encoding='utf-8'))
        box.write('deploy/production.yaml', 'replicas: 99\n')
        attempt = E.graded('feature', 'scripted', project, E.Verifier(command=PYTEST), sandbox=box)
    assert attempt.passed, 'the tests still pass; scope is the only problem'
    changed = [line[3:] for line in git(project, 'status', '--porcelain').splitlines()]
    decision = P.decide(project, changed, attempt=attempt, allowed_paths=('src/',), approved=True)
    assert not decision.allowed
    assert decision.reason == P.OUT_OF_SCOPE


def test_work_that_committed_a_secret_is_not_promoted(project):
    with EX.for_development(project, name='DEV-leak') as box:
        box.write('src/config.py', 'ANTHROPIC = "sk-ant-' 'api03-aaaaaaaaaaaaaaaaaaaaaaaaaaaaa"\n')
        attempt = E.graded('feature', 'scripted', project, E.Verifier(command=PYTEST), sandbox=box)
    changed = [line[3:] for line in git(project, 'status', '--porcelain').splitlines()]
    decision = P.decide(project, changed, attempt=attempt, approved=True)
    assert not decision.allowed
    assert decision.reason == P.SECRET_IN_DIFF


def test_the_host_project_is_untouched_until_promotion(project):
    """
    Nothing the gate refused may reach the branch. The working tree can be
    dirty - that is what `git checkout` is for - but the commit must not
    exist.
    """
    before = git(project, 'rev-parse', 'HEAD').strip()
    with EX.for_development(project, name='DEV-refused') as box:
        box.write('src/drone.py', 'def broken(:\n')
        attempt = E.graded('feature', 'scripted', project, E.Verifier(command=PYTEST), sandbox=box)
    changed = [line[3:] for line in git(project, 'status', '--porcelain').splitlines()]
    decision = P.decide(project, changed, attempt=attempt, approved=True)
    assert not decision.allowed
    git(project, 'checkout', '--', '.')
    assert git(project, 'rev-parse', 'HEAD').strip() == before
    assert git(project, 'status', '--porcelain').strip() == ''


def test_no_process_outlives_the_slice(project):
    box = EX.for_development(project, name='DEV-cleanup')
    with box:
        result = box.run([sys.executable, '-c', "print('work')"])
        assert result.ok
    assert box._closed
    assert box.report()['strength'] in ('JOB_OBJECT', 'PROCESS_ONLY')


def test_the_agent_never_saw_a_provider_key(project, monkeypatch):
    monkeypatch.setenv('ANTHROPIC_API_KEY', 'sk-ant-must-not-leak')
    with EX.for_development(project, name='DEV-env') as box:
        result = box.run([sys.executable, '-c', "import os; print(len([k for k in os.environ if k.endswith('_API_KEY')]))"])
    assert result.stdout.strip() == '0', 'a provider key crossed the boundary'


def test_the_run_is_reportable_end_to_end(project):
    """A run nobody can explain afterwards is a run nobody should trust."""
    goal = 'add a shield to the drone'
    graph = G.CodeGraph.build(project)
    team = R.compile_team(goal, files=len(graph.fingerprints))
    with EX.for_development(project, name='DEV-report') as box:
        attempt = E.graded('feature', 'scripted', project, E.Verifier(command=PYTEST, proves='the suite passes'), sandbox=box)
        boundary = box.report()
    decision = P.decide(project, ['src/drone.py'], attempt=attempt, allowed_paths=('src/',), approved=True)
    report = {'goal': goal, 'graph': graph.repo_map(limit=3), 'team': team.as_dict(), 'environment': boundary, 'verdict': attempt.verdict, 'gate': decision.as_dict()}
    assert report['graph']['symbols'] > 0
    assert report['team']['roles']
    assert report['environment']['egress'] == 'DENY_ALL'
    assert report['verdict'] == E.PASSED
    assert report['gate']['allowed'] is True
    assert [c['check'] for c in report['gate']['checks']] == ['changes', 'verified', 'scope', 'reviewable', 'secrets', 'approved']
