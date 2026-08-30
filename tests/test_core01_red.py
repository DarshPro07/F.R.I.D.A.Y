"""
CORE-01 gates: admission, reach, and a graph that is actually a graph.

These three failures are one failure wearing three costumes, and fixing any
one alone makes the system worse rather than better:

    admission without reach   objectives start, reach 5 of 132, and abandon
                              the rest silently - worse than today, because
                              today's failure is at least visible
    reach without admission   nothing admits anything, so nothing changes
    either without the graph  one unlucky task strands every independent one

So they ship together, and they are asserted together, here.

Each test states the behaviour that *should* hold. All three fail on the
current tree. That is the point: they are the definition of CORE-01 being
done, written before it is done.
"""
from __future__ import annotations
import asyncio
import pytest
from friday import capabilities as C
from friday import objectives as O
from friday.store import Store


@pytest.fixture
def store(tmp_path):
    store = Store(tmp_path / "core01.sqlite3")
    yield store
    store.close()
MUST_REACH = ['music_play', 'power_lock', 'windows_list', 'brightness_get', 'audio_sessions', 'documents_inspect', 'apps_close']


def _dispatch():
    from friday import objective_cli

    return objective_cli.build_dispatch()


@pytest.mark.parametrize("capability_id", MUST_REACH)
def test_red_a_an_objective_can_reach_a_registered_capability(capability_id):
    """
    Reachable means "the executor resolves it", not "it succeeded".

    The distinction matters: `power_lock` will refuse without a confirmation
    and `music_play` may fail for want of ffmpeg, and both of those are
    correct, informative outcomes. What must not happen is LookupError, which
    says the capability does not exist - about a capability the same process
    has registered and can call from conversation.
    """
    assert capability_id in {cap.id for cap in C._ALL}, \
        f"{capability_id} is not registered; this test is measuring nothing"

    dispatch = _dispatch()
    try:
        asyncio.run(dispatch(capability_id, {}))
    except LookupError as exc:
        pytest.fail(
            f"{capability_id} is registered and reachable in conversation, "
            f"and the objective executor says: {exc}")
    except Exception:
        # Anything else is the capability itself talking - a policy refusal, a
        # missing argument, absent hardware. All of those are reach.
        pass


def test_red_a_reach_is_most_of_the_registry_not_a_handful():
    """
    The number, asserted, so it cannot quietly regress to five again.

    Deliberately measured from `capability_runtime.reachable()` rather than
    from "did the dispatch avoid raising". The first version of this test did
    the latter, and it stopped measuring anything the moment CORE01-B started
    returning NOT_CONFIGURED instead of LookupError for the adapter-only 28:
    every registered id then "passed", and the assertion would have held at a
    reach of zero.

    A test that cannot fail for the reason it names is worse than no test,
    because it reads like cover.

    The shortfall is honest. 28 capabilities keep their logic in the MCP
    adapter and return a plain dict with no ActionResult beneath it. Wrapping
    those in a synthetic result would manufacture evidence, so they are
    declared adapter-only and closed in CORE-02.
    """
    from friday import capability_runtime
    registered = {cap.id for cap in C._ALL}
    reachable = set(capability_runtime.reachable())
    unresolved = set(capability_runtime.unresolved())
    assert reachable | unresolved == registered, 'a capability is in neither list; the accounting has a hole'
    assert len(reachable) >= len(registered) - 30, f"{len(reachable)} of {len(registered)} capabilities resolve to a real implementation; CORE-01 requires all but the adapter-only ones"
    assert len(reachable) >= 90, f"reach collapsed to {len(reachable)}; it was 5 before CORE-01 and must never return there"


def test_red_a_an_unresolved_capability_says_why_rather_than_vanishing():
    """
    The 28 must be refused informatively, not reported as non-existent.

    "No such capability" about something Friday has registered and can call in
    conversation is the csrss.exe mistake again: describing a thing that is
    plainly there as absent.
    """
    from friday import capability_runtime
    unresolved = capability_runtime.unresolved()
    assert unresolved, 'nothing is unresolved; this test is measuring nothing'
    dispatch = _dispatch()
    result = asyncio.run(dispatch(unresolved[0], {}))
    assert result['status'] == 'not_configured'
    assert unresolved[0] in result['error']
    assert 'adapter' in result['error'].lower()
COMPOUND = 'Check my system, open Paint, find one AI story, and create a temporary note.'


class _Learner:
    def observe(self, *args, **kwargs):
        pass

    def inject(self, ctx):
        pass


