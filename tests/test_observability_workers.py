"""
UX-001 (PRD 69): "User may show/hide workers." The `workers` flag on
/api/observability is a view over an already-redacted trace; it grants nothing.
Kept apart from tests/test_observability.py, which another build owns.
"""
from __future__ import annotations

import pytest

from friday import observability as OB
from friday.store import Store


@pytest.fixture
def store(tmp_path):
    return Store(str(tmp_path / "obs_workers.sqlite3"))


def _ui_client(store, monkeypatch):
    pytest.importorskip("httpx")
    from starlette.testclient import TestClient
    from friday import access
    from friday import ui_server as u

    monkeypatch.setattr(access, "GATE_ENABLED", False)
    monkeypatch.setenv("ADA_DB", str(store.path))
    # GateMiddleware 403s any non-local Host (TestClient's default is "testserver").
    return TestClient(u.create_app(), base_url="http://127.0.0.1:8770")


def test_user_may_show_or_hide_workers_in_either_mode(store, monkeypatch):
    # PRD 69: "User may show/hide workers." Normal hides them by default and
    # Developer shows them; the `workers` flag flips either, and is only a view.
    started = {"at": "2026-10-10T00:00:01", "kind": "worker", "event": "worker.started",
               "executor": "hermes", "worker_run_id": "W1", "pid": 4242}
    step = {"at": "2026-10-10T00:00:00", "kind": "objective", "event": "task.started"}
    full = {"run_id": "RUN-w", "found": True, "status": "RUNNING", "tasks": [],
            "timeline": [step, started], "errors": []}
    monkeypatch.setattr(OB, "trace", lambda run_id: dict(full))

    def get(client, **params):
        r = client.get("/api/observability", params={"run_id": "RUN-w", **params})
        assert r.status_code == 200, r.text
        return r.json()

    with _ui_client(store, monkeypatch) as client:
        normal, normal_shown = get(client), get(client, workers="show")
        dev, dev_hidden = get(client, mode="developer"), get(client, mode="developer", workers="hide")
        odd = get(client, workers="all")
    assert "workers" not in normal and "workers" not in odd
    assert normal_shown["workers"] == [{"at": started["at"], "event": "worker.started",
                                        "executor": "hermes", "worker_run_id": "W1"}]
    assert "timeline" not in normal_shown  # showing workers does not open the developer view
    assert dev["workers"] == normal_shown["workers"] and started in dev["timeline"]
    assert "workers" not in dev_hidden and dev_hidden["timeline"] == [step]
