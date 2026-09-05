"""
Scheduled objectives and conditional monitoring (PRD v3.1 FR-041, FR-042).

FR-041 acceptance: a scheduled task survives restart and records every
execution. FR-042 acceptance: the no-noise test suppresses notification
when the condition is false.

Every firing here goes through the REAL objective engine
(compile_objective + ContinuousTaskExecutor) against a real SQLite store;
only the capability being called and `schtasks` are stand-ins.
"""
from __future__ import annotations

import asyncio
import json

import pytest

from friday import contracts as c
from friday.store import Store
from friday.toolsets import schedules as S


@pytest.fixture
def store(tmp_path, monkeypatch):
    db = Store(str(tmp_path / "sched.sqlite3"))
    from friday.toolsets import objectives as OT
    monkeypatch.setattr(OT, "store", lambda: db)
    monkeypatch.setattr(S, "store", lambda: db)
    return db


@pytest.fixture
def os_tasks(monkeypatch):
    """A recording stand-in for the Windows Task Scheduler."""
    registered: dict[str, dict] = {}

    def register(name, trigger, description):
        task = S.task_name_for(name)
        registered[task] = {"trigger": trigger, "description": description}
        return task

    monkeypatch.setattr(S, "register_task", register)
    monkeypatch.setattr(S, "task_exists", lambda task: task in registered)
    monkeypatch.setattr(S, "delete_task", lambda task: registered.pop(task, None) is not None)
    return registered


def _dispatch(price: float, fail: bool = False):
    async def dispatch(capability, arguments):
        if fail:
            raise TimeoutError("upstream gone")
        return {"ok": True, "capability": capability, "price": price, "hits": 1 if price < 100 else 0}
    return dispatch


def _create(store, name="watch", condition=None, trigger=None, delivery="session",
            permissions="", budgets="", tasks=None):
    run = c.Run.create("schedule", capability="schedules")
    return S.schedules_create(
        run, name, "watch the price", json.dumps(trigger or {"kind": "manual"}),
        tasks=json.dumps(tasks or [{"capability": "price_check", "arguments": {"item": "gpu"}}]),
        condition=json.dumps(condition) if condition else "", delivery=delivery,
        permissions=permissions, budgets=budgets)


# -- FR-042 the no-noise rule --------------------------------------------------


def test_condition_false_runs_the_objective_but_delivers_nothing(store, os_tasks):
    out = _create(store, condition={"kind": "task_output", "task": "t1", "path": "hits", "op": ">", "value": 0})
    assert out.status == c.SUCCEEDED, out.error
    fired = asyncio.run(S.fire("watch", store=store, dispatch=_dispatch(price=150.0), fired_by="test"))
    assert fired["status"] == "COMPLETED"                 # the check ran, for real
    assert fired["condition_met"] is False and fired["suppressed"] is True
    assert fired["delivered"] is False
    assert store.pending_objective_deliveries() == []     # nothing queued for the session
    history = store.schedule_history("watch")
    assert len(history) == 1 and history[0]["condition_met"] is False
    assert "hits" in history[0]["condition_detail"] and history[0]["delivered_via"] == ""
    # The objective itself is in the ledger like any other, tagged with its source.
    ledger = store.objective_ledger(fired["run_id"])
    assert ledger["source_channel"] == "schedule:watch"
    assert ledger["plan_steps"][0]["status"] == "SUCCEEDED"


def test_condition_true_delivers_once_to_the_session(store, os_tasks):
    _create(store, condition={"kind": "task_output", "task": "t1", "path": "hits", "op": ">", "value": 0})
    fired = asyncio.run(S.fire("watch", store=store, dispatch=_dispatch(price=80.0), fired_by="test"))
    assert fired["condition_met"] and fired["delivered"] and fired["delivered_via"] == "session_queue"
    pending = store.pending_objective_deliveries()
    assert len(pending) == 1 and pending[0]["run_id"] == fired["run_id"]
    assert "Condition met" in pending[0]["message"]
    assert store.schedule_history("watch")[0]["delivered_via"] == "session_queue"


def test_any_failed_condition_fires_only_on_failure(store, os_tasks):
    _create(store, condition={"kind": "any_failed"})
    ok = asyncio.run(S.fire("watch", store=store, dispatch=_dispatch(price=1.0), fired_by="test"))
    assert ok["condition_met"] is False and not ok["delivered"]
    bad = asyncio.run(S.fire("watch", store=store, dispatch=_dispatch(price=1.0, fail=True),
                             fired_by="test", wait_s=30))
    assert bad["status"] in ("FAILED", "PARTIAL") and bad["condition_met"] is True and bad["delivered"]
    assert [h["condition_met"] for h in store.schedule_history("watch")] == [True, False]


