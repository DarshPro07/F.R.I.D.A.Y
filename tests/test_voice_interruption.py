"""
Voice interruption and mute (PRD v3.1 FR-038, FR-039).

    FR-038  mute is a real input state: every mic muted -> audio input
            detached; unmute -> re-attached (VoiceInputGate, now attached
            in the entrypoint)
    FR-039  when interrupted, history reflects only what the user heard.
            LiveKit path: the framework stores the synchronized transcript
            with interrupted=True (livekit-agents 1.5.1 agent_activity).
            Browser-UI path: the page reports the heard prefix; the stored
            turn is truncated to it with an [interrupted] marker.
"""
from __future__ import annotations

import re

import pytest


# -- FR-039 browser-UI path ---------------------------------------------------


def test_store_truncates_a_reply_to_what_was_heard(tmp_path, monkeypatch):
    from friday.store import Store
    store = Store(str(tmp_path / "s.sqlite3"))
    mid = store.add_message("web-x", "assistant", "First sentence. Second sentence. Third.")
    assert store.truncate_message(mid, "First sentence. Second sentence.")
    rows = store.recent_messages(5)
    assert rows[-1]["content"] == "First sentence. Second sentence. [interrupted]"
    # A client cannot rewrite the reply into something that was never said.
    assert not store.truncate_message(mid, "Something else entirely")
    assert store.recent_messages(5)[-1]["content"].startswith("First sentence.")
    # Cut before anything played: the turn says so instead of lying.
    mid2 = store.add_message("web-x", "assistant", "Never played.")
    assert store.truncate_message(mid2, "")
    assert store.recent_messages(5)[-1]["content"] == "[interrupted before anything was heard]"
    assert not store.truncate_message(999999, "x")


def test_history_given_to_the_model_reflects_the_interruption(tmp_path, monkeypatch):
    """The UI brain's `_recent_turns` is what the next prompt sees."""
    from friday import voice_brain as V
    from friday.store import Store
    from friday.toolsets import memory as M
    store = Store(str(tmp_path / "s.sqlite3"))
    monkeypatch.setattr(M, "store", lambda: store)
    store.add_message("web-x", "user", "tell me about the weather")
    mid = store.add_message("web-x", "assistant",
                            "It is sunny. Tomorrow will rain. Take an umbrella.")
    assert V.mark_interrupted(mid, "It is sunny.")
    store.add_message("web-x", "user", "next question")      # the turn being asked
    turns = V._recent_turns(10)
    assert ("model", "It is sunny. [interrupted]") in turns
    assert not any("umbrella" in body for _, body in turns)


def test_reply_carries_the_message_id_the_page_needs(monkeypatch, tmp_path):
    from friday import voice_brain as V
    from friday.store import Store
    from friday.toolsets import memory as M
    store = Store(str(tmp_path / "s.sqlite3"))
    monkeypatch.setattr(M, "store", lambda: store)
    monkeypatch.setattr(V, "_try_command", lambda text: {"reply": "Locking.", "action": "lock"})
    out = V.reply("lock")
    assert isinstance(out.get("message_id"), int)
    assert store.recent_messages(5)[-1]["content"] == "Locking."


def test_api_interrupted_truncates_through_the_ui_server(monkeypatch, tmp_path):
    pytest.importorskip("httpx")
    from starlette.testclient import TestClient
    from friday import access
    from friday import ui_server as u
    from friday.store import Store
    from friday.toolsets import memory as M
    monkeypatch.setattr(access, "GATE_ENABLED", False)   # the face gate has its own suite
    store = Store(str(tmp_path / "s.sqlite3"))
    monkeypatch.setattr(M, "store", lambda: store)
    mid = store.add_message("web-x", "assistant", "One. Two. Three.")
    with TestClient(u.create_app()) as client:
        r = client.post("/api/interrupted", json={"message_id": mid, "heard": "One. Two."})
        assert r.status_code == 200 and r.json() == {"ok": True}
        assert client.post("/api/interrupted", json={}).status_code == 400
        r = client.post("/api/interrupted", json={"message_id": mid, "heard": "Four."})
        assert r.json() == {"ok": False}
    assert store.recent_messages(5)[-1]["content"] == "One. Two. [interrupted]"


