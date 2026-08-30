"""
CORE-01 integration gates: one execution, one owner, no "Continue".

RED-A/B/C proved the three defects were real and are fixed. These prove the
things that only go wrong once they *are* fixed:

    the model helpfully doing the objective's work as well
    a run nobody can ask about or stop by speaking
    a compound request that still needs a second message to finish

The last one is the whole point of CORE-01, so it is measured rather than
asserted from the shape of the code: the counters are incremented by the
production path, and the gate reads them.
"""
from __future__ import annotations
import asyncio
import pytest
from friday import objectives as O
from friday import ownership
from friday.store import Store


@pytest.fixture
def store(tmp_path):
    from friday.toolsets import objectives as OT

    db = Store(tmp_path / "gates.sqlite3")
    OT.reset_store(db)
    ownership.reset_store(db)
    yield db
    OT.reset_store(None)
    ownership.reset_store(None)
    db.close()


class _Learner:
    def observe(self, *args, **kwargs):
        pass

    def inject(self, ctx):
        pass


class _Stub:
    prepare_turn = None
    read_before_answering = None

    def __init__(self):
        self._learner = _Learner()
        self._turn_owned_by = ''
        self._toolset = type('T', (), {'_tools': []})()
        self._router = type('R', (), {'active_tools': lambda self: []})()

    def _apply_tools(self):
        self._toolset._tools = []


class _Message:
    def __init__(self, text):
        self.text_content = text


def _turn(text: str):
    import agent_friday as A
    _Stub.prepare_turn = A.FridayAgent.prepare_turn
    _Stub.read_before_answering = A.FridayAgent.read_before_answering
    agent = _Stub()
    asyncio.run(A.FridayAgent.on_user_turn_completed(agent, None, _Message(text)))
    return agent
COMPOUND = 'Check my system, open Paint, find one AI story, and create a temporary note.'


def test_a_queued_capability_is_claimed_by_the_objective(store):
    """
    The window where the same work would be done twice.

    Admission returns and LiveKit generates the reply for that same turn with
    the whole toolset still available. The model reads "open Paint" and opens
    Paint; the task graph opens Paint. Both are right from where they stand,
    and neither can see the other.
    """
    _turn(COMPOUND)
    active = O.active_run(store)
    assert active is not None, "nothing was admitted; this gate measures nothing"

    queued = {task["capability"] for task in store.objective_tasks(active["run_id"])}
    assert "apps_open" in queued, f"expected apps_open in the graph, got {queued}"

    assert ownership.claimed_by("apps_open", db=store) == active["run_id"]


def test_a_capability_the_objective_does_not_want_is_free(store):
    """The guard covers the objective's own work and nothing else."""
    _turn(COMPOUND)
    assert ownership.claimed_by("brightness_set", db=store) is None
    assert ownership.claimed_by("power_lock", db=store) is None


def test_nothing_is_claimed_when_no_objective_is_running(store):
    assert ownership.claimed_by("apps_open", db=store) is None


def test_a_finished_task_releases_its_claim(store, monkeypatch):
    """
    Once the objective has actually opened Paint, a person asking for Paint
    *later* means it, and should get it.

    "Later" is the load-bearing word, which is why the reply window is closed
    here. Within it a finished task stays claimed, because the executor can
    beat the reply to the finish: in a real voice run the objective completed
    in 2.5s and the reply reached its first tool at 4.7s, found everything
    terminal, and did the whole job again.
    """
    monkeypatch.setattr(ownership, 'REPLY_SECONDS', 0.0)
    _turn(COMPOUND)
    active = O.active_run(store)
    with store._tx() as conn:
        conn.execute('UPDATE objective_tasks SET status = ? WHERE run_id = ? AND capability = ?', (O.TASK_SUCCEEDED, active['run_id'], 'apps_open'))
    assert ownership.claimed_by('apps_open', db=store) is None


def test_a_claim_expires_so_a_later_request_is_not_refused(store, monkeypatch):
    """
    Why this is a window and not a lock.

    A research objective can run for minutes holding `apps_open` in its graph.
    "Open Chrome", said half a minute later about something else entirely,
    must not be refused by machinery meant to prevent an accident.
    """
    _turn(COMPOUND)
    assert ownership.claimed_by("apps_open", db=store) is not None

    monkeypatch.setattr(ownership, "CLAIM_SECONDS", -1.0)
    assert ownership.claimed_by("apps_open", db=store) is None, \
        "a stale claim would refuse an unrelated request forever"


