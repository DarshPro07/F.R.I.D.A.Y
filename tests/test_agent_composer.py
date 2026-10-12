"""
AGENT-003: the Agent Composer starts a specialist on an executor with a
capped context packet and explicit limits, and the real worker lifecycle is
written to the agent ledger and survives a restart.

Executors without a builder (opencode, codex) and every lifecycle shape are
exercised through the injectable launcher seam, so nothing here depends on
what is installed on the machine; availability is pinned through `probe`.
"""

from __future__ import annotations

import asyncio
import json
import threading

import pytest

from friday import agent_composer as AC
from friday import contracts as c
from friday.agent_ledger import (
    CANCELLED, FAILED, INTERRUPTED, RUNNING, SUCCEEDED, TERMINAL, AgentLedger)
from friday.executors import runs as R
from friday.store import Store

ROUTE = "anthropic/claude-sonnet/5.5/subscription"
LIMITS = AC.Limits(wall_s=5.0, tool_calls=10, spend_usd=1.0)


@pytest.fixture
def db(tmp_path):
    return tmp_path / "composer.sqlite3"


@pytest.fixture
def store(db):
    s = Store(db)
    yield s
    s.close()


@pytest.fixture
def ledger(store):
    return AgentLedger(store)


def _request(executor_id="opencode", **over):
    fields = dict(role_id="minimal", executor_id=executor_id, model_route=ROUTE,
                  goal="fix the bug", workspace="/w", limits=LIMITS,
                  run_id="AGT-1", allowed_tools=("Read", "Edit"))
    fields.update(over)
    return AC.Request(**fields)


def _composer(ledger, launcher=None, executor_id="opencode", probe=None):
    launchers = {executor_id: launcher} if launcher is not None else {}
    return AC.Composer(ledger, launchers=launchers,
                       probe=probe or (lambda _id: None))


def _kinds(ledger, run_id="AGT-1"):
    return [e["kind"] for e in ledger.events(run_id)]


# -- the context packet ---------------------------------------------------


def test_a_packet_over_its_cap_is_cut_and_points_at_the_rest():
    items = (AC.ContextItem("spec", "A" * 400, "docs/spec.md"),
             AC.ContextItem("log", "B" * 5000, "runs/log-7.txt"),
             AC.ContextItem("notes", "C" * 300, "notes/n.md"))
    packet = AC.build_packet(items, cap=600)
    assert len(packet.text) <= 600
    assert packet.truncated is True
    assert packet.omitted_chars > 0
    assert "runs/log-7.txt" in packet.text           # the cut item's reference
    assert "notes/n.md" in packet.text               # an item that did not fit
    assert "docs/spec.md" not in packet.text.split("cut")[-1]
    assert set(packet.refs) == {"runs/log-7.txt", "notes/n.md"}


def test_a_packet_within_its_cap_is_whole():
    packet = AC.build_packet((AC.ContextItem("a", "short text", "x.md"),), cap=600)
    assert packet.truncated is False and packet.omitted_chars == 0
    assert "short text" in packet.text and packet.refs == ()


def test_a_context_item_without_a_reference_is_refused():
    with pytest.raises(AC.ComposerError, match="reference"):
        AC.ContextItem("transcript", "a whole transcript", "")


@pytest.mark.parametrize("bad", [dict(wall_s=0), dict(tool_calls=0),
                                 dict(spend_usd=0)])
def test_a_limit_must_be_explicit_and_positive(bad):
    fields = dict(wall_s=1.0, tool_calls=1, spend_usd=1.0)
    fields.update(bad)
    with pytest.raises(AC.ComposerError, match="limit"):
        AC.Limits(**fields)


# -- the lifecycle in the ledger ----------------------------------------


def test_start_progress_and_completion_each_leave_evidence(ledger):
    seen = {}

    async def launcher(job, channel):
        channel.started()
        channel.progress("read the failing test", tool_calls=2, spend_usd=0.1)
        seen["context"] = "\n".join(job.bundle.context)
        return AC.Outcome(SUCCEEDED, "fixed", evidence="pytest: 3 passed")

    result = asyncio.run(_composer(ledger, launcher).run(
        _request(context=(AC.ContextItem("spec", "the spec", "docs/spec.md"),))))
    assert result.status == SUCCEEDED and result.run_id == "AGT-1"
    assert "the spec" in seen["context"]
    kinds = _kinds(ledger)
    for kind in ("claimed", "composed", "packet", "limits", "worker_started",
                 "progress", "completed", "finished"):
        assert kind in kinds, kinds
    assert all(e["evidence_refs"] for e in ledger.events("AGT-1"))
    row = ledger.run("AGT-1")
    assert (row["status"], row["executor_id"], row["role_id"]) \
        == (SUCCEEDED, "opencode", "minimal")


