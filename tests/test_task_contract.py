"""
S1: the task contract, goal to Hermes prompt.

`development.py` built a TaskBundle with no acceptance at all; Hermes
checked acceptance to decide SUCCEEDED vs PARTIAL and never got any. These
tests drive that whole chain and would have failed before the fix: an
empty `acceptance` tuple, and a worker prompt with none of the contract
sections.
"""
import pytest

from friday import development as D
from friday import evaluation as E
from friday import hermes_bridge as HB
from friday.executors import hermes as H


class _Agent:
    """A coding agent, as a script. Speaks the existing executor contract."""

    def __init__(self):
        self.saw = None

    async def execute(self, bundle, *, timeout=1800.0, **kwargs):
        self.saw = bundle
        return type("R", (), {"status": "succeeded", "output": {}})()


@pytest.fixture(autouse=True)
def _own_cache(tmp_path, monkeypatch):
    monkeypatch.setattr("friday.codegraph.graph_path",
                        lambda root: tmp_path / "cache" / "g.json")


@pytest.fixture
def empty_project(tmp_path):
    root = tmp_path / "proj"
    root.mkdir()
    (root / "README.md").write_text("# proj\n", encoding="utf-8")
    return root


def test_acceptance_reaches_hermes_from_a_development_run(empty_project):
    import asyncio

    run = D.for_goal("add a shrink method to Widget", empty_project)
    agent = _Agent()
    verifier = E.Verifier(command=("pytest", "-q"), proves="shrink() works")

    asyncio.run(run.execute(agent, verifier=verifier))

    # claude_code.TaskBundle side: acceptance is no longer empty.
    dev_bundle = agent.saw
    assert dev_bundle.acceptance == ("shrink() works",)
    assert dev_bundle.verification == ("pytest -q",)

    # hermes_bridge side: the same acceptance survives the translation and
    # shows up in the rendered worker prompt.
    bridge_bundle = H.to_bridge_bundle(dev_bundle)
    assert bridge_bundle.acceptance == ("shrink() works",)
    prompt = bridge_bundle.render()
    assert "ACCEPTANCE CRITERIA" in prompt
    assert "shrink() works" in prompt


def test_worker_prompt_has_the_ten_sections_in_order():
    bundle = HB.TaskBundle(
        goal="do the thing",
        acceptance=("it works",),
        known_facts=("3 files, 5 symbols.",),
        assumptions=("nobody else touches this module",),
        constraints=("no network",),
        allowed_paths=("src/",),
        disallowed=("do not push to origin",),
        role="Implementer",
        verification=("pytest -q",),
    )
    prompt = bundle.render()

    order = ["GOAL", "ACCEPTANCE CRITERIA", "KNOWN FACTS", "ASSUMPTIONS",
             "CONSTRAINTS", "ALLOWED SCOPE", "PROHIBITED ACTIONS",
             "ROLE / RESPONSIBILITY", "VERIFICATION", "REPORTING CONTRACT"]
    positions = [prompt.index(title) for title in order]
    assert positions == sorted(positions), prompt


def test_worker_prompt_names_the_claude_subagent():
    from friday.roles import claude_agent_for
    from friday.executors.claude_code import TaskBundle as ClaudeBundle

    bundle = ClaudeBundle(goal="do the thing", workspace=".", role="Implementer")
    prompt = bundle.prompt()
    assert f"Use the `{claude_agent_for('Implementer')}` subagent" in prompt


def test_iteration_budget_is_rendered():
    from friday.executors.claude_code import TaskBundle as ClaudeBundle

    bundle = ClaudeBundle(goal="do the thing", workspace=".", iteration_budget=3)
    prompt = bundle.prompt()
    assert "ITERATION BUDGET: 3 attempts" in prompt

    bridge_bundle = HB.TaskBundle(goal="do the thing", iteration_budget=3)
    rendered = bridge_bundle.render()
    assert "ITERATION BUDGET: 3 attempts" in rendered


def test_empty_fields_render_no_empty_sections():
    bundle = HB.TaskBundle(goal="do the thing")
    prompt = bundle.render()

    assert "GOAL" in prompt
    assert "REPORTING CONTRACT" in prompt
    for title in ("ACCEPTANCE CRITERIA", "KNOWN FACTS", "ASSUMPTIONS",
                  "CONSTRAINTS", "ALLOWED SCOPE", "PROHIBITED ACTIONS",
                  "ROLE / RESPONSIBILITY", "VERIFICATION"):
        assert title not in prompt, f"{title} rendered with no content"
