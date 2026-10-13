"""The camera and the PIN are both doors (owner ruling 2026-10-05).

Until 2026-10-05 the PIN opened only when no face could be read. The owner then
asked for a PIN door beside the face. What keeps it safe is pinned here: only
the owner can set a PIN, wrong PINs are locked out, and every attempt is logged.
"""
from __future__ import annotations

import pytest

from friday import access, camera
from friday import ui_server as u

PIN = "sokovia-42"


@pytest.fixture
def gate(monkeypatch, tmp_path):
    monkeypatch.setattr(access, "OWNER_PATH", tmp_path / "owner.json")
    monkeypatch.setattr(access, "PIN_PATH", tmp_path / "pin.json")
    monkeypatch.setattr(access, "LOG_PATH", tmp_path / "access.jsonl")
    monkeypatch.setattr(access, "GATE_ENABLED", True)
    monkeypatch.setattr(access, "AUTH_MODE", "face")
    access._sessions.clear()
    access._pin_fails.clear()
    access._cam_hold["at"] = 0.0
    return tmp_path


def free_camera(monkeypatch):
    monkeypatch.setattr(camera, "status", lambda: {"busy": False, "holders": [], "yield_to": None, "why": ""})


def busy_camera(monkeypatch, kind="meeting", label="Zoom"):
    monkeypatch.setattr(camera, "status", lambda: {
        "busy": True, "holders": [{"process": "zoom.exe", "label": label, "kind": kind}],
        "yield_to": kind if kind in ("meeting", "stream") else None,
        "why": "%s is using the camera." % label})


def test_the_pin_is_a_door_of_its_own_while_the_camera_is_free(gate, monkeypatch):
    free_camera(monkeypatch)
    assert access.pin_allowed() == (False, "no PIN is set")
    access.set_pin(PIN)
    assert access.pin_allowed()[0] is True
    assert access.verify_pin("wrong-one")["ok"] is False
    out = access.verify_pin(PIN)
    assert out["ok"] is True and access.session_ok(out["token"])


def test_pin_opens_the_door_when_a_meeting_holds_the_camera(gate, monkeypatch):
    busy_camera(monkeypatch, "meeting", "Zoom")
    access.set_pin(PIN)
    allowed, why = access.pin_allowed()
    assert allowed is True          # a door of its own since 2026-10-05; the meeting is not the reason
    assert access.verify_pin("wrong-one")["ok"] is False
    out = access.verify_pin(PIN)
    assert out["ok"] is True and access.session_ok(out["token"])


def test_a_friday_window_reading_a_face_does_not_close_the_pin_door(gate, monkeypatch):
    busy_camera(monkeypatch, "browser", "Chrome")
    access.set_pin(PIN)
    access.note_camera_hold()
    allowed, why = access.pin_allowed()
    assert allowed is True and "Friday window" in why
    assert access.verify_pin(PIN)["ok"] is True


def test_password_mode_always_allows_the_pin(gate, monkeypatch):
    free_camera(monkeypatch)
    monkeypatch.setattr(access, "AUTH_MODE", "pin")
    access.set_pin(PIN)
    access.note_camera_hold()                          # even a live camera cannot override --password
    assert access.pin_allowed()[0] is True
    assert access.verify_pin(PIN)["ok"] is True


def test_brute_force_gets_locked_out(gate, monkeypatch):
    busy_camera(monkeypatch)
    access.set_pin(PIN)
    for _ in range(access.PIN_MAX_TRIES):
        assert access.verify_pin("nope")["ok"] is False
    out = access.verify_pin(PIN)                       # correct now, and still refused
    assert out["ok"] is False and out["locked_out"] > 0


def test_parallel_wrong_pins_never_get_more_hash_checks_than_the_limit(gate, monkeypatch):
    # Fails if the failure is saved after the 0.3 s hash: 40 parallel guesses
    # all read "0 failures" first and got ~33 checks instead of 5.
    import threading
    import time
    busy_camera(monkeypatch)
    access.set_pin(PIN)
    real, checks = access._hash_pin, []

    def slow_hash(pin, salt):
        checks.append(pin)
        time.sleep(0.05)
        return real(pin, salt)
    monkeypatch.setattr(access, "_hash_pin", slow_hash)
    threads = [threading.Thread(target=access.verify_pin, args=("guess%02d" % i,)) for i in range(40)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(checks) <= access.PIN_MAX_TRIES, len(checks)
    assert access.verify_pin(PIN)["ok"] is False                   # still locked out


def test_a_right_pin_undoes_its_own_reserved_failure(gate, monkeypatch):
    busy_camera(monkeypatch)
    access.set_pin(PIN)
    assert access.verify_pin("nope")["tries_left"] == access.PIN_MAX_TRIES - 1
    assert access.verify_pin(PIN)["ok"] is True
    assert access._pin_fails == []


def test_weak_pins_are_rejected(gate):
    for bad in ("", "12", "1111", None, "x" * 40):
        assert access.set_pin(bad)["ok"] is False
    assert access.set_pin("0421")["ok"] is True


def test_camera_status_shape_is_stable():
    s = camera.status()
    assert set(("busy", "holders", "yield_to", "why")) <= set(s)
    assert s["yield_to"] in (None, "meeting", "stream")
    assert isinstance(camera.free_enough(), bool)


def test_camera_endpoint_answers_while_locked(gate, monkeypatch):
    pytest.importorskip("httpx")
    from starlette.testclient import TestClient
    free_camera(monkeypatch)
    with TestClient(u.create_app(), base_url="http://127.0.0.1:8770") as c:
        assert c.get("/api/state").status_code == 423          # locked, as ever
        assert c.get("/api/camera").status_code == 200         # but the lock screen can explain itself
        assert c.post("/api/camera/hold").status_code == 200
        assert access.camera_held_by_friday() is True



def test_an_enrolled_owners_room_does_not_take_a_first_pin_from_the_lock_screen(gate, monkeypatch):
    # Found 2026-10-05: with a face enrolled and no PIN yet, anyone at the screen could
    # POST a first PIN while locked and then walk in through it.
    pytest.importorskip("httpx")
    from starlette.testclient import TestClient
    free_camera(monkeypatch)
    monkeypatch.setattr(access, "enrolled", lambda: True)
    with TestClient(u.create_app(), base_url="http://127.0.0.1:8770") as c:
        r = c.post("/api/auth/pin/set", json={"pin": PIN})
        assert r.status_code == 423 and not access.has_pin()
    monkeypatch.setattr(access, "enrolled", lambda: False)        # a fresh install claims its PIN
    with TestClient(u.create_app(), base_url="http://127.0.0.1:8770") as c:
        assert c.post("/api/auth/pin/set", json={"pin": PIN}).json()["ok"] is True


def test_password_mode_takes_a_first_pin_even_with_a_face_enrolled(gate, monkeypatch):
    pytest.importorskip("httpx")
    from starlette.testclient import TestClient
    monkeypatch.setattr(access, "enrolled", lambda: True)
    monkeypatch.setattr(access, "AUTH_MODE", "pin")
    with TestClient(u.create_app(), base_url="http://127.0.0.1:8770") as c:
        assert c.post("/api/auth/pin/set", json={"pin": PIN}).json()["ok"] is True
