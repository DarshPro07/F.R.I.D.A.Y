"""
Forge: Friday writing its own capabilities, with the boundaries outside the
generated code.

The lifecycle is borrowed from Ada-SI (spec -> static check -> isolated
verification -> registration). Its *trust model* is not, and that is the whole
point of this module:

    A Python venv is dependency isolation. It is not a security sandbox.

Python's own documentation describes venv as isolating the interpreter and
packages from other environments. It makes no claim about the host filesystem,
processes, network, credentials or OS APIs - and generated code inside one can
still call `subprocess.run`, walk `Path.home()`, or open a socket. So this
chain is NOT a safety argument, and this module never treats it as one:

    generated python -> AST passed -> ran in a temp venv -> therefore safe

What is actually claimed, gate by gate, is written down in CLAIMS below and
returned with every verification, so a caller cannot accidentally infer more
safety than was demonstrated.

The design decision that follows from being honest about the above: a forged
skill is deliberately *not* arbitrary host Python. It is a function that
receives explicit inputs, talks to Friday through a CapabilityContext with a
fixed method surface, and returns explicit outputs. Anything a skill wants to
do to the world it asks the context to do, and the context is ordinary Friday
code with the ordinary policy engine behind it. Arbitrary code needs a real
OS-level boundary - a container, a job object, an AppContainer - and until
that exists, that kind of skill is refused rather than mislabelled.

Forge is also deliberately small. It is not a second coding executor: anything
that needs to change Friday's own source goes to the Claude Executor and its
worktree pipeline, which already isolates, verifies, promotes, rejects and
rolls back. Two promotion models would be one too many.
"""

from __future__ import annotations

import ast
import hashlib
import json
import logging
import os
import re
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path

from friday.config import DATA_DIR, PROJECT_ROOT

logger = logging.getLogger("friday.forge")

SKILLS_DIR = DATA_DIR / "forge" / "skills"

# ---------------------------------------------------------------------------
# What each gate actually proves. Returned with every verification.
# ---------------------------------------------------------------------------

CLAIMS: dict[str, str] = {
    "static_gate": (
        "Rejects code that names a forbidden construct. An early rejection "
        "layer, not a proof of runtime safety: it cannot see what dynamic "
        "code does."
    ),
    "environment_scrub": (
        "PROVEN. The verification subprocess is given an explicit environment "
        "built from nothing, so Friday's API keys, OAuth tokens and companion "
        "secret are absent from it - not hidden, absent."
    ),
    "capability_context": (
        "The intended interface: a skill asks Friday to act rather than "
        "acting itself, and those calls go through the ordinary policy engine."
    ),
    "process_boundary": (
        "A separate process with its own working directory. Bounds runtime "
        "and contains a crash. It is NOT a privilege boundary - the child "
        "runs as the same user."
    ),
    "skillspector": (
        "A static scan of SKILL.md, code and declared dependencies by NVIDIA "
        "SkillSpector. A high-severity finding rejects the candidate, and a "
        "scan that could not run is never read as a clean one. A pass is one "
        "layer, not a proof of runtime safety."
    ),
    "not_claimed": (
        "That hostile code which defeats the static gate cannot reach the "
        "filesystem, network or OS. Nothing here demonstrates that. A venv "
        "isolates dependencies, not privileges."
    ),
}

# ---------------------------------------------------------------------------
# Gate 1 - the specification
# ---------------------------------------------------------------------------

#: Resource verdicts a spec may declare.
DENY = "deny"
ALLOW = "allow"
SCOPED = "scoped"

#: Modules a skill may import regardless of its dependency list. Pure data and
#: text handling only: nothing here reaches the filesystem, the network, the
#: process table or the interpreter's own machinery.
STDLIB_SAFE = frozenset({
    "base64", "binascii", "bisect", "calendar", "cmath", "collections",
    "colorsys", "copy", "csv", "dataclasses", "datetime", "decimal", "difflib",
    "enum", "fractions", "functools", "hashlib", "heapq", "hmac", "html",
    "itertools", "json", "math", "numbers", "operator", "random", "re",
    "statistics", "string", "textwrap", "time", "typing", "unicodedata",
    "urllib.parse", "uuid", "zoneinfo",
})

