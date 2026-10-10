"""
FAB-005: a temporary specialist is composed per task from a role, a model
route, an executor, task skills, allowed tools and a workspace policy.

The claim: the same role runs on two different executors with the same skills
and limits, and both results are attributed to the role in the durable ledger.
The model route is an input here (the owner's routing-policy choice is not
needed to compose a specialist).
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta, timezone

import pytest

from friday import contracts as c
from friday import hermes_bridge as hb
from friday import specialist as S
from friday.agent_ledger import AgentLedger, FAILED, LedgerError, SUCCEEDED, TERMINAL
from friday.executors.hermes import HermesExecutor
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


class _Executor:
    """Stands in for an executor: `execute(bundle) -> ActionResult` is the contract."""

    def __init__(self, tool_id: str, outcome: str = "succeeded") -> None:
        self.tool_id, self.outcome, self.seen, self.limits = tool_id, outcome, [], []

    async def execute(self, bundle, **_kwargs):
        self.seen.append(bundle)
        self.limits.append(_kwargs)
        started = c.started(c.new_run_id(), self.tool_id)
        if self.outcome == "raise":
            raise RuntimeError("the executor fell over")
        if self.outcome == "failed":
            return c.failed(started, "the build is red")
        if self.outcome == "partial":
            return started.finish(status=c.PARTIAL, error="needs an answer")
        return c.succeeded(
            started, output={"result": "done"},
            verification=c.Verification(method=f"{self.tool_id}.turn_complete",
                                        evidence="reported COMPLETE"))


@pytest.fixture
def executors(monkeypatch):
    """`executor_router.build` hands back a stand-in, by id, as it would a real one."""
    made = {"hermes": _Executor("executor.hermes"),
            "claude": _Executor("executor.claude_code")}
    monkeypatch.setattr(S.executor_router, "build",
                        lambda executor_id, store, **kw: made[executor_id])
    return made


def _results(ledger, run_id: str) -> list[dict]:
    return [json.loads(e["message"]) for e in ledger.events(run_id)
            if e["kind"] == S.RESULT]


def test_the_same_role_is_run_on_two_executors_and_both_results_are_the_roles(
        ledger, executors):
    for executor_id, run_id in (("hermes", "R-H"), ("claude", "R-C")):
        got = asyncio.run(S.dispatch(
            ledger, _compose(executor_id), "fix the bug", "/w", run_id=run_id))
        assert got.status == c.SUCCEEDED

    hermes, claude = executors["hermes"].seen[0], executors["claude"].seen[0]
    assert (hermes.role, hermes.allowed_paths, hermes.isolate, hermes.constraints) \
        == (claude.role, claude.allowed_paths, claude.isolate, claude.constraints) \
        == ("minimal", POLICY.allowed_paths, True, ("Allowed tools: Read, Edit, Bash",))
    assert (hermes.run_id, claude.run_id) == ("R-H", "R-C")

    attributed = ledger.by_role("minimal")
    assert {r["run_id"]: (r["executor_id"], r["status"]) for r in attributed} \
        == {"R-H": ("hermes", SUCCEEDED), "R-C": ("claude", SUCCEEDED)}
    assert _results(ledger, "R-H")[0]["tool_id"] == "executor.hermes"
    assert _results(ledger, "R-C")[0]["tool_id"] == "executor.claude_code"
    for run_id in ("R-H", "R-C"):
        (result,) = _results(ledger, run_id)
        assert result["status"] == c.SUCCEEDED
        assert result["verification"]["evidence"] == "reported COMPLETE"


def test_the_run_limits_reach_every_executor_alike_and_none_is_invented(
        ledger, executors):
    envelope = c.Envelope(run_id="R-ENV", cwd="/w", write_roots=("/w",))
    for executor_id, run_id in (("hermes", "R-H"), ("claude", "R-C")):
        asyncio.run(S.dispatch(ledger, _compose(executor_id), "t", "/w",
                               run_id=run_id, timeout=90.0, envelope=envelope))
    assert executors["hermes"].limits == executors["claude"].limits \
        == [{"timeout": 90.0, "envelope": envelope}]

    # No limit asked for: the executor's own default applies, not one made up here.
    asyncio.run(S.dispatch(ledger, _compose("hermes"), "t", "/w", run_id="R-D"))
    assert executors["hermes"].limits[-1] == {}


class _Supervisor:
    """The bridge a real `HermesExecutor` hands its task to: keeps what it was sent."""

    def __init__(self, raises: Exception | None = None) -> None:
        self.calls, self.raises = [], raises

    def delegate(self, bundle, **kwargs):
        if self.raises is not None:
            raise self.raises
        self.calls.append((bundle, kwargs))
        return {"work_run_id": "WR-1",
                "result": {"status": hb.COMPLETE, "result": "done", "model": "m"}}


def _envelope(tmp_path, *, seconds=120.0, capabilities=("hermes_delegate",)):
    work = tmp_path / "ws"
    work.mkdir(exist_ok=True)
    expires = datetime.now(timezone.utc) + timedelta(seconds=seconds)
    return c.Envelope(run_id="R-ENV", cwd=str(work), read_roots=(str(tmp_path),),
                      write_roots=(str(work),), allowed_capabilities=capabilities,
                      expires_at=expires.isoformat())


def _real_hermes(monkeypatch, supervisor):
    """Hermes is the real executor class; only its gateway is the stand-in."""
    monkeypatch.setattr(
        S.executor_router, "build",
        lambda executor_id, store, **kw: HermesExecutor(store, supervisor=supervisor))


def test_the_hermes_executor_holds_the_role_run_to_the_envelope_it_was_given(
        ledger, tmp_path, monkeypatch):
    envelope, supervisor = _envelope(tmp_path), _Supervisor()
    _real_hermes(monkeypatch, supervisor)
    work = envelope.cwd

    got = asyncio.run(S.dispatch(ledger, _compose("hermes"), "t", work, run_id="R-H",
                                 timeout=900.0, envelope=envelope))

    assert got.status == c.SUCCEEDED
    ((bundle, sent),) = supervisor.calls
    # The bridge registers the envelope, so the exec bridge judges every file call by it.
    assert sent["scope"] is envelope
    assert sent["workspace"] == work
    # The run ends when its envelope does, whatever timeout was asked for.
    assert 0 < sent["expires_in"] <= 120
    assert sent["turn_timeout"] <= 120
    assert bundle.allowed_paths == envelope.write_roots
    assert "Allowed tools: Read, Edit, Bash" in bundle.constraints
    assert any(line.startswith("write only under: ") for line in bundle.constraints)
    assert ledger.run("R-H")["status"] == SUCCEEDED

    # No envelope asked for: the delegation is the unscoped one it always was.
    asyncio.run(S.dispatch(ledger, _compose("hermes"), "t", work, run_id="R-D",
                           timeout=900.0))
    (_, plain) = supervisor.calls[-1]
    assert "scope" not in plain and "expires_in" not in plain
    assert plain["turn_timeout"] == 900.0


@pytest.mark.parametrize("make, reason", [
    (lambda tmp: _envelope(tmp, seconds=-5.0), "expired"),
    (lambda tmp: _envelope(tmp, capabilities=()), "hermes_delegate"),
], ids=["expired", "delegation-not-allowed"])
def test_an_envelope_that_does_not_allow_the_run_is_refused_not_run_on_hermes(
        ledger, tmp_path, monkeypatch, make, reason):
    envelope, supervisor = make(tmp_path), _Supervisor()
    _real_hermes(monkeypatch, supervisor)

    got = asyncio.run(S.dispatch(ledger, _compose("hermes"), "t", envelope.cwd,
                                 run_id="R-H", envelope=envelope))

    assert got.status == c.NOT_PERMITTED
    assert got.error.startswith("ENVELOPE: ") and reason in got.error
    assert supervisor.calls == []
    assert ledger.run("R-H")["status"] in TERMINAL
    assert _results(ledger, "R-H")[0]["status"] == c.NOT_PERMITTED


def test_a_workspace_outside_the_envelope_is_refused_on_hermes_as_on_claude(
        ledger, tmp_path, monkeypatch):
    envelope, supervisor = _envelope(tmp_path), _Supervisor()
    _real_hermes(monkeypatch, supervisor)
    elsewhere = tmp_path.parent / "not-in-the-envelope"

    got = asyncio.run(S.dispatch(ledger, _compose("hermes"), "t", str(elsewhere),
                                 run_id="R-H", envelope=envelope))

    assert got.status == c.NOT_PERMITTED
    assert "outside this run's envelope" in got.error
    assert supervisor.calls == []


def test_a_gateway_that_cannot_be_held_to_the_envelope_is_a_refusal_not_a_crash(
        ledger, tmp_path, monkeypatch):
    envelope = _envelope(tmp_path)
    _real_hermes(monkeypatch, _Supervisor(
        raises=c.OutsideEnvelope("no gateway can be held to an envelope here")))

    got = asyncio.run(S.dispatch(ledger, _compose("hermes"), "t", envelope.cwd,
                                 run_id="R-H", envelope=envelope))

    assert got.status == c.NOT_PERMITTED
    assert got.error == "ENVELOPE: no gateway can be held to an envelope here"
    assert ledger.run("R-H")["status"] == FAILED


def test_a_failed_result_closes_the_role_run_as_failed_with_the_reason(
        ledger, executors):
    executors["hermes"].outcome = "failed"
    got = asyncio.run(S.dispatch(ledger, _compose("hermes"), "t", "/w", run_id="R1"))
    assert got.status == c.FAILED
    assert ledger.run("R1")["status"] == FAILED
    assert _results(ledger, "R1")[0]["error"] == "the build is red"


def test_a_partial_result_is_recorded_without_claiming_the_run_finished(
        ledger, executors):
    executors["claude"].outcome = "partial"
    asyncio.run(S.dispatch(ledger, _compose("claude"), "t", "/w", run_id="R1"))
    assert ledger.run("R1")["status"] not in TERMINAL
    assert _results(ledger, "R1")[0]["status"] == c.PARTIAL


def test_a_result_the_executor_already_closed_is_not_closed_twice(ledger, executors):
    class Closing(_Executor):
        async def execute(self, bundle, **kwargs):
            got = await super().execute(bundle, **kwargs)
            ledger.finish(bundle.run_id, SUCCEEDED, [f"executor_runs:{bundle.run_id}"])
            return got

    executors["claude"] = Closing("executor.claude_code")
    asyncio.run(S.dispatch(ledger, _compose("claude"), "t", "/w", run_id="R1"))
    kinds = [e["kind"] for e in ledger.events("R1")]
    assert kinds.count("finished") == 1
    assert kinds.count(S.RESULT) == 1


def test_an_executor_that_raises_leaves_the_run_failed_not_pending(ledger, executors):
    executors["hermes"].outcome = "raise"
    with pytest.raises(RuntimeError, match="fell over"):
        asyncio.run(S.dispatch(ledger, _compose("hermes"), "t", "/w", run_id="R1"))
    assert ledger.run("R1")["status"] == FAILED


def test_an_executor_that_cannot_be_built_is_refused_before_anything_is_recorded(
        ledger, monkeypatch):
    def missing(executor_id, store, **kw):
        raise FileNotFoundError("claude is not on PATH, so claude cannot run")

    monkeypatch.setattr(S.executor_router, "build", missing)
    with pytest.raises(S.SpecialistError, match="not on PATH"):
        asyncio.run(S.dispatch(ledger, _compose("claude"), "t", "/w", run_id="R1"))
    assert ledger.by_role("minimal") == []
