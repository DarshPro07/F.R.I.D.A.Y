"""Fresh-controller build program (BH-OPT-02 item 1): waves run in code, not in a model session.

    python scripts/build_program.py [--max-waves 5] [--max-usd 10] [--cap N]
                                    [--priority P0] [--dry-run]

Each wave: ready queue -> launch -> watch -> integrate -> persist state. A model
is only ever a worker or a verifier; anything that needs judgement becomes an
escalation in data/build_state.json. The program owns the block between the
build-program markers in docs/implementation/PRD_CURRENT_STATE.md and nothing
else in that file.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT))

import build_integrate  # noqa: E402
import build_kernel  # noqa: E402
import prd_ledger as PL  # noqa: E402

STATE_FILE = ROOT / "data" / "build_state.json"
STATE_DOC = ROOT / "docs" / "implementation" / "PRD_CURRENT_STATE.md"
DOCS_DIR = "docs/implementation"
BEGIN = b"<!-- build-program:begin -->"
END = b"<!-- build-program:end -->"
MAX_ATTEMPTS = 2
NO_PROGRESS_WAVES = 2
GIT_S = 60
LEDGER_S = 300
IDENTITY = ("-c", "user.name=DarshPro07", "-c", "user.email=DarshPro07")
LAST_SUITE = {"commit": "258e3c0", "result": "4,753 passed, 0 failed"}


# ------------------------------------------------------------------ helpers

def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def sh(cmd: list[str], cwd: Path, timeout: int = GIT_S) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout)


def git_out(repo: Path, *args: str, runner=sh) -> str:
    return (runner(["git", *args], repo).stdout or "").strip()


def tree_dirty(repo: Path, runner=sh) -> bool:
    return bool(git_out(repo, "status", "--porcelain", "--untracked-files=no", runner=runner))


def atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_bytes(data)
    os.replace(tmp, path)


def park(state: dict, req: str, reason: str, kind: str, detail: str) -> None:
    state["parked"][req] = reason
    state["escalations"].append({"req": req, "kind": kind, "detail": detail, "at": now()})


# -------------------------------------------------------------------- state

def load_state(path: Path) -> dict:
    try:
        old = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        old = {}
    if not isinstance(old, dict):
        old = {}
    return {"last_full_suite": old.get("last_full_suite") or dict(LAST_SUITE),
            "waves": list(old.get("waves") or []), "parked": dict(old.get("parked") or {}),
            "attempts": dict(old.get("attempts") or {}),
            "escalations": list(old.get("escalations") or []),
            "spend_usd_total": float(old.get("spend_usd_total") or 0.0)}


def snapshot(state: dict, rows: list[dict], nxt: list[str], stop: str | None,
             repo: Path, runner=sh) -> dict:
    return {**state, "updated": now(), "head": git_out(repo, "rev-parse", "--short", "HEAD", runner=runner),
            "branch": git_out(repo, "branch", "--show-current", runner=runner),
            "counts": PL.counts(rows), "unresolved_p0": len(PL.unresolved(rows, "P0")),
            "next_ready": nxt, "stop_reason": stop}


def write_state(path: Path, snap: dict) -> None:
    atomic_write(path, (json.dumps(snap, indent=2) + "\n").encode("utf-8"))


# ---------------------------------------------------------------- doc block

def block_lines(snap: dict) -> list[str]:
    last = (snap["waves"] or [{}])[-1]
    suite = snap["last_full_suite"]
    results = ", ".join(f"{k}={v}" for k, v in last.get("results", {}).items()) or "none"
    parked = ", ".join(f"{k} ({v})" for k, v in snap["parked"].items()) or "none"
    esc = snap["escalations"][-10:]
    out = ["## Build program (generated; do not edit by hand)", "",
           f"- HEAD: {snap['head']} on {snap['branch']}, updated {snap['updated']}",
           f"- Counts: {json.dumps(snap['counts'], sort_keys=True)}",
           f"- Unresolved P0: {snap['unresolved_p0']}",
           f"- Last full suite: {suite.get('result')} at {suite.get('commit')}",
           f"- Last wave ({last.get('n', '-')}): {results}",
           f"- Parked: {parked}",
           f"- Open escalations: {len(snap['escalations'])}"]
    out += [f"  - {e['req']} {e['kind']}: {str(e['detail'])[:100]}" for e in esc]
    out.append(f"- Next ready: {', '.join(snap['next_ready']) or 'none'}")
    return out[:40]


def render_doc(data: bytes, lines: list[str]) -> bytes:
    """Replace only the marker block; append it when absent. Bytes outside the
    markers are untouched and the file's own newline style is reused."""
    nl = b"\r\n" if b"\r\n" in data else b"\n"
    block = BEGIN + nl + nl.join(s.encode("utf-8") for s in lines) + nl + END
    start = data.find(BEGIN)
    end = data.find(END, start + 1) if start != -1 else -1
    if start != -1 and end != -1:
        return data[:start] + block + data[end + len(END):]
    if data and not data.endswith(b"\n"):
        data += nl
    return data + block + nl


