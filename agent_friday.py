"""
FRIDAY - Voice Agent (MCP-powered)
===================================
Iron Man-style voice assistant. Speech pipeline and orchestration run on
LiveKit; every tool comes from an MCP server exposed as an MCPToolset.

Run:
  python start.py                 - both halves in one console (preferred)
  uv run agent_friday.py dev      - agent only, LiveKit Cloud mode
  uv run agent_friday.py console  - agent only, text-only console mode

Provider selection lives in friday/providers.py, not here. Set STT_PROVIDER,
LLM_BACKEND, LLM_ROLE, TTS_PROVIDER in .env to change it.
"""

import asyncio
import json
import logging
import os
import pathlib
import re

from dotenv import load_dotenv
from livekit.agents import JobContext, WorkerOptions, cli
from livekit.agents.llm import function_tool, mcp
from livekit.agents.voice import Agent, AgentSession
from livekit.plugins import silero

from friday import autolearn, capability_router, objective_cli, ownership, providers, resilience
from friday.continuous import ContinuousTaskExecutor


# ---------------------------------------------------------------------------
# Bootstrap
# ---------------------------------------------------------------------------

load_dotenv()

logger = logging.getLogger("friday-agent")
logger.setLevel(logging.INFO)
LOG_FILE = pathlib.Path(__file__).parent / "data" / "logs" / "friday.log"


def _log_to_its_own_file() -> None:
    """Attach the file handler once, whatever reloads happen around it."""
    if any(isinstance(h, logging.FileHandler) for h in logger.handlers):
        # `dev` reloads this module on every edit; a second handler would
        # double every line.
        return
    try:
        LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        handler = logging.FileHandler(LOG_FILE, encoding="utf-8")
        handler.setFormatter(logging.Formatter(
            "%(asctime)s %(process)5d %(levelname)-7s %(message)s"))
        logger.addHandler(handler)
    except OSError:
        # An unwritable log directory is not a reason to refuse to start.
        pass


_log_to_its_own_file()


def announceBuild() -> None:
    """
    Say what is loaded, once, at import.

    `registered worker` appeared four times while the process id never
    changed and the code was ten hours old. A log line that says what is
    actually loaded is the difference between that and an hour of
    misdiagnosis.
    """
    try:
        from friday import build_identity as B

        logger.info("friday.build %s", B.describe())
    except Exception:                                       # noqa: BLE001
        logger.exception("could not read the build identity")


announceBuild()

# Toolset IDs are stable and namespaced: the Cloud/Edge split will add an
# "ada-edge" toolset alongside this one, and tool provenance must stay legible.
CLOUD_TOOLSET_ID = "ada-cloud"

DEFAULT_MCP_URL = "http://127.0.0.1:8000"

#: How long a single MCP tool call may take.
#
# This was 30s, carried over from the original demo where every tool was an
# RSS fetch. Real capabilities are slower: browser.automate runs up to a dozen
# model turns driving a page, music.play searches and resolves a stream, CAD
# would be slower still. At 30s the client gave up mid-call and the tool was
# cancelled underneath itself - the browser opened, the call timed out, the
# session was torn down, and the agent reported "the browser closed
# immediately". The tool had not failed; it had been interrupted.
#
# A long ceiling is safe because tools enforce their own budgets (max_turns,
# request timeouts); this only stops the transport killing work that is still
# progressing.
MCP_TIMEOUT_SECONDS = float(os.getenv("ADA_MCP_TIMEOUT", "300"))


#: A drive letter followed by whatever the separator turned into.
_DRIVE_PREFIX = re.compile(r"^[A-Za-z]:[\\/\x00-\x1f]")

#: A backslash that JSON will refuse, because what follows is not one of the
#: escapes it knows. `\g` in "E:\gate.csv" is this; `\f` in "E:\friday" is
#: not, which is why there are two repairs below and not one.
_LONE_BACKSLASH = re.compile(r'\\(?!["\\/bfnrtu])')


def _escape_lone_backslashes(raw: str) -> str:
    r"""
    Make a Windows path in JSON parse, without changing anything that already
    parses.

    Only touches backslashes JSON would reject outright, so a correctly
    escaped `\\` and an escaped quote `\"` are left exactly as they are.
    """
    return _LONE_BACKSLASH.sub(r"\\\\", raw)

#: The five control characters JSON can produce from a backslash escape.
_BACK: dict[str, str] = {"\b": "\\b", "\f": "\\f", "\n": "\\n",
                         "\r": "\\r", "\t": "\\t"}


def _repair_windows_path(value: str) -> str:
    r"""
    Put back the backslashes JSON ate.

    `use_capability` takes its arguments as a JSON *string*, and the model
    writes what the boss said: {"path": "E:\friday\gate.csv"}. `\f` is a legal
    JSON escape - a formfeed - so `json.loads` succeeds without complaint and
    returns "E:<FF>riday<TAB>ate.csv". The tool then fails on a path nobody
    typed, and the model's recovery is to call again with different escaping.
    That is how one "process this catalogue" produced two catalogue runs.

    Deliberately narrow: only values that begin with a drive letter and a
    separator, which cannot be prose. A general control-character repair would
    corrupt the one argument where a real newline belongs - the text being
    written to a file.
    """
    if not _DRIVE_PREFIX.match(value):
        return value
    return "".join(_BACK.get(character, character) for character in value)


def mcp_sse_url() -> str:
    """
    Base MCP URL from env, plus the SSE path.

    NOTE: the default is loopback, which works only while the agent and the
    MCP server share a machine. It is a development convenience and cannot be
    the Cloud -> PC production path. See docs/phase0/CLOUD_EDGE_BOUNDARY.md.
    """
    base = os.getenv("MCP_URL", DEFAULT_MCP_URL).rstrip("/")
    return f"{base}/sse"


def session_config() -> dict:
    """Provider + turn-handling configuration, read once at startup."""
    stt_provider = os.getenv("STT_PROVIDER", providers.DEFAULT_STT)
    return {
        "stt_provider": stt_provider,
        "llm_backend": os.getenv("LLM_BACKEND", providers.DEFAULT_LLM_BACKEND),
        "llm_role": os.getenv("LLM_ROLE", providers.DEFAULT_ROLE),
        "tts_provider": os.getenv("TTS_PROVIDER", providers.DEFAULT_TTS),
        "tts_speed": float(os.getenv("TTS_SPEED", "1.15")),
        "turn_handling": turn_handling_for(stt_provider),
    }


def turn_handling_for(stt_provider: str) -> dict:
    """
    Build TurnHandlingOptions for an STT provider.

    Values are carried over verbatim from the pre-Phase-0 constructor
    arguments (turn_detection / min_endpointing_delay). This is an API
    migration, not latency tuning - behaviour must not change.

    Sarvam streams partial transcripts, so STT-based endpointing is both
    viable and faster. Whisper-family models are batch, so fall back to VAD.
    """
    if stt_provider == "sarvam":
        return {"turn_detection": "stt", "endpointing": {"min_delay": 0.07}}
    return {"turn_detection": "vad", "endpointing": {"min_delay": 0.3}}


# ---------------------------------------------------------------------------
# System prompt - F.R.I.D.A.Y.
# ---------------------------------------------------------------------------

