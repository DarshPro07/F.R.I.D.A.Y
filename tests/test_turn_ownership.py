"""
The reply must not race the objective it just started.

Reconstructed from a real failed voice run. The boss asked for four things in
one breath. A durable run should have taken the work; instead the reply to
that sentence executed all four itself:

    system_resource_usage -> system_list_processes -> apps_open -> web_search

all under one LiveKit speech_id. That is four tool steps against a budget of
three, so LiveKit did what it documents: one final request with tool use
disabled. Gemini answered that with UNEXPECTED_TOOL_CALL, the fallback
inherited the history, and the run died on a missing thought signature.

The provider failure is real and is handled separately. But it was reached by
a request that should never have been in the conversational loop at all, and
that is what these gates hold.

Two things had to be true and neither was:

    admission        the turn becomes a durable run, and says so in the log
                     either way - both of the declining paths used to be
                     silent, so afterwards nobody could tell whether admission
                     had refused the request or had never been asked
    ownership        while that run is fresh, the reply may not act on the
                     world. Matching on capability id alone was not enough:
                     the plan claimed `system_get_info` and the model chose
                     `system_resource_usage`, so the claim covered nothing
                     that was actually duplicated.
"""
from __future__ import annotations
import json
import pytest
from friday import objectives as O
from friday import ownership
from friday.store import Store
COMPOUND = 'Friday, check my computer, open Paint, find one current technology story, and finish the complete job without me saying continue.'
WHAT_THE_REPLY_DID = ['system_resource_usage', 'system_list_processes', 'apps_open', 'web_search']


@pytest.fixture
def admitted(tmp_path):
    """A fresh durable run whose plan does NOT name the ids the model chose."""
    store = Store(tmp_path / "objectives.sqlite3")
    run_id = "RUN-live-shape"
    store.open_objective_run(run_id, request=COMPOUND,
                             objective_summary="the compound request",
                             status=O.RUN_RUNNING)
    # Deliberately the planner's ids, not the model's - that mismatch is the
    # bug this file exists for.
    for task_id, capability in [("t1", "system_get_info"),
                                ("t2", "apps_open"),
                                ("t3", "web_search")]:
        store.save_objective_task(
            task_id=task_id, run_id=run_id, capability=capability,
            arguments="{}", dependencies="[]", status=O.TaskStatus.QUEUED)
    ownership.reset_store(store)
    yield store, run_id
    ownership.reset_store(None)


@pytest.fixture
def objective_store(tmp_path):
    """
    A store of this test's own for the admission path.

    These tests drive `route_input` and `prepare_turn`, which reach the global
    objectives store. Another test file resets that global to its own fixture
    and closes it at teardown, so by the time this file runs the handle can
    already be closed - "Cannot operate on a closed database", from a test that
    passes perfectly well on its own.
    """
    from friday.toolsets import objectives as OT
    store = Store(tmp_path / 'admission.sqlite3')
    OT.reset_store(store)
    ownership.reset_store(store)
    yield store
    OT.reset_store(None)
    ownership.reset_store(None)


def test_the_compound_request_is_classified_as_an_objective():
    from friday.arbiter import classify_input

    assert classify_input(COMPOUND) == "NEW_OBJECTIVE"


def test_admission_logs_its_decision_either_way(objective_store, caplog):
    """
    Both declining paths were silent returns. After a failed live run there
    was no way to tell "admission refused this" from "admission never ran",
    and answering it took a database query rather than a glance at the log.
    """
    import logging
    import agent_friday
    with caplog.at_level(logging.INFO, logger='friday-agent'):
        agent_friday.route_input('what is the time')
    messages = ' '.join((record.message for record in caplog.records))
    assert 'input.classified' in messages, 'the classification was not recorded'
    caplog.clear()
    with caplog.at_level(logging.INFO, logger='friday-agent'):
        agent_friday.admit_objective('what is the time')
    assert 'objective.rejected' in ' '.join((r.message for r in caplog.records)), 'a refusal was not recorded'


@pytest.mark.parametrize("capability_id", WHAT_THE_REPLY_DID)
def test_the_reply_may_not_do_the_objectives_work(admitted, capability_id):
    """
    Every one of the four calls the live reply made must be refused.

    Two of them - `system_resource_usage` and `system_list_processes` - are
    not in the plan at all. Under the old id-matching claim they were allowed,
    which is how a three-task objective still produced four conversational
    tool calls.
    """
    _store, run_id = admitted
    assert ownership.claimed_by(capability_id) == run_id, (
        f"{capability_id} was allowed while objective {run_id} owned the turn")


