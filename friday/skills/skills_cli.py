"""
The Vercel skills CLI as a skill-distribution backend (SKMKT-001, slice 1).

Friday never lets the CLI decide anything. Each verb is a fixed argument list
built here from allowlisted pieces, run without a shell, with the
credential-free environment `friday.forge.scrubbed_env` builds from nothing
and a timeout. `add` and `use` run inside a fresh directory under
`work_dir/quarantine/` and hand back a `CandidateDrop` bound to the bytes it
holds; this module writes nowhere else and installs nothing. Scanning,
sandboxing and admission are the caller's, through the forge and the skill
lifecycle (a community skill starts QUARANTINED there).

The argument shapes follow the public CLI (`skills find|add|update`,
`add --list`, `--skill`, `--agent`, `--yes`); they could not be checked
against a live install here, so the runner is injectable and the tests pin
the contract rather than the upstream.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import uuid
from dataclasses import dataclass
from pathlib import Path

from friday import forge

OK = "OK"
UNAVAILABLE = "UNAVAILABLE"
ERROR = "ERROR"

DEFAULT_TIMEOUT_SECONDS = 60
#: Output kept on a result; the CLI's progress art is not worth a context.
MAX_OUTPUT_CHARS = 20000

#: The verbs this module can ever run. Anything else is refused.
VERBS = ("find", "add", "update", "use")

_REPO = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*/[A-Za-z0-9][A-Za-z0-9._-]*")
_NAME = re.compile(r"[a-z0-9][a-z0-9._-]*")
_REF = re.compile(r"[A-Za-z0-9][A-Za-z0-9._/-]*")
_QUERY = re.compile(r"[A-Za-z0-9][A-Za-z0-9 ._/-]{0,79}")


class SkillsCliRefused(ValueError):
    """An argument or verb outside the allowlist; raised before any process."""


@dataclass(frozen=True)
class CandidateDrop:
    """A downloaded skill tree, bound to its bytes and to where it came from."""
    path: Path
    repo: str
    ref: str
    skill: str
    sha256: str


@dataclass
class CliResult:
    status: str
    verb: str
    argv: list
    stdout: str = ""
    detail: str = ""
    drop: CandidateDrop | None = None


def _check(pattern: re.Pattern, value, what: str) -> str:
    if not isinstance(value, str) or not pattern.fullmatch(value):
        raise SkillsCliRefused(f"{what} {value!r} is not allowed")
    return value


def _subprocess_runner(argv, *, env, cwd, timeout):
    exe = shutil.which(argv[0], path=env.get("PATH"))
    if exe is None:
        raise FileNotFoundError(argv[0])
    return subprocess.run([exe, *argv[1:]], capture_output=True, text=True,
                          env=env, cwd=cwd, timeout=timeout)


class SkillsCliBackend:
    def __init__(self, work_dir, runner=None,
                 timeout: float = DEFAULT_TIMEOUT_SECONDS):
        self.work_dir = Path(work_dir)
        self.runner = runner or _subprocess_runner
        self.timeout = timeout

    # --- verbs ---------------------------------------------------------------

    def find(self, query: str) -> CliResult:
        _check(_QUERY, query, "search query")
        return self._run("find", [query], cwd=self._scratch())

    def list_repo(self, repo: str) -> CliResult:
        _check(_REPO, repo, "repository")
        return self._run("add", [repo, "--list"], cwd=self._scratch())

    def add(self, repo: str, skill: str, agents, ref: str | None = None) -> CliResult:
        source = self._source(repo, ref)
        _check(_NAME, skill, "skill name")
        names = self._agents(agents)
        return self._run("add", [source, "--skill", skill, "--agent", *names,
                                 "--yes"],
                         repo=repo, ref=ref, skill=skill, drop=True)

    def update(self, name: str) -> CliResult:
        _check(_NAME, name, "skill name")
        return self._run("update", [name], cwd=self._new_quarantine())

    def use(self, repo: str, skill: str, ref: str | None = None) -> CliResult:
        source = self._source(repo, ref)
        _check(_NAME, skill, "skill name")
        return self._run("use", [source, "--skill", skill],
                         repo=repo, ref=ref, skill=skill, drop=True)

    # --- machinery -----------------------------------------------------------

    @staticmethod
    def _source(repo: str, ref: str | None) -> str:
        _check(_REPO, repo, "repository")
        if ref is None:
            return repo
        return f"{repo}#{_check(_REF, ref, 'ref')}"

    @staticmethod
    def _agents(agents) -> list:
        if isinstance(agents, str) or not agents:
            raise SkillsCliRefused("agents must be a non-empty list of names")
        return [_check(_NAME, a, "agent name") for a in agents]

    def _scratch(self) -> Path:
        self.work_dir.mkdir(parents=True, exist_ok=True)
        return self.work_dir

    def _new_quarantine(self) -> Path:
        path = self.work_dir / "quarantine" / uuid.uuid4().hex
        path.mkdir(parents=True, exist_ok=False)
        return path

    def _run(self, verb: str, args: list, *, cwd: Path | None = None,
             repo: str = "", ref: str | None = None, skill: str = "",
             drop: bool = False) -> CliResult:
        if verb not in VERBS:
            raise SkillsCliRefused(f"verb {verb!r} is not allowed")
        if drop:
            cwd = self._new_quarantine()
        argv = ["npx", "skills", verb, *args]
        result = CliResult(status=ERROR, verb=verb, argv=argv)
        try:
            completed = self.runner(argv, env=forge.scrubbed_env(),
                                    cwd=str(cwd), timeout=self.timeout)
        except FileNotFoundError:
            result.status = UNAVAILABLE
            result.detail = "npx is not installed or not on PATH"
            return result
        except subprocess.TimeoutExpired:
            result.detail = f"the skills CLI exceeded its {self.timeout:g}s budget"
            return result
        except OSError as exc:
            result.detail = f"the skills CLI could not be started: {exc}"
            return result

        result.stdout = (completed.stdout or "")[-MAX_OUTPUT_CHARS:]
        if completed.returncode != 0:
            result.detail = (f"skills {verb} exited {completed.returncode}: "
                             f"{(completed.stderr or '').strip()[-200:]}")
            return result
        if drop:
            if not any(p.is_file() for p in Path(cwd).rglob("*")):
                result.detail = f"skills {verb} produced no files"
                return result
            result.drop = CandidateDrop(
                path=Path(cwd), repo=repo, ref=ref or "", skill=skill,
                sha256=forge.tree_digest(Path(cwd)))
        result.status = OK
        return result