def test_condition_evaluation_is_code_not_a_model():
    tasks = [{"task_id": "RUN-x-t1", "capability": "price_check", "status": "SUCCEEDED",
              "result": json.dumps({"output": {"price": 42.5, "tags": ["sale"]}})}]
    assert S.evaluate_condition({"kind": "task_output", "task": "t1", "path": "price", "op": "<", "value": 50}, tasks)[0]
    assert not S.evaluate_condition({"kind": "task_output", "task": "t1", "path": "price", "op": ">=", "value": 50}, tasks)[0]
    assert S.evaluate_condition({"kind": "task_output", "task": "price_check", "path": "tags", "op": "contains", "value": "sale"}, tasks)[0]
    assert S.evaluate_condition({"kind": "task_status", "task": "t1", "op": "==", "value": "SUCCEEDED"}, tasks)[0]
    met, why = S.evaluate_condition({"kind": "task_output", "task": "t9", "path": "price", "op": "<", "value": 50}, tasks)
    assert not met and "not in the run" in why
    met, why = S.evaluate_condition({"kind": "task_output", "task": "t1", "path": "tags", "op": "<", "value": 50}, tasks)
    assert not met and "as a number" in why                 # a bad comparison is unmet, not a crash
    with pytest.raises(S.ScheduleError):
        S.validate_condition({"kind": "task_output"})           # task is required
    with pytest.raises(S.ScheduleError):
        S.validate_condition({"kind": "task_output", "task": "t1", "op": "~"})


# -- FR-041 persisted schedules with budgets, permissions, delivery, history ---


def test_definition_survives_a_restart_and_records_every_execution(tmp_path, monkeypatch, os_tasks):
    path = tmp_path / "persist.sqlite3"
    first = Store(str(path))
    from friday.toolsets import objectives as OT
    monkeypatch.setattr(OT, "store", lambda: first)
    monkeypatch.setattr(S, "store", lambda: first)
    out = _create(first, name="nightly", trigger={"kind": "daily", "at": "02:30"},
                  budgets=json.dumps({"retry_budget": 1, "time_budget_s": 120}),
                  permissions="web.search")
    assert out.status == c.SUCCEEDED, out.error
    assert out.verification.method == "schtasks_query" and "FridaySchedule_nightly" in os_tasks
    asyncio.run(S.fire("nightly", store=first, dispatch=_dispatch(1.0), fired_by="schedule"))
    first.close()

    # A new process: only the database and the OS task in common.
    second = Store(str(path))
    monkeypatch.setattr(OT, "store", lambda: second)
    monkeypatch.setattr(S, "store", lambda: second)
    row = second.get_schedule("nightly")
    assert row["trigger"] == {"kind": "daily", "at": "02:30"} and row["enabled"]
    assert row["budgets"]["retry_budget"] == 1 and row["budgets"]["time_budget_s"] == 120
    assert row["permissions"] == ["web.search"] and row["delivery"] == "session"
    asyncio.run(S.fire("nightly", store=second, dispatch=_dispatch(1.0), fired_by="schedule"))
    history = second.schedule_history("nightly")
    assert len(history) == 2 and all(h["run_id"] and h["status"] == "COMPLETED" for h in history)
    assert {h["fired_by"] for h in history} == {"schedule"}
    # Budgets reached the objective row (the engine honours them there).
    ledger = second.objective_ledger(history[0]["run_id"])
    assert ledger["retry_budget"] == 1 and ledger["time_budget_s"] == 120
    assert ledger["approvals"] == ["web.search"]


def test_once_schedule_disarms_itself_after_firing(store, os_tasks):
    from datetime import datetime, timedelta
    at = (datetime.now() + timedelta(hours=1)).isoformat(timespec="minutes")
    out = _create(store, name="single", trigger={"kind": "once", "at": at})
    assert out.status == c.SUCCEEDED and "FridaySchedule_single" in os_tasks
    asyncio.run(S.fire("single", store=store, dispatch=_dispatch(1.0), fired_by="schedule"))
    assert store.get_schedule("single")["enabled"] is False
    assert "FridaySchedule_single" not in os_tasks
    with pytest.raises(S.ScheduleError, match="disabled"):
        asyncio.run(S.fire("single", store=store, dispatch=_dispatch(1.0)))


