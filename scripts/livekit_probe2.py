"""Talk to Friday over a real LiveKit room and read her reply THREE ways:
the lk.transcription text stream, the agent's published audio track (how
many seconds of speech actually arrived), and the transcription text with
audio subscribed - so a silent reply, a text-only reply and a real spoken
reply are told apart.

Why this exists next to livekit_probe.py: that probe joins the room and
reads `lk.transcription`, but never subscribes to the agent's audio track.
Agents 1.5 emits the transcription IN SYNC with audio playout
(TranscriptSynchronizer, RoomIO default `sync_transcription=True`), and the
playout clock is driven by `AudioSource.wait_for_playout` on the agent's
published track. Whether a subscriber-less track advances that clock is
exactly the question this probe answers by measuring rather than reading
the SDK.

Usage:
  .venv/Scripts/python.exe scripts/livekit_probe2.py --room X --wait 150 "say exactly: FRIDAY_OK"
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import pathlib
import sys
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from dotenv import load_dotenv                                   # noqa: E402
from livekit import api, rtc                                     # noqa: E402

load_dotenv()

TOPIC_CHAT = "lk.chat"
TOPIC_TRANSCRIPTION = "lk.transcription"
IDENTITY = "friday-live-probe2"


def token(room: str) -> str:
    grant = api.VideoGrants(room_join=True, room=room, can_publish=True,
                            can_subscribe=True, can_publish_data=True)
    return (api.AccessToken(os.environ["LIVEKIT_API_KEY"], os.environ["LIVEKIT_API_SECRET"])
            .with_identity(IDENTITY).with_name("Live Probe 2").with_grants(grant).to_jwt())


async def converse(room_name: str, message: str, *, wait: float, settle: float) -> dict:
    room = rtc.Room()
    started = time.perf_counter()
    text_events: list[dict] = []
    audio = {"frames": 0, "seconds": 0.0, "first_at": None, "last_at": None, "track": None}
    agent_joined = asyncio.Event()
    audio_task: asyncio.Task | None = None

    @room.on("participant_connected")
    def _joined(p: rtc.RemoteParticipant) -> None:
        if p.identity != IDENTITY:
            agent_joined.set()

    async def drain(track: rtc.Track) -> None:
        stream = rtc.AudioStream(track)
        async for ev in stream:
            frame = ev.frame
            now = round(time.perf_counter() - started, 2)
            if audio["first_at"] is None:
                audio["first_at"] = now
            audio["last_at"] = now
            audio["frames"] += 1
            audio["seconds"] += frame.samples_per_channel / frame.sample_rate

    @room.on("track_subscribed")
    def _sub(track, publication, participant) -> None:
        nonlocal audio_task
        if track.kind == rtc.TrackKind.KIND_AUDIO and participant.identity != IDENTITY:
            audio["track"] = participant.identity
            audio_task = asyncio.create_task(drain(track))

    async def on_transcription(reader, identity: str) -> None:
        # Read DELTAS, not read_all(): the agent's transcription is a delta
        # stream that closes only when the segment's playback finishes. A
        # stream that never closes (the thing being diagnosed) would hand
        # read_all() nothing; chunk by chunk, whatever arrived is kept.
        if identity == IDENTITY:
            return
        attrs = dict(getattr(reader.info, "attributes", {}) or {})
        event = {"at": round(time.perf_counter() - started, 2), "from": identity,
                 "text": "", "attrs": attrs, "closed": False, "chunks": 0,
                 "last_chunk_at": None}
        text_events.append(event)
        try:
            async for chunk in reader:
                event["text"] += chunk
                event["chunks"] += 1
                event["last_chunk_at"] = round(time.perf_counter() - started, 2)
            event["closed"] = True
        except Exception as exc:                                     # noqa: BLE001
            event["error"] = f"{type(exc).__name__}: {exc}"[:120]

    room.register_text_stream_handler(
        TOPIC_TRANSCRIPTION,
        lambda reader, identity: asyncio.create_task(on_transcription(reader, identity)))

    await room.connect(os.environ["LIVEKIT_URL"], token(room_name),
                       options=rtc.RoomOptions(auto_subscribe=True))
    try:
        await asyncio.wait_for(agent_joined.wait(), timeout=settle)
    except asyncio.TimeoutError:
        pass
    for p in room.remote_participants.values():
        if p.identity != IDENTITY:
            agent_joined.set()
    # let the greeting play out before we type, so its audio is not confused
    # with the reply's
    await asyncio.sleep(8)
    greeting_audio = audio["seconds"]
    await room.local_participant.send_text(message, topic=TOPIC_CHAT)
    sent_at = round(time.perf_counter() - started, 2)

    deadline = time.perf_counter() + wait
    last = (len(text_events), audio["seconds"], 0)
    last_change = time.perf_counter()
    while time.perf_counter() < deadline:
        await asyncio.sleep(1.0)
        cur = (len(text_events), audio["seconds"], sum(e["chunks"] for e in text_events))
        if cur != last:
            last, last_change = cur, time.perf_counter()
        elif (text_events or audio["seconds"] > greeting_audio) and time.perf_counter() - last_change > 25:
            break
    await room.disconnect()
    if audio_task:
        audio_task.cancel()
    return {
        "room": room_name, "agent_joined": agent_joined.is_set(), "sent_at": sent_at,
        "audio_track_from": audio["track"], "audio_seconds_total": round(audio["seconds"], 1),
        "audio_seconds_after_send": round(audio["seconds"] - greeting_audio, 1),
        "audio_first_at": audio["first_at"], "audio_last_at": audio["last_at"],
        "transcription_events": text_events,
    }


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("message")
    p.add_argument("--room", default="P2")
    p.add_argument("--wait", type=float, default=120.0)
    p.add_argument("--settle", type=float, default=40.0)
    a = p.parse_args()
    out = asyncio.run(converse(f"friday-probe2-{a.room}", a.message, wait=a.wait, settle=a.settle))
    print(json.dumps(out, indent=1, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
