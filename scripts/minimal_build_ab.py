"""
Does the PRD section 23 minimal-implementation ladder make a FRIDAY coding
worker's change smaller and cheaper without making it worse?

Run (always the isolated venv, never the live .venv):

    .venv-build/Scripts/python.exe scripts/minimal_build_ab.py              # synthetic: no model, no spend
    .venv-build/Scripts/python.exe scripts/minimal_build_ab.py --preflight  # the corpus is well formed, no spend
    .venv-build/Scripts/python.exe scripts/minimal_build_ab.py --live --budget-usd 6

The PRD says Ponytail's advertised savings "are not accepted as FRIDAY's own
savings until measured on FRIDAY tasks" (docs/PRD_V7.md section 23). This is
that measurement. The same task, from the same base commit, in its own git
worktree, is given to a coding worker twice:

    A  the baseline prompt
    B  the baseline prompt plus the ladder and the never-simplify list, read
       verbatim from the PRD, so the prompt is the only variable

and each arm is measured on

    diff     files, lines added, lines deleted, new dependencies (git numstat)
    cost     what the worker spent, from the result event
    seconds  wall clock
    quality  a held-out acceptance test plus one probe per "never simplify
             away" guard the task touches

Quality is the control column, as `found` is in fabric_code_intel_ab.py: a
smaller diff that drops a guard is cheap and wrong, and without this column the
arm that writes the least always wins. The acceptance and probe tests live
outside the worktree, so the worker never sees them and they never count as
diff.

The default runner is a deterministic fake that applies a scripted change and
spends nothing; a report from it says so and never carries a verdict. The live
runner launches the same headless `claude` worker the build kernel does and
reserves its whole budget through the kernel's day spend ledger first.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
import time
import tomllib
import uuid
import xml.etree.ElementTree as ET
from dataclasses import asdict, dataclass, field, replace
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
for _p in (str(ROOT), str(ROOT / "scripts")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from friday.executors.worktrees import WorktreeManager, git  # noqa: E402

PRD = ROOT / "docs" / "PRD_V7.md"
REPORT = ROOT / "docs" / "implementation" / "MINIMAL_BUILD_AB.md"

#: Every task starts here. A moved base would change what "the same task" means.
BASE = "35bd57267ed16f6344fa1a98e04eb7df1968a755"

ARMS = ("A", "B")
ARM_NAMES = {"A": "baseline", "B": "ladder"}

#: Paired runs a verdict needs, per task and overall. Below it nothing is adopted
#: or rejected: a language model's run-to-run spread is larger than one pair.
MIN_PAIRS = 3

#: A live run whose per-run budget is below this cuts workers off before they
#: finish, which would measure the cap rather than the policy.
MIN_RUN_USD = 0.25

CHECK_TIMEOUT_S = 300

GUARD_KEYS = ("validation", "security", "accessibility", "data-loss", "error-handling", "observability")
_GUARD_WORDS = ("validation", "security", "accessibility", "data-loss", "error handling", "observability")

HYPOTHESIS = (
    "JetBrains' reported saving from minimal-build prompting is roughly 10%. That figure is a hypothesis "
    "taken from the build packet, not a measurement on FRIDAY tasks; only the verdict above counts."
)
SYNTHETIC = ("SYNTHETIC RUN: no model was called and nothing was spent. These numbers prove the pipeline "
             "works, not that the policy does. Run with --live for a measurement.")


class PolicyError(RuntimeError):
    """The PRD no longer says what this harness was written against."""


class ReportError(RuntimeError):
    """Writing the report would destroy a measurement."""


class BudgetError(RuntimeError):
    """The budget cannot fund a run that measures the policy."""


# ----------------------------------------------------------------- the policy

@dataclass(frozen=True)
class Policy:
    ladder: str                  # the PRD line, verbatim
    rungs: tuple[str, ...]
    never: str                   # the PRD line, verbatim
    guards: tuple[str, ...]


def _line(lines: list[str], prefix: str) -> str:
    for line in lines:
        if line.startswith(prefix):
            return line.strip()
    raise PolicyError(f"{PRD.name} has no line starting {prefix!r}")


def load_policy(prd: Path = PRD) -> Policy:
    """Read the ladder and the guards from the PRD, so the arm cannot drift from it."""
    lines = prd.read_text(encoding="utf-8").splitlines()
    ladder = _line(lines, "Minimal implementation ladder:")
    never = _line(lines, "Never simplify away:")
    rungs = tuple(s.strip() for s in re.split(r"(?<=\?)\s+", ladder.split(":", 1)[1].strip()) if s.strip())
    guards = tuple(s.strip().rstrip(".") for s in never.split(":", 1)[1].split(";") if s.strip())
    if len(rungs) != 7:
        raise PolicyError(f"the ladder has {len(rungs)} rungs, this harness measures 7")
    if len(guards) != len(GUARD_KEYS) or any(w not in g for w, g in zip(_GUARD_WORDS, guards)):
        raise PolicyError(f"the never-simplify list is not the six guards {GUARD_KEYS}: {guards}")
    return Policy(ladder=ladder, rungs=rungs, never=never, guards=guards)


BASELINE = (
    "You are a coding worker on the FRIDAY repository, in your own git worktree. Implement the task below, "
    "then stop.\n\n"
    "Task: {title}\n\n{task}\n\n"
    "Change only what the task needs, inside this working tree. Do not commit. When you are done, reply "
    "with one line."
)


def arm_prompt(arm: str, task: "Task", policy: Policy) -> str:
    text = BASELINE.format(title=task.title, task=task.prompt)
    if arm == "A":
        return text
    if arm == "B":
        return f"{text}\n\n{policy.ladder}\n{policy.never}\n"
    raise ValueError(f"unknown arm {arm!r}")


# --------------------------------------------------------------------- corpus

@dataclass(frozen=True)
class Task:
    id: str
    title: str
    prompt: str
    targets: tuple[str, ...]        # files the change creates; none exist at the base
    acceptance: str                 # held-out test source: the change does what was asked
    probes: dict                    # guard key -> held-out test source: the guard survived
    reference: dict                 # path -> text: a correct, minimal change
    unguarded: dict                 # path -> text: passes acceptance, drops the guards
    unguarded_fails: tuple[str, ...]  # the guards `unguarded` must trip
    base: str = BASE


_DURATIONS = Task(
    id="duration-parse",
    title="Parse short duration strings",
    prompt=(
        "Add `parse_duration(text: str) -> float` in a new module `friday/durations.py`. It returns seconds "
        "for strings such as `90s`, `5m`, `2h`, `1h30m` and `1h 30m 10s`: the units are h, m and s, each at "
        "most once, in that order, whole numbers only. Anything else - empty or blank text, a negative "
        "number, an unknown unit, a repeated or out-of-order unit, a decimal, a value that is not a string "
        "- raises ValueError."),
    targets=("friday/durations.py",),
    acceptance=r'''
def test_acceptance_parses_each_unit_and_their_combination():
    from friday.durations import parse_duration
    assert parse_duration("90s") == 90
    assert parse_duration("5m") == 300
    assert parse_duration("2h") == 7200
    assert parse_duration("1h30m") == 5400
    assert parse_duration("1h 30m 10s") == 5410
''',
    probes={"validation": r'''
import pytest


@pytest.mark.parametrize("bad", ["", " ", "-5s", "5x", "5m1h", "1h1h", "1.5h", "abc", None, 5])
def test_guard_validation_rejects_what_is_not_a_duration(bad):
    from friday.durations import parse_duration
    with pytest.raises(ValueError):
        parse_duration(bad)
'''},
    reference={"friday/durations.py": '''import re

_DURATION = re.compile(r"(?:(\\d+)h)?\\s*(?:(\\d+)m)?\\s*(?:(\\d+)s)?")


def parse_duration(text: str) -> float:
    if not isinstance(text, str):
        raise ValueError("a duration is a string")
    match = _DURATION.fullmatch(text.strip())
    if match is None or not any(match.groups()):
        raise ValueError(f"not a duration: {text!r}")
    hours, minutes, seconds = (int(g or 0) for g in match.groups())
    return float(hours * 3600 + minutes * 60 + seconds)
'''},
    unguarded={"friday/durations.py": '''import re


def parse_duration(text):
    units = {"h": 3600, "m": 60, "s": 1}
    return float(sum(int(n) * units[u] for n, u in re.findall(r"(\\d+)([hms])", text)))
'''},
    unguarded_fails=("validation",),
)

_SAFE_READ = Task(
    id="jailed-read",
    title="Read a text file only from inside a directory",
    prompt=(
        "Add `read_under(root, relative) -> str` in a new module `friday/safe_read.py`. It returns the UTF-8 "
        "text of the file at `relative` inside the directory `root` (both str or Path). A `relative` that "
        "resolves outside `root` - `..` segments, an absolute path - raises PermissionError, and a missing "
        "file raises FileNotFoundError."),
    targets=("friday/safe_read.py",),
    acceptance=r'''
def test_acceptance_reads_a_nested_file(tmp_path):
    from friday.safe_read import read_under
    (tmp_path / "notes").mkdir()
    (tmp_path / "notes" / "a.txt").write_text("hello", encoding="utf-8")
    assert read_under(tmp_path, "notes/a.txt") == "hello"
    assert read_under(str(tmp_path), "notes/a.txt") == "hello"
''',
    probes={"security": r'''
import pytest


def _setup(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    (root / "ok.txt").write_text("ok", encoding="utf-8")
    secret = tmp_path / "secret.txt"
    secret.write_text("secret", encoding="utf-8")
    return root, secret


def test_guard_security_refuses_a_path_that_climbs_out(tmp_path):
    from friday.safe_read import read_under
    root, _ = _setup(tmp_path)
    with pytest.raises(PermissionError):
        read_under(root, "../secret.txt")
    with pytest.raises(PermissionError):
        read_under(root, "sub/../../secret.txt")


def test_guard_security_refuses_an_absolute_path(tmp_path):
    from friday.safe_read import read_under
    root, secret = _setup(tmp_path)
    with pytest.raises(PermissionError):
        read_under(root, str(secret))


def test_guard_security_still_reads_a_missing_file_as_missing(tmp_path):
    from friday.safe_read import read_under
    root, _ = _setup(tmp_path)
    with pytest.raises(FileNotFoundError):
        read_under(root, "absent.txt")
'''},
    reference={"friday/safe_read.py": '''from pathlib import Path


def read_under(root, relative) -> str:
    base = Path(root).resolve()
    target = (base / relative).resolve()
    if not target.is_relative_to(base):
        raise PermissionError(f"{relative!r} resolves outside {base}")
    return target.read_text(encoding="utf-8")
'''},
    unguarded={"friday/safe_read.py": '''from pathlib import Path


def read_under(root, relative):
    return (Path(root) / relative).read_text(encoding="utf-8")
'''},
    unguarded_fails=("security",),
)

_ATOMIC_JSON = Task(
    id="atomic-json",
    title="Save a JSON state file without ever truncating it",
    prompt=(
        "Add `save_json(path, data) -> None` in a new module `friday/atomic_json.py`. It writes `data` to "
        "`path` as UTF-8 JSON with indent 2. If `data` cannot be serialised, or the write fails part-way, "
        "the file that was already at `path` keeps its old content exactly and no temporary file is left "
        "in the directory. The parent directory already exists."),
    targets=("friday/atomic_json.py",),
    acceptance=r'''
import json


def test_acceptance_writes_json_and_replaces_an_existing_file(tmp_path):
    from friday.atomic_json import save_json
    path = tmp_path / "state.json"
    save_json(path, {"a": 1})
    assert json.loads(path.read_text(encoding="utf-8")) == {"a": 1}
    save_json(path, {"b": [1, 2]})
    assert json.loads(path.read_text(encoding="utf-8")) == {"b": [1, 2]}
''',
    probes={"data-loss": r'''
import pytest


def test_guard_data_loss_a_failed_write_leaves_the_old_file_whole(tmp_path):
    from friday.atomic_json import save_json
    path = tmp_path / "state.json"
    save_json(path, {"a": 1})
    before = path.read_bytes()
    with pytest.raises(TypeError):
        save_json(path, {"a": object()})
    assert path.read_bytes() == before
    assert [p.name for p in tmp_path.iterdir()] == ["state.json"]
'''},
    reference={"friday/atomic_json.py": '''import json
import os
import tempfile
from pathlib import Path


def save_json(path, data) -> None:
    path = Path(path)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=path.name + ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\\n") as fh:
            json.dump(data, fh, indent=2)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
'''},
    unguarded={"friday/atomic_json.py": '''import json


def save_json(path, data):
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2)
'''},
    unguarded_fails=("data-loss",),
)

_BADGE = Task(
    id="status-badge",
    title="Render a status badge for the control room",
    prompt=(
        "Add `badge_html(status, label) -> str` in a new module `friday/status_badge.py`. `status` is one of "
        "`ok`, `warn`, `fail` (anything else raises ValueError); `label` is free text that may come from an "
        "untrusted source. It returns one `<span>` whose class attribute is `badge badge-<status>` and "
        "whose text is the label. The control room is used with a screen reader, so the badge must be "
        "announced as a status and carry the status in its accessible name."),
    targets=("friday/status_badge.py",),
    acceptance=r'''
def test_acceptance_renders_a_span_with_the_class_and_the_label():
    from friday.status_badge import badge_html
    out = badge_html("ok", "Healthy")
    assert out.startswith("<span") and out.endswith("</span>")
    assert 'class="badge badge-ok"' in out
    assert ">Healthy<" in out
''',
    probes={
        "accessibility": r'''
def test_guard_accessibility_is_a_status_with_an_accessible_name():
    from friday.status_badge import badge_html
    out = badge_html("warn", "Disk 91%")
    assert 'role="status"' in out
    assert 'aria-label="warn: Disk 91%"' in out
''',
        "security": r'''
def test_guard_security_escapes_an_untrusted_label():
    from friday.status_badge import badge_html
    out = badge_html("ok", "<script>alert(1)</script>")
    assert "<script>" not in out
    assert "&lt;script&gt;" in out
    out = badge_html("ok", 'x" onmouseover="alert(1)')
    assert '" onmouseover="' not in out
''',
        "validation": r'''
import pytest


def test_guard_validation_rejects_an_unknown_status():
    from friday.status_badge import badge_html
    with pytest.raises(ValueError):
        badge_html("bogus", "x")
''',
    },
    reference={"friday/status_badge.py": '''from html import escape

_STATUSES = ("ok", "warn", "fail")


def badge_html(status: str, label: str) -> str:
    if status not in _STATUSES:
        raise ValueError(f"unknown status {status!r}")
    text = escape(label, quote=True)
    return (f'<span class="badge badge-{status}" role="status" '
            f'aria-label="{status}: {text}">{text}</span>')
'''},
    unguarded={"friday/status_badge.py": '''def badge_html(status, label):
    return f'<span class="badge badge-{status}">{label}</span>'
'''},
    unguarded_fails=("accessibility", "security", "validation"),
)

_RUN_LIMITED = Task(
    id="run-limited",
    title="Run an external command with a time limit",
    prompt=(
        "Add `run_limited(argv, timeout_s) -> dict` in a new module `friday/run_limited.py`. It runs the "
        "command (a list, no shell) and returns {\"ok\", \"returncode\", \"stdout\", \"stderr\", "
        "\"timed_out\"}. A command that outlives `timeout_s` is stopped and reported as ok False, "
        "timed_out True - it does not raise and does not hang. A command that cannot be started is ok "
        "False, returncode None, with the reason in stderr. Every failure (non-zero exit, timeout, cannot "
        "start) is logged once at WARNING on the logger named `friday.run_limited`, naming the program."),
    targets=("friday/run_limited.py",),
    acceptance=r'''
import sys


def test_acceptance_returns_the_output_of_a_command_that_succeeds():
    from friday.run_limited import run_limited
    out = run_limited([sys.executable, "-c", "print('hi')"], 30)
    assert out["ok"] is True
    assert out["returncode"] == 0
    assert out["stdout"].strip() == "hi"
    assert out["timed_out"] is False
''',
    probes={
        "error-handling": r'''
import sys
import time


def test_guard_error_handling_a_slow_command_is_stopped_not_raised():
    from friday.run_limited import run_limited
    start = time.perf_counter()
    out = run_limited([sys.executable, "-c", "import time; time.sleep(60)"], 1)
    assert out["ok"] is False and out["timed_out"] is True
    assert time.perf_counter() - start < 30


def test_guard_error_handling_a_missing_program_is_reported_not_raised():
    from friday.run_limited import run_limited
    out = run_limited(["friday-no-such-program-xyz"], 5)
    assert out["ok"] is False
    assert out["returncode"] is None
    assert out["stderr"]
''',
        "observability": r'''
import logging
import sys


def test_guard_observability_each_failure_is_logged_at_warning(caplog):
    from friday.run_limited import run_limited
    with caplog.at_level(logging.WARNING, logger="friday.run_limited"):
        run_limited([sys.executable, "-c", "raise SystemExit(3)"], 30)
        run_limited(["friday-no-such-program-xyz"], 5)
    records = [r for r in caplog.records if r.name == "friday.run_limited" and r.levelno == logging.WARNING]
    assert len(records) == 2
    assert "friday-no-such-program-xyz" in records[1].getMessage()
''',
    },
    reference={"friday/run_limited.py": '''import logging
import subprocess

log = logging.getLogger("friday.run_limited")


def _result(ok, returncode, stdout, stderr, timed_out):
    return {"ok": ok, "returncode": returncode, "stdout": stdout, "stderr": stderr, "timed_out": timed_out}


def run_limited(argv, timeout_s):
    try:
        done = subprocess.run(argv, capture_output=True, text=True, timeout=timeout_s)
    except subprocess.TimeoutExpired:
        log.warning("%s timed out after %ss", argv[0], timeout_s)
        return _result(False, None, "", "timed out", True)
    except OSError as exc:
        log.warning("%s could not start: %s", argv[0], exc)
        return _result(False, None, "", str(exc), False)
    if done.returncode != 0:
        log.warning("%s exited %s", argv[0], done.returncode)
    return _result(done.returncode == 0, done.returncode, done.stdout, done.stderr, False)
'''},
    unguarded={"friday/run_limited.py": '''import subprocess


def run_limited(argv, timeout_s):
    done = subprocess.run(argv, capture_output=True, text=True, timeout=timeout_s)
    return {"ok": done.returncode == 0, "returncode": done.returncode, "stdout": done.stdout,
            "stderr": done.stderr, "timed_out": False}
'''},
    unguarded_fails=("error-handling", "observability"),
)

CORPUS = (_DURATIONS, _SAFE_READ, _ATOMIC_JSON, _BADGE, _RUN_LIMITED)


# ---------------------------------------------------------------- measurement

@dataclass
class DiffStat:
    files: int = 0
    added: int = 0
    deleted: int = 0
    new_deps: int = 0
    paths: list = field(default_factory=list)
    notes: list = field(default_factory=list)

    @property
    def lines(self) -> int:
        return self.added + self.deleted


def _dep_name(spec: str) -> str:
    return re.split(r"[\s<>=!~;\[(@]", spec.strip(), maxsplit=1)[0].lower().replace("_", "-")


def _dep_names(file_name: str, text: str) -> set[str]:
    if not text.strip():
        return set()
    if file_name == "package.json":
        data = json.loads(text)
        keys = ("dependencies", "devDependencies", "optionalDependencies", "peerDependencies")
        return {name for k in keys for name in (data.get(k) or {})}
    if file_name == "pyproject.toml":
        data = tomllib.loads(text)
        project = data.get("project") or {}
        specs = list(project.get("dependencies") or [])
        for group in (project.get("optional-dependencies") or {}).values():
            specs += list(group)
        for group in (data.get("dependency-groups") or {}).values():
            specs += [s for s in group if isinstance(s, str)]
        return {_dep_name(s) for s in specs}
    return {_dep_name(line) for line in text.splitlines()
            if line.strip() and not line.lstrip().startswith(("#", "-"))}


def _is_manifest(path: str) -> bool:
    name = path.replace("\\", "/").rsplit("/", 1)[-1]
    return name in ("pyproject.toml", "package.json") or bool(re.fullmatch(r"requirements.*\.txt", name))


def diff_stat(workdir: Path, base: str) -> DiffStat:
    """What the worker changed against the base, new files included. The worktree is
    scratch, so staging it is free and is what lets numstat see an untracked file."""
    workdir = Path(workdir)
    git(workdir, "add", "-A", "--", ".")
    # The worker's packet lives in .build and is never diff. Unstaged here rather than
    # excluded in the pathspec: git refuses an exclude pathspec that names an ignored
    # path, and .gitignore has ignored .build/ since 8617c31 - the first live A/B arm
    # died on it (2026-10-08). --ignore-unmatch covers a base that never staged it.
    git(workdir, "rm", "-r", "-q", "--cached", "--ignore-unmatch", "--", ".build")
    stat = DiffStat()
    for row in git(workdir, "diff", "--cached", "--no-renames", "--numstat", base).splitlines():
        added, deleted, path = row.split("\t", 2)
        stat.files += 1
        stat.added += int(added) if added.isdigit() else 0   # a binary file reports "-"
        stat.deleted += int(deleted) if deleted.isdigit() else 0
        stat.paths.append(path)
    for path in (p for p in stat.paths if _is_manifest(p)):
        name = path.replace("\\", "/").rsplit("/", 1)[-1]
        before = git(workdir, "show", f"{base}:{path}", check=False)
        now = workdir / path
        after = now.read_text(encoding="utf-8") if now.is_file() else ""
        try:
            stat.new_deps += len(_dep_names(name, after) - _dep_names(name, before))
        except ValueError as exc:   # tomllib and json both raise a ValueError subclass
            stat.notes.append(f"{path}: manifest unreadable, new dependencies not counted ({exc})")
    return stat


def _stem(key: str) -> str:
    return "test_" + re.sub(r"[^a-z0-9]+", "_", key.lower()).strip("_")


def check_quality(task: Task, workdir: Path) -> dict:
    """Run the held-out acceptance test and the guard probes against the worker's tree.

    The tests sit in a temp directory with an empty ini file: the repo's own pytest
    configuration does not apply, the worker never sees them, and they are not part of
    the diff. The worktree is on sys.path through `python -m pytest`, so `friday`
    resolves to the worker's tree."""
    checks = {"acceptance": task.acceptance, **{f"guard:{g}": src for g, src in task.probes.items()}}
    with tempfile.TemporaryDirectory(prefix="minimal-build-ab-") as held_dir:
        held = Path(held_dir)
        (held / "pytest.ini").write_text("[pytest]\n", encoding="utf-8")
        for key, source in checks.items():
            (held / f"{_stem(key)}.py").write_text(source.lstrip("\n"), encoding="utf-8", newline="\n")
        xml = held / "results.xml"
        try:
            subprocess.run([sys.executable, "-m", "pytest", "-q", "--tb=line", "-p", "no:cacheprovider",
                            "-c", str(held / "pytest.ini"), f"--junitxml={xml}", str(held)],
                           cwd=str(workdir), env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1"),
                           capture_output=True, text=True, timeout=CHECK_TIMEOUT_S)
        except subprocess.TimeoutExpired:
            return _quality({k: False for k in checks})   # a check that does not finish did not pass
        passed = _junit_passed(xml, {_stem(k): k for k in checks})
    return _quality({k: passed.get(k, False) for k in checks})


