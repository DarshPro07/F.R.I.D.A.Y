"""Deterministic integrator (BH-OPT-02): proves a worker's commit, merges it, records it.

    python scripts/build_integrate.py REQ [--dry-run] [--no-ledger]

Reads data/build_runs/<REQ>.json and writes data/build_runs/<REQ>.integration.json.
Order: refuse -> collision gate -> acceptance fast path (new tests RED at the
base, GREEN with the commit) -> verifier (only when the change is risky) ->
cherry-pick -> affected tests -> ledger entry. Exit 0 INTEGRATED, 3 CONFLICT,
2 refused, 1 any other result. The integrator is the only ledger writer.
"""

from __future__ import annotations

import argparse
import ast
import contextlib
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent))

import build_kernel  # noqa: E402
import build_providers as P  # noqa: E402
from build_kernel import (ROOT, RUNS_DIR, VENV_PY, KernelError, agent_utility,  # noqa: E402
                          concurrency, load_rows)
import test_impact  # noqa: E402

LEDGER = ROOT / "scripts" / "prd_ledger.py"
PYTEST_BRIEF = HERE / "pytest_brief.py"
IDENTITY = ("-c", "user.name=DarshPro07", "-c", "user.email=DarshPro07")
PROTECTED = frozenset(f"friday/{n}.py" for n in (
    "policy", "confirmation", "sensitive_domains", "fsjail",
    "execution", "netguard", "evaluation", "honesty"))
VERDICTS = ("PASS", "FAIL", "INSUFFICIENT_EVIDENCE")
VERIFIER_TOOLS = ",".join(("Read", "Glob", "Grep", "Bash(git diff:*)",
                           "Bash(git log:*)", "Bash(git show:*)"))
PATCH_CAP = 40 * 1024
#: The project's deterministic gate (AGENTS.md): live tests need the network or
#: a running service, slow ones wall-clock time - neither proves a change.
#: A hung test fails on its own, by name, after GATE_TEST_S (pytest-timeout); TEST_S is only the
#: chunk's backstop. One fixed chunk limit could not tell a hang from a heavy chunk: MODEL-012's
#: chunk 1 (the harness's own git-and-subprocess tests) took 28 min with nothing hung (2026-10-05).
GATE_TEST_S = 300
GATE = ("-m", "not live and not slow", f"--timeout={GATE_TEST_S}")
_FAILED = re.compile(r"^(?:FAILED|ERROR) (\S+)")
VERIFIER_FLOOR_USD = build_kernel.VERIFIER_FLOOR_USD
VERIFIER_TURNS = build_kernel.VERIFIER_TURNS
GIT_S = 120
TEST_S = 3600
GATE_CHUNK = 40
WIDE_IMPACT_FILES = 60  # an affected set larger than this is its own, chunked job
QUEUE_FILE = "merge_queue.jsonl"
QUEUE_LOCK = "merge_queue.lock"
LANDING_FILE = "merge_landing.json"  # a pick on HEAD the gate has not settled yet
QUEUE_WAIT_S = 1800
QUEUE_POLL_S = 1.0
TIMEOUT = -999  # gate_run's rc when a chunk outlived TEST_S
VERIFIER_S = 900


# ---------------------------------------------------------------- git

def git(repo: Path, *args: str, check: bool = False, timeout: int = GIT_S) -> subprocess.CompletedProcess:
    res = subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True,
                         encoding="utf-8", errors="replace", timeout=timeout)
    if check and res.returncode != 0:
        raise KernelError(f"git {' '.join(args)} failed: {res.stderr.strip()}")
    return res


def rev(repo: Path, ref: str) -> str:
    return git(repo, "rev-parse", ref, check=True).stdout.strip()


def lines(text: str) -> list[str]:
    return [ln.strip().replace("\\", "/") for ln in text.splitlines() if ln.strip()]


# ---------------------------------------------------------------- gates

