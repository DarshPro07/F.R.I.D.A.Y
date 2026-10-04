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
import hashlib
import contextlib
import time
import json
import os
import subprocess
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT))

import build_integrate  # noqa: E402
import build_kernel  # noqa: E402
import prd_ledger as PL  # noqa: E402

STATE_FILE = ROOT / "data" / "build_state.json"
LOCK_FILE = ROOT / "data" / "build_program.lock"
BUSY = "another program is running"
BUSY_EXIT = 3
STATE_DOC = ROOT / "docs" / "implementation" / "PRD_CURRENT_STATE.md"
DOCS_DIR = "docs/implementation"
BEGIN = b"<!-- build-program:begin -->"
END = b"<!-- build-program:end -->"
MAX_ATTEMPTS = 2
MAX_REPAIRS = len(build_kernel.CASCADE)  # repair, advisor, advised implementer
# Rejections a worker can answer by editing its own change; CONFLICT is a merge
# problem and an integrator refusal is a refusal, so neither is sent back.
REPAIRABLE = ("VERIFIER_FAIL", "NOT_RED", "NOT_GREEN", "NO_TEST", "FAILED_INTEGRATION")
NO_PROGRESS_WAVES = 2
#: A wave without progress that spends this share of the run's budget stops
#: the run to replan instead of spending the rest the same way.
COSTLY_SHARE = 0.25
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


# --------------------------------------------------------------------- lock

def pid_alive(pid: int) -> bool:
    """Whether a process with this pid exists. Never signals it: on Windows
    os.kill terminates the target."""
    if pid <= 0:
        return False
    psutil = build_kernel.psutil
    if psutil is not None:
        return psutil.pid_exists(pid)
    if os.name == "nt":
        import ctypes
        handle = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)  # QUERY_LIMITED_INFORMATION
        if not handle:
            return False
        ctypes.windll.kernel32.CloseHandle(handle)
        return True
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def acquire_lock(path: Path) -> bool:
    """Take the exclusive program lock (O_CREAT|O_EXCL, holding our pid and start
    time). A lock whose pid is not alive is stale and replaced; False when held."""
    path.parent.mkdir(parents=True, exist_ok=True)
    for _ in range(2):
        try:
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            try:
                holder = int(json.loads(path.read_text(encoding="utf-8")).get("pid") or 0)
            except (OSError, ValueError, AttributeError):
                holder = 0  # unreadable or half-written: nobody holds it
            if pid_alive(holder):
                return False
            try:
                path.unlink()
            except FileNotFoundError:
                pass
            continue
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump({"pid": os.getpid(), "started": now()}, fh)
        return True
    return False


def release_lock(path: Path) -> None:
    try:
        path.unlink()
    except FileNotFoundError:
        pass


# -------------------------------------------------------------------- state

#: The controller's queued state edits, applied by the next run under the program lock.
INBOX = "controller_inbox.jsonl"


