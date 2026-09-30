"""Deterministic build scheduler (BH-OPT-01 step 4): code decides, no model in the loop.

    python scripts/build_kernel.py ready
    python scripts/build_kernel.py waves [--cap N]
    python scripts/build_kernel.py concurrency
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
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT))

import code_capsule  # noqa: E402
import prd_ledger as PL  # noqa: E402

DEPS_FILE = ROOT / "docs" / "plans" / "build-harness" / "dependencies.json"
RUNS_DIR = ROOT / "data" / "build_runs"
VENV_PY = "F:/F.R.I.D.A.Y/.venv-build/Scripts/python.exe"
DEFAULT_CAP = 2
DEFAULT_WALL_S = 45 * 60
DEFAULT_STALL_S = 10 * 60
ALLOWED_TOOLS = ",".join(("Read", "Edit", "Write", "Glob", "Grep",
                          "Bash(git status:*)", "Bash(git diff:*)", "Bash(git log:*)",
                          f"Bash({VENV_PY}:*)"))
_PRIORITY = {"P0": 0, "P1": 1, "P2": 2, "P3": 3}
# Read-only scouting. The one write is the findings file, path-scoped.
SCOUT_TOOLS = ",".join(("Read", "Glob", "Grep", "Write(.build/findings.json)",
                        "Bash(git status:*)", "Bash(git diff:*)", "Bash(git log:*)"))
# class -> (max turns, max budget USD, wall seconds)
BUDGETS = {"SCOUT": (8, 0.75, 15 * 60), "MICRO": (12, 1.0, 20 * 60),
           "NORMAL": (25, 3.0, 45 * 60), "COMPLEX": (40, 6.0, 75 * 60),
           "P0": (40, 6.0, 75 * 60)}
READ_FILES = {"SCOUT": 12, "MICRO": 6, "NORMAL": 8, "COMPLEX": 12, "P0": 12}
WORKER_PROMPT = "Read .build/packet.md and carry out the requirement it describes."
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
    if load_findings(row["id"], runs_dir) is not None:
        return "SHIP"
    if not surface(row) or not row.get("tests") or not str(row.get("acceptance", "")).strip():
        return "SCOUT"
    return "SHIP"


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
   "acceptance_met": true|false, "gaps": [], "blockers": []}}
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
- Finish by writing `.build/findings.json`:
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


def _start(cmd: list[str], wt: Path) -> subprocess.Popen:
    """Start a worker appending to the stream log, so a continuation keeps it."""
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
    base_sha = create_worktree(wt, name, repo=repo, detach=scout)
    (wt / ".build").mkdir(parents=True, exist_ok=True)
    text = scout_packet_text(row) if scout else packet_text(row, findings, klass, capsule)
    (wt / ".build" / "packet.md").write_text(text, encoding="utf-8", newline="\n")
    proc = _start(cmd, wt)
    return Running(row["id"], proc, wt, mode=mode, klass=klass, base_sha=base_sha,
                   branch="" if scout else f"build/{name}", model=model, wall_s=wall, repo=repo)


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


def _events(path: Path) -> list[dict]:
    out: list[dict] = []
    if path.exists():
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
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
    """Post-hoc read cost from the stream: Read calls, the bytes they returned,
    and how many Reads came before the first Edit/Write. Tolerates missing keys."""
    reads: set[str] = set()
    n_reads, before_edit, edited, nbytes = 0, 0, False, 0
    for ev in events:
        msg = ev.get("message")
        blocks = msg.get("content") if isinstance(msg, dict) else None
        if not isinstance(blocks, list):
            continue
        for b in blocks:
            if not isinstance(b, dict):
                continue
            if ev.get("type") == "assistant" and b.get("type") == "tool_use":
                if b.get("name") == "Read":
                    n_reads += 1
                    before_edit += not edited
                    reads.add(str(b.get("id")))
                elif b.get("name") in ("Edit", "Write"):
                    edited = True
            elif b.get("type") == "tool_result" and str(b.get("tool_use_id")) in reads:
                nbytes += _content_len(b.get("content"))
    return {"reads": n_reads, "read_bytes": nbytes, "reads_before_first_edit": before_edit}


def _evidence(wt: Path, name: str = "evidence.json") -> dict:
    try:
        data = json.loads((wt / ".build" / name).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _changes(wt: Path) -> str:
    """Porcelain status of the worktree, excluding the kernel's own .build/."""
    return _git(["status", "--porcelain", "--", ".", ":(exclude).build"], wt, 60)