#: Refused outright, whatever the spec says. These are the routes by which
#: code stops being the code that was reviewed.
NEVER_IMPORT = frozenset({
    "ctypes", "importlib", "imp", "marshal", "pickle", "shelve", "dill",
    "subprocess", "multiprocessing", "os", "sys", "shutil", "socket",
    "ssl", "signal", "threading", "asyncio", "pty", "tty", "fcntl", "mmap",
    "gc", "inspect", "types", "builtins", "__builtin__", "code", "codeop",
    "compileall", "py_compile", "runpy", "site", "sysconfig", "atexit",
    "webbrowser", "tempfile", "pathlib", "glob", "fileinput", "linecache",
    "sqlite3", "dbm", "winreg", "msvcrt", "platform", "getpass", "pwd", "spwd",
})

#: Names that are an escape hatch by construction.
NEVER_CALL = frozenset({
    "eval", "exec", "compile", "__import__", "open", "input", "breakpoint",
    "globals", "locals", "vars", "memoryview",
})

#: Attribute access that walks out of the object graph into the interpreter.
NEVER_ATTRIBUTE = frozenset({
    "__globals__", "__builtins__", "__subclasses__", "__bases__", "__mro__",
    "__code__", "__closure__", "__func__", "__self__", "__loader__",
    "__spec__", "__dict__", "__getattribute__", "__reduce__",
    "__reduce_ex__", "__class_getitem__", "f_globals", "f_locals", "f_back",
    "gi_frame", "cr_frame",
})

NAME_RE = re.compile(r"^[a-z][a-z0-9_]{2,39}$")


class ForgeError(ValueError):
    """A specification or a candidate was refused. The message says why."""


@dataclass(frozen=True)
class CapabilitySpec:
    """
    What the skill must do, and what it is permitted to touch.

    Forge never receives "write whatever code fixes this". It receives this,
    and the implementation is judged against it - which is what makes a
    refusal explainable rather than a matter of taste.
    """

    name: str
    goal: str
    inputs: dict[str, str]              # name -> plain-language type
    outputs: dict[str, str]
    side_effects: tuple[str, ...] = ()
    filesystem: str = DENY              # deny | scoped (read-only, given paths)
    network: str = DENY                 # deny | scoped (allow-listed hosts)
    process: str = DENY                 # always deny in v1
    environment: str = DENY             # always deny in v1
    allowed_hosts: tuple[str, ...] = ()
    dependencies: tuple[str, ...] = ()  # third-party, beyond STDLIB_SAFE
    permissions: tuple[str, ...] = ()   # Friday tool ids the context may use
    verification: tuple[dict, ...] = () # [{"inputs": {...}, "expect": {...}}]
    budget_seconds: float = 20.0

    def __post_init__(self) -> None:
        if not NAME_RE.match(self.name):
            raise ForgeError(
                f"skill name {self.name!r} must be lowercase, 3-40 chars, "
                "letters/digits/underscore, starting with a letter")
        if not self.goal.strip():
            raise ForgeError("a spec needs a goal; that is the whole point")
        if not self.inputs:
            raise ForgeError(
                "a spec needs declared inputs - a skill that takes anything "
                "cannot be judged against anything")
        if not self.outputs:
            raise ForgeError("a spec needs declared outputs")
        if not self.verification:
            raise ForgeError(
                "a spec needs verification criteria. A skill nobody can check "
                "is a skill nobody should install")
        for verdict, field_name in ((self.filesystem, "filesystem"),
                                    (self.network, "network"),
                                    (self.process, "process"),
                                    (self.environment, "environment")):
            if verdict not in (DENY, SCOPED, ALLOW):
                raise ForgeError(
                    f"{field_name} must be {DENY!r}, {SCOPED!r} or {ALLOW!r}")
        if self.process != DENY or self.environment != DENY:
            raise ForgeError(
                "process and environment access are refused in this version. "
                "They need an OS-level boundary that does not exist yet, and "
                "shipping them behind a venv would be calling dependency "
                "isolation a sandbox")
        if self.network == ALLOW or self.filesystem == ALLOW:
            raise ForgeError(
                f"unscoped access is refused; use {SCOPED!r} and name what is "
                "allowed")
        if self.network == SCOPED and not self.allowed_hosts:
            raise ForgeError("scoped network access must name its hosts")
        if not 1.0 <= self.budget_seconds <= 120.0:
            raise ForgeError("budget_seconds must be between 1 and 120")

    def to_dict(self) -> dict:
        return {
            "name": self.name, "goal": self.goal, "inputs": self.inputs,
            "outputs": self.outputs, "side_effects": list(self.side_effects),
            "filesystem": self.filesystem, "network": self.network,
            "process": self.process, "environment": self.environment,
            "allowed_hosts": list(self.allowed_hosts),
            "dependencies": list(self.dependencies),
            "permissions": list(self.permissions),
            "verification": list(self.verification),
            "budget_seconds": self.budget_seconds,
        }

    @classmethod
    def from_dict(cls, raw: dict) -> "CapabilitySpec":
        if not isinstance(raw, dict):
            raise ForgeError("a spec must be an object")
        known = {
            "name", "goal", "inputs", "outputs", "side_effects", "filesystem",
            "network", "process", "environment", "allowed_hosts",
            "dependencies", "permissions", "verification", "budget_seconds",
        }
        unknown = set(raw) - known
        if unknown:
            raise ForgeError(f"unknown spec field(s): {', '.join(sorted(unknown))}")
        tuples = ("side_effects", "allowed_hosts", "dependencies",
                  "permissions", "verification")
        data = {k: (tuple(v) if k in tuples and v is not None else v)
                for k, v in raw.items()}
        try:
            return cls(**data)
        except TypeError as exc:
            raise ForgeError(str(exc)) from exc


