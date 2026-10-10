"""
The Protected Evaluation Lab: the one place a self-evolution candidate is judged.

EVOLAB-001 slice 1, the lab core. Pure Python with an injected runner: no model
call, no network, nothing wired into selfdev yet. `selfdev.propose` refuses only
KERNEL_PATHS, so a candidate may list its own `tests/test_x.py`, and the sandbox
runs whatever copy of that test the candidate left there. A candidate that can
edit the thing that judges it will eventually pass by editing the judge. This
module closes that by never letting the candidate's copy decide:

  1. Pristine overlay. Every named test, and every conftest.py / pytest config
     above it, plus the verifier, is re-read from the base commit with
     `git show` and written over a COPY of the sandbox before the run. A
     conftest, pytest config or verifier that differs from the base is TAMPERED
     and is not run at all.
  2. Weakening scan. The candidate's copy of each test is compared with the
     base copy by AST: fewer assert statements, fewer pytest.raises, any added
     skip / skipif / xfail / importorskip, or fewer tests -> WEAKENED. The run
     that would follow uses the pristine copy anyway; the scan exists so the
     attempt is named and archived rather than silently ignored.
  3. Receipt. A run is recorded as sha256 over (run id, command, exit code,
     counts, overlay hash, full base commit, candidate content hash) and the
     record is appended to the lab's own ledger. A claimed receipt that is not
     in that ledger, whose digest does not match its own fields, or that was
     issued for another base commit or another candidate, is FABRICATED.
  4. The child's environment is built from nothing (`forge.scrubbed_env`), so
     the candidate's tests cannot read Friday's credentials.

The run happens in a fresh temporary copy of the sandbox, never in the sandbox
and never in the live checkout. A sandbox that is the live checkout is refused.
This is directory isolation, not an OS jail; see the EVOLAB-001 ledger row.

Every verdict other than PASS is appended to `<archive>/rejections.jsonl`.

This file is the judge, so it is in `self_upgrade.KERNEL_PATHS`: a candidate
cannot change it through selfdev.
"""
from __future__ import annotations

import ast
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Callable

from friday.forge import scrubbed_env

PASS = "PASS"
FAIL = "FAIL"
WEAKENED = "WEAKENED"
TAMPERED = "TAMPERED"
FABRICATED = "FABRICATED"
VERDICTS = (PASS, FAIL, WEAKENED, TAMPERED, FABRICATED)

#: Files that decide how, or whether, a test is collected and run. They are
#: looked for in every directory from a test's own up to the repository root.
PROTECTED_NAMES = ("conftest.py", "pytest.ini", "tox.ini", "setup.cfg", "pyproject.toml")

#: The verifier itself. A candidate that edits it is grading its own paper.
EVALUATOR_FILES = ("friday/evaluation.py",)

#: Ways to stop a test counting: a marker, a call, or an imported bare name.
_SKIP_NAMES = frozenset({"skip", "skipif", "xfail", "importorskip",
                         "skipTest", "skipUnless", "skipIf"})

_COPY_IGNORE = shutil.ignore_patterns(".git", "__pycache__", ".venv*", "node_modules")
_COMMIT = re.compile(r"^[0-9a-fA-F]{7,64}$")
_GIT_TIMEOUT = 60


@dataclass(frozen=True)
class RunResult:
    """What the injected runner reports: pytest's exit code and outcome counts."""
    exit_code: int
    counts: dict


#: runner(command, cwd, env) -> RunResult
Runner = Callable[[list, Path, dict], RunResult]


@dataclass(frozen=True)
class Judgement:
    verdict: str
    reasons: tuple = ()
    receipt: dict | None = None
    run_id: str = ""