def apply_inbox(state: dict, runs_dir: Path) -> list[str]:
    """Apply the controller's queued edits - {"action": "unpark"|"requeue", "req", "step", "why"} per
    line - then archive the inbox. Edits written to the state file while a run held it were lost to
    that run's next write, and waiters racing the lock died to quoting bugs and memory reaping
    (2026-10-02..04): one reader, under the lock, removes the race."""
    path = Path(runs_dir) / INBOX
    if not path.exists():
        return []
    applied = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            cmd = json.loads(line)
        except ValueError:
            continue
        req, action = cmd.get("req") if isinstance(cmd, dict) else None, (cmd or {}).get("action")
        if not req or action not in ("unpark", "requeue"):
            continue
        state["parked"].pop(req, None)
        state["partials"].pop(req, None)
        state["attempts"].pop(req, None)
        state["cascade"].pop(req, None)
        if action == "requeue":
            state["repairs"][req] = int(cmd.get("step") or 0)
            state["repair_queue"][req] = {"result": str(cmd.get("result") or "CONTROLLER"), "at": now()}
        else:
            state["repairs"].pop(req, None)
            state["repair_queue"].pop(req, None)
        state["escalations"].append({"req": req, "kind": "resolved", "at": now(),
                                     "detail": f"controller {action}: {cmd.get('why', '')}"})
        applied.append(f"{action} {req}")
    path.replace(path.with_name(f"controller_inbox.applied-{int(time.time())}.jsonl"))
    return applied


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
            "repairs": dict(old.get("repairs") or {}),
            "repair_queue": dict(old.get("repair_queue") or {}),
            "cascade": dict(old.get("cascade") or {}),
            "partials": dict(old.get("partials") or {}),
            "capsule_decision": old.get("capsule_decision"),
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
              priority: str | None, k=build_kernel, record: bool = True,
              resumed: set[str] | None = None) -> list[dict]:
    """Ready rows minus parked and integrated ids. A row whose worktree dir is
    left over stays queued when its killed run is resumable and has not been
    resumed in this program run; otherwise it is parked (unless `record` is
    off, as in a dry run)."""
    cfg = k.load_config()
    out = []
    for row in k.ready(rows, cfg["depends"], cfg["order"]):
        rid = row["id"]
        if rid in state["parked"] or rid in integrated or rid in state["repair_queue"]:
            continue
        if priority and row.get("priority") != priority:
            continue
        if (worktrees / rid.lower()).exists():
            if rid not in (resumed or set()) and k.resumable(worktrees / rid.lower()):
                out.append(row)
                continue
            if record:
                park(state, rid, "stale worktree", "stale_worktree",
                     f"{worktrees / rid.lower()} already exists")
            continue
        out.append(row)
    return out


def class_of(row: dict, mode: str, k=build_kernel) -> str:
    return "SCOUT" if mode == "SCOUT" else k.budget_class(row)


def admission_candidates(queue: list[dict], repairs: list[tuple[str, str]], k=build_kernel) -> list[tuple]:
    """Queued repairs first (they run first), then the ready rows in order, each as
    (row, mode, class) so admission prices every job by its own memory history."""
    out = [({"id": rid}, "SHIP", klass) for rid, klass in repairs]
    for row in queue:
        mode = k.mode_for(row)
        out.append((row, mode, class_of(row, mode, k)))
    return out


def pick_wave(queue: list[dict], cap: int, n: int, remaining: float,
              k=build_kernel, first_within: float | None = None) -> list[tuple[dict, str, str]]:
    """First wave of the queue, limited to `n` rows and trimmed to the spend left.
    `first_within` lets the wave's first row use that larger budget: a wave cap
    limits how many workers run together, never whether one can run at all."""
    chosen, total = [], 0.0
    waves = k.waves(queue, cap)
    if not waves:  # admission may leave nothing but repairs the run cannot afford
        return chosen
    for row in waves[0][:n]:
        mode = k.mode_for(row)
        klass = class_of(row, mode, k)
        cost = k.BUDGETS[klass][1]
        first_fits = not chosen and first_within is not None and cost <= first_within
        if total + cost > remaining and not first_fits:
            break
        chosen.append((row, mode, klass))
        total += cost
    return chosen


def capsule_arm(req: str, state: dict) -> bool:
    """Whether this requirement's worker gets the code capsule: the program's
    decision once made, else a stable per-id coin so the A/B survives restarts."""
    decision = state.get("capsule_decision")
    if decision in ("on", "off"):
        return decision == "on"
    return hashlib.sha256(req.encode("utf-8")).digest()[0] % 2 == 1


def gate(k, req: str, klass: str, mode: str, state: dict, runs_dir: Path | None = None,
         cap: int = build_kernel.DEFAULT_CAP, kind: str = "SHIP") -> str | None:
    """QUEUED -> admission -> memory lease -> spend lease for one chosen job. Returns
    its attempt id when it may spawn; None (job WAITING_RESOURCES, reason in state)
    when not. A spend refusal gives the memory lease back and sets state['budget']."""
    runs_dir = runs_dir or k.RUNS_DIR
    attempt_id = k.job_queue(req, klass, mode, runs_dir)
    ok, why = k.reserve(attempt_id, req, klass, mode, cap=cap, runs_dir=runs_dir)
    if ok:
        ok, why = k.spend_reserve(attempt_id, "launch", req, kind, k.launch_usd(klass, kind), runs_dir)
        if ok:
            return attempt_id
        k.lease_release(attempt_id, runs_dir)
        state["budget"] = why
    else:
        state["resources"] = why
    k.job_wait(attempt_id, why, runs_dir)
    state.setdefault("waiting", {})[req] = why
    return None