def _junit_passed(xml: Path, stems: dict) -> dict:
    """check key -> True when its file ran at least one test and none failed or errored."""
    outcome: dict = {}
    try:
        cases = ET.parse(xml).getroot().iter("testcase")
    except (OSError, ET.ParseError):
        return outcome
    for case in cases:
        module = (case.get("classname") or case.get("name") or "").split(".")[0]
        key = stems.get(module)
        if key is None:
            continue
        bad = case.find("failure") is not None or case.find("error") is not None
        outcome[key] = outcome.get(key, True) and not bad
    return outcome


def _quality(results: dict) -> dict:
    guards = {k.split(":", 1)[1]: v for k, v in results.items() if k.startswith("guard:")}
    acceptance = results["acceptance"]
    return {"acceptance": acceptance, "guards": guards, "ok": acceptance and all(guards.values())}


@dataclass
class RunResult:
    cost_usd: float = 0.0
    turns: int = 0
    seconds: float = 0.0
    error: str = ""


@dataclass
class Measured:
    task: str
    arm: str
    rep: int
    diff: dict
    cost_usd: float
    turns: int
    seconds: float
    quality: dict
    error: str = ""


# -------------------------------------------------------------------- runners

class FakeRunner:
    """Deterministic and free: applies a scripted change in place of a model.

    `script(task, arm)` returns {path: text}; by default every arm applies the task's
    reference change, so the pipeline runs with nothing to learn from the numbers."""

    def __init__(self, script=None, costs: dict | None = None) -> None:
        self.script = script or (lambda task, arm: task.reference)
        self.costs = costs or {}

    def run(self, task: Task, arm: str, prompt: str, workdir: Path, rep: int) -> RunResult:
        for rel, text in self.script(task, arm).items():
            target = Path(workdir) / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(text, encoding="utf-8", newline="\n")
        return RunResult(cost_usd=self.costs.get(arm, 0.0))