# ---------------------------------------------------------------------------
# Gate 2 - the static gate
# ---------------------------------------------------------------------------


@dataclass
class StaticFinding:
    line: int
    kind: str
    detail: str

    def __str__(self) -> str:
        return f"line {self.line}: {self.detail}"


def _module_root(name: str) -> str:
    return (name or "").split(".")[0]


def static_gate(source: str, spec: CapabilitySpec) -> list[StaticFinding]:
    """
    Reject code that *names* something it must not use.

    This is an early rejection layer. It catches the obvious - and most
    generated code that goes wrong goes wrong obviously - but it cannot prove
    what dynamic code does at runtime, and nothing here should be described as
    sandboxing. That is why `NEVER_CALL` includes the constructs which make
    static reading meaningless in the first place: eval, exec, compile and
    __import__ are refused precisely so that reading the source stays a
    meaningful thing to do.
    """
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        return [StaticFinding(exc.lineno or 0, "syntax",
                              f"the code does not parse: {exc.msg}")]

    allowed_modules = set(STDLIB_SAFE) | {_module_root(d) for d in spec.dependencies}
    findings: list[StaticFinding] = []

    def refuse(node, kind: str, detail: str) -> None:
        findings.append(StaticFinding(getattr(node, "lineno", 0), kind, detail))

    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            else:
                # `from . import x` has no module; relative imports are out.
                if node.level:
                    refuse(node, "import", "relative imports are not permitted")
                    continue
                names = [node.module or ""]
            for name in names:
                root = _module_root(name)
                if root in NEVER_IMPORT:
                    refuse(node, "import",
                           f"{name!r} is refused outright - it is a route out "
                           f"of the reviewed code")
                elif root not in allowed_modules and name not in allowed_modules:
                    refuse(node, "import",
                           f"{name!r} is not in the spec's dependencies "
                           f"{sorted(spec.dependencies) or '[]'} nor the safe "
                           f"standard library set")

        elif isinstance(node, ast.Call):
            target = node.func
            if isinstance(target, ast.Name) and target.id in NEVER_CALL:
                refuse(node, "call", f"{target.id}() is not permitted")
            elif isinstance(target, ast.Attribute) and target.attr in NEVER_CALL:
                refuse(node, "call", f".{target.attr}() is not permitted")

        elif isinstance(node, ast.Attribute):
            if node.attr in NEVER_ATTRIBUTE:
                refuse(node, "attribute",
                       f".{node.attr} reaches into the interpreter")

        elif isinstance(node, ast.Name) and node.id in NEVER_CALL:
            # `f = eval` then `f(...)`: the call site would look innocent.
            if isinstance(getattr(node, "ctx", None), ast.Load):
                findings.append(StaticFinding(
                    node.lineno, "name",
                    f"{node.id} is not permitted, even uncalled"))

    # The entry point must exist and take the context first.
    entry = next((n for n in tree.body
                  if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
                  and n.name == "run"), None)
    if entry is None:
        findings.append(StaticFinding(
            0, "shape", "no top-level `run` function; a skill is one function"))
    else:
        params = [a.arg for a in entry.args.args]
        if not params or params[0] != "ctx":
            findings.append(StaticFinding(
                entry.lineno, "shape",
                "`run` must take the capability context first: run(ctx, ...)"))
        declared = set(spec.inputs)
        taken = set(params[1:])
        if taken != declared:
            findings.append(StaticFinding(
                entry.lineno, "shape",
                f"`run` takes {sorted(taken)} but the spec declares "
                f"{sorted(declared)}"))

    return findings