def launch_all(chosen, state: dict, k=build_kernel, worktrees: Path | None = None,
               resumed: set[str] | None = None, runs_dir: Path | None = None,
               cap: int = build_kernel.DEFAULT_CAP) -> list:
    runs = []
    for row, mode, klass in chosen:
        rid = row["id"]
        kind = ("SCOUT" if mode == "SCOUT" else
                "RESUME" if worktrees is not None and (worktrees / rid.lower()).exists() else "SHIP")
        attempt_id = gate(k, rid, klass, mode, state, runs_dir, cap, kind)
        if attempt_id is None:
            continue
        state.get("waiting", {}).pop(rid, None)
        try:
            if worktrees is not None and (worktrees / rid.lower()).exists():
                runs.append(k.resume(rid, worktrees=worktrees))
                state["attempts"][rid] = state["attempts"].get(rid, 0) + 1
                if resumed is not None:
                    resumed.add(rid)
                continue
            capsule = k.code_map(row) if mode == "SHIP" and capsule_arm(rid, state) else ""
            runs.append(k.launch(row, mode=mode, capsule=capsule))
        except k.KernelError as exc:
            k.job_close(attempt_id, "FAILED", f"launch failed: {exc}", runs_dir or k.RUNS_DIR)
            park(state, row["id"], "launch failed", "launch", str(exc))
    return runs


def pending_repairs(rows: list[dict], state: dict, integrated: set[str],
                    priority: str | None, k=build_kernel) -> list[tuple[str, str]]:
    """Queued repairs as (req, budget class), oldest first; a repair costs half its class budget."""
    by_id = {r["id"]: r for r in rows}
    out = []
    for rid in sorted(state["repair_queue"], key=lambda r: state["repair_queue"][r].get("at", "")):
        if rid in state["parked"] or rid in integrated:
            continue
        row = by_id.get(rid)
        if priority and (row or {}).get("priority") != priority:
            continue
        strong = state["repairs"].get(rid, 0) in (1, 2)  # the escalation and the advisor run on P0 limits
        out.append((rid, "P0" if strong else k.budget_class(row) if row else "NORMAL"))
    return out


def launch_repairs(repairs: list[tuple[str, str]], state: dict, k=build_kernel,
                   worktrees: Path | None = None, runs_dir: Path | None = None,
                   cap: int = build_kernel.DEFAULT_CAP) -> list:
    """Launch each queued requirement's next cascade step: repair (resume when
    localized, else fresh), a fresh strong-model escalation, a read-only advisor,
    then a fresh normal-model implementer on the advisor's packet. Each step is
    one job; the position is recorded in state['cascade']."""
    runs = []
    for rid, klass in repairs:
        spent = state["repairs"].get(rid, 0)
        step = k.agent_utility({"id": rid}, {"failures": spent})[0]["step"]
        attempt_id = None
        if step in ("ESCALATE", "ADVISOR", "IMPLEMENTER", "REPAIR"):
            attempt_id = gate(k, rid, klass, "SHIP", state, runs_dir, cap, step)
            if attempt_id is None:  # stays queued: no repair has been consumed
                continue
        state.get("waiting", {}).pop(rid, None)
        state["repair_queue"].pop(rid, None)
        state["repairs"][rid] = spent + 1
        state["cascade"][rid] = {"step": step, "n": spent + 1}
        try:
            if step == "ESCALATE":
                runs.append(k.escalate(rid, worktrees=worktrees))
            elif step == "ADVISOR":
                runs.append(k.advise(rid, worktrees=worktrees))
            elif step == "IMPLEMENTER":
                runs.append(k.repair(rid, worktrees=worktrees, advice=k.load_advice(rid)))
            elif step == "REPAIR":
                runs.append(k.repair(rid, worktrees=worktrees))
            else:
                park(state, rid, "cascade exhausted", "cascade", f"{spent} repairs spent")
        except k.KernelError as exc:
            k.job_close(attempt_id, "FAILED", f"{step.lower()} failed: {exc}", runs_dir or k.RUNS_DIR)
            park(state, rid, f"{step.lower()} failed", "repair", str(exc))
    return runs


