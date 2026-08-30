"""The face gate: locked means locked, on the server, with an audit trail.

Recognition happens in the browser; these tests exercise the part that
matters for security -- matching and enforcement -- with synthetic descriptors.
"""
from __future__ import annotations

import json

import pytest

from friday import access
from friday import ui_server as u

OWNER = [0.01 * i for i in range(128)]


@pytest.fixture
def gate(monkeypatch, tmp_path):
    monkeypatch.setattr(access, "OWNER_PATH", tmp_path / "owner.json")
    monkeypatch.setattr(access, "LOG_PATH", tmp_path / "access.jsonl")
    monkeypatch.setattr(access, "GATE_ENABLED", True)
    access._sessions.clear()
    return tmp_path


def _client():
    pytest.importorskip("httpx")
    from starlette.testclient import TestClient
    return TestClient(u.create_app())


def test_everything_is_locked_and_logged_until_a_face_matches(gate):
    with _client() as c:
        for path in ("/api/state", "/api/ask", "/api/deck", "/api/graph"):
            r = c.get(path) if path != "/api/ask" else c.post(path, json={"text": "hi"})
            assert r.status_code == 423, path
            assert r.json()["locked"] is True
        # the page, its assets and the auth endpoints stay reachable (the camera must run)
        assert c.get("/").status_code == 200
        assert c.get("/health").status_code == 200
        assert c.get("/api/auth/status").json()["mode"] == "yellow"
    log = [json.loads(l) for l in (gate / "access.jsonl").read_text().splitlines()]
    assert [e["kind"] for e in log].count("blocked") >= 4
    assert all("path" in e for e in log if e["kind"] == "blocked")


def test_first_enrolment_is_open_then_matching_unlocks_and_lock_relocks(gate):
    with _client() as c:
        assert c.post("/api/auth/enrol", json={"descriptor": OWNER}).json()["ok"] is True
        # a stranger's face is rejected and logged
        r = c.post("/api/auth/verify", json={"descriptor": [0.5] * 128}).json()
        assert r["ok"] is False and r["distance"] > access.THRESHOLD
        assert c.get("/api/state").status_code == 423
        # the owner (tiny jitter, as a live camera would give) unlocks
        r = c.post("/api/auth/verify", json={"descriptor": [x + 0.001 for x in OWNER]}).json()
        assert r["ok"] is True and r["distance"] <= access.THRESHOLD
        assert c.get("/api/state").status_code == 200
        assert c.get("/api/auth/status").json()["mode"] == "blue"
        # re-enrolment is allowed while unlocked
        assert c.post("/api/auth/enrol", json={"descriptor": OWNER}).status_code == 200
        # locking relocks the whole API
        c.post("/api/auth/lock")
        assert c.get("/api/state").status_code == 423
        # once someone is enrolled, a locked client cannot swap the owner
        assert c.post("/api/auth/enrol", json={"descriptor": [0.5] * 128}).status_code == 423
    kinds = [json.loads(l)["kind"] for l in (gate / "access.jsonl").read_text().splitlines()]
    for k in ("enrol", "face_rejected", "unlock", "lock", "enrol_refused"):
        assert k in kinds, k


def test_bad_descriptors_never_unlock(gate):
    with _client() as c:
        c.post("/api/auth/enrol", json={"descriptor": OWNER})
        for bad in ([], [1.0] * 12, ["x"] * 128, None, ["nan"] * 128, ["inf"] * 128):
            r = c.post("/api/auth/verify", json={"descriptor": bad}).json()
            assert r["ok"] is False
        assert c.get("/api/state").status_code == 423


def test_gate_can_be_switched_off_for_headless_use(gate, monkeypatch):
    monkeypatch.setattr(access, "GATE_ENABLED", False)
    with _client() as c:
        assert c.get("/api/state").status_code == 200
        assert c.get("/api/auth/status").json()["mode"] == "blue"