def _commit_run(run: Running, summary: str) -> tuple[str, list[str]]:
    """Commit the worker's diff on its build branch; ('', []) when nothing changed."""
    if not run.base_sha or not _changes(run.wt):
        return "", []
    _git(["add", "-A", "--", ".", ":(exclude).build"], run.wt, 60)
    msg = f"build({run.req}): {(summary.splitlines() or [''])[0].strip()[:60]}"
    _git(["-c", "user.name=DarshPro07", "-c", "user.email=DarshPro07", "commit", "-m", msg],
         run.wt, 120)
    sha = _git(["rev-parse", "HEAD"], run.wt, 30)
    return sha, _git(["diff", "--name-only", run.base_sha, sha], run.wt, 60).splitlines()


def _scout_outcome(run: Running, runs_dir: Path) -> dict:
    """Keep a valid findings file as runs_dir/<REQ>.scout.json; {} when missing or invalid."""
    found = _evidence(run.wt, "findings.json")
    ok = bool(found) and found.get("risk") in ("LOW", "MEDIUM", "HIGH")
    if ok:
        (runs_dir / f"{run.req}.scout.json").write_text(json.dumps(found, indent=2) + "\n",
                                                        encoding="utf-8", newline="\n")
    return found if ok else {}


def finalize(run: Running, reason: str | None, runs_dir: Path = RUNS_DIR) -> dict:
    """Write data/build_runs/<req>.json from the result events and evidence;
    a DONE SHIP run is committed on its branch, a scout's findings are kept."""
    log = run.wt / ".build" / "stream.jsonl"
    events = _events(log)
    results = _results(events)
    res = results[-1] if results else {}
    runs_dir.mkdir(parents=True, exist_ok=True)
    scout = run.mode == "SCOUT"
    ev = _scout_outcome(run, runs_dir) if scout else _evidence(run.wt)
    if reason:
        state = reason
    elif run.proc.returncode != 0 or not res or res.get("is_error"):
        state = "FAILED"
    elif scout:
        state = "DONE" if ev else "FAILED"
    elif ev.get("status") in ("DONE", "BLOCKED"):
        state = ev["status"]
    else:
        state = "FAILED"
    summary = ev.get("gap" if scout else "summary", "")
    commit, files, commit_error = "", [], ""
    if state == "DONE" and not scout:
        try:
            commit, files = _commit_run(run, str(summary))
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
           "session_id": res.get("session_id", ""), "reads": read_metrics(events)}
    if commit_error:
        rec["commit_error"] = commit_error
    (runs_dir / f"{run.req}.json").write_text(json.dumps(rec, indent=2) + "\n",
                                              encoding="utf-8", newline="\n")
    if log.exists():
        stem = f"{run.req}.scout" if scout else run.req
        shutil.copyfile(log, runs_dir / f"{stem}.stream.jsonl")
    if scout and run.base_sha:
        _remove_worktree(run.repo, run.wt)
    return rec


def continuation(run: Running) -> bool:
    """Resume a SHIP worker that ran out of turns/budget with work on disk and
    no verdict, once, at half its class limits. True when it was restarted."""
    if run.continued or run.mode != "SHIP" or not run.base_sha:
        return False
    res = _stream_facts(run.wt / ".build" / "stream.jsonl")
    sid = res.get("session_id")
    if not str(res.get("subtype", "")).startswith("error_max") or not sid:
        return False
    if _evidence(run.wt).get("status") in ("DONE", "BLOCKED") or not _changes(run.wt):
        return False
    turns, budget, _ = BUDGETS[run.klass]
    cmd = worker_command(run.model, max(1, turns // 2), budget / 2, resume=sid,
                         prompt=CONTINUE_PROMPT)
    run.proc = _start(cmd, run.wt)
    run.continued = True
    return True


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

def status_table(runs_dir: Path = RUNS_DIR) -> list[str]:
    out = [f"{'REQ':<12}{'STATE':<9}{'TURNS':>6}{'IN':>9}{'CACHE_R':>11}{'CACHE_W':>10}"
           f"{'OUT':>8}{'USD':>8}{'SEC':>8}"]
    # <REQ>.json only; <REQ>.scout.json holds findings, not a run record.
    for p in sorted(runs_dir.glob("*.json")) if runs_dir.exists() else []:
        if p.name.count(".") != 1:
            continue
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
            mode = args.mode or mode_for(row)
            res = launch(row, model=args.model, max_turns=args.max_turns,
                         max_budget=args.max_budget_usd, dry_run=args.dry_run,
                         mode=mode, klass=args.klass,
                         capsule=code_map(row) if mode == "SHIP" else "")
            print(json.dumps(res, indent=2) if args.dry_run else f"started {row['id']} in {res.wt}")
        else:  # run-wave
            n = concurrency(cap=args.cap)
            first = waves(queue, args.cap)[0][:n] if queue and n else []
            if not first:
                print(f"nothing to run (ready={len(queue)}, concurrency={n})")
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