def write_doc(doc: Path, snap: dict) -> None:
    try:
        data = doc.read_bytes()
    except OSError:
        data = b""
    atomic_write(doc, render_doc(data, block_lines(snap)))


# -------------------------------------------------------------------- waves

def queue_for(rows: list[dict], state: dict, integrated: set[str], worktrees: Path,
              priority: str | None, k=build_kernel, record: bool = True) -> list[dict]:
    """Ready rows minus parked and integrated ids; a row whose worktree dir is
    left over is parked (unless `record` is off, as in a dry run)."""
    cfg = k.load_config()
    out = []
    for row in k.ready(rows, cfg["depends"], cfg["order"]):
        rid = row["id"]
        if rid in state["parked"] or rid in integrated:
            continue
        if priority and row.get("priority") != priority:
            continue
        if (worktrees / rid.lower()).exists():
            if record:
                park(state, rid, "stale worktree", "stale_worktree",
                     f"{worktrees / rid.lower()} already exists")
            continue
        out.append(row)
    return out


def class_of(row: dict, mode: str, k=build_kernel) -> str:
    return "SCOUT" if mode == "SCOUT" else k.budget_class(row)


def pick_wave(queue: list[dict], cap: int, n: int, remaining: float,
              k=build_kernel) -> list[tuple[dict, str, str]]:
    """First wave of the queue, limited to `n` rows and trimmed to the spend left."""
    chosen, total = [], 0.0
    for row in k.waves(queue, cap)[0][:n]:
        mode = k.mode_for(row)
        klass = class_of(row, mode, k)
        cost = k.BUDGETS[klass][1]
        if total + cost > remaining:
            break
        chosen.append((row, mode, klass))
        total += cost
    return chosen


def launch_all(chosen, state: dict, k=build_kernel) -> list:
    runs = []
    for row, mode, klass in chosen:
        try:
            capsule = k.code_map(row) if mode == "SHIP" else ""
            runs.append(k.launch(row, mode=mode, capsule=capsule))
        except k.KernelError as exc:
            park(state, row["id"], "launch failed", "launch", str(exc))
    return runs


def settle(rec: dict, state: dict, integrated: set[str], k=build_kernel, bi=build_integrate) -> tuple[str, float, bool]:
    """Apply one watched run's outcome. Returns (result, spend, made_progress)."""
    req, st = rec["requirement"], rec["state"]
    spend = float(rec.get("cost_usd") or 0.0)
    if st == "DONE" and rec.get("mode") == "SCOUT":
        return "SCOUT_DONE", spend, True
    if st == "DONE":
        try:
            res = bi.integrate(req)
        except k.KernelError as exc:
            park(state, req, "integration refused", "integration", str(exc))
            return "REFUSED", spend, False
        spend += float((res.get("verifier") or {}).get("cost_usd") or 0.0)
        if res["result"] == "INTEGRATED":
            integrated.add(req)
            return "INTEGRATED", spend, True
        park(state, req, res["result"], "integration", json.dumps(res, default=str)[:300])
        return res["result"], spend, False
    if st == "BLOCKED":
        blockers = (rec.get("evidence") or {}).get("blockers") or []
        park(state, req, "blocked", "blocked", "; ".join(map(str, blockers)) or "no blockers recorded")
        return "BLOCKED", spend, False
    state["attempts"][req] = state["attempts"].get(req, 0) + 1
    if state["attempts"][req] >= MAX_ATTEMPTS:
        park(state, req, f"{st.lower()} x{state['attempts'][req]}", "attempts", f"{st} twice")
    return st, spend, False