def test_permissions_cannot_pre_approve_confirm_or_high_tier_tools(store, os_tasks):
    out = _create(store, name="dangerous", permissions="power.shutdown")     # CONFIRM tier
    assert out.status == c.FAILED and "cannot be granted" in out.error
    out = _create(store, name="deleting", permissions="files.delete")        # ASK, but R3
    assert out.status == c.FAILED and "risk tier R3" in out.error
    out = _create(store, name="browsing", permissions="browser.automate")    # ASK, but R2
    assert out.status == c.FAILED and "risk tier R2" in out.error
    out = _create(store, name="unknown", permissions="no.such.tool")
    assert out.status == c.FAILED and "cannot be granted" in out.error
    out = _create(store, name="reading", permissions="web.search,memory.remember")   # R0 / R1
    assert out.status == c.SUCCEEDED, out.error
    assert store.get_schedule("dangerous") is None and store.get_schedule("deleting") is None
    assert store.get_schedule("reading")["permissions"] == ["web.search", "memory.remember"]


def test_bad_definitions_are_refused_before_anything_is_stored(store, os_tasks):
    run = c.Run.create("schedule", capability="schedules")
    bad = S.schedules_create(run, "Bad Name!", "x", json.dumps({"kind": "manual"}))
    assert bad.status == c.FAILED and "lowercase" in bad.error
    bad = S.schedules_create(run, "past", "x", json.dumps({"kind": "once", "at": "2000-01-01T00:00"}))
    assert bad.status == c.FAILED and "past" in bad.error
    bad = S.schedules_create(run, "fast", "x", json.dumps({"kind": "interval", "minutes": 1}))
    assert bad.status == c.FAILED and "between 5 and 1440" in bad.error
    bad = S.schedules_create(run, "empty", "", json.dumps({"kind": "manual"}))
    assert bad.status == c.FAILED and "needs an objective" in bad.error
    bad = S.schedules_create(run, "where", "x", json.dumps({"kind": "manual"}), delivery="pager")
    assert bad.status == c.FAILED and "delivery" in bad.error
    assert store.schedules() == []


def test_list_run_history_delete_faces(store, os_tasks, monkeypatch):
    from friday import objective_cli
    monkeypatch.setattr(objective_cli, "build_dispatch", lambda: _dispatch(20.0))
    _create(store, name="face", trigger={"kind": "interval", "minutes": 30},
            condition={"kind": "task_output", "task": "t1", "path": "hits", "op": ">", "value": 0})
    run = c.Run.create("list", capability="schedules")
    listed = S.schedules_list(run)
    assert listed.output["count"] == 1 and listed.output["schedules"][0]["os_task_registered"] is True
    run = c.Run.create("run", capability="schedules")
    fired = asyncio.run(S.schedules_run(run, "face"))
    assert fired.status == c.SUCCEEDED and fired.output["condition_met"] and fired.output["delivered"]
    run = c.Run.create("history", capability="schedules")
    hist = S.schedules_history(run, name="face")
    assert hist.output["count"] == 1 and hist.output["runs"][0]["fired_by"] == "hand"
    run = c.Run.create("delete", capability="schedules")
    gone = S.schedules_delete(run, "face")
    assert gone.status == c.SUCCEEDED and gone.output["os_task_removed"] is True
    assert store.get_schedule("face") is None and "FridaySchedule_face" not in os_tasks
    run = c.Run.create("run missing", capability="schedules")
    assert asyncio.run(S.schedules_run(run, "face")).status == c.FAILED


def test_task_xml_is_well_formed_for_every_trigger_kind():
    from xml.dom import minidom
    for trigger in ({"kind": "once", "at": "2030-01-01T08:00"}, {"kind": "daily", "at": "08:00"},
                    {"kind": "interval", "minutes": 15}):
        xml = S.task_xml(trigger, "C:/py/pythonw.exe", '-m friday.toolsets.schedules --fire "x"', "d & <e>")
        doc = minidom.parseString(xml.encode("utf-16"))
        assert doc.getElementsByTagName("Triggers")
        assert doc.getElementsByTagName("MultipleInstancesPolicy")[0].firstChild.data == "IgnoreNew"
        assert "friday.toolsets.schedules" in xml


def test_tool_faces_are_wired():
    from friday import capabilities as C, capability_router as R, policy as P
    for tool in ("schedules_create", "schedules_list", "schedules_run", "schedules_history", "schedules_delete"):
        assert tool in C.CAPABILITIES and tool in P.TOOL_CATEGORIES
        assert tool in R.GROUPS["schedules"]
    engine = P.PolicyEngine()
    assert engine.decide("schedules.create").allowed          # REMINDER tier is AUTO
    assert engine.decide("schedules.history").allowed