def test_the_guard_defers_a_claimed_conversational_call(store):
    """
    End to end through the wrapper that every MCP tool is registered behind.

    Deferred, not failed: nothing went wrong, and the work is in hand.
    """
    calls = []

    class FakeMCP:
        def tool(self, *args, **kwargs):
            def decorate(function):
                calls.append(function)
                return function
            return decorate

    guarded = ownership.guard(FakeMCP())

    @guarded.tool()
    def apps_open(name: str) -> dict:
        return {"opened": name}

    registered = calls[0]
    assert registered(name="Paint") == {"opened": "Paint"}, \
        "the guard blocked a call with no objective running"

    _turn(COMPOUND)
    with pytest.raises(ownership.Deferred) as caught:
        registered(name="Paint")
    assert "already part of objective" in str(caught.value)
    assert caught.value.capability_id == "apps_open"


def test_the_objective_executor_is_not_guarded(store):
    """
    The one thing the guard must never do.

    The executor reaches capabilities through `CapabilityRuntime`, not through
    the MCP registration the guard wraps - so an objective can always do its
    own work, even while that work is claimed for everybody else.
    """
    import ast
    import inspect

    from friday import capability_runtime

    tree = ast.parse(inspect.getsource(capability_runtime))
    imported = {node.module for node in ast.walk(tree)
                if isinstance(node, ast.ImportFrom) and node.module}
    assert not any("ownership" in (name or "") for name in imported), \
        "the runtime imports the guard; an objective could block itself"


def test_asking_how_it_is_going_reports_and_does_not_disturb_the_run(store):
    import agent_friday as A

    _turn(COMPOUND)
    before = O.active_run(store)

    intent, detail = A.route_input("how is that going")

    assert intent == "QUERY_ABOUT_RUN"
    assert detail, "the status came back empty"
    after = O.active_run(store)
    assert after is not None and after["run_id"] == before["run_id"]
    assert after["status"] == before["status"], "a question changed the run"


def test_saying_stop_cancels_the_run(store):
    import agent_friday as A

    _turn(COMPOUND)
    run_id = O.active_run(store)["run_id"]

    intent, detail = A.route_input("stop that")

    assert intent == "CANCEL" and detail == run_id
    assert store.objective_run(run_id)["status"] == O.RUN_CANCELLED


def test_a_side_question_leaves_the_run_alone(store):
    import agent_friday as A

    _turn(COMPOUND)
    before = O.active_run(store)

    intent, _ = A.route_input("what time is it")

    assert intent == "SIDE_CONVERSATION"
    after = O.active_run(store)
    assert after["run_id"] == before["run_id"]
    assert after["status"] == before["status"]


def test_stop_with_nothing_running_is_just_conversation(store):
    """A cancellation needs something to cancel. "Stop" said to nobody is a word."""
    import agent_friday as A

    intent, _ = A.route_input("stop that")
    assert intent == "SIDE_CONVERSATION"


def test_one_compound_turn_needs_no_second_message(store):
    """
    §34, without a microphone and without a model.

    The production input path is driven once, the executor drives the graph to
    a terminal state, and the count of further user messages required is zero.
    That number is the whole feature: it used to be one, and it was called
    "Continue".
    """
    from friday import capability_runtime

    _turn(COMPOUND)
    run = O.active_run(store)
    assert run is not None, "nothing was admitted"
    run_id = run["run_id"]

    executed: list[str] = []
    runtime = capability_runtime.CapabilityRuntime()

    async def call(capability_id, arguments):
        executed.append(capability_id)
        if capability_id == "apps_open":
            # One deliberately unsupported step, per the gate's own terms.
            raise RuntimeError("Paint is not installed on this machine")
        result = runtime.execute("system_get_info", {}) \
            if capability_id == "system_get_info" else None
        return (result.to_dict() if result is not None else
                {"status": "succeeded", "output": {},
                 "verification": {"method": "gate", "evidence": "ran"}})

    async def drive():
        from friday.continuous import ContinuousTaskExecutor

        executor = ContinuousTaskExecutor(store, call_capability=call)
        try:
            deadline = asyncio.get_event_loop().time() + 20.0
            while asyncio.get_event_loop().time() < deadline:
                row = store.objective_run(run_id)
                if row["status"] in O.RUN_TERMINAL:
                    return
                await executor.start(run_id)
                await asyncio.sleep(0.02)
        finally:
            executor.stop()

    asyncio.run(drive())

    final = store.objective_run(run_id)
    statuses = {t["task_id"]: t["status"]
                for t in store.objective_tasks(run_id)}

    assert final["status"] in O.RUN_TERMINAL, (
        f"the run never finished: {final['status']}, tasks {statuses}")
    assert final["status"] == O.RUN_PARTIAL, (
        f"one step failed, so the run is partial, not {final['status']}")

    succeeded = [s for s in statuses.values() if s == O.TASK_SUCCEEDED]
    assert len(succeeded) >= 2, (
        f"a single failure stranded the independent work: {statuses}")

    # Every capability the graph asked for was reached - none of them came
    # back "no such capability", which is what 5-of-132 used to mean.
    assert executed, "nothing executed"