def load_run(req: str, runs_dir: Path = RUNS_DIR) -> dict:
    path = Path(runs_dir) / f"{req}.json"
    if not path.is_file():
        raise KernelError(f"no run record {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def refuse_reason(run: dict, repo: Path = ROOT) -> str:
    """Why this run may not be integrated right now, or ''."""
    if run.get("state") != "DONE":
        return f"run state is {run.get('state')!r}, not DONE"
    if not run.get("commit"):
        return "run has no commit"
    if git(repo, "status", "--porcelain", "--untracked-files=no").stdout.strip():
        return "the checkout has tracked changes"
    return ""


def collision(run: dict, repo: Path = ROOT) -> list[str]:
    """Files the commit touches that HEAD changed since the worker's base.
    Raises KernelError when the base is not an ancestor of HEAD."""
    base = run["base_sha"]
    if git(repo, "merge-base", "--is-ancestor", base, "HEAD").returncode != 0:
        raise KernelError(f"base {base[:10]} is not an ancestor of HEAD")
    moved = set(lines(git(repo, "diff", "--name-only", f"{base}..HEAD", check=True).stdout))
    return sorted(moved & {f.replace("\\", "/") for f in run.get("files_changed", [])})


def commit_test_files(run: dict, repo: Path = ROOT) -> list[str]:
    out = git(repo, "diff-tree", "--no-commit-id", "--name-only", "-r", "--diff-filter=AM",
              run["commit"], check=True).stdout
    return [f for f in lines(out) if f.startswith("tests/") and f.endswith(".py")
            and Path(f).name.startswith("test_")]


_HUNK = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@")


def touched_tests(sha: str, repo: Path = ROOT) -> list[str]:
    """`file::test` for each test function whose lines commit `sha` added or changed."""
    spans: dict[str, list[tuple[int, int]]] = {}
    path = ""
    for ln in git(repo, "show", "--format=", "-U0", sha, "--", "tests/", check=True).stdout.splitlines():
        if ln.startswith("+++ "):
            path = ln[6:] if ln.startswith("+++ b/") and ln.endswith(".py") else ""
        elif path and (m := _HUNK.match(ln)) and m.group(2) != "0":
            start = int(m.group(1))
            spans.setdefault(path, []).append((start, start + int(m.group(2) or 1) - 1))
    out = []
    for path, changed in spans.items():
        try:
            tree = ast.parse(git(repo, "show", f"{sha}:{path}", check=True).stdout)
        except (SyntaxError, ValueError):  # a file that does not parse names no test
            continue
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith("test"):
                first = min([d.lineno for d in node.decorator_list] + [node.lineno])
                if any(a <= node.end_lineno and first <= b for a, b in changed):
                    out.append(f"{path}::{node.name}")
    return out


def row_build_tests(req: str, python: str, cwd: Path, repo: Path = ROOT) -> list[str]:
    """Gate-runnable tests the row's own build commits on HEAD added or changed, newest first -
    each passed RED/GREEN when it was integrated. A PARTIAL row cites no tests in the ledger.
    Only those tests, not their files: 2026-10-09 BG-001 was confirmed citing twelve
    tests/test_autolearn.py payload tests because one of its commits had touched that file."""
    if not re.fullmatch(r"[A-Za-z0-9-]+", req):  # ids are plain; anything else is no git pattern
        return []
    shas = lines(git(repo, "log", "--format=%H", f"--grep=^Build run: {req}$", "HEAD", check=True).stdout)
    order: dict[str, int] = {}
    for sha in shas:  # newest first
        for key in touched_tests(sha, repo):
            order.setdefault(key, len(order))
    files = sorted({k.split("::")[0] for k in order if (repo / k.split("::")[0]).is_file()})
    if not files:
        return []

    def key(test_id: str) -> str:  # file::function, whatever class or parameters it sits in
        return f"{test_id.split('::')[0]}::{test_id.split('[')[0].split('::')[-1]}"

    picked = [t for t in collected_ids(python, cwd, files, runnable=True) if key(t) in order]
    return named_tests(sorted(picked, key=lambda t: order[key(t)]))


@contextlib.contextmanager
def temp_worktree(repo: Path, sha: str):
    tmp = Path(tempfile.mkdtemp(prefix="bi-wt-"))
    wt = tmp / "wt"
    try:
        git(repo, "worktree", "add", "--detach", str(wt), sha, check=True)
        yield wt
    finally:
        git(repo, "worktree", "remove", "--force", str(wt))
        git(repo, "worktree", "prune")
        shutil.rmtree(tmp, ignore_errors=True)


def run_pytest(python: str, cwd: Path, files: list[str]) -> tuple[int, str]:
    try:
        res = subprocess.run([python, "-m", "pytest", *files, *GATE, "-q", "--tb=line", "-p", "no:cacheprovider"],
                             cwd=cwd, capture_output=True, text=True, encoding="utf-8",
                             errors="replace", timeout=TEST_S)
    except subprocess.TimeoutExpired as exc:
        raise KernelError(f"pytest in {cwd} timed out after {TEST_S}s") from exc
    return res.returncode, res.stdout + res.stderr


def red_green(run: dict, tests: list[str], repo: Path = ROOT, python: str = VENV_PY) -> dict:
    """RED at the base with the commit's tests, GREEN at the commit."""
    if not tests:
        return {"result": "NO_TEST", "tests": []}
    with temp_worktree(repo, run["base_sha"]) as wt:
        git(wt, "checkout", run["commit"], "--", *tests, check=True)
        rc, out = run_pytest(python, wt, tests)
    # rc 1 = tests failed, 2 = collection error / interrupted; 0 and 5 (nothing collected) are not RED
    if rc not in (1, 2):
        return {"result": "NOT_RED", "tests": tests, "red_rc": rc}
    red_kind = "collection" if rc == 2 or "ERROR collecting" in out else "assertion"
    # The tests that prove the change: those that failed at the base. A collection
    # RED names none, so then every test the commit's files hold at the commit.
    proving = failed_ids(out.splitlines()) if red_kind == "assertion" else []
    with temp_worktree(repo, run["commit"]) as wt:
        rc, out = run_pytest(python, wt, tests)
        if rc == 0 and not named_tests(proving):
            proving = collected_ids(python, wt, tests)
    if rc != 0:
        return {"result": "NOT_GREEN", "tests": tests, "red_kind": red_kind, "green_rc": rc,
                "tail": out.strip().splitlines()[-5:]}
    return {"result": "OK", "tests": tests, "red_kind": red_kind, "proving": named_tests(proving)}


def collected_ids(python: str, cwd: Path, files: list[str], runnable: bool = False) -> list[str]:
    """The test ids in `files`; with `runnable`, only those the gate runs (no live or slow)."""
    marks = list(GATE[:2]) if runnable else []
    try:
        res = subprocess.run([python, "-m", "pytest", "--collect-only", "-q", "-p", "no:cacheprovider", *marks,
                              *files],
                             cwd=cwd, capture_output=True, text=True, encoding="utf-8",
                             errors="replace", timeout=TEST_S)
    except subprocess.TimeoutExpired:
        return []
    return [ln.strip() for ln in res.stdout.splitlines() if "::" in ln]


def named_tests(ids, limit: int = 12) -> list[str]:
    """Test ids as the ledger cites them - forward slashes, parameters dropped,
    each once, at most `limit`. A v7 row is VERIFIED only by a named test;
    2026-10-08: MCP-001 was written VERIFIED citing a test FILE (the RED/GREEN
    file list), the ledger check failed, and every controller landing stopped."""
    out: list[str] = []
    for test_id in ids or ():
        base = str(test_id).replace(chr(92), "/").split("[", 1)[0]
        if "::" in base and base not in out:
            out.append(base)
    return out[:limit]


# ---------------------------------------------------------------- verifier

def _protected(path: str) -> bool:
    f = path.replace("\\", "/")
    return f in PROTECTED or "secret" in f or "broker" in f


def needs_verifier(row: dict, files: list[str]) -> bool:
    decision, _ = agent_utility(row, {"protected": any(_protected(f) for f in files)})
    return decision["verify"] == "VERIFY"


def verifier_prompt(row: dict, run: dict, rg: dict, repo: Path = ROOT) -> str:
    stat = git(repo, "diff", "--stat", run["base_sha"], run["commit"]).stdout
    patch = git(repo, "diff", run["base_sha"], run["commit"]).stdout
    raw = patch.encode("utf-8")
    clipped = raw[:PATCH_CAP].decode("utf-8", errors="ignore")
    note = ""
    if len(raw) > PATCH_CAP:
        files = git(repo, "diff", "--name-only", run["base_sha"], run["commit"]).stdout.split()
        note = (f"\nTRUNCATED: the patch is {len(raw) // 1024} KB and only the first {PATCH_CAP // 1024} KB "
                "is shown. Read the changed files in this worktree before judging: " + ", ".join(files) + "\n")
    claimed = bool((run.get("evidence") or {}).get("acceptance_met"))
    scope = ("The implementer claims the full acceptance is met: judge the change against all of it."
             if claimed else
             "The implementer does NOT claim the full acceptance: this change is one step, and if it passes it "
             "integrates as PARTIAL, never VERIFIED. Judge it against the step the next action asks for now (its "
             "current slice, when one is named). PASS when that step is delivered correctly and every remaining "
             "acceptance item is listed honestly as a gap; acceptance items the next action defers to later slices "
             "are not failures. FAIL for a defect in what the change delivers, a claim the code does not back, or a "
             "gap that is hidden rather than listed.")
    return f"""You are an independent verifier. {scope}
Use the material below and read-only tools (read any file you need). Reply with ONE JSON object and nothing else:
{{"verdict": "PASS|FAIL|INSUFFICIENT_EVIDENCE", "requirement_ids": [...], "failing_criteria": [...], "evidence_refs": ["file:line", ...]}}

## Requirement {row['id']}
{row.get('summary', '')}

## Acceptance
{row.get('acceptance', '')}

## Next action in the ledger (owner rulings here are binding)
{row.get('next_action', '') or '(none)'}

## RED/GREEN result
{json.dumps(rg, indent=1)}

## Implementer evidence
{json.dumps(run.get('evidence', {}), indent=1)}

## git diff --stat
{stat}
## Patch (first {PATCH_CAP // 1024} KB)
{clipped}
{note}"""


def verifier_model(files: list[str]) -> str:
    """Opus adjudicates trust-boundary changes; other consequential work gets a
    fresh Sonnet - the verifier's independence is its fresh context."""
    return "opus" if any(_protected(f) for f in files) else "sonnet"


class VerifierUnavailable(KernelError):
    """No route can verify: every provider that could is out. The program treats
    it as providers out - it waits for the reset and counts no attempt."""

    def __init__(self, detail: str = "no verifier route", reset: float | None = None):
        super().__init__(detail)
        self.reset = reset


def verifier_command(cost_usd: float, files: list[str] = (), exe: str | None = None,
                     turns: int | None = None) -> list[str]:
    """Turns and budget sized to the change (build_kernel.verifier_turns and
    verifier_budget - the amount finalize held for it under the day's spend lease).

    When Claude is out but Codex is available on its ChatGPT login, the verifier is
    the read-only Codex shim (build_worker.py --verify): the same receipt shape, so
    parse_receipt works unchanged. With no route at all, VerifierUnavailable."""
    budget = build_kernel.verifier_budget(cost_usd, files)
    if not P.available("claude"):
        if P.available("codex") and P.codex_on_plan():
            models = P._codex_models(config=P.CODEX_CONFIG)
            if models:
                return [VENV_PY, str(ROOT / "scripts" / "build_worker.py"), "codex", models[0],
                        "--verify", "--read-only"]
        raise VerifierUnavailable("no verifier route", P.next_reset())
    return [exe or shutil.which("claude") or "claude", "-p", "--model", verifier_model(list(files)),
            "--allowedTools", VERIFIER_TOOLS,
            "--strict-mcp-config", "--settings", '{"disableAllHooks": true}',
            "--max-turns", str(turns or build_kernel.verifier_turns(files)), "--max-budget-usd", f"{budget:.2f}",
            "--output-format", "json"]


def default_runner(cmd: list[str], prompt: str, cwd: Path, timeout: int = VERIFIER_S) -> str:
    return subprocess.run(cmd, input=prompt, cwd=cwd, capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=timeout).stdout


def parse_receipt(stdout: str) -> dict:
    """The first JSON object carrying a verdict in the verifier's result text,
    with what the run cost and how it stopped when the CLI reported that."""
    text, meta = stdout, {}
    try:
        outer = json.loads(stdout)
    except ValueError:
        outer = None
    if isinstance(outer, dict):
        if isinstance(outer.get("result"), str):
            text = outer["result"]
        meta = {"cost_usd": outer.get("total_cost_usd"), "stop": outer.get("subtype")}
        if outer.get("is_error") or str(outer.get("subtype", "")).startswith("error"):
            errors = "; ".join(map(str, outer.get("errors") or []))
            return {"verdict": "INSUFFICIENT_EVIDENCE", "requirement_ids": [], "evidence_refs": [],
                    "failing_criteria": [f"verifier stopped: {outer.get('subtype')} {errors}".strip()],
                    **meta}
    receipt = _first_verdict(text)
    return {**receipt, **meta}


def _first_verdict(text: str) -> dict:
    dec, i = json.JSONDecoder(), 0
    while (i := text.find("{", i)) != -1:
        try:
            obj, end = dec.raw_decode(text, i)
        except ValueError:
            i += 1
            continue
        if isinstance(obj, dict) and obj.get("verdict") in VERDICTS:
            return {"verdict": obj["verdict"],
                    "requirement_ids": list(obj.get("requirement_ids") or []),
                    "failing_criteria": list(obj.get("failing_criteria") or []),
                    "evidence_refs": list(obj.get("evidence_refs") or [])}
        i = end if isinstance(obj, dict) else i + 1
    return {"verdict": "INSUFFICIENT_EVIDENCE", "requirement_ids": [],
            "failing_criteria": ["no parseable verdict in the verifier output"], "evidence_refs": []}


def verify_key(run: dict) -> str:
    """The spend-lease key of a run's verifier: the one its build launch reserved under."""
    return f"{run.get('attempt_id') or 'unattempted-' + str(run.get('requirement'))}:verify"


def run_verifier(row: dict, run: dict, rg: dict, *, repo: Path = ROOT, runner=None,
                 runs_dir: Path = RUNS_DIR, prompt: str | None = None, files: list[str] | None = None,
                 key: str | None = None) -> dict:
    """Run the fresh verifier inside the day's budget: it uses the lease its build
    launch reserved (or takes one under the hard ceiling; SpendCapReached when none
    fits, and nothing runs), records its actual cost, then gives the lease back.
    `prompt`, `files` and `key` replace the change's own (a confirm judges HEAD)."""
    cwd = Path(run.get("worktree") or repo)
    if not cwd.is_dir():
        cwd = repo
    files = run.get("files_changed", []) if files is None else files
    key = key or verify_key(run)
    turns = build_kernel.verifier_turns(files)
    receipt = _verify_once(row, run, rg, repo, runner, runs_dir, cwd, turns, key, prompt, files)
    if receipt.get("stop") == "error_max_turns" and turns < build_kernel.VERIFIER_MAX_TURNS:
        # It ran out of turns before judging anything (RES-001, 2026-10-08: 15 turns,
        # then INSUFFICIENT_EVIDENCE refused a change whose RED/GREEN had passed).
        # One more run with twice the turns; a second stop stands.
        more = min(build_kernel.VERIFIER_MAX_TURNS, 2 * turns)
        receipt = _verify_once(row, run, rg, repo, runner, runs_dir, cwd, more, key + ":more-turns", prompt, files)
    return receipt


def _verify_once(row: dict, run: dict, rg: dict, repo: Path, runner, runs_dir: Path, cwd: Path,
                  turns: int, key: str, prompt: str | None = None, files: list[str] | None = None) -> dict:
    files = run.get("files_changed", []) if files is None else files
    try:
        cmd = verifier_command(run.get("cost_usd", 0), files, turns=turns)
        # The Codex verifier shim reserves 0 for itself; only a Claude verifier has a budget.
        budget = float(cmd[cmd.index("--max-budget-usd") + 1]) if "--max-budget-usd" in cmd else 0.0
        build_kernel.spend_guard(key, "verify", str(run.get("requirement")), "VERIFIER", budget, runs_dir)
        out = (runner or default_runner)(cmd, prompt or verifier_prompt(row, run, rg, repo), cwd)
    except VerifierUnavailable as exc:
        # No route can verify: the program treats it as providers out - it waits
        # for the reset and counts no attempt.
        return {"verdict": "INSUFFICIENT_EVIDENCE", "requirement_ids": [],
                "failing_criteria": [f"no verifier route: {exc}"], "evidence_refs": [],
                "providers_reset": exc.reset}
    except (subprocess.SubprocessError, OSError) as exc:
        return {"verdict": "INSUFFICIENT_EVIDENCE", "requirement_ids": [],
                "failing_criteria": [f"verifier did not run: {type(exc).__name__}"], "evidence_refs": []}
    else:
        receipt = parse_receipt(out)
        record_verifier(run, receipt, runs_dir)
        return receipt
    finally:
        build_kernel.spend_release(key, runs_dir)


def record_verifier(run: dict, verifier: dict, runs_dir: Path = RUNS_DIR) -> None:
    """A paid verifier call is a line in the invocation ledger like any worker's,
    so the day's spend is read from one place. It carries no wave or program-run
    id: the program adds verifier cost to those totals itself."""
    cost = verifier.get("cost_usd")
    if not isinstance(cost, (int, float)) or cost <= 0:
        return
    build_kernel.ledger_append({
        "attempt_id": f"verifier-{uuid.uuid4()}", "req": run["requirement"], "kind": "VERIFIER",
        "model": verifier_model(list(run.get("files_changed") or [])), "cost_usd": float(cost),
        "at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "state": verifier.get("verdict")},
        Path(runs_dir))


# ---------------------------------------------------------------- merge

def affected(run: dict, extra: list[str], repo: Path = ROOT) -> list[str]:
    graph = test_impact.build_graph(test_impact.collect_sources(repo))
    hit = test_impact.affected_tests({f.replace("\\", "/") for f in run.get("files_changed", [])},
                                     graph, direct=True)
    return sorted(set(hit) | set(extra))


def mark_landing(runs_dir: Path, req: str, pre_head: str, commit: str, pid: int | None = None) -> None:
    """Record a pick the gate has not settled. A process killed now leaves it on HEAD un-gated:
    on 2026-10-09 BRAIN-001's b71a2ee sat under five integrations before anyone saw it.
    The next merge-queue turn takes it back (MergeQueue.recover_landing)."""
    Path(runs_dir).mkdir(parents=True, exist_ok=True)
    (Path(runs_dir) / LANDING_FILE).write_text(
        json.dumps({"req": req, "pid": pid or os.getpid(), "pre_head": pre_head, "commit": commit,
                    "at": time.time()}), encoding="utf-8", newline="\n")


def clear_landing(runs_dir: Path) -> None:
    (Path(runs_dir) / LANDING_FILE).unlink(missing_ok=True)


def merge(run: dict, tests_extra: list[str], repo: Path = ROOT, python: str = VENV_PY,
          runs_dir: Path = RUNS_DIR) -> dict:
    """Cherry-pick, run the affected tests, reset on failure (guarded). The pick stays marked
    as landing until it is reset here or its ledger commit is made."""
    pre_head = rev(repo, "HEAD")
    pick = git(repo, *IDENTITY, "cherry-pick", run["commit"])
    if pick.returncode != 0:
        git(repo, "cherry-pick", "--abort")
        return {"result": "CONFLICT", "pre_head": pre_head, "detail": pick.stderr.strip()[-300:]}
    integrated = rev(repo, "HEAD")
    mark_landing(runs_dir, run.get("requirement", ""), pre_head, integrated)
    tests = [t for t in affected(run, tests_extra, repo) if (repo / t).is_file()]
    chunks = -(-len(tests) // GATE_CHUNK)
    ok = {"result": "INTEGRATED", "pre_head": pre_head, "integrated_commit": integrated,
          "affected": tests, "summary": [], "pre_existing": [],
          "impact": "WIDE" if len(tests) > WIDE_IMPACT_FILES else "NORMAL",
          "gate_chunks": chunks, "gate_workers": gate_workers(len(tests))}
    if not tests:  # pytest with no paths would run the whole suite
        return ok
    rc, lines = gate_run(python, repo, tests)
    ok["summary"] = lines[:12]
    if rc in (0, 5):  # 5: everything affected is live/slow, nothing to run
        return ok
    timed_out = rc == TIMEOUT
    pre: set[str] = set()
    if not timed_out:
        failing = failed_ids(lines)
        pre = still_failing(failing, pre_head, repo, python)
        if failing and set(failing) <= pre:
            return {**ok, "pre_existing": sorted(pre)}
    failures = [ln.replace(m.group(1), m.group(1).replace(chr(92), "/")) for ln in lines
                if (m := _FAILED.match(ln)) and m.group(1).replace(chr(92), "/") not in pre][:20]
    parent = git(repo, "rev-parse", "HEAD^").stdout.strip()
    if parent == pre_head:
        git(repo, "reset", "--hard", pre_head, check=True)
        reset = "reset to pre_head"
    else:
        reset = "NOT reset: HEAD^ is not pre_head; inspect by hand"
    clear_landing(runs_dir)  # settled here: taken back, or reported for a person to inspect
    return {"result": "TEST_TIMEOUT" if timed_out else "FAILED_INTEGRATION", "pre_head": pre_head,
            "integrated_commit": "", "affected": tests, "summary": lines[:12], "reset": reset,
            "impact": ok["impact"], "gate_chunks": chunks, "gate_workers": ok["gate_workers"],
            "failures": failures or (lines[-1:] if timed_out else lines[:12]),
            "pre_existing": sorted(pre), **({"detail": f"{lines[-1]} ({len(tests)} affected); {reset}"}
                                            if timed_out else {})}


def gate_chunk(python: str, cwd: Path, tests: list[str]) -> tuple[int, list[str]]:
    """One pytest_brief subprocess; the full output's lines (the brief caps its
    failure list, the log does not). A timeout is TIMEOUT, never an exception."""
    try:
        res = subprocess.run([python, str(PYTEST_BRIEF), "--", *GATE, *tests], cwd=cwd, capture_output=True,
                             text=True, encoding="utf-8", errors="replace", timeout=TEST_S)
    except subprocess.TimeoutExpired:
        return TIMEOUT, []
    out = res.stdout
    log = next((ln.split("full log: ", 1)[1].strip() for ln in out.splitlines()
                if ln.startswith("full log: ")), "")
    if log and Path(log).is_file():
        out = Path(log).read_text(encoding="utf-8", errors="replace")
    return res.returncode, [ln.strip() for ln in out.splitlines()]


def gate_workers(n_tests: int) -> int:
    """Chunks the gate may run at once: only a WIDE set runs in parallel, and never
    more than the host governor's concurrency() slots (1 on this host)."""
    if n_tests <= WIDE_IMPACT_FILES:
        return 1
    slots, _ = concurrency()
    return max(1, min(slots, -(-n_tests // GATE_CHUNK)))


def gate_run(python: str, cwd: Path, tests: list[str]) -> tuple[int, list[str]]:
    """Run tests under the gate markers in chunks of GATE_CHUNK files so one huge
    affected set cannot outlive TEST_S. rc is 0 only when every chunk is 0 or 5;
    a chunk timeout stops the run with TIMEOUT and a line naming the chunk."""
    chunks = [tests[i:i + GATE_CHUNK] for i in range(0, len(tests), GATE_CHUNK)]
    worst, all_lines = 0, []
    workers = gate_workers(len(tests))
    if workers > 1:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            outcomes = list(pool.map(lambda c: gate_chunk(python, cwd, c), chunks))
    else:
        outcomes = None
    for n, chunk in enumerate(chunks, 1):
        rc, got = outcomes[n - 1] if outcomes else gate_chunk(python, cwd, chunk)
        all_lines += got
        if rc == TIMEOUT:
            all_lines.append(f"TIMEOUT: chunk {n}/{len(chunks)} ({len(chunk)} files) exceeded {TEST_S}s")
            return TIMEOUT, all_lines
        if rc not in (0, 5) and worst in (0, 5):
            worst = rc
    return worst, all_lines


def failed_ids(lines: list[str]) -> list[str]:
    """Failing test ids with forward slashes: pytest on Windows may print tests\\x.py,
    and a pre-existing failure compared by its other spelling was blamed on the change."""
    return sorted({m.group(1).replace(chr(92), "/") for ln in lines if (m := _FAILED.match(ln))})


def still_failing(ids: list[str], sha: str, repo: Path, python: str) -> set[str]:
    """Which of these tests fail at `sha` too - without the change. A test the
    change added does not exist there, so it is never excused."""
    if not ids:
        return set()
    with temp_worktree(repo, sha) as wt:
        present = [i for i in ids if (wt / i.split("::")[0]).is_file()]
        if not present:
            return set()
        _, lines = gate_run(python, wt, present)
    return set(failed_ids(lines)) & set(present)


# ---------------------------------------------------------------- ledger

def cited_files(row: dict, changed: list[str]) -> list[str]:
    """A VERIFIED row's file citations: what it already cites - a curated
    "path:line,line" keeps its lines - plus each changed code file not cited yet.
    2026-10-08: MCP-001's twelve firewall citations were replaced with the one
    file its last slice touched, and the test that follows them broke."""
    have = [str(f) for f in row.get("files") or []]
    paths = {f.partition(":")[0] for f in have}
    new = [f for f in (str(c).replace("\\", "/") for c in changed or [])
           if not f.startswith("tests/") and f not in paths]
    return have + new


def decide_status(run: dict, rg: dict, merged: dict, required: bool, verifier: dict | None) -> tuple[str, list[str]]:
    """VERIFIED only when every proof is present; else PARTIAL with the gaps."""
    gaps = list((run.get("evidence") or {}).get("gaps") or [])
    if not (run.get("evidence") or {}).get("acceptance_met"):
        gaps.append("implementer did not claim acceptance met")
    if rg.get("result") != "OK":
        gaps.append(f"RED/GREEN: {rg.get('result')}")
    if merged.get("result") != "INTEGRATED":
        gaps.append("affected tests did not pass")
    if required and (verifier or {}).get("verdict") != "PASS":
        gaps.append("verifier did not PASS")
    return ("PARTIAL" if gaps else "VERIFIED"), gaps


def _end_of_list(text: str, name: str = "PROGRESS_V7") -> tuple[int, int]:
    """(line index, char column) of the closing bracket of the PROGRESS_V7 list."""
    for node in ast.parse(text).body:
        target = node.target if isinstance(node, ast.AnnAssign) else (
            node.targets[0] if isinstance(node, ast.Assign) else None)
        if isinstance(target, ast.Name) and target.id == name and isinstance(node.value, ast.List):
            line = text.splitlines(keepends=True)[node.value.end_lineno - 1]
            col = len(line.encode("utf-8")[:node.value.end_col_offset - 1].decode("utf-8"))
            return node.value.end_lineno - 1, col
    raise KernelError(f"{name} not found")


def entry_text(req: str, status: str, impl: str, next_action: str, nl: str = "\n",
               evidence: str = "", extra: dict | None = None) -> str:
    lines = [f"    ({req!r}, {{",
             f'        "status": {status},',
             f'        "current_implementation": {impl!r},',
             f'        "next_action": {next_action!r},']
    if evidence:
        lines.append(f'        "evidence": {evidence!r},')
    for key, value in (extra or {}).items():
        lines.append(f'        "{key}": {value!r},')
    return nl.join((*lines, "    }),")) + nl


def evidence_text(run: dict, rg: dict, merged: dict, verifier: dict | None) -> str:
    """What this integration proved, for the ledger's VERIFIED rule."""
    counts = next((ln for ln in reversed(merged.get("summary") or []) if "passed" in ln), "passed")
    parts = [f"build run {run['requirement']}, commit {merged['integrated_commit'][:9]}",
             f"RED ({rg.get('red_kind')}) at {run['base_sha'][:9]} and GREEN at the commit for "
             + ", ".join(rg.get("tests") or []),
             f"affected tests {counts.strip()}"
             + (f" (failing before the change too: {', '.join(merged['pre_existing'])})"
                if merged.get("pre_existing") else "")]
    if verifier:
        cost = verifier.get("cost_usd")
        parts.append(f"fresh verifier {verifier.get('verdict')}"
                     + (f" (${cost:.2f})" if isinstance(cost, (int, float)) else ""))
    return "; ".join(parts)


def load_ledger(path: Path):
    spec = importlib.util.spec_from_file_location("prd_ledger_check", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def append_progress(path: Path, req: str, status: str, impl: str, next_action: str,
                    evidence: str = "", extra: dict | None = None) -> None:
    """Append one PROGRESS_V7 entry; restore the file byte-for-byte if it no longer loads.
    A VERIFIED entry needs evidence and a named test: `prd_ledger.py --check`
    refuses a VERIFIED v7 row without either. `extra` sets further row fields."""
    if status == "VERIFIED" and not evidence.strip():
        raise KernelError(f"{req}: a VERIFIED entry needs evidence")
    if status == "VERIFIED" and not (extra or {}).get("tests"):
        raise KernelError(f"{req}: a VERIFIED entry needs at least one named test")
    original = Path(path).read_bytes()
    text = original.decode("utf-8")
    nl = "\r\n" if "\r\n" in text else "\n"
    idx, col = _end_of_list(text)
    lines_ = text.splitlines(keepends=True)
    head, tail = lines_[idx][:col], lines_[idx][col:]
    entry = entry_text(req, status, impl, next_action, nl, evidence, extra)
    if head.strip():
        entry = head.rstrip() + nl + entry
        head = ""
    lines_[idx] = head + entry + tail
    Path(path).write_bytes("".join(lines_).encode("utf-8"))
    try:
        module = load_ledger(Path(path))
        module.apply_progress(module._load_v7(), module.PROGRESS_V7)
    except Exception as exc:
        Path(path).write_bytes(original)
        raise KernelError(f"ledger entry for {req} does not load ({type(exc).__name__}: {exc}); file restored") from exc


def commit_ledger(path: Path, req: str, status: str, repo: Path = ROOT) -> str:
    git(repo, "add", str(path), check=True)
    git(repo, *IDENTITY, "commit", "-m", f"docs(ledger): {req} -> {status} from build run",
        "--", str(path), check=True)
    return rev(repo, "HEAD")


def remove_worker(run: dict, repo: Path = ROOT) -> None:
    wt = run.get("worktree")
    if wt:
        git(repo, "worktree", "remove", "--force", str(wt))
    if run.get("branch"):
        git(repo, "branch", "-D", run["branch"])


# ---------------------------------------------------------------- driver

class MergeQueueBusy(KernelError):
    """The queue stayed busy past its wait: transient, nothing is wrong with the run (2026-10-09:
    MEM-003 waited 1800 s behind a 35-minute gate and was parked as if its integration were refused)."""


class MergeQueue:
    """Integrations run one at a time, first finished first, from an append-only
    log (runs_dir/merge_queue.jsonl). An entry whose process died drops out, so a
    crashed integrator cannot hold the line. The lock is held while a candidate
    is cherry-picked and gated; a turn first takes back what a killed one left."""

    def __init__(self, runs_dir: Path, wait_s: float = QUEUE_WAIT_S, poll_s: float = QUEUE_POLL_S,
                 repo: Path = ROOT):
        self.dir, self.wait_s, self.poll_s, self.repo = Path(runs_dir), wait_s, poll_s, Path(repo)
        self.log, self.lock = self.dir / QUEUE_FILE, self.dir / QUEUE_LOCK

    def _events(self) -> list[dict]:
        try:
            text = self.log.read_text(encoding="utf-8")
        except OSError:
            return []
        out = []
        for ln in text.splitlines():
            try:
                ev = json.loads(ln)
            except ValueError:
                continue
            if isinstance(ev, dict) and ev.get("req"):
                out.append(ev)
        return out

    def _append(self, ev: dict) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        with open(self.log, "a", encoding="utf-8", newline="\n") as fh:
            fh.write(json.dumps(ev) + "\n")

    @staticmethod
    def _alive(pid: int) -> bool:
        psutil = build_kernel.psutil
        return pid == os.getpid() or psutil is None or psutil.pid_exists(pid)

    def enqueue(self, req: str, completed: float) -> None:
        if all(e["req"] != req for e in self.pending()):
            self._append({"req": req, "event": "ENQUEUED", "completed": completed,
                          "pid": os.getpid(), "at": time.time()})

    def pending(self) -> list[dict]:
        """Waiting integrations, FIFO by completion time (enqueue order breaks ties)."""
        waiting: dict[str, dict] = {}
        for ev in self._events():
            if ev.get("event") == "ENQUEUED":
                waiting[ev["req"]] = ev
            else:
                waiting.pop(ev["req"], None)
        live = [e for e in waiting.values() if self._alive(int(e.get("pid") or 0))]
        return sorted(live, key=lambda e: (e.get("completed", 0.0), e.get("at", 0.0)))

    def finish(self, req: str, result: str) -> None:
        self._append({"req": req, "event": "DONE", "result": result, "at": time.time()})

    def _take_lock(self) -> bool:
        self.dir.mkdir(parents=True, exist_ok=True)
        for _ in range(2):
            try:
                fd = os.open(self.lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            except FileExistsError:
                try:
                    holder = int(json.loads(self.lock.read_text(encoding="utf-8")).get("pid") or 0)
                except (OSError, ValueError, AttributeError):
                    holder = 0
                if holder and self._alive(holder):
                    return False
                try:
                    self.lock.unlink()
                except FileNotFoundError:
                    pass
                continue
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump({"pid": os.getpid()}, fh)
            return True
        return False

    @contextmanager
    def turn(self, req: str):
        """Wait until `req` heads the queue and the lock is free, then hold it."""
        deadline = time.monotonic() + self.wait_s
        while True:
            head = next(iter(self.pending()), None)
            if head and head["req"] == req and self._take_lock():
                break
            if time.monotonic() >= deadline:
                raise MergeQueueBusy(f"merge queue: {req} waited {self.wait_s:.0f}s behind "
                                     f"{head['req'] if head else 'a held lock'}")
            time.sleep(self.poll_s)
        try:
            self.recover_landing()
            yield
        finally:
            try:
                self.lock.unlink()
            except FileNotFoundError:
                pass

    def recover_landing(self) -> str:
        """Take back a pick whose integrator died before the gate settled it, when HEAD is
        still exactly that pick on its pre_head. Runs under the lock, so no landing is in
        progress; a marker whose process lives is left alone (a live integrator settles its
        own, and a reused pid only delays this). Returns RESET, LEFT or '' (nothing to do)."""
        path = self.dir / LANDING_FILE
        try:
            mark = json.loads(path.read_text(encoding="utf-8"))
        except OSError:
            return ""
        except ValueError:
            mark = None
        # logged under its own key: an event for the run's id would drop a re-queued entry of it
        key = f"landing:{mark.get('req') or '?'}" if isinstance(mark, dict) else "landing:?"
        if not isinstance(mark, dict) or not mark.get("commit") or not mark.get("pre_head"):
            self._append({"req": key, "event": "LANDING_UNREADABLE", "at": time.time()})
            path.unlink(missing_ok=True)
            return "LEFT"
        if self._alive(int(mark.get("pid") or 0)):
            return ""
        head = rev(self.repo, "HEAD")
        outcome = "LEFT"  # main moved past it: the landing finished, or something was built on it
        if head == mark["commit"] and git(self.repo, "rev-parse", "HEAD^").stdout.strip() == mark["pre_head"]:
            git(self.repo, "reset", "--hard", mark["pre_head"], check=True)
            outcome = "RESET"
        self._append({"req": key, "event": "LANDING_RECOVERED", "outcome": outcome, "commit": mark["commit"],
                      "pre_head": mark["pre_head"], "head": head, "at": time.time()})
        path.unlink(missing_ok=True)
        return outcome


def integrate(req: str, *, repo: Path = ROOT, runs_dir: Path = RUNS_DIR, ledger_path: Path = LEDGER,
              python: str = VENV_PY, rows: list[dict] | None = None, runner=None,
              dry_run: bool = False, no_ledger: bool = False, queue: MergeQueue | None = None) -> dict:
    """Integrate one finished run through the merge queue (a dry run touches nothing)."""
    kw = dict(repo=repo, runs_dir=runs_dir, ledger_path=ledger_path, python=python, rows=rows,
              runner=runner, dry_run=dry_run, no_ledger=no_ledger)
    if dry_run:
        return _integrate(req, **kw)
    queue = queue or MergeQueue(Path(runs_dir), repo=repo)
    try:
        completed = (Path(runs_dir) / f"{req}.json").stat().st_mtime
    except OSError:
        completed = time.time()
    queue.enqueue(req, completed)
    result = "ERROR"
    try:
        with queue.turn(req):
            out = _integrate(req, **kw)
            result = out["result"]
            return out
    finally:
        queue.finish(req, result)
        # Whatever happened, the verifier reservation this run's launch held is done with:
        # used and recorded, not needed, or the run went back for a rebase or repair.
        build_kernel.spend_release(verify_key(build_kernel._read_json(Path(runs_dir) / f"{req}.json")),
                                   runs_dir)


#: An owner ruling that keeps a row's extra scope (00-owner-directive-2026-10-08e): its
#: acceptance alone does not close it, so a no-change run cannot be confirmed.
KEEP_SCOPE = re.compile(r"keep (?:this )?extra scope inside the row", re.I)


def carried_rulings(next_action: str) -> str:
    """The owner rulings in a row's next_action, which the gap list an integration
    writes must carry: rewritten as a bare "GAPS: ..." they were dropped, and with
    them the keep-scope guard (CYBER-002, 2026-10-09)."""
    parts = re.split(r"\s+\|\s+|\n+", next_action or "")
    return " | ".join(p.strip() for p in parts if "RULING" in p)


def confirm_prompt(row: dict, tests: list[str], head: str) -> str:
    return f"""You are an independent verifier. A build worker changed nothing and claims the acceptance below is
ALREADY met by the code at HEAD ({head[:9]}). Judge the existing code against all of it: read the files the
ledger names and the named tests. PASS only when every acceptance item is met by code that exists now and the
named tests prove it; FAIL for any item that is missing, stubbed or only claimed; INSUFFICIENT_EVIDENCE when
you cannot tell. Reply with ONE JSON object and nothing else:
{{"verdict": "PASS|FAIL|INSUFFICIENT_EVIDENCE", "requirement_ids": [...], "failing_criteria": [...], "evidence_refs": ["file:line", ...]}}

## Requirement {row['id']}
{row.get('summary', '')}

## Acceptance
{row.get('acceptance', '')}

## Next action in the ledger (owner rulings here are binding)
{row.get('next_action', '') or '(none)'}

## Files the ledger names
{', '.join(map(str, row.get('files') or [])) or '(none)'}

## Named tests (they pass at HEAD)
{chr(10).join(tests)}
"""


def _confirm_existing(req: str, run: dict, row: dict, out: dict, done, *, repo: Path, runs_dir: Path,
                      ledger_path: Path, python: str, runner, dry_run: bool, no_ledger: bool) -> dict:
    """The worker changed nothing and says the acceptance is already met. 2026-10-09:
    BCON-001, CYBER-002, DUR-001 and EVAL-001 were each refused 'run has no commit' and
    parked in one night, waiting for a controller to flip them by hand. Prove it on HEAD
    instead: the row's named tests (or, for a row that cites none yet, the tests its own
    build commits brought) pass there, and a fresh verifier finds the acceptance met by
    the existing code; then the row is VERIFIED on that evidence."""
    if KEEP_SCOPE.search(row.get("next_action") or ""):
        return done("NOT_CONFIRMED", detail="the owner keeps extra scope in this row; the worker built none of it")
    head = git(repo, "rev-parse", "HEAD", check=True).stdout.strip()
    tests = named_tests(row.get("tests"))
    files = sorted({str(t).split("::")[0] for t in row.get("tests") or [] if str(t).split("::")[0].endswith(".py")})
    if not tests and files:
        with temp_worktree(repo, head) as wt:
            tests = named_tests(collected_ids(python, wt, files, runnable=True))
    if not tests:  # PARTIAL so far (2026-10-09: BRAIN-001 refused "names no test")
        with temp_worktree(repo, head) as wt:
            tests = row_build_tests(req, python, wt, repo)
    if not tests:
        return done("NOT_CONFIRMED", detail="the row names no test to confirm the acceptance with")
    if dry_run:
        return done("DRY_RUN", tests=tests)
    with temp_worktree(repo, head) as wt:
        rc, output = run_pytest(python, wt, tests)
        if rc == 5 and (own := row_build_tests(req, python, wt, repo)):
            # The gate deselects live and slow tests: pytest ran none of the named ones (2026-10-09:
            # BG-001 cites one @slow test, and exit 5 was reported as "the named tests fail at HEAD").
            tests = own
            rc, output = run_pytest(python, wt, tests)
    if rc == 5:
        return done("NOT_CONFIRMED", detail="the gate ran none of the named tests (live or slow only)",
                    tests=tests)
    if rc != 0:
        return done("NOT_CONFIRMED", detail="the named tests fail at HEAD", tests=tests,
                    tail=output.strip().splitlines()[-5:])
    code = [str(f).split(":")[0] for f in row.get("files") or []]
    out["verifier"] = verdict = run_verifier(row, run, {}, repo=repo, runner=runner, runs_dir=runs_dir,
                                             prompt=confirm_prompt(row, tests, head), files=code,
                                             key=verify_key(run) + ":confirm")
    if verdict.get("verdict") != "PASS":
        return done("NOT_CONFIRMED", detail="the verifier did not find the acceptance met at HEAD", tests=tests)
    if not no_ledger:
        summary = str((run.get("evidence") or {}).get("summary") or run.get("summary") or "")[:300]
        evidence = (f"Confirmed at {head[:9]}: the build worker found the acceptance already met and changed "
                    f"nothing; {', '.join(tests)} pass at HEAD and an independent verifier judged the existing "
                    f"code to meet it. Worker: {summary}")
        append_progress(Path(ledger_path), req, "VERIFIED", row.get("current_implementation", ""), "", evidence,
                        {"tests": tests, "files": cited_files(row, [])})
        commit_ledger(Path(ledger_path), req, "VERIFIED", repo)
        out["ledger_status"] = "VERIFIED"
    return done("CONFIRMED", tests=tests)


def _integrate(req: str, *, repo: Path, runs_dir: Path, ledger_path: Path, python: str,
               rows: list[dict] | None, runner, dry_run: bool, no_ledger: bool) -> dict:
    run = load_run(req, runs_dir)
    why = refuse_reason(run, repo)
    # A DONE run with no commit that says the acceptance is already met is confirmed on
    # HEAD rather than refused - on a clean checkout, like any integration.
    confirm = why == "run has no commit" and bool((run.get("evidence") or {}).get("acceptance_met"))
    if confirm and git(repo, "status", "--porcelain", "--untracked-files=no").stdout.strip():
        why, confirm = "the checkout has tracked changes", False
    if why and not confirm:
        raise KernelError(f"refusing {req}: {why}")
    row = next((r for r in (rows if rows is not None else load_rows()) if r["id"] == req), None)
    if row is None:
        raise KernelError(f"{req} is not a ledger row")
    out = {"requirement": req, "result": "", "red_green": {}, "verifier": None,
           "integrated_commit": "", "ledger_status": ""}

    def done(result: str, **extra) -> dict:
        out.update(result=result, **extra)
        if not dry_run:
            (Path(runs_dir) / f"{req}.integration.json").write_text(
                json.dumps(out, indent=2), encoding="utf-8", newline="\n")
        return out

    if confirm:
        return _confirm_existing(req, run, row, out, done, repo=repo, runs_dir=runs_dir, ledger_path=ledger_path,
                                 python=python, runner=runner, dry_run=dry_run, no_ledger=no_ledger)
    try:
        clash = collision(run, repo)
    except KernelError as exc:  # the base is no longer an ancestor of HEAD
        return done("REBASE_REQUIRED", detail=str(exc))
    if clash:  # park it with its worktree kept: the worker rebases onto HEAD
        return done("REBASE_REQUIRED", detail=f"HEAD changed {clash} since {run['base_sha'][:10]}")
    tests = commit_test_files(run, repo)
    out["red_green"] = rg = red_green(run, tests, repo, python)
    if rg["result"] != "OK":
        return done(rg["result"])
    required = needs_verifier(row, run.get("files_changed", []))
    if dry_run:
        return done("DRY_RUN", verifier_required=required)
    if required:
        out["verifier"] = verifier = run_verifier(row, run, rg, repo=repo, runner=runner, runs_dir=runs_dir)
        if verifier["verdict"] != "PASS":
            return done("VERIFIER_FAIL")
    else:
        verifier = None
    merged = merge(run, tests, repo, python, runs_dir=runs_dir)
    out.update(impact=merged.get("impact", "NORMAL"), gate_chunks=merged.get("gate_chunks", 0),
               gate_workers=merged.get("gate_workers", 1))
    if merged["result"] != "INTEGRATED":
        return done(merged["result"], detail=merged.get("detail") or merged.get("reset", ""),
                    failures=merged.get("failures", []), affected=merged.get("affected", []),
                    pre_existing_failures=merged.get("pre_existing", []))
    out["integrated_commit"] = merged["integrated_commit"]
    out["pre_existing_failures"] = merged.get("pre_existing", [])
    if not no_ledger:
        status, gaps = decide_status(run, rg, merged, required, verifier)
        named = named_tests(rg.get("proving") or rg.get("tests"))
        if status == "VERIFIED" and not named:
            # A v7 row is VERIFIED only by a named test (prd_ledger --check); a file is not one.
            status, gaps = "PARTIAL", gaps + ["RED/GREEN named no test the ledger can cite"]
        impl = f"{(run.get('evidence') or {}).get('summary', '')} (build run, commit {merged['integrated_commit'][:9]})"
        rulings = carried_rulings(row.get("next_action") or "")
        nxt = "" if status == "VERIFIED" else "GAPS: " + "; ".join(gaps) + (f" | {rulings}" if rulings else "")
        proof = evidence_text(run, rg, merged, verifier) if status == "VERIFIED" else ""
        cited = ({"tests": named,
                  "files": cited_files(row, run.get("files_changed", []))}
                 if status == "VERIFIED" else None)
        append_progress(Path(ledger_path), req, status, impl, nxt, proof, cited)
        commit_ledger(Path(ledger_path), req, status, repo)
        out["ledger_status"] = status
        out["gaps"] = gaps
    clear_landing(runs_dir)  # gated, and in the ledger: nothing left to take back
    remove_worker(run, repo)
    return done("INTEGRATED")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("req")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--no-ledger", action="store_true")
    args = ap.parse_args(argv)
    try:
        out = integrate(args.req, dry_run=args.dry_run, no_ledger=args.no_ledger)
    except KernelError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    print(f"{out['requirement']}: {out['result']} red_green={out['red_green'].get('result')} "
          f"verifier={(out['verifier'] or {}).get('verdict', '-')} commit={out['integrated_commit'][:9] or '-'} "
          f"ledger={out['ledger_status'] or '-'}")
    return {"INTEGRATED": 0, "DRY_RUN": 0, "CONFLICT": 3, "REBASE_REQUIRED": 3}.get(out["result"], 1)


if __name__ == "__main__":
    sys.exit(main())
