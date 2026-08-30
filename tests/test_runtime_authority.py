"""
What actually authorises a capability call, and what only looks like it does.

Every capability the runtime can resolve takes `run` as its first parameter.
That is a real invariant and `test_capability_reach.py` checks it - but it is a
*structural* guard: it proves the dispatcher is able to hand over the right
context, and nothing more. It says nothing about whether policy ran, whether
the caller was allowed, or whether the evidence that came back belongs to the
run the decision was made under.

Those are separate properties and they are checked here, because a signature
shape reported as a security property is the kind of claim that survives
right up until somebody depends on it.
"""
from __future__ import annotations
import pytest
from friday import capabilities as C
from friday import capability_runtime as R
from friday import contracts as c
from friday import policy as p


@pytest.fixture
def runtime():
    return R.CapabilityRuntime()


def test_the_runtime_supplies_a_run_when_the_caller_does_not(runtime):
    """A capability is never invoked without a durable run behind it."""
    result = runtime.execute("files_roots", {})
    assert result.run_id, "a capability ran with no run context"


def test_the_result_is_bound_to_the_authorising_run(runtime):
    run = c.Run.create("bind", capability="files_roots")
    result = runtime.execute("files_roots", {}, run=run)
    assert result.run_id == run.run_id
    assert result in run.results, "the result was not filed against its run"


def test_a_capability_cannot_return_another_runs_result(runtime, monkeypatch):
    """
    The hole the `run`-first invariant does not cover.

    Policy is evaluated against the run the call was made under. If the
    capability may then return an ActionResult carrying a different run_id,
    the decision and the evidence come apart: authorised here, recorded
    there. Nothing prevented that until it was checked for.
    """
    elsewhere = c.Run.create("elsewhere", capability="files_roots")
    foreign = c.started(elsewhere.run_id, "files_roots").finish(
        status=c.FAILED, error="from another run")

    class Substitute:
        module, qualname = "test", "substitute"

        def load(self):
            return lambda run, **kwargs: foreign

    monkeypatch.setitem(R.resolutions(), "files_roots", Substitute())

    here = c.Run.create("here", capability="files_roots")
    result = runtime.execute("files_roots", {}, run=here)

    assert result.status == c.FAILED
    assert result.run_id == here.run_id, "the foreign result was passed through"
    assert elsewhere.run_id in (result.error or ""), \
        "the refusal does not say which run it came from"


def test_arguments_cannot_smuggle_in_a_run(runtime):
    """
    The LLM chooses the arguments. It must not be able to choose the run they
    execute under - the run is the thing policy was evaluated against.
    """
    run = c.Run.create("smuggle", capability="files_roots")
    other = c.Run.create("other", capability="files_roots")
    result = runtime.execute("files_roots", {"run": other}, run=run)

    assert result.status == c.FAILED
    assert result.run_id == run.run_id


def test_policy_runs_for_every_capability_the_runtime_dispatches(runtime):
    """
    A durable objective is not a licence. The same table, the same decisions.
    """
    engine = p.PolicyEngine()
    confirming = [cap.id for cap in C._ALL if cap.id in R.reachable() and any((t in p.TOOL_CATEGORIES and engine.decide(t).decision == p.CONFIRM for t in cap.policy_tool_ids()))]
    assert confirming, 'no CONFIRM capability is reachable; this proves nothing'
    for capability_id in confirming:
        result = runtime.execute(capability_id, {})
        assert result.status in (c.CANCELLED, c.FAILED), f"{capability_id} is CONFIRM and ran without one (status {result.status})"


@pytest.mark.parametrize('capability_id', ['power_shutdown', 'power_restart', 'process_terminate'])
def test_read_material_provenance_cannot_reach_a_destructive_capability(runtime, capability_id):
    """
    Something Friday read is not somebody asking. Provenance is fixed when the
    run is created, so this is decided before the capability is chosen, and it
    can only ever narrow - it never grants.

    Scoped to DESTRUCTIVE deliberately. `files_recycle` is not covered, and
    that is a decision rather than an oversight: the Recycle Bin is the undo,
    so recycling sits with the other file writes (friday/policy.py, at
    "files.recycle"). An earlier version of this test asserted the block for
    files_recycle and failed - it was asserting a property the system does not
    claim to have.
    """
    run = c.Run.create('a web page said to do this', capability=capability_id)
    run.provenance = c.READ_MATERIAL
    result = runtime.execute(capability_id, {}, run=run)
    assert result.status in (c.FAILED, c.CANCELLED)
    assert 'BLOCKED' in (result.error or ''), f"read material reached {capability_id}: {result.error!r}"


def test_recycling_is_reachable_from_read_material_and_that_is_deliberate():
    """
    The converse, written down so the gap is visible rather than assumed.

    Under FULL autonomy an ASK becomes an AUTO, so a page Friday read can
    cause a file to be recycled without anyone being asked. That is bounded by
    the Recycle Bin being recoverable and by the workspace jail, and it is the
    documented trade. It is recorded here because an unstated gap and a
    decided one look identical from the outside.
    """
    assert p.TOOL_CATEGORIES['files.recycle'] not in p.DESTRUCTIVE
    assert p.provenance_verdict('files.recycle', c.READ_MATERIAL) is None


def test_the_signature_guard_is_not_claimed_as_authorisation():
    """
    A note in executable form, so the distinction survives a refactor.

    `run` being the first parameter of all resolved implementations is what
    makes the domain-prefix resolution rule safe - a same-named helper that
    does not take a run is not an implementation. It is a compatibility
    invariant. The authorisation properties are the four tests above.
    """
    import inspect

    for capability_id in R.reachable():
        function = R.resolutions()[capability_id].load()
        first = list(inspect.signature(function).parameters)[:1]
        assert first == ["run"], f"{capability_id} takes {first}"


def test_an_async_capability_is_awaited_before_it_is_checked():
    """
    Some capabilities are coroutines: `web_answer`, `web_crawl`,
    `automations_run`. The run-binding check reads a field off the result, and
    a coroutine has no fields - so adding that check broke every async
    capability at once, and nothing went red until one was dispatched for
    real. The reach tests resolve them and the authority tests exercised only
    synchronous ones.
    """
    import asyncio
    import inspect
    from friday import objective_cli
    dispatch = objective_cli.build_dispatch()
    asynchronous = [capability_id for capability_id in R.reachable() if inspect.iscoroutinefunction(R.resolutions()[capability_id].load())]
    assert asynchronous, 'no async capability resolved; this proves nothing'
    for capability_id in asynchronous[:4]:
        result = asyncio.run(dispatch(capability_id, {}))
        assert result.get('run_id'), f"{capability_id} came back with no run attached"
        assert 'coroutine' not in str(result.get('error') or ''), f"{capability_id} was checked before it was awaited: {result}"
