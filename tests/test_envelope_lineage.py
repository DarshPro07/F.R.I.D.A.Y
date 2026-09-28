"""
TRUST-002 / SEC-005 (PRD v7): lineage and the execution envelope survive the
durable objective path.

Before this, `objective_runs` had no provenance, attendance or envelope, and
both dispatch ports ran every task as the person, attended (V7-D04). An
objective derived from something FRIDAY read therefore reached the
capability runtime looking exactly like one the person asked for, and the
provenance gate that refuses read material a destructive tool never saw it.

Now the objective records its lineage and an envelope when it is compiled,
the executor rebuilds each task's Run from them, and the runtime refuses a
capability outside the envelope or after it expires. A run written before
envelopes existed has none, and waits for the person rather than running
under an assumed one.
"""
from __future__ import annotations

import ast
import asyncio
import inspect
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from friday import capability_runtime as CR
from friday import contracts as c
from friday import objectives as O
from friday import policy as P
from friday.continuous import ContinuousTaskExecutor
from friday.store import Store

ROOT = Path(__file__).resolve().parent.parent
PAST = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat(timespec="seconds")


@pytest.fixture
def store(tmp_path):
    db = Store(tmp_path / "lineage.sqlite3")
    yield db
    db.close()


@pytest.fixture
def shutdown_recorder(monkeypatch):
    """power_shutdown resolved to a recorder, so nothing here can ever reach
    the real shutdown and the test can tell whether a refusal came first."""
    called = []

    class _Recorder:
        capability_id, module, function = "power_shutdown", "tests", "recorder"

        def load(self):
            def recorder(run, **arguments):
                called.append(run.provenance)
                return c.succeeded(c.started(run.run_id, "power_shutdown"),
                                   "recorded, not performed")
            return recorder

    real = CR.resolutions()
    monkeypatch.setattr(CR, "resolutions", lambda: {**real, "power_shutdown": _Recorder()})
    return called


def _compile(store, capability, **lineage) -> str:
    created = O.compile_objective(
        store, request=f"please {capability}",
        tasks=[{"capability": capability, "arguments": {}}],
        manifest=[{"id": capability, "description": capability}],
        objective_summary=f"run {capability}", **lineage)
    return created["run_id"]


def _drive(store, run_id, dispatch, seconds: float = 20.0):
    """The executor over one run until it is terminal or parked."""
    async def go():
        executor = ContinuousTaskExecutor(store, call_capability=dispatch)
        try:
            loop = asyncio.get_running_loop()
            deadline = loop.time() + seconds
            while loop.time() < deadline:
                status = store.objective_run(run_id)["status"]
                if status in O.RUN_TERMINAL or status in O.RUN_WAITING_STATUSES:
                    return
                await executor.start(run_id)
                await asyncio.sleep(0.02)
        finally:
            executor.stop()

    asyncio.run(go())
    return store.objective_run(run_id), store.objective_tasks(run_id)


def _dangerous_dispatch():
    return CR.build_dispatch(engine=P.PolicyEngine(autonomy=P.DANGEROUS))


# --- lineage on the durable path (V7-D04) ---------------------------------

def test_read_material_is_refused_a_destructive_capability_on_the_durable_path(
        store, shutdown_recorder):
    run_id = _compile(store, "power_shutdown",
                      provenance=c.READ_MATERIAL, attended=False)
    _, tasks = _drive(store, run_id, _dangerous_dispatch())

    assert shutdown_recorder == [], "the shutdown was reached"
    assert "came from something Friday read" in json.dumps(tasks), tasks


def test_the_persons_objective_reaches_the_ordinary_question(store, shutdown_recorder):
    """The control: the refusal above is about where the objective came from,
    not about the capability. The same task from the person meets the policy
    question an irreversible action always meets."""
    run_id = _compile(store, "power_shutdown", provenance=c.PERSON, attended=True)
    _, tasks = _drive(store, run_id, _dangerous_dispatch())

    assert shutdown_recorder == []
    text = json.dumps(tasks)
    assert "came from something Friday read" not in text
    assert "APPROVAL_REQUIRED" in text, tasks


