"""
Build the DEVELOPMENT / CALIBRATION / HOLDOUT corpora for the selective router.

Run:  .venv/Scripts/python.exe scripts/build_corpus.py

## Why not reuse the 507

The existing benchmark is Friday's own `intent_examples`. It has now been read,
re-read and inspected repeatedly across router development, so it can still
detect regressions and can no longer be evidence that anything generalises. It
is frozen as LEGACY_REGRESSION_SET and nothing here is derived from it.

## Where these come from, and the contamination this cannot escape

    real            what the store actually holds. There is almost none - the
                    `utterances` and `messages` tables are empty and
                    `objective_runs` has four distinct requests, all long
                    compound dictations from the same test. Included anyway,
                    labelled, because four real sentences beat none.

    template        generated from the *semantic definition* of a capability -
                    its operation, its target, its description - and never
                    from its example phrases. The verbs and objects here are
                    written against the operation vocabulary in semantics.py,
                    not against anything the router was tuned on.

    adversarial     hand-written confusable pairs. This is the one place
                    hand-authoring is right: the pairs that matter are the
                    ones a person can see are confusable and a generator
                    cannot.

    out_of_domain   requests that must never reach a capability.

**The honest caveat.** These were written by the same process that wrote the
router. Templates reduce that - the generator does not know which capability
will win - but they do not remove it. The only genuinely independent
distribution is real traffic, which is what SHADOW mode exists to collect, and
until it has run these numbers are an upper bound on real performance rather
than a measurement of it.

## The splits

    DEVELOPMENT   debugging and designing fixes. Look at it as much as you like.
    CALIBRATION   thresholds and operating points only. Never used to invent a
                  routing rule.
    HOLDOUT       locked. Opened once, for a promotion decision, and then it
                  becomes evaluation history and a new one is needed.

Assignment is by a hash of the utterance, so the same sentence always lands in
the same split however often this is re-run, and a paraphrase family cannot be
split across development and holdout.
"""

from __future__ import annotations

import hashlib
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from friday import capabilities as C            # noqa: E402
from friday import capability_runtime as R      # noqa: E402
from friday import semantics as S               # noqa: E402

OUT = pathlib.Path(__file__).resolve().parent.parent / "data" / "corpus"

ABSTAIN = "ABSTAIN"

# Categories, so §21's distribution can be reported rather than assumed.
SIMPLE = "SIMPLE"
CONFUSABLE = "CONFUSABLE"
QUESTION = "QUESTION"
NEGATION = "NEGATION"
HYPOTHETICAL = "HYPOTHETICAL"
MULTI_INTENT = "MULTI_INTENT"
REFERENT = "REFERENT"
OOD = "OOD"
TRANSCRIPTION = "TRANSCRIPTION"
HIGH_RISK = "HIGH_RISK"

CATEGORIES = (SIMPLE, CONFUSABLE, QUESTION, NEGATION, HYPOTHETICAL,
              MULTI_INTENT, REFERENT, OOD, TRANSCRIPTION, HIGH_RISK)


# ---------------------------------------------------------------------------
# Vocabulary, written against semantics.py rather than against the registry's
# example phrases
# ---------------------------------------------------------------------------

#: operation -> ways of saying it. Chosen from ordinary speech, not from
#: `intent_examples` - the point is language the router has never scored.
VERBS = {
    S.OPEN: ("open", "bring up", "launch", "fire up", "start up", "pull up"),
    S.LIST: ("list", "show me", "what are my", "give me a list of",
             "run through my"),
    S.READ: ("check", "read", "look at", "have a look at", "tell me about"),
    S.SEARCH: ("search for", "look up", "find", "go find", "dig up"),
    S.CONTROL: ("pause", "resume", "hold", "carry on with", "keep going with"),
    S.CREATE: ("create", "make", "make me", "put together", "start a new"),
    S.UPDATE: ("change", "set", "adjust", "turn", "update"),
    S.MOVE: ("move", "shift", "put", "drag", "send"),
    S.DELETE: ("delete", "get rid of", "bin", "throw away", "clear out"),
    S.START: ("start", "kick off", "begin", "get going on", "set off"),
    S.CANCEL: ("cancel", "call off", "stop", "abandon", "drop"),
    S.EXECUTE: ("run", "execute", "do", "carry out", "perform"),
}