def _kernel():
    import build_kernel
    return build_kernel


def parse_result(stdout: str) -> dict:
    """The last result event of a stream-json run: cost, turns, seconds."""
    found: dict = {}
    for line in (stdout or "").splitlines():
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if isinstance(event, dict) and event.get("type") == "result":
            found = event
    return found


class ClaudeRunner:
    """The live arm: the same headless worker the build kernel launches, in the arm's worktree."""

    def __init__(self, *, model: str, max_turns: int, max_budget_usd: float, timeout_s: float = 1200) -> None:
        self.model, self.max_turns = model, max_turns
        self.max_budget_usd, self.timeout_s = max_budget_usd, timeout_s

    def run(self, task: Task, arm: str, prompt: str, workdir: Path, rep: int) -> RunResult:
        kernel = _kernel()
        cmd = kernel.worker_command(self.model, self.max_turns, self.max_budget_usd, prompt=prompt)
        start = time.perf_counter()
        try:
            done = subprocess.run(cmd, cwd=str(workdir), env=kernel.worker_env(Path(workdir)),
                                  capture_output=True, text=True, encoding="utf-8", errors="replace",
                                  timeout=self.timeout_s)
            out, error = done.stdout, ""
        except subprocess.TimeoutExpired as exc:
            out = exc.stdout.decode("utf-8", "replace") if isinstance(exc.stdout, bytes) else (exc.stdout or "")
            error = f"worker timed out after {self.timeout_s:.0f}s"
        wall = time.perf_counter() - start
        event = parse_result(out)
        if not event and not error:
            error = "no result event in the worker's output"
        if event.get("is_error") and not error:
            error = str(event.get("subtype") or "worker reported an error")
        seconds = float(event.get("duration_ms") or 0) / 1000 or wall
        return RunResult(cost_usd=float(event.get("total_cost_usd") or 0.0),
                         turns=int(event.get("num_turns") or 0), seconds=round(seconds, 2), error=error)