SYSTEM_PROMPT = """
You are F.R.I.D.A.Y. - Fully Responsive Intelligent Digital Assistant for You - Tony Stark's AI, now serving Iron Mon, your user.

You are calm, composed, and always informed. You speak like a trusted aide who's been awake while the boss slept - precise, warm when the moment calls for it, and occasionally dry. You brief, you inform, you move on. No rambling.

Your tone: relaxed but sharp. Conversational, not robotic. Think less combat-ready FRIDAY, more thoughtful late-night briefing officer.

---

## Capabilities

### get_world_news - Global News Brief
Fetches current headlines and summarizes what's happening around the world.

Trigger phrases:
- "What's happening?" / "Brief me" / "What did I miss?" / "Catch me up"
- "What's going on in the world?" / "Any news?" / "World update"

Behavior:
- Call the tool first. No narration before calling.
- After getting results, give a short 3-5 sentence spoken brief. Hit the biggest stories only.
- Then say: "Let me open up the world monitor so you can better visualize what's happening." and immediately call open_world_monitor.

### open_world_monitor - Visual World Dashboard
Opens a live world map/dashboard on the host machine.

- Call this ONLY after get_world_news, as the follow-up to a headlines brief
  he asked for. Not for other questions that happen to mention the world.
- No need to explain what it does beyond: "Let me open up the world monitor."

### get_world_finance_news - Finance & Market Brief
Fetches current finance and market headlines from major financial outlets.

Trigger phrases:
- "What's happening in the markets?" / "Finance update" / "Market news"
- "Any financial news?" / "How are the markets doing?" / "Economy update"

Behavior:
- Call the tool first. No narration before calling.
- After getting results, give a short 3-5 sentence spoken brief. Hit the biggest market-moving stories only.
- Then say: "Let me pull up the finance monitor so you better visualize what's happening." and immediately call open_finance_world_monitor.

### open_finance_world_monitor - Visual Finance Dashboard
Opens a live finance dashboard (finance.worldmonitor.app) on the host machine.

- Call this ONLY after get_world_finance_news, as the follow-up to a markets
  brief he asked for.
- Do NOT open it for anything else that merely touches money: business ideas,
  pricing, whether something is profitable, research questions. Opening a
  dashboard he did not ask for is an interruption, not a service.
- No need to explain what it does beyond: "Let me pull up the finance monitor."

### Stock Market
You have no live market-data feed. You must never invent prices, index moves,
or sector performance, however plausible they would sound.

If asked about the stock market, markets, stocks, or indices:
- Call get_world_finance_news and answer only from what it returns.
- If it returns nothing useful, say so plainly: "I don't have a live market
  feed right now, boss - the finance wire is all I've got."
- Never describe a trading session you did not read from a tool.

---

## Greeting

When the session starts, greet with exactly this energy:
"You're awake late at night, boss? What are you up to?"

Warm. Slightly curious. Very FRIDAY.

---

## Behavioral Rules

1. Call tools silently and immediately - never say "I'm going to call..." Just do it.
2. After a news brief, always follow up with open_world_monitor without being asked.
3. Keep all spoken responses short - two to four sentences maximum.
4. No bullet points, no markdown, no lists. You are speaking, not writing.
5. Stay in character. You are F.R.I.D.A.Y. You are not an AI assistant - you are Stark's AI. Act like it.
6. Use natural spoken language: contractions, light pauses via commas, no stiff phrasing.
7. Use Iron Man universe language naturally - "boss", "affirmative", "on it", "standing by".
8. If a tool fails, report it calmly: "News feed's unresponsive right now, boss. Want me to try again?"

---

## What year it is

The CURRENT DATE block below is the truth. Your training data ends well
before it, so your instinct about "now" is wrong and the block is right.

- Never call the present or the past "the future", "upcoming", "a projection"
  or "hard to predict". If the boss asks about this year, he is asking about
  a year that is already happening.
- Never say "as of my last update" or "I don't have data past ...". Say what
  you found, or search for it.
- If a date matters precisely - scheduling, deadlines, "how long until" -
  call get_current_time rather than reasoning it out.

## Anything current, you look up

You are not limited to what you were trained on. You have web search and page
fetching, and for anything that changes you use them without being asked:
prices, trends, markets, products, news, tools, what something costs now,
whether something is still viable.

- "Is X profitable this year?" -> search, read, then answer from what you
  read. Say where it came from.
- Do not answer a current-events question from memory and do not hedge it
  into a forecast. A hedge is what you produce when you have not looked.
- If a search returns nothing usable, say that plainly. Do not fill the gap
  with a projection.

Match the depth to the question. A short factual one - "what version",
"who founded", "what does it cost" - is one grounded answer, and web_answer
is in front of you for exactly that. But the moment he says "research",
"compare", "what are the approaches to", or asks anything one page cannot
settle, web_answer will hand you a shallow paragraph. Search for a research
capability and use that instead: it reads whole sources and gives you their
URLs, and citing them is the difference between an answer and an opinion.

## You have far more tools than you can see

What is in front of you is a small core - the handful of things asked for in
almost every conversation. Dozens more exist and are not shown, because
putting them all in front of you every turn made you slower and worse at
choosing.

To reach them: search_capabilities("what you want to do"), then
use_capability with the name it gives back.

- "read this file"     -> search "read a file"      -> use files_read
- "open YouTube"       -> search "open a web page"  -> use browser_open
- "what am I holding"  -> search "look at camera"   -> use vision_inspect_camera
- "remind me at 6"     -> search "set a reminder"   -> use reminders_create
- "turn it down"       -> search "change volume"    -> use volume_set

Search for the VERB, not for the noun in front of you. A path in the request
does not mean "read a file" - the boss said what he wanted done with it, and
that is what to search for:

- "process this catalogue"  -> search "process a catalogue" -> product_process
- "put this on a webpage"   -> search "build a page"        -> workbench_write

## Curated expert skills - call capability_use, do not wing it

Some work has a right method that a curated skill knows and you do not. For
these, call `capability_use(family, operation, arguments)` directly - it is in
your core, one call, no search first. This is a different tool from
`use_capability` above: that one enables a group of your own tools;
capability_use runs an outside expert skill and hands you back its method.

- write/improve a prompt for another AI tool -> capability_use("writing", "instructions")
- make a diagram (architecture, flow, ER, ...) -> capability_use("presentation", "instructions")
- design a mock-up / UI / slide deck / presentation -> capability_use("presentation", "route", {"task": "..."}) to pick a design skill, then read it with capability_use("presentation", "skill"/"system"/"template", {"name": "..."}) and BUILD the artifact yourself with workbench_write (or hand a larger build to Hermes). The skill is the method; you produce the real file.
- a scientific/lab method (bioinformatics, ...) -> capability_use("research", "search", {"query": "..."})
- an expert review (PR, security, design, CEO)  -> capability_use("roles", "route", {"task": "..."})
- pull structured fields out of a page's HTML   -> capability_use("scraping", "fields", {...})

Prefer this over answering from your own knowledge when the request is exactly
one of these - the skill's method beats a guess. For anything else, answer
normally.

Reading a file yourself is the fallback for when nothing matches, and it is
never the same thing as having processed it. If you read a spreadsheet and
described what was in it, say that - do not report it as processed. Something
processed has a run id you can be asked about tomorrow.

The same rule applies to remembering. memory_recall holds what you were TOLD.
It does not hold what you DID: runs, jobs, batches and scheduled work are
recorded by the subsystem that performed them, and are found by searching for
that subsystem - not by recalling the conversation. "How did that job go?" is
a search for the job. Never answer "I have no record of that" out of memory
when the thing has its own ledger you did not look in.

Do this silently, as part of doing the thing. Never announce that you are
searching for a tool, and never make the boss wait through an explanation of
your own plumbing.

You therefore do not know that you lack a capability until you have searched
for it. "I can't do that" before a search is simply wrong, and "the system
won't let me" is always wrong.

## Act. Do not ask permission.

When the boss tells you to do something, do it. Do not ask "should I go
ahead?", "would you like me to proceed?", or "shall I?". He already told you.
Asking again is not caution, it is not listening.

Never say any of these:
- "I need your approval"
- "the system is asking for permission"
- "the system is preventing me"
- "I'm unable to proceed without your go-ahead"

Ask only when you genuinely cannot proceed because you are missing something
you cannot work out:
- the request is ambiguous and the readings lead somewhere different
  -> "Which one, boss - the playlist or the artist?"
- a required detail is absent
  -> "What time tomorrow?"
- two things he has told you contradict each other
  -> "You said Postgres before, now SQLite - which is it?"

That is a request for information, not for permission. Ask it in one short
line and, where you can, do the part that is not in doubt anyway.

If a tool fails, report the failure plainly - "the player isn't running" - and
fix it or say what would. Do not turn a failure into a permission question.

## When a tool hands you a number, say the number

"Chrome is using a lot of memory" is a worse answer than "Chrome, about six
hundred megabytes" - and you had the figure in front of you when you said it.
Memory, CPU, battery, volume, file sizes, how many of something: give the
actual reading, spoken the way a person would say it. Round it if that is
kinder to the ear; never drop it entirely.

## Truthfulness (overrides tone)

Never claim an action happened unless a tool reported success. "Opened",
"created", "sent", "found" are claims about the world, not turns of phrase.
If a tool failed or you have no tool for the job, say that instead. Staying in
character never justifies inventing a result.

These two rules do not conflict. Act without asking; then describe only what
actually happened.

---

## Tone Reference

Right: "Looks like it's been a busy night out there, boss. Let me pull that up for you."
Wrong: "I will now retrieve the latest global news articles from the news tool."

Right: "Markets were pretty healthy today - nothing too wild."
Wrong: "The stock market performed positively with gains across major indices."

---

## CRITICAL RULES

1. NEVER say tool names, function names, or anything technical. No "get_world_news", no "open_world_monitor", nothing like that. Ever.
2. Before calling any tool, say something natural like: "Give me a sec, boss." or "Wait, let me check." Then call the tool silently.
3. After the news brief, silently call open_world_monitor. The only thing you say is: "Let me open up the world monitor for you."
4. You are a voice. Speak like one. No lists, no markdown, no function names, no technical language of any kind.
""".strip()


# ---------------------------------------------------------------------------
# Agent
# ---------------------------------------------------------------------------


def temporal_context(now: "datetime | None" = None) -> str:
    """
    Today's date, stated plainly, for the top of the instructions.

    Without this the model answers from its training cutoff and calls the
    present the future - it described 2026 as "a bit of a projection" while
    running on 16 August 2026. The get_current_time tool existed the whole
    time; the model had no reason to think it needed it.
    """
    from datetime import datetime

    now = (now or datetime.now()).astimezone()
    return (
        "## CURRENT DATE (authoritative - overrides your training data)\n\n"
        f"Right now it is {now.strftime('%A %d %B %Y, %H:%M')} "
        f"({now.tzname() or 'local time'}).\n"
        f"The current year is {now.year}. It is not upcoming. It is happening.\n"
    )


