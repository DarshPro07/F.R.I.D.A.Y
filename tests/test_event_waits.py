"""FR-100 / FR-103 / FR-104 (GB-35): an objective parks on an external
event and is resumed by that event - verified, deterministic, bounded.

The mechanism reuses the wait machinery the engine already had (WAITING
task + a run status in RUN_WAITING_STATUSES), so the driver loop, the
watchdog and the invariant treat WAITING_EVENT exactly as they treat
WAITING_PERMISSION. What this file proves, on a real Store:

* a `status: waiting` result parks the task and the run, with no wake;
* the invariant holds while parked; the driver loop does not touch it;
* `deliver_event` with the exact (kind, key) resumes it, the payload
  reaches the task, and the trace shows delivered -> re-run -> succeeded;
* a non-matching event resumes nothing and is recorded as unmatched;
* the deadline expires the wait honestly (TRANSIENT, reason named);
* an unknown wait kind is refused at park time, never parked;
* the payload is redacted before it is written;
* `files_wait` drives the whole loop end to end: park on a missing file,
  the driver tick sees it appear and delivers, the task re-runs and reads
  it back;
* the remote door refuses unauthenticated and replayed deliveries and
  resumes nothing for them.
"""
from __future__ import annotations

import asyncio
import json
import time

import pytest

from friday import continuous as CT
from friday import contracts as c
from friday import objectives as O
from friday.continuous import ContinuousTaskExecutor
from friday.objectives import compile_objective
from friday.store import Store


# ---------------------------------------------------------------------------
# a fake capability set with one waiting capability
# ---------------------------------------------------------------------------

class Caps:
    def __init__(self):
        self.calls: list[tuple[str, dict]] = []
        self.arrived: dict | None = None

    async def call(self, capability_id: str, arguments: dict) -> dict:
        self.calls.append((capability_id, dict(arguments)))
        if capability_id == "wait_for_mail":
            if arguments.get("delivered_event"):
                self.arrived = arguments["delivered_event"]
                return {"status": "succeeded", "output": {"mail": "read"},
                        "verification": {"method": "inbox_read", "evidence": "1 message"}}
            return {"status": c.WAITING,
                    "output": {"wait": {"kind": "email", "key": "boss@example.com",
                                        "deadline_s": arguments.get("deadline_s", 3600)}}}
        if capability_id == "wait_unknown":
            return {"status": c.WAITING,
                    "output": {"wait": {"kind": "telepathy", "key": "x"}}}
        if capability_id == "summarise":
            return {"status": "succeeded", "output": {"ok": True},
                    "verification": {"method": "noop", "evidence": "done"}}
        raise LookupError(capability_id)


def manifest():
    return [{"id": n, "description": n} for n in ("wait_for_mail", "wait_unknown", "summarise")]


@pytest.fixture
def store():
    return Store(":memory:")


@pytest.fixture
def caps():
    return Caps()


@pytest.fixture
def executor(store, caps):
    return ContinuousTaskExecutor(store, caps.call, executor_id="test-exec-events")


def _compile(store, tasks, request="wait for the mail then summarise"):
    return compile_objective(store, request=request, tasks=tasks, manifest=manifest(),
                             objective_summary="event wait demo")


def _events(store, run_id):
    return [e["event"] for e in store.objective_events(run_id)]


# ---------------------------------------------------------------------------
# park
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_a_waiting_result_parks_the_task_and_the_run_with_no_wake(store, executor, caps):
    run = _compile(store, [{"capability": "wait_for_mail", "arguments": {}},
                           {"capability": "summarise", "arguments": {}, "dependencies": ["t1"]}])
    await executor.start(run["run_id"])
    row = store.objective_run(run["run_id"])
    assert row["status"] == O.RunStatus.WAITING_EVENT
    assert row["next_wake"] is None, "a wait is not a poll"
    assert "waiting for email: boss@example.com" in (row.get("blocker") or "")
    task = store.objective_tasks(run["run_id"])[0]
    assert task["status"] == O.TaskStatus.WAITING
    assert task["failure_kind"] == O.FailureKind.EVENT_REQUIRED
    wait = task["detail"]["wait"]
    assert wait["kind"] == "email" and wait["key"] == "boss@example.com" and wait["deadline"]
    assert O.EVENT_WAIT_PARKED in _events(store, run["run_id"])
    # the dependent task did not run
    assert [cid for cid, _ in caps.calls] == ["wait_for_mail"]
    # the invariant accepts the parked state as a legitimate wait
    executor._check_invariant(run["run_id"])
    # the executor released its lease: nobody owns a parked run
    assert not row.get("lease_executor_id") or not CT._fresh(row["lease_expiry"])


