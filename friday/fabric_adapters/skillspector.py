"""
NVIDIA SkillSpector: the static security scan every imported skill, MCP server
or package passes before it is activated.

`friday.forge` already owns the scan *contract*: how a report is parsed, what
blocks, and that anything which did not run to completion is "not scanned",
never "clean" (`run_skillspector`, `scan_import`, `scan_admits`). This adapter
is the fabric-side half: it pins the scanner as an upstream, says where the
dedicated environment lives, and exposes one operation, `scan`, that goes
through that same contract. It does not parse a report itself, so there is one
reading of SkillSpector's output in the tree.

## A dedicated environment, never a shared Python

SkillSpector needs Python >= 3.12 and a large dependency set (yara, langgraph,
...). It runs from `.venv-skillspector/`, created with
`uv sync --frozen --python 3.12 --no-dev` against the pinned clone's own
`uv.lock`, so it neither inherits nor disturbs Friday's `.venv`. The adapter
calls that environment's `skillspector` executable directly; nothing is put on
PATH and nothing is imported into Friday's process.

## Strict, static, and absent-safe

The command is `scan <dir> --no-llm --format json --fail-on-incomplete`.
`--no-llm` keeps the scan static (no file content goes to a model provider and
no key is needed); `--fail-on-incomplete` makes a partial analysis exit
non-zero instead of looking like a quiet pass. Declared dependency *names* are
still looked up against OSV.dev by the scanner; that is its documented
behaviour and is why `notes` says so.

A missing clone, a missing environment, a clone that is not at the pinned
commit, or an executable that does not report the pinned version are all
`UNAVAILABLE` from `health()`, with the command that repairs it. `start()` and
import never raise for any of them (NON_NEGOTIABLE 15). `call()` raises when
the scan did not happen; a scan that ran returns its report, CLEAN or BLOCKED.

`FRIDAY_SKILLSPECTOR_VENV` points at a different environment directory (a git
worktree has no `.venv-skillspector` of its own; the main checkout does).
"""

from __future__ import annotations

import os
import pathlib
import re
import subprocess

from friday import fabric

ROOT = pathlib.Path(__file__).resolve().parent.parent.parent

UPSTREAM = "skillspector"
RELEASE = "v2.12.0"
#: The commit the v2.12.0 tag resolves to (verified with `git ls-remote`
#: against refs/tags/v2.12.0 on 2026-10-09, owner ruling CAP-MKT-002).
COMMIT = "c7958a3268d9498644b22edb75d0f051bbc8cbfc"

VENV_ENV = "FRIDAY_SKILLSPECTOR_VENV"
VERSION_TIMEOUT = 60.0
SCAN_TIMEOUT = 120.0

#: Static, strict. "{target}" is the directory, substituted by forge as one
#: argv element, never through a shell.
STRICT_ARGS = ("scan", "{target}", "--no-llm", "--format", "json",
               "--fail-on-incomplete")

INSTALL = ("uv python install 3.12 && cd third_party/upstream/skillspector && "
           "UV_PROJECT_ENVIRONMENT=<repo>/.venv-skillspector "
           "uv sync --frozen --python 3.12 --no-dev")

_FULL_SHA = re.compile(r"^[0-9a-f]{40}$")


def clone_dir() -> pathlib.Path:
    return ROOT / "third_party" / "upstream" / UPSTREAM


def venv_dir() -> pathlib.Path:
    override = os.environ.get(VENV_ENV)
    return pathlib.Path(override) if override else ROOT / ".venv-skillspector"


def executable() -> pathlib.Path | None:
    """The dedicated environment's `skillspector`, or None if it is absent."""
    scripts = venv_dir() / ("Scripts" if os.name == "nt" else "bin")
    for name in ("skillspector.exe", "skillspector"):
        candidate = scripts / name
        if candidate.is_file():
            return candidate
    return None


