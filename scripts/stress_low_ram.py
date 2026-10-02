"""
Real memory pressure against the resource governor (PRD v7 RES-001, RES-003, §55).

Run:  .venv-verify/Scripts/python.exe scripts/stress_low_ram.py --out <file.json>

The governor's unit tests turn a dial; this does not. A child process
allocates and touches memory in steps while one governor samples the real
machine with its own sampler and judges it the way the product does
(RES-003): against this machine's idle baseline - measured here, at rest,
before anything is allocated - by the share of the idle headroom in use, by
how fast available memory is falling, and by the emergency guardrails. No
threshold is moved to make a level reachable. The allocation is bounded (at
most --cap-mb, and never leaving less than --floor-gb available).

At each step the governor is asked for optional work, a first worker and a
second worker, and what it admits, queues and sheds is checked against its
policy: ELEVATED sheds optional work, HIGH holds concurrency to one worker,
CRITICAL starts nothing; the leases are handed back after each ask. Then the
memory is released and the governor is watched until its level falls: how
long it holds the worse level once the pressure has gone (the hysteresis) is
measured, not assumed. The run demonstrates RES-003 when memory - not CPU,
not disk - is read as pressure before RAM reaches the emergency line, and the
level falls once the memory is back.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

#: Allocate what each stdin line asks for, in MB, and touch every page so the
#: operating system counts it as used. A zero or a closed stdin ends it.
CHILD = """
import sys
blocks = []
for line in sys.stdin:
    mb = int(line)
    if mb <= 0:
        break
    block = bytearray(mb * 1024 * 1024)
    block[::4096] = bytes([1]) * len(range(0, len(block), 4096))
    blocks.append(block)
    print(len(blocks), flush=True)