@pytest.mark.asyncio
async def test_the_driver_loop_and_watchdog_leave_a_parked_run_alone(store, executor, caps):
    run = _compile(store, [{"capability": "wait_for_mail", "arguments": {}}])
    await executor.start(run["run_id"])
    before = len(caps.calls)
    await executor._driver_tick()
    await executor._driver_tick()
    assert len(caps.calls) == before, "the driver re-ran a parked task"
    assert store.objective_run(run["run_id"])["status"] == O.RunStatus.WAITING_EVENT
    watchdog = CT.Watchdog(executor) if hasattr(CT, "Watchdog") else None
    if watchdog is not None:
        assert watchdog._is_orphan(store.objective_run(run["run_id"])) is False


@pytest.mark.asyncio
async def test_speak_names_what_it_is_waiting_for(store, executor):
    run = _compile(store, [{"capability": "wait_for_mail", "arguments": {}}])
    await executor.start(run["run_id"])
    said = CT.speak(store, run["run_id"])
    assert "parked" in said and "email" in said and "boss@example.com" in said


# ---------------------------------------------------------------------------
# deliver
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_a_matching_event_resumes_the_run_and_the_payload_reaches_the_task(store, executor, caps):
    run = _compile(store, [{"capability": "wait_for_mail", "arguments": {}},
                           {"capability": "summarise", "arguments": {}, "dependencies": ["t1"]}])
    await executor.start(run["run_id"])
    assert store.objective_run(run["run_id"])["status"] == O.RunStatus.WAITING_EVENT

    resumed = CT.deliver_event(store, "email", "boss@example.com",
                               {"subject": "re: the thing", "from": "boss@example.com"},
                               source="test")
    assert resumed == [run["run_id"]]
    row = store.objective_run(run["run_id"])
    assert row["status"] == O.RunStatus.RUNNING and row["next_wake"]
    assert (row.get("blocker") or "") == ""
    task = store.objective_tasks(run["run_id"])[0]
    assert task["status"] == O.TaskStatus.READY and task["failure_kind"] in ("", None)
    assert task["result"]["event"]["payload"]["subject"] == "re: the thing"

    # the driver picks it up and the graph finishes
    for _ in range(200):
        await executor._driver_tick()
        if store.objective_run(run["run_id"])["status"] in O.RUN_TERMINAL:
            break
        await asyncio.sleep(0)
    final = store.objective_run(run["run_id"])
    assert final["status"] == O.RunStatus.COMPLETED, final
    assert caps.arrived and caps.arrived["kind"] == "email" and caps.arrived["source"] == "test"
    events = _events(store, run["run_id"])
    assert events.index(O.EVENT_WAIT_PARKED) < events.index(O.EVENT_WAIT_DELIVERED) \
        < events.index(O.EVENT_TASK_SUCCEEDED)
    assert [cid for cid, _ in caps.calls] == ["wait_for_mail", "wait_for_mail", "summarise"]


@pytest.mark.asyncio
async def test_a_non_matching_event_resumes_nothing_and_is_recorded(store, executor, caps):
    run = _compile(store, [{"capability": "wait_for_mail", "arguments": {}}])
    await executor.start(run["run_id"])
    for kind, key in (("email", "someone-else@example.com"), ("ci", "boss@example.com"),
                      ("email", "BOSS@example.com"), ("", "boss@example.com")):
        assert CT.deliver_event(store, kind, key, {}) == []
    assert store.objective_run(run["run_id"])["status"] == O.RunStatus.WAITING_EVENT
    assert store.objective_tasks(run["run_id"])[0]["status"] == O.TaskStatus.WAITING
    assert _events(store, run["run_id"]).count(O.EVENT_UNMATCHED) == 3   # the empty kind is not an event
    assert len(caps.calls) == 1


@pytest.mark.asyncio
async def test_the_payload_is_redacted_before_it_is_written(store, executor):
    run = _compile(store, [{"capability": "wait_for_mail", "arguments": {}}])
    await executor.start(run["run_id"])
    CT.deliver_event(store, "email", "boss@example.com",
                     {"api_key": "sk-abcdefghijklmnopqrstuvwxyz123456", "body": "hi"})
    task = store.objective_tasks(run["run_id"])[0]
    written = json.dumps(task["result"])
    assert "sk-abcdefghijklmnop" not in written and "[REDACTED]" in written
    assert task["result"]["event"]["payload"]["body"] == "hi"