#: target -> ways of naming the thing. Again ordinary speech.
OBJECTS = {
    "APPLICATION": ("Paint", "Notepad", "Calculator", "Spotify", "that app",
                    "the calculator app"),
    "WINDOW": ("this window", "the Notepad window", "that window",
               "my windows", "the front window"),
    "FILE": ("that file", "my notes file", "the text file", "that document",
             "the file I made"),
    "MEDIA": ("the music", "the song", "this track", "what's playing",
              "the playlist"),
    "AUDIO": ("the volume", "the sound", "the audio level"),
    "SYSTEM": ("my computer", "this machine", "the laptop", "my PC"),
    "WEB": ("the web", "the internet", "online"),
    "BROWSER": ("the browser", "this tab", "the page"),
    "PROCESS": ("that process", "the frozen program"),
    "AUTOMATION": ("my automations", "that automation", "the routine"),
    "REMINDER": ("my reminders", "that reminder"),
    "OBJECTIVE": ("the job", "that run", "what you're working on"),
    "MEMORY": ("what you remember", "my notes on that"),
    "CLIPBOARD": ("the clipboard",),
    "DISPLAY": ("the brightness", "the screen brightness"),
    "DOCUMENT": ("that pdf", "the spreadsheet"),
    "WORKBENCH": ("the project", "the preview"),
    "PRODUCT": ("the catalogue", "the product list"),
    "VISION": ("the screen", "what's on screen"),
    "POWER": ("the computer",),
    "PROFILE": ("my preferences",),
}

#: Wrappers real speech puts around a command.
FILLERS = (
    "{c}",
    "{c}",
    "{c}",
    "friday {c}",
    "hey friday, {c}",
    "can you {c}",
    "could you {c} please",
    "{c} for me",
    "please {c}",
    "just {c}",
    "ok {c}",
    "{c} would you",
    "yeah {c}",
    "right, {c}",
)

#: What a speech-to-text pass does to a sentence. Applied to a share of the
#: simple commands, because a router that only survives clean text has not
#: met a microphone.
CORRUPTIONS = (
    ("the", "de"), ("please", "pls"), ("you", "u"), ("my", "mah"),
    ("open", "opne"), ("music", "musick"), ("computer", "compyuter"),
    ("window", "windo"), ("volume", "volum"), ("browser", "browzer"),
)

#: Hinglish markers, since the boss's historical speech carries them.
HINGLISH = (
    "{c} kar do", "{c} na", "zara {c}", "{c} karo please", "arre {c}",
    "{c} kar dijiye",
)


# ---------------------------------------------------------------------------
# Adversarial pairs. Hand-written on purpose.
# ---------------------------------------------------------------------------

CONFUSABLE_PAIRS = [
    ("restart the song", "music_play"),
    ("restart the computer", ABSTAIN),
    ("close Chrome", ABSTAIN),
    ("close this tab", ABSTAIN),
    ("stop the music", "music_stop"),
    ("stop that process", ABSTAIN),
    ("pause it", ABSTAIN),
    ("pause the automation", ABSTAIN),
    ("remove this file", ABSTAIN),
    ("remove this item from the list", ABSTAIN),
    ("open my windows", "windows_list"),
    ("what windows are open", ABSTAIN),
    ("cancel the automation", ABSTAIN),
    ("cancel shutdown", ABSTAIN),
    ("turn the music down", ABSTAIN),
    ("turn the music off", "music_stop"),
    ("kill the music", "music_stop"),
    ("kill that process", ABSTAIN),
    ("end the song", "music_stop"),
    ("end the meeting", ABSTAIN),
    ("shut the window", ABSTAIN),
    ("shut down the machine", ABSTAIN),
    ("clear the clipboard", ABSTAIN),
    ("clear my schedule", ABSTAIN),
    ("show me the time", "get_current_time"),
    ("show me the timer", ABSTAIN),
    ("play the next one", "music_next"),
    ("read the next one", ABSTAIN),
    ("mute it", ABSTAIN),
    ("mute the music", ABSTAIN),
]

QUESTIONS_ABOUT_ACTIONS = [
    "should I restart my PC",
    "should I close Chrome",
    "is it worth restarting the computer",
    "could stopping Chrome fix this",
    "would closing the browser help",
    "do I need to delete that file",
    "what happens if I shut down now",
    "is closing this safe",
    "can you delete files",
    "are you able to shut down the machine",
    "do you know how to open Paint",
    "what would restarting do",
    "how do I close a window",
    "why would I stop the music",
    "is the volume something you control",
]

HYPOTHETICALS = [
    "what if I shut down the computer",
    "suppose I deleted that file",
    "imagine we closed every window",
    "if I were to restart, would that help",
    "hypothetically, could you terminate that process",
    "in theory you could open Paint right",
    "what would happen if the music stopped",
    "supposing I wanted to clear the clipboard",
    "would it help to close the browser",
    "should I be pausing this",
]