def adopt_jobs(k=build_kernel, runs_dir: Path | None = None, repo: Path = ROOT) -> tuple[list, list[dict]]:
    """Take over the STARTED jobs a dead controller left behind, never relaunching
    one. A live pid is returned to be watched; a dead pid whose stream holds a
    result is finalized (its record returned); a dead pid with no result is marked
    FAILED and, when resumable, the queue resumes it from its session."""
    runs_dir = runs_dir or k.RUNS_DIR
    live, finished = [], []
    for job in k.jobs(runs_dir):
        if job.get("state") in ("QUEUED", "WAITING_RESOURCES"):
            # nothing was spawned: the controller that queued it is gone, the next wave re-queues
            k.job_close(job["attempt_id"], "FAILED", "controller died before the job spawned", runs_dir)
            continue
        if job.get("state") != "STARTED":
            continue
        if pid_alive(int(job.get("pid") or 0)):
            live.append(k.adopt(job, alive=pid_alive, runs_dir=runs_dir, repo=repo))
            continue
        run = k.adopt(job, alive=pid_alive, runs_dir=runs_dir, repo=repo)
        if k.has_result(run.wt, run.stream_offset):
            finished.append(k.finalize(run, None, runs_dir))
        else:
            k.job_close(job["attempt_id"], "FAILED", "controller died; process gone before a result", runs_dir)
    return live, finished


def recover_crash(state: dict, req: str, head: str, exc: Exception, repo: Path, runner=sh) -> str:
    """The integrator died. If it left a commit on the branch (a cherry-pick not yet
    verified), put HEAD back; otherwise stop the run, because the branch holds
    something nobody checked. Returns the settle result."""
    detail = repr(exc)[:300]
    try:
        now_head = git_out(repo, "rev-parse", "HEAD", runner=runner)
        if now_head == head:
            park(state, req, "integration crashed", "integration_crash", detail)
            return "INTEGRATION_CRASH"
        if git_out(repo, "rev-parse", "HEAD^", runner=runner) == head:
            reset = runner(["git", "reset", "--hard", head], repo, timeout=GIT_S)
            if reset.returncode == 0:
                park(state, req, "integration crashed", "integration_crash", detail)
                return "INTEGRATION_CRASH"
            detail += f"; reset failed: {(reset.stderr or '').strip()[-120:]}"
    except Exception as inner:  # git itself is unusable: nothing is known about the branch
        detail += f"; recovery failed: {inner!r}"[:200]
    park(state, req, "integration crashed, branch not restored", "integration_crash_unrecovered", detail)
    return "INTEGRATION_CRASH_UNRECOVERED"