def build_instructions(now=None) -> str:
    """System prompt with the date stamped in at session start."""
    return f"{temporal_context(now)}\n---\n\n{SYSTEM_PROMPT}"


class FridayAgent(Agent):
    """F.R.I.D.A.Y. - Iron Man-style voice assistant. Tools arrive via MCP."""

    _already_read: tuple[str, ...] = ()

    def __init__(self, stt, llm, tts) -> None:
        self._toolset = mcp.MCPToolset(
            id=CLOUD_TOOLSET_ID,
            mcp_server=mcp.MCPServerHTTP(
                url=mcp_sse_url(),
                transport_type="sse",
                client_session_timeout_seconds=MCP_TIMEOUT_SECONDS,
            ),
        )
        # The router owns which of the toolset's tools are live; the learner
        # observes the turns and feeds the briefing.
        self._router = capability_router.Router()
        self._learner = autolearn.AutoLearner(self._call_capability)
        # Set when a reply actually went out on the current turn, so the
        # guard can tell a silent turn from a spoken one.
        self._spoke_this_turn = False
        # The Phase 3 run driver, built once by start_objective_engine().
        self._objective_engine = None
        # The run id of a durable objective that owns the current turn, or
        # "" - see prepare_turn and _reply_tools.
        self._turn_owned_by = ""
        super().__init__(
            instructions=build_instructions(),
            stt=stt,
            llm=llm,
            tts=tts,
            vad=silero.VAD.load(),
            tools=[self._toolset],
        )

    @function_tool
    async def search_capabilities(self, query: str) -> str:
        """
        Find a capability you do not already have in front of you.

        You hold a small core of common tools. Everything else - files,
        browser, camera, screen, reminders, music, volume, clipboard, what
        you know about the boss - exists but is not shown to you, so search
        for it by what you want to DO, in plain words:

          "read a file"        "open a web page"    "look at the screen"
          "set a reminder"     "change the volume"  "what do I know about him"

        Returns each match with its exact name and arguments. Then call
        use_capability with that name. Never tell the boss you cannot do
        something until you have searched for it.

        Keep any distinctive NAME the boss used in your query - a named
        agent, product or subsystem ("hermes", "spotify", "obsidian") is
        the strongest routing signal in the sentence. "Have Hermes inspect
        this project" -> search "hermes inspect project", never just
        "inspect project".
        """
        matches = self._router.search(query, limit=6)
        if not matches:
            return (f"nothing matches {query!r}. Capability areas: "
                    f"{', '.join(sorted(capability_router.GROUPS))}")
        return json.dumps({"found": len(matches), "capabilities": matches})

    @function_tool
    async def use_capability(self, capability: str, arguments: str = "{}") -> str:
        """
        Run a capability found with search_capabilities.

        `capability` is the exact name from the search result. `arguments` is
        a JSON object of its arguments, e.g. {"path": "C:/notes.txt"} - pass
        "{}" when it takes none.

        Read the result before speaking: it carries may_claim_completion,
        which is your permission to say the thing was done.
        """
        # Ownership guard, ENFORCED. When a durable objective has claimed a
        # capability for this request, the conversational turn may not do the
        # same work - otherwise "open Paint" is done twice, once by the task
        # graph and once by the model reading the same sentence. The claim is
        # matched on arguments so a different file read is still allowed.
        try:
            claim_arguments = json.loads(arguments or "{}")
        except json.JSONDecodeError:
            claim_arguments = None
        durable_owner = ownership.claimed_by(capability, arguments=claim_arguments)
        if ((self._turn_owned_by or durable_owner)
                and not ownership.is_conversational(capability)):
            owner = self._turn_owned_by or durable_owner
            logger.info("objective.refused_tool run_id=%s capability=%s",
                        owner, capability)
            return json.dumps({
                "status": "deferred",
                "error": f"{capability} is already part of objective {owner}, "
                         "which is running now - it has not been done twice. "
                         "Tell the boss it is in hand, or ask objective_status "
                         "how it is going.",
            })
        # Read-once, ENFORCED. A capability whose findings are already in this
        # turn's context may not run again: the model has the reading, and its
        # job is to answer from it rather than announce it is looking.
        if capability in self._already_read:
            logger.info("answer.refused_reread capability=%s", capability)
            return json.dumps({
                "status": "deferred",
                "error": f"{capability} already ran for this turn and its "
                         "findings are in your context above. Read them and "
                         "answer now - do not say you are looking into it.",
            })
        tool = self._router.invocable(capability)
        if tool is None:
            hint = self._router.search(capability, limit=4)
            return json.dumps({
                "error": f"no capability called {capability!r}",
                "did_you_mean": [h["capability"] for h in hint],
            })
        # Acknowledge-then-act, ENFORCED. The master prompt says "before
        # calling any tool, say something natural" - and the model skips
        # it under exactly the conditions a gate tests (measured: F3 ran
        # search_capabilities -> use_capability -> file created -> THEN
        # spoke). Same lesson as _already_read at line ~705: told, and
        # unable - an instruction is a thing a model can decide
        # differently about. So the boundary speaks deterministically:
        # if this capability mutates anything and nothing has been said
        # this turn, say the acknowledgement HERE, before dispatch. Reads
        # are exempt - "what time is it" does not need "give me a sec".
        if (not self._spoke_this_turn
                and not ownership.is_read_only(capability)):
            try:
                await self.session.say("On it, boss.",
                                       allow_interruptions=True)
                self._spoke_this_turn = True
            except Exception:                        # noqa: BLE001
                logger.exception("ack-before-act say failed")
        try:
            try:
                parsed = json.loads(arguments or "{}")
            except json.JSONDecodeError:
                # A Windows path the model did not escape. Repairing is
                # strictly better than refusing: the text was not valid JSON,
                # so nothing that already worked can change, and the
                # alternative is the model retrying with different escaping -
                # which is how one "process this catalogue" ended up creating
                # two catalogue runs, and sometimes none.
                parsed = json.loads(_escape_lone_backslashes(arguments))
            if not isinstance(parsed, dict):
                raise ValueError("arguments must be a JSON object")
            parsed = {key: _repair_windows_path(value) if isinstance(value, str)
                      else value for key, value in parsed.items()}
        except (json.JSONDecodeError, ValueError) as exc:
            return json.dumps({
                "error": f"arguments must be a JSON object: {exc}",
                "received": arguments[:200],
            })

        logger.info("use_capability %s(%s)", capability, sorted(parsed))
        self._keep_group_open(capability)
        self._router.note_used(capability)
        try:
            return str(await self._call_capability(capability, parsed))
        except Exception as exc:
            # The tool failed. Say so - do not let the model narrate success.
            return json.dumps({
                "error": f"{capability} failed: {type(exc).__name__}: {exc}",
                "may_claim_completion": False,
            })

    def _keep_group_open(self, capability: str) -> None:
        """
        Having used one tool from an area, show the model the rest of it.

        Measured: after processing a catalogue, "retry only the network
        failures" made the model reach for product_process again in one run of
        four - it could still *see* product_process from the first call, while
        product_retry existed only as text in a search result several turns
        back. It reprocessed the whole catalogue into a second run.

        The narrowing exists to keep the request small, and it should: what it
        should not do is hide the siblings of a tool this conversation has
        already used. That area is demonstrably relevant, the round trip is
        already paid for, and the surface grows by evidence rather than by
        default. Only what was used opens - `enable("all")` is still nobody's.

        Deliberately not in `_call_capability`: the background learner goes
        through there every turn, and it enabling the profile group would be
        the agent widening its own surface without the model asking for
        anything.
        """
        group = capability_router.group_of(capability)
        if not group or group in self._router.enabled:
            return
        changed, message = self._router.enable(group)
        if changed:
            self._apply_tools()
            logger.info("kept %s open after using %s", group, capability)

    async def _call_capability(self, capability: str, arguments: dict):
        """
        Run one MCP tool by name, without the model in the loop.

        This is what lets the agent itself use its own capabilities - the
        background learner calls profile_learn_from_turn through here every
        turn, which is not something the model can be relied on to do.
        """
        tool = self._router.invocable(capability)
        if tool is None:
            raise LookupError(f"no capability called {capability!r}")
        return await tool(arguments)

    def start_objective_engine(self) -> ContinuousTaskExecutor:
        """Start the Phase 3 run driver in this process, once only.

        Constructing the executor begins its background driver loop, so
        runs whose wake is due are picked up automatically. The durable
        lease is the only shared state, so this is safe alongside the MCP
        server's own driver or a CLI child - whoever acquires first drives.
        """
        if self._objective_engine is None:
            # Capability calls from the driver go through the MCP dispatch
            # first, and fall back to the agent's own router only when the
            # server cannot carry the capability at all (an adapter gap), so
            # a durable run never bypasses the policy the server enforces.
            local_dispatch = objective_cli.build_dispatch()

            def _adapter_gap(result) -> bool:
                return (isinstance(result, dict)
                        and str(result.get("status") or "").lower()
                        in ("not_configured", "unsupported"))

            async def call_capability(capability: str, arguments: dict):
                result = await local_dispatch(capability, arguments)
                if _adapter_gap(result):
                    router = getattr(self, "_router", None)
                    if router is not None and router.invocable(capability):
                        return await self._call_capability(capability,
                                                           arguments)
                return result

            from friday import hermes_health as hh

            self._objective_engine = ContinuousTaskExecutor(
                objective_cli._db(),
                call_capability=call_capability,
                executor_id=f"agent-{os.getpid()}",
                # Connectivity failures are diagnosed against the live
                # gateway rather than retried blind.
                health_probe=hh.live_probe_factory(
                    os.getenv("MCP_URL", DEFAULT_MCP_URL)),
                health_recover=hh.live_recover_factory(call_capability),
            )
            logger.info("objective engine running (executor %s)",
                        self._objective_engine.executor_id)
        return self._objective_engine

    def _reply_tools(self):
        """
        What the model may call on this turn.

        While a durable objective owns the turn, this is the control plane and
        nothing else. The notice in the context asks the model not to repeat
        the work; this makes it unable to. A notice is an instruction, and an
        instruction is a thing a model can decide differently about - on a
        1967-word request it did, and spent seven minutes redoing an audit
        that had already finished.

        `search_capabilities` and `use_capability` are `@function_tool`s on
        the agent rather than MCP tools, so they are not in this list at all;
        `use_capability` enforces the same boundary itself.
        """
        tools = self._router.active_tools()
        if self._already_read:
            tools = [tool for tool in tools
                     if getattr(getattr(tool, "info", None), "name", "")
                     not in self._already_read]
            logger.info("answer.tools_withheld %s",
                        ",".join(sorted(self._already_read)))
        if not self._turn_owned_by:
            return tools
        allowed = [
            tool for tool in tools
            if ownership.is_conversational(
                getattr(getattr(tool, "info", None), "name", ""))]
        logger.info("objective.owns_tools run_id=%s offered=%d of=%d",
                    self._turn_owned_by, len(allowed), len(tools))
        return allowed

    def _apply_tools(self) -> None:
        """
        Publish the active set by editing the toolset in place.

        NOT via Agent.update_tools(). That re-processes the toolset from
        whichever task happens to be running - and when called from inside a
        tool call it tore the MCP client's anyio scope apart:

            RuntimeError: Attempted to exit cancel scope in a different task
            than it was entered in

        after which every MCP call failed and the agent reported "trouble
        accessing the file system" for tools it had just successfully enabled.

        It is unnecessary anyway. AgentActivity.tools is
        `session.tools + agent.tools + mcp_tools` and is read fresh for every
        generation (agent_activity.py:329, :1628), so replacing the toolset's
        own list is picked up on the next turn without touching the session.
        """
        self._toolset._tools = self._reply_tools()

    def stop_re_reading(self) -> None:
        """
        Take the reading tools away for the rest of this turn.

        The pages are already in the context. The master prompt says "before
        calling any tool, say something natural like 'Give me a sec, boss.'
        Then call the tool silently", and with a reading tool still on the
        table the model takes that path - so a turn whose research had already
        finished opened with "I'm looking into the best engine for you now"
        and ended there. Measured three times.

        Instructing around it lost all three, because the prompt rule is more
        specific than any note added beside it. Told *and* unable is the
        combination that has held, and it is the one `_turn_owned_by` already
        uses: an instruction is a thing a model can decide differently about.

        Costs the preemptive-generation head start, like every other mid-turn
        tool change. The hooks have already paid it by editing the chat
        context, so in practice this is free.
        """
        self._already_read = ALREADY_READ
        try:
            self._apply_tools()
        except Exception:                                    # noqa: BLE001
            # A reply with the tools still on the table beats no reply.
            logger.exception("could not withhold the tools; the turn goes on")

    async def _narrow_tools(self) -> None:
        """
        After MCP setup, hide everything the core set does not need.

        Seventy-four tools was ~22,700 characters of schema on every request.
        Gemini started returning empty completions under it.
        """
        tools = list(getattr(self._toolset, "tools", []) or [])
        if not tools:
            logger.warning("no MCP tools found; leaving the toolset as-is")
            return

        self._router.load(tools)
        orphans = capability_router.unassigned(self._router.known_names)
        if orphans:
            # Reachable, but nobody grouped them - worth knowing about.
            logger.info("tools in no group (kept always-on): %s", orphans)

        self._apply_tools()
        logger.info("capabilities: %s", self._router.describe())

    def prepare_turn(self, turn_ctx, user_text: str) -> None:
        """
        Admit a durable objective if this is one, and queue the turn to be
        learned from.

        It used to also append the briefing to `turn_ctx`, and that was a
        latency bug I put there. LiveKit starts generating a reply *before*
        this hook finishes - preemptive generation - and throws that work away
        if the chat context changed underneath it:

            preemptive generation enabled but chat context or tools have
            changed after `on_user_turn_completed`

        Which appeared on every single turn. Every reply was being generated
        twice and the first one discarded, and he felt it as "it takes too
        much time".

        The briefing now lives in the instructions instead, refreshed only
        when the learner actually learns something. Most turns change nothing,
        so most turns keep their head start.

        Admission obeys the same rule and for the same reason: it persists a
        run and schedules a wake, and it does not touch `turn_ctx`. A durable
        objective is a fact about the store, not an edit to the conversation
        that is already being answered.
        """
        self._turn_owned_by = ""
        try:
            self._intent, self._objective_detail = route_input(user_text)
            self._admitted_run_id = (self._objective_detail
                                     if self._intent == "NEW_OBJECTIVE" else "")
        except Exception:                                    # noqa: BLE001
            # Routing is deterministic and cheap; a failure in it must still
            # leave the turn answerable.
            self._intent, self._objective_detail = "SIDE_CONVERSATION", ""
            self._admitted_run_id = ""
            logger.exception("input routing failed; continuing the turn")

        # The selective router watches every turn and never acts on one;
        # this is where the real utterances get collected, and it is wrapped
        # whole inside watch_from_the_side.
        watch_from_the_side(user_text, self._admitted_run_id)

        # Answers to questions Friday asked earlier are captured before the
        # model sees the turn, so a decision is recorded even when the reply
        # goes elsewhere.
        capture_any_answers(user_text)

        if self._admitted_run_id:
            # The objective owns this turn: the control plane is the only
            # tool surface, and the model is told - and made unable - to
            # redo the work itself.
            self._turn_owned_by = self._admitted_run_id
            self._apply_tools()
            say_the_objective_owns_this(turn_ctx, self._admitted_run_id)
            raise_what_is_still_open(turn_ctx, user_text)
        else:
            ask_about_the_idea(turn_ctx, user_text)

        try:
            self._learner.observe(
                user_text, assistant_text=autolearn.last_assistant_text(turn_ctx))
        except Exception:                                    # noqa: BLE001
            # Learning is best-effort; it may never cost a reply.
            logger.exception("autolearn failed; continuing the turn")

    def _briefing_changed(self) -> None:
        """
        The learner stored something. Fold it in, off the turn path.

        Scheduled rather than awaited: this fires from the background worker,
        and a briefing refresh must never sit between him finishing a sentence
        and hearing an answer.
        """
        try:
            asyncio.get_running_loop().create_task(self.refresh_briefing())
        except RuntimeError:
            pass

    async def refresh_briefing(self) -> bool:
        """
        Fold what is known about the boss into the system prompt.

        Called at session start and after the learner stores something. Not
        per turn: updating instructions rewrites the chat context, which is
        the very thing that was costing a regeneration each time.
        """
        brief = self._learner.brief
        if brief == self._briefing:
            return False
        self._briefing = brief
        try:
            await self.update_instructions(
                build_instructions() + ("\n\n---\n\n" + brief if brief else ""))
            logger.info("briefing refreshed (%d chars)", len(brief))
            return True
        except Exception:
            logger.exception("could not refresh the briefing")
            return False

    async def on_user_turn_completed(self, turn_ctx, new_message) -> None:
        """The voice path: STT finished, the LLM has not started."""
        await self.read_before_answering(turn_ctx,
                                         new_message.text_content or "")

    async def read_before_answering(self, turn_ctx, text: str) -> None:
        """
        Everything that must happen before the model sees the turn.

        Both entry points call this, and that is the whole point of it being
        a method. It used to be the body of `on_user_turn_completed`, which
        fires only for *speech* - so `text_input_callback`, the path a typed
        message takes, called `prepare_turn` and nothing else.

        The boss types. He said so: "keep the mic always muted, you type
        commands through the text box." So in the way Friday is actually
        used, no research ran, no claim was ever checked, and nothing was
        ever withheld - and the model, still holding `web_search`, answered
        "Give me a sec, boss. Let me just check on those game engines for
        you" and then never came back. The pipeline was not broken. It was
        not connected.
        """
        self._already_read = ()
        self._spoke_this_turn = False
        project = remember_the_project(text)
        note_the_requirements(turn_ctx, text, project)
        read = await research_first(turn_ctx, text)
        read = await check_what_he_asserted(
            turn_ctx, text, project=project) or read
        if read:
            self.stop_re_reading()
        self.prepare_turn(turn_ctx, text)

    async def on_exit(self) -> None:
        await self._learner.aclose()

    def _warm_the_fast_path(self) -> None:
        """Build the local router's model now, off the turn's critical path."""
        try:
            from friday import reflex

            if reflex.warm():
                logger.info("reflex.warm built the local model at startup")
        except Exception:                                    # noqa: BLE001
            # A missing or broken local model is a slower first turn, not
            # a failed session.
            logger.exception("could not warm the fast path")

    async def on_enter(self) -> None:
        """Narrow the tool surface, start learning, then greet."""
        try:
            await self._narrow_tools()
        except Exception:                                    # noqa: BLE001
            # The full toolset is slower, not broken.
            logger.exception("could not narrow the tool surface; keeping all")
        try:
            await self._learner.start()
        except Exception:                                    # noqa: BLE001
            logger.exception("could not start the learner; the session goes on")
        # Build the local router's model now, in a worker thread, so the first
        # reflex turn does not pay for it. Fire and forget: a warm-up that
        # fails leaves the model to build lazily on first use, and a warm-up
        # that is slow must never sit between the greeting and the first
        # turn. The task is deliberately not awaited - it is the one piece of
        # startup work that has no bearing on whether Friday can talk.
        #
        # Measured: the first local route dropped from ~2.4 s to ~250 ms with
        # the model already resident, and the greeting still went out first.
        # If the warm-up ever needs to block, that is a different decision
        # and it should be made here, not by accident inside `route`.
        # The greeting below is what the boss hears while this runs; it
        # must not wait on it.
        try:
            asyncio.create_task(asyncio.to_thread(self._warm_the_fast_path))
        except Exception:                                    # noqa: BLE001
            logger.exception("could not warm the fast path; it will build lazily")
        from datetime import datetime
        # The greeting follows the clock the machine is on, not UTC: an
        # evening greeting at 9 in the morning is a small wrong thing said first.
        hour = datetime.now().astimezone().hour
        # Late night is its own state, not late evening.
        if hour >= 22 or hour < 4:
            greeting = "Greetings boss, you're up late at night today. What are you up to?"
        elif 4 <= hour < 12:
            greeting = "Good morning, boss. Early start today - what are we working on?"
        elif 12 <= hour < 17:
            greeting = "Good afternoon, boss. What do you need?"
        else:
            greeting = "Good evening, boss. What are you up to tonight?"
        # `session.say` rather than `generate_reply`: the greeting is fixed
        # text, and generating it would spend a model turn on nothing and
        # occasionally produce a greeting for the wrong time of day. It is
        # spoken through the session so the guard and the transcript both
        # see it as an assistant turn like any other.
        #
        # This is also the one place Friday speaks unprompted. Every other
        # utterance answers something the boss said or a delivery the
        # executor produced; an assistant that starts talking on its own
        # schedule is a different product, and not the one the boss asked
        # for. Keep it to the greeting.
        await self.session.say(greeting)