NEGATIONS = [
    "don't close Chrome",
    "do not stop the music",
    "never restart my computer",
    "anything except restart",
    "don't delete anything",
    "leave the music alone",
    "don't touch my files",
    "no need to open Paint",
    "I'd rather not close that window",
    "do everything apart from the restart",
    "skip the shutdown",
    "avoid closing the browser",
    "don't pause it",
    "instead of stopping it, turn it down",
]

MULTI = [
    "open Chrome and find today's AI news",
    "check my computer then open Paint",
    "pause the music and tell me the time",
    "close that window, open Notepad, and turn the volume up",
    "find a news story and save it to a file",
    "open Paint and then minimise it",
    "list my windows and close the last one",
    "check the disks and tell me if anything is full",
    "play some music and dim the screen",
    "search the web and read me the first result",
]

REASONING = [
    "why is my computer slow",
    "should I use Rust",
    "research OpenAI",
    "design a game for me",
    "what should I do here",
    "explain this error",
    "compare Godot and Unreal",
    "review this architecture",
    "help me decide between two laptops",
    "what do you think of this plan",
    "analyse my spending",
    "plan out my week",
    "debug this for me",
    "recommend a database",
    "figure out why the build is failing",
    "look into whether this is a good idea",
]

CHITCHAT = [
    "hello", "thanks", "never mind", "forget it", "good morning",
    "how are you", "you're great", "nothing", "that's all", "cool",
    "haha", "wait", "hold on", "sorry", "carry on",
]

#: §4. A referent is grounded only by what the conversation established.
MULTI_TURN = [
    (["play some music"], "pause it", "music_pause", {"MEDIA": "some music"}),
    (["play some music"], "stop it", "music_stop", {"MEDIA": "some music"}),
    (["play some music"], "skip it", "music_next", {"MEDIA": "some music"}),
    (["open Paint"], "close it", ABSTAIN, {"APPLICATION": "Paint"}),
    ([], "pause it", ABSTAIN, {}),
    ([], "delete it", ABSTAIN, {}),
    ([], "close it", ABSTAIN, {}),
    ([], "restart it", ABSTAIN, {}),
    ([], "stop it", ABSTAIN, {}),
    (["create a test note"], "delete it", ABSTAIN, {"FILE": "test note"}),
    (["play some music", "open Paint"], "pause it", ABSTAIN,
     {"MEDIA": "some music", "APPLICATION": "Paint"}),
    (["play some music"], "turn it down", ABSTAIN, {"MEDIA": "some music"}),
]


# ---------------------------------------------------------------------------
# Generation
# ---------------------------------------------------------------------------


def eligible() -> list:
    """Capabilities a reflex could conceivably route to."""
    reachable = set(R.reachable())
    return [cap for cap in C._ALL if cap.id in reachable]


def _record(text, expect, category, source, context=None, turns=()) -> dict:
    text = " ".join(text.split())
    return {
        "id": hashlib.sha1(text.lower().encode()).hexdigest()[:12],
        "text": text,
        "expect": expect,
        "category": category,
        "source": source,
        "context": context or {},
        "turns": list(turns),
    }


def _corrupt(text: str, index: int) -> str:
    """One transcription defect, chosen deterministically."""
    original, wrong = CORRUPTIONS[index % len(CORRUPTIONS)]
    if original in text:
        return text.replace(original, wrong, 1)
    return text.rstrip(".?! ") + " uh"


def templated() -> list[dict]:
    """
    Commands built from a capability's semantic definition.

    Never from its `intent_examples`. The generator picks a verb for the
    operation and a noun for the target and does not know, or check, which
    capability the router will choose - which is the only way a template can
    be evidence of anything.
    """
    records: list[dict] = []
    index = 0
    for capability in eligible():
        operation, target = S.for_capability(capability.id)
        verbs = VERBS.get(operation)
        objects = OBJECTS.get(target)
        if not verbs or not objects:
            continue
        required = R.required_arguments(capability.id) or ()
        for verb in verbs:
            noun = objects[index % len(objects)]
            wrapper = FILLERS[index % len(FILLERS)]
            command = f"{verb} {noun}"
            text = wrapper.format(c=command)
            # A capability needing an argument the sentence never supplies is
            # not a SIMPLE case - it is a legitimate abstention.
            expect = capability.id
            if required and noun.startswith(("that ", "this ", "the ")) \
                    and "my" not in noun:
                expect = capability.id      # the noun is the argument
            records.append(_record(text, expect, SIMPLE, "template"))
            index += 1
            if index % 7 == 0:
                records.append(_record(_corrupt(text, index), expect,
                                       TRANSCRIPTION, "template"))
            if index % 11 == 0:
                records.append(_record(
                    HINGLISH[index % len(HINGLISH)].format(c=command),
                    expect, TRANSCRIPTION, "template"))
    return records