def test_an_objective_records_its_lineage_and_its_envelope(store):
    run_id = _compile(store, "system_get_info",
                      provenance=c.READ_MATERIAL, attended=False)
    row = store.objective_run(run_id)
    assert row["provenance"] == c.READ_MATERIAL
    assert row["attended"] == 0
    envelope = c.Envelope.from_dict(json.loads(row["envelope"]))
    assert envelope.run_id == run_id and envelope.objective_id == run_id
    assert envelope.provenance == c.READ_MATERIAL and envelope.attended is False
    assert envelope.allowed_capabilities == ("system_get_info",)
    assert envelope.expires_at, "an envelope without an expiry lasts forever"


def test_a_reopened_run_keeps_the_envelope_it_was_compiled_with(store):
    """A restart re-opens the run it was reconciling; it may not swap in a
    wider envelope on the way."""
    run_id = _compile(store, "system_get_info", provenance=c.PERSON, attended=True)
    before = store.objective_run(run_id)["envelope"]
    wider = c.Envelope(run_id=run_id, objective_id=run_id,
                       allowed_capabilities=(c.ANY_CAPABILITY,))
    store.open_objective_run(run_id, request="again", objective_summary="again",
                             provenance=c.PERSON, attended=True,
                             envelope=wider.to_dict())
    assert store.objective_run(run_id)["envelope"] == before


# --- the envelope, enforced ------------------------------------------------

def test_a_capability_outside_the_envelope_is_refused(store):
    run_id = "OBJ-ENVELOPE-1"
    envelope = O.envelope_for(run_id, ["system_get_info"], provenance=c.PERSON,
                              attended=True).narrow(allowed_capabilities=())
    _compile(store, "system_get_info", provenance=c.PERSON, attended=True,
             run_id=run_id, envelope=envelope)
    _, tasks = _drive(store, run_id, _dangerous_dispatch())
    assert "outside this run's envelope" in json.dumps(tasks), tasks


def test_an_expired_envelope_refuses_every_capability(store):
    run_id = "OBJ-ENVELOPE-2"
    envelope = c.Envelope(run_id=run_id, objective_id=run_id,
                          allowed_capabilities=("system_get_info",), expires_at=PAST)
    _compile(store, "system_get_info", provenance=c.PERSON, attended=True,
             run_id=run_id, envelope=envelope)
    final, tasks = _drive(store, run_id, _dangerous_dispatch())
    # The budget parks it before anything is dispatched (tests/test_envelope_limits.py).
    assert final["status"] == O.RUN_PAUSED
    assert "expired" in (final.get("blocker") or ""), final
    assert all(int(t.get("attempts") or 0) == 0 for t in tasks), tasks


def test_an_envelope_for_another_run_is_refused_at_compile(store):
    with pytest.raises(c.ContractError):
        _compile(store, "system_get_info", provenance=c.PERSON, attended=True,
                 run_id="OBJ-A", envelope=c.Envelope(run_id="OBJ-B"))


# --- a supplied envelope is admitted only if it is no wider ------------------
# Slice 2 review: compile took a caller's envelope on trust, so an envelope
# with no expiry, or granting what the plan never uses, ran as issued.

def _minted(run_id, capabilities=("system_get_info",)):
    return O.envelope_for(run_id, capabilities, provenance=c.PERSON, attended=True)


def test_a_supplied_envelope_with_no_expiry_is_refused_at_compile(store):
    run_id = "OBJ-ADMIT-1"
    forever = _minted(run_id)
    forever = c.Envelope(**{**forever.__dict__, "expires_at": ""})
    with pytest.raises(c.ContractError, match="expiry"):
        _compile(store, "system_get_info", provenance=c.PERSON, attended=True,
                 run_id=run_id, envelope=forever)
    assert store.objective_run(run_id) is None, "a refused envelope persisted nothing"


def test_a_supplied_envelope_cannot_grant_what_the_plan_does_not_use(store):
    run_id = "OBJ-ADMIT-2"
    wider = _minted(run_id, ("system_get_info", "terminal_run"))
    with pytest.raises(c.ContractError, match="capability terminal_run"):
        _compile(store, "system_get_info", provenance=c.PERSON, attended=True,
                 run_id=run_id, envelope=wider)
    assert store.objective_run(run_id) is None


def test_a_supplied_envelope_cannot_bring_its_own_network_grant(store):
    """Grants are issued by compile, audited, and bound to one objective; a
    grant id handed in could be another objective's live grant."""
    run_id = "OBJ-ADMIT-3"
    minted = _minted(run_id)
    borrowed = c.Envelope(**{**minted.__dict__, "network": c.NetworkScope(
        **{**minted.network.__dict__, "grant_id": "GRANT-someone-elses"})})
    with pytest.raises(c.ContractError, match="grant"):
        _compile(store, "system_get_info", provenance=c.PERSON, attended=True,
                 run_id=run_id, envelope=borrowed)
    assert store.grant("GRANT-someone-elses") is None