def settle(rec: dict, state: dict, integrated: set[str], k=build_kernel, bi=build_integrate,
           repo: Path = ROOT, runner=sh) -> tuple[str, float, bool]:
    """Apply one watched run's outcome. Returns (result, spend, settled).

    `spend` is the verifier's cost only: a worker's cost is read from the
    kernel's invocation ledger (wave_cost / run_cost), never from `rec`.

    `settled` is False only when the worker itself failed (FAILED, TIMEOUT,
    STALLED) or the integrator refused to run: those are what a broken
    environment looks like. A rejected or blocked requirement is settled - its
    blocker is persisted and the program moves on to independent work."""
    req, st = rec["requirement"], rec["state"]
    spend = 0.0
    if st == "DONE" and rec.get("mode") == "SCOUT":
        return "SCOUT_DONE", spend, True
    if rec.get("kind") == "ADVISOR":  # a diagnosis, never integrated: the implementer is next
        if st == "DONE":
            state["repair_queue"][req] = {"result": "ADVISED", "at": now()}
            return "ADVISED", spend, True
        park(state, req, f"advisor {st.lower()}", "advisor", f"the advisor job ended {st}")
        return "ADVISOR_FAILED", spend, False
    if st == "DONE":
        head = git_out(repo, "rev-parse", "HEAD", runner=runner)
        try:
            res = bi.integrate(req)
            # Main moved under a finished change: rebase it when that is clean, then try once more.
            if res["result"] == "REBASE_REQUIRED" and k.rebase_run(req, repo=repo):
                head = git_out(repo, "rev-parse", "HEAD", runner=runner)
                res = bi.integrate(req)
        except k.KernelError as exc:
            park(state, req, "integration refused", "integration", str(exc))
            return "REFUSED", spend, False
        except Exception as exc:  # any crash may sit between the cherry-pick and its guarded reset
            return recover_crash(state, req, head, exc, repo, runner), spend, False
        spend += float((res.get("verifier") or {}).get("cost_usd") or 0.0)
        if res["result"] == "INTEGRATED":
            integrated.add(req)
            # A PARTIAL row stays ready; cap how often a run may pay for it.
            if res.get("ledger_status") == "PARTIAL":
                state["partials"][req] = state["partials"].get(req, 0) + 1
                if state["partials"][req] >= MAX_ATTEMPTS:
                    park(state, req, f"partial x{state['partials'][req]}", "partial",
                         "integrated twice without closing its gaps")
            return "INTEGRATED", spend, True
        if res["result"] in REPAIRABLE and state["repairs"].get(req, 0) < MAX_REPAIRS:
            state["repair_queue"][req] = {"result": res["result"], "at": now()}
            return res["result"], spend, True
        kind = {"TEST_TIMEOUT": "test_timeout", "REBASE_REQUIRED": "rebase"}.get(res["result"], "integration")
        park(state, req, res["result"], kind, json.dumps(res, default=str)[:300])
        return res["result"], spend, True
    if st == "BLOCKED":
        blockers = (rec.get("evidence") or {}).get("blockers") or []
        park(state, req, "blocked", "blocked", "; ".join(map(str, blockers)) or "no blockers recorded")
        return "BLOCKED", spend, True
    if req in state["cascade"] and state["repairs"].get(req, 0) < MAX_REPAIRS:
        # A cascade step's worker failed (e.g. its turn cap): hand on to the next step
        # rather than dropping out of the cascade with the work it left on disk.
        state["repair_queue"][req] = {"result": f"WORKER_{st}", "at": now()}
        return st, spend, False
    if st in k.WORKER_FAILURES and k.has_work(rec):
        # A worker that ran out with its work on disk enters the repair cascade: a fresh
        # capsule session finishes it in the same worktree. As a plain attempt the next run
        # could only park the worktree as stale (2026-10-03, SEC-005).
        state["repair_queue"][req] = {"result": f"WORKER_{st}", "at": now()}
        return st, spend, False
    if st in k.WORKER_FAILURES:
        # Nothing on disk to keep: drop the worktree so the retry launches (a bigger budget class
        # when it ran out of turns) instead of the next run parking it as stale (2026-10-04, BROW-004).
        k.discard_worktree(rec, repo=repo)
    state["attempts"][req] = state["attempts"].get(req, 0) + 1
    if state["attempts"][req] >= MAX_ATTEMPTS:
        park(state, req, f"{st.lower()} x{state['attempts'][req]}", "attempts", f"{st} twice")
    return st, spend, False


def run_program(*, max_waves: int = 5, max_usd: float = 10.0, cap: int = build_kernel.DEFAULT_CAP,
                max_wave_usd: float | None = None,
                priority: str | None = None, dry_run: bool = False, repo: Path = ROOT,
                state_file: Path = STATE_FILE, state_doc: Path = STATE_DOC,
                worktrees: Path | None = None, k=build_kernel, bi=build_integrate,
                runner=sh, out=print, lock_file: Path | None = None,
                runs_dir: Path | None = None, checkout_turn=None) -> dict:
    lock = lock_file or state_file.parent / LOCK_FILE.name
    if not acquire_lock(lock):
        return {"stop_reason": BUSY, "waves": []}
    run_id = uuid.uuid4().hex[:12]
    saved = {key: os.environ.get(key) for key in ("FRIDAY_BUILD_RUN_ID", "FRIDAY_BUILD_WAVE_ID")}
    os.environ["FRIDAY_BUILD_RUN_ID"] = run_id
    try:
        return _run_locked(run_id, max_waves=max_waves, max_usd=max_usd, cap=cap,
                           max_wave_usd=max_wave_usd, priority=priority, dry_run=dry_run,
                           repo=repo, state_file=state_file, state_doc=state_doc,
                           worktrees=worktrees, k=k, bi=bi, runner=runner, out=out,
                           runs_dir=runs_dir, checkout_turn=checkout_turn)
    finally:
        for key, val in saved.items():
            if val is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = val
        release_lock(lock)


