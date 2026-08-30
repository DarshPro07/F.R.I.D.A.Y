
#!/usr/bin/env python3
"""
Can Friday be *asked* for what it can do?

Three different questions, and Friday needs all three answered:

    IMPLEMENTED    the function works
    TOOL_VERIFIED  calling it directly does the right thing
    AGENT_ROUTED   asked in English, the model finds it and picks it over the
                   plausible alternatives
    JOURNEY_VERIFIED  and the whole multi-turn errand holds together

The product tools scored 24/24 on the second and 2/17 on the third, on the
same day, with no code between them. That gap is what this measures, and it is
the gate every migrated donor capability has to pass before the next batch
starts - because tool search degrades as tools are added, and the failure mode
is silent: a capability that exists, works, and never gets chosen.

    python scripts/golden_agentability.py                # every domain
    python scripts/golden_agentability.py --domain music
    python scripts/golden_agentability.py --runs 4       # routing reliability

Half of what this measures is a language model choosing a tool, so one run is
an anecdote. `--runs N` reports per-utterance rates instead of a verdict.

The deep multi-turn journeys live in their own gates - golden_product_agent.py
is the worked example. This one is broad and shallow on purpose: it is what
you run after every migration batch, and it has to stay cheap enough that you
actually do.
"""

from __future__ import annotations

import asyncio

import json

import subprocess

import sys

import time

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv  # noqa: E402


load_dotenv(ROOT / ".env")

from friday import capabilities as C  # noqa: E402

from friday import health  # noqa: E402

from friday import capability_router as CR  # noqa: E402