def test_a_narrower_supplied_envelope_is_admitted_as_supplied(store):
    run_id = "OBJ-ADMIT-4"
    narrower = _minted(run_id).narrow(token_limit=5_000, allowed_capabilities=())
    _compile(store, "system_get_info", provenance=c.PERSON, attended=True,
             run_id=run_id, envelope=narrower)
    stored = json.loads(store.objective_run(run_id)["envelope"])
    assert stored["token_limit"] == 5_000
    assert stored["allowed_capabilities"] == []
    assert stored["network"]["grant_id"].startswith("GRANT-"), "compile issued the grant"


def test_a_run_from_before_envelopes_waits_for_the_person(store):
    """Fail-closed legacy: a row the migration found without an envelope does
    not run under an assumed one."""
    run_id = _compile(store, "system_get_info", provenance=c.PERSON, attended=True)
    with store._tx() as conn:
        conn.execute("UPDATE objective_runs SET envelope=NULL, provenance=NULL, "
                     "attended=NULL WHERE run_id=?", (run_id,))
    called = []

    async def port(capability_id, arguments, run=None):
        called.append(capability_id)
        return c.succeeded(c.started("x", capability_id), "ran").to_dict()

    final, _ = _drive(store, run_id, port)
    assert called == [], "a run with no envelope was dispatched"
    assert final["status"] == O.RUN_PAUSED
    assert "envelope" in (final.get("blocker") or ""), final


@pytest.mark.parametrize("damage", [
    lambda e: e.update(expires_at=""),
    lambda e: e.pop("expires_at"),
    lambda e: e.pop("provenance"),
    lambda e: e.pop("attended"),
    lambda e: e.update(attended="yes"),
    lambda e: e["network"].update(expires_at=""),
], ids=["no-expiry", "expiry-missing", "provenance-missing", "attended-missing",
        "attended-not-a-bool", "network-never-expires"])
def test_a_stored_envelope_that_leaves_something_out_is_not_trusted(store, damage):
    """Slice 2 review: the stored form was read with the most trusting
    defaults - a missing provenance read as the person, a missing attended as
    attended, and no expiry as forever. `to_dict` writes every one of them,
    so a row without them is not an envelope FRIDAY issued."""
    run_id = _compile(store, "system_get_info", provenance=c.READ_MATERIAL, attended=False)
    stored = json.loads(store.objective_run(run_id)["envelope"])
    damage(stored)
    with store._tx() as conn:
        conn.execute("UPDATE objective_runs SET envelope=? WHERE run_id=?",
                     (json.dumps(stored), run_id))
    called = []

    async def port(capability_id, arguments, run=None):
        called.append(capability_id)
        return c.succeeded(c.started("x", capability_id), "ran").to_dict()

    final, _ = _drive(store, run_id, port)
    assert called == [], "a run under an incomplete envelope was dispatched"
    assert final["status"] == O.RUN_PAUSED
    assert "envelope" in (final.get("blocker") or ""), final


def test_a_network_scope_that_reaches_nothing_needs_no_expiry():
    envelope = O.envelope_for("OBJ-QUIET", ["system_get_info"], provenance=c.PERSON,
                              attended=True).narrow(network=c.NetworkScope())
    assert c.Envelope.from_dict(envelope.to_dict()) == envelope


# --- the seams that carry it ----------------------------------------------

def test_the_live_agents_objective_port_carries_the_run():
    """The agent's engine wrapped objective_cli's run-carrying dispatch in a
    closure that took no `run`, so the envelope's file roots and network
    scope, which the runtime enforces from the Run, never reached it."""
    import agent_friday as A

    seen = []

    async def local(capability_id, arguments, *, run=None):
        seen.append(run)
        return {"status": "succeeded"}

    port = A.objective_port(local, router_of=lambda: None, call_via_router=None)
    assert "run" in inspect.signature(port).parameters
    task_run = c.Run.create("list", capability="files_list", provenance=c.READ_MATERIAL)
    asyncio.run(port("files_list", {}, run=task_run))
    assert seen == [task_run]


def test_the_production_ports_carry_the_run():
    from friday import objective_cli

    for port in (CR.build_dispatch(), objective_cli.build_dispatch()):
        assert "run" in inspect.signature(port).parameters