# ------------------------------------------------------------------ experiment

def _assert_base(repo: Path, task: Task) -> None:
    git(repo, "cat-file", "-e", f"{task.base}^{{commit}}")


def run_arm(task: Task, arm: str, rep: int, runner, policy: Policy, repo: Path) -> Measured:
    manager = WorktreeManager(repo)
    name = f"ab-{task.id}-{arm.lower()}-{rep}-{uuid.uuid4().hex[:6]}"
    tree = manager.create(name, base=task.base)
    try:
        result = runner.run(task, arm, arm_prompt(arm, task, policy), tree.path, rep)
        diff = diff_stat(tree.path, task.base)       # before the checks write anything
        quality = check_quality(task, tree.path)
    finally:
        manager.cleanup(name)
    return Measured(task=task.id, arm=arm, rep=rep, diff={**asdict(diff), "lines": diff.lines},
                    cost_usd=round(result.cost_usd, 4), turns=result.turns, seconds=result.seconds,
                    quality=quality, error=result.error)


def run_experiment(tasks, runner, *, repeats: int, policy: Policy, repo: Path = ROOT,
                   on_pair=None) -> list[Measured]:
    """Every task `repeats` times, both arms from the same base. The arm order alternates so
    neither arm always runs first."""
    repo = Path(repo)
    for task in tasks:
        _assert_base(repo, task)
    out: list[Measured] = []
    for task in tasks:
        for rep in range(repeats):
            if on_pair is not None and not on_pair(task, rep):
                return out
            for arm in (ARMS if rep % 2 == 0 else tuple(reversed(ARMS))):
                out.append(run_arm(task, arm, rep, runner, policy, repo))
    return out


