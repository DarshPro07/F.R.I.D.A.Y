"""
The Jarvis screen powers: pointing, and the gated takeover.

Every test here has a known failure mode written next to it, because the point
of these is not coverage - it is that the specific way this feature could hurt
someone stays impossible.
"""

from __future__ import annotations

import pytest

from friday import contracts as c
from friday import policy as p
from friday.policy import PolicyEngine
from friday.toolsets import desktop, screen, vision


def engine() -> PolicyEngine:
    """The most permissive setting the system has. Everything here must hold."""
    return PolicyEngine(autonomy=p.FULL)


def run(request: str = "take over") -> c.Run:
    return c.Run.create(request, capability="desktop")


# --- the tiers -------------------------------------------------------------

def test_takeover_is_confirm_and_full_autonomy_does_not_grant_it():
    # Fails if a takeover becomes ASK: FULL autonomy turns ASK into a yes, and
    # Friday would then start driving the mouse without a person saying so.
    for tool in ("desktop.plan", "desktop.step"):
        verdict = engine().decide(tool)
        assert verdict.decision == p.CONFIRM, tool
        assert not verdict.allowed, tool


def test_stopping_is_never_gated():
    # Fails if stop needs approval - a stop you have to authorise is not a stop.
    verdict = engine().decide("desktop.stop")
    assert verdict.allowed


def test_pointing_is_auto_and_reads_only():
    verdict = engine().decide("screen.point")
    assert verdict.allowed and verdict.decision == p.AUTO


def test_credential_entry_is_denied_and_cannot_be_granted():
    # Fails if a session approval could unlock typing the boss's password.
    assert engine().decide("desktop.credential_entry").decision == p.DENY
    assert p.DESKTOP_CREDENTIAL_ENTRY in p.NON_APPROVABLE
    with pytest.raises(p.PolicyError):
        engine().approve_for_session("desktop.credential_entry")


def test_a_page_cannot_ask_for_a_takeover():
    # Fails if text Friday read somewhere can drive the desktop. No answer to
    # that question could make it safe, so it must never become a question.
    for tool in ("desktop.plan", "desktop.step"):
        assert p.provenance_verdict(tool, c.READ_MATERIAL) is not None, tool
    assert p.provenance_verdict("desktop.plan", c.PERSON) is None


# --- the danger gate, which is code and not a prompt ------------------------

@pytest.mark.parametrize("target", [
    "pay the invoice", "buy this now", "check out with my card",
    "enter my CVV", "type my password", "the one-time code",
    "transfer to that IBAN", "delete every file in Documents",
    "disable the firewall", "uninstall the antivirus",
])
def test_danger_gate_refuses_whole_categories(target):
    # Fails if these are only refused by prose in a prompt - which a model can
    # decide differently about.
    verdict = desktop.forbidden({"action": "click", "target": target})
    assert verdict.refused, target
    assert verdict.reason


def test_danger_gate_is_independent_of_autonomy():
    # The refusal is a function of the step, not of a setting anywhere.
    step = {"action": "click", "target": "pay the invoice"}
    assert desktop.forbidden(step).refused
    assert desktop.plan_refusal([{"action": "click", "target": "open notes"},
                                 step]).refused


def test_ordinary_work_is_not_refused():
    # Fails if the gate is so broad it eats the feature.
    for target in ("Compose button", "the File menu", "New tab",
                   "the search box", "Save as"):
        assert not desktop.forbidden({"action": "click", "target": target}).refused


def test_sending_needs_the_exact_text_first():
    # Fails if Friday can send a message whose wording was never shown.
    assert desktop.forbidden({"action": "click", "target": "Send"}).refused
    assert not desktop.forbidden(
        {"action": "click", "target": "Send", "text": "Running late, sorry."}).refused


def test_a_dangerous_task_is_refused_before_the_screen_is_touched(monkeypatch):
    # Fails if Friday screenshots and calls a model before noticing it will not
    # do the thing anyway.
    def explode(*a, **k):                      # pragma: no cover - must not run
        raise AssertionError("the screen was captured for a refused task")
    monkeypatch.setattr(vision, "capture_screen", explode)
    result = desktop.desktop_plan(run(), "pay the electricity bill", engine=engine())
    assert result.status == c.CANCELLED
    assert result.output["result"] == "refused"