class _Stub:
    """Just enough FridayAgent for the unbound hook to run."""
    prepare_turn = None
    read_before_answering = None

    def __init__(self):
        self._learner = _Learner()
        self._chat_ctx = None
        self._turn_owned_by = ''
        self._toolset = type('T', (), {'_tools': []})()
        self._router = type('R', (), {'active_tools': lambda self: []})()

    def _apply_tools(self):
        self._toolset._tools = []


class _Message:
    def __init__(self, text):
        self.text_content = text


def test_red_b_a_compound_request_becomes_a_durable_run(store, monkeypatch):
    """
    The root cause, stated as a requirement.

    `classify_input` already returns NEW_OBJECTIVE for this sentence and has
    since it was written. What has never existed is anything that acts on it:
    on_user_turn_completed calls prepare_turn, which observes for the learner
    and returns. No run is created, so the model has to carry a six-part
    request through a four-step tool budget, and cannot.
    """
    import agent_friday as A
    from friday.arbiter import classify_input
    assert classify_input(COMPOUND) == 'NEW_OBJECTIVE', 'the arbiter no longer classifies this as compound'
    from friday.toolsets import objectives as OT
    OT.reset_store(store)
    _Stub.prepare_turn = A.FridayAgent.prepare_turn
    _Stub.read_before_answering = A.FridayAgent.read_before_answering
    asyncio.run(A.FridayAgent.on_user_turn_completed(_Stub(), None, _Message(COMPOUND)))
    runs = store._conn.execute('SELECT run_id, status FROM objective_runs').fetchall()
    assert len(runs) == 1, f"a compound request produced {len(runs)} durable runs; exactly one was required"
    assert runs[0]['status'] not in O.RUN_TERMINAL


def test_red_b_an_ordinary_turn_does_not_touch_the_turn_context(store, monkeypatch):
    """
    The latency incident, as a guard.

    `prepare_turn` used to append a briefing to turn_ctx on *every* turn.
    LiveKit begins generating before this hook returns and discards that work
    if the context changed underneath it, so every single reply was generated
    twice and the first thrown away - felt, correctly, as Friday being slow.

    That is what this protects, and it is about ordinary turns: the ones that
    are not admitted, which is nearly all of them. An admitted turn is
    deliberately different and is covered by the test below - the briefing
    cost every reply, the ownership notice costs only the rare compound one,
    and the thing it buys is the model not doing the work twice.
    """
    import agent_friday as A

    class Recorder:
        def __init__(self):
            self.mutated = False

        def add_message(self, *args, **kwargs):
            self.mutated = True

        def insert(self, *args, **kwargs):
            self.mutated = True

        @property
        def items(self):
            return []
    from friday.toolsets import objectives as OT
    OT.reset_store(store)
    turn_ctx = Recorder()
    _Stub.prepare_turn = A.FridayAgent.prepare_turn
    _Stub.read_before_answering = A.FridayAgent.read_before_answering
    asyncio.run(A.FridayAgent.on_user_turn_completed(_Stub(), turn_ctx, _Message('what time is it')))
    assert not turn_ctx.mutated, 'an ordinary turn mutated turn_ctx; every reply will now generate twice'


def test_red_b_an_admitted_turn_tells_the_model_it_is_owned(store, monkeypatch):
    """
    The other side of the trade, and why it is worth paying.

    Admission used to be a fact about the database and a secret from the
    model, which saw the request and did as it was asked. On a dictated audit
    request that meant 205 durable tasks the executor finished in 22 seconds,
    while the model spent seven minutes attempting the same audit through
    search_capabilities and use_capability - 22 UNEXPECTED_TOOL_CALL, 32
    missing-signature 400s, 11 total provider failures. The claim could not
    help: it had released the moment the objective finished, long before the
    reply got that far.

    So an admitted turn does rewrite the context, and does lose the head start
    preemptive generation gives. That cost lands only on compound requests;
    the briefing bug landed on all of them.
    """
    import agent_friday as A
    from friday.toolsets import objectives as OT
    OT.reset_store(store)

    class Recorder:
        def __init__(self):
            self.said = []

        def add_message(self, role='', content='', **kwargs):
            self.said.append(f"{role}: {content}")

        @property
        def items(self):
            return []
    turn_ctx = Recorder()
    _Stub.prepare_turn = A.FridayAgent.prepare_turn
    _Stub.read_before_answering = A.FridayAgent.read_before_answering
    asyncio.run(A.FridayAgent.on_user_turn_completed(_Stub(), turn_ctx, _Message(COMPOUND)))
    notice = ' '.join(turn_ctx.said)
    assert notice, 'an admitted turn told the model nothing'
    assert 'NOT carry out that work yourself' in notice
    assert 'use_capability' in notice, 'the notice leaves the use_capability route open'


