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
    access._pin_fails.clear()
    return tmp_path


def _client():
    pytest.importorskip("httpx")
    from starlette.testclient import TestClient
    return TestClient(u.create_app(), base_url="http://127.0.0.1:8770")


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
        assert r == {"ok": False}                       # no distance: it would guide a forger
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


def _tiny_gated_app():
    pytest.importorskip("httpx")
    from starlette.applications import Starlette
    from starlette.middleware import Middleware
    from starlette.responses import PlainTextResponse
    from starlette.routing import Route, WebSocketRoute
    from starlette.testclient import TestClient

    async def ok(request):
        return PlainTextResponse("ran")

    async def sock(ws):
        await ws.accept()
        await ws.close()
    app = Starlette(middleware=[Middleware(access.GateMiddleware)], routes=[
        Route("/", ok), Route("/api/ask", ok, methods=["GET", "POST"]), WebSocketRoute("/api/stt", sock)])
    return TestClient(app, base_url="http://127.0.0.1:8770")


def _unlocked_cookie():
    token = "t-" + "x" * 20
    access._sessions[token] = 9e18
    return {"cookie": "%s=%s" % (access.COOKIE, token)}


def test_another_local_page_cannot_post_with_the_owners_cookie(gate):
    # Fails if the gate trusts the cookie alone: a page on another local port is
    # same-site, so the Strict cookie rides along on a no-preflight text/plain POST.
    c, cookie = _tiny_gated_app(), _unlocked_cookie()
    r = c.post("/api/ask", content='{"text":"okay"}',
               headers={**cookie, "Origin": "http://127.0.0.1:5173", "content-type": "text/plain"})
    assert r.status_code == 403 and r.text != "ran"
    assert c.post("/api/ask", headers={**cookie, "Origin": "http://127.0.0.1:8770"}).text == "ran"
    assert c.post("/api/ask", headers=cookie).text == "ran"          # scripts send no Origin
    log = [json.loads(l) for l in (gate / "access.jsonl").read_text().splitlines()]
    assert any(e["kind"] == "cross_origin_refused" and e["path"] == "/api/ask" for e in log)


def test_a_foreign_host_header_is_refused_even_on_open_paths(gate):
    # Fails if a DNS-rebound name (evil.example -> 127.0.0.1) reaches the app.
    c = _tiny_gated_app()
    assert c.get("/", headers={"host": "evil.example:8770"}).status_code == 403
    assert c.get("/", headers={"host": "localhost:8770"}).status_code == 200


def test_bypass_mode_still_refuses_a_foreign_origin(gate, monkeypatch):
    # Fails if --bypass-login lifts the origin check along with the face check.
    from starlette.websockets import WebSocketDisconnect
    monkeypatch.setattr(access, "GATE_ENABLED", False)
    c = _tiny_gated_app()
    assert c.post("/api/ask", headers={"Origin": "http://localhost:3000"}).status_code == 403
    assert c.get("/api/ask", headers={"host": "evil.example"}).status_code == 403
    assert c.post("/api/ask", headers={"Origin": "http://127.0.0.1:8770"}).text == "ran"
    with pytest.raises(WebSocketDisconnect):
        with c.websocket_connect("ws://127.0.0.1:8770/api/stt", headers={"Origin": "http://127.0.0.1:5173"}):
            pass


def test_a_face_miss_says_only_no_and_counts_toward_the_lockout(gate):
    # Fails if a miss returns the distance (a forger's hill-climbing signal) or is free.
    access.enrol(OWNER)
    for _ in range(access.PIN_MAX_TRIES):
        assert access.verify([0.5] * 128) == {"ok": False}
    assert len(access._pin_fails) == access.PIN_MAX_TRIES
    assert access.verify(OWNER)["ok"] is False                     # locked out, even for a match
    log = [json.loads(l) for l in (gate / "access.jsonl").read_text().splitlines()]
    assert all("distance" not in e for e in log if e["kind"] == "face_rejected")
