"""
The learning loop nobody has to remember to run.

`profile.learn_from_turn` worked from the day it was written, but only if the
model chose to call `profile_learn_from_turn` - and it almost never did,
because deciding to file a memory is not what a voice assistant is thinking
about mid-sentence. So the user model stayed nearly empty while the boss said
plenty worth keeping, and the briefing that was supposed to inform the next
turn had nothing in it.

This wires learning to the turn itself. Two halves, both deliberately off the
path the boss can hear:

    observe   every user turn is queued and learned from in the background.
              Extraction is a Gemini call - a second or so - and a second of
              silence before every reply is not a trade worth making.
    inject    the resulting briefing is held in memory and stamped into the
              chat context of the next turn. No I/O while he waits.

The queue is bounded and drops the oldest turn when it fills. Learning that
falls behind must never become back-pressure on speech.

Everything goes through the MCP tools rather than opening the SQLite store
here: one writer, one policy gate, and it keeps working when the store moves
behind the Edge Controller.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable
from typing import Any

logger = logging.getLogger("friday-agent.autolearn")

#: Turns this short are acknowledgements - "yes", "go on", "stop". Extraction
#: costs a model call, and there is nothing in two words to learn.
MIN_WORDS = 3

#: How many turns may wait to be learned from. Beyond this the oldest is
#: dropped: falling behind is acceptable, blocking the voice path is not.
MAX_PENDING = 16

Invoke = Callable[[str, dict], Awaitable[Any]]


def payload(raw: Any) -> dict:
    """
    Unwrap whatever an MCP tool call returned into the dict the tool returned.

    LiveKit's default resolver hands back the serialised MCP TextContent -
    ``{"type": "text", "text": "<the actual JSON>"}`` - so the useful payload
    is one layer further down (llm/mcp.py:61). Tolerates all three shapes
    rather than assuming, because the resolver is configurable.
    """
    if isinstance(raw, dict):
        value: Any = raw
    else:
        try:
            value = json.loads(str(raw))
        except (TypeError, ValueError):
            return {}
    if isinstance(value, dict) and "text" in value and "status" not in value:
        try:
            value = json.loads(value["text"])
        except (TypeError, ValueError):
            return {}
    return value if isinstance(value, dict) else {}


class AutoLearner:
    """
    Learns from turns in the background and keeps a briefing ready.

    `invoke(tool_name, arguments)` runs one MCP tool and returns its result.
    """

    def __init__(self, invoke: Invoke, *, min_words: int = MIN_WORDS,
                 max_pending: int = MAX_PENDING, on_learned=None) -> None:
        self._invoke = invoke
        # Called when the briefing actually changes, so the agent can fold it
        # into its instructions. Only then - refreshing on every turn rewrites
        # the chat context and costs a regenerated reply.
        self._on_learned = on_learned
        self._min_words = min_words
        self._queue: asyncio.Queue[tuple[str, str]] = asyncio.Queue(max_pending)
        self._task: asyncio.Task | None = None
        self._brief = ""
        self._conflicts: dict[str, str] = {}
        self._last_seen = ""
        self.learned = 0  # for logs and tests; not load-bearing

    # -- lifecycle ---------------------------------------------------------

    async def start(self) -> None:
        """Load the briefing that already exists, then start the worker."""
        await self._refresh()
        if self._task is None:
            self._task = asyncio.create_task(self._worker(), name="ada-autolearn")

    async def aclose(self) -> None:
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):  # noqa: B014 - shutdown
                pass
            self._task = None

    # -- observe -----------------------------------------------------------

    def observe(self, user_text: str, assistant_text: str = "") -> bool:
        """
        Queue a turn to learn from. Returns whether it was queued.

        Never blocks and never raises: it is called from the turn hook, and a
        problem here must cost the reply nothing.
        """
        text = (user_text or "").strip()
        if len(text.split()) < self._min_words:
            return False
        if text == self._last_seen:
            return False  # the same sentence twice teaches nothing new
        self._last_seen = text

        try:
            self._queue.put_nowait((text, (assistant_text or "").strip()))
        except asyncio.QueueFull:
            try:
                self._queue.get_nowait()      # drop the oldest, keep the newest
                self._queue.task_done()
                self._queue.put_nowait((text, (assistant_text or "").strip()))
                logger.warning("autolearn is behind; dropped the oldest turn")
            except (asyncio.QueueEmpty, asyncio.QueueFull):
                return False
        return True

    async def _worker(self) -> None:
        while True:
            user_text, assistant_text = await self._queue.get()
            try:
                await self._learn(user_text, assistant_text)
            except asyncio.CancelledError:
                raise
            except Exception:
                # Learning is a background nicety. It never breaks a session.
                logger.exception("could not learn from a turn")
            finally:
                self._queue.task_done()

    async def _learn(self, user_text: str, assistant_text: str) -> None:
        result = payload(await self._invoke(
            "profile_learn_from_turn",
            {"user_said": user_text, "you_replied": assistant_text},
        ))
        if result.get("status") != "succeeded":
            logger.info("profile_learn_from_turn: %s",
                        result.get("error") or result.get("status") or "no result")
            return

        stored = result.get("learned") or []
        reinforced = result.get("reinforced") or []
        for conflict in result.get("conflicts") or []:
            subject = str(conflict.get("subject", "")).strip()
            if subject:
                self._conflicts[subject] = str(conflict.get("reason", "")).strip()

        self.learned += len(stored)
        if stored or reinforced:
            logger.info("learned %d, reinforced %d: %s", len(stored), len(reinforced),
                        [item.get("subject") for item in stored + reinforced])
            await self._refresh()

    async def _refresh(self) -> None:
        """Re-read the briefing so the next turn starts from the new one."""
        try:
            result = payload(await self._invoke("profile_get", {}))
        except LookupError as exc:
            # The MCP server is not up, or has no profile tools. Expected in a
            # bare test harness, and a stack trace for it every session start
            # is noise that hides real failures.
            logger.info("no user briefing available: %s", exc)
            return
        except Exception:
            logger.exception("could not refresh the user briefing")
            return
        if result.get("status") == "succeeded":
            fresh = (result.get("brief") or "").strip()
            changed = fresh != self._brief
            self._brief = fresh
            if changed and self._on_learned is not None:
                try:
                    self._on_learned()
                except Exception:
                    logger.exception("briefing callback failed")

    # -- inject ------------------------------------------------------------

    @property
    def brief(self) -> str:
        """The briefing as it will be injected, conflicts included."""
        parts = [self._brief] if self._brief else []
        if self._conflicts:
            parts.append(
                "[UNRESOLVED - he has told you two different things. Ask him "
                "which is right, once, when it next comes up]\n"
                + "\n".join(f"  - {subject}: {reason}" for subject, reason
                            in self._conflicts.items())
            )
        return "\n\n".join(parts)

    def inject(self, turn_ctx) -> bool:
        """
        Stamp the briefing onto this turn's context. Returns whether anything
        was added.

        The context handed to `on_user_turn_completed` is a throwaway copy that
        does not yet hold the new user message, so appending here puts the
        briefing immediately before what he just said - freshest position, and
        it does not accumulate across turns.
        """
        text = self.brief
        if not text:
            return False
        turn_ctx.add_message(role="system", content=text)
        return True


def last_assistant_text(turn_ctx) -> str:
    """
    What Friday said last, for the extractor's benefit.

    "yes, that one" only means something next to the question it answers, and
    the reply to the current turn does not exist yet at hook time.
    """
    for item in reversed(list(getattr(turn_ctx, "items", []) or [])):
        if getattr(item, "role", None) == "assistant":
            return (getattr(item, "text_content", "") or "").strip()
    return ""