# ---------------------------------------------------------------------------
# Gate 4 - the environment scrub
# ---------------------------------------------------------------------------

#: Anything matching these is never passed to a verification process. The list
#: is a backstop: the environment is built from nothing rather than filtered,
#: so a new provider key is excluded by default rather than by remembering to
#: add it here. This exists to catch the day someone changes that.
SECRET_SHAPED = re.compile(
    r"(KEY|TOKEN|SECRET|PASSWORD|PASSWD|CREDENTIAL|AUTH|SESSION|COOKIE|"
    r"PRIVATE|SIGNATURE|LICENSE|BEARER|OAUTH|API)", re.I)

#: The only variables a verification process gets. Everything else is absent.
#: PATH is needed to find the interpreter; SYSTEMROOT and friends are needed
#: for Python to start at all on Windows.
ENV_KEEP = ("PATH", "SYSTEMROOT", "SYSTEMDRIVE", "WINDIR", "COMSPEC",
            "NUMBER_OF_PROCESSORS", "PROCESSOR_ARCHITECTURE", "PATHEXT",
            "TEMP", "TMP", "LANG", "LC_ALL")


def scrubbed_env() -> dict[str, str]:
    """
    Build the child's environment from nothing.

    Friday holds Google, Groq, OpenAI, LiveKit and Sarvam credentials, a
    companion pairing token and whatever the executor needs for GitHub. A
    forged skill has no business being able to read any of them, and the
    reliable way to ensure that is to construct the environment rather than
    subtract from it - a filter has to be updated every time a credential is
    added, and will eventually not be.
    """
    env = {}
    for name in ENV_KEEP:
        value = os.environ.get(name)
        if value is None:
            continue
        if SECRET_SHAPED.search(name):        # belt and braces; none should
            continue
        env[name] = value
    # A skill's own imports must not resolve against Friday's tree.
    env["PYTHONNOUSERSITE"] = "1"
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    return env


def env_leaks(env: dict[str, str]) -> list[str]:
    """Names in a candidate environment that look like a credential."""
    return sorted(name for name in env if SECRET_SHAPED.search(name)
                  and name not in ("PATHEXT",))


# ---------------------------------------------------------------------------
# Gate 2b - SkillSpector, the mandatory pre-activation security scan
# ---------------------------------------------------------------------------

#: The scanner is NVIDIA SkillSpector, run as a CLI (its stdio MCP mode has a
#: known initialisation problem). It is not pinned or installed by default, so
#: the command is configurable: a JSON list of arguments in this variable, with
#: "{target}" standing for the directory to scan. Assumed, not verified against
#: a release: `skillspector scan --format json <dir>` printing JSON.
SKILLSPECTOR_ENV = "FRIDAY_SKILLSPECTOR_CMD"
DEFAULT_SKILLSPECTOR_CMD = ("skillspector", "scan", "--format", "json",
                            "{target}")
SCAN_TIMEOUT_SECONDS = 120.0

SCAN_CLEAN = "CLEAN"
SCAN_BLOCKED = "BLOCKED"
#: Neither of these is a pass. The scan did not happen, so nothing is known.
SCAN_UNAVAILABLE = "UNAVAILABLE"
SCAN_ERROR = "ERROR"

_SEVERITIES = ("info", "low", "medium", "high", "critical")
_BLOCKING_SEVERITIES = frozenset({"high", "critical"})
#: Prompt injection, exfiltration, privilege escalation, tool or memory
#: poisoning, dangerous code and vulnerable dependencies are high severity by
#: definition, whatever the scanner called them.
_BLOCKING_CATEGORY_WORDS = ("injection", "exfiltrat", "privilege", "poison",
                            "dangerous", "vulnerab")
_FINDING_LISTS = ("findings", "issues", "results", "vulnerabilities")


@dataclass
class ScanReport:
    status: str
    findings: list[dict] = field(default_factory=list)
    detail: str = ""
    scanner_version: str = ""
    scanned_at: str = ""
    target_sha256: str = ""
    report_sha256: str = ""

    @property
    def clean(self) -> bool:
        return self.status == SCAN_CLEAN

    def blocking(self) -> list[dict]:
        return [f for f in self.findings if f["severity"] in _BLOCKING_SEVERITIES]

    def to_dict(self) -> dict:
        # What PRD v7 s47 says to keep: scan version, date, source, findings,
        # report hash.
        return {
            "status": self.status, "scanner": "skillspector",
            "scanner_version": self.scanner_version,
            "scanned_at": self.scanned_at,
            "target_sha256": self.target_sha256,
            "report_sha256": self.report_sha256,
            "detail": self.detail, "findings": self.findings,
            "blocking": len(self.blocking()),
        }


