#!/usr/bin/env python3
"""
The product journey with the model actually driving it.

golden_product_mcp.py calls the six tools directly, which proves the surface.
It does not prove the thing that matters in a conversation: that Friday
*reaches* for product processing when asked, that "which products failed?" is
answered by reading a recorded run rather than by doing the work again, and
that a run found by id in a session which never saw it start is the same run.

Four asks, one catalogue, and the database checked between every one of them:

    "process this catalogue"        -> a run exists that did not exist before
    "which products failed?"        -> nothing was rewritten
    "retry the network failures"    -> only those rows were rewritten
    (new session) "how did it go?"  -> found by id, still nothing rewritten

    python scripts/golden_product_agent.py
"""

from __future__ import annotations

import asyncio
import csv
import json
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env")

import agent_friday  # noqa: E402
from friday import health  # noqa: E402
from friday import providers  # noqa: E402
from friday.store import Store  # noqa: E402

#: Inside the project because product_process jails the path it is given, and
#: the model is the one choosing that path.
CATALOGUE_PATH = ROOT / "data" / "gate" / "agent_catalogue.csv"

CATALOGUE = [
    {"sku": "A-200", "title": "Blue Cotton Shirt", "price": "29.99",
     "image": "https://example.com/1.jpg", "description": "A cotton shirt."},
    {"sku": "A-201", "title": "Leather Boots", "price": "89.00",
     "image": "https://example.com/2.jpg", "description": "Leather boots."},
    {"sku": "A-202", "title": "Denim Jacket", "price": "banana",
     "image": "https://example.com/3.jpg"},                       # quarantine
    {"sku": "A-203", "title": "Silk Tie", "price": "15.00",
     "image": "http://169.254.169.254/latest/meta-data/"},        # refused
    {"sku": "A-204", "title": "Linen Hat", "price": "12.00",
     "image": "https://host-that-does-not-resolve.invalid/5.jpg"},  # retryable
]


