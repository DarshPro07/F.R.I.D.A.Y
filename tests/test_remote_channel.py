"""
Remote channels (PRD v3.1 FR-040): an authenticated remote objective uses
the same identity, policy and objective ledger as a local request.

The UI server's /api/objective is the remote door. The face/PIN session
gate is the identity (a request without a valid session is 423 before
any handler runs); the objective toolset compiles into the same ledger a
spoken request uses; the run is tagged `source_channel=remote:<channel>`
and the running control plane drives it. Nothing about policy changes
with the channel - a remote write still parks for approval.
"""
from __future__ import annotations

import json

import pytest


@pytest.fixture
def server(tmp_path, monkeypatch):
    pytest.importorskip("httpx")
    from starlette.testclient import TestClient
    from friday import access
    from friday import ui_server as u
    from friday.store import Store
    from friday.toolsets import objectives as OT
    store = Store(str(tmp_path / "remote.sqlite3"))
    monkeypatch.setattr(OT, "store", lambda: store)
    monkeypatch.setattr(access, "GATE_ENABLED", True)
    monkeypatch.setattr(access, "LOG_PATH", tmp_path / "access.jsonl")
    token = "remote-session-token"
    access._sessions[token] = 9999999999.0
    with TestClient(u.create_app()) as client:
        yield client, store, token
    access._sessions.pop(token, None)


def test_unauthenticated_remote_request_is_refused_before_any_handler(server):
    client, store, _ = server
    r = client.post("/api/objective", json={"objective": "list my workspace roots"})
    assert r.status_code == 423 and r.json()["locked"] is True
    assert store.objective_runs(limit=5) == []               # nothing entered the ledger


def test_authenticated_remote_objective_enters_the_same_ledger(server):
    client, store, token = server
    r = client.post("/api/objective",
                    json={"objective": "list my workspace roots",
                          "tasks": json.dumps([{"capability": "files_roots", "arguments": {}}])},
                    headers={"Cookie": f"friday_session={token}", "X-Friday-Channel": "telegram"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] and body["run_id"].startswith("RUN-") and body["channel"] == "telegram"
    run = store.objective_run(body["run_id"])
    assert run["source_channel"] == "remote:telegram"
    assert run["status"] in ("QUEUED", "RUNNING", "READY", "PLANNED")
    events = [e["event"] for e in store.objective_events(body["run_id"])]
    assert "run.created" in events and "remote.accepted" in events
    # the same ledger shape a local request gets
    ledger = store.objective_ledger(body["run_id"])
    assert ledger["plan_steps"][0]["capability"] == "files_roots"
    # and the remote side can read it back
    s = client.get(f"/api/objective/status?run_id={body['run_id']}",
                   headers={"Cookie": f"friday_session={token}"})
    assert s.status_code == 200 and s.json()["status"] == "succeeded"
    assert s.json()["output"]["run_id"] == body["run_id"]


def test_remote_channel_name_is_sanitised_and_logged(server):
    client, store, token = server
    r = client.post("/api/objective",
                    json={"objective": "x", "tasks": json.dumps([{"capability": "files_roots", "arguments": {}}])},
                    headers={"Cookie": f"friday_session={token}",
                             "X-Friday-Channel": "evil channel; DROP TABLE"})
    assert r.status_code == 200 and r.json()["channel"] == "web"   # unparseable -> default
    from friday import access
    log = access.recent_log(10)
    assert any(e.get("kind") == "remote_objective" and e.get("accepted") for e in log)


def test_empty_remote_objective_is_a_400(server):
    client, _, token = server
    r = client.post("/api/objective", json={}, headers={"Cookie": f"friday_session={token}"})
    assert r.status_code == 400


def test_remote_policy_is_the_local_policy(server, tmp_path, monkeypatch):
    """A remote request for an ASK-tier write parks exactly like a local
    one would: same engine, same boundary, no channel privilege."""
    import asyncio
    from friday import continuous as CT
    from friday import golden as G
    client, store, token = server
    r = client.post("/api/objective",
                    json={"objective": "create a file",
                          "tasks": json.dumps([{"capability": "files_create",
                                                "arguments": {"path": str(tmp_path / "remote.txt"),
                                                              "content": "hi"}}])},
                    headers={"Cookie": f"friday_session={token}", "X-Friday-Channel": "signal"})
    assert r.status_code == 200
    run_id = r.json()["run_id"]
    # Drive it with a GUARDED engine (the remote channel gets no more than local).
    with G.Bench(tmp_path / "bench") as bench:
        monkeypatch.setattr("friday.toolsets.objectives.store", lambda: store)
        dispatch = G.golden_dispatch(bench, autonomy="guarded")

        async def drive():
            ex = CT.ContinuousTaskExecutor(store, dispatch, executor_id="remote-test")
            ex.stop()
            await ex.start(run_id)
        asyncio.run(drive())
    assert store.objective_run(run_id)["status"] == "WAITING_PERMISSION"
    assert not (tmp_path / "remote.txt").exists()