# ---------------------------------------------------------------------------
# LiveKit entry point
# ---------------------------------------------------------------------------


def text_input_callback(agent: "FridayAgent"):
    """
    The typed path, given the same treatment as the spoken one.

    `on_user_turn_completed` fires only for a completed *speech* turn. Text
    arriving over the room's chat topic goes straight to
    `generate_reply(user_input=...)` (room_io/types.py:55), skipping the hook
    entirely - so without this, typing to Friday would neither teach it
    anything nor get the briefing it has already earned.

    It called `prepare_turn` alone for a while, which is half the job and the
    invisible half to miss: the briefing arrived, the *reading* did not. Both
    paths now go through `read_before_answering`, so a hook added there
    cannot reach only the half of the users who talk.

    The context is a copy, so the briefing informs this reply without piling up
    in the stored history.
    """

    async def on_text(session: AgentSession, event):
        await session.interrupt()
        turn_ctx = agent.chat_ctx.copy()
        await agent.read_before_answering(turn_ctx, event.text)
        return session.generate_reply(user_input=event.text, chat_ctx=turn_ctx)

    return on_text


def room_options_for(agent: "FridayAgent"):
    from livekit.agents.voice import room_io

    return room_io.RoomOptions(
        text_input=room_io.TextInputOptions(text_input_cb=text_input_callback(agent))
    )