"""

#: Reasons that come from memory - headroom, the fall, the commit charge,
#: the floor - as opposed to CPU or disk.
#: Idle work for the governor to stop: it does nothing until it is terminated.
IDLE_HELPER = "import time\nwhile True:\n    time.sleep(1)\n"

MEMORY_REASONS =("RAM ", "available RAM", "commit", "only ")

#: What the governor's policy says each level must do with the three asks.
EXPECTED = {
    "NORMAL": ("ADMIT", "ADMIT", "ADMIT"),
    "ELEVATED": ("SHED", "ADMIT", "ADMIT"),
    "HIGH": ("SHED", "ADMIT", "QUEUE"),
    "CRITICAL": ("SHED", "SHED", "SHED"),
}


def measure_baseline(*, readings: int = 5, every_s: float = 1.0):
    """The machine at rest, before anything is allocated: medians of a few
    readings, as a hardware qualification takes them."""
    from friday import governor as G

    G.sample_machine()                      # primes the CPU counter
    samples = []
    for _ in range(readings):
        time.sleep(every_s)
        samples.append(G.sample_machine())
    return G.Baseline.of(samples, provisional=False)


def _probe(gov, started: float) -> dict:
    from friday import governor as G

    # One real sample, held for the three asks, so they see the same machine.
    pressure = gov.pressure(fresh=True)
    asks = (gov.admit(G.OPTIONAL, label="stress optional"),
            gov.admit(G.WORKER, label="stress worker 1"),
            gov.admit(G.WORKER, label="stress worker 2"))
    for d in asks:
        if d.lease:
            gov.release(d.lease)
    decisions = tuple(d.decision for d in asks)
    return {"t_s": round(pressure.sample.at - started, 2),
            "ram_percent": round(pressure.sample.ram_percent, 2),
            "available_gb": round(pressure.sample.ram_available_gb, 3),
            "commit_available_gb": (round(pressure.sample.commit_available_gb, 3)
                                    if pressure.sample.commit_available_gb is not None else None),
            "level": pressure.level, "raw": pressure.raw, "reasons": pressure.reasons,
            "memory_driven": any(r.startswith(MEMORY_REASONS) for r in pressure.reasons
                                 if not r.startswith("holding")),
            "optional": decisions[0], "first_worker": decisions[1], "second_worker": decisions[2],
            "as_policy_says": decisions == EXPECTED[pressure.level]}


def _stopped_at(gov, lease: str, step: dict) -> dict | None:
    """The governor's reduction record for `lease`, with the level of the
    reading that stopped it; None while the work is still running."""
    for record in gov.reductions:
        if record["lease"] == lease:
            return {"level": step["level"], "memory_driven": step["memory_driven"],
                    "reduction": dict(record)}
    return None


def run(*, step_mb: int | None = None, cap_mb: int = 1000, floor_gb: float = 1.0,
        settle_s: float = 1.5, watch_s: float | None = None) -> dict:
    """`step_mb` defaults to a step sized to this machine: 1/150 of the memory
    available at the start (at least 20 MB), so the fall is fast relative to
    the machine's own headroom - what is fast on a machine with 1 GB free is
    unremarkable on one with 12 GB."""
    import psutil

    from friday import governor as G

    baseline = measure_baseline()
    if step_mb is None:
        step_mb = max(20, int(psutil.virtual_memory().available / 1024**2 / 150))
    gov = G.Governor(baseline=baseline, sample_ttl_s=60.0)
    started = time.time()
    # Real running optional work, admitted before any memory is taken: the
    # governor must stop it by itself when memory pressure arrives.
    helper = subprocess.Popen([sys.executable, "-c", IDLE_HELPER])
    admission = gov.admit(G.OPTIONAL, label="stress helper", owner="stress_low_ram",
                          objective_id="RES-001", pid=helper.pid, deadline_s=600.0,
                          cleanup=helper.terminate)
    helper_record = next((o for o in gov.owned_processes()
                          if o["lease"] == admission.lease), None)
    helper_stopped: dict | None = None
    steps = [{"allocated_mb": 0, **_probe(gov, started)}]
    stopped = "cap reached"
    child = subprocess.Popen([sys.executable, "-c", CHILD], stdin=subprocess.PIPE,
                             stdout=subprocess.PIPE, text=True)
    try:
        allocated = 0
        while allocated + step_mb <= cap_mb:
            if psutil.virtual_memory().available / 1024**3 - step_mb / 1024 < floor_gb:
                stopped = f"stopped to keep {floor_gb} GB available"
                break
            child.stdin.write(f"{step_mb}\n")
            child.stdin.flush()
            if not child.stdout.readline():
                stopped = "the allocating process ended"
                break
            allocated += step_mb
            time.sleep(settle_s)
            steps.append({"allocated_mb": allocated, **_probe(gov, started)})
            helper_stopped = helper_stopped or _stopped_at(gov, admission.lease, steps[-1])
            if steps[-1]["level"] == G.CRITICAL:
                stopped = "CRITICAL reached"
                break
            # Enough shown: memory has held concurrency to one worker. Going on
            # would only load the person's machine further.
            if steps[-1]["level"] == G.HIGH and steps[-1]["memory_driven"]:
                stopped = "HIGH reached on memory"
                break
    finally:
        try:
            child.stdin.write("0\n")
            child.stdin.close()
        except OSError:
            pass
        try:
            child.wait(timeout=30)
        except subprocess.TimeoutExpired:
            child.kill()
            child.wait(timeout=30)
        try:
            helper.wait(timeout=10)
        except subprocess.TimeoutExpired:
            helper.kill()
            helper.wait(timeout=10)
    released_at = time.time()
    helper_stopped = helper_stopped or _stopped_at(gov, admission.lease, steps[-1])

    order = list(G.LEVELS)
    peak = max((s["level"] for s in steps), key=order.index)
    # Watch the level fall once the memory is back: it must hold for the
    # dwell (no flapping) and then fall below the peak.
    limit = watch_s if watch_s is not None else gov.thresholds.dwell_s + 30.0
    after: list[dict] = []
    while True:
        time.sleep(2.0)
        after.append(_probe(gov, started))
        if order.index(after[-1]["level"]) < order.index(peak) or time.time() - released_at > limit:
            break
    released = after[-1]
    held_s = round(time.time() - released_at, 1)
    # Where memory was first read as pressure - the demonstration is about
    # memory, so a CPU or disk reason does not count.
    first = next((s for s in steps if s["raw"] != G.NORMAL and s["memory_driven"]), None)
    running_optional = {
        "owned_process": helper_record,
        "stopped_at_level": helper_stopped["level"] if helper_stopped else None,
        "memory_driven": helper_stopped["memory_driven"] if helper_stopped else False,
        "exit_code": helper.returncode,
        "reduction": helper_stopped["reduction"] if helper_stopped else None}
    helper_ok = (helper_stopped is not None and helper_stopped["memory_driven"]
                 and order.index(helper_stopped["level"]) >= order.index(G.ELEVATED))
    return {"running_optional": running_optional,"baseline": baseline.to_dict(), "step_mb": step_mb, "settle_s": settle_s,
            "thresholds": {"ram_yellow": gov.thresholds.ram_yellow,
                           "ram_orange": gov.thresholds.ram_orange,
                           "ram_red": gov.thresholds.ram_red,
                           "trend_orange_s": gov.thresholds.trend_orange_s,
                           "trend_yellow_s": gov.thresholds.trend_yellow_s,
                           "dwell_s": gov.thresholds.dwell_s,
                           "ram_critical": gov.thresholds.ram_critical},
            "steps": steps, "stopped": stopped, "after_release": after,
            "released": released, "held_s": held_s, "peak": peak, "first_pressure": first,
            "as_policy_says": all(s["as_policy_says"] for s in steps + after),
            "demonstrated": (first is not None and helper_ok
                             and first["ram_percent"] < gov.thresholds.ram_critical
                             and order.index(released["level"]) < order.index(peak))}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--out", required=True)
    parser.add_argument("--step-mb", type=int, default=None)
    parser.add_argument("--cap-mb", type=int, default=1000)
    parser.add_argument("--floor-gb", type=float, default=1.0)
    parser.add_argument("--settle-s", type=float, default=1.5)
    args = parser.parse_args(argv)
    result = run(step_mb=args.step_mb, cap_mb=args.cap_mb, floor_gb=args.floor_gb,
                 settle_s=args.settle_s)
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(result, indent=1), encoding="utf-8")
    print(json.dumps({k: v for k, v in result.items() if k not in ("steps", "after_release")}, indent=1))
    for step in result["steps"] + result["after_release"]:
        print({k: v for k, v in step.items() if k != "reasons"}, step["reasons"])
    return 0 if result["as_policy_says"] and result["demonstrated"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