def test_talking_about_the_objective_still_works(admitted):
    """
    A claim that silenced Friday would be worse than the duplication. The
    reply must still be able to say what is happening.
    """
    for capability_id in ("objective_status", "objective_list",
                          "memory_session_recap", "get_current_time"):
        assert ownership.claimed_by(capability_id) is None, (
            f"{capability_id} was refused, so Friday cannot say what it is "
            f"doing while it does it")


def test_the_claim_lifts_when_the_work_is_finished(admitted, monkeypatch):
    """
    Once the objective has actually opened Paint, asking for Paint *later*
    means it. A claim that outlived the work would refuse the boss for real.

    "Later" is load-bearing, hence the reply window being closed here. The
    reply to the turn that started the objective is not a later request, and
    treating it as one is what let a real voice run do the whole job twice -
    the executor finished in 2.5s and the reply reached its first tool at 4.7s,
    by which time every task was terminal and the claim had released.
    """
    store, _run_id = admitted
    for task in store.objective_tasks('RUN-live-shape'):
        store.update_objective_task(task['task_id'], status=O.TaskStatus.SUCCEEDED)
    monkeypatch.setattr(ownership, 'REPLY_SECONDS', 0.0)
    assert ownership.claimed_by('apps_open') is None, 'the claim outlived the objective that made it'
    assert ownership.claimed_by('system_resource_usage') is None


def test_finished_work_is_still_owned_while_the_reply_is_in_flight(admitted):
    """
    The measured race, as a gate.

    Timings from the live run that motivated it:

        20:38:00.2  objective admitted
        20:38:02.7  every task SUCCEEDED - the whole objective, in 2.5s
        20:38:04.7  the reply reaches its first tool

    The claim had correctly released by then, so the reply ran
    system_resource_usage, apps_open and system_list_processes all over again.
    A finished task must not free its capability while the sentence that
    started it is still being answered.
    """
    store, run_id = admitted
    for task in store.objective_tasks('RUN-live-shape'):
        store.update_objective_task(task['task_id'], status=O.TaskStatus.SUCCEEDED)
    for capability_id in WHAT_THE_REPLY_DID:
        assert ownership.claimed_by(capability_id) == run_id, f"{capability_id} was free {0}s after the objective finished, so the reply can do it again"


def test_the_claim_expires(admitted, monkeypatch):
    """
    A window, not a lock. A long research objective must not refuse "open
    Chrome" half a minute later about something else entirely.
    """
    monkeypatch.setattr(ownership, "CLAIM_SECONDS", 0.0)
    assert ownership.claimed_by("apps_open") is None, \
        "the claim did not expire, so it is a lock"


def test_with_no_objective_running_nothing_is_claimed(tmp_path):
    store = Store(tmp_path / "empty.sqlite3")
    ownership.reset_store(store)
    try:
        for capability_id in WHAT_THE_REPLY_DID:
            assert ownership.claimed_by(capability_id) is None, (
                f"{capability_id} was refused with no objective running")
    finally:
        ownership.reset_store(None)


def test_the_reply_cannot_reach_the_tool_step_ceiling(admitted):
    """
    The failure in one assertion.

    LiveKit's budget is three tool calls per LLM turn; the reply made four and
    triggered the final tools-disabled request that the provider then refused.
    With the objective owning the turn, the reply can make none of them, so
    the ceiling is not reached and that provider path is never entered.
    """
    allowed = [capability_id for capability_id in WHAT_THE_REPLY_DID
               if ownership.claimed_by(capability_id) is None]
    assert len(allowed) == 0, (
        f"the reply could still call {allowed} - {len(allowed)} of "
        f"{len(WHAT_THE_REPLY_DID)} calls, against a budget of 3")