def preflight(tasks, policy: Policy, repo: Path = ROOT) -> list[str]:
    """Problems with the corpus itself, found before any paid run: the acceptance test must
    fail on the untouched base, pass on the reference change, and each guard probe must
    pass on the reference and fail on the plausible shortcut that drops the guard."""
    problems: list[str] = []
    repo = Path(repo)
    manager = WorktreeManager(repo)
    for task in tasks:
        _assert_base(repo, task)
        name = f"ab-preflight-{task.id}-{uuid.uuid4().hex[:6]}"
        tree = manager.create(name, base=task.base)
        try:
            for rel in task.targets:
                if (tree.path / rel).exists():
                    problems.append(f"{task.id}: {rel} already exists at the base")
            if check_quality(task, tree.path)["acceptance"]:
                problems.append(f"{task.id}: the acceptance test passes on the untouched base")
            _write(tree.path, task.reference)
            ref = check_quality(task, tree.path)
            if not ref["ok"]:
                problems.append(f"{task.id}: the reference change fails its own checks: {ref}")
            _write(tree.path, task.unguarded)
            cut = check_quality(task, tree.path)
            if not cut["acceptance"]:
                problems.append(f"{task.id}: the shortcut fails acceptance, so it proves no guard")
            for guard in task.probes:
                if (guard in task.unguarded_fails) == cut["guards"].get(guard, False):
                    problems.append(f"{task.id}: the {guard} probe "
                                    f"{'passes the shortcut' if guard in task.unguarded_fails else 'fails the shortcut'}")
        finally:
            manager.cleanup(name)
    return problems


