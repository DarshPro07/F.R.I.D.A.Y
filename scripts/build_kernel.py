"""Deterministic build scheduler (BH-OPT-01 step 4): code decides, no model in the loop.

    python scripts/build_kernel.py ready
    python scripts/build_kernel.py waves [--cap N]
    python scripts/build_kernel.py concurrency
    python scripts/build_kernel.py launch REQ [--dry-run] [--model M] [--max-turns N] [--max-budget-usd X]
    python scripts/build_kernel.py run-wave [--cap N] [--dry-run]
    python scripts/build_kernel.py status

Ready = open ledger rows whose dependencies (dependencies.json) are all
terminal. Waves group rows whose cited-file surfaces are disjoint. Concurrency
comes from the resource governor. Workers are headless `claude -p` processes,
one git worktree each, watched with zero tokens (exit, heartbeat, wall time).
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import signal
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT))

import prd_ledger as PL  # noqa: E402

DEPS_FILE = ROOT / "docs" / "plans" / "build-harness" / "dependencies.json"
RUNS_DIR = ROOT / "data" / "build_runs"
VENV_PY = "F:/F.R.I.D.A.Y/.venv-build/Scripts/python.exe"
DEFAULT_CAP = 2
DEFAULT_WALL_S = 45 * 60
DEFAULT_STALL_S = 10 * 60
ALLOWED_TOOLS = "Read,Edit,Write,Glob,Grep,Bash"
_PRIORITY = {"P0": 0, "P1": 1, "P2": 2, "P3": 3}


class KernelError(Exception):
    pass


# ---------------------------------------------------------------- queue

def load_rows() -> list[dict]:
    """The same rows `prd_ledger.py --set v7` renders."""
    return PL.apply_progress(PL._load_v7(), PL.PROGRESS_V7)


def load_config(path: Path = DEPS_FILE) -> dict:
    cfg = json.loads(Path(path).read_text(encoding="utf-8"))
    return {"depends": cfg.get("depends", {}), "order": cfg.get("order", [])}


def load_deps(path: Path = DEPS_FILE) -> dict[str, list[str]]:
    return load_config(path)["depends"]


def waiting_on_owner(row: dict) -> bool:
    return str(row.get("next_action", "")).lstrip().startswith("OWNER:")


def ready(rows: list[dict], deps: dict[str, list[str]], order: list[str] | None = None,
          include_owner: bool = False) -> list[dict]:
    """Open rows whose dependencies are all terminal, not waiting on the owner
    (unless include_owner); sorted priority, then position in `order`, then id."""
    by_id = {r["id"]: r for r in rows}
    for rid, needs in deps.items():
        for one in (rid, *needs):
            if one not in by_id:
                raise KernelError(f"dependencies.json names unknown id {one!r}")
    pos = {rid: i for i, rid in enumerate(order or [])}
    out = [r for r in rows if r["status"] in PL.WORKING
           and (include_owner or not waiting_on_owner(r))
           and all(by_id[d]["status"] in PL.TERMINAL for d in deps.get(r["id"], ()))]
    return sorted(out, key=lambda r: (_PRIORITY.get(r["priority"], 9),
                                      pos.get(r["id"], len(pos)), r["id"]))


_CODE_DIRS = ("friday/", "scripts/", "tests/", "ui/")
_LEDGER_FILES = frozenset({"scripts/prd_ledger.py", "scripts/prd_v7_table.py"})


def surface(row: dict) -> frozenset[str]:
    """Code paths the row cites, ':lines' stripped; empty means unknown.

    Only friday/, scripts/, tests/, ui/ and root *.py count; the ledger files
    (the controller edits them at integration), data/ and docs/ do not."""
    out = set()
    for f in row.get("files", ()):
        path = f.strip().split(":", 1)[0].replace(chr(92), "/")
        if path in _LEDGER_FILES:
            continue
        if path.startswith(_CODE_DIRS) or ("/" not in path and path.endswith(".py")):
            out.add(path)
    return frozenset(out)


def waves(rows: list[dict], cap: int = DEFAULT_CAP) -> list[list[dict]]:
    """Greedy, in priority order: a row joins a wave if its surface is known
    and disjoint from the wave's. Unknown-surface rows run alone."""
    cap = max(1, cap)
    pending = list(rows)
    out: list[list[dict]] = []
    while pending:
        first = pending.pop(0)
        wave, used = [first], set(surface(first))
        if used:
            for row in list(pending):
                if len(wave) >= cap:
                    break
                s = surface(row)
                if s and not (s & used):
                    wave.append(row)
                    used |= s
                    pending.remove(row)
        out.append(wave)
    return out


def concurrency(level: str | None = None, cap: int = DEFAULT_CAP) -> int:
    if level is None:
        from friday.governor import governor
        level = governor().pressure().level
    return {"NORMAL": cap, "ELEVATED": 1}.get(level, 0)


# --------------------------------------------------------------- launch