def test_objective_start_records_the_callers_lineage(store):
    from friday.toolsets import objectives as OT

    OT.reset_store(store)
    try:
        started = OT.objective_start(
            c.Run.from_read_material("check my system", "objectives"),
            "check my system")
        assert started.status == c.SUCCEEDED, started.error
        row = store.objective_run(started.output["run_id"])
    finally:
        OT.reset_store(None)
    assert row["provenance"] == c.READ_MATERIAL


@pytest.mark.parametrize("left_out", ["provenance", "attended"])
def test_compile_will_not_assume_the_lineage(store, left_out):
    """Slice 2 review: defaulting to the person, attended, is how a caller
    that forgot the lineage would be trusted anyway, in a script the static
    check below never reads. Both are required."""
    lineage = {"provenance": c.PERSON, "attended": True}
    del lineage[left_out]
    with pytest.raises(TypeError, match=left_out):
        O.compile_objective(store, request="check", objective_summary="check",
                            tasks=[{"capability": "system_get_info", "arguments": {}}],
                            manifest=[{"id": "system_get_info", "description": "info"}],
                            **lineage)


def test_every_product_caller_states_the_lineage():
    """compile_objective no longer has a default, and an Envelope's is the
    person, attended - convenient in a unit test, never in product code,
    because a caller that forgot is how V7-D04 happened."""
    offenders = []
    paths = sorted((ROOT / "friday").rglob("*.py")) + [ROOT / "scripts" / "soak.py"]
    for path in paths:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if (isinstance(node, ast.Call)
                    and getattr(node.func, "attr", getattr(node.func, "id", ""))
                    in ("compile_objective", "Envelope")):
                named = {k.arg for k in node.keywords}
                if not {"provenance", "attended"} <= named:
                    offenders.append(f"{path.relative_to(ROOT).as_posix()}:{node.lineno}")
    assert offenders == [], offenders


# --- lineage through a schedule --------------------------------------------
# A schedule is a transform too: written once, fired later by the OS or by
# hand. Its firing must carry where the schedule came from, and a firing
# nobody attended is unattended.

@pytest.fixture
def schedules(store, monkeypatch):
    from friday.toolsets import objectives as OT
    from friday.toolsets import schedules as S

    monkeypatch.setattr(OT, "store", lambda: store)
    monkeypatch.setattr(S, "store", lambda: store)
    registered = {}

    def register(name, trigger, description):
        task = S.task_name_for(name)
        registered[task] = description
        return task

    monkeypatch.setattr(S, "register_task", register)
    monkeypatch.setattr(S, "task_exists", lambda task: task in registered)
    monkeypatch.setattr(S, "delete_task", lambda task: registered.pop(task, None) is not None)
    return S


def _capturing_port(seen):
    async def port(capability_id, arguments, *, run=None):
        seen.append((run.provenance, run.attended))
        return c.succeeded(c.started(run.run_id, capability_id), "ran").to_dict()
    return port


def _schedule(S, creator: c.Run, name="digest"):
    made = S.schedules_create(creator, name, "check my system",
                              json.dumps({"kind": "manual"}),
                              tasks=json.dumps([{"capability": "system_get_info"}]))
    assert made.status == c.SUCCEEDED, made.error


def test_a_schedule_written_from_read_material_fires_as_read_material(store, schedules):
    _schedule(schedules, c.Run.from_read_material("schedule", "schedules"))
    seen = []
    fired = asyncio.run(schedules.fire("digest", store=store,
                                       dispatch=_capturing_port(seen), fired_by="schedule"))
    assert seen == [(c.READ_MATERIAL, False)], seen
    assert store.objective_run(fired["run_id"])["provenance"] == c.READ_MATERIAL


def test_the_persons_schedule_fires_as_the_person_and_unattended(store, schedules):
    _schedule(schedules, c.Run.create("schedule", "schedules"))
    seen = []
    asyncio.run(schedules.fire("digest", store=store,
                               dispatch=_capturing_port(seen), fired_by="schedule"))
    assert seen == [(c.PERSON, False)], seen


def test_a_hand_firing_from_read_material_is_read_material(store, schedules):
    """Firing the person's schedule because a page said to is still read
    material acting: the less trusted of the two wins."""
    _schedule(schedules, c.Run.create("schedule", "schedules"))
    seen = []
    asyncio.run(schedules.fire("digest", store=store, dispatch=_capturing_port(seen),
                               fired_by="hand", provenance=c.READ_MATERIAL))
    assert seen == [(c.READ_MATERIAL, True)], seen