def _run_locked(run_id: str, *, max_waves, max_usd, cap, max_wave_usd, priority, dry_run, repo,
                state_file, state_doc, worktrees, k, bi, runner, out, runs_dir=None, checkout_turn=None) -> dict:
    if tree_dirty(repo, runner):
        return {"stop_reason": "dirty tree", "waves": []}
    worktrees = worktrees or k._worktrees_root()
    state = load_state(state_file)
    if not dry_run:
        for done in apply_inbox(state, runs_dir or k.RUNS_DIR):
            out(f"inbox: {done}")
    integrated: set[str] = set()
    resumed: set[str] = set()
    if not dry_run:  # jobs a dead controller left STARTED are adopted, never relaunched
        live, adopted = adopt_jobs(k, runs_dir, repo)
        adopted += k.watch(live, runs_dir=runs_dir or k.RUNS_DIR) if live else []
        for rec in adopted:
            settle(rec, state, integrated, k, bi, repo, runner)
    verifier_spent = 0.0  # verifier invocations are not in the worker ledger
    spent, idle, waves_run, stop = 0.0, 0, 0, None
    wave_cap = max_usd / 2 if max_wave_usd is None else max_wave_usd
    while not stop:
        rows = k.load_rows()
        chosen, fixes = [], []
        repairs = pending_repairs(rows, state, integrated, priority, k)
        if waves_run >= max_waves:
            stop = "max waves"
        elif spent >= max_usd:
            stop = "spend"
        elif not (queue := queue_for(rows, state, integrated, worktrees, priority, k, not dry_run, resumed)) \
                and not repairs:
            stop = "no ready work"
        elif not (fit := k.admissible(admission_candidates(queue, repairs, k), cap))[0]:
            stop = "pressure"
            state["resources"] = fit[1]
        else:
            # Only what admission would start now: a job too big for the memory left
            # waits this wave, and the lighter work behind it goes (owner v12).
            fits = set(fit[0])
            n = len(fit[0])
            repairs = [(rid, klass) for rid, klass in repairs if rid in fits]
            queue = [row for row in queue if row["id"] in fits]
            left_run = max_usd - spent
            left = min(left_run, wave_cap)
            for rid, klass in repairs[:n]:
                cost = k.BUDGETS[klass][1] / 2
                if cost > left and (fixes or cost > left_run):
                    break
                fixes.append((rid, klass))
                left -= cost
            chosen = (pick_wave(queue, cap, n - len(fixes), left, k,
                                first_within=None if fixes else left_run)
                      if n > len(fixes) else [])
            if not fixes and not chosen:
                stop = "spend"
        if stop:
            break
        if dry_run:
            for rid, klass in fixes:
                out(f"{rid} REPAIR {klass}")
            for row, mode, klass in chosen:
                out(f"{row['id']} {mode} {klass}")
            return {"stop_reason": "dry run", "waves": []}
        results, wave_verifier, progress = {}, 0.0, False
        wave_id = f"{run_id}-w{len(state['waves']) + 1}"
        os.environ["FRIDAY_BUILD_WAVE_ID"] = wave_id
        state.pop("budget", None)
        runs = (launch_repairs(fixes, state, k, worktrees, runs_dir, cap)
                + launch_all(chosen, state, k, worktrees, resumed, runs_dir, cap))
        if not runs and any(rid in state.get("waiting", {})
                            for rid in [r for r, _ in fixes] + [r["id"] for r, _, _ in chosen]):
            # Admission, the lease recheck or the day's spend lease refused every job: spawn
            # nothing. Memory frees up within the run's day; the spend budget does not.
            stop = "day budget" if state.get("budget") else "pressure"
            break
        crashed = False
        for rec in k.watch(runs):
            res, cost, ok = settle(rec, state, integrated, k, bi, repo, runner)
            results[rec["requirement"]] = res
            wave_verifier += cost
            progress = progress or ok
            if res == "INTEGRATION_CRASH_UNRECOVERED":
                crashed = True  # the branch is unverified: integrate nothing more
                break
        verifier_spent += wave_verifier
        wave_spend = k.wave_cost(wave_id) + wave_verifier
        spent = k.run_cost(run_id) + verifier_spent
        waves_run += 1
        idle = 0 if progress else idle + 1
        state["spend_usd_total"] = round(state["spend_usd_total"] + wave_spend, 4)
        state["waves"].append({"n": len(state["waves"]) + 1, "reqs": [rid for rid, _ in fixes] + [r["id"] for r, _, _ in chosen],
                               "results": results, "spend": round(wave_spend, 4)})
        stop = "no progress" if idle >= NO_PROGRESS_WAVES else None
        if not progress and wave_spend >= COSTLY_SHARE * max_usd:
            stop = "costly wave without progress"
        if crashed:
            stop = "integration crash"
        rows = k.load_rows()
        snap = snapshot(state, rows, next_ready(rows, state, integrated, cap, k), stop, repo, runner)
        # Only the untracked state file between waves: the next wave's
        # integration refuses a checkout with tracked changes, and the
        # current-state doc is tracked. It is written once, below.
        write_state(state_file, snap)
    if dry_run:
        out(f"nothing to plan: {stop}"
            + (f" ({state['resources']})" if stop == "pressure" and state.get("resources") else ""))
        return {"stop_reason": stop, "waves": []}
    # Waiting for memory is not a failed run: say what it waits for.
    state["status"] = "WAITING_RESOURCES" if stop == "pressure" else "STOPPED"
    if stop != "pressure":
        state.pop("resources", None)
    snap = snapshot(state, rows, next_ready(rows, state, integrated, cap, k), stop, repo, runner)
    write_state(state_file, snap)
    if waves_run == 0:
        # Nothing was built: only the untracked state file changes. Committing the
        # doc here made an empty commit on every pressure stop.
        return snap
    # The state doc, the ledger render and the docs commit write tracked files in the
    # shared checkout, so they take the same merge-queue turn as an integration: an
    # integrator checking for a clean tree must never see them half-written.
    with (checkout_turn or merge_queue_turn)(f"program:{os.getpid()}"):
        write_doc(state_doc, snap)
        return finish(snap, state, repo, state_file, state_doc, runner)