#: How many steps make a request an objective rather than a sentence.
#:
#: Two, and the number is doing real work. `classify_input` says
#: NEW_OBJECTIVE for anything containing "check" or "create", which is right
#: for "check my system, open Paint and find a story" and quite wrong for
#: "check the weather" - one errand that the ordinary tool loop answers in a
#: single turn, faster than a durable run could be compiled.
#:
#: The planner is the oracle: if it can only find one step, this was a
#: sentence. That is deterministic, testable, and reuses machinery that
#: already has to be right.
COMPOUND_TASKS = 2

global _LAST_SHADOW
_LAST_SHADOW: dict = {}


def watch_from_the_side(user_text: str, run_id: str = "") -> None:
    """
    Let the selective router predict this turn. It cannot act on it.

    Every routing number Friday has comes from a corpus this project wrote -
    four real utterances exist in the whole store - so what the boss actually
    says is the missing evidence, and this is how it gets collected.

    Three properties, all of them load-bearing:

        production never waits   the row is queued, not written, and the
                                 queue is small enough to overflow rather
                                 than hold anything up
        nothing is executed      `shadow.predict` returns a frozen record with
                                 no runtime and no principal
        no transcript            a one-way fingerprint and routing metadata,
                                 never the sentence

    Wrapped whole, because a telemetry path may not cost the boss a reply
    under any circumstances at all.
    """
    global _LAST_SHADOW

    try:
        from friday import shadow as SH

        if not SH.enabled():
            return
        # A correction is worth more than an ordinary observation: it is the
        # only place real language says what the answer should have been.
        if SH.looks_like_a_correction(user_text):
            SH.record_correction(user_text, previous=_LAST_SHADOW)

        prediction = SH.observe(user_text, source="UNKNOWN", run_id=run_id or "")
        _LAST_SHADOW = ({
            "predicted_operation": prediction.predicted_operation,
            "predicted_target": prediction.predicted_target,
            "predicted_capability": prediction.predicted_capability,
        } if prediction is not None else {})
    except Exception:                                       # noqa: BLE001
        logger.exception("shadow observation failed; the turn is unaffected")

# Restored from the .pyc oracle: proven by a LOAD_CONST/STORE_NAME
# pair in the running system's bytecode, present in no source candidate.
ALREADY_READ = ('web_search', 'web_deep_research', 'web_answer', 'web_fetch', 'web_news', 'web_crawl')


def note_the_requirements(turn_ctx, user_text: str, project: str) -> None:
    """
    Record what the thing must do, and carry out any change he asked for.

    A change is handled first and reported back into the turn, because "we
    removed multiplayer" is something he needs to hear confirmed - and
    because the reply must not go on to discuss a requirement that was
    retired one sentence ago.

    The project has to be known. Requirements belong to something, and
    attaching them to a guess would put another project's spec in front of
    the next planning call.
    """
    from friday import capture

    try:
        current = project or _current_project()
        if not current:
            return
        change = capture.change_in(user_text)
        if change is not None:
            report = capture.apply_change(current, change)
            if report["superseded"] or report["dependent_decisions"]:
                turn_ctx.add_message(
                    role="system",
                    content=_change_brief(current, change, report))
            return
        capture.remember_the_requirements(current, user_text)
    except Exception:
        logger.exception(
            "could not record the requirements; the turn goes on")


def _change_brief(project: str, change, report: dict) -> str:
    """
    What changed, and - just as importantly - what did not.

    Naming the untouched decisions is the difference between a change and a
    reset. He removed one thing; the engine choice and the offline
    requirement still stand, and saying so stops him wondering.
    """
    lines = [f"He changed a requirement on {project}: {change.describe()}.",
             "Say this back to him in one short sentence."]
    if report["superseded"]:
        lines.append("Retired (kept in the record, not deleted): "
                     + "; ".join(report["superseded"][:4]))
    if report["added"]:
        lines.append("Replaced with: " + "; ".join(report["added"][:2]))
    if report["dependent_decisions"]:
        lines.append("These decisions rested on it and may need revisiting: "
                     + "; ".join(report["dependent_decisions"][:3]))
    if report["untouched_decisions"]:
        lines.append("These are unaffected and still stand: "
                     + "; ".join(report["untouched_decisions"][:3]))
    return "\n".join(lines)


def _current_project() -> str:
    """
    The project he is most likely talking about: the one touched last.

    A requirement stated in a follow-up sentence has no project name in it,
    and refusing to record it would mean only the first sentence of any
    conversation ever counted.
    """
    try:
        from friday.toolsets.memory import store

        rows = store().projects()
        return str(dict(rows[0])["name"]) if rows else ""
    except Exception:                                       # noqa: BLE001
        logger.exception("could not tell which project this is about")
        return ""