#: domain -> (what the boss says, what must be reached).
#:
#: `wanted` is a tuple because more than one answer is often right: "play
#: something upbeat" is `music_play` or `music_play_mood`, and insisting on
#: one of them would be testing this file's opinion rather than Friday's
#: routing. `forbidden` is where the sharpness lives - it names the plausible
#: wrong answer that was actually observed or is actually likely.
UTTERANCES: dict[str, tuple[tuple[str, tuple[str, ...], tuple[str, ...]], ...]] = {
    'products': (
        ('process the catalogue at {catalogue}', ('product_process',), ('files_read',)),
        (
            'which products had problems in that catalogue job',
            ('product_result',),
            ('product_process',),
        ),
        (
            'retry only the network failures on that job',
            ('product_retry',),
            ('product_process',),
        ),
        (
            'how did that catalogue job finish',
            ('product_status', 'product_runs'),
            ('product_process',),
        ),
    ),
    'documents': (
        ('what does the pdf at {pdf} say', ('documents_extract',), ()),
        (
            'how many pages is the pdf at {pdf}',
            ('documents_inspect',),
            ('documents_extract',),
        ),
        (
            'what sheets are in the spreadsheet at {workbook}',
            ('documents_inspect', 'documents_extract'),
            ('files_read',),
        ),
    ),
    'hardware': (
        ('how much battery is left', ('system_battery',), ('system_resource_usage',)),
        (
            'which of my drives is running out of space',
            ('system_disks',),
            ('system_resource_usage',),
        ),
        (
            'how many monitors am I using',
            ('system_displays',),
            ('vision_inspect_screen',),
        ),
    ),
    'windows': (
        (
            'what windows do I have open',
            ('windows_list',),
            ('vision_inspect_screen', 'apps_list_known'),
        ),
        (
            'put the notepad window on the left half of the screen',
            ('windows_arrange',),
            ('apps_focus',),
        ),
        (
            'minimize that notepad window out of my way',
            ('windows_minimize',),
            ('apps_close', 'process_close'),
        ),
    ),
    'audio': (
        (
            'lower spotify to 30 percent',
            ('audio_session_volume',),
            ('audio_master_volume', 'volume_set'),
        ),
        (
            'what apps are playing sound right now',
            ('audio_sessions',),
            ('music_current',),
        ),
        (
            'set the system volume to 30 percent',
            ('audio_master_volume', 'volume_set'),
            ('audio_session_volume',),
        ),
    ),
    'brightness': (
        ('how bright is my screen', ('brightness_get',), ('vision_inspect_screen',)),
        ('dim the screen a bit', ('brightness_set',), ('audio_master_volume',)),
    ),
    'files': (
        ('what files are in my workspace', ('files_list', 'files_roots'), ()),
        ('search my project for the word reactor', ('files_search',), ('web_search',)),
    ),
    'music': (
        (
            "what's playing right now",
            ('music_current', 'spotify_current'),
            ('music_play',),
        ),
        ('play something by daft punk', ('music_play', 'music_search'), ()),
        ('skip to the next track', ('music_next', 'spotify_next'), ('music_play',)),
    ),
    'automations': (
        (
            'every morning at seven, without asking me, check the news and save me a summary',
            ('automations_create',),
            ('reminders_create',),
        ),
        (
            'how did that automation go this morning',
            ('automations_history', 'automations_list'),
            ('automations_create',),
        ),
        (
            'delete the automation you just created',
            ('automations_delete',),
            ('automations_run',),
        ),
    ),
    'vision': (
        (
            'take a look at my screen and describe what you can see',
            ('vision_inspect_screen', 'vision_screen_capture'),
            (),
        ),
        (
            'what am I holding up to the camera',
            ('vision_inspect_camera', 'vision_camera_frame'),
            (),
        ),
    ),
    'research': (
        (
            'research how people build long term memory for AI agents',
            ('web_deep_research',),
            (),
        ),
        (
            'read https://en.wikipedia.org/wiki/Web_crawler and https://en.wikipedia.org/wiki/Web_scraping properly and compare them',
            ('web_crawl', 'web_deep_research'),
            (),
        ),
    ),
    'youtube': (
        (
            'how many subscribers does the Techno Gamerz youtube channel have',
            ('youtube_channel_details', 'youtube_find_channel', 'web_answer'),
            (),
        ),
        (
            "list the Techno Gamerz channel's last five uploads with the view count for each one",
            ('youtube_recent_videos', 'youtube_find_channel'),
            (),
        ),
    ),
    'profile': (
        (
            'what do you know about me',
            ('profile_get', 'memory_recall', 'memory_search'),
            (),
        ),
    ),
    'runs': (
        ('how is the long-running objective going', ('run_status', 'run_list'), ()),
        ('pause the active long-running objective', ('run_pause',), ('run_cancel',)),
        ('resume the paused long-running objective', ('run_resume',), ('run_cancel',)),
        (
            'cancel the active long-running objective',
            ('run_cancel',),
            ('run_pause', 'run_resume'),
        ),
    ),
    'processes': (
        (
            'ask the process named {missing_close_process} to close',
            ('process_close', 'apps_close'),
            ('process_terminate', 'power_shutdown', 'power_restart'),
        ),
        (
            'force close the process named {missing_force_process}',
            ('process_terminate',),
            ('process_close', 'apps_close', 'power_shutdown', 'power_restart'),
        ),
    ),
    'power': (
        (
            'lock this computer',
            ('power_lock',),
            ('power_sleep', 'power_shutdown', 'process_terminate'),
        ),
        (
            'put this computer to sleep',
            ('power_sleep',),
            ('music_pause', 'power_hibernate', 'power_shutdown'),
        ),
        (
            'hibernate this computer',
            ('power_hibernate',),
            ('power_sleep', 'power_shutdown'),
        ),
        (
            'shut down this computer',
            ('power_shutdown',),
            ('music_stop', 'process_terminate', 'power_restart'),
        ),
        ('restart this computer', ('power_restart',), ('music_play', 'power_shutdown')),
        (
            'force this computer to shut down',
            ('power_shutdown',),
            ('process_terminate', 'power_restart'),
        ),
        (
            'force this computer to restart',
            ('power_restart',),
            ('process_terminate', 'power_shutdown'),
        ),
    ),
}

#: The turn before, for utterances that only make sense as follow-ups. The
#: router ranks differently once work has been started, and testing a
#: follow-up from a cold start measures the wrong thing.
AFTER: dict[str, str] = {
    "which products had problems in that catalogue job": "product_process",
    "retry only the network failures on that job": "product_process",
    "how did that catalogue job finish": "product_process",
    "how did that automation go this morning": "automations_create",
    "delete the automation you just created": "automations_create",
}

CATALOGUE = ROOT / "data" / "gate" / "agent_catalogue.csv"

SAMPLE_PDF = ROOT / "data" / "gate" / "sample.pdf"

SAMPLE_XLSX = ROOT / "data" / "gate" / "sample.xlsx"

MCP_LOG = ROOT / 'logs' / 'golden_agentability_mcp.log'

MISSING_CLOSE_PROCESS = 'friday-agentability-no-such-close-process.exe'

MISSING_FORCE_PROCESS = 'friday-agentability-no-such-force-process.exe'