def next_ready(rows: list[dict], state: dict, integrated: set[str], cap: int, k=build_kernel) -> list[str]:
    cfg = k.load_config()
    return [r["id"] for r in k.ready(rows, cfg["depends"], cfg["order"])
            if r["id"] not in state["parked"] and r["id"] not in integrated][:cap]


@contextlib.contextmanager
def merge_queue_turn(label: str):
    """Hold the integrations' merge-queue turn while the program writes tracked files."""
    queue = build_integrate.MergeQueue(build_kernel.RUNS_DIR)
    queue.enqueue(label, time.time())
    try:
        with queue.turn(label):
            yield
    finally:
        queue.finish(label, "DONE")


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
        for cmd in (["git", "add", "-u", "--", DOCS_DIR],
                    ["git", *IDENTITY, "commit", "-m",
                     f"docs(state): build program stopped after wave {n} ({snap['stop_reason']})"]):
            r = runner(cmd, repo)
            if r.returncode != 0:
                state["escalations"].append({"req": "-", "kind": "docs_commit", "at": now(),
                                             "detail": (r.stderr or r.stdout or "").strip()[-300:]})
                snap = {**snap, "escalations": state["escalations"]}
                write_state(state_file, snap)
                break
    return snap


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--max-waves", type=int, default=5)
    ap.add_argument("--max-usd", type=float, default=10.0)
    ap.add_argument("--max-wave-usd", type=float, default=None,
                    help="budget one wave may plan for (default: half of --max-usd)")
    ap.add_argument("--cap", type=int, default=build_kernel.DEFAULT_CAP)
    ap.add_argument("--priority", choices=sorted(build_kernel._PRIORITY))
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args(argv)
    res = run_program(max_waves=a.max_waves, max_usd=a.max_usd, cap=a.cap,
                      max_wave_usd=a.max_wave_usd,
                      priority=a.priority, dry_run=a.dry_run)
    print(f"stopped: {res['stop_reason']}")
    if res["stop_reason"] == BUSY:
        return BUSY_EXIT
    return 1 if res["stop_reason"] == "dirty tree" else 0


if __name__ == "__main__":
    sys.exit(main())