def test_a_second_objective_gets_its_own_task_graph(tmp_path):
    """
    The root cause of the live failure, in one test.

    Task ids were numbered t1, t2, t3 from one *per run*, and
    `objective_tasks.task_id` is the table's PRIMARY KEY. So the second
    objective a Friday installation ever ran did not insert its graph -
    `save_objective_task` upserts ON CONFLICT(task_id), so it overwrote the
    first run's rows and then read back none of its own.

    Only the first objective ever compiled had tasks. Every one after was an
    admitted run with an empty graph, which claims nothing, which means the
    conversational reply did all the work itself.
    """
    from friday import capabilities as caps
    from friday import objectives as O
    store = Store(tmp_path / 'two.sqlite3')
    manifest = caps.as_dicts()
    specs = [{'capability': 'system_get_info', 'arguments': {}, 'dependencies': []}, {'capability': 'apps_open', 'arguments': {'name': 'Paint'}, 'dependencies': []}]
    first = O.compile_objective(store, request='one', tasks=specs, manifest=manifest, objective_summary='one')
    second = O.compile_objective(store, request='two', tasks=specs, manifest=manifest, objective_summary='two')
    assert first['run_id'] != second['run_id']
    assert len(store.objective_tasks(first['run_id'])) == 2, 'the first run lost its tasks to the second'
    assert len(store.objective_tasks(second['run_id'])) == 2, 'the second objective was admitted with an empty task graph'
    total = store._conn.execute('SELECT COUNT(*) c FROM objective_tasks').fetchone()['c']
    assert total == 4, f"{total} task rows for two 2-task runs"


def test_task_ids_are_unique_across_runs(tmp_path):
    from friday import capabilities as caps
    from friday import objectives as O
    store = Store(tmp_path / 'unique.sqlite3')
    specs = [{'capability': 'system_get_info', 'arguments': {}, 'dependencies': []}]
    ids = set()
    for _ in range(3):
        created = O.compile_objective(store, request='r', tasks=specs, manifest=caps.as_dicts(), objective_summary='r')
        for task in store.objective_tasks(created['run_id']):
            assert task['task_id'] not in ids, f"{task['task_id']} was reused across runs"
            ids.add(task['task_id'])


def test_dependencies_still_resolve_after_the_rename(tmp_path):
    """
    Plan-facing names stay t1..tN; only what reaches disk is scoped. A
    dependency that stopped pointing at anything would be a worse bug than
    the one being fixed.
    """
    from friday import capabilities as caps
    from friday import objectives as O
    store = Store(tmp_path / 'deps.sqlite3')
    specs = [{'capability': 'system_get_info', 'arguments': {}, 'dependencies': []}, {'capability': 'apps_open', 'arguments': {'name': 'Paint'}, 'dependencies': ['t1']}]
    created = O.compile_objective(store, request='r', tasks=specs, manifest=caps.as_dicts(), objective_summary='r')
    tasks = {t['capability']: t for t in store.objective_tasks(created['run_id'])}
    dependency = tasks['apps_open']['dependencies']
    assert len(dependency) == 1
    assert dependency[0] == tasks['system_get_info']['task_id'], f"dependency {dependency} does not name a task in this run"
    assert dependency[0].startswith(created['run_id'])


def test_an_admitted_turn_tells_the_model_the_work_is_taken(objective_store, caplog):
    """
    `_admitted_run_id` was set and read nowhere, so admission was a fact about
    the database and a secret from the model. It saw the request and did what
    it was asked.

    On a dictated audit request that meant 205 durable tasks which the
    executor finished in 22 seconds, while the model spent the next seven
    minutes attempting the same audit through search_capabilities and
    use_capability: 22 UNEXPECTED_TOOL_CALL, 32 missing-signature 400s, 11
    total provider failures. The claim could not help - it had released the
    moment the objective finished, long before the reply got that far.
    """
    import logging
    from livekit.agents import llm as lkllm
    import agent_friday
    from friday import objectives as O
    from friday.toolsets import objectives as OT
    store = OT.store()
    for run in store.objective_runs(limit=10):
        if run['status'] not in O.RUN_TERMINAL:
            O.cancel_run(store, run_id=run['run_id'], reason='test', executor_id='test')
    agent = object.__new__(agent_friday.FridayAgent)
    agent._intent = agent._objective_detail = agent._admitted_run_id = ''
    agent._turn_owned_by = ''
    agent._router = _Router(DOMAIN + CONTROL)
    agent._toolset = type('T', (), {'_tools': []})()

    class Learner:
        def observe(self, *args, **kwargs):
            pass
    agent._learner = Learner()
    turn_ctx = lkllm.ChatContext.empty()
    with caplog.at_level(logging.INFO, logger='friday-agent'):
        agent.prepare_turn(turn_ctx, 'check my computer, open Paint, and find a tech story')
    assert agent._admitted_run_id, 'the turn was not admitted'
    said = ' '.join((str(getattr(item, 'text_content', '') or '') for item in turn_ctx.items))
    assert agent._admitted_run_id in said, 'the model was not told which objective owns this turn'
    assert 'NOT carry out that work yourself' in said, 'the model was not told to leave the work alone'
    assert 'use_capability' in said, 'the notice does not close the use_capability route'
    messages = ' '.join((record.message for record in caplog.records))
    assert 'objective.owns_turn' in messages