def write_documents() -> None:
    """
    Real files for the document utterances to point at.

    "What does this pdf say" with no pdf in the conversation is not a routing
    question - and asked it, Friday answered "I'm unable to directly interact
    with PDF files", which is false and which it had no way to know. A
    question needs a referent before it can measure anything.
    """
    SAMPLE_PDF.parent.mkdir(parents=True, exist_ok=True)
    if not SAMPLE_PDF.is_file():
        from pypdf import PdfWriter
        from pypdf.generic import (ArrayObject, DecodedStreamObject,
                                   DictionaryObject, FloatObject, NameObject,
                                   NumberObject)

        writer = PdfWriter()
        for body in ("Arc reactor maintenance log",
                     "Palladium core replacement notes"):
            page = writer.add_blank_page(width=200, height=200)
            stream = DecodedStreamObject()
            stream.set_data(
                f"BT /F1 12 Tf 20 100 Td ({body}) Tj ET".encode("latin-1"))
            font = DictionaryObject({
                NameObject("/Type"): NameObject("/Font"),
                NameObject("/Subtype"): NameObject("/Type1"),
                NameObject("/BaseFont"): NameObject("/Helvetica")})
            page[NameObject("/Contents")] = writer._add_object(stream)
            page[NameObject("/Resources")] = DictionaryObject({
                NameObject("/Font"): DictionaryObject({
                    NameObject("/F1"): writer._add_object(font)})})
            page[NameObject("/MediaBox")] = ArrayObject(
                [NumberObject(0), NumberObject(0),
                 FloatObject(200), FloatObject(200)])
        with SAMPLE_PDF.open("wb") as handle:
            writer.write(handle)

    if not SAMPLE_XLSX.is_file():
        from openpyxl import Workbook

        workbook = Workbook()
        sheet = workbook.active
        sheet.title = "readings"
        sheet.append(["hour", "output"])
        for hour in range(6):
            sheet.append([hour, hour * 3])
        workbook.create_sheet("notes").append(["nothing yet"])
        workbook.save(str(SAMPLE_XLSX))


def start_mcp():
    """
    A server that answers HTTP, not a port that accepts TCP.

    Those are different things, and the difference cost a whole live gate: a
    previous run's server had been killed, a connect still succeeded, and
    every session started with "no MCP tools found" - so thirteen perfectly
    reachable capabilities were reported unreachable.
    """
    if health.serving():
        return None
    python = ROOT / ".venv" / "Scripts" / "python.exe"
    MCP_LOG.parent.mkdir(parents=True, exist_ok=True)
    with MCP_LOG.open("w", encoding="utf-8") as server_log:
        proc = subprocess.Popen(
            [str(python if python.exists() else sys.executable), "server.py"],
            cwd=str(ROOT), stdout=server_log, stderr=subprocess.STDOUT)
    if not health.wait_until_serving():
        raise SystemExit("the MCP server never started serving; nothing to gate")
    return proc


class FakeTool:
    def __init__(self, name: str, description: str) -> None:
        self.info = type("Info", (), {
            "name": name,
            "raw_schema": {"description": description, "parameters": {}},
        })()


def offline_router() -> CR.Router:
    router = CR.Router()
    router.load([FakeTool(cap.id, cap.description) for cap in C._ALL])
    return router


def check_ranking(domains) -> list[tuple[str, bool]]:
    """Every utterance, scored by where the router puts the right answer."""
    results: list[tuple[str, bool]] = []
    print("=" * 74)
    print("RANKING - no model, no network: can the router even surface it?")
    print("=" * 74)
    for domain in domains:
        print(f"\n  {domain}")
        for said, wanted, forbidden in UTTERANCES[domain]:
            router = offline_router()
            if said in AFTER:
                router.note_used(AFTER[said])
            spoken = said.format(catalogue=CATALOGUE, pdf=SAMPLE_PDF,
                                 workbook=SAMPLE_XLSX,
                                 missing_close_process=MISSING_CLOSE_PROCESS,
                                 missing_force_process=MISSING_FORCE_PROCESS)
            ranked = [m["capability"] for m in router.search(spoken, limit=6)]
            # The model is handed the top six and chooses, so "reachable"
            # means near the top with nothing wrong above it - not first.
            # Asserting first place would be testing this file's taste.
            place = min((ranked.index(w) for w in wanted if w in ranked),
                        default=99)
            blocked = [f for f in forbidden
                       if f in ranked and ranked.index(f) < place]
            ok = place < 3 and not blocked
            # Print where the RIGHT answer landed, not what came first.
            # Showing the top-1 next to a top-3 criterion reads as a
            # contradiction - "PASS ... -> web_deep_research" for a question
            # about the camera is alarming until you know it was second.
            found = (f"#{place + 1} {ranked[place]}" if place < len(ranked)
                     else "not in the top six")
            print(f"    [{'PASS' if ok else 'FAIL'}] {said[:52]:<52} {found}")
            if not ok:
                reason = (f"{blocked} outranks it" if blocked else
                          f"wanted one of {list(wanted)}")
                print(f"           {reason}; got {ranked[:4]}")
            results.append((f"rank: {said}", ok))
    return results