def test_the_packet_and_limits_are_recorded_as_evidence(ledger):
    async def launcher(job, channel):
        return AC.Outcome(SUCCEEDED, "ok", evidence="e")

    items = (AC.ContextItem("log", "L" * 3000, "runs/log.txt"),)
    asyncio.run(_composer(ledger, launcher).run(
        _request(context=items, packet_cap=500)))
    events = {e["kind"]: e for e in ledger.events("AGT-1")}
    packet = json.loads(events["packet"]["message"])
    assert packet["cap"] == 500 and packet["truncated"] is True
    assert packet["length"] <= 500 and packet["refs"] == ["runs/log.txt"]
    assert "runs/log.txt" in events["packet"]["evidence_refs"]
    limits = json.loads(events["limits"]["message"])
    assert limits["wall_s"] == 5.0 and limits["tool_calls"] == 10
    assert limits["spend_usd"] == 1.0 and limits["allowed_tools"] == ["Read", "Edit"]


def test_a_launcher_that_raises_is_a_recorded_failure(ledger):
    async def launcher(job, channel):
        channel.started()
        raise RuntimeError("provider returned 500")

    result = asyncio.run(_composer(ledger, launcher).run(_request()))
    assert result.status == FAILED and "provider returned 500" in result.reason
    assert ledger.run("AGT-1")["status"] == FAILED
    failure = [e for e in ledger.events("AGT-1") if e["kind"] == "failed"]
    assert failure and "RuntimeError" in failure[0]["message"]
    assert failure[0]["evidence_refs"]


def test_a_failed_outcome_is_a_failed_run(ledger):
    async def launcher(job, channel):
        channel.started()
        return AC.Outcome(FAILED, "tests still red")

    result = asyncio.run(_composer(ledger, launcher).run(_request()))
    assert result.status == FAILED
    assert ledger.run("AGT-1")["status"] == FAILED


def test_a_success_without_evidence_is_refused():
    with pytest.raises(AC.ComposerError, match="evidence"):
        AC.Outcome(SUCCEEDED, "trust me")


def test_cancellation_stops_the_worker_and_is_recorded(ledger):
    reached = {}

    async def launcher(job, channel):
        channel.started()
        await asyncio.sleep(60)
        reached["after"] = True
        return AC.Outcome(SUCCEEDED, "never", evidence="x")

    async def scenario():
        composer = _composer(ledger, launcher)
        task = asyncio.ensure_future(composer.run(_request()))
        for _ in range(100):
            await asyncio.sleep(0.01)
            if "worker_started" in _kinds(ledger):
                break
        assert composer.cancel("AGT-1") is True
        return await task

    result = asyncio.run(scenario())
    assert result.status == CANCELLED and "after" not in reached
    assert ledger.run("AGT-1")["status"] == CANCELLED
    event = [e for e in ledger.events("AGT-1") if e["kind"] == "cancelled"]
    assert event and event[0]["evidence_refs"]


def test_cancelling_an_unknown_run_says_so(ledger):
    assert _composer(ledger).cancel("nope") is False


def test_a_restart_mid_run_reads_interrupted_never_a_phantom_running(
        ledger, db, monkeypatch):
    states = {}

    async def launcher(job, channel):
        monkeypatch.setattr(R, "process_alive", lambda pid, started_at=None: True)
        channel.started(pid=4321)
        channel.progress("working", tool_calls=1)
        states["live"] = ledger.run("AGT-1")["status"]
        # the process is gone and a new FRIDAY opens the same database
        monkeypatch.setattr(R, "process_alive", lambda pid, started_at=None: False)
        restarted = Store(db)
        try:
            states["after_restart"] = AgentLedger(restarted).run("AGT-1")["status"]
            states["active"] = AgentLedger(restarted).active()
        finally:
            restarted.close()
        raise RuntimeError("process died")

    asyncio.run(_composer(ledger, launcher).run(_request()))
    assert states["live"] == RUNNING
    assert states["after_restart"] == INTERRUPTED and states["active"] == []
    assert "interrupted" in _kinds(ledger)


