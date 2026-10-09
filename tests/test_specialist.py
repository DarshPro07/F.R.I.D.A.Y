"""
FAB-005: a temporary specialist is composed per task from a role, a model
route, an executor, task skills, allowed tools and a workspace policy.

The claim: the same role runs on two different executors with the same skills
and limits, and both results are attributed to the role in the durable ledger.
The model route is an input here (the owner's routing-policy choice is not
needed to compose a specialist).
"""

from __future__ import annotations

import json

import pytest

from friday import specialist as S
from friday.agent_ledger import AgentLedger, LedgerError, SUCCEEDED
from friday.store import Store

ROUTE = "anthropic/claude-sonnet/5.5/subscription"
SKILLS = ("karpathy-guidelines", "test-driven-development")
TOOLS = ("Read", "Edit", "Bash")
POLICY = S.WorkspacePolicy(allowed_paths=("friday/store.py", "tests/"))


@pytest.fixture
def ledger(tmp_path):
    store = Store(tmp_path / "specialist.sqlite3")
    yield AgentLedger(store)
    store.close()


def _compose(executor_id: str) -> S.Specialist:
    return S.compose("minimal", executor_id=executor_id, model_route=ROUTE,
                     skills=SKILLS, allowed_tools=TOOLS,
                     workspace_policy=POLICY)


def test_the_same_role_runs_on_two_executors_with_one_set_of_limits(ledger):
    a, b = _compose("hermes"), _compose("claude")
    assert a.executor_id != b.executor_id
    assert (a.role_id, a.skills, a.allowed_tools, a.workspace) \
        == (b.role_id, b.skills, b.allowed_tools, b.workspace)

    packets = [s.bundle("fix the bug", "/w") for s in (a, b)]
    for packet in packets:
        assert packet.role == "minimal"
        assert packet.allowed_paths == POLICY.allowed_paths
        assert packet.isolate is True
        assert any("karpathy-guidelines" in line for line in packet.context)
        assert any("Read, Edit, Bash" in line for line in packet.constraints)
    assert packets[0].context == packets[1].context
    assert packets[0].constraints == packets[1].constraints

    for run_id, spec in (("R-H", a), ("R-C", b)):
        S.record(ledger, spec, run_id=run_id, task="fix the bug", workspace="/w")
        ledger.finish(run_id, SUCCEEDED, [f"executor_runs:{run_id}"])

    attributed = ledger.by_role("minimal")
    assert {r["run_id"]: r["executor_id"] for r in attributed} \
        == {"R-H": "hermes", "R-C": "claude"}
    assert {r["model_route"] for r in attributed} == {ROUTE}
    assert {r["status"] for r in attributed} == {SUCCEEDED}


def test_the_composition_is_recorded_as_evidence_on_the_run(ledger):
    S.record(ledger, _compose("hermes"), run_id="R1", task="t", workspace="/w")
    composed = [e for e in ledger.events("R1") if e["kind"] == "composed"]
    assert len(composed) == 1
    assert composed[0]["evidence_refs"] == ["agent_runs:R1"]
    body = json.loads(composed[0]["message"])
    assert body["skills"] == list(SKILLS)
    assert body["allowed_tools"] == list(TOOLS)
    assert body["allowed_paths"] == list(POLICY.allowed_paths)


def test_the_role_comes_from_the_registry_not_from_the_caller():
    spec = _compose("claude")
    assert "Minimal Change Engineer" in "\n".join(spec.bundle("g", "/w").context)
    with pytest.raises(S.SpecialistError, match="no-such-role"):
        S.compose("no-such-role", executor_id="claude", model_route=ROUTE)


def test_an_executor_friday_cannot_drive_is_refused():
    with pytest.raises(S.SpecialistError, match="codex"):
        S.compose("minimal", executor_id="codex", model_route=ROUTE)
    with pytest.raises(S.SpecialistError, match="nonesuch"):
        S.compose("minimal", executor_id="nonesuch", model_route=ROUTE)


def test_a_route_without_four_parts_is_refused_before_anything_is_recorded(ledger):
    spec = S.compose("minimal", executor_id="claude", model_route="gpt-4")
    with pytest.raises(LedgerError):
        S.record(ledger, spec, run_id="R2", task="t", workspace="/w")
    assert ledger.by_role("minimal") == []
