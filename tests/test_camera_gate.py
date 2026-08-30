"""The camera is the first door; the PIN is the fallback, never the shortcut.

These tests pin the policy the owner asked for: a PIN proves knowledge, a face
proves presence, so knowledge alone only opens the door when no face could
have been read at all.
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


def test_pin_is_refused_while_the_camera_is_free(gate, monkeypatch):
    free_camera(monkeypatch)
    access.set_pin(PIN)
    allowed, why = access.pin_allowed()
    assert allowed is False and "face" in why
    out = access.verify_pin(PIN)                       # the RIGHT pin, still refused
    assert out["ok"] is False and out["refused"] is True
    assert not access.session_ok(out.get("token"))


def test_pin_opens_the_door_when_a_meeting_holds_the_camera(gate, monkeypatch):
    busy_camera(monkeypatch, "meeting", "Zoom")
    access.set_pin(PIN)
    allowed, why = access.pin_allowed()
    assert allowed is True and "Zoom" in why
    assert access.verify_pin("wrong-one")["ok"] is False
    out = access.verify_pin(PIN)
    assert out["ok"] is True and access.session_ok(out["token"])


def test_a_friday_window_with_the_camera_vetoes_the_pin(gate, monkeypatch):
    """The subtle one: Friday's own window makes the OS report 'a browser has the
    camera', which must NOT become grounds for a PIN -- a face is being read."""
    busy_camera(monkeypatch, "browser", "Chrome")
    access.set_pin(PIN)
    assert access.pin_allowed()[0] is True             # without the heartbeat, a browser is just a holder
    access.note_camera_hold()
    allowed, why = access.pin_allowed()
    assert allowed is False and "Friday window" in why
    assert access.verify_pin(PIN)["ok"] is False


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
    with TestClient(u.create_app()) as c:
        assert c.get("/api/state").status_code == 423          # locked, as ever
        assert c.get("/api/camera").status_code == 200         # but the lock screen can explain itself
        assert c.post("/api/camera/hold").status_code == 200
        assert access.camera_held_by_friday() is True
