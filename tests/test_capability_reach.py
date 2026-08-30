"""
Every capability is in exactly one place, and the count can go down.

The failure this guards against is not a broken capability. It is a number
that stops meaning anything: a reach metric that counts "did not raise" will
read 100% the moment refusals become polite, and a partition that leaves
things out will read whatever the person writing it hoped.

So: exactly one bucket each, the buckets sum to the registry, resolutions load
real callables, and a mutation that hollows the runtime out makes this file go
red rather than green.
"""
from __future__ import annotations
import pytest
from friday import capabilities as C
from friday import capability_runtime as R
from friday import policy as p
DECLARED_CLASSES = {'NEEDS_EXTRACTION', 'SESSION_REQUIRED', 'ADAPTER_PURE'}
PURE = {'format_json', 'word_count'}


def test_the_partition_is_complete_and_disjoint():
    registered = {cap.id for cap in C._ALL}
    reachable = set(R.reachable())
    unresolved = set(R.unresolved())

    assert reachable | unresolved == registered, (
        "these are in neither bucket: "
        f"{sorted(registered - reachable - unresolved)}")
    assert not (reachable & unresolved), (
        "these are in both: " + str(sorted(reachable & unresolved)))


def test_every_reachable_capability_loads_a_real_callable():
    """
    Resolution is a promise about execution. A name that resolves to something
    that cannot be called is a reach number with nothing behind it.
    """
    for capability_id in R.reachable():
        resolution = R.resolutions()[capability_id]
        function = resolution.load()
        assert callable(function), f"{capability_id} resolved to a non-callable"


def test_every_reachable_capability_takes_a_run():
    """
    The ActionResult contract, checked rather than assumed. This is also what
    makes the domain-prefix rule safe: a same-named helper that does not take
    a run is not an implementation and must never bind.
    """
    import inspect

    for capability_id in R.reachable():
        function = R.resolutions()[capability_id].load()
        parameters = list(inspect.signature(function).parameters)
        assert parameters and parameters[0] == "run", \
            f"{capability_id} -> {parameters[:3]}, which is not a capability"


def test_the_pure_transforms_stay_where_they_are():
    """
    Not every capability should become reachable, and saying so out loud stops
    somebody closing the gap by wrapping a string function in a fake result.
    """
    unresolved = set(R.unresolved())
    for capability_id in PURE:
        assert capability_id in unresolved, (
            f"{capability_id} became 'reachable' - if that is a real service "
            f"now, remove it from PURE deliberately; if it is a wrapper around "
            f"a pure function, it is manufacturing evidence")


def test_reach_is_most_of_the_registry():
    registered = {cap.id for cap in C._ALL}
    reachable = set(R.reachable())

    proportion = len(reachable) / len(registered)
    assert proportion > 0.80, (
        f"{len(reachable)}/{len(registered)} = {proportion:.0%} reachable; "
        f"it was 5/132 = 4% before CORE-01 and must not slide back")


def test_an_unresolved_capability_is_refused_with_its_reason():
    """
    "No such capability" about something Friday has registered is the csrss.exe
    mistake: describing as absent a thing that is plainly present.
    """
    import asyncio

    from friday import objective_cli

    dispatch = objective_cli.build_dispatch()
    unresolved = sorted(R.unresolved())
    assert unresolved, "nothing unresolved; this test measures nothing"

    result = asyncio.run(dispatch(unresolved[0], {}))
    assert result["status"] == "not_configured"
    assert unresolved[0] in result["error"]


def test_a_capability_registered_nowhere_still_raises():
    """
    The distinction NOT_CONFIGURED must not erase: a hallucinated tool name is
    a different fact from a real capability that cannot run here.
    """
    import asyncio

    from friday import objective_cli

    dispatch = objective_cli.build_dispatch()
    with pytest.raises(LookupError):
        asyncio.run(dispatch("definitely_not_a_capability", {}))


def test_policy_covers_the_capabilities_or_fails_closed():
    """
    A capability with no policy category is not a hole - policy treats unknown
    as ASK. This test records how many there are so the number is visible and
    shrinks deliberately.
    """
    unaudited = [cap.id for cap in C._ALL
                 if not any(t in p.TOOL_CATEGORIES
                            for t in cap.policy_tool_ids())]
    assert len(unaudited) <= 8, (
        f"{len(unaudited)} capabilities have no declared policy category: "
        f"{sorted(unaudited)}")
    for capability_id in unaudited:
        assert C.by_id(capability_id).requires_approval, \
            f"{capability_id} is unaudited and does not fail closed"


def test_a_hollowed_out_runtime_would_be_caught(monkeypatch):
    """
    §24-J. If resolution stopped binding anything - every capability polite,
    every call NOT_CONFIGURED - the reach gate must go red rather than green.

    This is the specific way a reach metric rots: refusals get tidier, nothing
    raises, and a test that counted exceptions reports success at a reach of
    zero. That happened to the first version of this gate, which is why it now
    reads the classification instead.
    """
    monkeypatch.setattr(R, "_RESOLUTIONS", {})

    registered = {cap.id for cap in C._ALL}
    assert set(R.reachable()) == set(), "the mutation did not take"

    with pytest.raises(AssertionError):
        proportion = len(R.reachable()) / len(registered)
        assert proportion > 0.80, "reach collapsed"