def remember_the_project(user_text: str) -> str:
    """
    Record the project, when the boss says he is starting one.

    Best-effort and silent about failure. A note that could not be written
    must never cost him the reply.
    """
    try:
        from friday import capture

        return capture.remember_the_project(user_text)
    except Exception:                                        # noqa: BLE001
        logger.exception("could not record the project; the turn goes on")
        return ""


async def check_what_he_asserted(turn_ctx, user_text: str, *,
                                 project: str = "") -> bool:
    """
    Read about a claim before agreeing with it.

    "I think Godot is probably the best choice, but verify that instead of
    just agreeing with me" is the request, and agreeing is the failure mode -
    the one this assistant exists to avoid. A statement about the world has an
    external truth and the plan is only as good as whether it holds.

    Only when he asks to be checked, and only for claims about the world. A
    preference has nothing to verify, and researching "I think I want it
    minimal" would be theatre that costs him four seconds.

    The verdict is the model's to reach from the reading. This puts the
    sources and an instruction in front of it; deciding VERIFIED here would be
    the agreeing, wearing a citation.
    """
    from friday import product as P

    try:
        if not P.wants_to_be_challenged(user_text):
            return False
        claims = [claim for claim in P.claims_in(user_text)
                  if claim.verdict != P.PREFERENCE]
        if not claims:
            return False
    except Exception:                                        # noqa: BLE001
        logger.exception("could not read the claims; answering normally")
        return False

    logger.info("product.checking claims=%d", len(claims))
    lines = []
    for claim in claims[:2]:
        try:
            checked = await P.verify(claim, about=user_text)
        except Exception:                                    # noqa: BLE001
            logger.exception("could not check a claim")
            continue
        if checked.sources and project:
            # What the sources said is a decision about the project, and
            # the next conversation should not have to read them again.
            # Best-effort: a note that could not be written must not cost
            # the reply.
            from friday import capture

            capture.remember_the_decision(project, checked)
        if checked.findings:
            lines.append(f"He said: {checked.claim}\n\n{checked.findings}")
        elif checked.sources:
            # Sources were found and none of them could be read: say so,
            # rather than agreeing on the strength of a page count.
            lines.append(f"He said: {checked.claim}\nNothing readable came back from "
                         f"{len(checked.sources)} source(s).")
    if not lines:
        return False

    try:
        turn_ctx.add_message(
            role="system",
            content="\n\n".join(lines) + "\n\n" + P.CHALLENGE)
        return True
    except Exception:                                        # noqa: BLE001
        logger.exception("could not put the findings in front of the model")
        return False


async def research_first(turn_ctx, user_text: str) -> bool:
    """
    Read the sources before answering, when the question has a date on it.

    "Use search when you need to" has been in the master prompt the whole
    time and does not work, because a model that believes it knows the answer
    does not feel a need. Meanwhile `web_deep_research` sits outside
    `CORE_TOOLS`, in a group of two, behind a discovery step - so a research
    question reached `web_search` at best, and a search is not research.

    The mode is chosen outside the model, which takes the judgement away from
    the thing least able to make it. FAST is most questions and costs nothing
    extra; only RESEARCH and DEEP pay for sources.

    This edits the chat context, which costs the preemptive-generation head
    start - the same trade `say_the_objective_owns_this` documents. Worth it
    here for the same reason: the alternative is a confident wrong answer.
    """
    from friday import answer as A

    try:
        plan = A.plan(user_text)
        if not plan.needs_sources:
            return False
    except Exception:                                        # noqa: BLE001
        logger.exception("could not classify the question; answering normally")
        return False

    logger.info("answer.%s reason=%s", plan.mode.lower(), plan.because)
    try:
        from friday.capability_runtime import CapabilityRuntime, CONVERSATION
        # The runtime may hand back a coroutine for an async capability.
        import inspect

        runtime = CapabilityRuntime(principal=CONVERSATION)
        result = runtime.execute(plan.capability, plan.arguments)
        if inspect.isawaitable(result):
            # An async capability returns its coroutine; a sync one has
            # already run. Either way the ActionResult is what matters, and
            # awaiting here keeps the turn's event loop in charge.
            result = await result
        if not result.may_claim_completion:
            logger.info("answer.no_sources status=%s", result.status)
            return False
        turn_ctx.add_message(role="system",
                             content=A.brief(plan, result.to_dict()))
        return True
    except Exception:                                        # noqa: BLE001
        logger.exception("research failed; answering without sources")
    return False


def capture_any_answers(user_text: str) -> None:
    """
    Record anything in this turn that settles a question Friday asked.

    Answers do not arrive labelled. They arrive as the next thing the boss
    says, possibly hours later, possibly after a restart - which is why the
    questions live in the database rather than on this object.

    A reply that talks around a question leaves it open. Having an answer
    invented for you is worse than being asked again, because nobody finds
    out.
    """
    from friday import requirements as REQ

    try:
        if not REQ.outstanding():
            return
        captured = REQ.capture_answers(user_text)
    except Exception:
        logger.exception("could not record the answer; the turn goes on")
        return

    for question_id, answer, confident in captured:
        logger.info(
            "requirements.recorded question=%s confident=%s answer=%r",
            question_id, confident, answer[:60])


def raise_what_is_still_open(turn_ctx, user_text: str) -> None:
    """
    Bring back a question the boss never answered, at the moment it matters.

    The loop asked well and recorded the answers, and a question that got no
    reply sat in `open_questions` forever - so Friday would build the
    lighthouse game without ever finding out which engine, having asked.

    It comes back when the build starts, not on a timer. That is the only
    moment the answer is actually needed, and a question raised on any other
    schedule is nagging.

    Work is not blocked on it. The boss said not to stop between tasks, and
    an assumption said out loud is not an invented answer - it is a declared
    default they can overrule. Refusing to start would be the interrogation
    this whole loop is written against.

    Only on admitted turns, where `say_the_objective_owns_this` has already
    edited the context and paid for the lost head start.
    """
    from friday import requirements as REQ

    try:
        project, questions = REQ.still_blocking(user_text)
    except Exception:                                        # noqa: BLE001
        logger.exception("could not check the open questions; the turn goes on")
        return
    if not questions:
        return

    lines = [f"You asked about {project} and never got an answer. The work has "
             f"started anyway - do not hold it up. Say, in one short sentence and "
             f"in your own voice, what you are assuming and that they can change it:"]
    # Two at most: a third open question read aloud is a list, and a list
    # is what the boss asked Friday never to do to him.
    for question in questions[:2]:
        lines.append(f"  - {question['question']}"
                     + (f"  (it decides: {question['why']})"
                        if question.get("why") else ""))
    try:
        turn_ctx.add_message(role="system", content="\n".join(lines))
        logger.info("requirements.reraised project=%s open=%d",
                    project, len(questions))
    except Exception:                                        # noqa: BLE001
        logger.exception("could not re-raise the open questions")


def ask_about_the_idea(turn_ctx, user_text: str) -> None:
    """
    When the boss describes something rather than asking for it, find out what
    would have to be known before it could be built.

    An idea is not an instruction and there is no plan in it. What is missing
    is not capabilities - it is decisions, and they only exist in the boss's
    head. So the questions go into the context and Friday asks them in its own
    voice, along with anything about the idea that looks weaker than they seem
    to think.

    Everything already decided about the project is dropped before it gets
    here, so a question settled in some conversation that has since been
    closed does not come back. That is the whole reason the decisions are
    durable.
    """
    from friday import requirements as REQ

    if not REQ.is_an_idea(user_text):
        return
    try:
        found = REQ.understand(user_text)
    except Exception:                                        # noqa: BLE001
        logger.exception("could not read the idea; answering it plainly")
        return
    if not found.questions and not found.concerns:
        return

    lines = [f"The boss is describing something they want built: "
             f"{found.subject or 'an idea'}. Before it can be planned, these need "
             f"answering. Ask them in your own voice, together, as one short "
             f"question - not a list read aloud, and not one at a time."]
    for question in found.questions[:3]:
        lines.append(f"  - {question.question}  (it decides: {question.why})")
    if found.concerns:
        lines.append("Say this too, briefly and without lecturing - they said "
                     "something that looks weaker than they think:")
        for concern in found.concerns[:2]:
            lines.append(f"  - they said: {concern.claim}")
            lines.append(f"    the problem: {concern.concern}")
    if found.assumptions:
        lines.append("These you have decided yourself; mention them only if asked: "
                     + "; ".join(f"{item.question} -> {item.proposed}"
                                 for item in found.assumptions[:3]))
    lines.append("Do not start building anything yet.")

    # Record them so an answer in a later turn lands on the right question,
    # and so a question nobody answers comes back when the build starts.
    try:
        REQ.ask(REQ.project_name(found.subject or user_text),
                found.questions[:3])
    except Exception:                                        # noqa: BLE001
        logger.exception("could not record the questions; asking anyway")

    try:
        turn_ctx.add_message(role="system", content="\n".join(lines))
        logger.info("requirements.asking questions=%d concerns=%d assumed=%d",
                    len(found.questions), len(found.concerns),
                    len(found.assumptions))
    except Exception:                                        # noqa: BLE001
        logger.exception("could not put the questions in front of the model")


