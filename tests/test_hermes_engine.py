"""Hermes as the single execution engine: shared memory, token-aware depth,
and the router preferring it over the coding-CLI fallbacks."""
from __future__ import annotations

import asyncio

import pytest

from friday import contracts as c
from friday import execution_economics as ee
from friday import executor_router as R
from friday import hermes_bridge as hb


# --- shared memory reaches the sub-agent ------------------------------------

def test_bundle_with_memory_pulls_from_the_one_memory_stack(monkeypatch):
    from friday import memory_stack
    seen = {}

    def fake_aggregate(task, budget_tokens=None, include_episodes=True):
        seen["task"], seen["budget"] = task, budget_tokens
        seen["episodes"] = include_episodes
        return {"prompt": "YOUR PREFERENCES AND RULES:\n- tabs: spaces"}

    monkeypatch.setattr(memory_stack, "aggregate", fake_aggregate)
    b = hb.TaskBundle(goal="refactor the parser").with_memory()
    # A worker gets rules/specs/relations, never the spoken transcript.
    assert seen == {"task": "refactor the parser", "budget": 600, "episodes": False}
    assert "tabs: spaces" in b.memory_context
    assert "WHAT FRIDAY ALREADY KNOWS" in b.render()
    assert "tabs: spaces" in b.render()


def test_memory_that_cannot_be_read_costs_nothing(monkeypatch):
    from friday import memory_stack
    monkeypatch.setattr(memory_stack, "aggregate",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("db")))
    b = hb.TaskBundle(goal="x").with_memory()
    assert b.memory_context == ""
    assert "WHAT FRIDAY ALREADY KNOWS" not in b.render()


def test_memory_is_bounded_not_the_store(monkeypatch):
    from friday import memory_stack
    monkeypatch.setattr(memory_stack, "aggregate",
                        lambda task, budget_tokens=None, **kw: {"prompt": "x" * 20000})
    b = hb.TaskBundle(goal="x").with_memory()
    assert b.measure()["oversized"]  # reported, so the caller can see it


def test_delegate_sends_effort_and_memory_to_the_gateway(tmp_path, monkeypatch):
    from friday import memory_stack
    monkeypatch.setattr(memory_stack, "aggregate",
                        lambda task, budget_tokens=None, **kw: {"prompt": "- owner likes brevity"})
    calls = []

    class Sup(hb.HermesSupervisor):
        def start(self):
            self.state = hb.SESSION_READY

        def request(self, method, params, *, timeout=30.0):
            calls.append((method, params))
            if method == "session.create":
                return {"session_id": "s1", "info": {"model": "m"}}
            return {}

    sup = Sup(log=hb.WorkRunLog(tmp_path / "w.sqlite3"))
    sup.state = hb.DISCONNECTED
    sup.delegate(hb.TaskBundle(goal="rename foo"), reasoning_effort="low")
    create = dict(calls[0][1])
    assert calls[0][0] == "session.create"
    assert create["reasoning_effort"] == "low"
    submit = dict(calls[1][1])
    assert "owner likes brevity" in submit["text"]


# --- token-aware depth ------------------------------------------------------

def test_plan_delegation_maps_tier_to_effort():
    tiny = ee.plan_delegation("rename the variable foo to bar in utils.py")
    deep = ee.plan_delegation(
        "design the architecture for a memory-sharing layer between "
        "Friday and Hermes sub-agents, evaluate trade-offs")
    assert tiny["effort"] == "low" and tiny["tier"] == ee.TIER_ECONOMY
    assert deep["effort"] == "high" and deep["tier"] == ee.TIER_DEEP
    assert "effort=" in tiny["reason"]


def test_caller_pins_win_and_are_recorded():
    p = ee.plan_delegation("anything", model="claude-sonnet-5", effort="xhigh")
    assert p["model"] == "claude-sonnet-5" and p["effort"] == "xhigh"
    assert "pinned by caller" in p["reason"]


def test_every_tier_has_a_valid_hermes_effort():
    valid = {"none", "minimal", "low", "medium", "high", "xhigh", "max", "ultra"}
    for tier in (ee.TIER_ECONOMY, ee.TIER_STANDARD, ee.TIER_DEEP):
        assert ee.resolve_effort(tier) in valid


# --- the router: Hermes is the engine, Claude the fallback -------------------

