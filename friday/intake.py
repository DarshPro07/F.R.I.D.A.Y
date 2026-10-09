"""
Repository intake (INTAKE-001), slice 1: the front door.

A GitHub URL given to Friday becomes one journey with fixed, ordered stages.
This slice is pure and injected - no network, no git. The three things that
touch the world are passed in:

    resolver(owner, repo, ref) -> 40-hex commit SHA
    decider(dossier)           -> Decision
    runner(stage, dossier)     -> None   (BUILD onward only)

Two rules shape everything here. A branch or tag is never a pin: only the
40-hex SHA is stored. And text inside the repository is data: it is scanned
for lines addressed to an agent and recorded as findings, but it is never
passed to the runner and never executed.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, replace
from urllib.parse import urlsplit

STAGES = ("UNDERSTAND", "QUARANTINE", "INSPECT", "DECIDE",
          "BUILD", "VERIFY", "REGISTER", "RESUME", "MAINTAIN")
DECISIONS = ("USE", "WRAP", "EXTEND", "EXTRACT", "REFERENCE", "REJECT")
REASON_MAX = 200

_SHA_RE = re.compile(r"[0-9a-f]{40}")
_NAME_RE = re.compile(r"[A-Za-z0-9._-]+")
_REF_RE = re.compile(r"[A-Za-z0-9._/-]+")

# Lines in repository text that address an agent rather than a human reader.
_INSTRUCTION_RES = tuple(re.compile(p, re.IGNORECASE) for p in (
    r"\bignore\s+(all\s+)?(the\s+)?(previous|prior|above)\b",
    r"\b(curl|wget)\b[^|\n]*\|\s*(sudo\s+)?(sh|bash)\b",
    r"\bdisable\s+(the\s+)?(scanner|scan|security|safety|guard|check)",
    r"\brun\s+(this|the\s+following|install\.sh|setup\.sh)\b",
))


class IntakeError(ValueError):
    """An intake input was refused. `field` names what was wrong."""

    def __init__(self, field: str, message: str = ""):
        self.field = field
        super().__init__(f"{field}: {message}" if message else field)


@dataclass(frozen=True)
class RepoRef:
    owner: str
    repo: str
    ref: str | None


def parse_repo_url(url: str) -> RepoRef:
    """Accept only https://github.com/<owner>/<repo>[.git][/tree/<ref>]."""
    if not isinstance(url, str) or not url.strip():
        raise IntakeError("url", "empty")
    parts = urlsplit(url.strip())
    if parts.scheme != "https":
        raise IntakeError("scheme", "only https is accepted")
    userinfo, _, hostport = parts.netloc.rpartition("@")
    if userinfo or "@" in parts.netloc:
        raise IntakeError("userinfo", "credentials in a URL are refused")
    if ":" in hostport:
        raise IntakeError("port", "an explicit port is refused")
    if hostport.lower() != "github.com":
        raise IntakeError("host", "only github.com is accepted")
    if parts.query:
        raise IntakeError("query", "a query string is refused")
    if parts.fragment:
        raise IntakeError("fragment", "a fragment is refused")

    segments = parts.path.strip("/").split("/")
    if len(segments) == 2:
        owner, repo, ref_segments = segments[0], segments[1], []
    elif len(segments) >= 4 and segments[2] == "tree":
        owner, repo, ref_segments = segments[0], segments[1], segments[3:]
    else:
        raise IntakeError("path", "expected /<owner>/<repo>[/tree/<ref>]")
    if repo.endswith(".git"):
        repo = repo[:-4]
    for name in (owner, repo):
        if not _NAME_RE.fullmatch(name) or name in (".", ".."):
            raise IntakeError("path", "owner or repository name is not valid")
    ref = None
    if ref_segments:
        ref = "/".join(ref_segments)
        if (not _REF_RE.fullmatch(ref)
                or any(s in ("", ".", "..") for s in ref_segments)):
            raise IntakeError("path", "ref is not valid")
    return RepoRef(owner, repo, ref)


