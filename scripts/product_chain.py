"""Keep the product lane moving without a controller session (owner, 2026-10-02: build the rest of FRIDAY).

    python scripts/product_chain.py [--day-usd 45] [--total-usd 45] [--run-usd 15] [--waves 6] [--wait-hours 6]

Waits until memory admission allows a worker (two consecutive admits), runs one bounded P0 program, and repeats
while it stops for reasons that a later run can clear (pressure, max waves, run spend). Stops for good on no ready
work, no progress, an unrecovered crash, another program holding the lock, the chain's total spend cap, or the
owner's day budget: it starts no run once the day's recorded spend plus open spend leases reach the soft stop
(build_kernel.DAY_SOFT_USD), and caps each run below the hard ceiling. The day counts every paid call of the
local day, this chain's or not, verifiers included (2026-10-02: a chain's own cap never saw the day's earlier
spend); the kernel's spend lease enforces the same budget on every launch. Waiting is recorded
as WAITING_RESOURCES in data/build_state.json, never as a failure. Model tokens are spent only by the program's
workers.
"""
from __future__ import annotations

import argparse
import datetime
import json
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import build_kernel as K  # noqa: E402

PY = sys.executable
STATE = ROOT / "data" / "build_state.json"
RUNS = K.RUNS_DIR
AGAIN = ("pressure", "max waves", "spend")  # stop reasons another run can clear
#: After a pressure stop that ran no wave, wait this long before probing again: the probe and the
#: program's own check can disagree right at the memory threshold (2026-10-02: a run a minute).
PRESSURE_BACKOFF_S = 300
PRIORITIES = ("P0", "P1", "P2", "P3")
#: In --until-done mode a run that made no progress (its requirements failed or parked) is retried
#: after this long - parks keep the same failure from being paid for again; crashes still stop.
RETRYABLE = ("no progress", "costly wave without progress", "dirty tree")  # a controller edit is transient
RETRY_AFTER_S = 1800
BUSY = "another program is running"  # build_program.BUSY
BUSY_RETRY_S = 120


def log(msg: str) -> None:
    print(f"{datetime.datetime.now().isoformat(timespec='seconds')} {msg}", flush=True)


def spent() -> float:
    try:
        return float(json.loads(STATE.read_text(encoding="utf-8")).get("spend_usd_total") or 0.0)
    except (OSError, ValueError):
        return 0.0


def waves() -> int:
    try:
        return len(json.loads(STATE.read_text(encoding="utf-8")).get("waves") or [])
    except (OSError, ValueError):
        return 0