def run_program(*, max_waves: int = 5, max_usd: float = 10.0, cap: int = build_kernel.DEFAULT_CAP,
                priority: str | None = None, dry_run: bool = False, repo: Path = ROOT,
                state_file: Path = STATE_FILE, state_doc: Path = STATE_DOC,
                worktrees: Path | None = None, k=build_kernel, bi=build_integrate,
                runner=sh, out=print) -> dict:
    if tree_dirty(repo, runner):
        return {"stop_reason": "dirty tree", "waves": []}
    worktrees = worktrees or k._worktrees_root()
    state = load_state(state_file)
    integrated: set[str] = set()
    spent, idle, waves_run, stop = 0.0, 0, 0, None
    while not stop:
        rows = k.load_rows()
        chosen = []
        if waves_run >= max_waves:
            stop = "max waves"
        elif spent >= max_usd:
            stop = "spend"
        elif not (queue := queue_for(rows, state, integrated, worktrees, priority, k, not dry_run)):
            stop = "no ready work"
        elif (n := k.concurrency(cap=cap)) <= 0:
            stop = "pressure"
        elif not (chosen := pick_wave(queue, cap, n, max_usd - spent, k)):
            stop = "spend"
        if stop:
            break
        if dry_run:
            for row, mode, klass in chosen:
                out(f"{row['id']} {mode} {klass}")
            return {"stop_reason": "dry run", "waves": []}
        results, wave_spend, progress = {}, 0.0, False
        for rec in k.watch(launch_all(chosen, state, k)):
            res, cost, ok = settle(rec, state, integrated, k, bi)
            results[rec["requirement"]] = res
            wave_spend += cost
            progress = progress or ok
        spent += wave_spend
        waves_run += 1
        idle = 0 if progress else idle + 1
        state["spend_usd_total"] = round(state["spend_usd_total"] + wave_spend, 4)
        state["waves"].append({"n": len(state["waves"]) + 1, "reqs": [r["id"] for r, _, _ in chosen],
                               "results": results, "spend": round(wave_spend, 4)})
        stop = "no progress" if idle >= NO_PROGRESS_WAVES else None
        rows = k.load_rows()
        snap = snapshot(state, rows, next_ready(rows, state, integrated, cap, k), stop, repo, runner)
        write_state(state_file, snap)
        write_doc(state_doc, snap)
    if dry_run:
        out(f"nothing to plan: {stop}")
        return {"stop_reason": stop, "waves": []}
    snap = snapshot(state, rows, next_ready(rows, state, integrated, cap, k), stop, repo, runner)
    write_state(state_file, snap)
    write_doc(state_doc, snap)
    return finish(snap, state, repo, state_file, state_doc, runner)


def next_ready(rows: list[dict], state: dict, integrated: set[str], cap: int, k=build_kernel) -> list[str]:
    cfg = k.load_config()
    return [r["id"] for r in k.ready(rows, cfg["depends"], cfg["order"])
            if r["id"] not in state["parked"] and r["id"] not in integrated][:cap]


def finish(snap: dict, state: dict, repo: Path, state_file: Path, state_doc: Path,
           runner=sh) -> dict:
    """Re-render the ledger docs, then commit what changed under docs/implementation."""
    n = len(state["waves"])
    r = runner([build_kernel.VENV_PY, "scripts/prd_ledger.py", "--set", "v7"], repo, LEDGER_S)
    if r.returncode != 0:
        park_detail = (r.stderr or r.stdout or "").strip()[-300:]
        state["escalations"].append({"req": "-", "kind": "ledger_render", "detail": park_detail, "at": now()})
        snap = {**snap, "escalations": state["escalations"]}
        write_state(state_file, snap)
        write_doc(state_doc, snap)
        return snap
    changed = git_out(repo, "diff", "--name-only", "--", DOCS_DIR, runner=runner)
    if changed:
        runner(["git", "add", "-u", "--", DOCS_DIR], repo)
        runner(["git", *IDENTITY, "commit", "-m",
                f"docs(state): build program stopped after wave {n} ({snap['stop_reason']})"], repo)
    return snap


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--max-waves", type=int, default=5)
    ap.add_argument("--max-usd", type=float, default=10.0)
    ap.add_argument("--cap", type=int, default=build_kernel.DEFAULT_CAP)
    ap.add_argument("--priority", choices=sorted(build_kernel._PRIORITY))
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args(argv)
    res = run_program(max_waves=a.max_waves, max_usd=a.max_usd, cap=a.cap,
                      priority=a.priority, dry_run=a.dry_run)
    print(f"stopped: {res['stop_reason']}")
    return 1 if res["stop_reason"] == "dirty tree" else 0


if __name__ == "__main__":
    sys.exit(main())