def _normalise_finding(raw) -> dict:
    """
    One finding in a fixed shape. A severity that is missing or unreadable is
    HIGH: a scanner whose output cannot be read is not evidence of anything.
    """
    if not isinstance(raw, dict):
        return {"severity": "high", "category": "unparsed",
                "message": str(raw)[:300], "file": "", "line": None}

    def pick(*keys):
        for key in keys:
            if raw.get(key) not in (None, ""):
                return raw[key]
        return ""

    category = re.sub(r"[^a-z0-9]+", "_", str(
        pick("category", "type", "rule", "rule_id", "id")).lower()).strip("_")
    severity = str(pick("severity", "level", "risk")).strip().lower()
    if severity not in _SEVERITIES or any(
            word in category for word in _BLOCKING_CATEGORY_WORDS):
        severity = "high"
    line = pick("line", "lineno")
    return {"severity": severity, "category": category,
            "message": str(pick("message", "title", "description"))[:300],
            "file": str(pick("file", "path", "location"))[:200],
            "line": line if isinstance(line, int) else None}


def parse_skillspector(raw: str) -> tuple[list[dict], str]:
    """Findings and scanner version from SkillSpector's JSON, or ValueError."""
    try:
        data = json.loads(raw)
    except ValueError as exc:
        raise ValueError("SkillSpector output is not JSON") from exc
    version = ""
    if isinstance(data, dict):
        if data.get("error"):
            raise ValueError(f"SkillSpector reported an error: {data['error']}")
        version = str(data.get("version") or data.get("scanner_version") or "")
        items = next((data[key] for key in _FINDING_LISTS
                      if isinstance(data.get(key), list)), None)
    else:
        items = data
    if not isinstance(items, list):
        raise ValueError("SkillSpector output has no findings list")
    return [_normalise_finding(item) for item in items], version


def _skillspector_argv(target: Path, command) -> list[str]:
    if command is None:
        raw = os.environ.get(SKILLSPECTOR_ENV)
        try:
            command = json.loads(raw) if raw else list(DEFAULT_SKILLSPECTOR_CMD)
        except ValueError:
            command = None
        if not (isinstance(command, list) and command
                and all(isinstance(arg, str) for arg in command)):
            raise ForgeError(
                f"{SKILLSPECTOR_ENV} must be a JSON list of strings")
    argv = [arg.replace("{target}", str(target)) for arg in command]
    if not any("{target}" in arg for arg in command):
        argv.append(str(target))
    return argv


def run_skillspector(target: Path, *, command=None,
                     timeout: float = SCAN_TIMEOUT_SECONDS) -> ScanReport:
    """
    Scan a directory and report. Never raises, and never returns CLEAN unless
    the scanner ran and its output was read as an empty or sub-high findings
    list: a missing binary, a timeout, an exit with unreadable output are all
    "not scanned".

    The scanner gets the same credential-free environment as a verification
    process; it reads files and has no use for Friday's keys.
    """
    from friday.contracts import now_iso

    report = ScanReport(status=SCAN_ERROR, scanned_at=now_iso())
    try:
        argv = _skillspector_argv(Path(target), command)
        completed = subprocess.run(
            argv, capture_output=True, text=True, env=scrubbed_env(),
            cwd=str(target), timeout=timeout)
    except ForgeError as exc:
        report.detail = str(exc)
        return report
    except FileNotFoundError:
        report.status = SCAN_UNAVAILABLE
        report.detail = "the SkillSpector CLI is not installed or not on PATH"
        return report
    except subprocess.TimeoutExpired:
        report.detail = f"SkillSpector exceeded its {timeout:g}s budget"
        return report
    except OSError as exc:
        report.detail = f"SkillSpector could not be started: {exc}"
        return report

    report.report_sha256 = digest(completed.stdout or "")
    try:
        report.findings, report.scanner_version = parse_skillspector(
            completed.stdout)
    except ValueError as exc:
        report.detail = (f"{exc} (exit {completed.returncode}): "
                         f"{(completed.stderr or '').strip()[-200:]}")
        return report
    report.status = SCAN_BLOCKED if report.blocking() else SCAN_CLEAN
    return report