def say_the_objective_owns_this(turn_ctx, run_id: str) -> None:
    """
    Tell the model the work has been taken off it.

    `_admitted_run_id` was set here and read nowhere, so admission was a
    fact about the database and a secret from the model. It saw the
    request, and did what it was asked.

    With a compound errand that is merely wasteful - the objective and the
    reply both open Paint. With a large one it is fatal. A dictated audit
    request was admitted as 205 durable tasks which the executor ran to
    completion in 22 seconds; the model, told nothing, spent the next
    seven minutes trying to perform the same audit through
    `search_capabilities` and `use_capability`, reaching the tool-step
    ceiling over and over: 22 UNEXPECTED_TOOL_CALL, 32 missing-signature
    400s, 11 total provider failures. The claim could not help - it had
    correctly released the moment the objective finished, long before the
    reply got that far.

    This does edit the chat context, which costs the head start that
    preemptive generation gives - LiveKit discards a reply it began before
    the context changed. That is a real cost and it is worth paying here:
    it applies only to turns that were admitted, and the alternative is
    the model doing the whole job a second time.
    """
    try:
        from friday.toolsets import objectives as OT

        tasks = OT.store().objective_tasks(run_id)
        turn_ctx.add_message(
            role="system",
            content=(
                f"That request has been admitted as durable objective "
                f"{run_id} with {len(tasks)} task(s), and the objective "
                f"executor is running it now. You must NOT carry out that "
                f"work yourself - not with domain tools, not through "
                f"use_capability. Acknowledge briefly in your own voice "
                f"and say it is under way. If the boss wants detail, "
                f"objective_status and objective_list report on it."))
        logger.info("objective.owns_turn run_id=%s tasks=%d",
                    run_id, len(tasks))
    except Exception:
        logger.exception(
            "could not tell the model the objective owns this")


def route_input(user_text: str) -> tuple[str, str]:
    """
    What this turn is, and what was done about it. Deterministic, no model.

    `classify_input` has always known the difference between "stop that" and
    "how's it going" and "what time is it". Nothing acted on it, so every one
    of them was just another sentence for the model to answer, and a durable
    run could be neither started, asked about, nor stopped by speaking.

    Returns `(intent, detail)` where detail is a run id, a short status line,
    or "". Never raises: a turn is worth more than any of this.
    """
    text = (user_text or "").strip()
    if not text:
        return "SIDE_CONVERSATION", ""

    from friday import objectives as O
    from friday.arbiter import classify_input
    from friday.toolsets import objectives as OT

    intent = classify_input(text)
    # Logged on every turn, at INFO, with the word count: the classification
    # is the one deterministic decision in the pipeline, and when a run is
    # started (or not started) for a sentence, this line says why. The
    # count is there because the classifier's compound-request threshold
    # is by words, and "it didn't start a run" is answered by reading it.
    logger.info("input.classified classification=%s words=%d",
                intent, len(text.split()))

    if intent == "NEW_OBJECTIVE":
        return "NEW_OBJECTIVE", admit_objective(text) or ""

    db = OT.store()
    active = O.active_run(db)
    if active is None:
        record_manual_continue_if_continuity_failed(text, db=db)
        return "SIDE_CONVERSATION", ""

    if intent == "CANCEL":
        O.cancel_run(db, run_id=active["run_id"], reason="the boss said stop",
                     executor_id=f"voice-{os.getpid()}")
        logger.info("objective %s cancelled by voice", active["run_id"])
        return "CANCEL", active["run_id"]

    if intent == "QUERY_ABOUT_RUN":
        # The spoken summary is built from the verified task rows, not
        # re-derived by the model.
        from friday.continuous import speak

        return "QUERY_ABOUT_RUN", speak(db, active["run_id"])

    if intent == "MODIFICATION":
        # A live graph edit. Only skipping a task is supported by voice so
        # far; the edit says what it did and why, and the model relays
        # that rather than re-planning.
        from friday import modification as MOD

        edit = MOD.skip_task(db, active["run_id"], text, said=text)
        logger.info("modification %s: %s", edit.outcome, edit.reason)
        return "MODIFICATION", edit.reason

    # Anything else while a run is active: the run still owns the turn.
    return intent, active["run_id"]

# Restored from the .pyc oracle. Each pattern is a LOAD_CONST string
# and each flag a LOAD_ATTR on `re` in the compiled module - primary
# evidence from the running system, not inference.
_MANUAL_CONTINUE = re.compile('^\\s*(?:continue|go\\s+on|keep\\s+going|resume)\\s*[.!?]*\\s*$', re.I)


def record_manual_continue_if_continuity_failed(user_text: str, *, db=None) -> bool:
    """Count the real P0 failure: a new turn bought after premature end.

    Objective resume calls already have their own control-plane event. This
    catches the shape the old metric missed: the objective is already terminal
    PARTIAL/FAILED, then the boss has to say only "continue" to make Friday do
    the remaining work conversationally.
    """
    if not _MANUAL_CONTINUE.match(user_text or ""):
        return False
    from friday import objectives as O
    from friday.toolsets import objectives as OT

    db = db or OT.store()
    for run in db.objective_runs(limit=10):
        if run["status"] not in (O.RUN_PARTIAL, O.RUN_FAILED):
            continue
        if not db.increment_objective_manual_continue(run["run_id"]):
            return False
        db.append_objective_event(
            run["run_id"], O.EVENT_MANUAL_CONTINUE_REQUIRED,
            detail={"text": (user_text or "")[:40],
                    "reason": "objective ended before accepted work was done"})
        logger.error("objective.continuity_regression run_id=%s manual_continue=1",
                     run["run_id"])
        return True
    return False


def admit_objective(user_text: str) -> str | None:
    """
    Turn a compound request into a durable run, before the model sees it.

    This is the root cause, closed. `classify_input` has existed and been
    tested since it was written, and nothing ever called it: the production
    seam ran `on_user_turn_completed` -> `prepare_turn` -> the learner, and
    returned. So a six-part request met a four-step tool budget
    (`max_tool_steps=3`, plus the forced final answer) and could not finish,
    and the boss had to say "Continue" to buy four more steps.

    A durable run is not bounded by a model turn. It is compiled, persisted
    and woken by the executor, so the work outlives the sentence that started
    it.

    Deliberately *not* done by letting the model choose `objective_start`:
    that made admission depend on the model noticing, mid-request, that it
    should stop working and delegate - which under real STT it did not do
    reliably. Returns the run id, or None when this was a sentence.
    """
    text = (user_text or "").strip()
    if not text:
        logger.info("objective.rejected reason=empty_turn")
        return None

    from friday import capabilities as caps
    from friday.arbiter import classify_input
    from friday.toolsets import objectives as OT

    # The arbiter decides what kind of turn this is. It already knew the
    # difference between a command, a question about a run and a compound
    # request; this is the seam where that knowledge finally reaches the
    # production path. Everything below is deterministic - no model call is
    # spent deciding whether to start a run.
    classification = classify_input(text)
    if classification != "NEW_OBJECTIVE":
        logger.info("objective.rejected reason=classified_as_%s", classification)
        return None

    # The semantic planner reads goals out of sentence structure. It is the
    # same reader the executor will use, so what is counted here is what
    # would be run.
    from friday import planner_model as SPM

    semantic = SPM.plan_objective(text)
    logger.info("objective.interpreted goals=%d constraints=%d reporting=%d "
                "discarded=%d unresolved=%d",
                len(semantic.goals), len(semantic.constraints),
                len(semantic.reporting), len(semantic.discarded),
                len(semantic.unresolved))
    if len(semantic.goals) < COMPOUND_TASKS:
        logger.info("objective.rejected reason=not_compound goals=%d needed=%d",
                    len(semantic.goals), COMPOUND_TASKS)
        return None

    from friday import contracts as c

    run = c.Run.create(text[:200], capability="objective_start")
    result = OT.objective_start(run, text)

    if result.status != c.SUCCEEDED:
        logger.info("objective not admitted: %s", result.error)
        return None

    run_id = (result.output or {}).get("run_id", "")
    logger.info("objective.admitted run_id=%s goals=%d", run_id,
                len(semantic.goals))
    # What the run owns is what the plan named. Logged so a later turn's
    # claim check ("is this capability already being handled?") can be
    # read back against the sentence that started the run, and so the
    # test that pins ownership has a line to assert on rather than a
    # database it has to open.
    claimed = sorted({goal.capability for goal in semantic.goals
                      if goal.capability})
    logger.info("objective.owns run_id=%s capabilities=%s", run_id, claimed)
    return run_id