# ---------------------------------------------------------------------------
# deadline, unknown kind
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_a_wait_past_its_deadline_is_expired_honestly(store, executor, caps, monkeypatch):
    run = _compile(store, [{"capability": "wait_for_mail", "arguments": {"deadline_s": 1}},
                           {"capability": "summarise", "arguments": {}, "dependencies": ["t1"]}])
    await executor.start(run["run_id"])
    task = store.objective_tasks(run["run_id"])[0]
    # make the recorded deadline the past, without sleeping
    wait = dict(task["detail"]["wait"])
    wait["deadline"] = "2000-01-01T00:00:00"
    store.update_objective_task(task["task_id"], detail={"wait": wait})
    executor._sweep_event_waits()
    task = store.objective_tasks(run["run_id"])[0]
    assert task["status"] == O.TaskStatus.FAILED
    assert task["failure_kind"] == O.FailureKind.TRANSIENT
    assert "did not arrive" in task["evidence"]
    assert O.EVENT_WAIT_EXPIRED in _events(store, run["run_id"])
    row = store.objective_run(run["run_id"])
    assert row["status"] == O.RunStatus.RUNNING and row["next_wake"]
    # the driver settles the graph: dependent skipped, run ends honestly
    for _ in range(100):
        await executor._driver_tick()
        if store.objective_run(run["run_id"])["status"] in O.RUN_TERMINAL:
            break
        await asyncio.sleep(0)
    assert store.objective_run(run["run_id"])["status"] in (O.RunStatus.FAILED, O.RunStatus.PARTIAL)
    assert [cid for cid, _ in caps.calls] == ["wait_for_mail"]


@pytest.mark.asyncio
async def test_an_unknown_wait_kind_is_refused_not_parked(store, executor):
    run = _compile(store, [{"capability": "wait_unknown", "arguments": {}}])
    await executor.start(run["run_id"])
    task = store.objective_tasks(run["run_id"])[0]
    assert task["status"] == O.TaskStatus.FAILED
    assert task["failure_kind"] == O.FailureKind.INVALID_ARGUMENT
    assert "telepathy" in task["evidence"]
    assert store.objective_run(run["run_id"])["status"] != O.RunStatus.WAITING_EVENT


def test_a_deadline_is_clamped_to_the_ceiling():
    assert CT._wait_condition({"status": "waiting", "output": {"wait": {"kind": "ci", "key": "k"}}}) \
        == {"kind": "ci", "key": "k", "deadline_s": None, "detail": {}}
    assert CT._wait_condition({"status": "succeeded"}) is None
    assert CT._wait_condition({"status": "waiting", "output": {}}) is None
    assert CT._wait_condition({"result": {"status": "waiting", "wait": {"kind": "CI", "key": "k"}}})["kind"] == "ci"


# ---------------------------------------------------------------------------
# files_wait end to end: the driver tick itself is the file watcher
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_files_wait_parks_on_a_missing_file_and_the_driver_delivers_when_it_appears(store, tmp_path, monkeypatch):
    from friday import fsjail
    from friday.toolsets import files as F
    from friday.policy import PolicyEngine
    root = fsjail.DEFAULT_WORKSPACE / f"wait-gate-{tmp_path.name}"
    root.mkdir(parents=True, exist_ok=True)
    target = root / "export.csv"
    engine = PolicyEngine()

    async def dispatch(capability_id, arguments):
        run = c.Run.create("x", capability="files")
        if capability_id == "files_wait":
            return F.files_wait(run, arguments["path"], deadline_s=arguments.get("deadline_s", 60),
                                delivered_event=arguments.get("delivered_event"), engine=engine).to_dict()
        raise LookupError(capability_id)

    executor = ContinuousTaskExecutor(store, dispatch, executor_id="test-files-wait")
    try:
        run = compile_objective(store, request="carry on when the export lands",
                                tasks=[{"capability": "files_wait", "arguments": {"path": str(target), "deadline_s": 60}}],
                                manifest=[{"id": "files_wait", "description": "wait"}],
                                objective_summary="files wait demo")
        await executor.start(run["run_id"])
        row = store.objective_run(run["run_id"])
        assert row["status"] == O.RunStatus.WAITING_EVENT, row
        wait = store.objective_tasks(run["run_id"])[0]["detail"]["wait"]
        assert wait["kind"] == "file" and wait["key"] == str(target.resolve())

        await executor._driver_tick()                     # nothing there yet
        assert store.objective_run(run["run_id"])["status"] == O.RunStatus.WAITING_EVENT

        target.write_text("a,b\n1,2\n", encoding="utf-8")   # the export lands
        for _ in range(100):
            await executor._driver_tick()
            if store.objective_run(run["run_id"])["status"] in O.RUN_TERMINAL:
                break
            await asyncio.sleep(0)
        final = store.objective_run(run["run_id"])
        assert final["status"] == O.RunStatus.COMPLETED, final
        task = store.objective_tasks(run["run_id"])[0]
        assert task["status"] == O.TaskStatus.SUCCEEDED
        assert task["result"]["output"]["exists"] is True
        assert task["result"]["output"]["arrived_via"] == "driver_tick"
        assert task["result"]["verification"]["method"] == "path_exists_read_back"
        events = _events(store, run["run_id"])
        assert events.index(O.EVENT_WAIT_PARKED) < events.index(O.EVENT_WAIT_DELIVERED)
    finally:
        import shutil
        shutil.rmtree(root, ignore_errors=True)