def _worktrees_root() -> Path:
    r = subprocess.run(["git", "rev-parse", "--git-common-dir"], cwd=ROOT,
                       capture_output=True, text=True, timeout=30)
    common = Path(r.stdout.strip())
    if not common.is_absolute():
        common = ROOT / common
    return common.resolve().parent / ".worktrees"


def packet_text(row: dict) -> str:
    def lines(key):
        v = row.get(key) or []
        return "\n".join(f"- {x}" for x in v) if isinstance(v, (list, tuple)) else str(v)
    return f"""# Build packet: {row['id']} ({row['priority']})

Summary: {row['summary']}

## Current implementation
{row.get('current_implementation', '')}

## Acceptance
{row.get('acceptance', '')}

## Next action
{row.get('next_action', '')}

## Cited files
{lines('files')}

## Cited tests
{lines('tests')}

## Rules
- Work only in this worktree. Never commit. Never touch data/ (especially data/ada.sqlite3).
- Run targeted tests only: `{VENV_PY} scripts/test_impact.py --direct --files <changed files>` then
  `{VENV_PY} scripts/pytest_brief.py -- <test files>`.
- Add a test that fails without your change. No debug prints, no secrets.
- Finish by writing `.build/evidence.json`:
  {{"requirement": "{row['id']}", "status": "DONE|BLOCKED", "summary": "<=300 chars",
   "files_changed": [], "tests": {{"command": "", "passed": 0, "failed": 0}}, "blockers": []}}
"""


def worker_command(model: str, max_turns: int, max_budget: float) -> list[str]:
    exe = shutil.which("claude") or "claude"
    return [exe, "-p", "Read .build/packet.md and carry out the requirement it describes.",
            "--model", model, "--max-turns", str(max_turns),
            "--max-budget-usd", str(max_budget), "--output-format", "stream-json",
            "--verbose", "--permission-mode", "acceptEdits", "--allowedTools", ALLOWED_TOOLS]


@dataclass
class Running:
    req: str
    proc: subprocess.Popen
    wt: Path
    started: float = field(default_factory=time.time)