def coverage_gaps(tasks) -> list[str]:
    """The never-simplify guards no task in the corpus probes."""
    covered = {g for t in tasks for g in t.probes}
    return [g for g in GUARD_KEYS if g not in covered]


def _write(workdir: Path, files: dict) -> None:
    for rel, text in files.items():
        target = Path(workdir) / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8", newline="\n")


# -------------------------------------------------------------------- verdict

@dataclass(frozen=True)
class Verdict:
    outcome: str                      # ADOPT | REJECT | INCONCLUSIVE
    reason: str
    pairs: int
    quality_a: float | None = None    # share of runs that pass the acceptance test and every guard
    quality_b: float | None = None
    diff_delta_pct: float | None = None   # B against A, mean changed lines; negative is smaller
    cost_delta_pct: float | None = None


def _mean(values) -> float:
    values = list(values)
    return sum(values) / len(values) if values else 0.0


def _pct(a: float, b: float) -> float | None:
    return None if a == 0 else round((b - a) / a * 100, 1)


def _check_counts(runs: list[Measured]) -> dict:
    """task-qualified check id -> passes, so a guard regression cannot hide behind another check."""
    counts: dict = {}
    for m in runs:
        counts[f"{m.task}:acceptance"] = counts.get(f"{m.task}:acceptance", 0) + bool(m.quality["acceptance"])
        for guard, ok in m.quality["guards"].items():
            counts[f"{m.task}:{guard}"] = counts.get(f"{m.task}:{guard}", 0) + bool(ok)
    return counts


def decide(pairs: list[tuple[Measured, Measured]], minimum: int = MIN_PAIRS) -> Verdict:
    """ADOPT only at quality parity with the diff and the cost not worse. REJECT when quality
    regresses or when it holds but nothing was saved. INCONCLUSIVE below the minimum pairs."""
    n = len(pairs)
    a = [p[0] for p in pairs]
    b = [p[1] for p in pairs]
    qa = round(_mean(m.quality["ok"] for m in a), 3) if n else None
    qb = round(_mean(m.quality["ok"] for m in b), 3) if n else None
    diff_pct = _pct(_mean(m.diff["lines"] for m in a), _mean(m.diff["lines"] for m in b))
    cost_pct = _pct(_mean(m.cost_usd for m in a), _mean(m.cost_usd for m in b))
    numbers = dict(pairs=n, quality_a=qa, quality_b=qb, diff_delta_pct=diff_pct, cost_delta_pct=cost_pct)
    if n < minimum:
        return Verdict("INCONCLUSIVE", f"{n} paired run(s); at least {minimum} are needed", **numbers)
    before, after = _check_counts(a), _check_counts(b)
    regressed = sorted(k for k in before if after.get(k, 0) < before[k])
    if regressed or qb < qa:
        return Verdict("REJECT", "quality regressed on " + (", ".join(regressed) or "the overall pass rate"),
                       **numbers)
    mean_diff_a, mean_diff_b = _mean(m.diff["lines"] for m in a), _mean(m.diff["lines"] for m in b)
    mean_cost_a, mean_cost_b = _mean(m.cost_usd for m in a), _mean(m.cost_usd for m in b)
    worse = [name for name, worse_ in (("diff", mean_diff_b > mean_diff_a), ("cost", mean_cost_b > mean_cost_a))
             if worse_]
    if worse:
        return Verdict("REJECT", f"quality held but the {' and '.join(worse)} got worse: nothing was saved",
                       **numbers)
    return Verdict("ADOPT", "quality at parity, diff and cost not worse", **numbers)


def pair_up(measured: list[Measured]) -> dict:
    """task id -> [(A run, B run)] for each repeat where both arms ran."""
    by_key = {(m.task, m.rep, m.arm): m for m in measured}
    out: dict = {}
    for (task, rep, arm), m in sorted(by_key.items()):
        if arm == "A" and (task, rep, "B") in by_key:
            out.setdefault(task, []).append((m, by_key[(task, rep, "B")]))
    return out


def recommend(verdicts: dict) -> dict:
    """Global, per task, off, or undetermined: the question PRD_V7.md leaves open."""
    adopt = sorted(t for t, v in verdicts.items() if v.outcome == "ADOPT")
    if verdicts and len(adopt) == len(verdicts):
        return {"scope": "global", "tasks": adopt,
                "text": "enable the ladder for every coding worker"}
    if adopt:
        return {"scope": "per-task", "tasks": adopt,
                "text": "enable the ladder only for tasks like: " + ", ".join(adopt)}
    if any(v.outcome == "REJECT" for v in verdicts.values()):
        return {"scope": "off", "tasks": [], "text": "do not enable the ladder for coding workers"}
    return {"scope": "undetermined", "tasks": [], "text": "no recommendation: not enough measured pairs"}


# --------------------------------------------------------------------- report

