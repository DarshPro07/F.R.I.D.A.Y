#!/usr/bin/env python3
"""
measure.py - resource + startup baseline for the Friday stack.

Launches the whole app via start.py, times the startup milestones, lets it
settle, samples the process tree, then tears it down and prints JSON.

    python scripts/measure.py --label before  --out docs/phase0/metrics-before.json
    python scripts/measure.py --label after   --out docs/phase0/metrics-after.json

Pure stdlib. Windows process sampling shells out to PowerShell CIM because
psutil is not a project dependency and this is not worth one.
"""

import argparse
import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SETTLE_SECONDS = 20.0
STARTUP_TIMEOUT = 90.0

# start.py milestones -> metric name
MILESTONES = {
    "starting MCP server": "t_server_spawn",
    "MCP ready": "t_mcp_ready",
    "starting voice agent": "t_agent_spawn",
    "up. Ctrl+C to stop": "t_stack_up",
}


def sample_tree(root_pid: int) -> dict:
    """Working set + CPU seconds for root_pid and every descendant."""
    if os.name != "nt":
        return {"error": "sampling implemented for Windows only"}

    ps = (
        "Get-CimInstance Win32_Process | "
        "Select-Object ProcessId,ParentProcessId,Name,WorkingSetSize,UserModeTime,KernelModeTime | "
        "ConvertTo-Json -Compress"
    )
    raw = subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps],
        capture_output=True, text=True, timeout=60,
    ).stdout
    rows = json.loads(raw)
    if isinstance(rows, dict):
        rows = [rows]

    by_parent: dict = {}
    by_pid: dict = {}
    for row in rows:
        by_pid[row["ProcessId"]] = row
        by_parent.setdefault(row["ParentProcessId"], []).append(row["ProcessId"])

    # BFS the tree from the launcher
    seen, queue = [], [root_pid]
    while queue:
        pid = queue.pop()
        if pid in seen or pid not in by_pid:
            continue
        seen.append(pid)
        queue.extend(by_parent.get(pid, []))

    procs = []
    for pid in seen:
        row = by_pid[pid]
        procs.append({
            "pid": pid,
            "name": row["Name"],
            "rss_mb": round(row["WorkingSetSize"] / 1048576, 1),
            # 100ns units -> seconds
            "cpu_seconds": round(
                (row["UserModeTime"] + row["KernelModeTime"]) / 1e7, 2
            ),
        })
    procs.sort(key=lambda p: -p["rss_mb"])
    return {
        "process_count": len(procs),
        "total_rss_mb": round(sum(p["rss_mb"] for p in procs), 1),
        "total_cpu_seconds": round(sum(p["cpu_seconds"] for p in procs), 2),
        "processes": procs,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--label", default="unlabelled")
    parser.add_argument("--out")
    args = parser.parse_args()

    env = dict(os.environ, PYTHONUNBUFFERED="1", PYTHONIOENCODING="utf-8")
    started = time.monotonic()
    proc = subprocess.Popen(
        [sys.executable, "start.py"],
        cwd=str(ROOT), env=env,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        stdin=subprocess.DEVNULL,
        text=True, encoding="utf-8", errors="replace", bufsize=1,
    )

    timings: dict = {}
    log: list = []
    up = threading.Event()

    def read():
        for line in proc.stdout:
            line = line.rstrip()
            log.append(line)
            for needle, key in MILESTONES.items():
                if needle in line and key not in timings:
                    timings[key] = round(time.monotonic() - started, 2)
            if "up. Ctrl+C" in line:
                up.set()

    threading.Thread(target=read, daemon=True).start()

    result = {"label": args.label, "settle_seconds": SETTLE_SECONDS}
    try:
        if not up.wait(STARTUP_TIMEOUT):
            result["error"] = f"stack did not come up within {STARTUP_TIMEOUT}s"
            result["log"] = log
            return _emit(result, args.out, 1)

        print(f"[measure] stack up in {timings.get('t_stack_up')}s; "
              f"settling {SETTLE_SECONDS}s ...", flush=True)
        time.sleep(SETTLE_SECONDS)

        result["startup_seconds"] = timings
        result["idle"] = sample_tree(proc.pid)
        result["log"] = log
        return _emit(result, args.out, 0)
    finally:
        if proc.poll() is None:
            if os.name == "nt":
                subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                               capture_output=True)
            else:
                proc.terminate()


def _emit(result: dict, out: str | None, code: int) -> int:
    text = json.dumps(result, indent=2)
    if out:
        path = ROOT / out
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        print(f"[measure] wrote {path}", flush=True)
    summary = {k: v for k, v in result.items() if k != "log"}
    print(json.dumps(summary, indent=2))
    return code


if __name__ == "__main__":
    sys.exit(main())
