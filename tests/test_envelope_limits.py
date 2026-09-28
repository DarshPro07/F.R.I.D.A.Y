"""
SEC-005 (PRD v7 section 43): an envelope's token and tool-call limits and its
expiry act through the objective budget (audit A-022), which already counts
recorded spend before every call - the model gateway asks the same check.

They only ever tighten it. A limit above the class ceiling changes nothing,
0 means "no ceiling of the envelope's own", and an expired envelope parks
the run for the person instead of failing its tasks one by one.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import pytest

from friday import capability_runtime as CR
from friday import contracts as c
from friday import objective_budget as B
from friday import objectives as O
from friday import policy as P
from friday.continuous import ContinuousTaskExecutor
from friday.store import Store

PAST = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat(timespec="seconds")


@pytest.fixture
def store(tmp_path):
    db = Store(tmp_path / "limits.sqlite3")
    yield db
    db.close()


class _Telemetry:
    """Recorded gateway usage for one objective."""

    def __init__(self, tokens: int) -> None:
        self.tokens = tokens

    def for_objective(self, run_id):
        return [{"input_tokens": self.tokens, "output_tokens": 0}]


def _compile(store, run_id: str, **limits) -> str:
    envelope = O.envelope_for(run_id, ["system_get_info"], provenance=c.PERSON,
                              attended=True).narrow(**limits)
    O.compile_objective(store, request="check my system",
                        tasks=[{"capability": "system_get_info", "arguments": {}}],
                        manifest=[{"id": "system_get_info", "description": "info"}],
                        objective_summary="check my system", run_id=run_id,
                        envelope=envelope, provenance=c.PERSON, attended=True)
    return run_id


def test_a_token_limit_tightens_the_ceiling(store):
    run_id = _compile(store, "OBJ-TOKENS", token_limit=100)
    verdict = B.check(store, run_id, telemetry=_Telemetry(150))
    assert not verdict.allowed
    assert (verdict.dimension, verdict.limit) == ("tokens", 100)


def test_a_token_limit_never_loosens_the_ceiling(store):
    run_id = _compile(store, "OBJ-LOOSE", token_limit=10 ** 9)
    ceiling = int(store.objective_run(run_id)["cost_budget_tokens"] or 0)
    assert ceiling > 0, "the class ceiling was not recorded"
    verdict = B.check(store, run_id, telemetry=_Telemetry(ceiling + 1))
    assert not verdict.allowed
    assert (verdict.dimension, verdict.limit) == ("tokens", ceiling)


def test_a_tool_call_limit_tightens_the_class_limit(store):
    run_id = _compile(store, "OBJ-CALLS", tool_call_limit=1)
    task = store.objective_tasks(run_id)[0]
    store.update_objective_task(task["task_id"], attempts=1)
    verdict = B.check(store, run_id)
    assert not verdict.allowed
    assert (verdict.dimension, verdict.limit) == ("tool_calls", 1)


def test_no_limit_of_its_own_leaves_the_budget_as_it_was(store):
    run_id = _compile(store, "OBJ-PLAIN")
    assert B.check(store, run_id, telemetry=_Telemetry(1)).allowed


def test_an_expired_envelope_parks_the_run_for_the_person(store):
    run_id = _compile(store, "OBJ-EXPIRED", expires_at=PAST)
    called = []

    async def port(capability_id, arguments, *, run=None):
        called.append(capability_id)
        return c.succeeded(c.started(run.run_id, capability_id), "ran").to_dict()

    async def go():
        executor = ContinuousTaskExecutor(store, call_capability=port)
        try:
            for _ in range(200):
                if store.objective_run(run_id)["status"] in (O.RUN_WAITING_STATUSES
                                                             + O.RUN_TERMINAL):
                    return
                await executor.start(run_id)
                await asyncio.sleep(0.02)
        finally:
            executor.stop()

    asyncio.run(go())
    final = store.objective_run(run_id)
    assert called == [], "a task ran under an expired envelope"
    assert final["status"] == O.RUN_PAUSED
    assert "expired" in (final.get("blocker") or ""), final


def test_the_runtime_refuses_an_expired_envelope_itself():
    """Defence in depth: whatever calls the runtime, an expired envelope
    authorises nothing."""
    envelope = c.Envelope(run_id="RUN-X", allowed_capabilities=("system_get_info",),
                          expires_at=PAST)
    run = c.Run(run_id="RUN-X", request="info", envelope=envelope)
    result = CR.CapabilityRuntime(engine=P.PolicyEngine(autonomy=P.DANGEROUS)).execute(
        "system_get_info", {}, run=run)
    assert result.status == c.NOT_PERMITTED
    assert "expired" in result.error


def test_a_session_approval_cannot_lift_an_envelope_block():
    """ADA_PREAPPROVED_TOOLS -> approve_for_session is a second approval
    path. It answers policy questions; a block is not a policy question."""
    engine = P.PolicyEngine(autonomy=P.GUARDED)
    engine.approve_for_session("files.list")
    envelope = c.Envelope(run_id="RUN-B", allowed_capabilities=(c.ANY_CAPABILITY,),
                          blocked_capabilities=("files_list",))
    run = c.Run(run_id="RUN-B", request="list", envelope=envelope)
    result = CR.CapabilityRuntime(engine=engine).execute("files_list", {"path": "."}, run=run)
    assert result.status == c.NOT_PERMITTED
    assert "blocked" in result.error


def test_a_port_that_carries_the_run_is_still_held_to_the_envelope(store):
    """The executor does not trust a port to enforce the envelope because
    it accepts the Run: a capability outside it never reaches the port."""
    run_id = "OBJ-PORT"
    envelope = O.envelope_for(run_id, ["system_get_info"], provenance=c.PERSON,
                              attended=True).narrow(allowed_capabilities=())
    O.compile_objective(store, request="info",
                        tasks=[{"capability": "system_get_info", "arguments": {}}],
                        manifest=[{"id": "system_get_info", "description": "info"}],
                        objective_summary="info", run_id=run_id, envelope=envelope, provenance=c.PERSON, attended=True)
    called = []

    async def port(capability_id, arguments, *, run=None):
        called.append(capability_id)
        return c.succeeded(c.started(run.run_id, capability_id), "ran").to_dict()

    async def go():
        executor = ContinuousTaskExecutor(store, call_capability=port)
        try:
            for _ in range(200):
                if store.objective_run(run_id)["status"] in (O.RUN_WAITING_STATUSES
                                                             + O.RUN_TERMINAL):
                    return
                await executor.start(run_id)
                await asyncio.sleep(0.02)
        finally:
            executor.stop()

    asyncio.run(go())
    assert called == [], "the port was trusted to enforce the envelope"