# -- hard limits -----------------------------------------------------------


@pytest.mark.parametrize("limits,usage,stopped_by", [
    (AC.Limits(wall_s=5.0, tool_calls=3, spend_usd=9.0), dict(tool_calls=4), "tool_calls"),
    (AC.Limits(wall_s=5.0, tool_calls=99, spend_usd=0.5), dict(spend_usd=0.6), "spend_usd"),
], ids=["tool_calls", "spend"])
def test_a_usage_limit_stops_the_worker_and_the_stop_is_recorded(
        ledger, limits, usage, stopped_by):
    reached = {}

    async def launcher(job, channel):
        channel.started()
        channel.progress("one step too many", **usage)
        await asyncio.sleep(60)
        reached["after"] = True
        return AC.Outcome(SUCCEEDED, "never", evidence="x")

    result = asyncio.run(_composer(ledger, launcher).run(_request(limits=limits)))
    assert result.status == FAILED and result.stopped_by == stopped_by
    assert "after" not in reached
    stop = [e for e in ledger.events("AGT-1") if e["kind"] == "limit_stop"]
    assert len(stop) == 1 and stopped_by in stop[0]["message"]
    assert stop[0]["evidence_refs"]
    assert ledger.run("AGT-1")["status"] == FAILED


def test_a_limit_stop_holds_even_when_the_launcher_returns_on_its_own(ledger):
    async def launcher(job, channel):
        channel.started()
        channel.progress("over", tool_calls=11)
        return AC.Outcome(SUCCEEDED, "claims success", evidence="x")

    result = asyncio.run(_composer(ledger, launcher).run(_request()))
    assert result.status == FAILED and result.stopped_by == "tool_calls"


def test_the_wall_time_limit_stops_the_worker_and_is_recorded(ledger):
    async def launcher(job, channel):
        channel.started()
        await asyncio.sleep(60)
        return AC.Outcome(SUCCEEDED, "never", evidence="x")

    limits = AC.Limits(wall_s=0.05, tool_calls=10, spend_usd=1.0)
    result = asyncio.run(_composer(ledger, launcher).run(_request(limits=limits)))
    assert result.status == FAILED and result.stopped_by == "wall_s"
    assert [e for e in ledger.events("AGT-1") if e["kind"] == "limit_stop"]
    assert ledger.run("AGT-1")["status"] in TERMINAL


# -- availability, and no impersonation -----------------------------------


def test_an_unavailable_runtime_is_reported_with_its_reason_and_not_replaced(ledger):
    called = []

    async def other(job, channel):
        called.append("claude")
        return AC.Outcome(SUCCEEDED, "wrong runtime", evidence="x")

    async def asked(job, channel):
        called.append("codex")
        return AC.Outcome(SUCCEEDED, "wrong", evidence="x")

    composer = AC.Composer(
        ledger, launchers={"codex": asked, "claude": other},
        probe=lambda eid: "not signed in" if eid == "codex" else None)
    result = asyncio.run(composer.run(_request("codex")))
    assert result.status == AC.UNAVAILABLE and result.reason == "not signed in"
    assert result.executor_id == "codex"
    assert called == []
    assert ledger.run("AGT-1") is None            # nothing ran, nothing claimed


def test_an_executor_with_no_builder_and_no_launcher_is_unavailable(ledger):
    result = asyncio.run(_composer(ledger).run(_request("opencode")))
    assert result.status == AC.UNAVAILABLE and "no builder" in result.reason


def test_an_unknown_executor_or_role_is_refused(ledger):
    unknown = asyncio.run(_composer(ledger).run(_request("nonesuch")))
    assert unknown.status == AC.UNAVAILABLE and "nonesuch" in unknown.reason
    with pytest.raises(AC.ComposerError, match="no-such-role"):
        asyncio.run(_composer(ledger).run(_request(role_id="no-such-role")))


