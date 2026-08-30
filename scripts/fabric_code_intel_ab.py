"""
Does the code graph actually beat grep on real Friday questions?

Run:  .venv/Scripts/python.exe scripts/fabric_code_intel_ab.py

The upstream advertises 120x fewer tokens. That is their number on their
repositories; promotion here requires ours. So this asks the same five real
questions two ways and measures what a model would actually pay:

    calls    how many tool invocations before the answer is in hand
    bytes    output the model has to read, which is the token bill
    seconds  wall clock
    found    whether the expected location is in the output at all

`found` is the control. A method that returns nothing is cheap and useless, and
without this column the cheapest method always wins.

The grep arm is deliberately the *good* version of grep - a targeted pattern a
competent engineer would write, not a naive one - because beating a strawman
proves nothing.
"""

from __future__ import annotations

import json
import pathlib
import subprocess
import time

ROOT = pathlib.Path(__file__).resolve().parent.parent
BINARY = ROOT / "third_party" / "bin" / "cbm" / "codebase-memory-mcp.exe"
PROJECT = "friday-core"

#: (question, grep args, graph flags, substring that proves the answer is there)
QUESTIONS = [
    (
        "Which code delivers a finished Hermes WorkRun into the live session?",
        ["grep", "-rn", "deliver", "--include=*.py", "friday/", "agent_friday.py"],
        ["search_graph", "--project", PROJECT, "--name-pattern", "*deliver*",
         "--limit", "25"],
        "drain_hermes_deliveries",
    ),
    (
        "Where is the objective run engine driven from?",
        ["grep", "-rn", "objective", "--include=*.py", "friday/", "agent_friday.py"],
        ["search_graph", "--project", PROJECT, "--name-pattern", "*objective*",
         "--limit", "25"],
        "objective",
    ),
    (
        "What guards a capability behind policy before it runs?",
        ["grep", "-rn", "requires_approval", "--include=*.py", "friday/"],
        ["search_graph", "--project", PROJECT, "--name-pattern", "*approval*",
         "--limit", "25"],
        "approval",
    ),
    (
        "Which functions talk to the shared knowledge brain?",
        ["grep", "-rn", "brain", "--include=*.py", "friday/"],
        ["search_graph", "--project", PROJECT, "--name-pattern", "*brain*",
         "--limit", "25"],
        "brain",
    ),
    (
        "Where does the Hermes supervisor live and what does it own?",
        ["grep", "-rn", "class HermesSupervisor", "-A", "40", "--include=*.py",
         "friday/"],
        ["search_graph", "--project", PROJECT, "--name-pattern", "*Hermes*",
         "--limit", "25"],
        "HermesSupervisor",
    ),
]


def measure(args: list[str], cwd: pathlib.Path) -> dict:
    start = time.perf_counter()
    try:
        result = subprocess.run(args, cwd=cwd, capture_output=True, text=True,
                                timeout=900, encoding="utf-8", errors="replace")
        out = result.stdout or ""
    except subprocess.TimeoutExpired:
        return {"seconds": 900.0, "bytes": 0, "text": "", "timeout": True}
    # The binary logs to stdout alongside its payload; those lines are not
    # something a model would be shown, so they are not charged for.
    out = "\n".join(line for line in out.splitlines()
                    if not line.startswith(("level=", "hint:", "warning:")))
    return {"seconds": round(time.perf_counter() - start, 2),
            "bytes": len(out.encode("utf-8")), "text": out, "timeout": False}


def main() -> int:
    if not BINARY.exists():
        print(f"binary missing: {BINARY}")
        return 1

    rows = []
    for question, grep_args, graph_args, proof in QUESTIONS:
        grep = measure(grep_args, ROOT)
        graph = measure([str(BINARY), "cli", *graph_args], ROOT)
        rows.append({
            "question": question,
            "grep": {"calls": 1, "bytes": grep["bytes"],
                     "seconds": grep["seconds"],
                     "found": proof.lower() in grep["text"].lower()},
            "graph": {"calls": 1, "bytes": graph["bytes"],
                      "seconds": graph["seconds"],
                      "found": proof.lower() in graph["text"].lower()},
        })
        print(f"  {question[:58]:60} "
              f"grep {grep['bytes']:>7}B {grep['seconds']:>6.2f}s "
              f"{'HIT' if rows[-1]['grep']['found'] else 'MISS'}   "
              f"graph {graph['bytes']:>6}B {graph['seconds']:>6.2f}s "
              f"{'HIT' if rows[-1]['graph']['found'] else 'MISS'}")

    both = [r for r in rows if r["grep"]["found"] and r["graph"]["found"]]
    grep_bytes = sum(r["grep"]["bytes"] for r in both)
    graph_bytes = sum(r["graph"]["bytes"] for r in both)
    summary = {
        "questions": len(rows),
        "both_methods_found_the_answer": len(both),
        "grep_bytes_total": grep_bytes,
        "graph_bytes_total": graph_bytes,
        "byte_reduction_x": round(grep_bytes / graph_bytes, 2) if graph_bytes else None,
        "grep_seconds_total": round(sum(r["grep"]["seconds"] for r in both), 2),
        "graph_seconds_total": round(sum(r["graph"]["seconds"] for r in both), 2),
        "grep_misses": [r["question"] for r in rows if not r["grep"]["found"]],
        "graph_misses": [r["question"] for r in rows if not r["graph"]["found"]],
    }
    print("\n  " + json.dumps(summary, indent=2).replace("\n", "\n  "))

    out = ROOT / "docs" / "architecture" / "CODE_INTEL_AB.json"
    out.write_text(json.dumps({"summary": summary, "rows": rows}, indent=2) + "\n",
                   encoding="utf-8")
    print(f"\n  -> {out.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
