"""Keep the product lane moving without a controller session (owner, 2026-10-02: build the rest of FRIDAY).

    python scripts/product_chain.py [--total-usd 45] [--run-usd 15] [--waves 6] [--wait-hours 6]

Waits until memory admission allows a worker (two consecutive admits), runs one bounded P0 program, and repeats
while it stops for reasons that a later run can clear (pressure, max waves, run spend). Stops for good on no ready
work, no progress, an unrecovered crash, another program holding the lock, or the total spend cap. Waiting is
recorded as WAITING_RESOURCES in data/build_state.json, never as a failure. Model tokens are spent only by the
program's workers.
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
PY = sys.executable
STATE = ROOT / "data" / "build_state.json"
AGAIN = ("pressure", "max waves", "spend")  # stop reasons another run can clear


def log(msg: str) -> None:
    print(f"{datetime.datetime.now().isoformat(timespec='seconds')} {msg}", flush=True)


def spent() -> float:
    try:
        return float(json.loads(STATE.read_text(encoding="utf-8")).get("spend_usd_total") or 0.0)
    except (OSError, ValueError):
        return 0.0


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
    r = subprocess.run([PY, "scripts/build_kernel.py", "concurrency"], cwd=ROOT, capture_output=True,
                       text=True, encoding="utf-8", errors="replace", timeout=120)
    out = r.stdout.strip()
    try:
        return int(out.split(" ", 1)[0]) >= 1, out
    except ValueError:
        return False, out or r.stderr.strip()[-200:]


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


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--total-usd", type=float, default=45.0)
    ap.add_argument("--run-usd", type=float, default=15.0)
    ap.add_argument("--waves", type=int, default=6)
    ap.add_argument("--wait-hours", type=float, default=6.0)
    a = ap.parse_args(argv)
    begin = spent()
    while True:
        used = spent() - begin
        if used >= a.total_usd:
            mark("STOPPED", f"total spend cap ${a.total_usd:.2f} reached (${used:.2f})")
            log(f"STOP total spend ${used:.2f}")
            return 0
        if not wait_for_admission(a.wait_hours):
            return 0
        run_usd = min(a.run_usd, a.total_usd - used)
        mark("RUNNING", f"P0 program, up to {a.waves} waves, ${run_usd:.2f}")
        log(f"RUN P0 program (${run_usd:.2f}, {a.waves} waves); spent so far ${used:.2f}")
        r = subprocess.run([PY, "scripts/build_program.py", "--priority", "P0", "--max-waves", str(a.waves),
                            "--max-usd", f"{run_usd:.2f}"], cwd=ROOT, capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=24 * 3600)
        reason = next((ln.split("stopped: ", 1)[1].strip() for ln in r.stdout.splitlines()
                       if ln.startswith("stopped: ")), f"exit {r.returncode}")
        log(f"program stopped: {reason} (exit {r.returncode}) {r.stderr.strip()[-300:]}")
        if reason not in AGAIN:
            mark("STOPPED", f"program stopped: {reason}")
            return 0


if __name__ == "__main__":
    sys.exit(main())