def check(passed: bool, message: str, detail: str = "") -> tuple[str, bool]:
    print(f"  [{'PASS' if passed else 'FAIL'}] {message}")
    if detail:
        print(f"         {detail}")
    return (message, bool(passed))


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
    proc = subprocess.Popen(
        [str(python if python.exists() else sys.executable), "server.py"],
        cwd=str(ROOT), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    if not health.wait_until_serving():
        raise SystemExit("the MCP server never started serving; nothing to gate")
    return proc


def write_catalogue() -> Path:
    CATALOGUE_PATH.parent.mkdir(parents=True, exist_ok=True)
    with CATALOGUE_PATH.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(
            handle, fieldnames=["sku", "title", "price", "image", "description"])
        writer.writeheader()
        for row in CATALOGUE:
            writer.writerow({k: row.get(k, "") for k in writer.fieldnames})
    return CATALOGUE_PATH


# ---------------------------------------------------------------------------
# Reading what the model did, rather than what it said it did
# ---------------------------------------------------------------------------


def capabilities_used(result) -> list[str]:
    """
    Which capabilities were invoked this turn.

    The model reaches non-core tools through `use_capability`, so the tool
    *name* on the event is always the proxy. The capability is in its
    arguments, and that is the thing worth asserting on.
    """
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
            used.append(arguments.get("capability", "use_capability(?)"))
        elif name == "search_capabilities":
            # Worth printing: the first failure of this gate was the model
            # searching "read a file" for "process this catalogue", which is
            # invisible if only the tool names are recorded.
            used.append(f"search({arguments.get('query', '')!r})")
        else:
            used.append(name)
    return used


def spoken(result) -> str:
    return " ".join(
        event.item.text_content or ""
        for event in result.events
        if type(event).__name__ == "ChatMessageEvent"
        and event.item.role == "assistant")


def snapshot(store: Store, run_id: str) -> dict:
    """Enough of a run's records to tell 'read' from 'rewritten'."""
    return {r["product_key"]: (r["at"], r["output_hash"], r["status"])
            for r in store.product_records(run_id)}


async def ask(session, text: str) -> tuple[list[str], str]:
    print("=" * 70)
    print(f"[you] {text}")
    print("=" * 70)
    started = time.monotonic()
    result = await session.run(user_input=text)
    used, reply = capabilities_used(result), spoken(result)
    print(f"  {time.monotonic() - started:.1f}s")
    print(f"  capabilities : {used}")
    print(f"  reply        : {reply[:300]}\n")
    return used, reply


def new_agent():
    config = agent_friday.session_config()
    from livekit.agents.voice import AgentSession

    session = AgentSession(turn_handling=config["turn_handling"])
    agent = agent_friday.FridayAgent(
        stt=providers.build_stt(config["stt_provider"]),
        llm=providers.build_resilient_llm(config["llm_backend"],
                                          config["llm_role"]),
        tts=providers.build_tts(config["tts_provider"]),
    )
    return session, agent


async def journey() -> list[bool]:
    results: list[bool] = []
    store = Store()
    catalogue = write_catalogue()
    runs_before = {r["run_id"] for r in store.product_runs(limit=50)}

    session, agent = new_agent()
    await session.start(agent)
    try:
        await asyncio.sleep(2.0)  # let on_enter narrow the tool surface

        # --- 1. process ----------------------------------------------------
        used, reply = await ask(
            session,
            f"process the product catalogue at {catalogue} and tell me how it "
            f"went")

        runs_after = store.product_runs(limit=50)
        fresh = [r for r in runs_after if r["run_id"] not in runs_before]
        results.append(check("product_process" in used,
                             "it reached for product processing itself",
                             f"used: {used}"))
        results.append(check(len(fresh) == 1,
                             "exactly one new run exists in the database",
                             f"new runs: {[r['run_id'] for r in fresh]}"))
        if not fresh:
            print("  no run was created - the rest cannot be judged")
            return results

        run_id = fresh[0]["run_id"]
        print(f"  run created  : {run_id}  status={fresh[0]['status']}\n")
        results.append(check(fresh[0]["source"] == catalogue.name,
                             "the run records which catalogue it came from",
                             fresh[0]["source"]))
        results.append(check(
            not any(word in reply.lower()
                    for word in ("i can't", "i don't have", "unable to")),
            "it did not claim it lacked the ability"))

        # --- 2. which failed ------------------------------------------------
        before = snapshot(store, run_id)
        used, reply = await ask(session, "which products failed, and why?")
        after = snapshot(store, run_id)

        results.append(check("product_result" in used,
                             "it read the recorded run", f"used: {used}"))
        results.append(check("product_process" not in used,
                             "it did not reprocess the catalogue to answer"))
        results.append(check(before == after,
                             "not one record was rewritten by being asked"))
        named = [key for key in ("A-202", "A-203", "A-204")
                 if key in reply or key.replace("-", " ") in reply]
        results.append(check(bool(named),
                             "the answer names actual products, not a summary",
                             f"named: {named}"))

        # --- 3. retry -------------------------------------------------------
        before = snapshot(store, run_id)
        retryable = [key for key, row in
                     ((r["product_key"], r) for r in store.product_records(run_id))
                     if any(s.get("status") == "FAILED_RETRYABLE"
                            for s in (row["stages"] or {}).values())]
        print(f"  retryable    : {retryable}")
        results.append(check(bool(retryable),
                             "there is genuinely something to retry",
                             "otherwise the retry checks below prove nothing"))

        used, reply = await ask(session, "retry only the network failures")
        after = snapshot(store, run_id)

        results.append(check("product_retry" in used,
                             "it used the retry capability", f"used: {used}"))
        rewritten = [key for key in after if before.get(key, ("",))[0] != after[key][0]]
        results.append(check(
            set(rewritten) == set(retryable),
            "exactly the retryable products were re-run",
            f"rewritten={rewritten} retryable={retryable}"))
        results.append(check(len(after) == len(before),
                             "the retry duplicated nothing"))
        results.append(check(
            {k for k, v in before.items() if v[2] == "SUCCEEDED"}
            <= {k for k, v in after.items() if v[2] == "SUCCEEDED"},
            "products that already succeeded were not lost"))
        results.append(check(
            len(store.product_runs(limit=50)) == len(runs_after),
            "the retry stayed inside the same run rather than starting a new one"))
    finally:
        await session.aclose()

    # --- 4. a session that never saw it happen -----------------------------
    print("=" * 70)
    print("NEW SESSION - no memory of any of the above")
    print("=" * 70)
    before = snapshot(store, run_id)
    session, agent = new_agent()
    await session.start(agent)
    try:
        await asyncio.sleep(2.0)
        used, reply = await ask(
            session, "how did that product catalogue job finish?")
        after = snapshot(store, run_id)

        results.append(check(
            any(name in used for name in ("product_runs", "product_status",
                                          "product_result")),
            "it went to the database rather than to its own memory of the turn",
            f"used: {used}"))
        results.append(check(before == after,
                             "answering after the fact rewrote nothing"))
        results.append(check(
            run_id in reply or any(word in reply.lower() for word in
                                   ("partial", "failed", "quarantin")),
            "it reported the recorded outcome", reply[:160]))
    finally:
        await session.aclose()
    return results


def main() -> int:
    """
    `--runs N` because half of what this measures is a language model choosing
    a tool, and one sample of that is an anecdote. Tuning a prompt against a
    single green run is how you fit noise. The deterministic half - what the
    database says happened - is the same every time, so a check that varies
    across runs is telling you which half it belongs to.
    """
    runs = 1
    if "--runs" in sys.argv:
        runs = int(sys.argv[sys.argv.index("--runs") + 1])

    mcp = start_mcp()
    tally: dict[str, list[bool]] = {}
    try:
        for attempt in range(runs):
            if runs > 1:
                print(f"\n{'#' * 70}")
                print(f"# RUN {attempt + 1} of {runs}")
                print("#" * 70)
            for label, passed in asyncio.run(journey()):
                tally.setdefault(label, []).append(passed)
    finally:
        if mcp is not None:
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(mcp.pid)],
                           capture_output=True)

    print("\n" + "=" * 70)
    print(f"RESULT over {runs} run(s)")
    print("=" * 70)
    perfect = 0
    for label, outcomes in tally.items():
        rate = sum(outcomes)
        mark = "PASS" if rate == len(outcomes) else (
            "FAIL" if rate == 0 else "FLAKY")
        perfect += rate == len(outcomes)
        print(f"  [{mark:<5}] {rate}/{len(outcomes)}  {label}")
    print(f"\n  {perfect}/{len(tally)} checks passed every run")
    return 0 if perfect == len(tally) else 1


if __name__ == "__main__":
    sys.exit(main())
