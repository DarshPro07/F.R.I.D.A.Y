"""
SIGNAL-001, web surface: the control room sends a signal to an objective.

`POST /api/objective/signal` is the first surface adapter on the bus: it sits
behind the same session gate as the other control endpoints, validates every
field, and delivers through `signals.send(surface="web")` - so what the page
said is in the objective's trace next to what the desktop and voice said.
"""
from __future__ import annotations

import json

import pytest

from friday import objectives as O
from friday import signals as S

TOKEN = "signal-session-token"
COOKIE = {"Cookie": f"friday_session={TOKEN}"}


@pytest.fixture
def server(tmp_path, monkeypatch):
    pytest.importorskip("httpx")
    from starlette.testclient import TestClient
    from friday import access
    from friday import ui_server as u
    from friday.store import Store
    from friday.toolsets import objectives as OT
    store = Store(str(tmp_path / "signal.sqlite3"))
    monkeypatch.setattr(OT, "store", lambda: store)
    monkeypatch.setattr(access, "GATE_ENABLED", True)
    monkeypatch.setattr(access, "LOG_PATH", tmp_path / "access.jsonl")
    access._sessions[TOKEN] = 9999999999.0
    with TestClient(u.create_app(), base_url="http://127.0.0.1:8770") as client:
        yield client, store, tmp_path / "access.jsonl"
    access._sessions.pop(TOKEN, None)


def _run(store, status=O.RUN_RUNNING, run_id="R"):
    store.open_objective_run(run_id, request="x", objective_summary="x",
                             status=status)


def _parked_for_approval(store):
    _run(store, O.RUN_WAITING_PERMISSION)
    store.save_objective_task(
        task_id="t1", run_id="R", capability="files_write",
        arguments=json.dumps({"path": "notes.md"}), dependencies="[]",
        status=O.TaskStatus.WAITING)
    store.update_objective_task(
        "t1", failure_kind=O.FailureKind.USER_REQUIRED,
        evidence="approval_required: write notes.md")


def _post(client, body, headers=COOKIE):
    return client.post("/api/objective/signal", json=body, headers=headers)


def _events(store, run_id="R"):
    return [e["event"] for e in store.objective_events(run_id)]


def _log_lines(path):
    return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines()]


def test_the_app_exposes_the_signal_endpoint():
    from friday import ui_server as u
    assert "/api/objective/signal" in {r.path for r in u.create_app().routes}


def test_a_request_without_a_session_is_refused_logged_and_sends_nothing(server):
    client, store, log = server
    _run(store)
    r = _post(client, {"run_id": "R", "signal": "stop"}, headers={})
    assert r.status_code == 423 and r.json()["locked"] is True
    assert store.objective_run("R")["status"] == O.RUN_RUNNING
    assert S.EVENT_RECEIVED not in _events(store)
    assert any(e["kind"] == "blocked" and e["path"] == "/api/objective/signal"
               for e in _log_lines(log))


