"""
Talk to Friday the way a user does: a real LiveKit room, over the real worker.

Run:  .venv/Scripts/python.exe scripts/livekit_probe.py "Reply with exactly: FRIDAY_DIRECT_OK"
      .venv/Scripts/python.exe scripts/livekit_probe.py --room L3 --wait 180 "<a long objective>"
      .venv/Scripts/python.exe scripts/livekit_probe.py --room L3 --follow "steer text" --wait 120

## Why this exists

The hosted playground at agents-playground.livekit.io now redirects to a
LiveKit Cloud sign-in, which is the user's action and not ours. But the thing
the playground actually *is* - a participant that joins a room, types on the
`lk.chat` text stream and reads `lk.transcription` back - is reproducible
without it, using the project credentials already in `.env`.

So this is not a mock and not a direct Python call into the agent. It mints a
real token, joins a real room on LiveKit Cloud, and the real registered worker
dispatches the real `entrypoint`. Everything between the room and Friday's
answer is production: admission, planner, MCP, Hermes, delivery. If a gate
passes here it passed through the customer path.

The one thing it cannot prove is what a human sees rendered, which is what the
Chrome pass is for.

## Reading the output

`--json` prints one object: the room, every inbound message with its timestamp
and sender, how long the first and last took, and whether an agent ever
joined. `agent_joined: false` with an empty transcript means the worker never
picked the job up, which is a different failure from a worker that answered
badly - and telling those apart is most of what a live gate is for.
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

#: The topics LiveKit Agents uses. Not ours to choose - `livekit/agents/types.py`.
TOPIC_CHAT = "lk.chat"
TOPIC_TRANSCRIPTION = "lk.transcription"

#: Who we claim to be in the room. Distinct from any real user identity so a
#: probe turn is identifiable in logs after the fact.
IDENTITY = "friday-live-probe"


def token(room: str) -> str:
    grant = api.VideoGrants(room_join=True, room=room,
                            can_publish=True, can_subscribe=True,
                            can_publish_data=True)
    return (api.AccessToken(os.environ["LIVEKIT_API_KEY"],
                            os.environ["LIVEKIT_API_SECRET"])
            .with_identity(IDENTITY)
            .with_name("Live Probe")
            .with_grants(grant)
            .to_jwt())


async def converse(room_name: str, messages: list[str], *, wait: float,
                   settle: float, gap: float) -> dict:
    room = rtc.Room()
    received: list[dict] = []
    agent_joined = asyncio.Event()
    started = time.perf_counter()

    @room.on("participant_connected")
    def _joined(participant: rtc.RemoteParticipant) -> None:
        # The agent is the participant that is not us. Identity is assigned by
        # the worker, so matching on a name would be guesswork.
        if participant.identity != IDENTITY:
            agent_joined.set()

    async def on_transcription(reader, participant_identity: str) -> None:
        text = await reader.read_all()
        if participant_identity == IDENTITY:
            return                                    # our own echo
        received.append({
            "at": round(time.perf_counter() - started, 2),
            "from": participant_identity,
            "text": text,
        })

    room.register_text_stream_handler(
        TOPIC_TRANSCRIPTION,
        lambda reader, identity: asyncio.create_task(
            on_transcription(reader, identity)))

    await room.connect(os.environ["LIVEKIT_URL"], token(room_name))

    # A room created by us dispatches a job; the worker needs a moment to pick
    # it up. Waiting on the event rather than sleeping a fixed time means a
    # fast dispatch is not paid for.
    try:
        await asyncio.wait_for(agent_joined.wait(), timeout=settle)
    except asyncio.TimeoutError:
        pass
    for existing in room.remote_participants.values():
        if existing.identity != IDENTITY:
            agent_joined.set()

    sent = []
    for index, message in enumerate(messages):
        if index:
            await asyncio.sleep(gap)
        await room.local_participant.send_text(message, topic=TOPIC_CHAT)
        sent.append({"at": round(time.perf_counter() - started, 2),
                     "text": message})

    # Quiet period, not a fixed wait: stop once nothing new has arrived for a
    # while, so a fast gate does not cost the slow gate's timeout.
    deadline = time.perf_counter() + wait
    last_count, last_change = len(received), time.perf_counter()
    while time.perf_counter() < deadline:
        await asyncio.sleep(1.0)
        if len(received) != last_count:
            last_count, last_change = len(received), time.perf_counter()
        elif received and time.perf_counter() - last_change > 20:
            break

    await room.disconnect()
    return {
        "room": room_name,
        "agent_joined": agent_joined.is_set(),
        "sent": sent,
        "transcript": received,
        "first_reply_seconds": received[0]["at"] if received else None,
        "last_reply_seconds": received[-1]["at"] if received else None,
        "replies": len(received),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("message", nargs="*", help="what to say to Friday")
    parser.add_argument("--room", default="", help="room name suffix")
    parser.add_argument("--follow", action="append", default=[],
                        help="a second/third message in the same room")
    parser.add_argument("--wait", type=float, default=90.0,
                        help="seconds to keep listening")
    parser.add_argument("--settle", type=float, default=25.0,
                        help="seconds to wait for the agent to join")
    parser.add_argument("--gap", type=float, default=8.0,
                        help="seconds between messages")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    text = " ".join(args.message).strip()
    if not text:
        print("nothing to say")
        return 2
    room_name = f"friday-probe-{args.room or 'default'}"

    result = asyncio.run(converse(room_name, [text, *args.follow],
                                  wait=args.wait, settle=args.settle,
                                  gap=args.gap))
    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print(f"  room            {result['room']}")
        print(f"  agent joined    {result['agent_joined']}")
        print(f"  replies         {result['replies']}")
        print(f"  first reply     {result['first_reply_seconds']}s")
        for entry in result["transcript"]:
            print(f"\n  [{entry['at']:>6.2f}s {entry['from']}]\n"
                  + "\n".join("    " + line for line in entry["text"].splitlines()))
    return 0 if result["agent_joined"] else 1


if __name__ == "__main__":
    sys.exit(main())