def render_skill_md(spec: CapabilitySpec) -> str:
    """The SKILL.md a forged candidate is scanned as: its stated intent."""
    def listing(items) -> str:
        return "\n".join(f"- {key}: {value}" if value else f"- {key}"
                         for key, value in items) or "- none"

    return (
        f"---\nname: {spec.name}\ndescription: {json.dumps(spec.goal)}\n---\n\n"
        f"# {spec.name}\n\n{spec.goal}\n\n"
        f"## Inputs\n{listing(spec.inputs.items())}\n\n"
        f"## Outputs\n{listing(spec.outputs.items())}\n\n"
        f"## Side effects\n{listing((s, '') for s in spec.side_effects)}\n\n"
        f"## Permissions\n{listing((p, '') for p in spec.permissions)}\n")


def stage_for_scan(spec: CapabilitySpec, source: str, directory: Path) -> Path:
    """Lay a candidate out as the skill directory SkillSpector scans."""
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "SKILL.md").write_text(render_skill_md(spec), encoding="utf-8")
    (directory / "skill.py").write_text(source, encoding="utf-8")
    if spec.dependencies:
        (directory / "requirements.txt").write_text(
            "\n".join(spec.dependencies) + "\n", encoding="utf-8")
    return directory


# ---------------------------------------------------------------------------
# Gate 3 + 5 - verification, then a registry where passing is not promotion
# ---------------------------------------------------------------------------

CANDIDATE = "CANDIDATE"
VERIFIED = "VERIFIED"
REGISTERED = "REGISTERED"
ENABLED = "ENABLED"
REJECTED = "REJECTED"

LIFECYCLE = (CANDIDATE, VERIFIED, REGISTERED, ENABLED, REJECTED)

#: Passing tests does not mean "always on". Each step is a separate decision,
#: and the one that matters - ENABLED, with the scopes it is enabled for - is
#: never taken by the thing that wrote the code.
PROMOTIONS = {
    CANDIDATE: (VERIFIED, REJECTED),
    VERIFIED: (REGISTERED, REJECTED),
    REGISTERED: (ENABLED, REJECTED),
    ENABLED: (REGISTERED, REJECTED),
    REJECTED: (),
}


def digest(source: str) -> str:
    return hashlib.sha256(source.encode("utf-8")).hexdigest()


@dataclass
class VerificationReport:
    passed: bool
    cases: list[dict] = field(default_factory=list)
    findings: list[str] = field(default_factory=list)
    stderr: str = ""
    took_ms: int = 0
    env_names: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "passed": self.passed, "cases": self.cases,
            "findings": self.findings, "stderr": self.stderr[-2000:],
            "took_ms": self.took_ms,
            "environment_given": self.env_names,
            "claims": CLAIMS,
        }


def verify(source: str, spec: CapabilitySpec, *,
           workdir: Path | None = None) -> VerificationReport:
    """
    Run the skill's declared cases in a separate process with no credentials.

    The static gate runs first and a failure there is terminal: there is no
    reason to execute code that has already been refused, and "we ran it to
    see" is how a sandbox that is not a sandbox gets exercised.
    """
    started = time.monotonic()
    findings = static_gate(source, spec)
    if findings:
        return VerificationReport(
            passed=False, findings=[str(f) for f in findings],
            took_ms=int((time.monotonic() - started) * 1000))

    workdir = Path(workdir or (DATA_DIR / "forge" / "staging" / spec.name))
    workdir.mkdir(parents=True, exist_ok=True)
    skill_path = workdir / "skill.py"
    skill_path.write_text(source, encoding="utf-8")
    payload = {"skill": str(skill_path), "spec": spec.to_dict()}
    request = workdir / "request.json"
    request.write_text(json.dumps(payload), encoding="utf-8")

    env = scrubbed_env()
    try:
        completed = subprocess.run(
            [sys.executable, "-I", "-m", "friday.forge_runtime", str(request)],
            capture_output=True, text=True, env=env,
            cwd=str(PROJECT_ROOT),
            timeout=spec.budget_seconds + 10,
        )
    except subprocess.TimeoutExpired:
        return VerificationReport(
            passed=False, findings=[f"exceeded its {spec.budget_seconds}s budget"],
            took_ms=int((time.monotonic() - started) * 1000),
            env_names=sorted(env))

    took = int((time.monotonic() - started) * 1000)
    try:
        report = json.loads(completed.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError):
        return VerificationReport(
            passed=False,
            findings=["the verification process returned nothing usable"],
            stderr=completed.stderr, took_ms=took, env_names=sorted(env))

    cases = report.get("cases") or []
    return VerificationReport(
        passed=bool(cases) and all(case.get("passed") for case in cases),
        cases=cases, findings=report.get("findings") or [],
        stderr=completed.stderr, took_ms=took, env_names=sorted(env))


