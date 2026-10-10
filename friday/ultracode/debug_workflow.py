"""UCW-005: the Autonomous Debugging workflow, friday.debug v1.

The workflow is a UCW-002 definition (`docs/plans/ultracode/workflows/debug.json`):

    reproduce -> locate -> hypothesize -> fix -> verify

`reproduce`, `locate` and `verify` are deterministic executors that call no
model. `reproduce` runs the failing test command and turns its per-test results
into a black-box style trace; `locate` hands that trace to TRAJ-002
(`friday.trace_debugger.debug`) and names the first critical failing step;
`hypothesize` and `fix` are the only model steps; `verify` is the gate.

`verify` accepts a fix only when all of these hold, and otherwise names the
first rule it broke in a stable `code`:

- the agent did not commit (HEAD_MOVED), did not change what git hides
  (GIT_CONFIG_CHANGED for .git/info/exclude or a git config, HIDDEN_CHANGE for
  assume-unchanged/skip-worktree, HIDDEN_FILE for an ignored file it created),
  and did not touch test configuration (TEST_CONFIG_CHANGED, .gitignore
  included), delete a test (TEST_DELETED), or weaken one (TEST_WEAKENED);
- it names at least one regression test (NO_REGRESSION_TEST) that is new, i.e.
  lives in a test file the fix added or extended and did not exist before
  (NOT_A_NEW_TEST);
- every regression test FAILS on the pre-fix tree (baseline commit plus only
  the changed test files, in a scratch copy) and PASSES on the fixed tree
  (NOT_RED_BEFORE, NOT_GREEN_AFTER);
- the whole test command then passes (STILL_FAILING);
- the semantic guard: the BASELINE test files, untouched, pass against the
  fixed sources (a copy of the baseline commit with only the non-test changes
  overlaid). A test silenced by an inserted early return or a shadowing
  assignment passes on the fixed tree, but not there (TEST_WEAKENED). The
  removed-lines/skip-marker checks stay as a first, cheaper layer.

`autonomous_debug` is the bounded loop around it. After `k` attempts without
progress (progress = fewer failing tests than any earlier attempt, measured
only when the red/green proof held) the next attempt uses a different strategy
and, when a higher rung of `models` exists, a different model, and the change
is recorded. The loop stops at its token budget or its time budget, or when nothing
new is left to try, and in every case returns - and, given `evidence_path`,
writes - the evidence: reproduction, first critical step, every attempt with
its verdict, the strategy/model changes, and what was spent.

Assumed (owner not asked):
- The loop owns `workdir`: it must be a clean git tree with no ignored files
  and every rejected attempt is undone with `git reset --hard <baseline>` +
  `git clean -fdx` (and the git files that hide things are put back). Point it
  at an isolated worktree, never at a tree with someone's edits.
- Tests and git run with `forge.scrubbed_env()` plus the caller's `env`, never
  Friday's `os.environ`: agent-written code gets no credentials.
- The time budget is checked before every model call and before every test
  run, and each test run is capped at the time left, so the loop overshoots by
  at most one model call. Only `__pycache__` and `.pytest_cache` are exempt
  from the ignored-file rule.
- A fixed tree is left uncommitted; committing is the orchestrator's call.
- Token spend is the agent's own `tokens_used` when it reports one, otherwise
  a rough chars/4 estimate of the result; an agent can under-report.
- It cannot prove a new assertion is meaningful; the red/green proof and the
  baseline-tests semantic guard are what make a regression test worth anything.
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Callable, Mapping, Sequence

from friday import objectives as O
from friday import trace_debugger
from friday.forge import scrubbed_env
from friday.ultracode import definition as D

DEFINITION_PATH = (Path(__file__).resolve().parents[2]
                   / "docs" / "plans" / "ultracode" / "workflows" / "debug.json")
DEFAULT_STRATEGIES = ("minimal_patch", "root_cause_rewrite", "bisect_first_failing_step")
_BAD = ("FAILED", "ERROR")
_OUTPUT_CAP = 4000
_DIFF_CAP = 4000
_MAX_REGRESSION_TESTS = 20
_CONFIG_FILES = frozenset({"conftest.py", "pytest.ini", "tox.ini", "setup.cfg", "pyproject.toml",
                          ".gitignore"})
_OK = frozenset({"PASSED", "SKIPPED", "XFAIL", "XPASS"})
_CACHE_DIRS = frozenset({"__pycache__", ".pytest_cache"})
_GIT_STATE = ("info/exclude", "config", "config.worktree")
_SKIP = re.compile(r"\b(skip|skipif|xfail|importorskip|skipTest)\b")
_VERBOSE = re.compile(r"^(\S+?::.+?) (PASSED|FAILED|ERROR|SKIPPED|XFAIL|XPASS)\b")
_SUMMARY = re.compile(r"^(FAILED|ERROR) (\S+?)(?: - (.*))?$")
_COLLECTED = re.compile(r"^(\S+?::\S.*)$")


class DebugError(RuntimeError):
    """The loop cannot start or a tool it depends on failed."""


class _Stop(Exception):
    """A budget ran out mid-attempt."""

    def __init__(self, status: str) -> None:
        super().__init__(status)
        self.status = status


def load_definition() -> D.Definition:
    """The versioned friday.debug definition, validated by UCW-002."""
    return D.parse_definition(DEFINITION_PATH.read_text(encoding="utf-8"))


# --- running tools -----------------------------------------------------------------

@dataclass(frozen=True)
class _Ran:
    returncode: int
    output: str
    timed_out: bool = False


def _text(value) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value or ""


def _run(cmd: Sequence[str], cwd: Path, env: Mapping[str, str], timeout: float) -> _Ran:
    try:
        done = subprocess.run(list(cmd), cwd=cwd, env=dict(env), capture_output=True,
                              text=True, encoding="utf-8", errors="replace",
                              timeout=timeout, stdin=subprocess.DEVNULL)
    except subprocess.TimeoutExpired as exc:
        return _Ran(-1, _text(exc.stdout) + _text(exc.stderr), timed_out=True)
    return _Ran(done.returncode, done.stdout + done.stderr)


def _outcomes(output: str) -> tuple[dict[str, str], dict[str, str]]:
    """Per-test outcomes in the order pytest reported them, and failure messages.
    Test output is untrusted: it is parsed for these anchored lines and nothing else."""
    outcomes: dict[str, str] = {}
    messages: dict[str, str] = {}
    for line in output.splitlines():
        found = _VERBOSE.match(line)
        if found:
            outcomes[found.group(1)] = found.group(2)
            continue
        found = _SUMMARY.match(line)
        if found:
            outcomes.setdefault(found.group(2), found.group(1))
            if found.group(3):
                messages[found.group(2)] = found.group(3)
    return outcomes, messages


def _is_test(path: str) -> bool:
    p = PurePosixPath(path)
    return (p.name.startswith("test_") or p.name.endswith("_test.py")
            or "tests" in p.parts or "test" in p.parts)


def child_env(extra: Mapping[str, str] | None = None) -> dict[str, str]:
    """The environment every test and git process of the loop gets.

    Agent-written code runs in those processes, so it starts from
    `forge.scrubbed_env()` - built from nothing, no credentials - and the
    caller's `extra` is merged on top, never `os.environ`."""
    return {**scrubbed_env(), "PYTHONDONTWRITEBYTECODE": "1", **(extra or {})}