@pytest.mark.parametrize("executor_id", ["opencode", "codex"])
def test_executors_without_a_builder_run_through_the_launcher_seam(
        ledger, executor_id):
    async def launcher(job, channel):
        channel.started()
        return AC.Outcome(SUCCEEDED, "done", evidence="check passed")

    result = asyncio.run(_composer(ledger, launcher, executor_id).run(
        _request(executor_id)))
    assert result.status == SUCCEEDED
    assert ledger.run("AGT-1")["executor_id"] == executor_id


def test_the_default_probe_reports_a_missing_binary(monkeypatch):
    from friday import executor_router

    monkeypatch.setattr(executor_router.Executor, "path", lambda self: None)
    assert AC.default_probe("claude") == "not installed"
    assert "nonesuch" in AC.default_probe("nonesuch")


# -- the real-executor launcher -------------------------------------------


def test_the_executor_launcher_hands_limits_and_packet_to_a_built_executor(
        ledger, monkeypatch):
    from friday import executor_router

    seen = {}

    class Fake:
        async def execute(self, bundle, *, timeout, envelope, **_):
            seen.update(bundle=bundle, timeout=timeout, envelope=envelope)
            prior = c.started(bundle.run_id, "executor.fake")
            return c.succeeded(prior, verification=c.Verification(
                method="pytest", evidence="3 passed"))

    monkeypatch.setattr(executor_router, "build", lambda eid, store, **kw: Fake())
    composer = AC.Composer(ledger, probe=lambda _id: None)
    result = asyncio.run(composer.run(_request(
        "claude", context=(AC.ContextItem("spec", "the spec", "docs/spec.md"),))))
    assert result.status == SUCCEEDED
    assert seen["timeout"] == LIMITS.wall_s
    assert seen["envelope"].tool_call_limit == LIMITS.tool_calls
    assert "the spec" in "\n".join(seen["bundle"].context)
    assert ledger.run("AGT-1")["status"] == SUCCEEDED
    assert "completed" in _kinds(ledger)


# -- sign-in, the event loop, and stopping a real executor ----------------


class _Backend:
    def __init__(self, ok, detail):
        self._ok, self._detail = ok, detail

    def __call__(self):
        return self

    def signed_in(self):
        return self._ok, self._detail


def _installed(monkeypatch, validate=None):
    from friday import executor_router

    monkeypatch.setattr(executor_router.Executor, "path", lambda self: "/bin/x")
    monkeypatch.setattr(executor_router.Executor, "validate_binary",
                        lambda self, found: validate)


@pytest.mark.parametrize("executor_id", ["claude", "codex"])
def test_an_executor_that_is_not_signed_in_is_unavailable(monkeypatch, executor_id):
    from friday import cli_model_provider

    _installed(monkeypatch)
    monkeypatch.setattr(cli_model_provider, "BACKENDS",
                        {"claude": _Backend(False, "signed out"),
                         "codex": _Backend(False, "signed out")})
    assert AC.default_probe(executor_id) == "not signed in: signed out"


def test_a_signed_in_executor_falls_through_to_its_binary_check(monkeypatch):
    from friday import cli_model_provider

    _installed(monkeypatch, validate="binary does not run")
    monkeypatch.setattr(cli_model_provider, "BACKENDS",
                        {"claude": _Backend(True, "ok")})
    assert AC.default_probe("claude") == "binary does not run"
    _installed(monkeypatch, validate=None)
    assert AC.default_probe("claude") is None


def test_the_composer_reports_a_signed_out_claude_unavailable_and_claims_nothing(
        ledger, monkeypatch):
    from friday import cli_model_provider

    _installed(monkeypatch)
    monkeypatch.setattr(cli_model_provider, "BACKENDS",
                        {"claude": _Backend(False, "signed out")})
    result = asyncio.run(AC.Composer(ledger).run(_request("claude")))
    assert result.status == AC.UNAVAILABLE
    assert result.reason == "not signed in: signed out"
    assert ledger.run("AGT-1") is None


def test_the_availability_probe_runs_off_the_event_loop(ledger):
    seen = {}

    def probe(_id):
        seen["probe"] = threading.get_ident()
        return None

    async def launcher(job, channel):
        channel.started()
        return AC.Outcome(SUCCEEDED, "done", evidence="ok")

    async def go():
        seen["loop"] = threading.get_ident()
        return await AC.Composer(
            ledger, launchers={"opencode": launcher}, probe=probe).run(_request())

    assert asyncio.run(go()).status == SUCCEEDED
    assert seen["probe"] != seen["loop"]