def test_rewriting_a_schedule_takes_the_new_authors_provenance(store, schedules):
    _schedule(schedules, c.Run.create("schedule", "schedules"))
    _schedule(schedules, c.Run.from_read_material("schedule", "schedules"))
    assert store.get_schedule("digest")["provenance"] == c.READ_MATERIAL


def test_a_schedule_from_before_provenance_is_untrusted(store, schedules):
    _schedule(schedules, c.Run.create("schedule", "schedules"))
    with store._tx() as conn:
        conn.execute("UPDATE schedules SET provenance=NULL WHERE name='digest'")
    seen = []
    asyncio.run(schedules.fire("digest", store=store,
                               dispatch=_capturing_port(seen), fired_by="schedule"))
    assert seen == [(c.READ_MATERIAL, False)], seen


def test_running_a_schedule_by_hand_passes_the_requesters_lineage(store, schedules):
    """`schedules_run` is the tool a conversation calls. A request that came
    from read material fires the person's schedule as read material."""
    _schedule(schedules, c.Run.create("schedule", "schedules"))
    fired = asyncio.run(schedules.schedules_run(
        c.Run.from_read_material("run the digest", "schedules"), "digest"))
    assert fired.status == c.SUCCEEDED, fired.error
    row = store.objective_run(fired.output["run_id"])
    assert row["provenance"] == c.READ_MATERIAL and row["attended"] == 1


# --- read material may not reach a terminal ---------------------------------
# TRUST-002's acceptance: "untrusted-to-shell path requires gate". Command
# execution - Hermes delegation (terminals and file edits), capability_use,
# the connectors, self-development promotion - was AUTO under the default
# autonomy and open to read material, which was invisible only while the
# durable path dropped lineage anyway.

def test_no_command_execution_tool_is_reachable_from_read_material():
    commands = [t for t, cat in P.TOOL_CATEGORIES.items() if cat == P.COMMAND_EXECUTION]
    assert "hermes_delegate" in commands
    for tool_id in commands:
        verdict = P.provenance_verdict(tool_id, c.READ_MATERIAL)
        assert verdict is not None and verdict.denied, tool_id
        assert P.provenance_verdict(tool_id, c.PERSON) is None, tool_id


def test_a_read_material_objective_cannot_delegate_to_a_terminal(store, monkeypatch):
    delegated = []

    class _Recorder:
        capability_id, module, function = "hermes_delegate", "tests", "recorder"

        def load(self):
            def recorder(run, **arguments):
                delegated.append(arguments)
                return c.succeeded(c.started(run.run_id, "hermes_delegate"), "recorded")
            return recorder

    real = CR.resolutions()
    monkeypatch.setattr(CR, "resolutions", lambda: {**real, "hermes_delegate": _Recorder()})
    run_id = _compile(store, "hermes_delegate", provenance=c.READ_MATERIAL, attended=False)
    _, tasks = _drive(store, run_id, _dangerous_dispatch())
    assert delegated == [], "read material reached a terminal"
    assert "came from something Friday read" in json.dumps(tasks), tasks


def test_a_port_that_cannot_carry_the_run_still_meets_the_lineage(store, monkeypatch):
    """The live agent's objective engine calls a port with no `run` parameter
    (agent_friday.start_objective_engine). The executor held such a port to
    the envelope but not to the lineage, and the runtime behind it built a
    default Run - the person, attended - so read material reached a terminal."""
    delegated = []

    class _Recorder:
        capability_id, module, function = "hermes_delegate", "tests", "recorder"

        def load(self):
            def recorder(run, **arguments):
                delegated.append(run.provenance)
                return c.succeeded(c.started(run.run_id, "hermes_delegate"), "recorded")
            return recorder

    real = CR.resolutions()
    monkeypatch.setattr(CR, "resolutions", lambda: {**real, "hermes_delegate": _Recorder()})
    inner = _dangerous_dispatch()

    async def runless(capability_id, arguments):
        return await inner(capability_id, arguments)

    run_id = _compile(store, "hermes_delegate", provenance=c.READ_MATERIAL, attended=False)
    _, tasks = _drive(store, run_id, runless)
    assert delegated == [], f"read material reached a terminal as {delegated}"
    assert "came from something Friday read" in json.dumps(tasks), tasks
