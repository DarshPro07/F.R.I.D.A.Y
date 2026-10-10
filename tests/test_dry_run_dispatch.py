"""SIDE-002: the live capability dispatch runs the dry run before a
consequential capability, so the person sees the expected effects before
approving, and a larger blast radius escalates the approval.

Everything goes through `CapabilityRuntime.execute` (what `build_dispatch`
wraps) with stand-in capabilities and a stub policy engine, so nothing here
depends on the real registry, the real policy table or machine state.
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from friday import capabilities as C
from friday import capability_runtime as CR
from friday import confirmation, dry_run
from friday import contracts as c
from friday import policy as PO


class _Engine:
    """Policy that says AUTO (or ASK) for everything."""

    def __init__(self, decision: str = PO.AUTO) -> None:
        self.decision = decision

    def decide(self, tool_id: str) -> PO.Verdict:
        return PO.Verdict(tool_id, "stand-in", self.decision, "stand-in policy")


def _cap(cid: str, **fields) -> C.Capability:
    base = dict(id=cid, description=f"{cid} stand-in", execution_scope="network",
                side_effect="read")
    base.update(fields)
    return C.Capability(**base)


CAPS = {
    "t_read": _cap("t_read", side_effect="read"),
    "t_local_write": _cap("t_local_write", execution_scope="agent_runtime",
                          side_effect="write"),
    "t_flagged": _cap("t_flagged", execution_scope="agent_runtime",
                      side_effect="write", requires_confirmation=True),
    "t_external": _cap("t_external", side_effect="external_action", risk="MEDIUM"),
    "t_core": _cap("t_core", side_effect="external_action", risk="IRREVERSIBLE",
                   reversible=False),
}


@pytest.fixture
def world(monkeypatch):
    calls: list[tuple[str, dict]] = []

    def make(cid):
        def function(run, **arguments):
            calls.append((cid, arguments))
            return run.record(c.started(run.run_id, cid).finish(
                status=c.SUCCEEDED,
                verification=c.Verification("stand-in", "the stand-in ran")))
        return function

    monkeypatch.setattr(C, "by_id", lambda cid: CAPS.get(cid))
    monkeypatch.setattr(CR, "resolutions", lambda: {
        cid: SimpleNamespace(load=lambda cid=cid: make(cid)) for cid in CAPS})
    return SimpleNamespace(calls=calls, book=confirmation.Book())


def _runtime(world, decision=PO.AUTO):
    return CR.CapabilityRuntime(engine=_Engine(decision), book=world.book)


def test_consequential_capability_is_held_with_its_effects_before_it_runs(world):
    run = c.Run.create("sync", capability="t_external")
    result = _runtime(world).execute("t_external", {"to": "a@b.c"}, run=run)
    assert result.status == c.CANCELLED
    assert world.calls == []                       # nothing happened yet
    assert result.error.startswith("APPROVAL_REQUIRED")
    assert "integration" in result.error           # the blast radius
    assert "an external action outside FRIDAY" in result.error  # the effects
    (pending,) = world.book.pending.values()      # the yes is asked, bound to it
    assert pending.action == "t_external" and pending.arguments == {"to": "a@b.c"}
    assert "an external action outside FRIDAY" in pending.question
    assert pending.nonce in result.error


def test_the_approved_yes_runs_it_once_and_only_for_that_action(world):
    run = c.Run.create("sync", capability="t_external")
    runtime = _runtime(world)
    held = runtime.execute("t_external", {"to": "a@b.c"}, run=run)
    (pending,) = world.book.pending.values()
    assert held.status == c.CANCELLED

    # An unapproved nonce is not a yes.
    unapproved = runtime.execute(
        "t_external", {"to": "a@b.c", "confirmation_nonce": pending.nonce}, run=run)
    assert unapproved.status == c.CANCELLED and world.calls == []

    world.book.approve(pending.nonce)
    # The yes was for these arguments, not others.
    other = runtime.execute(
        "t_external", {"to": "x@y.z", "confirmation_nonce": pending.nonce}, run=run)
    assert other.status == c.CANCELLED and world.calls == []

    done = runtime.execute(
        "t_external", {"to": "a@b.c", "confirmation_nonce": pending.nonce}, run=run)
    assert done.status == c.SUCCEEDED
    # The nonce is a marker, not an argument of the capability.
    assert world.calls == [("t_external", {"to": "a@b.c"})]

    again = runtime.execute(
        "t_external", {"to": "a@b.c", "confirmation_nonce": pending.nonce}, run=run)
    assert again.status == c.CANCELLED and len(world.calls) == 1


def test_a_larger_blast_radius_escalates_the_approval(world):
    runtime = _runtime(world)
    local = runtime.execute("t_local_write", {}, run=c.Run.create("w", capability="t_local_write"))
    assert local.status == c.SUCCEEDED             # local radius: no extra yes

    mid = runtime.execute("t_external", {}, run=c.Run.create("m", capability="t_external"))
    big = runtime.execute("t_core", {}, run=c.Run.create("b", capability="t_core"))
    assert mid.status == big.status == c.CANCELLED
    assert "integration" in mid.error and "core" in big.error
    assert "cannot be undone" in big.error
    assert [call[0] for call in world.calls] == ["t_local_write"]


def test_a_local_write_still_shows_its_dry_run_but_asks_nothing(world, monkeypatch):
    seen = []
    real = dry_run.propose

    def spy(book, run_id, tool_id, arguments=None, **options):
        seen.append(tool_id)
        return real(book, run_id, tool_id, arguments, **options)

    monkeypatch.setattr(dry_run, "propose", spy)
    result = _runtime(world).execute("t_local_write", {}, run=c.Run.create("w"))
    assert result.status == c.SUCCEEDED
    assert seen == ["t_local_write"] and world.book.pending == {}


def test_policy_keeps_the_say_on_a_capability_flag_below_the_radius_floor(world):
    # Policy allowed it, and its blast radius is local: the dry run only adds
    # a floor, it does not re-ask what policy already answered.
    result = _runtime(world).execute("t_flagged", {}, run=c.Run.create("f"))
    assert result.status == c.SUCCEEDED
    assert world.book.pending == {}


def test_a_non_consequential_capability_takes_no_dry_run(world, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("a read took a dry run")

    monkeypatch.setattr(dry_run, "propose", forbidden)
    monkeypatch.setattr(dry_run, "dry_run", forbidden)
    result = _runtime(world).execute("t_read", {}, run=c.Run.create("r"))
    assert result.status == c.SUCCEEDED
    assert world.book.pending == {}


def test_an_ask_from_policy_shows_the_effects_too(world):
    result = _runtime(world, PO.ASK).execute(
        "t_external", {}, run=c.Run.create("m", capability="t_external"))
    assert result.status == c.CANCELLED
    assert result.error.startswith("APPROVAL_REQUIRED")
    assert "integration" in result.error
    assert "an external action outside FRIDAY" in result.error
    assert world.calls == []


def test_the_async_dispatch_port_holds_a_consequential_capability(world, monkeypatch):
    monkeypatch.setattr(confirmation, "book", world.book)
    dispatch = CR.build_dispatch(engine=_Engine())
    out = asyncio.run(dispatch("t_external", {}, run=c.Run.create("m", capability="t_external")))
    assert out["status"] == c.CANCELLED
    assert "an external action outside FRIDAY" in out["error"]
    assert world.calls == []


def test_a_confirm_from_policy_shows_the_effects_too(world):
    # The real policy table returns CONFIRM, not ASK, for the capabilities
    # that need a person (power_*, process_terminate): the effects must be in
    # front of the person for that tier as well.
    result = _runtime(world, PO.CONFIRM).execute(
        "t_core", {}, run=c.Run.create("m", capability="t_core"))
    assert result.status == c.CANCELLED
    assert result.error.startswith("APPROVAL_REQUIRED")
    assert "[CONFIRM]" in result.error
    assert "core" in result.error
    assert "Expected effects" in result.error
    assert world.calls == []


def test_a_confirm_on_a_non_consequential_capability_adds_no_dry_run(world):
    result = _runtime(world, PO.CONFIRM).execute(
        "t_read", {}, run=c.Run.create("m", capability="t_read"))
    assert result.status == c.CANCELLED
    assert "Expected effects" not in result.error
    assert world.calls == []