class _HangingExecutor:
    """Runs until `cancel()` is called, like a real process-backed executor:
    cancelling its task would orphan the process, asking it to stop does not."""

    def __init__(self):
        self.cancelled = 0
        self.released = False
        self._stop = asyncio.Event()

    async def execute(self, bundle, *, timeout, envelope, **_):
        started = c.started(bundle.run_id, "executor.fake")
        try:
            await self._stop.wait()
        finally:
            self.released = True            # sandbox closed, lease released
        return c.failed(started, "the run was stopped")

    async def cancel(self):
        self.cancelled += 1
        self._stop.set()
        return True


def _build_returning(monkeypatch, executor):
    from friday import executor_router

    monkeypatch.setattr(executor_router, "build", lambda eid, store, **kw: executor)


def test_cancelling_a_real_executor_run_asks_the_executor_to_stop(
        ledger, monkeypatch):
    fake = _HangingExecutor()
    _build_returning(monkeypatch, fake)
    composer = AC.Composer(ledger, probe=lambda _id: None)

    async def go():
        running = asyncio.ensure_future(composer.run(_request("claude")))
        for _ in range(100):
            if "AGT-1" in composer._tasks:
                break
            await asyncio.sleep(0.01)
        await asyncio.sleep(0.05)
        assert composer.cancel("AGT-1")
        return await running

    result = asyncio.run(go())
    assert result.status == CANCELLED
    assert fake.cancelled == 1 and fake.released
    assert ledger.run("AGT-1")["status"] in TERMINAL


def test_the_wall_time_limit_asks_a_real_executor_to_stop(ledger, monkeypatch):
    fake = _HangingExecutor()
    _build_returning(monkeypatch, fake)
    limits = AC.Limits(wall_s=0.1, tool_calls=10, spend_usd=1.0)
    result = asyncio.run(AC.Composer(ledger, probe=lambda _id: None).run(
        _request("claude", limits=limits)))
    assert result.status == FAILED and result.stopped_by == "wall_s"
    assert fake.cancelled == 1 and fake.released
    assert [e for e in ledger.events("AGT-1") if e["kind"] == "limit_stop"]
    assert ledger.run("AGT-1")["status"] in TERMINAL


def _executor_returning(make):
    class Fake:
        async def execute(self, bundle, *, timeout, envelope, **_):
            return make(c.started(bundle.run_id, "executor.fake"))
    return Fake()


def test_an_executor_tool_call_stop_is_recorded_as_a_limit_stop(ledger, monkeypatch):
    _build_returning(monkeypatch, _executor_returning(
        lambda prior: prior.finish(
            status=c.NOT_PERMITTED,
            error="ENVELOPE: tool-call limit 10 reached; the run was stopped")))
    result = asyncio.run(AC.Composer(ledger, probe=lambda _id: None).run(
        _request("claude")))
    assert result.status == FAILED and result.stopped_by == "tool_calls"
    stops = [e for e in ledger.events("AGT-1") if e["kind"] == "limit_stop"]
    assert stops and "tool-call limit 10" in stops[0]["message"]


def test_an_executor_timeout_is_recorded_as_a_wall_time_stop(ledger, monkeypatch):
    _build_returning(monkeypatch, _executor_returning(
        lambda prior: c.failed(prior, "claude exceeded 5s; the run was killed")))
    result = asyncio.run(AC.Composer(ledger, probe=lambda _id: None).run(
        _request("claude")))
    assert result.status == FAILED and result.stopped_by == "wall_s"
    assert [e for e in ledger.events("AGT-1") if e["kind"] == "limit_stop"]


def test_an_ordinary_executor_failure_is_not_a_limit_stop(ledger, monkeypatch):
    _build_returning(monkeypatch, _executor_returning(
        lambda prior: c.failed(prior, "the executor failed: boom")))
    result = asyncio.run(AC.Composer(ledger, probe=lambda _id: None).run(
        _request("claude")))
    assert result.status == FAILED and result.stopped_by == ""
    assert not [e for e in ledger.events("AGT-1") if e["kind"] == "limit_stop"]
