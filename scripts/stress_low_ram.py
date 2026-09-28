"""
Real memory pressure against the resource governor (PRD v7 RES-001, §55).

Run:  .venv-verify/Scripts/python.exe scripts/stress_low_ram.py --out <file.json>

The governor's unit tests turn a dial; this does not. A child process
allocates and touches memory in steps while the governor samples the real
machine with its own sampler. The RAM thresholds are set a few points above
what the machine is using when the run starts, so every level is crossed
with a bounded allocation (at most --cap-mb, and never leaving less than
--floor-gb available) rather than by driving the whole machine to 95%. CPU
and disk thresholds are set out of reach so only memory decides.

At each step the governor is asked for optional work, a first worker and a
second worker. What it admits, queues and sheds is the observation, checked
against its policy: ELEVATED sheds optional work, HIGH holds concurrency to
one worker, CRITICAL starts nothing. The memory is then released and the
same three questions asked again.
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

#: What the governor's policy says each level must do with the three asks.
EXPECTED = {
    "NORMAL": ("ADMIT", "ADMIT", "ADMIT"),
    "ELEVATED": ("SHED", "ADMIT", "ADMIT"),
    "HIGH": ("SHED", "ADMIT", "QUEUE"),
    "CRITICAL": ("SHED", "SHED", "SHED"),
}


def _probe(thresholds) -> dict:
    from friday import governor as G

    # One real sample, held for the three asks, so they see the same machine.
    gov = G.Governor(thresholds=thresholds, sample_ttl_s=60.0)
    pressure = gov.pressure(fresh=True)
    asks = (gov.admit(G.OPTIONAL, label="stress optional"),
            gov.admit(G.WORKER, label="stress worker 1"),
            gov.admit(G.WORKER, label="stress worker 2"))
    decisions = tuple(d.decision for d in asks)
    return {"ram_percent": round(pressure.sample.ram_percent, 2),
            "available_gb": round(pressure.sample.ram_available_gb, 2),
            "level": pressure.level, "optional": decisions[0],
            "first_worker": decisions[1], "second_worker": decisions[2],
            "as_policy_says": decisions == EXPECTED[pressure.level]}


def run(*, step_mb: int = 150, cap_mb: int = 1000, floor_gb: float = 1.5,
        margins: tuple[float, float, float] = (1.5, 3.0, 4.5),
        settle_s: float = 1.5) -> dict:
    import psutil

    from friday import governor as G

    baseline = G.sample_machine().ram_percent
    thresholds = G.Thresholds(
        ram_elevated=baseline + margins[0], ram_high=baseline + margins[1],
        ram_critical=baseline + margins[2],
        cpu_elevated=101.0, cpu_high=101.0, cpu_critical=101.0,
        disk_free_gb_low=0.0, disk_free_gb_critical=-1.0)
    steps = [{"allocated_mb": 0, **_probe(thresholds)}]
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
            steps.append({"allocated_mb": allocated, **_probe(thresholds)})
            if steps[-1]["level"] == G.CRITICAL:
                stopped = "CRITICAL reached"
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
    time.sleep(settle_s * 2)
    released = _probe(thresholds)

    order = list(G.LEVELS)
    peak = max((s["level"] for s in steps), key=order.index)
    return {"baseline_ram_percent": round(baseline, 2),
            "thresholds": {"ram_elevated": thresholds.ram_elevated,
                           "ram_high": thresholds.ram_high,
                           "ram_critical": thresholds.ram_critical},
            "steps": steps, "stopped": stopped, "released": released, "peak": peak,
            "as_policy_says": all(s["as_policy_says"] for s in steps + [released]),
            "demonstrated": (order.index(peak) >= order.index(G.HIGH)
                             and order.index(released["level"]) < order.index(peak))}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--out", required=True)
    parser.add_argument("--step-mb", type=int, default=150)
    parser.add_argument("--cap-mb", type=int, default=1000)
    parser.add_argument("--floor-gb", type=float, default=1.5)
    args = parser.parse_args(argv)
    result = run(step_mb=args.step_mb, cap_mb=args.cap_mb, floor_gb=args.floor_gb)
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(result, indent=1), encoding="utf-8")
    print(json.dumps({k: v for k, v in result.items() if k != "steps"}, indent=1))
    for step in result["steps"]:
        print(step)
    return 0 if result["as_policy_says"] and result["demonstrated"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