def launch(row: dict, *, worktrees: Path | None = None, model: str = "sonnet",
           max_turns: int = 25, max_budget: float = 3.0, dry_run: bool = False):
    """Create the worktree, write the packet, start the worker. In dry-run
    return the plan and touch nothing."""
    name = row["id"].lower()
    wt = (worktrees or _worktrees_root()) / name
    cmd = worker_command(model, max_turns, max_budget)
    if dry_run:
        return {"worktree": str(wt), "branch": f"build/{name}",
                "packet": str(wt / ".build" / "packet.md"), "command": cmd}
    if wt.exists():
        raise KernelError(f"worktree {wt} already exists; refusing to reuse it")
    subprocess.run(["git", "worktree", "add", str(wt), "-b", f"build/{name}", "HEAD"],
                   cwd=ROOT, check=True, capture_output=True, text=True, timeout=120)
    (wt / ".build").mkdir(parents=True, exist_ok=True)
    (wt / ".build" / "packet.md").write_text(packet_text(row), encoding="utf-8", newline="\n")
    log = open(wt / ".build" / "stream.jsonl", "wb")
    kw = ({"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP} if os.name == "nt"
          else {"start_new_session": True})
    proc = subprocess.Popen(cmd, cwd=wt, stdout=log, stderr=subprocess.STDOUT,
                            stdin=subprocess.DEVNULL, **kw)
    log.close()
    return Running(row["id"], proc, wt)


# ---------------------------------------------------------------- watch

def kill_tree(proc: subprocess.Popen) -> None:
    if proc.poll() is not None:
        return
    if os.name == "nt":
        subprocess.run(["taskkill", "/T", "/F", "/PID", str(proc.pid)],
                       capture_output=True, timeout=30)
    else:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    try:
        proc.wait(timeout=30)
    except subprocess.TimeoutExpired:
        proc.kill()


def _stream_facts(path: Path) -> dict:
    result = None
    if path.exists():
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            try:
                ev = json.loads(line)
            except ValueError:
                continue
            if isinstance(ev, dict) and ev.get("type") == "result":
                result = ev
    return result or {}


def _evidence(wt: Path) -> dict:
    try:
        return json.loads((wt / ".build" / "evidence.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def finalize(run: Running, reason: str | None, runs_dir: Path = RUNS_DIR) -> dict:
    """Write data/build_runs/<req>.json from the result event and evidence."""
    res = _stream_facts(run.wt / ".build" / "stream.jsonl")
    ev = _evidence(run.wt)
    usage = res.get("usage") or {}
    if reason:
        state = reason
    elif run.proc.returncode != 0 or not res or res.get("is_error"):
        state = "FAILED"
    elif ev.get("status") in ("DONE", "BLOCKED"):
        state = ev["status"]
    else:
        state = "FAILED"
    rec = {"requirement": run.req, "state": state, "turns": res.get("num_turns", 0),
           "input_tokens": usage.get("input_tokens", 0),
           "cache_read_tokens": usage.get("cache_read_input_tokens", 0),
           "cache_creation_tokens": usage.get("cache_creation_input_tokens", 0),
           "output_tokens": usage.get("output_tokens", 0),
           "cost_usd": res.get("total_cost_usd", 0.0),
           "wall_seconds": round(time.time() - run.started, 1),
           "summary": ev.get("summary", ""), "worktree": str(run.wt)}
    runs_dir.mkdir(parents=True, exist_ok=True)
    (runs_dir / f"{run.req}.json").write_text(json.dumps(rec, indent=2) + "\n",
                                              encoding="utf-8", newline="\n")
    return rec


def watch(runs: list[Running], *, wall_s: float = DEFAULT_WALL_S, stall_s: float = DEFAULT_STALL_S,
          poll_s: float = 5.0, runs_dir: Path = RUNS_DIR) -> list[dict]:
    """Poll to completion. Heartbeat = stream log growth. Zero model calls."""
    live = {r.req: r for r in runs}
    last = {r.req: (-1, time.time()) for r in runs}
    done: list[dict] = []
    while live:
        for req, run in list(live.items()):
            reason = None
            if run.proc.poll() is None:
                now = time.time()
                p = run.wt / ".build" / "stream.jsonl"
                size = p.stat().st_size if p.exists() else 0
                if size != last[req][0]:
                    last[req] = (size, now)
                if now - run.started > wall_s:
                    reason = "TIMEOUT"
                elif now - last[req][1] > stall_s:
                    reason = "STALLED"
                if reason is None:
                    continue
                kill_tree(run.proc)
            done.append(finalize(run, reason, runs_dir))
            del live[req]
        if live:
            time.sleep(poll_s)
    return done


# ------------------------------------------------------------------ CLI

def status_table(runs_dir: Path = RUNS_DIR) -> list[str]:
    out = [f"{'REQ':<12}{'STATE':<9}{'TURNS':>6}{'IN':>9}{'CACHE_R':>11}{'CACHE_W':>10}"
           f"{'OUT':>8}{'USD':>8}{'SEC':>8}"]
    for p in sorted(runs_dir.glob("*.json")) if runs_dir.exists() else []:
        r = json.loads(p.read_text(encoding="utf-8"))
        out.append(f"{r['requirement']:<12}{r['state']:<9}{r['turns']:>6}{r['input_tokens']:>9}"
                   f"{r['cache_read_tokens']:>11}{r['cache_creation_tokens']:>10}{r['output_tokens']:>8}"
                   f"{r['cost_usd']:>8.2f}{r['wall_seconds']:>8.0f}")
    return out


def _fmt(row: dict) -> str:
    return f"{row['priority']} {row['id']:<11} surface={sorted(surface(row)) or 'UNKNOWN'}"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("ready").add_argument("--all", action="store_true")
    sub.add_parser("concurrency")
    sub.add_parser("status")
    w = sub.add_parser("waves")
    rw = sub.add_parser("run-wave")
    for p in (w, rw):
        p.add_argument("--cap", type=int, default=DEFAULT_CAP)
    rw.add_argument("--dry-run", action="store_true")
    la = sub.add_parser("launch")
    la.add_argument("req")
    la.add_argument("--dry-run", action="store_true")
    la.add_argument("--model", default="sonnet")
    la.add_argument("--max-turns", type=int, default=25)
    la.add_argument("--max-budget-usd", type=float, default=3.0)
    args = ap.parse_args(argv)

    try:
        if args.cmd == "status":
            print("\n".join(status_table()))
            return 0
        if args.cmd == "concurrency":
            print(concurrency())
            return 0
        rows, cfg = load_rows(), load_config()
        queue = ready(rows, cfg["depends"], cfg["order"])
        if args.cmd == "ready":
            print(f"{len(queue)} ready")
            print("\n".join(_fmt(r) for r in queue))
            if args.all:
                held = [r for r in ready(rows, cfg["depends"], cfg["order"], True)
                        if waiting_on_owner(r)]
                print(f"waiting on owner: {len(held)}")
                for r in held:
                    print(_fmt(r))
        elif args.cmd == "waves":
            for i, wave in enumerate(waves(queue, args.cap), 1):
                print(f"wave {i}: " + ", ".join(r["id"] for r in wave))
        elif args.cmd == "launch":
            row = next((r for r in rows if r["id"] == args.req), None)
            if row is None:
                raise KernelError(f"unknown requirement {args.req!r}")
            res = launch(row, model=args.model, max_turns=args.max_turns,
                         max_budget=args.max_budget_usd, dry_run=args.dry_run)
            print(json.dumps(res, indent=2) if args.dry_run else f"started {row['id']} in {res.wt}")
        else:  # run-wave
            n = concurrency(cap=args.cap)
            first = waves(queue, args.cap)[0][:n] if queue and n else []
            if not first:
                print(f"nothing to run (ready={len(queue)}, concurrency={n})")
                return 0
            started = [launch(r, dry_run=args.dry_run) for r in first]
            if args.dry_run:
                print(json.dumps(started, indent=2))
                return 0
            for rec in watch(started):
                print(f"{rec['requirement']}: {rec['state']}")
    except KernelError as exc:
        print(f"build_kernel: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