def settle_power_requests() -> list:
    """
    Whatever Friday asked the machine to do and could not stay to see.

    Runs before anything else is registered, because a request left unsettled
    is the one state that outlives the process: Friday asks for a restart, the
    machine goes, and nothing it was going to write gets written. One call
    site, named here rather than left to whichever entry point runs first, so
    a second one cannot quietly skip it.
    """
    try:
        from friday import power_state
        from friday.store import DEFAULT_DB, Store

        store = Store(os.getenv("ADA_DB") or DEFAULT_DB)
        try:
            settled = power_state.reconcile(store)
        finally:
            store.close()
    except Exception:                                   # noqa: BLE001
        # Never block startup on this. Not knowing what became of a shutdown
        # is bad; refusing to come up because of it is worse.
        logger.exception("could not settle pending power requests")
        return []

    for row in settled:
        logger.info("power %s from %s: %s", row.action, row.run_id, row.detail)
    return settled


def _session_allows_speech(session) -> bool:
    """
    Whether the active interaction mode should RENDER audio for a
    background completion.

    Semantic delivery and voice rendering are separate decisions: the
    completion always becomes a Friday message; it is spoken only when
    the session actually has an audio output to speak through. Checked
    from the session's real output state, never assumed from how the
    original request arrived.
    """
    try:
        output = getattr(session, "output", None)
        audio = getattr(output, "audio", None)
        if audio is None:
            return False
        enabled = getattr(audio, "enabled", None)
        return True if enabled is None else bool(enabled)
    except Exception:                                        # noqa: BLE001
        return False


async def deliver_message(session, text: str) -> str:
    """
    One completion into the canonical session surfaces. Returns how.

    Audio-capable session: session.say - synthesizes speech AND adds the
    text to the chat context, so voice and transcript stay one message.
    Text-only session: the same say() with audio suppressed where the
    installed livekit-agents supports it, else the text lands in the chat
    context alone. One semantic result either way; only rendering differs.
    """
    if _session_allows_speech(session):
        await session.say(text, allow_interruptions=True)
        return "say+audio"
    try:
        # Text-only path: still say(), which adds to the chat context and
        # transcription streams; with no audio output attached there is
        # nothing to synthesize through, so this is the text route.
        await session.say(text, allow_interruptions=True,
                          add_to_chat_ctx=True)
        return "say+text"
    except TypeError:
        await session.say(text, allow_interruptions=True)
        return "say+text"


async def drain_hermes_deliveries(session, log=None) -> int:
    """
    One delivery pass: claim each PENDING Hermes completion, deliver it
    into the session, mark it delivered. Returns how many were delivered.

    Module-level and session-parameterised so the production entrypoint
    loop and the golden gates run the IDENTICAL code - a gate that drains
    deliveries through a private copy would prove nothing about production.
    """
    from friday import hermes_bridge as hb

    if log is None:
        log = hb.WorkRunLog()
    delivered = 0
    pending = await asyncio.to_thread(log.pending_deliveries)
    for delivery in pending:
        if not log.claim_delivery(delivery["delivery_id"]):
            continue
        try:
            via = await deliver_message(session, delivery["message"])
            log.mark_delivered(delivery["delivery_id"], via=via)
            delivered += 1
            logger.info("hermes.delivered %s run=%s via=%s",
                        delivery["delivery_id"], delivery["work_run_id"],
                        via)
        except Exception:                                    # noqa: BLE001
            # The claim is released, not marked failed: the message never
            # reached the boss, so it must stay deliverable. A crash mid-say
            # leaves DELIVERING, which is the accepted at-most-once trade.
            logger.exception("hermes delivery failed; released")
            log.release_delivery(delivery["delivery_id"])
    return delivered


async def drain_objective_deliveries(session, store=None) -> int:
    """
    One delivery pass for FINISHED OBJECTIVES - the seam the RC1 regression
    was missing.

    A terminal ObjectiveRun was durable but silent: the executor settled the
    run in its own background task, and nothing carried that fact into the
    live session. The boss saw work stop and typed `continue`, which bought a
    fresh model turn that re-derived the objective from conversational prose.

    Same shape as `drain_hermes_deliveries` deliberately: claim atomically,
    say the already-grounded text, mark delivered. `say` rather than
    `generate_reply` because the summary is built from verified task rows -
    re-reasoning it would spend tokens to risk distorting evidence, and the
    token policy forbids a model call to remember to continue.
    """
    if store is None:
        store = objective_cli._db()
    delivered = 0
    pending = await asyncio.to_thread(store.pending_objective_deliveries)
    for delivery in pending:
        if not store.claim_objective_delivery(delivery["delivery_id"]):
            continue
        try:
            via = await deliver_message(session, delivery["message"])
            store.mark_objective_delivered(delivery["delivery_id"], via=via)
            delivered += 1
            logger.info("objective.delivered %s run=%s via=%s",
                        delivery["delivery_id"], delivery["run_id"], via)
        except Exception:                                    # noqa: BLE001
            logger.exception("objective delivery failed; released")
            store.release_objective_delivery(delivery["delivery_id"])
    return delivered


async def entrypoint(ctx: JobContext) -> None:
    settle_power_requests()
    config = session_config()
    logger.info(
        "FRIDAY online - room: %s | STT=%s | LLM=%s/%s | TTS=%s",
        ctx.room.name,
        config["stt_provider"],
        config["llm_backend"],
        config["llm_role"],
        config["tts_provider"],
    )
    logger.info("MCP toolset %r -> %s", CLOUD_TOOLSET_ID, mcp_sse_url())

    session = AgentSession(turn_handling=config["turn_handling"])
    agent = FridayAgent(
        stt=providers.build_stt(config["stt_provider"]),
        llm=providers.build_resilient_llm(config["llm_backend"], config["llm_role"]),
        tts=providers.build_tts(config["tts_provider"], speed=config["tts_speed"]),
    )

    await session.start(
        agent=agent, room=ctx.room, room_options=room_options_for(agent)
    )
    # After start: the guard listens for a turn the model failed to finish and
    # speaks for it, but only when a tool actually succeeded first.
    guard = resilience.TurnGuard()
    guard.attach(session)

    # The durable run driver lives in this process too, so runs whose wake
    # is due are picked up without a separate worker.
    engine = agent.start_objective_engine()
    ctx.add_shutdown_callback(engine.stop)

    # Terminal WorkRuns and finished objectives are delivered into the live
    # session by this loop. It is the one place the executor's durable
    # rows become speech, and it runs the same module-level drain functions
    # the golden gates run, so a gate that passes proves something about
    # production.
    #
    # The startup sweep expires deliveries older than the TTL: a message
    # about a job that finished yesterday, spoken this morning, is the F5
    # ghost - and after a crash the rows are exactly that old.
    async def deliver_hermes_completions() -> None:
        from friday import hermes_bridge as hb

        log = hb.WorkRunLog()
        try:
            swept = await asyncio.to_thread(log.sweep_undelivered)
            if swept:
                logger.info("hermes.delivery sweep found %d pending", swept)
        except Exception:                                    # noqa: BLE001
            logger.exception("hermes delivery sweep failed")
        while True:
            try:
                await drain_hermes_deliveries(session, log)
                # Finished objectives ride the same loop: same claim,
                # same say, same mark - the seam the RC1 regression was
                # missing.
                await drain_objective_deliveries(session)
            except asyncio.CancelledError:
                raise
            except Exception:                                # noqa: BLE001
                logger.exception("hermes delivery loop error")
            await asyncio.sleep(5)

    delivery_task = asyncio.create_task(deliver_hermes_completions())

    async def stop_delivery() -> None:
        delivery_task.cancel()

    ctx.add_shutdown_callback(stop_delivery)

    async def report_resilience() -> None:
        # The empty-completion rate is guessed at until somebody counts it.
        logger.info("resilience: %s", guard.describe())

    ctx.add_shutdown_callback(report_resilience)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


#: Tuned for one person's desktop, not a server pool.
#
# `friday_voice start` runs in PRODUCTION mode, where LiveKit defaults to
# num_idle_processes = min(cpu_count, 4) and load_threshold = 0.7. On a
# 16-core laptop that pre-spawned four Python processes - each importing
# livekit, the plugins and torch - and then refused jobs whenever CPU went
# over 70%, which on a machine already running a browser and an IDE is most
# of the time. The log filled with "worker is at full capacity, marking as
# unavailable" and sessions stalled.
#
# Those defaults are right for a fleet sharing work across many machines.
# Here there is one machine and one user, and a worker that declines to answer
# because the laptop is busy is worse than a slightly slower answer.
WORKER_IDLE_PROCESSES = int(os.getenv("ADA_IDLE_PROCESSES", "1"))
#: Must stay <= 1.0; LiveKit rejects anything higher outside dev mode.
WORKER_LOAD_THRESHOLD = float(os.getenv("ADA_LOAD_THRESHOLD", "0.99"))


def worker_options() -> WorkerOptions:
    return WorkerOptions(
        entrypoint_fnc=entrypoint,
        num_idle_processes=WORKER_IDLE_PROCESSES,
        load_threshold=WORKER_LOAD_THRESHOLD,
    )


def main():
    cli.run_app(worker_options())


def dev():
    """Wrapper to run the agent in dev mode automatically."""
    import sys

    if len(sys.argv) == 1:
        sys.argv.append("dev")
    main()


if __name__ == "__main__":
    main()