def test_the_classification_is_logged_once(objective_store, caplog):
    """It was logged in route_input and again in admit_objective."""
    import logging
    import agent_friday
    with caplog.at_level(logging.INFO, logger='friday-agent'):
        agent_friday.route_input('what is the time')
    lines = [r.message for r in caplog.records if r.message.startswith('input.classified')]
    assert len(lines) == 1, f"classification logged {len(lines)} times"


class _Tool:
    def __init__(self, name):
        self.info = type('I', (), {'name': name})()


class _Router:
    """Enough router for the surface tests: what exists, and how to find it."""

    def __init__(self, names):
        self._names = list(names)

    def active_tools(self):
        return [_Tool(name) for name in self._names]

    def invocable(self, name):
        pass

    def search(self, query, limit=4):
        return [{'capability': name} for name in self._names if query in name][:limit]

    def note_used(self, name):
        pass


def _agent_with(names, owned_by=''):
    """A FridayAgent with a known tool surface and no LiveKit behind it."""
    import agent_friday
    agent = object.__new__(agent_friday.FridayAgent)
    agent._turn_owned_by = owned_by
    agent._router = _Router(names)
    agent._toolset = type('T', (), {'_tools': []})()
    return agent
DOMAIN = ['apps_open', 'web_search', 'system_resource_usage', 'files_write', 'music_play']
CONTROL = ['objective_status', 'objective_list', 'objective_cancel']


def test_an_unowned_turn_gets_the_whole_surface():
    agent = _agent_with(DOMAIN + CONTROL)
    offered = [t.info.name for t in agent._reply_tools()]
    assert set(offered) == set(DOMAIN + CONTROL)


def test_an_owned_turn_is_offered_no_domain_tools():
    """
    The structural half. The notice asks the model not to repeat the work;
    this makes it unable to, which is the difference between a request and a
    boundary.
    """
    agent = _agent_with(DOMAIN + CONTROL, owned_by='RUN-owned')
    offered = [t.info.name for t in agent._reply_tools()]
    assert not set(offered) & set(DOMAIN), f"an admitted turn was still offered {sorted(set(offered) & set(DOMAIN))}"
    assert set(offered) == set(CONTROL), 'the reply cannot report on the objective it just started'


def test_ownership_does_not_depend_on_the_claim_still_being_live(admitted, monkeypatch):
    """
    The exact race from the live run, with the claim deliberately dead.

        the objective finished in 22 seconds
        the reply reached its first tool 46 seconds later
        every claim had correctly released by then

    Time-based defence cannot cover that, and this asserts the structural one
    does: no live claim, no fresh run, and still no domain tools.
    """
    store, _run_id = admitted
    for task in store.objective_tasks('RUN-live-shape'):
        store.update_objective_task(task['task_id'], status=O.TaskStatus.SUCCEEDED)
    monkeypatch.setattr(ownership, 'REPLY_SECONDS', 0.0)
    monkeypatch.setattr(ownership, 'CLAIM_SECONDS', 0.0)
    for capability_id in WHAT_THE_REPLY_DID:
        assert ownership.claimed_by(capability_id) is None, 'the claim is supposed to be dead for this test to mean anything'
    agent = _agent_with(DOMAIN + CONTROL, owned_by='RUN-live-shape')
    offered = [t.info.name for t in agent._reply_tools()]
    assert not set(offered) & set(DOMAIN), 'with the claim expired, only the tool surface prevents duplication'


def test_use_capability_is_refused_on_an_owned_turn():
    """
    The route the model actually took. `use_capability` is a function tool on
    the agent, not an MCP tool, so shrinking the MCP surface leaves it wide
    open - and it dispatches any capability by name.
    """
    import asyncio
    import json as _json
    agent = _agent_with(DOMAIN + CONTROL, owned_by='RUN-owned')
    raw = asyncio.run(type(agent).use_capability.__wrapped__(agent, 'apps_open', '{}'))
    answer = _json.loads(raw)
    assert answer['status'] == 'deferred'
    assert 'RUN-owned' in answer['error']
    assert 'not been done twice' in answer['error']


def test_use_capability_still_reports_on_the_objective():
    """Refusing everything would leave Friday unable to say what it is doing."""
    import asyncio
    agent = _agent_with(DOMAIN + CONTROL, owned_by='RUN-owned')
    raw = asyncio.run(type(agent).use_capability.__wrapped__(agent, 'objective_status', '{}'))
    assert 'deferred' not in raw, 'the reply cannot report on the objective that owns the turn'