def scan_instructions(text: str) -> list[dict]:
    """Flag lines that read as instructions to an agent. Nothing is run."""
    findings = []
    for number, line in enumerate(text.splitlines(), 1):
        for pattern in _INSTRUCTION_RES:
            if pattern.search(line):
                findings.append({"line": number,
                                 "rule": pattern.pattern,
                                 "text": line.strip()[:REASON_MAX]})
                break
    return findings


@dataclass(frozen=True)
class IntakeDossier:
    owner: str
    repo: str
    sha: str
    goal: str
    findings: tuple


@dataclass(frozen=True)
class Decision:
    kind: str
    reason: str

    def __post_init__(self):
        if self.kind not in DECISIONS:
            raise IntakeError("decision", f"must be one of {', '.join(DECISIONS)}")
        if not self.reason.strip():
            raise IntakeError("reason", "a decision needs a reason")
        if len(self.reason) > REASON_MAX:
            raise IntakeError("reason", f"longer than {REASON_MAX} characters")


class IntakeRun:
    """One journey. `advance` runs the next stage and refuses any other."""

    def __init__(self, url: str, goal: str, *, resolver, decider, runner=None):
        self._url = url
        self._goal = goal
        self._resolver = resolver
        self._decider = decider
        self._runner = runner
        self._ref: RepoRef | None = None
        self._sha: str | None = None
        self.dossier: IntakeDossier | None = None
        self.decision: Decision | None = None
        self.evidence: list[dict] = []
        self.installed: tuple = ()

    @property
    def next_stage(self) -> str | None:
        if self.decision is not None and self.decision.kind == "REJECT":
            return None
        if len(self.evidence) == len(STAGES):
            return None
        return STAGES[len(self.evidence)]

    def advance(self, stage: str, *, text: str = "") -> dict:
        if stage not in STAGES:
            raise IntakeError("stage", f"unknown stage {stage!r}")
        if stage != self.next_stage:
            raise IntakeError("stage", f"next stage is {self.next_stage}, not {stage}")
        detail = getattr(self, "_" + stage.lower())(text)
        record = {"stage": stage, **detail}
        self.evidence.append(record)
        return record

    def _understand(self, text: str) -> dict:
        if not self._goal.strip():
            raise IntakeError("goal", "the journey needs a goal")
        self._ref = parse_repo_url(self._url)
        return {"owner": self._ref.owner, "repo": self._ref.repo}

    def _quarantine(self, text: str) -> dict:
        sha = self._resolver(self._ref.owner, self._ref.repo,
                             self._ref.ref or "HEAD")
        if not isinstance(sha, str) or not _SHA_RE.fullmatch(sha):
            raise IntakeError("sha", "the pin must be a 40-hex commit")
        self._sha = sha
        return {"sha": sha}

    def _inspect(self, text: str) -> dict:
        findings = tuple(scan_instructions(text))
        self.dossier = IntakeDossier(self._ref.owner, self._ref.repo,
                                     self._sha, self._goal, findings)
        return {"findings": len(findings)}

    def _decide(self, text: str) -> dict:
        decision = self._decider(self.dossier)
        if not isinstance(decision, Decision):
            raise IntakeError("decision", "the decider must return a Decision")
        self.decision = decision
        return {"decision": decision.kind, "reason": decision.reason}

    def _stage_by_runner(self, stage: str) -> dict:
        if self._runner is not None:
            # Where the repo's own words were found travels on; the words do not.
            redacted = tuple({"line": f["line"], "rule": f["rule"]}
                             for f in self.dossier.findings)
            self._runner(stage, replace(self.dossier, findings=redacted))
        return {}

    def _build(self, text: str) -> dict:
        return self._stage_by_runner("BUILD")

    def _verify(self, text: str) -> dict:
        return self._stage_by_runner("VERIFY")

    def _register(self, text: str) -> dict:
        detail = self._stage_by_runner("REGISTER")
        self.installed = (f"{self.dossier.owner}/{self.dossier.repo}@{self.dossier.sha}",)
        return detail

    def _resume(self, text: str) -> dict:
        return self._stage_by_runner("RESUME")

    def _maintain(self, text: str) -> dict:
        return self._stage_by_runner("MAINTAIN")