def receipt_digest(receipt: dict) -> str:
    """sha256 over exactly the fields a receipt vouches for."""
    body = {k: receipt.get(k) for k in
            ("run_id", "command", "exit_code", "counts", "overlay_hash",
             "base_commit", "candidate_hash")}
    return hashlib.sha256(
        json.dumps(body, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _tree_hash(root: Path) -> str:
    """sha256 over every file the lab would copy: relative path and content."""
    h = hashlib.sha256()
    for dirpath, dirnames, filenames in os.walk(root):
        skip = _COPY_IGNORE(dirpath, dirnames + filenames)
        dirnames[:] = sorted(d for d in dirnames if d not in skip)
        for name in sorted(f for f in filenames if f not in skip):
            f = Path(dirpath) / name
            rel = f.relative_to(root).as_posix()
            data = os.readlink(f).encode() if f.is_symlink() else f.read_bytes()
            h.update(f"{rel}\0{hashlib.sha256(data).hexdigest()}\n".encode())
    return h.hexdigest()


def _norm(data: bytes | None) -> bytes | None:
    # the working tree may be CRLF where the blob is LF; that is not an edit
    return None if data is None else data.replace(b"\r\n", b"\n")


def _profile(source: bytes) -> dict:
    tree = ast.parse(source)
    p = {"asserts": 0, "raises": 0, "skips": 0, "tests": 0}
    for node in ast.walk(tree):
        if isinstance(node, ast.Assert):
            p["asserts"] += 1
        elif isinstance(node, ast.Attribute) and node.attr == "raises":
            p["raises"] += 1
        elif isinstance(node, ast.Attribute) and node.attr in _SKIP_NAMES:
            p["skips"] += 1
        elif isinstance(node, ast.Name) and node.id in _SKIP_NAMES:
            p["skips"] += 1
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) \
                and node.name.startswith("test"):
            p["tests"] += 1
    return p


def _weakening(base: bytes, cand: bytes, name: str) -> list[str]:
    try:
        b, c = _profile(base), _profile(cand)
    except SyntaxError as exc:
        return [f"{name}: candidate copy does not parse ({exc.msg})"]
    why = []
    if c["asserts"] < b["asserts"]:
        why.append(f"{name}: assert statements {b['asserts']} -> {c['asserts']}")
    if c["raises"] < b["raises"]:
        why.append(f"{name}: pytest.raises {b['raises']} -> {c['raises']}")
    if c["skips"] > b["skips"]:
        why.append(f"{name}: skip/xfail markers {b['skips']} -> {c['skips']}")
    if c["tests"] < b["tests"]:
        why.append(f"{name}: collected tests {b['tests']} -> {c['tests']}")
    return why


class Lab:
    def __init__(self, repo: str | Path, archive_dir: str | Path):
        self.repo = Path(repo).resolve()
        self.archive_dir = Path(archive_dir)

    # -- reading the base -------------------------------------------------

    def _show(self, base_commit: str, rel: str) -> bytes | None:
        """The file as committed at `base_commit`, or None if it did not exist."""
        out = subprocess.run(["git", "show", f"{base_commit}:{rel}"], cwd=str(self.repo),
                             capture_output=True, timeout=_GIT_TIMEOUT)
        return out.stdout if out.returncode == 0 else None

    def _resolve(self, base_commit: str) -> str:
        """The full commit id `base_commit` names in the live repo; ValueError if none."""
        out = subprocess.run(["git", "rev-parse", "--verify", "--quiet",
                              f"{base_commit}^{{commit}}"], cwd=str(self.repo),
                             capture_output=True, text=True, timeout=_GIT_TIMEOUT)
        full = out.stdout.strip()
        if out.returncode != 0 or not _COMMIT.match(full):
            raise ValueError("base_commit is not a commit in the repository")
        return full.lower()

    # -- the ledger and the archive ---------------------------------------

    def _append(self, name: str, row: dict) -> None:
        self.archive_dir.mkdir(parents=True, exist_ok=True)
        with open(self.archive_dir / name, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, sort_keys=True) + "\n")

    def _issued(self, receipt: dict) -> bool:
        path = self.archive_dir / "receipts.jsonl"
        if not path.exists():
            return False
        for line in path.read_text(encoding="utf-8").splitlines():
            if line and json.loads(line) == receipt:
                return True
        return False

    def _reject(self, verdict: str, reasons: list[str], run_id: str,
                base_commit: str, tests: list[str]) -> Judgement:
        self._append("rejections.jsonl", {
            "ts": time.time(), "run_id": run_id, "verdict": verdict,
            "reasons": reasons, "base_commit": base_commit, "tests": tests})
        return Judgement(verdict, tuple(reasons), None, run_id)

    # -- the judgement ----------------------------------------------------

    def judge(self, base_commit: str, sandbox: str | Path, tests: list[str],
              runner: Runner, claimed: dict | None = None) -> Judgement:
        """
        Judge a candidate's `sandbox` against the pristine `base_commit`.

        `tests` are repo-relative posix paths. `claimed` is evidence the
        candidate supplies (a receipt); it is accepted only if this lab issued it.
        """
        if not _COMMIT.match(base_commit or ""):
            raise ValueError("base_commit must be a hex commit id")
        sandbox = Path(sandbox).resolve()
        if sandbox == self.repo or self.repo in sandbox.parents or sandbox in self.repo.parents:
            raise ValueError("the sandbox must be a separate directory from the live checkout")
        tests = list(tests)
        for t in tests:
            p = PurePosixPath(t)
            if not t or p.is_absolute() or ".." in p.parts or "\\" in t or ":" in t:
                raise ValueError(f"test path must be repo-relative and inside it: {t!r}")
        base_commit = self._resolve(base_commit)
        candidate_hash = _tree_hash(sandbox)
        run_id = uuid.uuid4().hex

        if claimed is not None:
            if claimed.get("digest") != receipt_digest(claimed) or not self._issued(claimed):
                return self._reject(FABRICATED, ["evidence has no matching lab receipt"],
                                    run_id, base_commit, tests)
            if claimed.get("base_commit") != base_commit:
                return self._reject(FABRICATED, ["receipt was issued for another base commit"],
                                    run_id, base_commit, tests)
            if claimed.get("candidate_hash") != candidate_hash:
                return self._reject(FABRICATED, ["receipt was issued for another candidate"],
                                    run_id, base_commit, tests)

        # the judge's own files: any difference from the base is tampering
        protected: list[str] = list(EVALUATOR_FILES)
        for t in tests:
            for d in PurePosixPath(t).parents:
                protected += [str(d / n) if str(d) != "." else n for n in PROTECTED_NAMES]
        overlay: dict[str, bytes] = {}
        tampered = []
        for rel in dict.fromkeys(protected):
            pristine = self._show(base_commit, rel)
            f = sandbox / rel
            cand = f.read_bytes() if f.is_file() else None
            if _norm(cand) != _norm(pristine):
                tampered.append(f"{rel}: differs from the base commit")
            if pristine is not None:
                overlay[rel] = pristine
        if tampered:
            return self._reject(TAMPERED, tampered, run_id, base_commit, tests)

        # the candidate's tests: compare, then discard its copy in favour of the base's
        weakened = []
        for t in tests:
            pristine = self._show(base_commit, t)
            if pristine is None:
                continue                      # a new test has no base to weaken
            overlay[t] = pristine
            f = sandbox / t
            if not f.is_file():
                weakened.append(f"{t}: test file removed")
                continue
            weakened += _weakening(pristine, f.read_bytes(), t)
        if weakened:
            return self._reject(WEAKENED, weakened, run_id, base_commit, tests)

        overlay_hash = hashlib.sha256("\n".join(
            f"{rel}\0{hashlib.sha256(data).hexdigest()}"
            for rel, data in sorted(overlay.items())).encode()).hexdigest()
        command = [sys.executable, "-m", "pytest", "-q", "--tb=line",
                   "-p", "no:cacheprovider", *tests]

        run_dir = Path(tempfile.mkdtemp(prefix="evolab-"))
        try:
            work = run_dir / "work"
            shutil.copytree(sandbox, work, symlinks=True, ignore=_COPY_IGNORE)
            for rel, data in overlay.items():
                target = work / rel
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(data)
            result = runner(command, work, scrubbed_env())
        finally:
            shutil.rmtree(run_dir, ignore_errors=True)

        receipt = {"run_id": run_id, "command": command, "exit_code": result.exit_code,
                   "counts": dict(result.counts), "overlay_hash": overlay_hash,
                   "base_commit": base_commit, "candidate_hash": candidate_hash}
        receipt["digest"] = receipt_digest(receipt)
        self._append("receipts.jsonl", receipt)
        if result.exit_code != 0:
            self._append("rejections.jsonl", {
                "ts": time.time(), "run_id": run_id, "verdict": FAIL,
                "reasons": [f"pytest exit code {result.exit_code}"],
                "base_commit": base_commit, "tests": tests})
            return Judgement(FAIL, (f"pytest exit code {result.exit_code}",), receipt, run_id)
        return Judgement(PASS, (), receipt, run_id)