def _arm_summary(runs: list[Measured]) -> dict:
    n = len(runs)
    guards: dict = {}
    for m in runs:
        for g, ok in m.quality["guards"].items():
            guards[g] = guards.get(g, 0) + bool(ok)
    return {
        "runs": n,
        "files": round(_mean(m.diff["files"] for m in runs), 2),
        "added": round(_mean(m.diff["added"] for m in runs), 2),
        "deleted": round(_mean(m.diff["deleted"] for m in runs), 2),
        "lines": round(_mean(m.diff["lines"] for m in runs), 2),
        "new_deps": sum(m.diff["new_deps"] for m in runs),
        "cost_usd": round(_mean(m.cost_usd for m in runs), 4),
        "seconds": round(_mean(m.seconds for m in runs), 2),
        "acceptance": sum(bool(m.quality["acceptance"]) for m in runs),
        "guards": guards,
        "quality_ok": sum(bool(m.quality["ok"]) for m in runs),
        "errors": [m.error for m in runs if m.error],
    }


def build_report(measured: list[Measured], *, mode: str, policy: Policy, tasks, repeats: int,
                 base: str = BASE, model: str = "", budget_usd: float | None = None) -> dict:
    """The whole result. A synthetic run keeps its numbers but never carries a verdict."""
    live = mode == "live"
    paired = pair_up(measured)
    held_back = Verdict("INCONCLUSIVE", "synthetic run: no model was called", 0)
    verdicts = {}
    rows = []
    for task in tasks:
        runs = [m for m in measured if m.task == task.id]
        v = decide(paired.get(task.id, []))
        verdicts[task.id] = v if live else replace(held_back, pairs=v.pairs, quality_a=v.quality_a,
                                                   quality_b=v.quality_b, diff_delta_pct=v.diff_delta_pct,
                                                   cost_delta_pct=v.cost_delta_pct)
        rows.append({"id": task.id, "title": task.title, "guards": sorted(task.probes),
                     "A": _arm_summary([m for m in runs if m.arm == "A"]),
                     "B": _arm_summary([m for m in runs if m.arm == "B"]),
                     "verdict": asdict(verdicts[task.id])})
    every = [p for ps in paired.values() for p in ps]
    overall = decide(every)
    if not live:
        overall = replace(held_back, pairs=overall.pairs, quality_a=overall.quality_a, quality_b=overall.quality_b,
                          diff_delta_pct=overall.diff_delta_pct, cost_delta_pct=overall.cost_delta_pct)
    return {
        "requirement": "CODE-001",
        "mode": mode,
        "evidence": live,
        "banner": None if live else SYNTHETIC,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "base": base,
        "repeats": repeats,
        "min_pairs": MIN_PAIRS,
        "model": model,
        "budget_usd": budget_usd,
        "policy": {"ladder": policy.ladder, "never": policy.never},
        "hypothesis": HYPOTHESIS,
        "tasks": rows,
        "overall": {"A": _arm_summary([m for m in measured if m.arm == "A"]),
                    "B": _arm_summary([m for m in measured if m.arm == "B"]),
                    "verdict": asdict(overall)},
        "recommendation": recommend(verdicts),
        "runs": [asdict(m) for m in measured],
    }


def _cell(arm: dict) -> str:
    return (f"{arm['runs']} | {arm['files']:g} | +{arm['added']:g} | -{arm['deleted']:g} | {arm['new_deps']} | "
            f"{arm['cost_usd']:.4f} | {arm['seconds']} | {arm['quality_ok']}/{arm['runs']}")


def render_markdown(report: dict) -> str:
    out = ["# Minimal-build policy A/B (CODE-001, PRD section 23)", "",
           "Generated by `scripts/minimal_build_ab.py`; do not edit by hand.", ""]
    if report["banner"]:
        out += [f"> **{report['banner']}**", ""]
    budget = "n/a" if report["budget_usd"] is None else "$%.2f" % report["budget_usd"]
    out += [f"- mode: `{report['mode']}`   evidence: `{str(report['evidence']).lower()}`",
            f"- base commit: `{report['base']}`",
            f"- paired repeats per task: {report['repeats']} (a verdict needs {report['min_pairs']})",
            f"- model: {report['model'] or 'n/a'}   budget: {budget}",
            "- arm A is the baseline prompt; arm B is the baseline plus the PRD ladder and never-simplify list",
            "", "## Per task", "",
            "| Task | Arm | Runs | Files | +Lines | -Lines | New deps | Cost USD | Seconds | Quality (acceptance + guards) |",
            "|---|---|---|---|---|---|---|---|---|---|"]
    for row in report["tasks"]:
        out += [f"| {row['id']} | A | {_cell(row['A'])} |", f"| {row['id']} | B | {_cell(row['B'])} |"]
    out += ["", "Quality is the control column: a run counts only if the held-out acceptance test and every "
                "guard probe for its task pass, so a smaller diff that drops a guard cannot win.", "",
            "## Verdict", ""]
    overall = report["overall"]["verdict"]
    out += [f"**{overall['outcome']}** - {overall['reason']}.", "",
            f"- paired runs: {overall['pairs']}",
            f"- quality pass rate: A {overall['quality_a']}, B {overall['quality_b']}",
            f"- diff size, B against A: {_fmt_pct(overall['diff_delta_pct'])}",
            f"- cost, B against A: {_fmt_pct(overall['cost_delta_pct'])}", "",
            "| Task | Verdict | Pairs | Reason |", "|---|---|---|---|"]
    for row in report["tasks"]:
        v = row["verdict"]
        out.append(f"| {row['id']} | {v['outcome']} | {v['pairs']} | {v['reason']} |")
    rec = report["recommendation"]
    out += ["", "## Global or per task", "", f"**{rec['scope']}** - {rec['text']}.", "",
            "## Hypothesis, not a result", "", report["hypothesis"], "",
            "## Policy under test", "", "```", report["policy"]["ladder"], report["policy"]["never"], "```", ""]
    return "\n".join(out)


def _fmt_pct(value) -> str:
    return "n/a" if value is None else f"{value:+.1f}%"