class _Tree:
    """One git working tree the loop owns, plus how to run its tests."""

    def __init__(self, root: Path, test_args: Sequence[str], env: Mapping[str, str],
                 timeout: float, remaining: Callable[[], float] | None = None) -> None:
        self.root, self.test_args, self.env, self.timeout = root, list(test_args), env, timeout
        self.remaining = remaining or (lambda: float("inf"))
        self.baseline = ""
        self._git_state: dict[Path, bytes | None] = {}

    def git(self, *args: str, cwd: Path | None = None) -> _Ran:
        return _run(["git", *args], cwd or self.root, self.env, self.timeout)

    def git_ok(self, *args: str) -> str:
        done = self.git(*args)
        if done.returncode != 0:
            raise DebugError(f"git {' '.join(args[:2])} failed ({done.returncode}): "
                             f"{done.output.strip()[-300:]}")
        return done.output

    def pytest(self, cwd: Path, args: Sequence[str], *, collect: bool = False) -> tuple[dict, dict, _Ran]:
        """One pytest run, never longer than the time budget has left; with no time
        left it does not start and reports a timeout."""
        left = self.remaining()
        if left <= 0:
            return {}, {}, _Ran(-1, "", timed_out=True)
        cmd = [sys.executable, "-m", "pytest", "-p", "no:cacheprovider", "--no-header"]
        cmd += ["--collect-only", "-q"] if collect else ["-v"]
        ran = _run([*cmd, *args], cwd, self.env, min(self.timeout, left))
        outcomes, messages = _outcomes(ran.output)
        return outcomes, messages, ran

    def dirty(self) -> str:
        return self.git_ok("status", "--porcelain", "-uall").strip()

    def ignored(self) -> list[str]:
        """Untracked files git is told to ignore, minus interpreter caches. They are
        invisible to `git status`, so they are the place to hide a conftest."""
        raw = self.git_ok("ls-files", "-o", "-i", "--exclude-standard", "-z")
        return sorted(p for p in raw.split("\0")
                      if p and not _CACHE_DIRS.intersection(PurePosixPath(p).parts))

    def hidden_tracked(self) -> list[str]:
        """Tracked files marked assume-unchanged or skip-worktree: edits to them do not
        show in `git status`."""
        return [line[2:] for line in self.git_ok("ls-files", "-v").splitlines()
                if line and line[0] != "H"]

    def _state(self) -> dict[Path, bytes | None]:
        """The git files that decide what git hides: info/exclude and the configs."""
        state: dict[Path, bytes | None] = {}
        for name in _GIT_STATE:
            raw = Path(self.git_ok("rev-parse", "--git-path", name).strip())
            path = raw if raw.is_absolute() else self.root / raw
            try:
                state[path] = path.read_bytes()
            except OSError:
                state[path] = None
        return state

    def begin(self) -> None:
        if self.dirty():
            raise DebugError(f"{self.root} is not clean; the debug loop resets its tree after "
                             "every rejected attempt, so it needs a worktree of its own")
        if (found := self.ignored()):
            raise DebugError(f"{self.root} holds ignored files ({', '.join(found[:3])}); the "
                             "loop deletes ignored files when it resets, so it needs a "
                             "pristine worktree of its own")
        if (found := self.hidden_tracked()):
            raise DebugError(f"{self.root} marks tracked files assume-unchanged or "
                             f"skip-worktree ({', '.join(found[:3])})")
        self.baseline = self.git_ok("rev-parse", "HEAD").strip()
        self._git_state = self._state()

    def restore(self) -> None:
        """Back to exactly the baseline: tracked files, untracked AND ignored files,
        and the git files that decide what git hides."""
        self.git_ok("reset", "--hard", "-q", self.baseline)
        self.git_ok("clean", "-fdxq")
        for path, data in self._git_state.items():
            if data is None:
                path.unlink(missing_ok=True)
            elif not path.exists() or path.read_bytes() != data:
                path.write_bytes(data)

    def changes(self) -> list[tuple[str, str]]:
        """(A|M|D|I, path) for every change against the tree's index and HEAD; `I` is
        an ignored file that appeared."""
        tokens = self.git_ok("status", "--porcelain", "-z", "-uall").split("\0")
        out: list[tuple[str, str]] = []
        i = 0
        while i < len(tokens):
            token = tokens[i]
            i += 1
            if len(token) < 4:
                continue
            xy, path = token[:2], token[3:]
            if xy[0] in "RC":
                origin = tokens[i]
                i += 1
                if xy[0] == "R":
                    out.append(("D", origin))
                out.append(("A", path))
            elif "D" in xy:
                out.append(("D", path))
            elif xy == "??" or "A" in xy:
                out.append(("A", path))
            else:
                out.append(("M", path))
        out.extend(("I", p) for p in self.ignored())
        return out

    def snapshot(self) -> dict:
        diff = self.git("diff", self.baseline).output
        return {"files": [{"change": c, "path": p} for c, p in self.changes()],
                "diff": diff[:_DIFF_CAP]}

    # --- the steps ---

    def reproduce(self) -> dict:
        outcomes, messages, ran = self.pytest(self.root, self.test_args)
        if ran.timed_out:
            raise DebugError(f"the reproduction timed out after {self.timeout}s")
        failing = [node for node, outcome in outcomes.items() if outcome in _BAD]
        if ran.returncode not in (0, 1, 5) and not failing:
            raise DebugError(f"pytest exited {ran.returncode} without a test result: "
                             f"{ran.output.strip()[-300:]}")
        trace: list[dict] = [{"event": O.EVENT_RUN_STARTED, "task_id": None, "at": 0.0,
                              "detail": None}]
        at = 1.0
        for node, outcome in outcomes.items():
            trace.append({"event": O.EVENT_TASK_STARTED, "task_id": node, "at": at,
                          "detail": None})
            trace.append({"event": O.EVENT_TASK_FAILED if outcome in _BAD
                          else O.EVENT_TASK_SUCCEEDED, "task_id": node, "at": at + 0.5,
                          "detail": {"error": messages.get(node, "")[:200]}
                          if outcome in _BAD else None})
            at += 1.0
        return {"reproduced": bool(failing), "failing": failing, "trace": trace,
                "output": ran.output[-_OUTPUT_CAP:]}

    @staticmethod
    def locate(trace: list[dict]) -> dict:
        report = trace_debugger.debug(trace)
        step = report["failure_step"]
        first = report["first_violation"]
        return {"located": step is not None, "failure_step": step or 0,
                "task_id": (trace[step - 1].get("task_id") or "") if step else "",
                "invariant": first["invariant"] if first else "",
                "root_cause": report["root_cause"] or ""}

    def _verdict(self, code: str, reason: str, **extra) -> dict:
        return {"accepted": code == "ACCEPTED", "code": code, "reason": reason,
                "failing_after": extra.pop("failing_after", -1), **extra}

    def _timeout(self, **extra) -> dict:
        return self._verdict("TIMEOUT", "a test run hit its timeout or the time budget ran out",
                             **extra)

    def _guard(self, changes: list[tuple[str, str]]) -> dict | None:
        for kind, path in changes:
            if kind == "I":
                return self._verdict("HIDDEN_FILE", f"{path} is an ignored file the attempt "
                                                    "created; git status cannot see it")
        for kind, path in changes:
            if PurePosixPath(path).name in _CONFIG_FILES:
                return self._verdict("TEST_CONFIG_CHANGED", f"{path} configures the test run")
        for kind, path in changes:
            if kind == "D" and _is_test(path):
                return self._verdict("TEST_DELETED", f"{path} was deleted")
        for kind, path in changes:
            if kind != "M" or not _is_test(path):
                continue
            stat = self.git_ok("diff", self.baseline, "--numstat", "--", path).split()
            if len(stat) < 2 or not stat[1].isdigit() or int(stat[1]) > 0:
                return self._verdict("TEST_WEAKENED", f"{path} has lines removed or changed")
            added = self.git_ok("diff", self.baseline, "-U0", "--", path).splitlines()
            if any(line.startswith("+") and not line.startswith("+++") and _SKIP.search(line)
                   for line in added):
                return self._verdict("TEST_WEAKENED", f"{path} gained a skip or xfail")
        return None

    def verify(self, regression_tests) -> dict:
        if self.git_ok("rev-parse", "HEAD").strip() != self.baseline:
            return self._verdict("HEAD_MOVED", "the agent committed; agents never commit")
        if self._state() != self._git_state:
            return self._verdict("GIT_CONFIG_CHANGED", "the attempt changed .git/info/exclude "
                                                       "or a git config file")
        if (hidden := self.hidden_tracked()):
            return self._verdict("HIDDEN_CHANGE", f"{hidden[0]} is marked assume-unchanged or "
                                                  "skip-worktree, so git status cannot see it")
        changes = self.changes()
        if (bad := self._guard(changes)) is not None:
            return bad
        if (not isinstance(regression_tests, list) or not regression_tests
                or len(regression_tests) > _MAX_REGRESSION_TESTS
                or not all(isinstance(n, str) and "::" in n for n in regression_tests)):
            return self._verdict("NO_REGRESSION_TEST",
                                 "the fix names no regression test (path::name)")
        added_tests = {p for kind, p in changes if kind in ("A", "M") and _is_test(p)}
        for node in regression_tests:
            if node.startswith("-") or node.split("::")[0].replace("\\", "/") not in added_tests:
                return self._verdict("NOT_A_NEW_TEST", f"{node} is not in a test file this fix "
                                                       "added or extended")
        scratch = Path(tempfile.mkdtemp(prefix="friday-debug-"))
        try:
            return self._prove(scratch, changes, regression_tests, added_tests)
        finally:
            shutil.rmtree(scratch, ignore_errors=True)

    def _prove(self, scratch: Path, changes: list[tuple[str, str]], nodes: list[str],
               added_tests: set[str]) -> dict:
        archive = scratch / "base.zip"
        self.git_ok("archive", "--format=zip", "-o", str(archive), self.baseline)
        # red: the baseline plus only the changed test files
        pre = self._extract(archive, scratch / "pre")
        _, _, collected = self.pytest(pre, self.test_args, collect=True)
        if collected.timed_out:
            return self._timeout()
        before = {m.group(1) for line in collected.output.splitlines()
                  if (m := _COLLECTED.match(line))}
        for node in nodes:
            if node in before:
                return self._verdict("NOT_A_NEW_TEST", f"{node} already existed before the fix")
        self._copy_in(pre, added_tests)
        outcomes, _, ran = self.pytest(pre, nodes)
        if ran.timed_out:
            return self._timeout()
        red = {n: outcomes.get(n, "MISSING") for n in nodes}
        if any(o != "FAILED" for o in red.values()):
            return self._verdict("NOT_RED_BEFORE", "a regression test did not fail on the "
                                 "pre-fix tree", red=red)
        # green: the fixed tree
        outcomes, _, ran = self.pytest(self.root, nodes)
        if ran.timed_out:
            return self._timeout(red=red)
        green = {n: outcomes.get(n, "MISSING") for n in nodes}
        if any(o != "PASSED" for o in green.values()):
            return self._verdict("NOT_GREEN_AFTER",
                                 "a regression test does not pass on the fixed tree",
                                 red=red, green=green)
        outcomes, _, ran = self.pytest(self.root, self.test_args)
        failing = sum(1 for o in outcomes.values() if o in _BAD)
        if ran.timed_out:
            return self._timeout(red=red, green=green)
        if failing or ran.returncode not in (0, 5):
            return self._verdict("STILL_FAILING", f"{failing} test(s) still fail on the fixed tree",
                                 failing_after=failing, red=red, green=green)
        # semantic guard: the BASELINE tests, untouched, must pass against the fixed sources.
        # A test silenced by an inserted early return or a shadowing assignment passes on the
        # fixed tree but fails here, because here its file is the one in the baseline commit.
        sem = self._extract(archive, scratch / "sem")
        self._copy_in(sem, {p for kind, p in changes if kind in ("A", "M") and not _is_test(p)})
        for kind, path in changes:
            if kind == "D" and not _is_test(path):
                (sem / path).unlink(missing_ok=True)
        outcomes, _, ran = self.pytest(sem, self.test_args)
        if ran.timed_out:
            return self._timeout(red=red, green=green)
        broken = sorted(n for n in before if outcomes.get(n, "MISSING") not in _OK)
        if broken:
            return self._verdict("TEST_WEAKENED",
                                 f"baseline test {broken[0]} fails against the fixed sources; the "
                                 "fix only passes because a test file was changed",
                                 failing_after=len(broken), red=red, green=green)
        return self._verdict("ACCEPTED", "red before, green after, no test weakened",
                             failing_after=0, red=red, green=green)

    @staticmethod
    def _extract(archive: Path, dest: Path) -> Path:
        with zipfile.ZipFile(archive) as bundle:
            bundle.extractall(dest)
        return dest

    def _copy_in(self, dest: Path, paths) -> None:
        base = dest.resolve()
        for path in sorted(paths):
            target = (base / path).resolve()
            if not target.is_relative_to(base):
                raise DebugError(f"refusing to copy {path} outside the scratch tree")
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(self.root / path, target)


