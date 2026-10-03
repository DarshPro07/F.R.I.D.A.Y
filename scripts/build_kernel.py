"""Deterministic build scheduler (BH-OPT-01 step 4): code decides, no model in the loop.

    python scripts/build_kernel.py ready
    python scripts/build_kernel.py waves [--cap N]
    python scripts/build_kernel.py concurrency
    python scripts/build_kernel.py qualify
    python scripts/build_kernel.py resume REQ
    python scripts/build_kernel.py roi
    python scripts/build_kernel.py repair REQ
    python scripts/build_kernel.py escalate REQ [--model opus] [--packet FILE]
    python scripts/build_kernel.py launch REQ [--dry-run] [--model M] [--max-turns N] [--max-budget-usd X]
                                     [--mode SHIP|SCOUT] [--class MICRO|NORMAL|COMPLEX|P0]
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
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT))

import code_capsule  # noqa: E402
import prd_ledger as PL  # noqa: E402

try:
    import psutil
except ImportError:  # worker memory is then reported as unmeasured
    psutil = None

DEPS_FILE = ROOT / "docs" / "plans" / "build-harness" / "dependencies.json"
RUNS_DIR = ROOT / "data" / "build_runs"
VENV_PY = "F:/F.R.I.D.A.Y/.venv-build/Scripts/python.exe"
DEFAULT_CAP = 2
HOST_PROFILE = RUNS_DIR / "host_profile.json"
DEFAULT_WORKER_GB = 1.5
RSS_WINDOW = 10
#: Append-only worker peak history; run records are overwritten per requirement.
PEAKS_FILE = "peaks.jsonl"
#: Append-only invocation ledger: one line per launch/resume/repair/escalate.
#: Every cost and ROI figure is a projection of it, never of a run record.
INVOCATIONS_FILE = "invocations.jsonl"
KINDS = ("SHIP", "SCOUT", "RESUME", "REPAIR", "ESCALATE", "ADVISOR")
JOBS_DIR = "jobs"
JOB_STATES = ("QUEUED", "STARTED", "WAITING_RESOURCES", "COMPLETE", "FAILED")
ROLE_OF_KIND = {"SHIP": "IMPLEMENTER", "RESUME": "IMPLEMENTER", "SCOUT": "SCOUT",
                "REPAIR": "REPAIR", "ESCALATE": "ESCALATE", "ADVISOR": "ADVISOR"}
#: A resumed session whose context grew past this multiple of its predecessor's
#: re-reads too much transcript: start fresh from a capsule instead.
RETRY_GROWTH_MAX = 1.3
ADVISOR_BUDGET = (15, 2.0, 20 * 60)  # turns, USD, wall seconds: a diagnosis, not a build
#: What the n-th repair of a requirement is, counting repairs already spent.
# Owner v12 section 9: a strong model diagnoses, a normal model implements. A full strong-model
# implementation (ESCALATE) hit the 40-turn cap twice on 2026-10-02 (ACT-001 $3.11, EVAL-001
# $2.14) with nothing integrable, so it is a manual command, not an automatic step.
CASCADE = ("REPAIR", "ADVISOR", "IMPLEMENTER")
#: A first repair may resume the session only for a failure this small.
LOCALIZED_FAILING_TESTS = 3
DEFAULT_WALL_S = 45 * 60
DEFAULT_STALL_S = 10 * 60
ALLOWED_TOOLS = ",".join(("Read", "Edit", "Write", "Glob", "Grep",
                          "Bash(git status:*)", "Bash(git diff:*)", "Bash(git log:*)",
                          f"Bash({VENV_PY}:*)"))
_PRIORITY = {"P0": 0, "P1": 1, "P2": 2, "P3": 3}
# Read-only scouting. The one write is the findings file, path-scoped.
# A scout writes nothing: its findings are its final message (a path rule for
# one file never matched the absolute path the Write tool uses).
SCOUT_TOOLS = ",".join(("Read", "Glob", "Grep",
                        "Bash(git status:*)", "Bash(git diff:*)", "Bash(git log:*)"))
# class -> (max turns, max budget USD, wall seconds)
BUDGETS = {"SCOUT": (8, 0.75, 15 * 60), "MICRO": (12, 1.0, 20 * 60),
           "NORMAL": (25, 3.0, 45 * 60), "COMPLEX": (40, 6.0, 75 * 60),
           "P0": (40, 6.0, 75 * 60)}
READ_FILES = {"SCOUT": 12, "MICRO": 6, "NORMAL": 8, "COMPLEX": 12, "P0": 12}
WORKER_PROMPT = "Read .build/packet.md and carry out the requirement it describes."
#: A read-only job (advisor, scout) that ran out of turns has read what it needed; it gets one
#: short resume to write its result (2026-10-02: EVAL-001's advisor hit its cap with nothing written).
WRITE_NOW_TURNS = 3
WRITE_NOW_USD = 0.5
WRITE_NOW_PROMPT = ("Stop reading. From what you have already read, write your final message now: the "
                    "complete packet your instructions asked for. Do not open more files.")
CONTINUE_PROMPT = ("Continue the requirement in .build/packet.md; "
                   "finish and write .build/evidence.json.")
LOCK_NAME = ".worktree.lock"
LOCK_STALE_S = 10 * 60
LOCK_WAIT_S = 120.0


class KernelError(Exception):
    pass


# -------------------------------------------------------- mode and budget

def load_findings(req: str, runs_dir: Path = RUNS_DIR) -> dict | None:
    """The scout's findings for `req`, or None when no scout has run."""
    try:
        data = json.loads((runs_dir / f"{req}.scout.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def code_map(row: dict, runs_dir: Path = RUNS_DIR) -> str:
    """The worker's repo capsule: the row's code surface plus any files its
    scout named; '' when neither is known."""
    findings = load_findings(row["id"], runs_dir) or {}
    files = surface(row) | surface({"files": findings.get("files") or ()})
    return code_capsule.capsule(sorted(files)) if files else ""


def mode_for(row: dict, runs_dir: Path = RUNS_DIR) -> str:
    """SCOUT when the row is too vague to build (no known surface, no cited
    tests, blank acceptance) and no scout has reported yet; otherwise SHIP."""
    decision, _ = agent_utility(row, {"has_findings": load_findings(row["id"], runs_dir) is not None})
    return decision["orient"]


def cascade_step(repairs: int) -> str:
    """The step the next repair is, given how many were already spent; PARK past the last."""
    return CASCADE[repairs] if 0 <= repairs < len(CASCADE) else "PARK"


def agent_utility(row: dict, context: dict | None = None) -> tuple[dict, list[str]]:
    """The one place that decides how an agent job is shaped. Pure: the row and
    the context in, a decision per axis and the reasons out.

    context keys (all optional): has_findings (a scout reported), protected
    (the change touches protected files), attempt (the attempt about to run),
    localized (the failure is small), retry_context_growth (last retry's context
    over its predecessor's), slots (free worker slots), overlap (the file set
    collides with a running job), failures (repairs already spent)."""
    ctx = context or {}
    reasons: list[str] = []
    known = bool(surface(row)) and bool(row.get("tests")) and bool(str(row.get("acceptance", "")).strip())
    if ctx.get("has_findings"):
        orient = "SHIP"
        reasons.append("a scout already reported")
    elif not known:
        orient = "SCOUT"
        reasons.append("surface, tests or acceptance unknown")
    else:
        orient = "SHIP"
        reasons.append("surface, tests and acceptance known")
    if row.get("priority") == "P0" or ctx.get("protected"):
        verify = "VERIFY"
        reasons.append("P0 or protected files: verification pays")
    else:
        verify = "SKIP_VERIFY"
    growth = ctx.get("retry_context_growth")
    if int(ctx.get("attempt") or 1) == 2 and ctx.get("localized") and not (growth and growth > RETRY_GROWTH_MAX):
        session = "RESUME"
        reasons.append("attempt 2 and the failure is localized")
    else:
        session = "FRESH"
        if growth and growth > RETRY_GROWTH_MAX:
            reasons.append(f"retry context grew x{growth}")
    if int(ctx.get("slots") or 1) > 1 and not ctx.get("overlap"):
        parallel = "PARALLEL"
    else:
        parallel = "SEQUENTIAL"
        reasons.append("file overlap" if ctx.get("overlap") else "one slot")
    failures = int(ctx.get("failures") or 0)
    step = cascade_step(failures)
    escalate = "PARK" if step == "PARK" else "ESCALATE" if failures and step in ("ESCALATE", "ADVISOR") else "NONE"
    if failures:
        reasons.append(f"{failures} repair(s) spent: next step {step}")
    return {"orient": orient, "verify": verify, "session": session, "parallel": parallel,
            "escalate": escalate, "step": step}, reasons


def budget_class(row: dict, findings: dict | None = None) -> str:
    if row.get("priority") == "P0":
        return "P0"
    found = (findings or {}).get("budget_class")
    if found in BUDGETS and found != "SCOUT":
        return found
    n = len(surface(row))
    if n >= 4:
        return "COMPLEX"
    return "MICRO" if n <= 1 else "NORMAL"


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
    """The owner holds it: parked by the owner, or its next action is the owner's."""
    return (row.get("status") == PL.PARKED_BY_OWNER
            or str(row.get("next_action", "")).lstrip().startswith("OWNER:"))


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


def host_governor(path: Path = HOST_PROFILE):
    """The governor for this build host: configured from its own qualified
    profile when that still describes this machine, else provisional (its
    first reading is its baseline, which a loaded host mistakes for idle)."""
    from friday import hardware_profile as HP
    from friday.governor import Governor
    profile = HP.stored(path)
    if profile is not None and not HP.changes(profile):
        return Governor(thresholds=HP._caps(profile), baseline=profile.baseline)
    return Governor()


def worker_gb(runs_dir: Path = RUNS_DIR) -> float:
    """Largest measured worker peak (GB) over the last RSS_WINDOW entries of the
    peak history; run records stand in until a history exists."""
    history = []
    try:
        for line in (runs_dir / PEAKS_FILE).read_text(encoding="utf-8").splitlines():
            try:
                peak = json.loads(line).get("peak_rss_mb")
            except (ValueError, AttributeError):
                continue
            if isinstance(peak, (int, float)) and not isinstance(peak, bool):
                history.append(peak)
    except OSError:
        pass
    if history:
        return max(history[-RSS_WINDOW:]) / 1024
    recs = []
    for p in run_records(runs_dir):
        try:
            peak = json.loads(p.read_text(encoding="utf-8")).get("peak_rss_mb")
        except (OSError, ValueError, AttributeError):
            continue
        if isinstance(peak, (int, float)) and not isinstance(peak, bool):
            recs.append((p.stat().st_mtime, peak))
    recent = [mb for _, mb in sorted(recs)[-RSS_WINDOW:]]
    return max(recent) / 1024 if recent else DEFAULT_WORKER_GB


CLASS_WINDOW = 20  # per-class peaks the class prediction looks back over
MIN_CLASS_SAMPLES = 3
LEASES_FILE = "leases.json"
LEASE_LOCK_WAIT_S = 15.0
LEASE_LOCK_STALE_S = 60.0
#: req -> attempt_id of a job whose memory lease is taken and whose process is
#: about to spawn; the Running built for that req adopts the id.
_PENDING_ATTEMPTS: dict[str, str] = {}


def _peak_history(runs_dir: Path = RUNS_DIR) -> list[dict]:
    """Measured worker peaks, oldest first: the peak history, else the run
    records (by modification time) until a history exists."""
    out = []
    try:
        for line in (runs_dir / PEAKS_FILE).read_text(encoding="utf-8").splitlines():
            try:
                item = json.loads(line)
            except ValueError:
                continue
            peak = item.get("peak_rss_mb") if isinstance(item, dict) else None
            if isinstance(peak, (int, float)) and not isinstance(peak, bool):
                out.append(item)
    except OSError:
        pass
    if out:
        return out
    recs = []
    for p in run_records(runs_dir):
        data = _read_json(p)
        peak = data.get("peak_rss_mb")
        if isinstance(peak, (int, float)) and not isinstance(peak, bool):
            recs.append((p.stat().st_mtime, {"req": data.get("requirement"), "mode": data.get("mode"),
                                             "budget_class": data.get("budget_class"),
                                             "peak_rss_mb": peak}))
    return [item for _, item in sorted(recs, key=lambda t: t[0])]


def _p95_gb(items: list[dict]) -> float:
    peaks = sorted(float(i["peak_rss_mb"]) for i in items)
    return peaks[max(0, -(-95 * len(peaks) // 100) - 1)] / 1024


def predict_gb(req: str, klass: str, mode: str, runs_dir: Path = RUNS_DIR) -> tuple[float, str]:
    """What one worker is expected to hold at its peak, and where the number
    came from: the requirement's own last peak, else the p95 of its budget class
    (>= MIN_CLASS_SAMPLES samples), else the p95 of recent peaks, else the default."""
    history = _peak_history(runs_dir)
    own = [h for h in history if req and h.get("req") == req]
    if own:
        return float(own[-1]["peak_rss_mb"]) / 1024, "requirement"
    same = [h for h in history if h.get("budget_class") == klass
            and h.get("mode") in (None, mode)][-CLASS_WINDOW:]
    if len(same) >= MIN_CLASS_SAMPLES:
        return _p95_gb(same), "class"
    if history:
        return _p95_gb(history[-RSS_WINDOW:]), "history"
    return DEFAULT_WORKER_GB, "default"


# ---------------------------------------------------------- memory leases

def _pid_gone(pid: int) -> bool:
    if pid <= 0:
        return False
    if psutil is not None:
        return not psutil.pid_exists(pid)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return True
    except OSError:
        return False
    return False


def _leases_load(runs_dir: Path) -> list[dict]:
    try:
        data = json.loads((runs_dir / LEASES_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    return [x for x in data if isinstance(x, dict) and x.get("attempt_id")] if isinstance(data, list) else []


def _leases_store(leases: list[dict], runs_dir: Path) -> None:
    path = runs_dir / LEASES_FILE
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(leases, indent=2) + "\n", encoding="utf-8", newline="\n")
    os.replace(tmp, path)


def _leases_live(leases: list[dict], now: float | None = None) -> list[dict]:
    """Leases that still hold memory: a dead pid or one older than its class wall limit is stale."""
    now = time.time() if now is None else now
    return [x for x in leases if not _pid_gone(int(x.get("pid") or 0))
            and now - float(x.get("at") or 0) <= float(x.get("wall_s") or DEFAULT_WALL_S)]


def lease_unconsumed_gb(lease: dict) -> float:
    """The part of a lease the host has not yet seen as used: what was predicted
    minus what the job's process tree already holds."""
    gb = float(lease.get("gb") or 0.0)
    pid = int(lease.get("pid") or 0)
    held = tree_rss_mb(pid) if pid > 0 else None
    return max(0.0, gb - held / 1024) if held is not None else gb


def leased_gb(runs_dir: Path = RUNS_DIR) -> float:
    return sum(lease_unconsumed_gb(x) for x in _leases_live(_leases_load(runs_dir)))


def _headroom(sample, reserve: float) -> list[float]:
    """Free memory above the reserve, physical and (when the sample has it) commit."""
    out = [sample.ram_available_gb - reserve]
    commit = getattr(sample, "commit_available_gb", None)
    if isinstance(commit, (int, float)) and not isinstance(commit, bool):
        out.append(commit - reserve)
    return out


def _candidate(item, runs_dir: Path) -> tuple[dict, str, str]:
    if isinstance(item, tuple):
        return item[0], item[1], item[2]
    return (item, item.get("mode") or "SHIP",
            item.get("klass") or budget_class(item, load_findings(item.get("id", ""), runs_dir)))


def admit(candidates: list, cap: int = DEFAULT_CAP, gov=None,
          runs_dir: Path = RUNS_DIR) -> tuple[list[tuple[dict, float]], str]:
    """Walk the candidates in order and admit each whose predicted memory fits
    the headroom left by the reserve, every live lease and everything admitted so
    far - on physical RAM and on commit alike. A candidate that does not fit waits
    and the ones behind it are still tried (owner v12: a branch that needs RAM
    waits, independent work continues). A candidate is a row or a (row, mode,
    class) tuple. CRITICAL admits none; HIGH at most one."""
    gov = gov or host_governor()
    pressure = gov.pressure()
    if pressure.level == "CRITICAL":
        return [], "pressure CRITICAL"
    sample = pressure.sample
    if sample is None:
        return [], "no memory reading"
    limits = gov.thresholds
    reserve = limits.ram_floor(sample.total_gb()) + 1.0
    leased = leased_gb(runs_dir)
    ceiling = min(cap, limits.max_workers, 1 if pressure.level == "HIGH" else cap)
    admitted: list[tuple[dict, float]] = []
    taken, why = 0.0, ""
    for item in candidates:
        row, mode, klass = _candidate(item, runs_dir)
        gb, source = predict_gb(row.get("id", ""), klass, mode, runs_dir)
        if len(admitted) >= ceiling:
            why = why or f"{len(admitted)} admitted, the ceiling for pressure {pressure.level}"
            break
        if min(_headroom(sample, reserve)) - leased - taken < gb:
            why = why or (f"{row.get('id') or 'a worker'} needs {gb:.1f} GB ({source}); "
                          f"{min(_headroom(sample, reserve)) - leased - taken:.1f} GB free above the reserve")
            continue
        admitted.append((row, gb))
        taken += gb
    return admitted, (f"pressure {pressure.level}: {sample.ram_available_gb:.1f} GB available, "
                      f"{reserve:.1f} GB reserve, {leased:.1f} GB leased, {taken:.1f} GB admitted "
                      f"-> {len(admitted)} slots (cap {cap}, max_workers {limits.max_workers})"
                      + (f"; {why}" if why else ""))


def admissible(candidates: list, cap: int = DEFAULT_CAP, gov=None,
               runs_dir: Path = RUNS_DIR) -> tuple[list[str], str]:
    """The ids admission would start now from the program's own candidates, in
    order: each job priced by its own prediction, never by a generic worker's."""
    got, why = admit(candidates, cap, gov, runs_dir)
    return [row.get("id", "") for row, _ in got], why


def concurrency(cap: int = DEFAULT_CAP, *, gov=None, runs_dir: Path = RUNS_DIR) -> tuple[int, str]:
    """How many default workers admission would start now, and why: the CLI view
    of admit(). The RAM slots bound every level: pressure only lowers."""
    default = ({"id": ""}, "SHIP", "NORMAL")
    got, why = admit([default] * max(0, cap), cap, gov, runs_dir)
    return len(got), why


def lease_release(attempt_id: str, runs_dir: Path = RUNS_DIR) -> None:
    for req in [r for r, a in _PENDING_ATTEMPTS.items() if a == attempt_id]:
        _PENDING_ATTEMPTS.pop(req, None)
    if not (runs_dir / LEASES_FILE).exists():
        return
    with creation_lock(runs_dir, LEASE_LOCK_WAIT_S, LEASE_LOCK_STALE_S):
        leases = _leases_load(runs_dir)
        kept = [x for x in leases if x["attempt_id"] != attempt_id]
        if len(kept) != len(leases):
            _leases_store(kept, runs_dir)


def lease_bind(attempt_id: str, pid: int, runs_dir: Path = RUNS_DIR) -> None:
    """Record the spawned pid on a job's lease; no lease, no write."""
    if not (runs_dir / LEASES_FILE).exists():
        return
    with creation_lock(runs_dir, LEASE_LOCK_WAIT_S, LEASE_LOCK_STALE_S):
        leases = _leases_load(runs_dir)
        for x in leases:
            if x["attempt_id"] == attempt_id:
                x["pid"] = pid
        _leases_store(leases, runs_dir)


def lease_of(attempt_id: str, runs_dir: Path = RUNS_DIR) -> dict | None:
    return next((x for x in _leases_load(runs_dir) if x["attempt_id"] == attempt_id), None)


def reserve(attempt_id: str, req: str, klass: str, mode: str = "SHIP", *, cap: int = DEFAULT_CAP,
            gov=None, runs_dir: Path = RUNS_DIR) -> tuple[bool, str]:
    """CHECK (admit) -> LEASE -> RECHECK for one job, before it spawns. The
    recheck takes a fresh governor sample under the lease lock and subtracts every
    live lease, this one included; a failure releases the lease. On success the
    next Running built for `req` carries `attempt_id`."""
    gov = gov or host_governor()
    got, why = admit([({"id": req}, mode, klass)], cap, gov, runs_dir)
    if not got:
        return False, why
    gb = got[0][1]
    runs_dir.mkdir(parents=True, exist_ok=True)
    with creation_lock(runs_dir, LEASE_LOCK_WAIT_S, LEASE_LOCK_STALE_S):
        leases = _leases_live(_leases_load(runs_dir))
        leases.append({"attempt_id": attempt_id, "req": req, "gb": round(gb, 3), "pid": 0,
                       "at": time.time(), "wall_s": BUDGETS[klass][2]})
        _leases_store(leases, runs_dir)
        pressure = gov.pressure()
        sample = pressure.sample
        reserve_gb = gov.thresholds.ram_floor(sample.total_gb()) + 1.0 if sample is not None else 0.0
        short = ("pressure CRITICAL" if pressure.level == "CRITICAL" else
                 "no memory reading" if sample is None else
                 "" if min(_headroom(sample, reserve_gb)) - sum(map(lease_unconsumed_gb, leases)) >= 0
                 else f"{gb:.1f} GB lease no longer fits: {sample.ram_available_gb:.1f} GB available, "
                      f"{reserve_gb:.1f} GB reserve, leases hold {sum(map(lease_unconsumed_gb, leases)):.1f} GB")
        if short:
            _leases_store([x for x in leases if x["attempt_id"] != attempt_id], runs_dir)
            return False, f"recheck: {short}"
    _PENDING_ATTEMPTS[req] = attempt_id
    return True, why


# ---------------------------------------------------------------- spend
#: The owner's day budget (2026-10-03, 00-owner-directive-2026-10-03.md): no new paid work starts at or
#: above the soft stop, and nothing may reserve past the hard ceiling. Per local day, every paid call -
#: worker, verifier, advisor, rescue, manual - inside a program or not.
DAY_SOFT_USD = 40.0
DAY_HARD_USD = 45.0
SPEND_LEASES_FILE = "spend_leases.json"
VERIFIER_FLOOR_USD = 1.0
VERIFIER_SHARE = 0.25
#: Purposes that finish work already paid for: only the hard ceiling refuses them.
FINISHING = ("continue", "verify")


class SpendCapReached(KernelError):
    """A paid call would pass the owner's day budget; nothing was started."""


def verifier_budget(cost_usd: float) -> float:
    """A quarter of the implementer's cost, never below the floor: the verifier
    pays for its own system prompt before it reads a line of the change."""
    return max(VERIFIER_FLOOR_USD, VERIFIER_SHARE * float(cost_usd or 0))


def launch_usd(klass: str, kind: str = "SHIP") -> float:
    """The most one launch may spend: its worker budget and, for a change that is
    then verified, its verifier's - so a finished worker can always be checked."""
    if kind == "ADVISOR":
        return ADVISOR_BUDGET[1]
    if kind == "SCOUT":
        return BUDGETS["SCOUT"][1]
    budget = BUDGETS["P0" if kind == "ESCALATE" else klass][1]
    if kind in ("REPAIR", "IMPLEMENTER", "RESUME"):
        budget /= 2
    return round(budget + verifier_budget(budget), 4)


def _runs(runs_dir: Path | None) -> Path:
    return Path(runs_dir) if runs_dir is not None else RUNS_DIR


def _today() -> str:
    return datetime.now().astimezone().date().isoformat()


def _spend_load(runs_dir: Path) -> list[dict]:
    try:
        data = json.loads((runs_dir / SPEND_LEASES_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    return [x for x in data if isinstance(x, dict) and x.get("key")] if isinstance(data, list) else []


def _spend_store(leases: list[dict], runs_dir: Path) -> None:
    """Only today's leases are kept: an earlier day's never counts again."""
    today = _today()
    path = runs_dir / SPEND_LEASES_FILE
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps([x for x in leases if x.get("day") == today], indent=2) + "\n",
                   encoding="utf-8", newline="\n")
    os.replace(tmp, path)


def spend_committed(runs_dir: Path | None = None) -> tuple[float, float]:
    """(spent, reserved) today: the ledger's recorded cost and the open spend leases."""
    runs_dir, today = _runs(runs_dir), _today()
    reserved = sum(float(x.get("usd") or 0) for x in _spend_load(runs_dir) if x.get("day") == today)
    return day_cost(datetime.now().astimezone().date(), runs_dir), round(reserved, 4)


def spend_reserve(key: str, purpose: str, req: str, kind: str, usd: float,
                  runs_dir: Path | None = None) -> tuple[bool, str]:
    """Reserve one paid call's maximum cost against today's budget. The ledger and
    the leases are re-read and the lease written under one lock, so the recheck is
    the check: two parallel launches can never both see the same dollars left. A
    (key, purpose) already held is not reserved twice (the program's gate takes the
    lease, the kernel function it then calls finds it)."""
    runs_dir = _runs(runs_dir)
    runs_dir.mkdir(parents=True, exist_ok=True)
    today = _today()
    with creation_lock(runs_dir, LEASE_LOCK_WAIT_S, LEASE_LOCK_STALE_S):
        leases = [x for x in _spend_load(runs_dir) if x.get("day") == today]
        if any(x["key"] == key and x.get("purpose") == purpose for x in leases):
            return True, "already reserved"
        spent = day_cost(datetime.now().astimezone().date(), runs_dir)
        reserved = sum(float(x.get("usd") or 0) for x in leases)
        committed = spent + reserved
        if purpose not in FINISHING and committed >= DAY_SOFT_USD:
            return False, (f"SPEND_SOFT_STOP: ${committed:.2f} committed today (${spent:.2f} spent, "
                           f"${reserved:.2f} reserved), soft stop ${DAY_SOFT_USD:.2f}")
        if committed + usd > DAY_HARD_USD:
            return False, (f"SPEND_CAP: ${committed:.2f} committed today + ${usd:.2f} for {req} {kind} "
                           f"would pass the ${DAY_HARD_USD:.2f} day ceiling")
        leases.append({"key": key, "purpose": purpose, "req": req, "kind": kind,
                       "usd": round(float(usd), 4), "day": today, "at": _iso()})
        _spend_store(leases, runs_dir)
    return True, f"${usd:.2f} reserved for {req} {kind}; ${committed + usd:.2f} committed today"


def spend_guard(key: str, purpose: str, req: str, kind: str, usd: float,
                runs_dir: Path | None = None) -> None:
    ok, why = spend_reserve(key, purpose, req, kind, usd, runs_dir)
    if not ok:
        raise SpendCapReached(why)


def spend_release(key: str, runs_dir: Path | None = None) -> None:
    runs_dir = _runs(runs_dir)
    if not (runs_dir / SPEND_LEASES_FILE).exists():
        return
    with creation_lock(runs_dir, LEASE_LOCK_WAIT_S, LEASE_LOCK_STALE_S):
        leases = _spend_load(runs_dir)
        kept = [x for x in leases if x["key"] != key]
        if len(kept) != len(leases):
            _spend_store(kept, runs_dir)


def spend_settle(attempt_id: str, req: str, verify_usd: float, runs_dir: Path | None = None) -> None:
    """After an attempt's actual cost is in the ledger: drop its launch and continue
    leases and, for a finished change, hold its verifier's budget until the
    integrator has run (or skipped) the verifier."""
    runs_dir = _runs(runs_dir)
    runs_dir.mkdir(parents=True, exist_ok=True)
    verify_key = f"{attempt_id}:verify"
    with creation_lock(runs_dir, LEASE_LOCK_WAIT_S, LEASE_LOCK_STALE_S):
        leases = [x for x in _spend_load(runs_dir) if x["key"] not in (attempt_id, verify_key)]
        if verify_usd > 0:
            leases.append({"key": verify_key, "purpose": "verify", "req": req, "kind": "VERIFIER",
                           "usd": round(float(verify_usd), 4), "day": _today(), "at": _iso()})
        _spend_store(leases, runs_dir)


def _attempt_for(req: str) -> str:
    """The attempt id a launch runs under: the one its memory lease was taken
    under, or a fresh one for a launch made outside the program."""
    return _PENDING_ATTEMPTS.get(req) or str(uuid.uuid4())


# --------------------------------------------------------------- launch

def _worktrees_root() -> Path:
    r = subprocess.run(["git", "rev-parse", "--git-common-dir"], cwd=ROOT,
                       capture_output=True, text=True, timeout=30)
    common = Path(r.stdout.strip())
    if not common.is_absolute():
        common = ROOT / common
    return common.resolve().parent / ".worktrees"


def _bullets(v) -> str:
    return "\n".join(f"- {x}" for x in v) if isinstance(v, (list, tuple)) else str(v or "")


def _findings_section(findings: dict) -> str:
    return f"""## Scout findings (trust these, do not re-investigate)
- Gap: {findings.get('gap', '')}
- Files:
{_bullets(findings.get('files') or [])}
- Tests:
{_bullets(findings.get('tests') or [])}
- Acceptance plan: {findings.get('acceptance_plan', '')}
- Risk: {findings.get('risk', '')}

"""


def packet_text(row: dict, findings: dict | None = None, klass: str = "NORMAL",
                capsule: str = "") -> str:
    found = _findings_section(findings) if findings else ""
    code_map = f"## Code map\n{capsule.strip()}\n\n" if capsule.strip() else ""
    return f"""# Build packet: {row['id']} ({row['priority']})

Summary: {row['summary']}

## Current implementation
{row.get('current_implementation', '')}

## Acceptance
{row.get('acceptance', '')}

## Next action
{row.get('next_action', '')}

## Cited files
{_bullets(row.get('files'))}

## Cited tests
{_bullets(row.get('tests'))}

{found}{code_map}## Rules
- Work only in this worktree. Never commit (the kernel commits a DONE run). Never touch data/ (especially data/ada.sqlite3).
- Read budget: at most {READ_FILES.get(klass, 8)} files and <= 400 lines before your first edit.
- Run targeted tests only: `{VENV_PY} scripts/test_impact.py --direct --files <changed files>` then
  `{VENV_PY} scripts/pytest_brief.py -- <test files>`.
- Add a test that fails without your change. No debug prints, no secrets.
- Finish by writing `.build/evidence.json`:
  {{"requirement": "{row['id']}", "status": "DONE|BLOCKED", "summary": "<=300 chars",
   "files_changed": [], "tests": {{"command": "", "passed": 0, "failed": 0}},
   "acceptance_met": true|false, "gaps": [], "blockers": [],
   "commit_subject": "<=72 chars, conventional, says why: e.g. fix(router): stop retrying a failing path"}}
"""


def scout_packet_text(row: dict) -> str:
    return f"""# Scout packet: {row['id']} ({row['priority']})

Summary: {row['summary']}

## Current implementation
{row.get('current_implementation', '')}

## Acceptance
{row.get('acceptance', '')}

## Next action
{row.get('next_action', '')}

## Cited files
{_bullets(row.get('files'))}

## Cited tests
{_bullets(row.get('tests'))}

## Rules
- Investigate only. You are read-only: do not edit code, do not commit, never touch data/.
- Read budget: at most {READ_FILES['SCOUT']} files.
- Finish with your findings as your final message - only this JSON object:
  {{"requirement": "{row['id']}", "gap": "what is missing or wrong", "files": [],
   "tests": [], "acceptance_plan": "how it will be met and proven",
   "risk": "LOW|MEDIUM|HIGH", "budget_class": "MICRO|NORMAL|COMPLEX"}}
"""


def worker_env(wt: Path, base: dict | None = None) -> dict:
    """The worker's environment: its own state root, never the live data/."""
    home = wt / ".build" / "home"
    home.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ if base is None else base)
    env.update(FRIDAY_HOME=str(home), ADA_DB=str(home / "ada.sqlite3"),
               ADA_AUDIT_DB=str(home / "audit.sqlite3"))
    return env


def worker_command(model: str, max_turns: int, max_budget: float, *,
                   tools: str = ALLOWED_TOOLS, resume: str | None = None,
                   prompt: str = WORKER_PROMPT) -> list[str]:
    exe = shutil.which("claude") or "claude"
    cmd = [exe, "-p", prompt]
    if resume:
        cmd += ["--resume", resume]
    # acceptEdits approves every edit in the worktree whatever the allowlist
    # says, so a scout runs in default mode: anything not allowed is denied.
    permission = "default" if tools == SCOUT_TOOLS else "acceptEdits"
    return cmd + ["--model", model, "--max-turns", str(max_turns),
                  "--max-budget-usd", str(max_budget), "--output-format", "stream-json",
                  "--verbose", "--permission-mode", permission, "--allowedTools", tools,
                  # A minimal worker (BUILD-010): no MCP servers, no hooks injecting
                  # context. Safety is the permission rules, which still apply.
                  "--strict-mcp-config", "--settings", '{"disableAllHooks": true}']


@dataclass
class Running:
    req: str
    proc: subprocess.Popen
    wt: Path
    started: float = field(default_factory=time.time)
    mode: str = "SHIP"
    klass: str = "NORMAL"
    base_sha: str = ""
    branch: str = ""
    model: str = "sonnet"
    wall_s: float = DEFAULT_WALL_S
    repo: Path = ROOT
    continued: bool = False
    repairs: int = 0
    capsule: bool = False
    peak_rss_mb: float | None = None
    stream_offset: int = 0  # bytes of stream.jsonl that belong to earlier runs
    kind: str = ""  # ledger kind; "" means SHIP (or SCOUT for a scout)
    reason: str = ""  # why agent_utility shaped this job as it did
    attempt_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    program_run_id: str = field(default_factory=lambda: os.environ.get("FRIDAY_BUILD_RUN_ID", ""))
    wave_id: str = field(default_factory=lambda: os.environ.get("FRIDAY_BUILD_WAVE_ID", ""))
    runs_dir: Path | None = None  # where its spend leases live; None is RUNS_DIR

    def __post_init__(self):
        leased = _PENDING_ATTEMPTS.pop(self.req, None)  # the id the job's memory lease was taken under
        if leased:
            self.attempt_id = leased


# ----------------------------------------------------------------- jobs

def _iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def job_path(attempt_id: str, runs_dir: Path = RUNS_DIR) -> Path:
    return runs_dir / JOBS_DIR / f"{attempt_id}.json"


def job_write(job: dict, runs_dir: Path = RUNS_DIR) -> dict:
    path = job_path(job["attempt_id"], runs_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(job, indent=2) + "\n", encoding="utf-8", newline="\n")
    os.replace(tmp, path)
    return job


def jobs(runs_dir: Path = RUNS_DIR) -> list[dict]:
    """Every job record, oldest first; unreadable files are skipped."""
    out = [j for p in sorted((runs_dir / JOBS_DIR).glob("*.json")) if (j := _read_json(p)).get("attempt_id")]
    return sorted(out, key=lambda j: str(j.get("started") or ""))


def job_open(run: "Running", runs_dir: Path = RUNS_DIR, state: str = "STARTED") -> dict:
    """The durable record of one agent job: written as the process starts."""
    lease_bind(run.attempt_id, run.proc.pid, runs_dir)
    return job_write({"attempt_id": run.attempt_id, "req": run.req,
                      "role": ROLE_OF_KIND[run.kind or ("SCOUT" if run.mode == "SCOUT" else "SHIP")],
                      "model": run.model, "state": state, "pid": run.proc.pid,
                      "worktree": str(run.wt), "started": _iso(), "ended": "",
                      "reason": run.reason, "program_run_id": run.program_run_id,
                      "wave_id": run.wave_id}, runs_dir)


def job_queue(req: str, klass: str, mode: str = "SHIP", runs_dir: Path = RUNS_DIR,
              reason: str = "") -> str:
    """Record a chosen job as QUEUED, before admission; returns its attempt id."""
    attempt_id = str(uuid.uuid4())
    job_write({"attempt_id": attempt_id, "req": req,
               "role": ROLE_OF_KIND["SCOUT" if mode == "SCOUT" else "SHIP"], "model": "",
               "state": "QUEUED", "pid": 0, "worktree": "", "started": _iso(), "ended": "",
               "reason": reason or f"{klass} {mode}", "program_run_id": os.environ.get("FRIDAY_BUILD_RUN_ID", ""),
               "wave_id": os.environ.get("FRIDAY_BUILD_WAVE_ID", "")}, runs_dir)
    return attempt_id


def job_wait(attempt_id: str, reason: str, runs_dir: Path = RUNS_DIR) -> dict | None:
    """Admission or the lease recheck refused the job: WAITING_RESOURCES, with why."""
    job = _read_json(job_path(attempt_id, runs_dir))
    if not job.get("attempt_id"):
        return None
    job.update(state="WAITING_RESOURCES", reason=reason)
    return job_write(job, runs_dir)


def job_close(attempt_id: str, state: str, reason: str = "", runs_dir: Path = RUNS_DIR) -> dict | None:
    """Move a job to COMPLETE or FAILED and release its memory lease and its launch and
    continue spend leases (a finished change's verify lease is the integrator's to
    release); None when it has no record."""
    lease_release(attempt_id, runs_dir)
    spend_release(attempt_id, runs_dir)
    job = _read_json(job_path(attempt_id, runs_dir))
    if not job.get("attempt_id"):
        return None
    job.update(state=state, ended=_iso())
    if reason:
        job["reason"] = "; ".join(x for x in (job.get("reason"), reason) if x)
    return job_write(job, runs_dir)


class PidProc:
    """The slice of Popen the watcher needs, for a worker this process did not
    start: it only knows the pid, and cannot read an exit code."""

    def __init__(self, pid: int, alive):
        self.pid, self._alive, self.returncode = pid, alive, None

    def poll(self):
        if self.returncode is None and not self._alive(self.pid):
            self.returncode = 0
        return self.returncode

    def wait(self, timeout=None):
        return self.poll()

    def kill(self):
        kill_tree(self)


def adopt(job: dict, *, alive, runs_dir: Path = RUNS_DIR, repo: Path = ROOT) -> "Running":
    """A Running for a STARTED job a dead controller left behind, so it can be
    watched or finalized instead of relaunched."""
    wt = Path(job["worktree"])
    meta = _run_meta(wt)
    mode = meta.get("mode") or ("SCOUT" if job.get("role") == "SCOUT" else "SHIP")
    klass = meta.get("klass") or ("SCOUT" if mode == "SCOUT" else "NORMAL")
    started = float(meta.get("started") or time.time())
    return Running(job["req"], PidProc(int(job["pid"]), alive), wt, started=started, mode=mode,
                   klass=klass, base_sha=meta.get("base_sha", ""), branch=meta.get("branch", ""),
                   model=job.get("model") or meta.get("model") or "sonnet",
                   wall_s=BUDGETS[klass][2], repo=repo, continued=True,
                   capsule=bool(meta.get("capsule")), stream_offset=int(meta.get("stream_offset") or 0),
                   kind="" if job.get("role") in ("IMPLEMENTER", "SCOUT") else str(job.get("role")),
                   reason=str(job.get("reason") or ""), attempt_id=job["attempt_id"],
                   program_run_id=job.get("program_run_id", ""), wave_id=job.get("wave_id", ""),
                   runs_dir=runs_dir)


def has_result(wt: Path, offset: int = 0) -> bool:
    """Whether the worker's stream already holds its own result event."""
    return bool(_own_results(_events(wt / ".build" / "stream.jsonl", offset)))


# ------------------------------------------------------------ worktrees

def _git(args: list[str], cwd: Path, timeout: int = 120) -> str:
    r = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, timeout=timeout)
    if r.returncode != 0:
        raise KernelError(f"git {' '.join(args)} failed in {cwd}: {r.stderr.strip()}")
    return r.stdout.strip()


def _same_path(a: str | Path, b: str | Path) -> bool:
    return os.path.normcase(os.path.realpath(a)) == os.path.normcase(os.path.realpath(b))


def _common_dir(cwd: Path) -> Path:
    p = Path(_git(["rev-parse", "--git-common-dir"], cwd))
    return p if p.is_absolute() else cwd / p


@contextmanager
def creation_lock(root: Path, wait_s: float = LOCK_WAIT_S, stale_s: float = LOCK_STALE_S,
                  poll_s: float = 0.2):
    """Serialize worktree creation across kernel processes: an O_EXCL lock
    file in the worktrees root. A lock older than `stale_s` is replaced."""
    root.mkdir(parents=True, exist_ok=True)
    lock = root / LOCK_NAME
    deadline = time.time() + wait_s
    while True:
        try:
            os.close(os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY))
            break
        except FileExistsError:
            try:
                age = time.time() - lock.stat().st_mtime
            except OSError:
                continue  # released between the open and the stat
            if age > stale_s:
                lock.unlink(missing_ok=True)
                continue
            if time.time() >= deadline:
                raise KernelError(f"timed out waiting for worktree lock {lock}")
            time.sleep(poll_s)
    try:
        yield
    finally:
        lock.unlink(missing_ok=True)


def _worktree_fault(wt: Path, repo: Path, base_sha: str, branch: str | None) -> str | None:
    """What is wrong with a just-created worktree, or None."""
    if _git(["rev-parse", "HEAD"], wt) != base_sha:
        return "HEAD is not the recorded base sha"
    if branch is not None and _git(["rev-parse", "--abbrev-ref", "HEAD"], wt) != branch:
        return f"branch is not {branch}"
    if not _same_path(_git(["rev-parse", "--show-toplevel"], wt), wt):
        return "toplevel is not the worktree path"
    if not _same_path(_common_dir(wt), _common_dir(repo)):
        return "git common dir is not the repository's"
    return None


def _remove_worktree(repo: Path, wt: Path, branch: str | None = None) -> None:
    subprocess.run(["git", "worktree", "remove", "--force", str(wt)], cwd=repo,
                   capture_output=True, timeout=120)
    if branch:
        subprocess.run(["git", "branch", "-D", branch], cwd=repo, capture_output=True, timeout=30)


def create_worktree(wt: Path, name: str, *, repo: Path = ROOT, detach: bool = False,
                    lock_wait_s: float = LOCK_WAIT_S) -> str:
    """Create the worktree under the lock, assert it is what was asked for,
    and return the base sha. A SHIP worktree is on build/<name>; a scout's is
    detached. Any mismatch removes the worktree and raises."""
    branch = None if detach else f"build/{name}"
    with creation_lock(wt.parent, lock_wait_s):
        if wt.exists():
            raise KernelError(f"worktree {wt} already exists; refusing to reuse it")
        base_sha = _git(["rev-parse", "HEAD"], repo)
        args = ["worktree", "add", *(["--detach"] if detach else ["-b", branch]), str(wt), "HEAD"]
        _git(args, repo)
        try:
            fault = _worktree_fault(wt, repo, base_sha, branch)
        except KernelError:
            _remove_worktree(repo, wt, branch)
            raise
        if fault:
            _remove_worktree(repo, wt, branch)
            raise KernelError(f"worktree {wt} failed its checks: {fault}")
    return base_sha


def _stream_size(wt: Path) -> int:
    try:
        return (wt / ".build" / "stream.jsonl").stat().st_size
    except OSError:
        return 0


def _start(cmd: list[str], wt: Path) -> subprocess.Popen:
    """Start a worker appending to the stream log, so a continuation keeps it.
    The previous invocation's evidence is archived first: a run's verdict must be
    its own, never a DONE left behind by the attempt before it."""
    ev = wt / ".build" / "evidence.json"
    if ev.exists():
        ev.replace(ev.with_name(f"evidence.prev-{time.time_ns()}.json"))
    kw = ({"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP} if os.name == "nt"
          else {"start_new_session": True})
    with open(wt / ".build" / "stream.jsonl", "ab") as log:
        return subprocess.Popen(cmd, cwd=wt, stdout=log, stderr=subprocess.STDOUT,
                                stdin=subprocess.DEVNULL, env=worker_env(wt), **kw)


def launch(row: dict, *, worktrees: Path | None = None, model: str = "sonnet",
           max_turns: int | None = None, max_budget: float | None = None,
           dry_run: bool = False, mode: str = "SHIP", klass: str | None = None,
           runs_dir: Path = RUNS_DIR, capsule: str = "", repo: Path = ROOT):
    """Create the worktree, write the packet, start the worker. In dry-run
    return the plan and touch nothing. SHIP builds on build/<id>; SCOUT
    investigates read-only in a detached worktree."""
    if mode not in ("SHIP", "SCOUT"):
        raise KernelError(f"unknown mode {mode!r}")
    scout = mode == "SCOUT"
    name = row["id"].lower()
    wt = (worktrees or _worktrees_root()) / name
    findings = None if scout else load_findings(row["id"], runs_dir)
    klass = "SCOUT" if scout else (klass or budget_class(row, findings))
    turns, budget, wall = BUDGETS[klass]
    turns, budget = max_turns or turns, max_budget or budget
    cmd = worker_command(model, turns, budget, tools=SCOUT_TOOLS if scout else ALLOWED_TOOLS)
    if dry_run:
        return {"worktree": str(wt), "mode": mode, "class": klass,
                "branch": "" if scout else f"build/{name}",
                "packet": str(wt / ".build" / "packet.md"), "command": cmd}
    if wt.exists():
        raise KernelError(f"worktree {wt} already exists; refusing to reuse it")
    # The spend lease before any side effect: a refused launch leaves no worktree behind.
    attempt_id = _attempt_for(row["id"])
    spend_guard(attempt_id, "launch", row["id"], mode,
                budget if scout else budget + verifier_budget(budget), runs_dir)
    try:
        base_sha = create_worktree(wt, name, repo=repo, detach=scout)
    except BaseException:
        spend_release(attempt_id, runs_dir)
        raise
    (wt / ".build").mkdir(parents=True, exist_ok=True)
    text = scout_packet_text(row) if scout else packet_text(row, findings, klass, capsule)
    (wt / ".build" / "packet.md").write_text(text, encoding="utf-8", newline="\n")
    branch = "" if scout else f"build/{name}"
    started = time.time()
    offset = _stream_size(wt)
    _write_run_meta(wt, {"req": row["id"], "mode": mode, "klass": klass, "base_sha": base_sha,
                         "branch": branch, "model": model, "started": started,
                         "capsule": bool(capsule), "stream_offset": offset})
    proc = _start(cmd, wt)
    decision, reasons = agent_utility(row, {"has_findings": findings is not None})
    run = Running(row["id"], proc, wt, started=started, mode=mode, klass=klass,
                  base_sha=base_sha, branch=branch, model=model, wall_s=wall, repo=repo,
                  capsule=bool(capsule), stream_offset=offset, attempt_id=attempt_id, runs_dir=runs_dir,
                  reason=f"{decision['orient']}: {'; '.join(reasons)}")
    job_open(run, runs_dir)
    return run


def _write_run_meta(wt: Path, meta: dict) -> None:
    (wt / ".build").mkdir(parents=True, exist_ok=True)
    (wt / ".build" / "run.json").write_text(json.dumps(meta, indent=2) + "\n",
                                            encoding="utf-8", newline="\n")


def _run_meta(wt: Path) -> dict:
    return _evidence(wt, "run.json")


def resumable(wt: Path) -> str | None:
    """The session id of a SHIP worker killed mid-run: its stream opens with an
    init event carrying a session id and holds no result event."""
    events = _events(wt / ".build" / "stream.jsonl")
    init = next((e for e in events if e.get("type") == "system" and e.get("subtype") == "init"), None)
    sid = (init or {}).get("session_id")
    if not sid or _results(events):
        return None
    mode = _run_meta(wt).get("mode")
    if mode is None:  # an older run left no run.json; a scout's worktree is detached
        try:
            mode = "SCOUT" if _git(["rev-parse", "--abbrev-ref", "HEAD"], wt, 30) == "HEAD" else "SHIP"
        except KernelError:
            return None
    return str(sid) if mode == "SHIP" else None


def resume(req: str, *, worktrees: Path | None = None, runs_dir: Path = RUNS_DIR,
           repo: Path = ROOT, row: dict | None = None, model: str | None = None) -> Running:
    """Relaunch a killed SHIP worker in its existing worktree from its session,
    at half its class limits."""
    wt = (worktrees or _worktrees_root()) / req.lower()
    sid = resumable(wt) if wt.is_dir() else None
    if not sid:
        raise KernelError(f"{req} has no resumable run in {wt}")
    meta = _run_meta(wt)
    if not meta.get("base_sha"):
        if row is None:
            row = next((r for r in load_rows() if r["id"] == req), None)
        if row is None:
            raise KernelError(f"unknown requirement {req!r}")
        meta = {"req": req, "mode": "SHIP", "klass": budget_class(row, load_findings(req, runs_dir)),
                "base_sha": _git(["merge-base", "HEAD", _git(["rev-parse", "HEAD"], repo)], wt),
                "branch": _git(["rev-parse", "--abbrev-ref", "HEAD"], wt),
                "model": model or "sonnet", "capsule": False}
    klass = meta["klass"]
    turns, budget, wall = BUDGETS[klass]
    attempt_id = _attempt_for(req)
    spend_guard(attempt_id, "launch", req, "RESUME", budget / 2 + verifier_budget(budget / 2), runs_dir)
    started = time.time()
    offset = _stream_size(wt)
    meta = {**meta, "started": started, "stream_offset": offset}
    _write_run_meta(wt, meta)
    cmd = worker_command(model or meta.get("model") or "sonnet", max(1, turns // 2), budget / 2,
                         resume=sid, prompt=CONTINUE_PROMPT)
    run = Running(req, _start(cmd, wt), wt, started=started, mode="SHIP", klass=klass,
                  base_sha=meta["base_sha"], branch=meta["branch"],
                  model=model or meta.get("model") or "sonnet", wall_s=wall, repo=repo,
                  continued=True, capsule=bool(meta.get("capsule")), stream_offset=offset,
                  attempt_id=attempt_id, runs_dir=runs_dir,
                  kind="RESUME", reason="RESUME: a killed worker continues its own session")
    job_open(run, runs_dir)
    return run


def _read_json(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def repair_prompt(integration: dict) -> str:
    """The integrator's rejection, as a prompt for the worker that made the change."""
    result = integration.get("result", "FAILED_INTEGRATION")
    lines = [f"Your change for {integration.get('requirement', 'this requirement')} was "
             f"rejected at integration: {result}."]
    verifier = integration.get("verifier") or {}
    if verifier:
        lines.append(f"Verifier verdict: {verifier.get('verdict', '')}.")
        for label, key in (("Failing criteria", "failing_criteria"),
                           ("Evidence refs", "evidence_refs")):
            items = verifier.get(key) or []
            if items:
                lines.append(f"{label}:")
                lines += [f"- {item}" for item in items]
    rg = integration.get("red_green") or {}
    if result in ("NOT_RED", "NOT_GREEN", "NO_TEST") and rg:
        lines.append(f"Red/green check: {rg.get('result', result)}.")
        if rg.get("tests"):
            lines.append("Tests: " + ", ".join(str(t) for t in rg["tests"]))
        if rg.get("tail"):
            lines += ["Test output tail:", str(rg["tail"])]
    if integration.get("failures"):
        lines.append("Tests that fail with your change (they pass without it):")
        lines += [f"- {f}" for f in integration["failures"]]
    if integration.get("detail"):
        lines.append(f"Detail: {integration['detail']}")
    lines.append("Fix every finding you can in code and tests. A finding that needs something "
                 "outside this worktree (a live run, hardware, an owner decision) is recorded "
                 "in evidence gaps, never faked. Then rewrite .build/evidence.json (status, "
                 "summary, acceptance_met, gaps, commit_subject).")
    return "\n".join(lines)


def _failing_tests(integration: dict) -> list[str]:
    rg = integration.get("red_green") or {}
    found = integration.get("failures") or rg.get("failing") or rg.get("tests") or []
    return [str(t) for t in found]


def localized(integration: dict) -> bool:
    """A failure small enough for the worker's own session to fix with its
    context: the integrator reset a change that ran (FAILED_INTEGRATION), or
    the new tests ran red with at most LOCALIZED_FAILING_TESTS failing. A worker
    that failed before integration never is: its own session is the one that ran
    out (2026-10-03, SEC-005: resuming it cost 1.5x the first run for half the turns)."""
    if not integration:
        return False
    result = integration.get("result", "FAILED_INTEGRATION")
    if result == "FAILED_INTEGRATION":
        return True
    return result == "NOT_GREEN" and len(_failing_tests(integration)) <= LOCALIZED_FAILING_TESTS


def _diff_stat(wt: Path, base_sha: str) -> str:
    try:
        return _git(["diff", "--stat", base_sha], wt, 60)
    except (KernelError, subprocess.TimeoutExpired):
        return ""


def failure_capsule(req: str, integration: dict, run_rec: dict, wt: Path, base_sha: str,
                    attempt: int) -> str:
    """What a fresh session needs to fix a rejected change, built from the
    integration record and the run record - never from the earlier transcript."""
    result = integration.get("result") or "no integration record"
    verifier = integration.get("verifier") or {}
    rg = integration.get("red_green") or {}
    failing = _failing_tests(integration)
    tests = [str(t) for t in rg.get("tests") or []] or [
        t.split(" - ")[0].removeprefix("FAILED ").strip() for t in failing]
    evidence = run_rec.get("evidence") or {}
    files = run_rec.get("files_changed") or []
    lines = [f"# Failure capsule: {req} (attempt {attempt})",
             "Read .build/packet.md for the requirement. This is a fresh session: the earlier "
             "transcript is deliberately not carried over; the worktree holds the earlier work.",
             "## What failed"]
    if integration:
        lines.append(f"Integration result: {result}.")
    else:
        lines.append(f"The earlier worker ended {run_rec.get('state') or 'FAILED'} before integration: it "
                     "stopped (its turn or time limit) before finishing and wrote no evidence. The "
                     "worktree holds its unfinished work - read the diff, finish it, do not start over.")
    if integration.get("detail"):
        lines.append(f"Detail: {integration['detail']}")
    lines += ["## Expected",
              "New or changed tests fail without the change and pass with it, nothing else "
              "regresses, and every acceptance criterion is met."]
    if verifier.get("failing_criteria"):
        lines += ["Criteria the verifier found unmet:"] + [f"- {c}" for c in verifier["failing_criteria"]]
    lines.append("## Observed")
    if verifier:
        lines.append(f"Verifier verdict: {verifier.get('verdict', '')}.")
    lines += [f"- failing: {t}" for t in failing] or ["- no failing test was recorded"]
    for ref in verifier.get("evidence_refs") or []:
        lines.append(f"- evidence: {ref}")
    if rg.get("tail"):
        lines += ["Test output tail:", str(rg["tail"])]
    lines.append("## Already ruled out")
    lines.append(f"- the earlier attempt's own claim (verify it, do not trust it): "
                 f"{evidence.get('summary') or 'none recorded'}")
    lines += [f"- known gap: {g}" for g in evidence.get("gaps") or []]
    lines.append("- repeating the earlier approach unchanged: it was rejected")
    lines += ["## Reproduction",
              f"{VENV_PY} scripts/pytest_brief.py -- " + (" ".join(tests) or "<the tests for this change>"),
              "## Files"] + ([f"- {f}" for f in files] or ["- none recorded"])
    stat = _diff_stat(wt, base_sha) if base_sha else ""
    lines += ["## Current diff stat", stat or "(unavailable)", "## Tests to run",
              " ".join(tests) or "the tests for this change",
              "Fix every finding you can in code and tests. A finding that needs something outside "
              "this worktree (a live run, hardware, an owner decision) is recorded in evidence gaps, "
              "never faked. Then rewrite .build/evidence.json (status, summary, acceptance_met, "
              "gaps, commit_subject)."]
    return "\n".join(lines)


#: A worker's own failure before integration: it ran out of turns or time.
WORKER_FAILURES = ("FAILED", "TIMEOUT")


def has_work(rec: dict) -> bool:
    """Whether a run left changes in its worktree (the kernel's .build/ aside). A
    worktree git cannot read counts as holding nothing: the run is then an ordinary
    failed attempt, which is what it was before this check existed."""
    wt = Path(rec.get("worktree") or "")
    try:
        return wt.is_dir() and bool(_changes(wt).strip())
    except KernelError:
        return False


def attempts_made(req: str, runs_dir: Path = RUNS_DIR) -> int:
    """Worker invocations already recorded for `req` (scouts excluded)."""
    return sum(e.get("req") == req and e.get("kind") != "SCOUT" for e in ledger_entries(runs_dir))


def advice_path(req: str, runs_dir: Path = RUNS_DIR) -> Path:
    return runs_dir / f"{req}.advice.md"


def load_advice(req: str, runs_dir: Path = RUNS_DIR) -> str:
    """The advisor's diagnosis packet for `req`, or ''."""
    try:
        return advice_path(req, runs_dir).read_text(encoding="utf-8")
    except OSError:
        return ""


def _last_growth(req: str, runs_dir: Path) -> float | None:
    grown = [e["retry_context_growth"] for e in ledger_entries(runs_dir)
             if e.get("req") == req and e.get("retry_context_growth")]
    return grown[-1] if grown else None


def repair(req: str, *, worktrees: Path | None = None, runs_dir: Path = RUNS_DIR,
           repo: Path = ROOT, model: str | None = None, advice: str = "") -> Running:
    """Repair a rejected change in its worktree at half its class limits. The
    first repair (attempt 2) resumes the worker's own session when the failure
    is localized and its context has not outgrown RETRY_GROWTH_MAX; any other,
    and every repair from attempt 3, is a FRESH session whose prompt is a
    Failure Capsule (or, after an advisor, its diagnosis packet), because a
    resumed session re-reads its whole transcript and costs more each time."""
    wt = (worktrees or _worktrees_root()) / req.lower()
    if not wt.is_dir():
        raise KernelError(f"{req} has no worktree at {wt}")
    run_rec = _read_json(runs_dir / f"{req}.json")
    sid = run_rec.get("session_id")
    if not sid:
        raise KernelError(f"{req} has no recorded session to repair")
    integration = _read_json(runs_dir / f"{req}.integration.json")
    # A worker that failed before integration (its turn cap, a timeout) has no integration
    # record; its unfinished work on disk is repaired from a fresh capsule all the same.
    if not integration and run_rec.get("state") not in WORKER_FAILURES:
        raise KernelError(f"{req} has no integration record to repair from")
    meta = _run_meta(wt)
    base_sha = meta.get("base_sha") or run_rec.get("base_sha")
    if not base_sha:
        raise KernelError(f"{req} has no base commit to repair against")
    klass = meta.get("klass") or run_rec.get("budget_class") or "NORMAL"
    turns, budget, wall = BUDGETS[klass]
    attempt = max(attempts_made(req, runs_dir), 1) + 1
    # Repairs and advised implementers are normal coding work (owner v12 section 8):
    # never inherit the strong model of the job before them (an Opus advisor).
    model = model or "sonnet"
    decision, reasons = agent_utility(
        {"id": req}, {"attempt": attempt, "localized": localized(integration),
                      "retry_context_growth": _last_growth(req, runs_dir)})
    if advice.strip():
        prompt = advice.strip()
        reasons.append("fresh implementer from the advisor's packet")
    elif decision["session"] == "RESUME":
        prompt = repair_prompt(integration)
    else:
        prompt = failure_capsule(req, integration, run_rec, wt, base_sha, attempt)
    resume = str(sid) if decision["session"] == "RESUME" and not advice.strip() else None
    cmd = worker_command(model, max(1, turns // 2), budget / 2, resume=resume, prompt=prompt)
    return _relaunch(req, wt, cmd, meta, run_rec, kind="REPAIR", klass=klass, model=model,
                     base_sha=base_sha, wall=wall, repo=repo, repairs=1, runs_dir=runs_dir,
                     usd=budget / 2 + verifier_budget(budget / 2),
                     reason=f"{decision['session']}: {'; '.join(reasons)}")


def _relaunch(req: str, wt: Path, cmd: list[str], meta: dict, run_rec: dict, *, kind: str,
              klass: str, model: str, base_sha: str, wall: float, repo: Path, usd: float,
              repairs: int = 0, runs_dir: Path = RUNS_DIR, reason: str = "",
              attempt_id: str | None = None) -> Running:
    attempt_id = attempt_id or _attempt_for(req)
    spend_guard(attempt_id, "launch", req, kind, usd, runs_dir)
    started = time.time()
    offset = _stream_size(wt)
    branch = meta.get("branch") or run_rec.get("branch", "")
    _write_run_meta(wt, {**meta, "req": req, "mode": "SHIP", "klass": klass,
                         "base_sha": base_sha, "branch": branch, "model": model,
                         "started": started, "stream_offset": offset})
    run = Running(req, _start(cmd, wt), wt, started=started, mode="SHIP", klass=klass,
                  base_sha=base_sha, branch=branch, model=model, wall_s=wall, repo=repo,
                  continued=False, repairs=repairs, capsule=bool(meta.get("capsule")),
                  stream_offset=offset, kind=kind, reason=reason, attempt_id=attempt_id,
                  runs_dir=runs_dir)
    job_open(run, runs_dir)
    return run


def escalate(req: str, *, model: str = "opus", capsule_extra: str = "",
             worktrees: Path | None = None, runs_dir: Path = RUNS_DIR, repo: Path = ROOT,
             row: dict | None = None) -> Running:
    """A fresh session on a stronger model, with the P0 budget, in the
    requirement's existing worktree (or a new one from HEAD when it has none).
    Its prompt is the Failure Capsule plus any packet text the owner adds."""
    name = req.lower()
    wt = (worktrees or _worktrees_root()) / name
    run_rec = _read_json(runs_dir / f"{req}.json")
    integration = _read_json(runs_dir / f"{req}.integration.json")
    meta = _run_meta(wt) if wt.is_dir() else {}
    base_sha = meta.get("base_sha") or run_rec.get("base_sha") or ""
    turns, budget, wall = BUDGETS["P0"]
    attempt_id = _attempt_for(req)
    usd = budget + verifier_budget(budget)
    if not wt.is_dir():
        row = row or next((r for r in load_rows() if r["id"] == req), None)
        if row is None:
            raise KernelError(f"unknown requirement {req!r}")
        spend_guard(attempt_id, "launch", req, "ESCALATE", usd, runs_dir)  # before the worktree exists
        try:
            base_sha = create_worktree(wt, name, repo=repo)
        except BaseException:
            spend_release(attempt_id, runs_dir)
            raise
        (wt / ".build").mkdir(parents=True, exist_ok=True)
        (wt / ".build" / "packet.md").write_text(
            packet_text(row, load_findings(req, runs_dir), "P0"), encoding="utf-8", newline="\n")
        meta = {"capsule": False}
    if not base_sha:
        raise KernelError(f"{req} has no base commit to escalate against")
    attempt = attempts_made(req, runs_dir) + 1
    prompt = failure_capsule(req, integration, run_rec, wt, base_sha, attempt)
    if capsule_extra.strip():
        prompt += "\n## Owner's packet\n" + capsule_extra.strip()
    cmd = worker_command(model, turns, budget, prompt=prompt)
    return _relaunch(req, wt, cmd, meta, run_rec, kind="ESCALATE", klass="P0", model=model,
                     base_sha=base_sha, wall=wall, repo=repo, runs_dir=runs_dir, usd=usd,
                     attempt_id=attempt_id,
                     reason=f"ESCALATE: attempt {attempt} is a fresh {model} session")


ADVISOR_PROMPT = ("You are the advisor for a requirement that failed repeatedly. Do NOT edit "
                  "anything: you have read-only tools. Diagnose the root cause from the failure "
                  "capsule below, then end with a diagnosis packet as your final message: the "
                  "cause, the exact files and lines to change, the design to follow, and the "
                  "tests that prove it. A fresh implementer will receive only that message.\n\n")


def advise(req: str, *, model: str = "opus", worktrees: Path | None = None,
           runs_dir: Path = RUNS_DIR, repo: Path = ROOT) -> Running:
    """A strong-model, read-only job that diagnoses a repeatedly failing
    requirement in its worktree. Its final message is saved as the advice
    packet (finalize); the next repair is a fresh implementer on that packet."""
    wt = (worktrees or _worktrees_root()) / req.lower()
    if not wt.is_dir():
        raise KernelError(f"{req} has no worktree at {wt}")
    run_rec = _read_json(runs_dir / f"{req}.json")
    integration = _read_json(runs_dir / f"{req}.integration.json")
    meta = _run_meta(wt)
    base_sha = meta.get("base_sha") or run_rec.get("base_sha")
    if not base_sha:
        raise KernelError(f"{req} has no base commit to advise against")
    attempt = attempts_made(req, runs_dir) + 1
    prompt = ADVISOR_PROMPT + failure_capsule(req, integration, run_rec, wt, base_sha, attempt)
    turns, budget, wall = ADVISOR_BUDGET
    cmd = worker_command(model, turns, budget, tools=SCOUT_TOOLS, prompt=prompt)
    return _relaunch(req, wt, cmd, meta, run_rec, kind="ADVISOR", klass=meta.get("klass") or "NORMAL",
                     model=model, base_sha=base_sha, wall=wall, repo=repo, runs_dir=runs_dir, usd=budget,
                     reason=f"ADVISOR: attempt {attempt}, read-only {model} diagnosis, no edits")


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


def _events(path: Path, offset: int = 0) -> list[dict]:
    """The stream's events from byte `offset` on (earlier runs share the file)."""
    out: list[dict] = []
    if path.exists():
        text = path.read_bytes()[offset:].decode("utf-8", errors="replace")
        for line in text.splitlines():
            try:
                ev = json.loads(line)
            except ValueError:
                continue
            if isinstance(ev, dict):
                out.append(ev)
    return out


def _results(events: list[dict]) -> list[dict]:
    return [e for e in events if e.get("type") == "result"]


def _stream_facts(path: Path) -> dict:
    res = _results(_events(path))
    return res[-1] if res else {}


def _content_len(content) -> int:
    if isinstance(content, str):
        return len(content)
    if isinstance(content, list):
        return sum(len(c.get("text", "")) for c in content if isinstance(c, dict))
    return 0


def read_metrics(events: list[dict]) -> dict:
    """Post-hoc orientation cost from the stream: Read calls, the bytes they
    returned, Reads and searches (Grep/Glob/Bash), all tool calls, and the input
    tokens spent before the first Edit/Write. Tolerates missing keys."""
    reads: set[str] = set()
    n_reads, before_edit, edited, nbytes = 0, 0, False, 0
    searches, calls, tokens = 0, 0, 0
    for ev in events:
        msg = ev.get("message")
        blocks = msg.get("content") if isinstance(msg, dict) else None
        if not isinstance(blocks, list):
            continue
        used = [b.get("name") for b in blocks if ev.get("type") == "assistant"
                and isinstance(b, dict) and b.get("type") == "tool_use"]
        if ev.get("type") == "assistant" and not edited:
            if any(u in ("Edit", "Write") for u in used):
                edited = True
            else:
                usage = msg.get("usage") if isinstance(msg.get("usage"), dict) else {}
                tokens += sum(usage.get(k) or 0 for k in
                              ("input_tokens", "cache_read_input_tokens",
                               "cache_creation_input_tokens"))
        searches += sum(u in ("Grep", "Glob", "Bash") for u in used)
        calls += len(used)
        for b in blocks:
            if not isinstance(b, dict):
                continue
            if ev.get("type") == "assistant" and b.get("type") == "tool_use":
                if b.get("name") == "Read":
                    n_reads += 1
                    before_edit += not edited
                    reads.add(str(b.get("id")))
            elif b.get("type") == "tool_result" and str(b.get("tool_use_id")) in reads:
                nbytes += _content_len(b.get("content"))
    return {"reads": n_reads, "read_bytes": nbytes, "reads_before_first_edit": before_edit,
            "searches": searches, "tool_calls": calls,
            "tokens_before_first_edit": tokens if edited else None}


def _evidence(wt: Path, name: str = "evidence.json") -> dict:
    try:
        data = json.loads((wt / ".build" / name).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _changes(wt: Path) -> str:
    """Porcelain status of the worktree, excluding the kernel's own .build/."""
    return _git(["status", "--porcelain", "--", ".", ":(exclude).build"], wt, 60)


def commit_subject(req: str, evidence: dict) -> str:
    """The worker's one-line subject when it wrote a usable one; else the
    summary's opening words, cut at a word boundary."""
    own = evidence.get("commit_subject")
    if isinstance(own, str) and own.strip() and "\n" not in own.strip() and len(own.strip()) <= 72:
        return own.strip()
    first = (str(evidence.get("summary", "")).splitlines() or [""])[0].strip()
    if len(first) > 60:
        cut = first[:61].rfind(" ")
        first = first[:cut] if cut > 0 else first[:60]
    return f"build({req}): {first}"


def commit_body(req: str, evidence: dict) -> str:
    return f"{str(evidence.get('summary', '')).strip()}\n\nBuild run: {req}".strip()


def _commit_run(run: Running, evidence: dict) -> tuple[str, list[str]]:
    """Commit the worker's diff on its build branch as ONE commit from base_sha
    (build_integrate cherry-picks only that commit, so a repair squashes onto the
    first run's commit); ('', []) when nothing changed since base_sha."""
    if not run.base_sha:
        return "", []
    head = _git(["rev-parse", "HEAD"], run.wt, 30)
    if not _changes(run.wt):
        if head == run.base_sha:
            return "", []
        return head, _git(["diff", "--name-only", run.base_sha, head], run.wt, 60).splitlines()
    if head != run.base_sha:
        _git(["reset", "--soft", run.base_sha], run.wt, 60)
    # Stage everything, then unstage the kernel's own files. An exclude
    # pathspec would do it in one step, but git refuses a pathspec naming a
    # path .gitignore already ignores, and this repository ignores .build/.
    _git(["add", "-A"], run.wt, 60)
    _git(["rm", "-r", "-q", "--cached", "--ignore-unmatch", ".build"], run.wt, 60)
    _git(["-c", "user.name=DarshPro07", "-c", "user.email=DarshPro07", "commit",
          "-m", commit_subject(run.req, evidence), "-m", commit_body(run.req, evidence)],
         run.wt, 120)
    sha = _git(["rev-parse", "HEAD"], run.wt, 30)
    return sha, _git(["diff", "--name-only", run.base_sha, sha], run.wt, 60).splitlines()


def _first_json_with(text: str, key: str) -> dict:
    """The first JSON object in `text` that has `key`; {} when there is none."""
    dec, i = json.JSONDecoder(), 0
    while (i := text.find("{", i)) != -1:
        try:
            obj, end = dec.raw_decode(text, i)
        except ValueError:
            i += 1
            continue
        if isinstance(obj, dict) and key in obj:
            return obj
        i = end
    return {}


def _scout_outcome(run: Running, runs_dir: Path, final: str = "") -> dict:
    """Keep valid findings as runs_dir/<REQ>.scout.json - from the scout's final
    message, or a findings file an older packet asked for; {} when invalid."""
    found = _first_json_with(final or "", "risk") or _evidence(run.wt, "findings.json")
    ok = bool(found) and found.get("risk") in ("LOW", "MEDIUM", "HIGH")
    if ok:
        (runs_dir / f"{run.req}.scout.json").write_text(json.dumps(found, indent=2) + "\n",
                                                        encoding="utf-8", newline="\n")
    return found if ok else {}


# ------------------------------------------------------ invocation ledger

def ledger_entries(runs_dir: Path = RUNS_DIR) -> list[dict]:
    """Every ledger line, oldest first; unreadable lines are skipped."""
    out: list[dict] = []
    try:
        text = (runs_dir / INVOCATIONS_FILE).read_text(encoding="utf-8")
    except OSError:
        return out
    for line in text.splitlines():
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        if isinstance(entry, dict) and entry.get("attempt_id"):
            out.append(entry)
    return out


def ledger_append(entry: dict, runs_dir: Path = RUNS_DIR) -> bool:
    """Append one invocation; a second line for the same attempt_id is a no-op
    (a controller restart re-finalizes an attempt it already recorded)."""
    if any(e["attempt_id"] == entry["attempt_id"] for e in ledger_entries(runs_dir)):
        return False
    runs_dir.mkdir(parents=True, exist_ok=True)
    with open(runs_dir / INVOCATIONS_FILE, "a", encoding="utf-8", newline="\n") as fh:
        fh.write(json.dumps(entry) + "\n")
    return True


def _cost_where(runs_dir: Path, **match) -> float:
    return round(sum(float(e.get("cost_usd") or 0.0) for e in ledger_entries(runs_dir)
                     if all(e.get(k) == v for k, v in match.items())), 4)


def invocation_cost(attempt_id: str, runs_dir: Path = RUNS_DIR) -> float:
    return _cost_where(runs_dir, attempt_id=attempt_id)


def wave_cost(wave_id: str, runs_dir: Path = RUNS_DIR) -> float:
    return _cost_where(runs_dir, wave_id=wave_id) if wave_id else 0.0


def run_cost(program_run_id: str, runs_dir: Path = RUNS_DIR) -> float:
    return _cost_where(runs_dir, program_run_id=program_run_id) if program_run_id else 0.0


def lifetime_cost(req: str, runs_dir: Path = RUNS_DIR) -> float:
    return _cost_where(runs_dir, req=req)


def _local_day(at: str):
    try:
        return datetime.fromisoformat(at).astimezone().date()
    except (TypeError, ValueError):
        return None


def day_cost(day, runs_dir: Path = RUNS_DIR) -> float:
    """Every paid call on one of the owner's local days (the ledger stamps UTC),
    inside a program or not: the owner's spend cap is per day (2026-10-02)."""
    return round(sum(float(e.get("cost_usd") or 0.0) for e in ledger_entries(runs_dir)
                     if _local_day(str(e.get("at") or "")) == day), 4)


def _context_tokens(entry: dict) -> int:
    return sum(int(entry.get(k) or 0) for k in
               ("input_tokens", "cache_read_tokens", "cache_creation_tokens"))


def _own_results(events: list[dict]) -> list[dict]:
    """This attempt's result events, each counted once. A stream can carry a
    foreign session's result (not one this attempt's init events announced) and a
    result event written twice; neither may add cost."""
    sessions = {e.get("session_id") for e in events if e.get("type") == "system"
                and e.get("subtype") in (None, "init") and e.get("session_id")}
    seen: set = set()
    out: list[dict] = []
    for r in _results(events):
        if sessions and r.get("session_id") and r["session_id"] not in sessions:
            continue
        key = r.get("uuid") or ((r.get("session_id"), r.get("num_turns"))
                                if r.get("session_id") else None)
        if key is not None:
            if key in seen:
                continue
            seen.add(key)
        out.append(r)
    return out


def finalize(run: Running, reason: str | None, runs_dir: Path = RUNS_DIR) -> dict:
    """Write data/build_runs/<req>.json from the result events and evidence;
    a DONE SHIP run is committed on its branch, a scout's findings are kept."""
    log = run.wt / ".build" / "stream.jsonl"
    events = _events(log, run.stream_offset)
    results = _own_results(events)
    res = results[-1] if results else {}
    runs_dir.mkdir(parents=True, exist_ok=True)
    leased = lease_of(run.attempt_id, runs_dir)  # read before job_close releases it
    predicted = (float(leased["gb"]) if leased else predict_gb(run.req, run.klass, run.mode, runs_dir)[0])
    error = None if run.peak_rss_mb is None else round(run.peak_rss_mb / 1024 - predicted, 3)
    predicted = round(predicted, 3)
    scout = run.mode == "SCOUT"
    advisor = run.kind == "ADVISOR"
    ev = (_scout_outcome(run, runs_dir, str(res.get("result") or "")) if scout
          else {} if advisor else _evidence(run.wt))
    if reason:
        state = reason
    elif run.proc.returncode != 0 or not res or res.get("is_error"):
        state = "FAILED"
    elif advisor:
        state = "DONE" if str(res.get("result") or "").strip() else "FAILED"
    elif scout:
        state = "DONE" if ev else "FAILED"
    elif ev.get("status") in ("DONE", "BLOCKED"):
        state = ev["status"]
    else:
        state = "FAILED"
    summary = ev.get("gap" if scout else "summary", "")
    commit, files, commit_error = "", [], ""
    if advisor and state == "DONE":
        advice_path(run.req, runs_dir).write_text(str(res["result"]).strip() + "\n",
                                                  encoding="utf-8", newline="\n")
    if state == "DONE" and not scout and not advisor:
        try:
            commit, files = _commit_run(run, ev)
        except KernelError as exc:
            state, commit_error = "FAILED", str(exc)
    usage = [r.get("usage") or {} for r in results]
    total = lambda key: sum(u.get(key, 0) for u in usage)  # noqa: E731
    rec = {"requirement": run.req, "state": state,
           "turns": sum(r.get("num_turns", 0) for r in results),
           "input_tokens": total("input_tokens"),
           "cache_read_tokens": total("cache_read_input_tokens"),
           "cache_creation_tokens": total("cache_creation_input_tokens"),
           "output_tokens": total("output_tokens"),
           "cost_usd": sum(r.get("total_cost_usd", 0.0) for r in results),
           "wall_seconds": round(time.time() - run.started, 1),
           "summary": summary, "worktree": str(run.wt),
           "mode": run.mode, "budget_class": run.klass, "base_sha": run.base_sha,
           "branch": run.branch, "commit": commit, "files_changed": files,
           "evidence": ev, "continued": run.continued,
           "session_id": res.get("session_id", ""), "reads": read_metrics(events),
           "capsule": run.capsule, "peak_rss_mb": run.peak_rss_mb,
           "predicted_gb": predicted, "error_gb": error}
    at =datetime.now(timezone.utc).isoformat(timespec="seconds")
    prior = [e for e in ledger_entries(runs_dir) if e["req"] == run.req
             and e["attempt_id"] != run.attempt_id and _context_tokens(e) > 0]
    entry = {"attempt_id": run.attempt_id, "req": run.req,
             "kind": run.kind or ("SCOUT" if scout else "SHIP"),
             "program_run_id": run.program_run_id, "wave_id": run.wave_id, "model": run.model,
             "session_id": rec["session_id"], "cost_usd": rec["cost_usd"],
             "input_tokens": rec["input_tokens"], "output_tokens": rec["output_tokens"],
             "cache_read_tokens": rec["cache_read_tokens"],
             "cache_creation_tokens": rec["cache_creation_tokens"], "turns": rec["turns"],
             "wall_seconds": rec["wall_seconds"], "peak_rss_mb": run.peak_rss_mb, "at": at,
             "predicted_gb": predicted, "error_gb": error, "state": state, "budget_class": run.klass, "capsule": run.capsule,
             "mode": run.mode, "reads": rec["reads"]}
    if entry["kind"] in ("REPAIR", "RESUME") and prior and _context_tokens(prior[-1]):
        entry["retry_context_growth"] = round(_context_tokens(entry) / _context_tokens(prior[-1]), 3)
    ledger_append(entry, runs_dir)
    # The actual cost is in the ledger now: the attempt's reservations give way to it, and
    # a finished change keeps its verifier's budget until the integrator is done with it.
    spend_settle(run.attempt_id, run.req,
                 verifier_budget(rec["cost_usd"]) if state == "DONE" and not scout and not advisor else 0.0,
                 runs_dir)
    rec["attempt_id"] = run.attempt_id
    rec["kind"] = entry["kind"]
    job_close(run.attempt_id, "COMPLETE" if state in ("DONE", "BLOCKED") else "FAILED",
              "" if state in ("DONE", "BLOCKED") else state, runs_dir)
    rec["cumulative_cost_usd"] = lifetime_cost(run.req, runs_dir)
    if commit_error:
        rec["commit_error"] = commit_error
    if run.peak_rss_mb is not None:
        with open(runs_dir / PEAKS_FILE, "a", encoding="utf-8", newline="\n") as fh:
            fh.write(json.dumps({"req": run.req, "mode": run.mode, "budget_class": run.klass,
                                 "peak_rss_mb": run.peak_rss_mb,
                                 "at": datetime.now(timezone.utc).isoformat(timespec="seconds")}) + "\n")
    if not advisor:  # an advisor edits nothing: the last build's record stays the one to repair from
        (runs_dir / f"{run.req}.json").write_text(json.dumps(rec, indent=2) + "\n",
                                                  encoding="utf-8", newline="\n")
    if log.exists():
        stem = f"{run.req}.scout" if scout else f"{run.req}.advisor" if advisor else run.req
        shutil.copyfile(log, runs_dir / f"{stem}.stream.jsonl")
    if scout and run.base_sha:
        _remove_worktree(run.repo, run.wt)
    return rec


def _write_now(run: Running) -> bool:
    """One short resume for a read-only job that hit its turn or budget cap with
    nothing written: the same session, read-only tools, WRITE_NOW_TURNS turns."""
    res = _stream_facts(run.wt / ".build" / "stream.jsonl")
    sid = res.get("session_id")
    if not str(res.get("subtype", "")).startswith("error_max") or not sid:
        return False
    if str(res.get("result") or "").strip():
        return False
    if not spend_reserve(run.attempt_id, "continue", run.req, run.kind or "SCOUT", WRITE_NOW_USD,
                         run.runs_dir)[0]:
        return False  # the day's ceiling: the job ends with what it has
    cmd = worker_command(run.model or "sonnet", WRITE_NOW_TURNS, WRITE_NOW_USD, tools=SCOUT_TOOLS,
                         resume=sid, prompt=WRITE_NOW_PROMPT)
    run.proc = _start(cmd, run.wt)
    run.continued = True
    return True


def continuation(run: Running) -> bool:
    """Resume a SHIP worker that ran out of turns/budget with work on disk and
    no verdict, once, at half its class limits. True when it was restarted."""
    if run.continued:
        return False
    if getattr(run, "kind", "") == "ADVISOR" or run.mode == "SCOUT":
        return _write_now(run)
    if run.mode != "SHIP" or not run.base_sha:
        return False
    res = _stream_facts(run.wt / ".build" / "stream.jsonl")
    sid = res.get("session_id")
    if not str(res.get("subtype", "")).startswith("error_max") or not sid:
        return False
    if _evidence(run.wt).get("status") in ("DONE", "BLOCKED") or not _changes(run.wt):
        return False
    turns, budget, _ = BUDGETS[run.klass]
    if not spend_reserve(run.attempt_id, "continue", run.req, run.kind or "SHIP", budget / 2, run.runs_dir)[0]:
        return False  # the day's ceiling: the run is finalized with what is on disk
    cmd = worker_command(run.model, max(1, turns // 2), budget / 2, resume=sid,
                         prompt=CONTINUE_PROMPT)
    run.proc = _start(cmd, run.wt)
    run.continued = True
    return True


def tree_rss_mb(pid: int) -> float | None:
    """Resident memory (MB) of a process and all its descendants; None when
    psutil is missing or the process is gone."""
    if psutil is None:
        return None
    try:
        root = psutil.Process(pid)
        procs = [root, *root.children(recursive=True)]
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        return None
    total = 0
    for p in procs:
        try:
            total += p.memory_info().rss
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    return total / 1024 / 1024


def sample_rss(run: Running) -> None:
    mb = tree_rss_mb(run.proc.pid)
    if mb is not None and mb > (run.peak_rss_mb or 0.0):
        run.peak_rss_mb = round(mb, 1)


def watch(runs: list[Running], *, wall_s: float | None = None, stall_s: float = DEFAULT_STALL_S,
          poll_s: float = 5.0, runs_dir: Path = RUNS_DIR) -> list[dict]:
    """Poll to completion. Heartbeat = stream log growth. Zero model calls.
    The wall limit is each run's own unless `wall_s` overrides it."""
    live = {r.req: r for r in runs}
    last = {r.req: (-1, time.time()) for r in runs}
    done: list[dict] = []
    while live:
        for req, run in list(live.items()):
            reason = None
            if run.proc.poll() is None:
                now = time.time()
                sample_rss(run)
                p = run.wt / ".build" / "stream.jsonl"
                size = p.stat().st_size if p.exists() else 0
                if size != last[req][0]:
                    last[req] = (size, now)
                if now - run.started > (run.wall_s if wall_s is None else wall_s):
                    reason = "TIMEOUT"
                elif now - last[req][1] > stall_s:
                    reason = "STALLED"
                if reason is None:
                    continue
                kill_tree(run.proc)
            elif continuation(run):
                last[req] = (-1, time.time())
                continue
            done.append(finalize(run, reason, runs_dir))
            del live[req]
        if live:
            time.sleep(poll_s)
    return done


# ------------------------------------------------------------------ CLI

def run_records(runs_dir: Path = RUNS_DIR) -> list[Path]:
    """<REQ>.json run records only: not <REQ>.integration/.scout.json, not the
    stream copies, not the kernel's own state files (host profile, memory and
    spend leases)."""
    if not runs_dir.exists():
        return []
    return [p for p in sorted(runs_dir.glob("*.json"))
            if p.name.count(".") == 1
            and p.name not in (HOST_PROFILE.name, PEAKS_FILE, LEASES_FILE, SPEND_LEASES_FILE)]


_ROI_FIELDS = (("turns", ("turns",)), ("cost", ("cost_usd",)), ("wall", ("wall_seconds",)),
               ("reads", ("reads", "reads")), ("searches", ("reads", "searches")),
               ("tok_pre_edit", ("reads", "tokens_before_first_edit")))


def _field(rec: dict, path: tuple[str, ...]):
    for key in path:
        rec = rec.get(key) if isinstance(rec, dict) else None
    return rec if isinstance(rec, (int, float)) and not isinstance(rec, bool) else None


def invocation_units(runs_dir: Path = RUNS_DIR) -> list[dict]:
    """What ROI counts: every ledger invocation, plus the run record of a
    requirement that predates the ledger (it has no invocation line)."""
    units = list(ledger_entries(runs_dir))
    covered = {u.get("req") for u in units}
    for p in run_records(runs_dir):
        try:
            rec = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(rec, dict) and rec.get("requirement") not in covered:
            units.append(rec)
    return units


def roi_groups(runs_dir: Path = RUNS_DIR) -> dict[str, dict[str, dict]]:
    """Run records grouped by capsule arm (SHIP runs) and by budget class, each
    with run count, DONE count, field means over the records that have the
    field, a count of records missing it, and AgentROI = DONE runs per dollar."""
    buckets: dict[str, dict[str, list[dict]]] = {"capsule": {}, "class": {}}
    for rec in invocation_units(runs_dir):
        if rec.get("mode") != "SCOUT":
            arm = {True: "on", False: "off"}.get(rec.get("capsule"), "unknown")
            buckets["capsule"].setdefault(arm, []).append(rec)
        buckets["class"].setdefault(str(rec.get("budget_class") or "unknown"), []).append(rec)
    out: dict[str, dict[str, dict]] = {}
    for dim, groups in buckets.items():
        out[dim] = {}
        for name, recs in sorted(groups.items()):
            done = sum(r.get("state") == "DONE" for r in recs)
            cost = sum(_field(r, ("cost_usd",)) or 0.0 for r in recs)
            stat = {"runs": len(recs), "done": done, "done_share": done / len(recs),
                    "agent_roi": done / cost if cost > 0 else None, "missing": 0}
            for label, path in _ROI_FIELDS:
                vals = [v for v in (_field(r, path) for r in recs) if v is not None]
                stat["missing"] += len(recs) - len(vals)
                stat[label] = sum(vals) / len(vals) if vals else None
            out[dim][name] = stat
    return out


def roi_table(runs_dir: Path = RUNS_DIR) -> list[str]:
    def num(v, spec: str) -> str:
        return "n/a" if v is None else format(v, spec)

    head = (f"{'GROUP':<14}{'RUNS':>5}{'DONE%':>7}" + "".join(f"{lbl.upper():>13}" for lbl, _ in _ROI_FIELDS)
            + f"{'ROI/USD':>9}{'MISSING':>8}")
    out = []
    for dim, groups in roi_groups(runs_dir).items():
        out += [f"by {dim}", head]
        for name, s in groups.items():
            out.append(f"{name:<14}{s['runs']:>5}{s['done_share'] * 100:>6.0f}%"
                       + "".join(f"{num(s[lbl], '.2f'):>13}" for lbl, _ in _ROI_FIELDS)
                       + f"{num(s['agent_roi'], '.2f'):>9}{s['missing']:>8}")
    return out


def status_table(runs_dir: Path = RUNS_DIR) -> list[str]:
    out = [f"{'REQ':<12}{'STATE':<9}{'TURNS':>6}{'IN':>9}{'CACHE_R':>11}{'CACHE_W':>10}"
           f"{'OUT':>8}{'USD':>8}{'SEC':>8}"]
    # <REQ>.json only; <REQ>.scout.json holds findings, not a run record.
    for p in run_records(runs_dir):
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
    sub.add_parser("qualify")
    sub.add_parser("roi")
    sub.add_parser("resume").add_argument("req")
    sub.add_parser("repair").add_argument("req")
    es = sub.add_parser("escalate")
    es.add_argument("req")
    es.add_argument("--model", default="opus")
    es.add_argument("--packet", default=None, help="file of extra packet text for the prompt")
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
    la.add_argument("--max-turns", type=int, default=None)
    la.add_argument("--max-budget-usd", type=float, default=None)
    la.add_argument("--mode", choices=("SHIP", "SCOUT"), default=None)
    la.add_argument("--class", dest="klass", choices=sorted(BUDGETS), default=None)
    args = ap.parse_args(argv)

    try:
        if args.cmd == "status":
            print("\n".join(status_table()))
            return 0
        if args.cmd == "concurrency":
            n, why = concurrency()
            print(f"{n} ({why})")
            return 0
        if args.cmd == "roi":
            print("\n".join(roi_table()))
            return 0
        if args.cmd == "qualify":
            from friday import hardware_profile as HP
            # save=False: qualify() would otherwise write the live profile path.
            profile = HP.qualify(cause="build-host", save=False)
            print(f"stored {HP.store(profile, HOST_PROFILE)}")
            return 0
        if args.cmd == "resume":
            for rec in watch([resume(args.req)]):
                print(f"{rec['requirement']}: {rec['state']}")
            return 0
        if args.cmd == "repair":
            for rec in watch([repair(args.req)]):
                print(f"{rec['requirement']}: {rec['state']}")
            return 0
        if args.cmd == "escalate":
            extra = Path(args.packet).read_text(encoding="utf-8") if args.packet else ""
            for rec in watch([escalate(args.req, model=args.model, capsule_extra=extra)]):
                print(f"{rec['requirement']}: {rec['state']}")
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
            mode = args.mode or mode_for(row)
            res = launch(row, model=args.model, max_turns=args.max_turns,
                         max_budget=args.max_budget_usd, dry_run=args.dry_run,
                         mode=mode, klass=args.klass,
                         capsule=code_map(row) if mode == "SHIP" else "")
            print(json.dumps(res, indent=2) if args.dry_run else f"started {row['id']} in {res.wt}")
        else:  # run-wave
            n, why = concurrency(cap=args.cap)
            first = waves(queue, args.cap)[0][:n] if queue and n else []
            if not first:
                print(f"nothing to run (ready={len(queue)}, concurrency={n}: {why})")
                return 0
            modes = [mode_for(r) for r in first]
            started = [launch(r, dry_run=args.dry_run, mode=m,
                              capsule=code_map(r) if m == "SHIP" else "")
                       for r, m in zip(first, modes)]
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