def clone_head() -> str:
    """
    The clone's checked-out commit read from `.git/HEAD`, or "".

    Read as a file rather than by running git: this is a health probe, and a
    probe that can hang on a stale `index.lock` is not one. A branch name, a
    gitlink file or an unreadable HEAD is "" - not provably the pinned commit.
    """
    try:
        head = (clone_dir() / ".git" / "HEAD").read_text(
            encoding="utf-8").strip()
    except OSError:
        return ""
    return head if _FULL_SHA.match(head) else ""


def _problem() -> str:
    """Why the scanner cannot be used, or "" when it can."""
    if not clone_dir().is_dir():
        return f"{clone_dir()} is not cloned"
    head = clone_head()
    if head != COMMIT:
        return (f"the clone is at {head or 'an unverifiable ref'}, "
                f"not the pinned {COMMIT}")
    if executable() is None:
        return f"no dedicated environment at {venv_dir()}"
    return ""


def _reported_version(exe: pathlib.Path) -> str:
    from friday import forge

    completed = subprocess.run(
        [str(exe), "--version"], capture_output=True, text=True,
        timeout=VERSION_TIMEOUT, env=forge.scrubbed_env(),
        encoding="utf-8", errors="replace")
    return (completed.stdout or "").strip()


def start():
    """A CLI has no process to hold; health() is where absence is reported."""
    return None


def stop(handle=None) -> None:
    """Nothing is held, so there is nothing to retire."""


def health(handle=None) -> dict:
    problem = _problem()
    if problem:
        return {"state": fabric.UNAVAILABLE,
                "detail": f"{problem}; install with: {INSTALL}"}
    try:
        reported = _reported_version(executable())
    except (OSError, subprocess.SubprocessError) as exc:
        return {"state": fabric.UNAVAILABLE,
                "detail": f"skillspector --version failed: {exc}"}
    if RELEASE.lstrip("v") not in reported:
        return {"state": fabric.UNAVAILABLE,
                "detail": f"environment reports {reported!r}, "
                          f"expected {RELEASE}"}
    return {"state": fabric.READY, "detail": reported}


def _command() -> list[str]:
    return [str(executable()), *STRICT_ARGS]


def call(operation: str, handle=None, *, run_id: str = "", **arguments):
    if operation != "scan":
        raise fabric.FabricError(f"{UPSTREAM} has no operation {operation!r}")
    path = arguments.get("path")
    if not path:
        raise ValueError("skillspector scan needs 'path', a directory")
    problem = _problem()
    if problem:
        raise FileNotFoundError(f"skillspector is unavailable: {problem}")

    from friday import forge

    report = forge.scan_import(pathlib.Path(str(path)), command=_command(),
                               timeout=SCAN_TIMEOUT)
    if report.status not in (forge.SCAN_CLEAN, forge.SCAN_BLOCKED):
        # The scan did not produce a verdict. Returning it would let a caller
        # read "no findings" as "safe", so it is a failure instead.
        raise RuntimeError(
            f"skillspector scan of {path} gave no verdict "
            f"({report.status}): {report.detail}")
    return report.to_dict()


DESCRIPTOR = fabric.Provider(
    id="skillspector",
    family="security",
    upstream=UPSTREAM,
    operations=("scan",),
    risk="low",
    license_mode=fabric.PERMISSIVE,
    integration_mode=fabric.CLI,
    cost_class="free",
    model_required=False,
    commit=COMMIT,
    version=RELEASE,
    owns_process=False,
    notes=(
        "NVIDIA SkillSpector v2.12.0, Apache-2.0. Run from a dedicated "
        "Python 3.12 environment (.venv-skillspector, built from the clone's "
        "uv.lock), never a shared Python. `scan` runs `--no-llm --format json "
        "--fail-on-incomplete` through friday.forge.scan_import, so the "
        "verdict is read by the one parser in forge.py; an unreadable, "
        "partial or failed scan raises and is never a pass. Read-only on the "
        "scanned directory. Declared dependency names are still looked up on "
        "OSV.dev by the scanner. Absent clone or environment reports "
        "UNAVAILABLE and never breaks boot."
    ),
)