def capabilities_used(result) -> list[str]:
    import json

    used: list[str] = []
    for event in result.events:
        if type(event).__name__ != "FunctionCallEvent":
            continue
        name = event.item.name
        try:
            arguments = json.loads(event.item.arguments or "{}")
        except (json.JSONDecodeError, ValueError):
            used.append(f"{name}(unparseable)")
            continue
        if name == "use_capability":
            used.append(arguments.get("capability", "?"))
        elif name != "search_capabilities":
            used.append(name)
    return used


async def check_live(domains) -> list[tuple[str, bool]]:
    """
    One session per domain, the utterances asked in order.

    Per domain rather than per utterance because the follow-ups need the turn
    before them to have happened - which is also how the boss talks.
    """
    from livekit.agents.voice import AgentSession

    import agent_friday
    from friday import providers

    results: list[tuple[str, bool]] = []
    print("\n" + "=" * 74)
    print("LIVE - the model choosing, with the real tool surface")
    print("=" * 74)

    for domain in domains:
        print(f"\n  {domain}")
        config = agent_friday.session_config()
        session = AgentSession(turn_handling=config["turn_handling"])
        agent = agent_friday.FridayAgent(
            stt=providers.build_stt(config["stt_provider"]),
            llm=providers.build_resilient_llm(config["llm_backend"],
                                              config["llm_role"]),
            tts=providers.build_tts(config["tts_provider"]),
        )
        await session.start(agent)
        try:
            await asyncio.sleep(2.0)
            # "The session started" is not "the session has tools". When the
            # MCP handshake fails the agent runs with an empty surface and
            # every utterance looks like a routing failure - which is how a
            # previous run reported thirteen perfectly reachable capabilities
            # as unreachable. Say what actually happened instead.
            if not agent._router.all_tools:
                print("    [SKIP] the MCP toolset is empty - the server did "
                      "not hand over any tools, so nothing here is a "
                      "measurement of routing")
                raise SystemExit("MCP handshake failed; refusing to report "
                                 "routing results")
            for said, wanted, forbidden in UTTERANCES[domain]:
                spoken = said.format(catalogue=CATALOGUE, pdf=SAMPLE_PDF,
                                     workbook=SAMPLE_XLSX,
                                     missing_close_process=MISSING_CLOSE_PROCESS,
                                     missing_force_process=MISSING_FORCE_PROCESS)
                started = time.monotonic()
                try:
                    outcome = await session.run(user_input=spoken)
                except Exception as exc:                # noqa: BLE001
                    print(f"    [FAIL] {said[:52]:<52} -> {type(exc).__name__}")
                    results.append((f"live: {said}", False))
                    continue
                used = capabilities_used(outcome)
                took = time.monotonic() - started
                hit = any(name in wanted for name in used)
                wrong = [name for name in used if name in forbidden]
                mark = "PASS" if hit and not wrong else "FAIL"
                print(f"    [{mark}] {said[:52]:<52} {took:4.1f}s {used}")
                if mark == "FAIL":
                    # What it SAID, not only what it called. Half the failures
                    # in the first live run were the model sensibly asking
                    # which automation, which pages, which channel - because
                    # the utterance had no referent. That is a defect in the
                    # question, and it is invisible from the tool list alone.
                    spoken_reply = " ".join(
                        e.item.text_content or "" for e in outcome.events
                        if type(e).__name__ == "ChatMessageEvent"
                        and e.item.role == "assistant")
                    print(f"           said: {spoken_reply[:150]}")
                results.append((f"live: {said}", hit and not wrong))
        finally:
            await session.aclose()
    return results

#: What this scored last time, so the next donor batch is judged against it
#: rather than against a memory of it.
BASELINE = ROOT / "docs" / "capabilities" / "agentability-baseline.json"


def freeze_baseline(tally: dict) -> None:
    BASELINE.parent.mkdir(parents=True, exist_ok=True)
    BASELINE.write_text(json.dumps(
        {label: [sum(outcomes), len(outcomes)]
         for label, outcomes in sorted(tally.items())},
        indent=2), encoding="utf-8")
    print(f"\n  baseline written to {BASELINE.name}")