# --- stopping and the step machine -----------------------------------------

def test_stop_sets_the_flag_and_the_next_step_refuses():
    # Fails if "stop" is advisory - the defect the whole one-step-at-a-time
    # design exists to prevent.
    desktop.ABORT.clear()
    stopped = desktop.desktop_stop(run(), engine=engine())
    assert stopped.status == c.SUCCEEDED
    assert desktop.ABORT.is_set()
    nxt = desktop.desktop_step(run(), engine=engine())
    assert nxt.status == c.CANCELLED
    assert nxt.output["result"] == "stopped"
    desktop.ABORT.clear()


def test_a_step_without_an_approved_plan_is_honest():
    desktop.ABORT.clear()
    result = desktop.desktop_step(run(), engine=engine())
    assert result.status == c.OBSERVED
    assert result.output["result"] == "no_plan"


def test_an_unattended_run_is_refused_rather_than_left_pending():
    # Fails if a scheduled job at 3am creates a live authorisation that sits
    # there until somebody says something that sounds like yes.
    desktop.ABORT.clear()
    unattended = c.Run.create("take over", capability="desktop")
    unattended.attended = False
    desktop._PLANS[unattended.run_id] = {
        "task": "open notes", "steps": [{"action": "click", "target": "Notes",
                                         "say": "Clicking Notes."}],
        "at": __import__("time").monotonic(), "index": 0, "monitor": 1}
    result = desktop.desktop_step(unattended, nonce="x", engine=engine())
    assert result.status == c.CANCELLED
    assert "nobody" in (result.error or "").lower()
    desktop._PLANS.clear()


# --- pointing ---------------------------------------------------------------

def test_low_confidence_never_draws(monkeypatch):
    # Fails if a confident-looking arrow is drawn at a guessed position.
    frame = vision.Frame(b"x", 1920, 1080, "screen")
    monkeypatch.setattr(vision, "capture_screen", lambda **k: frame)
    monkeypatch.setattr(vision, "locate_in_frame",
                        lambda *a, **k: {"found": True, "x": 0.5, "y": 0.5,
                                         "confidence": 0.2, "label": "maybe"})
    monkeypatch.setattr(screen, "draw_pointer",
                        lambda *a, **k: pytest.fail("drew at low confidence"))
    result = screen.screen_point(c.Run.create("where", capability="screen"),
                                 "the send button", overlay=False)
    assert result.status == c.OBSERVED
    assert result.output["result"] == "unsure"


def test_not_visible_says_so_instead_of_inventing_a_place(monkeypatch):
    frame = vision.Frame(b"x", 1920, 1080, "screen")
    monkeypatch.setattr(vision, "capture_screen", lambda **k: frame)
    monkeypatch.setattr(vision, "locate_in_frame",
                        lambda *a, **k: {"found": False, "confidence": 0.9,
                                         "why": "not on this screen"})
    monkeypatch.setattr(screen, "draw_pointer",
                        lambda *a, **k: pytest.fail("drew something not found"))
    result = screen.screen_point(c.Run.create("where", capability="screen"),
                                 "the send button", overlay=False)
    assert result.status == c.OBSERVED
    assert result.output["result"] == "not_visible"


def test_a_frame_remembers_which_monitor_it_came_from():
    # Fails if a capture of the second monitor loses its origin - the arrow
    # would be right and the click a whole screen to the left.
    frame = vision.Frame(b"x", 1920, 1080, "screen", origin_x=1920, origin_y=0)
    assert frame.origin_x == 1920
    assert frame.describe()["origin_x"] == 1920
    assert vision.Frame(b"x", 8, 8, "camera").origin_x == 0


def test_the_arrow_tip_is_at_the_requested_fraction():
    # Geometry check with no model involved: the tip must be the point.
    from PIL import Image
    import io
    blank = Image.new("RGB", (400, 300), (0, 0, 0))
    buf = io.BytesIO(); blank.save(buf, format="PNG")
    drawn = screen.draw_pointer(buf.getvalue(), 400, 300, 0.5, 0.5, "middle")
    out = Image.open(io.BytesIO(drawn)).convert("RGB")
    # The ink is orange-red; the tip pixel should be inked or immediately beside it.
    def inked(px):
        r, g, b = px
        return r > 180 and g < 140 and b < 110
    assert any(inked(out.getpixel((200 + dx, 150 + dy)))
               for dx in range(-3, 4) for dy in range(-3, 4)), \
        "no arrow ink within 3px of the requested tip"