def mark(status: str, detail: str) -> None:
    try:
        state = json.loads(STATE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        state = {}
    state["product_chain"] = {"status": status, "detail": detail,
                              "at": datetime.datetime.now().isoformat(timespec="seconds")}
    tmp = STATE.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")
    tmp.replace(STATE)


def admits() -> tuple[bool, str]:
    """Would the program start a job now? Its own dry run answers, pricing each
    queued job by its own memory history (2026-10-03: a probe priced at a generic
    worker - the heaviest recent peak - held every light job back). Only a pressure
    stop waits; any other answer is the program's to give when it runs."""
    r = subprocess.run([PY, "scripts/build_program.py", "--priority", "P0", "--dry-run"], cwd=ROOT,
                       capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=300)
    lines = [ln for ln in r.stdout.splitlines() if ln.strip()]
    why = next((ln for ln in lines if ln.startswith("nothing to plan: ")), "")
    if why.startswith("nothing to plan: pressure"):
        return False, why
    return True, why or "; ".join(lines)[-300:] or r.stderr.strip()[-200:] or f"exit {r.returncode}"


def wait_for_admission(hours: float) -> bool:
    start, ok = time.time(), 0
    while True:
        yes, why = admits()
        ok = ok + 1 if yes else 0
        if ok >= 2:
            return True
        if time.time() - start > hours * 3600:
            mark("WAITING_RESOURCES", f"MEMORY_ADMISSION_TIMEOUT after {hours} h: {why}")
            log(f"WAITING_RESOURCES (timeout): {why}")
            return False
        mark("WAITING_RESOURCES", why)
        time.sleep(60)


def seconds_to_next_day() -> float:
    """Seconds until five minutes past the next local midnight, when the day budget resets."""
    now = datetime.datetime.now()
    nxt = datetime.datetime.combine(now.date() + datetime.timedelta(days=1), datetime.time(0, 5))
    return max(60.0, (nxt - now).total_seconds())


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--day-usd", type=float, default=K.DAY_HARD_USD)
    ap.add_argument("--total-usd", type=float, default=45.0)
    ap.add_argument("--run-usd", type=float, default=15.0)
    ap.add_argument("--waves", type=int, default=6)
    ap.add_argument("--wait-hours", type=float, default=6.0)
    ap.add_argument("--priority", default=PRIORITIES[0], choices=PRIORITIES)
    ap.add_argument("--until-done", action="store_true",
                    help="keep building across days and priorities until no priority has ready work "
                         "(owner, 2026-10-04); --total-usd is ignored, the day budget governs")
    a = ap.parse_args(argv)
    begin, priority = spent(), a.priority

    def next_day(why: str) -> None:
        nonlocal priority
        mark("WAITING_RESOURCES", f"{why}; resuming after local midnight")
        log(f"WAIT {why}; sleeping to the next day")
        time.sleep(seconds_to_next_day())
        priority = a.priority  # a new day re-checks from the top: owner rulings may have unparked work

    while True:
        used = spent() - begin
        if not a.until_done and used >= a.total_usd:
            mark("STOPPED", f"total spend cap ${a.total_usd:.2f} reached (${used:.2f})")
            log(f"STOP total spend ${used:.2f}")
            return 0
        spent_today, reserved = K.spend_committed(RUNS)
        today = spent_today + reserved
        stop_at = min(a.day_usd, K.DAY_SOFT_USD)
        if today >= stop_at:
            why = (f"day spend cap ${stop_at:.2f} reached (${spent_today:.2f} spent, "
                   f"${reserved:.2f} reserved today)")
            if a.until_done:
                next_day(why)
                continue
            mark("STOPPED", why)
            log(f"STOP day spend ${today:.2f}")
            return 0
        if not wait_for_admission(a.wait_hours):
            if a.until_done:
                continue  # waiting is WAITING_RESOURCES, never the end of the build
            return 0
        budget_left = a.run_usd if a.until_done else a.total_usd - used
        run_usd = min(a.run_usd, budget_left, min(a.day_usd, K.DAY_HARD_USD) - today)
        waves_before = waves()
        mark("RUNNING", f"{priority} program, up to {a.waves} waves, ${run_usd:.2f}")
        log(f"RUN {priority} program (${run_usd:.2f}, {a.waves} waves); spent so far ${used:.2f}")
        r = subprocess.run([PY, "scripts/build_program.py", "--priority", priority, "--max-waves", str(a.waves),
                            "--max-usd", f"{run_usd:.2f}"], cwd=ROOT, capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=24 * 3600)
        reason = next((ln.split("stopped: ", 1)[1].strip() for ln in r.stdout.splitlines()
                       if ln.startswith("stopped: ")), f"exit {r.returncode}")
        log(f"program stopped: {reason} (exit {r.returncode}) {r.stderr.strip()[-300:]}")
        if a.until_done:
            if reason == "no ready work":
                if priority == PRIORITIES[-1]:
                    mark("STOPPED", "no ready work at every priority: the build queue is empty")
                    log("STOP no ready work at every priority")
                    return 0
                priority = PRIORITIES[PRIORITIES.index(priority) + 1]
                log(f"NEXT priority {priority}")
                continue
            if reason == "day budget" or (reason == "spend" and waves() == waves_before):
                next_day(f"{reason}: nothing more affordable today")
                continue
            if reason in RETRYABLE:
                mark("WAITING_RESOURCES", f"{reason}; retrying in {RETRY_AFTER_S}s")
                time.sleep(RETRY_AFTER_S)
                continue
            if reason == BUSY:
                mark("WAITING_RESOURCES", f"{reason}; retrying in {BUSY_RETRY_S}s")
                time.sleep(BUSY_RETRY_S)
                continue
        if reason not in AGAIN:
            mark("STOPPED", f"program stopped: {reason}")
            return 0
        if reason == "spend" and waves() == waves_before:
            # Nothing queued fits what is left of this chain's budget; the next run would
            # stop the same way a minute later (2026-10-03, chain 10).
            mark("STOPPED", f"spend stop with no wave: nothing affordable in ${run_usd:.2f}")
            log(f"STOP nothing affordable in ${run_usd:.2f}")
            return 0
        if reason == "pressure" and waves() == waves_before:
            mark("WAITING_RESOURCES", f"pressure stop with no wave; backing off {PRESSURE_BACKOFF_S}s")
            time.sleep(PRESSURE_BACKOFF_S)


if __name__ == "__main__":
    sys.exit(main())