def compare_to_baseline(tally: dict) -> list[str]:
    """
    Did anything get *worse*?

    The invariant this serves: adding a capability is a regression if it makes
    an existing one harder to reach. Seven new tools that each pass their own
    tests, and together drop an existing journey from 68/68 to 63/68, are a
    regression - and the only way to see that is to have written down what it
    scored before.
    """
    if not BASELINE.is_file():
        print("\n  no baseline recorded yet - run with --freeze to set one")
        return []
    before = json.loads(BASELINE.read_text(encoding="utf-8"))

    regressions, arrivals = [], []
    for label, outcomes in sorted(tally.items()):
        now = sum(outcomes) / len(outcomes)
        if label not in before:
            arrivals.append(label)
            continue
        was_passed, was_of = before[label]
        if now < (was_passed / was_of if was_of else 0):
            regressions.append(f"{label}: {sum(outcomes)}/{len(outcomes)} "
                               f"(was {was_passed}/{was_of})")

    print("\n" + "-" * 74)
    if regressions:
        print("  REGRESSED against the recorded baseline:")
        for line in regressions:
            print(f"    {line}")
        print("\n  Adding a capability is a regression if it makes an existing"
              " one harder\n  to reach. Fix the routing ontology before the "
              "next batch.")
    else:
        print("  nothing regressed against the recorded baseline")
    for label in arrivals:
        print(f"  new since the baseline: {label}")
    return regressions


def main() -> int:
    argv = sys.argv[1:]
    runs = 1
    if "--runs" in argv:
        runs = int(argv[argv.index("--runs") + 1])
    domains = list(UTTERANCES)
    if "--domain" in argv:
        wanted = argv[argv.index("--domain") + 1]
        if wanted not in UTTERANCES:
            print(f"unknown domain {wanted!r}; known: {list(UTTERANCES)}")
            return 2
        domains = [wanted]
    offline_only = "--offline" in argv

    tally: dict[str, list[bool]] = {}

    def record(pairs):
        for label, passed in pairs:
            tally.setdefault(label, []).append(passed)

    write_documents()
    record(check_ranking(domains))

    if not offline_only:
        mcp = start_mcp()
        try:
            for attempt in range(runs):
                if runs > 1:
                    print(f"\n### live run {attempt + 1} of {runs}")
                record(asyncio.run(check_live(domains)))
        finally:
            if mcp is not None:
                subprocess.run(["taskkill", "/F", "/T", "/PID", str(mcp.pid)],
                               capture_output=True)

    print("\n" + "=" * 74)
    print("AGENTABILITY")
    print("=" * 74)
    solid = 0
    for label, outcomes in tally.items():
        passed = sum(outcomes)
        mark = ("PASS" if passed == len(outcomes)
                else "FAIL" if passed == 0 else "FLAKY")
        solid += passed == len(outcomes)
        print(f"  [{mark:<5}] {passed}/{len(outcomes)}  {label}")
    print(f"\n  {solid}/{len(tally)} reachable every time")

    regressions = compare_to_baseline(tally)
    if "--freeze" in argv:
        freeze_baseline(tally)
    return 1 if regressions or solid != len(tally) else 0


#: THE QUESTIONS ARE FIXED NOW. They were edited three times, and each edit
#: had to satisfy one of exactly two justifications, both visible above:
#:
#:   a missing referent was supplied   "do this every morning" -> what;
#:                                     "that automation" -> which;
#:                                     "what happened" -> to what
#:   a second answer was genuinely right   web_answer really does answer how
#:                                     many subscribers, with a citation
#:
#: What is NOT allowed is widening `wanted` until a run goes green. Where
#: Friday reached a correct answer by a route this file had not imagined, the
#: question was made sharper rather than the bar lower - "what has it uploaded
#: recently" became "with the view count for each", which the generic web path
#: cannot answer and the domain tools can.
#:
#: Five of the first live run's eight failures were the model sensibly asking
#: which automation, which pages, which channel - because the utterance named
#: none. "Turn that automation off" has no referent, and answering it with a
#: question is right. Those are fixed above by saying which, not by widening
#: what counts as correct.
#:
#: The three that were widened are cases where a second answer is genuinely
#: correct: "what happened this morning" can reasonably start with the list,
#: skipping a track is music_next or spotify_next depending on what is
#: playing, and what Friday knows about him lives in memory as well as the
#: profile.


# ---------------------------------------------------------------------------
# Offline: does the router rank it correctly at all?
#
# This half needs no model and no network, so it runs in milliseconds and can
# be run on every commit. If the router cannot surface a capability, no amount
# of model quality will rescue it.
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Live: does the model actually reach for it?
# ---------------------------------------------------------------------------


if __name__ == "__main__":
    sys.exit(main())