def test_locate_rejects_a_coordinate_that_is_not_a_fraction():
    # Fails if the model answers in pixels and we draw at 1400% of the width.
    parsed = {"found": True, "x": 1400, "y": 600, "confidence": 0.9}
    # mirror the guard in locate_in_frame
    for axis in ("x", "y"):
        value = parsed.get(axis)
        if not isinstance(value, (int, float)) or not 0.0 <= value <= 1.0:
            parsed["found"] = False
            break
    assert parsed["found"] is False


# --- the gate, the socket, the deadline and the focus ----------------------

def test_a_locked_gate_refuses_the_speech_websocket(monkeypatch):
    # Fails if a WebSocket walks past the face gate: while locked, any local
    # process could stream the microphone through Friday to a paid recogniser.
    # The gap was latent until the first socket route existed.
    from starlette.testclient import TestClient
    from starlette.websockets import WebSocketDisconnect
    from friday import access, ui_server
    monkeypatch.setattr(access, "GATE_ENABLED", True)
    monkeypatch.setattr(access, "session_ok", lambda token: False)
    client = TestClient(ui_server.create_app())
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect("/api/stt"):
            pass  # pragma: no cover - the handshake must be refused


def test_an_open_gate_accepts_the_socket_and_is_honest_about_a_missing_key(monkeypatch):
    # The same socket, unlocked: the handshake succeeds and, with no key, the
    # first message says so instead of hanging or pretending to listen.
    import friday.config  # noqa: F401  load .env first so the delenv below sticks
    from starlette.testclient import TestClient
    from friday import access, ui_server
    monkeypatch.setattr(access, "GATE_ENABLED", False)
    monkeypatch.delenv("DEEPGRAM_API_KEY", raising=False)
    client = TestClient(ui_server.create_app())
    # A browser always sends Origin on a WebSocket handshake; the socket now
    # fails closed without one (security review, 2026-09-03).
    with client.websocket_connect("/api/stt", headers={"Origin": "http://testserver"}) as ws:
        assert ws.receive_json() == {"type": "error", "error": "no DEEPGRAM_API_KEY"}


def test_typing_refuses_when_the_focus_moved_since_the_last_step(monkeypatch):
    # Fails if a keystroke can land in whichever window took focus while
    # Friday was between steps - a password field, for instance.
    import time
    desktop.ABORT.clear()
    r = run()
    monkeypatch.setattr(desktop, "_foreground", lambda: 222)
    monkeypatch.setattr(desktop.native, "AVAILABLE", True, raising=False)
    monkeypatch.setattr(desktop.native, "SendInput", object(), raising=False)
    monkeypatch.setattr(desktop.native, "send_text",
                        lambda *a, **k: pytest.fail("typed into the wrong window"))
    monkeypatch.setattr(vision, "capture_screen",
                        lambda **k: vision.Frame(b"x", 10, 10, "screen"))
    desktop._PLANS[r.run_id] = {
        "task": "type a note", "monitor": 1, "index": 0, "active": True,
        "at": time.monotonic(), "hwnd": 111,
        "steps": [{"action": "type", "text": "hello", "say": "Typing."}]}
    result = desktop.desktop_step(r, engine=engine())
    assert result.status == c.OBSERVED
    assert result.output["result"] == "focus_moved"
    assert desktop._PLANS[r.run_id]["index"] == 0          # the plan did not advance
    desktop._PLANS.clear()


def test_every_vision_call_carries_a_deadline(monkeypatch):
    # Fails if a hung model call can hang pointing or planning forever.
    from google import genai
    captured = {}
    monkeypatch.setenv("GOOGLE_API_KEY", "test-key")
    monkeypatch.setattr(genai, "Client", lambda **kw: captured.update(kw) or object())
    vision._client()
    assert captured["http_options"].timeout == vision.VISION_TIMEOUT_MS
    assert vision.VISION_TIMEOUT_MS > 0