def test_the_page_reports_the_heard_prefix_on_interruption():
    """The JS contract, read from the served page: speak() takes the message
    id, counts finished sentences, and shutUp() reports before cutting."""
    from pathlib import Path
    html = Path("ui/index.html").read_text(encoding="utf-8")
    assert "async function speak(text,messageId)" in html
    assert re.search(r"if\(SPEAKING\)_reportInterruption\(\);", html)
    assert "HEARD.done=i+1" in html
    assert 'fetch("/api/interrupted"' in html
    assert "speak(rep,d.message_id)" in html


# -- FR-039 LiveKit path (framework behaviour, pinned) ------------------------


def test_livekit_stores_only_the_synchronized_transcript_when_interrupted():
    """livekit-agents 1.5.1: on interruption the assistant message is the
    playback-synchronized transcript with interrupted=True. Pinned here so
    an upgrade that changes it is noticed."""
    import inspect
    from livekit.agents.voice import agent_activity
    src = inspect.getsource(agent_activity)
    assert "synchronized_transcript" in src
    assert re.search(r"interrupted=speech_handle\.interrupted", src)
    from livekit.agents.llm.chat_context import ChatMessage
    assert "interrupted" in ChatMessage.model_fields


def test_session_interruptions_are_enabled_with_real_speech_threshold():
    import agent_friday as af
    handling = af.turn_handling_for("sarvam")
    inter = handling["interruption"]
    assert 0.2 <= inter["min_duration"] <= 1.0
    assert inter["resume_false_interruption"] is True
    src = inspect_source(af)
    assert src.count("allow_interruptions=True") >= 3


def inspect_source(mod) -> str:
    import inspect
    return inspect.getsource(mod)


# -- FR-038 mute gate -----------------------------------------------------------


class _Input:
    def __init__(self):
        self.audio_enabled = True

    def set_audio_enabled(self, on):
        self.audio_enabled = on


class _Session:
    def __init__(self):
        self.input = _Input()


class _Pub:
    def __init__(self, muted, source="TrackSource.SOURCE_MICROPHONE"):
        self.muted = muted
        self.source = source


class _Participant:
    def __init__(self, *pubs):
        self.track_publications = {i: p for i, p in enumerate(pubs)}


class _Room:
    def __init__(self, *participants):
        self.remote_participants = {i: p for i, p in enumerate(participants)}
        self.handlers = {}

    def on(self, name, fn):
        self.handlers[name] = fn

    def fire(self, name):
        self.handlers[name]()


def test_mute_gate_detaches_audio_when_every_mic_is_muted_and_reattaches():
    from friday.runtime_metrics import RuntimeTelemetry
    from friday.voice_input import VoiceInputGate
    session, telemetry = _Session(), RuntimeTelemetry()
    mic = _Pub(muted=False)
    room = _Room(_Participant(mic, _Pub(False, "TrackSource.SOURCE_CAMERA")))
    gate = VoiceInputGate(session, telemetry)
    gate.attach(room)
    assert gate.accepting_audio and gate.describe()["microphone_state"] == "listening"
    mic.muted = True
    room.fire("track_muted")
    assert not session.input.audio_enabled and gate.describe()["microphone_state"] == "muted"
    mic.muted = False
    room.fire("track_unmuted")
    assert session.input.audio_enabled
    # The mic leaves the room entirely: nothing to listen to, stay muted.
    room.remote_participants.clear()
    room.fire("participant_disconnected")
    assert not session.input.audio_enabled
    assert {"track_muted", "track_unmuted", "track_published", "track_unpublished",
            "participant_connected", "participant_disconnected"} <= set(room.handlers)


def test_mute_gate_is_attached_in_the_entrypoint():
    import agent_friday as af
    src = inspect_source(af)
    assert "VoiceInputGate(session, agent._continuity.telemetry)" in src
    assert "agent._input_gate.attach(ctx.room)" in src
    from friday import reachability as R
    assert "VoiceInputGate" not in R.KNOWN