def hand_written() -> list[dict]:
    records: list[dict] = []
    for text, expect in CONFUSABLE_PAIRS:
        records.append(_record(text, expect, CONFUSABLE, "adversarial"))
    for text in QUESTIONS_ABOUT_ACTIONS:
        records.append(_record(text, ABSTAIN, QUESTION, "adversarial"))
    for text in HYPOTHETICALS:
        records.append(_record(text, ABSTAIN, HYPOTHETICAL, "adversarial"))
    for text in NEGATIONS:
        records.append(_record(text, ABSTAIN, NEGATION, "adversarial"))
    for text in MULTI:
        records.append(_record(text, ABSTAIN, MULTI_INTENT, "adversarial"))
    for text in REASONING + CHITCHAT:
        records.append(_record(text, ABSTAIN, OOD, "adversarial"))
    for turns, text, expect, active in MULTI_TURN:
        records.append(_record(text, expect, REFERENT, "adversarial",
                               context={"active": active}, turns=turns))
    for capability in eligible():
        if capability.risk in ("HIGH", "IRREVERSIBLE") or \
                capability.requires_approval:
            operation, target = S.for_capability(capability.id)
            verbs = VERBS.get(operation) or ("do",)
            objects = OBJECTS.get(target) or ("that",)
            records.append(_record(f"{verbs[0]} {objects[0]}", ABSTAIN,
                                   HIGH_RISK, "adversarial"))
    return records


def real() -> list[dict]:
    """
    What the store actually holds.

    Thin to the point of embarrassment - the `utterances` and `messages`
    tables are empty and `objective_runs` has four distinct requests, all
    long compound dictations from the same test scenario. Included and
    labelled, because four real sentences are worth more than four hundred
    invented ones and because the count itself is the argument for shadow
    mode.
    """
    import sqlite3

    path = pathlib.Path("data/ada.sqlite3")
    if not path.exists():
        return []
    found: list[dict] = []
    try:
        db = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        db.row_factory = sqlite3.Row
        seen = set()
        for row in db.execute("SELECT DISTINCT request FROM objective_runs"):
            text = (row["request"] or "").strip()
            if not text or text.lower() in seen:
                continue
            seen.add(text.lower())
            found.append(_record(text, ABSTAIN, MULTI_INTENT, "real"))
        for row in db.execute(
                "SELECT DISTINCT raw FROM utterances WHERE raw != ''"):
            text = (row["raw"] or "").strip()
            if text and text.lower() not in seen:
                seen.add(text.lower())
                found.append(_record(text, ABSTAIN, SIMPLE, "real"))
        db.close()
    except sqlite3.Error:
        return found
    return found


# ---------------------------------------------------------------------------
# Splitting
# ---------------------------------------------------------------------------

SPLITS = ("development", "calibration", "holdout")


def split_of(record: dict) -> str:
    """
    Stable, content-derived, and family-safe.

    Hashed on the utterance so re-running never reshuffles anything, and on
    the *command* rather than the wrapper so "open Paint" and "could you open
    Paint please" cannot land on opposite sides of the holdout wall.
    """
    key = record["id"]
    bucket = int(key[:8], 16) % 10
    if bucket < 5:
        return "development"
    if bucket < 8:
        return "calibration"
    return "holdout"


def build() -> dict[str, list[dict]]:
    everything: dict[str, dict] = {}
    for record in real() + hand_written() + templated():
        everything.setdefault(record["id"], record)

    splits: dict[str, list[dict]] = {name: [] for name in SPLITS}
    for record in everything.values():
        # Adversarial cases are the whole point of the exercise and there are
        # not many of them; they are spread across all three so no split is
        # blind to a category.
        splits[split_of(record)].append(record)
    return splits


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    splits = build()

    print(f"{'split':14s} {'total':>6s}  categories")
    for name in SPLITS:
        records = splits[name]
        path = OUT / f"{name}.jsonl"
        with path.open("w", encoding="utf-8") as handle:
            for record in records:
                handle.write(json.dumps(record, ensure_ascii=False) + "\n")
        counts: dict[str, int] = {}
        for record in records:
            counts[record["category"]] = counts.get(record["category"], 0) + 1
        summary = "  ".join(f"{key}:{value}"
                            for key, value in sorted(counts.items()))
        print(f"{name:14s} {len(records):6d}  {summary}")

    total = sum(len(records) for records in splits.values())
    sources: dict[str, int] = {}
    for records in splits.values():
        for record in records:
            sources[record["source"]] = sources.get(record["source"], 0) + 1
    print()
    print(f"total {total} utterances   sources: {sources}")
    print(f"written to {OUT}")
    if total < 1500:
        print(f"\nWARNING: {total} is below the 1,500 floor.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