# ---------------------------------------------------------------------------
# The registry
# ---------------------------------------------------------------------------

#: Risk is derived from what the spec permits, never asserted by whatever
#: wrote the code. A skill that touches nothing is not risky because its
#: author was careful; it is not risky because it cannot reach anything.
def risk_of(spec: CapabilitySpec) -> str:
    if spec.network == SCOPED and spec.filesystem == SCOPED:
        return "elevated"
    if spec.network == SCOPED or spec.filesystem == SCOPED:
        return "moderate"
    return "contained"


_STORE = None


def store():
    global _STORE
    if _STORE is None:
        from friday.store import Store

        _STORE = Store()
    return _STORE


def reset_store(new=None) -> None:
    global _STORE
    _STORE = new


class Registry:
    """
    Where a verified skill waits for someone to decide it should run.

    The states exist because "the tests passed" and "this is on" are different
    claims, and collapsing them is how a system that writes its own code
    quietly grows capabilities nobody chose.
    """

    def __init__(self, skills_dir: Path | None = None) -> None:
        self.dir = Path(skills_dir or SKILLS_DIR)
        self.dir.mkdir(parents=True, exist_ok=True)

    def submit(self, spec: CapabilitySpec, source: str, *,
               provenance: str) -> dict:
        """Record a candidate. No verification, no promises, no execution."""
        from friday.contracts import now_iso

        existing = store().get_forged_skill(spec.name)
        path = self.dir / f"{spec.name}.py"
        path.write_text(source, encoding="utf-8")
        record = {
            "name": spec.name,
            "state": CANDIDATE,
            "version": (existing["version"] + 1) if existing else 1,
            "spec": json.dumps(spec.to_dict()),
            "source_sha256": digest(source),
            "source_path": str(path),
            "risk": risk_of(spec),
            "provenance": provenance,
            "verification": json.dumps({}),
            "verified_at": None,
            # A new version is never born enabled, even if the previous one
            # was: the scopes belonged to code that no longer exists.
            "scopes": json.dumps([]),
            "created_at": existing["created_at"] if existing else now_iso(),
            "updated_at": now_iso(),
        }
        store().save_forged_skill(record)
        store().record_forged_transition(
            spec.name, existing["state"] if existing else None, CANDIDATE,
            f"submitted (version {record['version']})", provenance)
        return self.get(spec.name)

    def get(self, name: str) -> dict | None:
        return store().get_forged_skill(name)

    def list(self, state: str | None = None) -> list[dict]:
        return store().forged_skills(state)

    def history(self, name: str) -> list[dict]:
        return store().forged_skill_history(name)

    # -- transitions -------------------------------------------------------

    @staticmethod
    def _row(record: dict, **changes) -> dict:
        from friday.contracts import now_iso

        row = {
            "name": record["name"], "state": record["state"],
            "version": record["version"],
            "spec": json.dumps(record["spec"]),
            "source_sha256": record["source_sha256"],
            "source_path": record["source_path"],
            "risk": record["risk"], "provenance": record["provenance"],
            "verification": json.dumps(record["verification"]),
            "verified_at": record["verified_at"],
            "scopes": json.dumps(record["scopes"]),
            "created_at": record["created_at"], "updated_at": now_iso(),
        }
        row.update(changes)
        return row

    def _transition(self, name: str, to_state: str, *, reason: str,
                    actor: str, **changes) -> dict:
        record = store().get_forged_skill(name)
        if record is None:
            raise ForgeError(f"no forged skill named {name!r}")
        allowed = PROMOTIONS[record["state"]]
        if to_state not in allowed:
            raise ForgeError(
                f"{name!r} is {record['state']}; it may only move to "
                f"{', '.join(allowed) or 'nowhere'}, not {to_state}")
        # Activation needs a clean SkillSpector scan of this exact source, on
        # top of whatever got the skill here. Demoting an ENABLED skill is a
        # safety move and is never blocked by it.
        if to_state in (REGISTERED, ENABLED) and record["state"] != ENABLED:
            scan = record["verification"].get("security_scan") or {}
            if (scan.get("status") != SCAN_CLEAN
                    or scan.get("target_sha256") != record["source_sha256"]):
                raise ForgeError(
                    f"{name!r} has no clean SkillSpector scan of this exact "
                    "source, so it is not activated")

        store().save_forged_skill(self._row(record, state=to_state, **changes))
        store().record_forged_transition(
            name, record["state"], to_state, reason, actor)
        return self.get(name)

    def _load_source(self, name: str, actor: str) -> tuple[dict, str | None]:
        """
        The candidate and its source, re-read from disk and re-hashed.

        A check that refers to code which has since changed is worse than none:
        it carries the authority of a check that was never run on this code.
        The source comes back None when that mismatch has just rejected it.
        """
        record = store().get_forged_skill(name)
        if record is None:
            raise ForgeError(f"no forged skill named {name!r}")
        source = Path(record["source_path"]).read_text(encoding="utf-8")
        if digest(source) != record["source_sha256"]:
            return self._transition(
                name, REJECTED, actor=actor,
                reason="the file on disk is not what was submitted"), None
        return record, source

    def _scan(self, record: dict, source: str, actor: str) -> dict:
        name = record["name"]
        spec = CapabilitySpec.from_dict(record["spec"])
        with tempfile.TemporaryDirectory(prefix="forge-scan-") as staging:
            report = run_skillspector(stage_for_scan(spec, source, Path(staging)))
        report.target_sha256 = record["source_sha256"]
        verification = json.dumps(
            {**record["verification"], "security_scan": report.to_dict()})

        if report.status == SCAN_BLOCKED:
            kinds = sorted({f["category"] or f["severity"]
                            for f in report.blocking()})
            return self._transition(
                name, REJECTED, actor=actor, verification=verification,
                reason=(f"SkillSpector: {len(report.blocking())} high-severity "
                        f"finding(s): {', '.join(kinds)}")[:300])
        # Keep what the scan said even when it did not complete, then refuse
        # to go on: a scan that did not run is not a scan that passed.
        store().save_forged_skill(self._row(record, verification=verification))
        if not report.clean:
            raise ForgeError(
                f"SkillSpector could not scan {name!r} ({report.detail}); it "
                f"stays {record['state']} and cannot be verified or activated "
                "until a scan completes")
        return self.get(name)

    def scan(self, name: str, *, actor: str = "forge") -> dict:
        """
        Scan the candidate with SkillSpector and keep the findings on it.

        A high-severity finding rejects it. A scan that could not run raises
        and changes nothing but the recorded scan outcome.
        """
        record, source = self._load_source(name, actor)
        if source is None:
            return record
        return self._scan(record, source, actor)

    def verify(self, name: str, *, actor: str = "forge") -> dict:
        """
        Scan the skill, then run its own criteria; record the outcome either way.

        SkillSpector goes first because the scan is mandatory and static:
        nothing is run that the scanner has already condemned.
        """
        record, source = self._load_source(name, actor)
        if source is None:
            return record
        record = self._scan(record, source, actor)
        if record["state"] == REJECTED:
            return record
        scan = record["verification"]["security_scan"]

        spec = CapabilitySpec.from_dict(record["spec"])
        report = verify(source, spec)
        verification = {**report.to_dict(), "security_scan": scan}
        if not report.passed:
            return self._transition(
                name, REJECTED, actor=actor,
                reason="; ".join(report.findings)[:300] or "its own cases failed",
                verification=json.dumps(verification))

        from friday.contracts import now_iso

        return self._transition(
            name, VERIFIED, actor=actor,
            reason=f"{len(report.cases)} declared case(s) passed",
            verification=json.dumps(verification), verified_at=now_iso())

    def register(self, name: str, *, actor: str) -> dict:
        """Installed and addressable. Still not callable by anything."""
        return self._transition(name, REGISTERED, actor=actor,
                                reason="registered")

    def enable(self, name: str, scopes: tuple[str, ...], *, actor: str) -> dict:
        """
        The decision that matters, and the one Forge never takes for itself.

        `actor` is recorded because "who turned this on" is the first question
        asked after a skill does something surprising.
        """
        if not scopes:
            raise ForgeError(
                "enabling needs the scopes it is enabled for; 'on' without a "
                "scope is the ambient authority this lifecycle exists to avoid")
        if actor == "forge":
            raise ForgeError(
                "the thing that wrote the code does not get to switch it on")
        return self._transition(name, ENABLED, actor=actor,
                                reason=f"enabled for {', '.join(scopes)}",
                                scopes=json.dumps(list(scopes)))

    def reject(self, name: str, reason: str, *, actor: str) -> dict:
        return self._transition(name, REJECTED, actor=actor, reason=reason)
