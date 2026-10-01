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
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent))

from build_kernel import ROOT, RUNS_DIR, VENV_PY, KernelError, load_rows  # noqa: E402
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
VERIFIER_FLOOR_USD = 1.0
VERIFIER_TURNS = 15
GIT_S = 120
TEST_S = 900
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
        res = subprocess.run([python, "-m", "pytest", *files, "-q", "--tb=line", "-p", "no:cacheprovider"],
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
    with temp_worktree(repo, run["commit"]) as wt:
        rc, out = run_pytest(python, wt, tests)
    if rc != 0:
        return {"result": "NOT_GREEN", "tests": tests, "red_kind": red_kind, "green_rc": rc,
                "tail": out.strip().splitlines()[-5:]}
    return {"result": "OK", "tests": tests, "red_kind": red_kind}


# ---------------------------------------------------------------- verifier

def _protected(path: str) -> bool:
    f = path.replace("\\", "/")
    return f in PROTECTED or "secret" in f or "broker" in f


def needs_verifier(row: dict, files: list[str]) -> bool:
    return row.get("priority") == "P0" or any(_protected(f) for f in files)


def verifier_prompt(row: dict, run: dict, rg: dict, repo: Path = ROOT) -> str:
    stat = git(repo, "diff", "--stat", run["base_sha"], run["commit"]).stdout
    patch = git(repo, "diff", run["base_sha"], run["commit"]).stdout
    clipped = patch.encode("utf-8")[:PATCH_CAP].decode("utf-8", errors="ignore")
    return f"""You are an independent verifier. Judge whether this change meets the requirement.
Use only the material below and read-only tools. Reply with ONE JSON object and nothing else:
{{"verdict": "PASS|FAIL|INSUFFICIENT_EVIDENCE", "requirement_ids": [...], "failing_criteria": [...], "evidence_refs": ["file:line", ...]}}

## Requirement {row['id']}
{row.get('summary', '')}

## Acceptance
{row.get('acceptance', '')}

## RED/GREEN result
{json.dumps(rg, indent=1)}

## Implementer evidence
{json.dumps(run.get('evidence', {}), indent=1)}

## git diff --stat
{stat}
## Patch (first {PATCH_CAP // 1024} KB)
{clipped}
"""


def verifier_model(files: list[str]) -> str:
    """Opus adjudicates trust-boundary changes; other consequential work gets a
    fresh Sonnet - the verifier's independence is its fresh context."""
    return "opus" if any(_protected(f) for f in files) else "sonnet"


def verifier_command(cost_usd: float, files: list[str] = (), exe: str | None = None) -> list[str]:
    """A quarter of the implementer's cost, never below the floor: the verifier
    pays for its own system prompt before it reads a line of the change."""
    budget = max(VERIFIER_FLOOR_USD, 0.25 * float(cost_usd or 0))
    return [exe or shutil.which("claude") or "claude", "-p", "--model", verifier_model(list(files)),
            "--allowedTools", VERIFIER_TOOLS,
            "--strict-mcp-config", "--settings", '{"disableAllHooks": true}',
            "--max-turns", str(VERIFIER_TURNS), "--max-budget-usd", f"{budget:.2f}",
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


def run_verifier(row: dict, run: dict, rg: dict, *, repo: Path = ROOT, runner=None) -> dict:
    cwd = Path(run.get("worktree") or repo)
    if not cwd.is_dir():
        cwd = repo
    try:
        out = (runner or default_runner)(verifier_command(run.get("cost_usd", 0), run.get("files_changed", [])),
                                         verifier_prompt(row, run, rg, repo), cwd)
    except (subprocess.SubprocessError, OSError) as exc:
        return {"verdict": "INSUFFICIENT_EVIDENCE", "requirement_ids": [],
                "failing_criteria": [f"verifier did not run: {type(exc).__name__}"], "evidence_refs": []}
    return parse_receipt(out)


# ---------------------------------------------------------------- merge

def affected(run: dict, extra: list[str], repo: Path = ROOT) -> list[str]:
    graph = test_impact.build_graph(test_impact.collect_sources(repo))
    hit = test_impact.affected_tests({f.replace("\\", "/") for f in run.get("files_changed", [])},
                                     graph, direct=True)
    return sorted(set(hit) | set(extra))


def merge(run: dict, tests_extra: list[str], repo: Path = ROOT, python: str = VENV_PY) -> dict:
    """Cherry-pick, run the affected tests, reset on failure (guarded)."""
    pre_head = rev(repo, "HEAD")
    pick = git(repo, *IDENTITY, "cherry-pick", run["commit"])
    if pick.returncode != 0:
        git(repo, "cherry-pick", "--abort")
        return {"result": "CONFLICT", "pre_head": pre_head, "detail": pick.stderr.strip()[-300:]}
    integrated = rev(repo, "HEAD")
    tests = [t for t in affected(run, tests_extra, repo) if (repo / t).is_file()]
    res = subprocess.run([python, str(PYTEST_BRIEF), "--", *tests], cwd=repo, capture_output=True,
                         text=True, encoding="utf-8", errors="replace", timeout=TEST_S)
    summary = res.stdout.strip().splitlines()[:12]
    if res.returncode == 0:
        return {"result": "INTEGRATED", "pre_head": pre_head, "integrated_commit": integrated,
                "affected": tests, "summary": summary}
    parent = git(repo, "rev-parse", "HEAD^").stdout.strip()
    if parent == pre_head:
        git(repo, "reset", "--hard", pre_head, check=True)
        reset = "reset to pre_head"
    else:
        reset = "NOT reset: HEAD^ is not pre_head; inspect by hand"
    return {"result": "FAILED_INTEGRATION", "pre_head": pre_head, "integrated_commit": "",
            "affected": tests, "summary": summary, "reset": reset}


# ---------------------------------------------------------------- ledger

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


def entry_text(req: str, status: str, impl: str, next_action: str, nl: str = "\n") -> str:
    return nl.join((f"    ({req!r}, {{",
                    f'        "status": {status},',
                    f'        "current_implementation": {impl!r},',
                    f'        "next_action": {next_action!r},',
                    "    }),")) + nl


def load_ledger(path: Path):
    spec = importlib.util.spec_from_file_location("prd_ledger_check", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def append_progress(path: Path, req: str, status: str, impl: str, next_action: str) -> None:
    """Append one PROGRESS_V7 entry; restore the file byte-for-byte if it no longer loads."""
    original = Path(path).read_bytes()
    text = original.decode("utf-8")
    nl = "\r\n" if "\r\n" in text else "\n"
    idx, col = _end_of_list(text)
    lines_ = text.splitlines(keepends=True)
    head, tail = lines_[idx][:col], lines_[idx][col:]
    entry = entry_text(req, status, impl, next_action, nl)
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

def integrate(req: str, *, repo: Path = ROOT, runs_dir: Path = RUNS_DIR, ledger_path: Path = LEDGER,
              python: str = VENV_PY, rows: list[dict] | None = None, runner=None,
              dry_run: bool = False, no_ledger: bool = False) -> dict:
    run = load_run(req, runs_dir)
    why = refuse_reason(run, repo)
    if why:
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

    clash = collision(run, repo)
    if clash:
        return done("CONFLICT", detail=f"HEAD changed {clash} since {run['base_sha'][:10]}")
    tests = commit_test_files(run, repo)
    out["red_green"] = rg = red_green(run, tests, repo, python)
    if rg["result"] != "OK":
        return done(rg["result"])
    required = needs_verifier(row, run.get("files_changed", []))
    if dry_run:
        return done("DRY_RUN", verifier_required=required)
    if required:
        out["verifier"] = verifier = run_verifier(row, run, rg, repo=repo, runner=runner)
        if verifier["verdict"] != "PASS":
            return done("VERIFIER_FAIL")
    else:
        verifier = None
    merged = merge(run, tests, repo, python)
    if merged["result"] != "INTEGRATED":
        return done(merged["result"], detail=merged.get("detail") or merged.get("reset", ""))
    out["integrated_commit"] = merged["integrated_commit"]
    if not no_ledger:
        status, gaps = decide_status(run, rg, merged, required, verifier)
        impl = f"{(run.get('evidence') or {}).get('summary', '')} (build run, commit {merged['integrated_commit'][:9]})"
        nxt = "" if status == "VERIFIED" else "GAPS: " + "; ".join(gaps)
        append_progress(Path(ledger_path), req, status, impl, nxt)
        commit_ledger(Path(ledger_path), req, status, repo)
        out["ledger_status"] = status
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
    return {"INTEGRATED": 0, "DRY_RUN": 0, "CONFLICT": 3}.get(out["result"], 1)


if __name__ == "__main__":
    sys.exit(main())