def test_hermes_is_the_default_and_claude_the_fallback(monkeypatch):
    monkeypatch.setattr(R.shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr("friday.executors.hermes._hermes_python",
                        lambda: "D:/hermes/venv/python.exe")
    monkeypatch.setattr("friday.executors.cli.claude_path", lambda: "/x/claude")
    choice = R.choose("build")
    assert choice.executor == "hermes"
    assert "claude" in choice.alternatives


def test_without_hermes_claude_is_chosen_and_labelled(monkeypatch):
    monkeypatch.setattr(R.shutil, "which", lambda name: None)
    monkeypatch.setattr("friday.executors.hermes._hermes_python", lambda: None)
    monkeypatch.setattr("friday.executors.cli.claude_path", lambda: "/x/claude")
    choice = R.choose("build")
    assert choice.executor == "claude"


def test_build_constructs_the_hermes_executor(monkeypatch):
    monkeypatch.setattr("friday.executors.hermes._hermes_python", lambda: "py")
    from friday.executors.hermes import HermesExecutor
    assert isinstance(R.build("hermes", store=None), HermesExecutor)


# --- the executor contract --------------------------------------------------

def test_hermes_executor_returns_an_honest_envelope(tmp_path):
    from friday.executors.claude_code import TaskBundle as DevBundle
    from friday.executors.hermes import HermesExecutor

    class FakeSup:
        def delegate(self, bundle, **kw):
            assert kw["reasoning_effort"]
            assert kw["workspace"] == str(tmp_path)
            return {"work_run_id": "hermes-1",
                    "result": {"status": hb.COMPLETE, "result": "done",
                               "model": "claude-opus-5"}}

    ex = HermesExecutor(None, supervisor=FakeSup())
    res = asyncio.run(ex.execute(DevBundle(goal="add tests", workspace=str(tmp_path),
                                           acceptance=("tests pass",))))
    assert res.status == "succeeded"
    assert res.output["work_run_id"] == "hermes-1"
    assert "not this claim" in res.verification.evidence


def test_hermes_executor_reports_unavailable_not_a_crash(tmp_path):
    from friday.executors.claude_code import TaskBundle as DevBundle
    from friday.executors.hermes import HermesExecutor

    class Dead:
        def delegate(self, *a, **k):
            raise hb.HermesUnavailable("no gateway")

    res = asyncio.run(HermesExecutor(None, supervisor=Dead()).execute(
        DevBundle(goal="x", workspace=str(tmp_path))))
    assert res.status == "failed" and "unavailable" in res.error


class _Turn:
    """A fake gateway whose one turn runs until it is interrupted, or until
    `ends_after` seconds pass with it still WORKING (the wait timed out)."""

    def __init__(self, ends_after: float = 5.0, started: bool = True) -> None:
        import threading
        self.entered, self.stopped = threading.Event(), threading.Event()
        #: cleared = the gateway is still starting; no work-run row exists yet
        self.started = threading.Event()
        if started:
            self.started.set()
        self.ends_after, self.interrupted, self.rows = ends_after, [], []
        self.log = self

    def for_friday_run(self, friday_run_id):
        return [r for r in self.rows if r["friday_run_id"] == friday_run_id]

    def delegate(self, bundle, *, friday_run_id, **kw):
        self.entered.set()
        assert self.started.wait(5)
        self.rows.append({"work_run_id": "hermes-1", "friday_run_id": friday_run_id,
                          "status": "WORKING"})
        self.entered.set()
        stopped = self.stopped.wait(self.ends_after)
        status = hb.PARTIAL if stopped else "WORKING"
        self.rows[-1]["status"] = status
        return {"work_run_id": "hermes-1", "result": {"status": status}}

    def interrupt(self, work_run_id):
        self.interrupted.append(work_run_id)
        self.stopped.set()
        return {}


def test_cancelling_the_hermes_executor_interrupts_its_live_work_run(tmp_path):
    """AGENT-003: the composer's cancel reaches what Hermes is running, not
    just the asyncio task, and the turn does not block the event loop."""
    from friday.executors.claude_code import TaskBundle as DevBundle
    from friday.executors.hermes import HermesExecutor

    turn = _Turn()
    ex = HermesExecutor(None, supervisor=turn)

    async def scenario():
        work = asyncio.ensure_future(ex.execute(
            DevBundle(goal="x", workspace=str(tmp_path)), timeout=30))
        assert await asyncio.wait_for(asyncio.to_thread(turn.entered.wait, 5), 2)
        assert not work.done()
        assert await ex.cancel() is True
        return await asyncio.wait_for(work, 5)

    res = asyncio.run(scenario())
    assert turn.interrupted == ["hermes-1"]
    assert res.status == c.PARTIAL
    assert asyncio.run(ex.cancel()) is False          # nothing live is left


def test_a_cancel_before_hermes_has_a_work_run_still_stops_the_turn(tmp_path):
    """The cancel lands while the gateway is still starting (no row yet); the
    turn is interrupted as soon as its row appears, not left to run."""
    from friday.executors.claude_code import TaskBundle as DevBundle
    from friday.executors.hermes import HermesExecutor

    turn = _Turn(started=False)
    ex = HermesExecutor(None, supervisor=turn)

    async def scenario():
        work = asyncio.ensure_future(ex.execute(
            DevBundle(goal="x", workspace=str(tmp_path)), timeout=30))
        assert await asyncio.wait_for(asyncio.to_thread(turn.entered.wait, 5), 2)
        assert await ex.cancel() is True              # armed, nothing to hit yet
        assert turn.interrupted == []
        turn.started.set()
        return await asyncio.wait_for(work, 5)

    res = asyncio.run(scenario())
    assert turn.interrupted == ["hermes-1"]
    assert res.status == c.PARTIAL


def test_a_cancelled_task_leaves_its_live_turn_reachable_by_cancel(tmp_path):
    """The composer's fallback cancels the awaiting task; the thread still
    runs delegate, so the run must stay findable until the thread ends."""
    from friday.executors.claude_code import TaskBundle as DevBundle
    from friday.executors.hermes import HermesExecutor

    turn = _Turn()
    ex = HermesExecutor(None, supervisor=turn)

    async def scenario():
        work = asyncio.ensure_future(ex.execute(
            DevBundle(goal="x", workspace=str(tmp_path)), timeout=30))
        assert await asyncio.wait_for(asyncio.to_thread(turn.entered.wait, 5), 2)
        work.cancel()
        with pytest.raises(asyncio.CancelledError):
            await work
        assert await ex.cancel() is True
        assert await asyncio.wait_for(asyncio.to_thread(turn.stopped.wait, 5), 6)

    asyncio.run(scenario())
    assert turn.interrupted == ["hermes-1"]


def test_cancel_with_nothing_in_flight_does_not_build_a_supervisor():
    from friday.executors.hermes import HermesExecutor

    ex = HermesExecutor(None)
    assert asyncio.run(ex.cancel()) is False
    assert ex._supervisor is None


def test_one_failing_interrupt_does_not_skip_the_others(tmp_path):
    from friday.executors.hermes import HermesExecutor

    class _Two:
        def __init__(self):
            self.log, self.interrupted = self, []

        def for_friday_run(self, run_id):
            return [{"work_run_id": f"w-{run_id}", "status": "WORKING"}]

        def interrupt(self, work_run_id):
            self.interrupted.append(work_run_id)
            if work_run_id == "w-a":
                raise TimeoutError("gateway did not answer")

    sup = _Two()
    ex = HermesExecutor(None, supervisor=sup)
    ex._running.update({"a", "b"})
    assert asyncio.run(ex.cancel()) is True
    assert sorted(sup.interrupted) == ["w-a", "w-b"]


def test_a_hermes_turn_that_outlives_its_wait_is_stopped_and_says_it_timed_out(tmp_path):
    from friday import agent_composer
    from friday.executors.claude_code import TaskBundle as DevBundle
    from friday.executors.hermes import HermesExecutor

    turn = _Turn(ends_after=0.0)
    res = asyncio.run(HermesExecutor(None, supervisor=turn).execute(
        DevBundle(goal="x", workspace=str(tmp_path)), timeout=7))
    assert res.status == "failed"
    assert "hermes run hermes-1 exceeded 7s" in res.error
    assert agent_composer._EXECUTOR_TIMEOUT.search(res.error)
    assert turn.interrupted == ["hermes-1"]


def test_the_bridge_bundle_carries_the_development_bundle_reason(tmp_path):
    from friday import governor as G
    from friday.executors.claude_code import TaskBundle as DevBundle
    from friday.executors.hermes import to_bridge_bundle

    assert to_bridge_bundle(DevBundle(goal="x", workspace=str(tmp_path))
                            ).spawn_reason == G.SPECIALISATION
    assert to_bridge_bundle(DevBundle(goal="x", workspace=str(tmp_path),
                                      spawn_reason=G.PARALLEL)).spawn_reason == G.PARALLEL