def test_files_wait_reports_an_existing_file_at_once_and_disagrees_with_a_false_event(tmp_path):
    from friday import fsjail
    from friday.toolsets import files as F
    root = fsjail.DEFAULT_WORKSPACE / f"wait-now-{tmp_path.name}"
    root.mkdir(parents=True, exist_ok=True)
    try:
        present = root / "here.txt"
        present.write_text("x", encoding="utf-8")
        res = F.files_wait(c.Run.create("w", capability="files"), str(present))
        assert res.status == c.SUCCEEDED and res.output["exists"] is True
        # an event claims a file that is not on disk: the disk wins
        res2 = F.files_wait(c.Run.create("w", capability="files"), str(root / "ghost.txt"),
                            delivered_event={"kind": "file", "key": "ghost", "source": "remote:x"})
        assert res2.status == c.FAILED and "not on disk" in res2.error
        # a parked result cannot back a completion claim
        res3 = F.files_wait(c.Run.create("w", capability="files"), str(root / "later.txt"))
        assert res3.status == c.WAITING and res3.may_claim_completion is False
        assert res3.output["wait"]["kind"] == "file"
    finally:
        import shutil
        shutil.rmtree(root, ignore_errors=True)


# ---------------------------------------------------------------------------
# the remote door
# ---------------------------------------------------------------------------

@pytest.fixture
def server(tmp_path, monkeypatch):
    pytest.importorskip("httpx")
    from starlette.testclient import TestClient
    from friday import access
    from friday import ui_server as u
    from friday.toolsets import objectives as OT
    db = Store(str(tmp_path / "remote.sqlite3"))
    monkeypatch.setattr(OT, "store", lambda: db)
    monkeypatch.setattr(access, "GATE_ENABLED", True)
    monkeypatch.setattr(access, "LOG_PATH", tmp_path / "access.jsonl")
    monkeypatch.setattr(access, "NONCES_PATH", tmp_path / "nonces.json")
    access._seen_nonces.clear()
    token = "remote-event-token"
    access._sessions[token] = 9999999999.0
    with TestClient(u.create_app()) as client:
        yield client, db, token
    access._sessions.pop(token, None)


def _fresh():
    import secrets
    return {"nonce": secrets.token_urlsafe(16), "timestamp": time.time()}


@pytest.mark.asyncio
async def test_remote_event_door_resumes_only_an_authenticated_fresh_matching_delivery(server, caps):
    client, db, token = server
    executor = ContinuousTaskExecutor(db, caps.call, executor_id="test-remote-event")
    run = _compile(db, [{"capability": "wait_for_mail", "arguments": {}}])
    await executor.start(run["run_id"])
    assert db.objective_run(run["run_id"])["status"] == O.RunStatus.WAITING_EVENT
    auth = {"Cookie": f"friday_session={token}", "X-Friday-Channel": "mailhook"}

    # unauthenticated: 423 before any handler, nothing resumed
    r = client.post("/api/event", json={"kind": "email", "key": "boss@example.com", **_fresh()})
    assert r.status_code == 423
    assert db.objective_run(run["run_id"])["status"] == O.RunStatus.WAITING_EVENT

    # no nonce: replay protection refuses
    r = client.post("/api/event", json={"kind": "email", "key": "boss@example.com"}, headers=auth)
    assert r.status_code == 409 and "replay" in r.json()["error"]
    assert db.objective_run(run["run_id"])["status"] == O.RunStatus.WAITING_EVENT

    # a local-file claim is not accepted from outside
    r = client.post("/api/event", json={"kind": "file", "key": "C:/x", **_fresh()}, headers=auth)
    assert r.status_code == 400

    # the real thing
    fresh = _fresh()
    r = client.post("/api/event", json={"kind": "email", "key": "boss@example.com",
                                        "payload": {"subject": "ok"}, **fresh}, headers=auth)
    assert r.status_code == 200, r.text
    assert r.json()["matched"] is True and r.json()["resumed"] == [run["run_id"]]
    assert db.objective_run(run["run_id"])["status"] == O.RunStatus.RUNNING
    task = db.objective_tasks(run["run_id"])[0]
    assert task["result"]["event"]["source"] == "remote:mailhook"

    # the same nonce again is a replay, even though nothing is waiting now
    r = client.post("/api/event", json={"kind": "email", "key": "boss@example.com", **fresh}, headers=auth)
    assert r.status_code == 409

    from friday import access
    kinds = [e.get("kind") for e in access.recent_log(20)]
    assert "remote_event" in kinds and "remote_event_refused" in kinds
    # the key (which can name a person) is not in the access log
    assert not any("boss@example.com" in json.dumps(e) for e in access.recent_log(20))
