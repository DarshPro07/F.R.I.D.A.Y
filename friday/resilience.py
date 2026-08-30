"""
What happens when the model returns nothing.

Gemini intermittently answers with `finish_reason=STOP` and no content at all.
The plugin raises "no response generated" (google/llm.py:512), LiveKit retries,
and usually the second attempt is fine - the boss hears a pause. Twice in the
logs so far it has taken two retries at session start.

Three layers, in the order they fire:

    retry      LiveKit's own, already there. The error is marked retryable
               only while nothing has been emitted, so a half-spoken sentence
               is never repeated.
    fallback   a second model, wired here. Only reached once the primary has
               genuinely failed its retries.
    rescue     if every attempt came back empty *and a tool already succeeded
               this turn*, say so with a fixed line instead of leaving him in
               silence after his music started playing.

The rescue is the part that cannot be bought from upstream, because it needs
something only this codebase has: an ActionResult that says whether the action
really happened. It speaks through `session.say`, so no model is asked to
narrate and no tool is invoked a second time.

## What was measured, and what was not

The empty completions in the logs appear at session start, so that shape was
probed directly - the real system prompt, the real 15 core tool schemas, the
real greeting instruction, 25 trials streaming and 25 unary:

    streaming: 0/25 empty   median 1.23s
    unary    : 0/25 empty   median 1.14s

So it did not reproduce, and there is no local evidence that unary generation
helps. Google staff have recommended unary as a workaround for a related case
with *automatic* function calling; LiveKit does function calling manually, so
that case is not this one. Nothing here switches the conversational stream to
unary on a hunch - `TurnGuard` counts the real occurrences instead, so the
next decision can be made from data.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field

from friday.autolearn import payload  # the MCP TextContent unwrapper

logger = logging.getLogger("friday-agent.resilience")

#: How the empty-completion failure spells itself, at both layers.
EMPTY_MARKERS = (
    "no response generated",
    "failed to generate llm completion",
)


def is_empty_completion(error: object) -> bool:
    """Is this the model returning nothing, rather than a real API failure?"""
    text = f"{type(error).__name__}: {error}".lower()
    body = str(getattr(error, "body", "") or "").lower()
    if not any(marker in text for marker in EMPTY_MARKERS):
        return False
    # A blocked or truncated generation is a different problem with a
    # different answer, and must not be papered over as a hiccup.
    return "stop" in body or not body


# ---------------------------------------------------------------------------
# What actually happened this turn
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ToolOutcome:
    name: str
    status: str
    may_claim: bool

    @property
    def succeeded(self) -> bool:
        return self.status == "succeeded" and self.may_claim


def read_outcome(name: str, raw: object) -> ToolOutcome:
    """
    Read one tool result. Anything unparseable counts as unproven.

    `use_capability` wraps the real call, so its result carries the inner
    tool's status - unwrap one more layer rather than reporting the proxy.
    """
    body = payload(raw)
    if not body and isinstance(raw, str):
        try:
            body = json.loads(raw)
        except (TypeError, ValueError):
            body = {}
    if not isinstance(body, dict):
        return ToolOutcome(name, "unknown", False)
    return ToolOutcome(
        name=name,
        status=str(body.get("status", "unknown")),
        may_claim=bool(body.get("may_claim_completion", False)),
    )


def outcomes_from(event) -> list[ToolOutcome]:
    return [
        read_outcome(getattr(call, "name", "?"),
                     getattr(output, "output", None) if output else None)
        for call, output in event.zipped()
    ]


# ---------------------------------------------------------------------------
# The lines. Fixed, short, and never a claim a tool did not back.
# ---------------------------------------------------------------------------

DONE = (
    "That's done, boss.",
    "Handled, boss.",
    "Done - all set, boss.",
)
PARTLY = (
    "Partly there, boss - one of those didn't go through.",
    "Got most of it, boss. One didn't take.",
)
BROKEN = (
    "Something went sideways on my end there, boss. Say again?",
    "I lost that one, boss - run it by me again?",
)


def narrate(outcomes: list[ToolOutcome], *, turn: int = 0) -> str:
    """
    A line for a turn the model failed to finish.

    Never names a tool, and never says "done" for work no ActionResult
    supports - a rescue that lied would be worse than the silence.
    """
    done = [o for o in outcomes if o.succeeded]
    if not outcomes:
        return BROKEN[turn % len(BROKEN)]
    if len(done) == len(outcomes):
        return DONE[turn % len(DONE)]
    if done:
        return PARTLY[turn % len(PARTLY)]
    return BROKEN[turn % len(BROKEN)]


# ---------------------------------------------------------------------------
# The guard
# ---------------------------------------------------------------------------


@dataclass
class TurnGuard:
    """
    Watches a session so a finished action is never followed by silence.

    Attach once, after `session.start`. Everything it does is event driven -
    nothing here sits on the turn's latency path.
    """

    session: object = None
    pending: list[ToolOutcome] = field(default_factory=list)
    empty_completions: int = 0     # every occurrence, recoverable or not
    recovered: int = 0             # LiveKit's retry got there in the end
    rescued: int = 0               # we spoke because it did not
    _turn: int = 0

    def attach(self, session) -> None:
        self.session = session
        session.on("function_tools_executed", self.on_tools_executed)
        session.on("conversation_item_added", self.on_item_added)
        session.on("error", self.on_error)

    # -- observation -------------------------------------------------------

    def on_tools_executed(self, event) -> None:
        try:
            self.pending = outcomes_from(event)
        except Exception:
            logger.exception("could not read this turn's tool outcomes")
            self.pending = []

    def on_item_added(self, event) -> None:
        """An assistant message means the turn spoke for itself."""
        item = getattr(event, "item", None)
        if getattr(item, "role", None) == "assistant" and \
                (getattr(item, "text_content", "") or "").strip():
            self.pending = []
            self._turn += 1

    # -- rescue ------------------------------------------------------------

    def on_error(self, event) -> None:
        error = getattr(getattr(event, "error", None), "error", None)
        if error is None or not is_empty_completion(error):
            return

        self.empty_completions += 1
        if getattr(event.error, "recoverable", False):
            # LiveKit is going to try again. Count it; do not speak over it.
            self.recovered += 1
            logger.info("empty completion (retrying) - %d so far this session",
                        self.empty_completions)
            return

        logger.warning("empty completion, retries exhausted (%d this session)",
                       self.empty_completions)
        self.rescue()

    def rescue(self) -> str | None:
        """
        Speak for a turn the model could not finish. Returns what was said.

        Deliberately says nothing when no tool ran: an empty completion with
        no action behind it is a hiccup the boss can simply repeat, and a
        canned apology for every blip is its own kind of noise.
        """
        if not self.pending:
            return None
        line = narrate(self.pending, turn=self._turn)
        self.pending = []
        self._turn += 1
        self.rescued += 1
        try:
            self.session.say(line)
        except Exception:
            logger.exception("could not speak the rescue line")
            return None
        return line

    def describe(self) -> dict:
        return {
            "empty_completions": self.empty_completions,
            "recovered_by_retry": self.recovered,
            "rescued_by_narration": self.rescued,
            "pending_outcomes": len(self.pending),
        }
