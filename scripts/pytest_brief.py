"""Run pytest quietly: full output to a log file, a short digest to stdout.

    python scripts/pytest_brief.py [--log FILE] -- <pytest args>

Prints the counts line, each failing test id with its one-line reason, and
`full log: <path>`. Exit code is pytest's. The default log lives under the
system temp dir, never inside the repo.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path

MAX_FAILURE_LINES = 10
MAX_REASON = 160
_COUNTS = re.compile(r"\d+ (?:passed|failed|skipped|error|errors|deselected|xfailed|xpassed|warnings?)\b.* in [\d.]+s")
_FAIL = re.compile(r"^(FAILED|ERROR) (\S+)(?: - (.*))?$")


def default_log() -> Path:
    d = Path(tempfile.gettempdir()) / "friday-test-runs"
    d.mkdir(parents=True, exist_ok=True)
    return d / f"run-{time.strftime('%Y%m%d-%H%M%S')}-{time.monotonic_ns() % 100000}.log"


def digest(output: str) -> list[str]:
    """Counts line plus failing ids with reasons, from pytest -q -rfE output."""
    lines = output.splitlines()
    counts = next((ln.strip("= ").strip() for ln in reversed(lines) if _COUNTS.search(ln)), "no counts line found")
    failures: list[str] = []
    for ln in lines:
        m = _FAIL.match(ln.strip())
        if m:
            reason = (m.group(3) or "").strip()[:MAX_REASON]
            failures.append(f"{m.group(1)} {m.group(2)}" + (f" - {reason}" if reason else ""))
    shown = failures[:MAX_FAILURE_LINES]
    if len(failures) > len(shown):
        shown.append(f"... and {len(failures) - len(shown)} more (see log)")
    return [counts, *shown]


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    pytest_args: list[str] = []
    if "--" in argv:
        i = argv.index("--")
        argv, pytest_args = argv[:i], argv[i + 1:]
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--log", type=Path, default=None)
    args = ap.parse_args(argv)
    log = args.log or default_log()
    log.parent.mkdir(parents=True, exist_ok=True)
    cmd = [sys.executable, "-m", "pytest", *pytest_args, "-q", "-rfE", "--tb=short", "-p", "no:cacheprovider"]
    proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    output = proc.stdout + ("\n" + proc.stderr if proc.stderr else "")
    log.write_text(output, encoding="utf-8", newline="\n")
    for line in digest(output):
        print(line)
    print(f"full log: {log}")
    return proc.returncode


if __name__ == "__main__":
    sys.exit(main())