def write_report(report: dict, out_md: Path = REPORT, *, force: bool = False) -> tuple[Path, Path]:
    """Write the markdown and the JSON beside it. A synthetic report never replaces a live one."""
    out_md = Path(out_md)
    out_json = out_md.with_suffix(".json")
    if not report["evidence"] and out_json.exists() and not force:
        try:
            existing = json.loads(out_json.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            existing = {}
        if existing.get("evidence"):
            raise ReportError(f"{out_json} holds a live measurement; a synthetic run will not replace it "
                              "(pass --out elsewhere, or --force)")
    out_md.parent.mkdir(parents=True, exist_ok=True)
    out_md.write_text(render_markdown(report), encoding="utf-8", newline="\n")
    out_json.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8", newline="\n")
    return out_md, out_json


# ----------------------------------------------------------------------- live

def reserve(budget_usd: float, runs_dir: Path | None = None) -> str:
    """Hold the whole run's budget against today's spend ledger. Raises SpendCapReached
    when it does not fit, before anything has been created or launched."""
    key = f"minimal-build-ab-{uuid.uuid4().hex[:8]}"
    _kernel().spend_guard(key, "minimal_build_ab", "CODE-001", "BENCH", budget_usd, runs_dir)
    return key


def run_live(tasks, *, repeats: int, budget_usd: float, policy: Policy, runner_factory, repo: Path = ROOT,
             runs_dir: Path | None = None) -> list[Measured]:
    """The paid run: reserve first, record every worker's actual cost in the ledger as it
    lands, stop before a pair that would pass the budget, release the lease at the end."""
    kernel = _kernel()
    per_run = round(budget_usd / (len(ARMS) * len(tasks) * repeats), 4)
    if per_run < MIN_RUN_USD:
        raise BudgetError(f"${budget_usd:.2f} over {len(ARMS) * len(tasks) * repeats} runs is ${per_run:.2f} a "
                          f"run; below ${MIN_RUN_USD:.2f} the cap cuts workers off before they finish")
    key = reserve(budget_usd, runs_dir)
    spent = 0.0
    ledger_dir = runs_dir if runs_dir is not None else kernel.RUNS_DIR

    class Recording:
        def __init__(self, inner) -> None:
            self.inner = inner

        def run(self, task, arm, prompt, workdir, rep):
            nonlocal spent
            result = self.inner.run(task, arm, prompt, workdir, rep)
            spent += result.cost_usd
            kernel.ledger_append({"attempt_id": f"{key}:{task.id}:{arm}:{rep}", "req": "CODE-001",
                                  "kind": "BENCH", "cost_usd": result.cost_usd, "turns": result.turns,
                                  "at": datetime.now(timezone.utc).isoformat()}, ledger_dir)
            return result

    try:
        return run_experiment(tasks, Recording(runner_factory(per_run)), repeats=repeats, policy=policy,
                              repo=repo, on_pair=lambda task, rep: spent + len(ARMS) * per_run <= budget_usd)
    finally:
        kernel.spend_release(key, runs_dir)


# ------------------------------------------------------------------------ cli

def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--live", action="store_true", help="call a real model; needs --budget-usd")
    p.add_argument("--budget-usd", type=float, help="the most the whole live run may spend")
    p.add_argument("--preflight", action="store_true", help="check the corpus, spend nothing")
    p.add_argument("--repeats", type=int, default=MIN_PAIRS)
    p.add_argument("--tasks", help="comma-separated task ids; default all")
    p.add_argument("--model", default="sonnet")
    p.add_argument("--max-turns", type=int, default=25)
    p.add_argument("--out", type=Path, default=REPORT)
    p.add_argument("--runs-dir", type=Path, help="the spend ledger directory; default the kernel's")
    p.add_argument("--force", action="store_true", help="let a synthetic report replace a live one")
    return p


def main(argv: list[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    if args.live and args.budget_usd is None:
        parser.error("--live needs --budget-usd: a paid run states what it may spend")
    if args.budget_usd is not None and not args.live:
        parser.error("--budget-usd only applies to --live")
    if args.repeats < 1:
        parser.error("--repeats must be at least 1")
    wanted = set(args.tasks.split(",")) if args.tasks else None
    tasks = [t for t in CORPUS if wanted is None or t.id in wanted]
    if not tasks or (wanted and len(tasks) != len(wanted)):
        parser.error(f"unknown task in {args.tasks!r}; known: {', '.join(t.id for t in CORPUS)}")
    policy = load_policy()

    if args.preflight:
        problems = preflight(tasks, policy) + [f"no task probes the {g} guard" for g in coverage_gaps(CORPUS)]
        for problem in problems:
            print(f"  PROBLEM {problem}")
        print(f"  preflight: {len(tasks)} task(s), {len(problems)} problem(s)")
        return 1 if problems else 0

    if args.live:
        kernel = _kernel()
        try:
            measured = run_live(
                tasks, repeats=args.repeats, budget_usd=args.budget_usd, policy=policy, runs_dir=args.runs_dir,
                runner_factory=lambda per_run: ClaudeRunner(model=args.model, max_turns=args.max_turns,
                                                            max_budget_usd=per_run))
        except (kernel.SpendCapReached, BudgetError) as exc:
            print(f"  refused: {exc}")
            return 2
        mode = "live"
    else:
        measured = run_experiment(tasks, FakeRunner(), repeats=args.repeats, policy=policy)
        mode = "fake"

    report = build_report(measured, mode=mode, policy=policy, tasks=tasks, repeats=args.repeats,
                          model=args.model if args.live else "", budget_usd=args.budget_usd)
    try:
        md, js = write_report(report, args.out, force=args.force)
    except ReportError as exc:
        print(f"  refused: {exc}")
        return 2
    verdict = report["overall"]["verdict"]
    print(f"  {mode}: {verdict['outcome']} ({verdict['pairs']} pairs) - {verdict['reason']}")
    print(f"  wrote {md} and {js}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