# --- the loop ----------------------------------------------------------------------

class _Meter:
    def __init__(self, clock: Callable[[], float], budget_tokens: int, budget_seconds: float):
        self.clock, self.budget_tokens, self.budget_seconds = clock, budget_tokens, budget_seconds
        self.started = clock()
        self.spent = 0

    def stop_reason(self) -> str:
        if self.spent >= self.budget_tokens:
            return "BUDGET_TOKENS"
        if self.clock() - self.started >= self.budget_seconds:
            return "BUDGET_TIME"
        return ""

    def add(self, result) -> int:
        used = result.get("tokens_used") if isinstance(result, dict) else None
        if not isinstance(used, int) or isinstance(used, bool) or used < 0:
            used = len(json.dumps(result, default=str)) // 4 + 1
        self.spent += used
        return used


def _write_evidence(path: Path, report: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    scratch = path.with_name(path.name + ".tmp")
    scratch.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    os.replace(scratch, path)


async def autonomous_debug(workdir, *, agent: D.Agent,
                           test_args: Sequence[str] = (),
                           definition: D.Definition | None = None,
                           models: Sequence[str] = (),
                           strategies: Sequence[str] = DEFAULT_STRATEGIES,
                           k: int = 3, max_attempts: int = 6,
                           budget_tokens: int | None = None,
                           budget_seconds: float = 900.0,
                           clock: Callable[[], float] = time.monotonic,
                           env: Mapping[str, str] | None = None,
                           test_timeout: float = 300.0,
                           evidence_path=None) -> dict:
    """Run friday.debug until a fix is accepted, a budget runs out, or nothing
    new is left to try. Returns the evidence report (see the module docstring)."""
    if k < 1 or max_attempts < 1 or budget_seconds <= 0 or not strategies:
        raise DebugError("k, max_attempts, budget_seconds and strategies must be positive/non-empty")
    definition = definition or load_definition()
    compiled = D.compile_definition(definition)
    meter = _Meter(clock, budget_tokens if budget_tokens is not None
                   else definition.budget_tokens, budget_seconds)
    tree = _Tree(Path(workdir).resolve(), test_args, child_env(env), test_timeout,
                 lambda: meter.budget_seconds - (clock() - meter.started))
    await asyncio.to_thread(tree.begin)
    report: dict = {
        "workflow": f"{definition.workflow_id}@v{definition.version}", "status": "",
        "baseline": tree.baseline, "reproduction": {}, "attempts": [], "changes": [],
        "tokens_spent": 0, "budget_tokens": meter.budget_tokens, "elapsed_seconds": 0.0,
        "budget_seconds": budget_seconds}
    try:
        await _loop(tree, compiled, agent, report, meter, models, tuple(strategies), k, max_attempts)
    except BaseException as exc:
        report["status"] = "ERROR"
        report["error"] = f"{type(exc).__name__}: {exc}"[:300]
        raise
    finally:
        report["tokens_spent"] = meter.spent
        report["elapsed_seconds"] = clock() - meter.started
        if evidence_path is not None:
            await asyncio.to_thread(_write_evidence, Path(evidence_path), report)
    return report


async def _loop(tree: _Tree, compiled: D.Compiled, agent: D.Agent, report: dict,
                meter: _Meter, models: Sequence[str], strategies: tuple[str, ...],
                k: int, max_attempts: int) -> None:
    repro = await asyncio.to_thread(tree.reproduce)
    located = tree.locate(repro["trace"])
    report["reproduction"] = {"failing": repro["failing"], "first_critical": located}
    if not repro["reproduced"]:
        report["status"] = "NOT_REPRODUCED"
        return

    async def reproduce(step, handed):
        return repro

    async def locate(step, handed):
        return tree.locate(handed["reproduce"]["trace"])

    async def verify(step, handed):
        return await asyncio.to_thread(tree.verify, handed["fix"]["regression_tests"])

    executors = {"reproduce": reproduce, "locate": locate, "verify": verify}
    definition = compiled.definition
    strat_i = model_i = stalls = 0
    best = len(repro["failing"])
    tried: list[str] = []
    report["status"] = "REJECTED"
    for n in range(1, max_attempts + 1):
        if (why := meter.stop_reason()):
            report["status"] = why
            return
        model = models[model_i] if models else None
        context = {"attempt": n, "strategy": strategies[strat_i], "tried": list(tried),
                   "failing": repro["failing"]}
        record: dict = {"attempt": n, "strategy": strategies[strat_i], "model": model,
                        "tokens": 0, "hypothesis": "", "steps": {}, "verdict": None}
        report["attempts"].append(record)

        async def metered(step, handed, chosen, context=context, record=record):
            if (stop := meter.stop_reason()):
                raise _Stop(stop)
            result = await agent(step, {**handed, "debug": context}, chosen)
            record["tokens"] += meter.add(result)
            return result

        resolutions = {s.id: D.Resolution(s.id, s.role, D.STATUS_CHOSEN, model,
                                          f"debug loop model ladder rung {model_i + 1}", s.tier)
                       for s in definition.steps if s.kind == "agent"} if model else None
        try:
            outcomes = await D.run_definition(compiled, agent=metered, executors=executors,
                                              resolutions=resolutions)
        except _Stop as stop:
            record["verdict"] = {"accepted": False, "code": stop.status,
                                 "reason": "a budget ran out during the attempt",
                                 "failing_after": -1}
            record["rejected_change"] = await asyncio.to_thread(tree.snapshot)
            await asyncio.to_thread(tree.restore)
            report["status"] = stop.status
            return
        record["steps"] = {sid: o.state for sid, o in outcomes.items()}
        fixed = outcomes["fix"].result if outcomes["fix"].state == "DONE" else None
        record["hypothesis"] = (fixed or {}).get("hypothesis", "")
        if outcomes["verify"].state == "DONE":
            record["verdict"] = outcomes["verify"].result
        else:
            late = next(outcomes[s] for s in compiled.order if outcomes[s].state != "DONE")
            record["verdict"] = {"accepted": False, "code": f"STEP_{late.state}",
                                 "reason": f"{late.step}: {late.reason}", "failing_after": -1}
        verdict = record["verdict"]
        if verdict["accepted"]:
            record["change"] = await asyncio.to_thread(tree.snapshot)
            report["status"] = "FIXED"
            return
        record["rejected_change"] = await asyncio.to_thread(tree.snapshot)
        await asyncio.to_thread(tree.restore)
        if record["hypothesis"]:
            tried.append(record["hypothesis"])
        if 0 <= verdict["failing_after"] < best:
            best, stalls = verdict["failing_after"], 0
        else:
            stalls += 1
        if stalls < k:
            continue
        next_strat = (strat_i + 1) % len(strategies)
        next_model = min(model_i + 1, len(models) - 1) if models else 0
        if next_strat == strat_i and next_model == model_i:
            report["status"] = "NO_NEW_STRATEGY"
            return
        report["changes"].append({
            "after_attempt": n, "from_strategy": strategies[strat_i],
            "to_strategy": strategies[next_strat], "from_model": model,
            "to_model": models[next_model] if models else None,
            "reason": f"{stalls} attempts without progress"})
        strat_i, model_i, stalls = next_strat, next_model, 0
