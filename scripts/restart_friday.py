"""
Restart Friday's two processes and prove they are running the current code.

Run:  .venv/Scripts/python.exe scripts/restart_friday.py
      .venv/Scripts/python.exe scripts/restart_friday.py --check

## Why this exists rather than "just restart it"

Friday is two processes and they fail differently:

    server.py          serves the MCP tools. The agent talks to it over SSE,
                       so a stale one means the model never sees a new
                       capability however correctly it is registered.
    agent_friday.py    the LiveKit worker. `dev` mode watches the directory
                       and re-registers the worker on a change *without*
                       re-importing modules already in `sys.modules`.

Both of those lie in the same direction: they look like they restarted.
Measured, on one evening - four `registered worker` lines, one unchanged pid,
and a capability written ten hours after the server started. A live test asked
"what am I working on?", got "I don't have a record of that", and it read as a
routing bug. The routing was fine.

So this stops both, starts both, and then *checks* - it compares the registry
hash the MCP server reports against the one this process computes from the
tree. Same hash means the running code is the code on disk. A different hash
is the stale case, named rather than guessed at.

`--check` does the comparison without restarting anything, which is what a QA
loop should call before it drives the product.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import subprocess
import sys
import time
import urllib.error
import urllib.request

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from friday import build_identity as B      # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parent.parent
PYTHON = ROOT / ".venv" / "Scripts" / "python.exe"
MCP = "http://127.0.0.1:8000"

#: Where the two processes write. Not the project directory - these are
#: operational noise, not artifacts.
LOGS = ROOT / "data" / "logs"


def running() -> list[dict]:
    """Friday's own python processes, by command line."""
    try:
        out = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | "
             "Where-Object { $_.CommandLine -like '*server.py*' -or "
             "$_.CommandLine -like '*agent_friday*' } | "
             "Select-Object ProcessId,CommandLine | ConvertTo-Json"],
            capture_output=True, text=True, timeout=30).stdout.strip()
        if not out:
            return []
        found = json.loads(out)
        return found if isinstance(found, list) else [found]
    except Exception:                                       # noqa: BLE001
        return []


def stop() -> list[int]:
    stopped = []
    for row in running():
        pid = row.get("ProcessId")
        if not pid:
            continue
        subprocess.run(["powershell", "-NoProfile", "-Command",
                        f"Stop-Process -Id {pid} -Force -ErrorAction "
                        f"SilentlyContinue"], capture_output=True, timeout=20)
        stopped.append(pid)
    return stopped


def start(script: str, *arguments: str) -> int:
    LOGS.mkdir(parents=True, exist_ok=True)
    stem = pathlib.Path(script).stem
    command = (
        f"$p = Start-Process -FilePath '{PYTHON}' "
        f"-ArgumentList '{script}'"
        + (f",'{','.join(arguments)}'" if arguments else "")
        + f" -RedirectStandardOutput '{LOGS / (stem + '.log')}'"
        f" -RedirectStandardError '{LOGS / (stem + '.err.log')}'"
        f" -WindowStyle Hidden -PassThru; $p.Id")
    out = subprocess.run(["powershell", "-NoProfile", "-Command", command],
                         capture_output=True, text=True, timeout=30,
                         cwd=str(ROOT)).stdout.strip()
    return int(out) if out.isdigit() else 0


def mcp_build(timeout: float = 45.0) -> dict | None:
    """Ask the MCP server what it is running. None if it never came up."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(f"{MCP}/sse", timeout=3):
                pass
        except (urllib.error.URLError, OSError):
            time.sleep(1.5)
            continue
        # Reachable. The tool itself is only callable over MCP, so read the
        # line the server prints at startup instead - same information, and
        # it needs no client.
        log = LOGS / "server.log"
        if log.exists():
            for line in log.read_text(errors="replace").splitlines():
                if line.startswith("friday.build"):
                    return _parse(line)
        return {}
    return None


def _parse(line: str) -> dict:
    found = {}
    parts = line.split()
    for index, word in enumerate(parts):
        if word == "commit" and index + 1 < len(parts):
            found["commit"] = parts[index + 1].replace("+dirty", "")
            found["dirty"] = "+dirty" in parts[index + 1]
        if word == "registry" and index + 1 < len(parts):
            found["registry_hash"] = parts[index + 1]
    return found


def check() -> int:
    """Is the running server the code on disk? The question a QA loop asks."""
    mine = B.expected()
    theirs = mcp_build(timeout=5.0)

    print(f"  this process   {B.describe()}")
    if theirs is None:
        print("  mcp server     not reachable on :8000")
        return 2
    if not theirs:
        print("  mcp server     reachable, but has not reported a build "
              "(started before this was added?)")
        return 2

    print(f"  mcp server     commit {theirs.get('commit')} "
          f"registry {theirs.get('registry_hash')}")
    if theirs.get("registry_hash") != mine.registry_hash:
        print("\n  STALE: the server's capability registry differs from the "
              "tree.\n  Anything tested against it is testing old code. "
              "Restart before testing.")
        return 1
    print("\n  OK: the running server matches the working tree.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true",
                        help="compare without restarting")
    arguments = parser.parse_args()

    if arguments.check:
        return check()

    stopped = stop()
    print(f"  stopped {stopped or 'nothing'}")
    time.sleep(3)

    server = start("server.py")
    print(f"  server.py       pid {server}")
    if mcp_build() is None:
        print("  server did not come up; see data/logs/server.err.log")
        return 2

    agent = start("agent_friday.py", "dev")
    print(f"  agent_friday.py pid {agent}")
    time.sleep(6)
    print()
    return check()


if __name__ == "__main__":
    raise SystemExit(main())