def test_stop_then_back_pause_and_resume_the_run_as_the_web_surface(server):
    client, store, _ = server
    _run(store)
    r = _post(client, {"run_id": "R", "signal": "stop", "reason": "hold on"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is True and body["effect"] == S.PAUSED and body["applied"] is True
    assert body["surface"] == "web" and body["signal_id"] > 0
    assert store.objective_run("R")["status"] == O.RUN_PAUSED
    r = _post(client, {"run_id": "R", "signal": "back"})
    assert r.json()["effect"] == S.RESUMED
    assert store.objective_run("R")["status"] == O.RUN_RUNNING
    seen = [e["detail"] for e in store.objective_events("R")
            if e["event"] == S.EVENT_RECEIVED]
    assert [d["kind"] for d in seen] == ["USER_STOP", "USER_BACK"]
    assert {d["surface"] for d in seen} == {"web"}


def test_approve_resumes_the_parked_step_only_for_the_named_action(server):
    client, store, _ = server
    _parked_for_approval(store)
    wrong = _post(client, {"run_id": "R", "signal": "approve",
                           "operation": "files_write", "target": "other.md"})
    assert wrong.status_code == 200 and wrong.json()["effect"] == S.IGNORED
    assert store.objective_run("R")["status"] == O.RUN_WAITING_PERMISSION
    right = _post(client, {"run_id": "R", "signal": "approve",
                           "operation": "files_write", "target": "notes.md"})
    assert right.json()["effect"] == S.RESUMED
    assert store.objective_run("R")["status"] == O.RUN_RUNNING


@pytest.mark.parametrize("body", [
    {"run_id": "R", "signal": "approve"},
    {"run_id": "R", "signal": "approve", "operation": "files_write"},
    {"run_id": "R", "signal": "approve", "operation": "files_write", "target": ""},
    {"run_id": "R", "signal": "approve", "target": "notes.md"},
], ids=["blank", "operation-only", "empty-target", "target-only"])
def test_a_blind_approval_never_resumes_a_parked_step(server, body):
    client, store, _ = server
    _parked_for_approval(store)
    before = _events(store)
    r = _post(client, body)
    assert r.status_code == 400 and "operation" in r.json()["error"]
    assert _events(store) == before
    assert store.objective_run("R")["status"] == O.RUN_WAITING_PERMISSION


def test_deny_stops_the_waiting_step(server):
    client, store, _ = server
    _parked_for_approval(store)
    r = _post(client, {"run_id": "R", "signal": "deny",
                       "operation": "files_write", "target": "notes.md"})
    assert r.json()["effect"] == S.DENIED
    assert store.objective_task("t1")["status"] == O.TaskStatus.FAILED


def test_answer_releases_a_run_held_for_an_assumption(server):
    from friday import requirements as REQ
    client, store, _ = server
    _run(store, O.RUN_WAITING_QUESTION)
    store.save_objective_task(
        task_id="t1", run_id="R", capability="work",
        arguments=json.dumps({"name": "a"}), dependencies="[]",
        status=O.TaskStatus.QUEUED)
    entry = REQ.Assumption(statement="the report is for Q3", origin="request",
                           confidence=REQ.LOW, consequence=REQ.HIGH,
                           validation="ask the person")
    store.save_assumption("R", key=entry.key, **entry.to_dict())
    r = _post(client, {"run_id": "R", "signal": "answer", "answer": "yes, Q3"})
    assert r.json()["effect"] == S.ANSWERED, r.text
    assert store.objective_run("R")["status"] == O.RUN_RUNNING


def test_amend_applies_to_the_future_and_lifts_the_barrier(server):
    client, store, _ = server
    _run(store)
    store.save_objective_task(
        task_id="t1", run_id="R", capability="work",
        arguments=json.dumps({"name": "research", "source": "reuters"}),
        dependencies="[]", status=O.TaskStatus.QUEUED)
    r = _post(client, {"run_id": "R", "signal": "amend", "said": "use bbc",
                       "description": "research", "changes": {"source": "bbc"}})
    assert r.json()["effect"] == S.BARRIER_RELEASED, r.text
    assert store.objective_task("t1")["arguments"]["source"] == "bbc"


def test_amend_without_an_edit_raises_the_barrier_for_the_dependents(server):
    client, store, _ = server
    _run(store)
    r = _post(client, {"run_id": "R", "signal": "amend", "said": "wait, change it"})
    assert r.json()["effect"] == S.BARRIER_RAISED
    assert S.barrier_up(store, "R")


def test_the_surface_is_always_web_whatever_the_caller_claims(server):
    client, store, _ = server
    _run(store)
    r = _post(client, {"run_id": "R", "signal": "stop"},
              headers={**COOKIE, "X-Friday-Channel": "telegram"})
    assert r.json()["surface"] == "web"
    r = _post(client, {"run_id": "R", "signal": "back", "surface": "voice"})
    assert r.status_code == 400


BAD = [
    pytest.param({"signal": "stop"}, id="no-run-id"),
    pytest.param({"run_id": "R"}, id="no-signal"),
    pytest.param({"run_id": 7, "signal": "stop"}, id="run-id-not-text"),
    pytest.param({"run_id": "R" * 65, "signal": "stop"}, id="run-id-too-long"),
    pytest.param({"run_id": "R", "signal": "shrug"}, id="unknown-signal"),
    pytest.param({"run_id": "R", "signal": ["stop"]}, id="signal-not-text"),
    pytest.param({"run_id": "R", "signal": "stop", "reason": "x" * 301},
                 id="reason-too-long"),
    pytest.param({"run_id": "R", "signal": "stop", "reason": {"a": 1}},
                 id="reason-not-text"),
    pytest.param({"run_id": "R", "signal": "approve", "operation": "o" * 129},
                 id="operation-too-long"),
    pytest.param({"run_id": "R", "signal": "approve", "target": "t" * 513},
                 id="target-too-long"),
    pytest.param({"run_id": "R", "signal": "answer", "answer": "a" * 2001},
                 id="answer-too-long"),
    pytest.param({"run_id": "R", "signal": "answer", "state": "MAYBE"},
                 id="bad-state"),
    pytest.param({"run_id": "R", "signal": "amend", "said": "s" * 2001},
                 id="said-too-long"),
    pytest.param({"run_id": "R", "signal": "amend", "changes": ["a"]},
                 id="changes-not-a-dict"),
    pytest.param({"run_id": "R", "signal": "amend", "description": "d",
                  "changes": {f"k{i}": 1 for i in range(21)}},
                 id="too-many-changes"),
    pytest.param({"run_id": "R", "signal": "amend", "description": "d",
                  "changes": {"k": "v" * 2001}}, id="change-value-too-long"),
    pytest.param({"run_id": "R", "signal": "stop", "extra": 1}, id="unknown-field"),
    pytest.param({"run_id": "R", "signal": "approve"}, id="approve-no-operation-or-target"),
    pytest.param({"run_id": "R", "signal": "approve", "operation": "files_delete"},
                 id="approve-operation-only"),
    pytest.param({"run_id": "R", "signal": "approve", "operation": "files_delete",
                  "target": ""}, id="approve-empty-target"),
    pytest.param({"run_id": "R", "signal": "approve", "operation": "  ",
                  "target": "important.md"}, id="approve-blank-operation"),
    pytest.param(["stop"], id="body-not-an-object"),
]


@pytest.mark.parametrize("body", BAD)
def test_every_field_is_validated_and_a_bad_request_writes_nothing(server, body):
    client, store, _ = server
    _run(store)
    before = _events(store)
    r = _post(client, body)
    assert r.status_code == 400, r.text
    assert r.json()["ok"] is False and r.json()["error"]
    assert _events(store) == before
    assert store.objective_run("R")["status"] == O.RUN_RUNNING


def test_a_body_that_is_not_json_or_is_too_large_is_refused(server):
    client, store, _ = server
    _run(store)
    r = client.post("/api/objective/signal", content=b"{not json",
                    headers={**COOKIE, "content-type": "application/json"})
    assert r.status_code == 400
    big = json.dumps({"run_id": "R", "signal": "stop", "reason": "x" * 40000})
    r = client.post("/api/objective/signal", content=big.encode(),
                    headers={**COOKIE, "content-type": "application/json"})
    assert r.status_code == 413
    assert S.EVENT_RECEIVED not in _events(store)


def test_an_unknown_run_is_404_and_a_finished_run_is_ignored(server):
    client, store, _ = server
    assert _post(client, {"run_id": "NOPE", "signal": "stop"}).status_code == 404
    _run(store, O.RUN_COMPLETED)
    r = _post(client, {"run_id": "R", "signal": "stop"})
    assert r.status_code == 200 and r.json()["effect"] == S.IGNORED


def test_a_cross_site_request_is_refused_and_logged(server):
    client, store, log = server
    _run(store)
    r = _post(client, {"run_id": "R", "signal": "stop"},
              headers={**COOKIE, "Origin": "https://evil.example"})
    assert r.status_code == 403
    assert store.objective_run("R")["status"] == O.RUN_RUNNING
    # The gate (access.GateMiddleware) now refuses a cross-origin write before
    # the route's own copy of the check runs, and logs it under its own kind.
    assert any(e["kind"] == "cross_origin_refused" and e["path"] == "/api/objective/signal"
               for e in _log_lines(log))


def test_a_delivered_signal_is_in_the_access_log_without_its_text(server):
    client, store, log = server
    _run(store)
    _post(client, {"run_id": "R", "signal": "stop", "reason": "private words"})
    assert "private words" not in log.read_text(encoding="utf-8")
    assert any(e["kind"] == "signal" and e["run_id"] == "R" for e in _log_lines(log))