def test_red_b_a_simple_request_does_not_become_a_run(store, monkeypatch):
    """
    Not everything is an objective. "What time is it" must stay fast, and a
    system that opens a durable run for every utterance has replaced one
    failure with a slower one.
    """
    import agent_friday as A
    from friday.toolsets import objectives as OT
    OT.reset_store(store)
    _Stub.prepare_turn = A.FridayAgent.prepare_turn
    _Stub.read_before_answering = A.FridayAgent.read_before_answering
    asyncio.run(A.FridayAgent.on_user_turn_completed(_Stub(), None, _Message('what time is it')))
    runs = store._conn.execute('SELECT run_id FROM objective_runs').fetchall()
    assert runs == [], 'a simple question opened a durable objective run'


def test_red_c_independent_clauses_do_not_depend_on_each_other():
    """
    `plan_objective` ends with:

        task["dependencies"] = [f"t{index - 1}"] if index > 1 else []

    which makes checking the system a prerequisite for opening Paint. It is
    not. The executor beneath it already supports a real graph - it promotes a
    task only when every dependency succeeded (continuous.py:338) and skips
    dependents of a failure (continuous.py:466) - so the capability is bought
    and thrown away one line before it is used.
    """
    from friday.toolsets import objectives as OT

    plan = OT.plan_objective(
        "check my system, open Paint, and find one AI story",
        C.as_dicts())
    tasks = plan["tasks"]
    assert len(tasks) >= 3, f"expected three independent steps, got {tasks}"

    dependent = [t for t in tasks if t.get("dependencies")]
    assert not dependent, (
        "independent clauses were chained: "
        + ", ".join(f"{t['capability']}<-{t['dependencies']}" for t in dependent))


def test_red_c_a_failure_does_not_strand_its_siblings(store):
    """
    A B C D E, where B fails and only D depends on it.

    Expected: A, C and E still run. D is skipped because its dependency died.
    The run is PARTIAL - some of it worked, and saying otherwise in either
    direction would be false.
    """
    compiled = O.compile_objective(store, request='five things, one of which fails', objective_summary='failure locality', manifest=[{'id': 'ok'}, {'id': 'boom'}], tasks=[{'capability': 'ok', 'arguments': {'n': 1}}, {'capability': 'boom', 'arguments': {}}, {'capability': 'ok', 'arguments': {'n': 3}}, {'capability': 'ok', 'arguments': {'n': 4}, 'dependencies': ['t2']}, {'capability': 'ok', 'arguments': {'n': 5}}])
    run_id = compiled['run_id']

    async def call(capability_id, arguments):
        if capability_id == 'boom':
            raise RuntimeError('this one was always going to fail')
        return {'status': 'succeeded', 'output': arguments, 'verification': {'method': 'test', 'evidence': 'ran'}}

    async def drive():
        from friday.continuous import ContinuousTaskExecutor
        executor = ContinuousTaskExecutor(store, call_capability=call)
        try:
            deadline = asyncio.get_event_loop().time() + 5.0
            while asyncio.get_event_loop().time() < deadline:
                row = store.objective_run(run_id)
                if row is not None and row['status'] in O.RUN_TERMINAL:
                    return
                await executor.start(run_id)
                await asyncio.sleep(0.02)
        finally:
            executor.stop()
    asyncio.run(drive())
    rows = {row['task_id'].rsplit('-', 1)[-1]: row['status'] for row in store._conn.execute('SELECT task_id, status FROM objective_tasks WHERE run_id = ?', (run_id,)).fetchall()}
    assert rows['t1'] == O.TASK_SUCCEEDED
    assert rows['t2'] == O.TASK_FAILED
    assert rows['t3'] == O.TASK_SUCCEEDED, 'an independent task was stranded by an unrelated failure'
    assert rows['t4'] == O.TASK_SKIPPED, 'a task whose dependency failed should be skipped, not run'
    assert rows['t5'] == O.TASK_SUCCEEDED, 'an independent task was stranded by an unrelated failure'
    run = store._conn.execute('SELECT status FROM objective_runs WHERE run_id = ?', (run_id,)).fetchone()
    assert run['status'] == O.RUN_PARTIAL
