"""
Deterministic skill validation: shape, references, safety, secrets, privacy.

PASS, WARN or FAIL, every finding named with a code, so the curator, the
lifecycle and the publisher act on the same evidence. No model judgment
here: a linter that agrees with itself on every run is the point.

Three questions, three consumers:

    may it be ACTIVATED?   FAIL on malformed, unsafe, secret-bearing content
    may it be SHARED?      `for_publication=True` promotes privacy WARNs to FAIL
    what is its RISK?      `classify_risk` - from the content, never from
                           what the skill says about itself
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field

from friday.skills import contracts as K
from friday.skills import files as F

PASS, WARN, FAIL = "PASS", "WARN", "FAIL"

#: The sections Hermes's template asks for. Missing ones are a WARN: a
#: procedure without verification is a guess dressed as a skill.
EXPECTED_SECTIONS = ("when to use", "procedure", "verification")

#: SKILL.md above this is a reference document, not a procedure.
#: Hermes's hard limit is 100k characters; this is where the curator moves
#: material into references/.
LONG_SKILL_CHARS = 20_000

#: Instructions no skill may carry. (pattern, code, detail)
UNSAFE_PATTERNS: tuple[tuple[re.Pattern, str, str], ...] = (
    (re.compile(r"ignore (all |any |the )?(previous|prior|above|earlier) (instructions|rules|guidance)", re.I),
     "prompt_injection", "asks the model to ignore its instructions"),
    (re.compile(r"\b(disable|bypass|turn off|skip|circumvent|override)\b[^.\n]{0,40}\b(policy|policy engine|approval|confirmation|safety|guard|guardrail|kernel|trust root)s?\b", re.I),
     "policy_bypass", "asks to disable or bypass a policy or approval gate"),
    (re.compile(r"\brm\s+-rf\s+(/|~|\$HOME|\*)(?![\w.-])", re.I), "destructive_command", "recursive delete of a root or home"),
    (re.compile(r"\bformat\s+[a-z]:\b", re.I), "destructive_command", "formats a drive"),
    (re.compile(r"\bgit\s+push\b[^\n]*--force\b(?![^\n]*--force-with-lease)", re.I),
     "destructive_command", "force-push without a lease"),
    (re.compile(r"\b(curl|wget|iwr|invoke-webrequest)\b[^\n|]*\|\s*(ba|z|k)?sh\b", re.I),
     "remote_code_execution", "pipes a download into a shell"),
    (re.compile(r"\biex\s*\(|invoke-expression\s*\(?.*downloadstring", re.I),
     "remote_code_execution", "executes downloaded PowerShell"),
    (re.compile(r"\b(exfiltrate|smuggle|leak)\b[^.\n]{0,60}\b(key|secret|token|password|credential)s?\b", re.I),
     "exfiltration", "moves credentials out"),
    (re.compile(r"\b(send|post|upload|email)\b[^.\n]{0,40}\b(api key|secret|token|password|credential)s?\b[^.\n]{0,40}\bto\b", re.I),
     "exfiltration", "sends credentials somewhere"),
    (re.compile(r"\b(grant|give)\b[^.\n]{0,30}\b(yourself|itself|this skill)\b[^.\n]{0,30}\b(permission|authority|access|rights)\b", re.I),
     "self_authorization", "a skill cannot grant itself authority"),
)

#: Zero-width and bidi characters hide text from a reader but not a model.
HIDDEN_CHARS = frozenset("​‌‍‎‏‪‫‬‭‮⁠⁦⁧⁨⁩﻿")

#: Credentials. Exact well-known shapes FAIL; a generic assignment WARNs.
SECRET_PATTERNS: tuple[tuple[re.Pattern, str], ...] = (
    (re.compile(r"\bsk-(proj-|ant-)?[A-Za-z0-9_-]{20,}"), "openai/anthropic-style key"),
    (re.compile(r"\bAKIA[0-9A-Z]{16}\b"), "AWS access key id"),
    (re.compile(r"\bgh[pousr]_[A-Za-z0-9]{36,}\b"), "GitHub token"),
    (re.compile(r"\bgithub_pat_[A-Za-z0-9_]{22,}\b"), "GitHub fine-grained token"),
    (re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}"), "Slack token"),
    (re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b"), "Google API key"),
    (re.compile(r"-----BEGIN (RSA |EC |OPENSSH |DSA |PGP )?PRIVATE KEY-----"), "private key block"),
    (re.compile(r"\bAPI[A-Za-z0-9]{12,}\b(?=.{0,80}(livekit|secret))", re.I | re.S), "LiveKit API key"),
)
GENERIC_SECRET = re.compile(
    r"\b(api[_-]?key|secret|token|password|passwd|client[_-]?secret)\b\s*[:=]\s*['\"]?([A-Za-z0-9_\-/+.]{16,})", re.I)
PLACEHOLDER = re.compile(r"(x{4,}|your[-_ ]|example|placeholder|<[^>]+>|\$\{?[A-Z_]+\}?|%[A-Z_]+%|\.\.\.)", re.I)

#: Things that identify a machine or a person. WARN for a private skill,
#: FAIL for one leaving the machine.
PRIVATE_PATTERNS: tuple[tuple[re.Pattern, str], ...] = (
    (re.compile(r"\b[A-Za-z]:\\Users\\[^\\\s\"']+"), "a Windows user profile path"),
    (re.compile(r"(?<![\w/])/home/[^/\s\"']+"), "a Linux home path"),
    (re.compile(r"(?<![\w/])/Users/[^/\s\"']+"), "a macOS home path"),
    (re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b"), "an e-mail address"),
    (re.compile(r"\b(localhost|127\.0\.0\.1|10\.\d+\.\d+\.\d+|192\.168\.\d+\.\d+):\d+\b"), "an internal address"),
)

#: The residue of one incident. The curator strips these; the linter warns.
INCIDENT_PATTERNS: tuple[tuple[re.Pattern, str], ...] = (
    (re.compile(r"\b20\d\d-\d\d-\d\d\b"), "a calendar date"),
    (re.compile(r"\b(run|proc|task|job)[-_ ]?[0-9a-f]{8,}\b", re.I), "a run or process id"),
    (re.compile(r"\b\d{1,2}:\d\d(:\d\d)?\s*(am|pm|ist|utc)?\b", re.I), "a clock time"),
)

UNBOUNDED = re.compile(r"\b(repeat|retry|loop|keep (trying|going))\b[^.\n]{0,30}\b(until it works|forever|indefinitely|endlessly)\b", re.I)

#: Keys a skill may not set about itself. Verification is external evidence.
SELF_CLAIMS = ("state", "verified", "quality_score", "quality", "trust", "last_verified", "risk")

REFERENCE_LINK = re.compile(r"(?<![\w/])((?:references|templates|scripts|assets)/[\w./-]+)")

#: Risk classification by what the procedure does. Highest class wins.
RISK_SIGNALS: tuple[tuple[str, re.Pattern], ...] = (
    (K.ACCOUNT_CONTROL, re.compile(r"\b(reset|change|rotate)\b[^.\n]{0,30}\b(password|2fa|mfa|recovery (code|email)|account (settings|owner))\b|\b(delete|close|deactivate)\b[^.\n]{0,20}\baccount\b", re.I)),
    # "checkout" alone is a git word and "wire" a cable; only the money senses count.
    (K.FINANCIAL, re.compile(r"\b(pay|payment|purchase|refund|invoice|wire transfer|transfer (money|funds)|credit card|bank(ing)? (account|transfer|login)|checkout (page|cart|flow)|proceed to checkout)\b", re.I)),
    (K.SECURITY_SENSITIVE, re.compile(r"\b(firewall|sudo|administrator|admin rights|elevat(e|ion)|certificate|ssh key|api key|token|credential|permission|acl|policy)\b", re.I)),
    (K.DESTRUCTIVE, re.compile(r"\b(rm -rf|del /[sq]|rmdir /s|remove-item .*-recurse|drop table|truncate|wipe|format [a-z]:|reset --hard|force[- ]push|push\b[^.\n]{0,30}--force|overwrite|delete)\b", re.I)),
    (K.EXTERNAL_WRITE, re.compile(r"\b(send|email|post|publish|push|deploy|upload|submit|tweet|message|book|order|call)\b", re.I)),
    (K.LOW_RISK_WRITE, re.compile(r"\b(create|write|edit|save|commit|rename|move|copy|install|generate|update)\b", re.I)),
)


@dataclass
class Finding:
    level: str
    code: str
    detail: str
    file: str = "SKILL.md"

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class LintReport:
    skill: str
    findings: list[Finding] = field(default_factory=list)
    for_publication: bool = False

    @property
    def verdict(self) -> str:
        levels = {f.level for f in self.findings}
        if FAIL in levels:
            return FAIL
        if WARN in levels:
            return WARN
        return PASS

    def failures(self) -> list[Finding]:
        return [f for f in self.findings if f.level == FAIL]

    def warnings(self) -> list[Finding]:
        return [f for f in self.findings if f.level == WARN]

    def codes(self) -> set[str]:
        return {f.level + ":" + f.code for f in self.findings}

    def to_dict(self) -> dict:
        return {"skill": self.skill, "verdict": self.verdict, "for_publication": self.for_publication,
                "findings": [f.to_dict() for f in self.findings]}


# ---------------------------------------------------------------------------
# scanners (also used on drafts before a file exists)
# ---------------------------------------------------------------------------

def scan_unsafe(text: str, file: str = "SKILL.md") -> list[Finding]:
    found = [Finding(FAIL, code, detail, file) for pattern, code, detail in UNSAFE_PATTERNS if pattern.search(text)]
    hidden = sorted({f"U+{ord(ch):04X}" for ch in text if ch in HIDDEN_CHARS})
    if hidden:
        found.append(Finding(FAIL, "hidden_text", "invisible characters " + ", ".join(hidden), file))
    if UNBOUNDED.search(text):
        found.append(Finding(WARN, "unbounded_instruction", "a loop with no bound", file))
    return found


def scan_secrets(text: str, file: str = "SKILL.md") -> list[Finding]:
    found = [Finding(FAIL, "secret", f"looks like {what}", file) for pattern, what in SECRET_PATTERNS if pattern.search(text)]
    for match in GENERIC_SECRET.finditer(text):
        value = match.group(2)
        if PLACEHOLDER.search(value):
            continue
        found.append(Finding(WARN, "possible_secret", f"'{match.group(1)}' is assigned a literal value", file))
    return found


def scan_private(text: str, file: str = "SKILL.md", *, for_publication: bool = False) -> list[Finding]:
    level = FAIL if for_publication else WARN
    return [Finding(level, "private_data", f"contains {what}", file) for pattern, what in PRIVATE_PATTERNS if pattern.search(text)]


def scan_incident_noise(text: str, file: str = "SKILL.md") -> list[Finding]:
    return [Finding(WARN, "incident_specific", f"contains {what} - a lesson, not a log", file)
            for pattern, what in INCIDENT_PATTERNS if pattern.search(text)]


def classify_risk(text: str) -> str:
    """The highest risk class whose signals appear in the text."""
    found = K.READ_ONLY
    for risk, pattern in RISK_SIGNALS:
        if pattern.search(text) and K.RISK_ORDER[risk] > K.RISK_ORDER[found]:
            found = risk
    return found


def scan_text(text: str, *, for_publication: bool = False) -> list[Finding]:
    """Every content scanner over one text, for drafts that are not on disk yet."""
    return (scan_unsafe(text) + scan_secrets(text) + scan_private(text, for_publication=for_publication)
            + scan_incident_noise(text))


# ---------------------------------------------------------------------------
# the linter
# ---------------------------------------------------------------------------

def lint(skill: F.SkillFiles, *, for_publication: bool = False) -> LintReport:
    report = LintReport(skill=skill.name, for_publication=for_publication)
    add = report.findings.append

    if not skill.exists():
        add(Finding(FAIL, "missing", f"{skill.skill_md} does not exist"))
        return report

    text = skill.text()
    try:
        frontmatter, body = F.parse_skill_md(text)
    except K.SkillError as exc:
        add(Finding(FAIL, "frontmatter", str(exc)))
        return report
    if not frontmatter:
        add(Finding(FAIL, "frontmatter", "no YAML frontmatter"))
        return report

    name = str(frontmatter.get("name", "") or "")
    problem = K.valid_name(name)
    if problem:
        add(Finding(FAIL, "name", problem))
    elif name != skill.name:
        add(Finding(FAIL, "name", f"frontmatter name '{name}' differs from directory '{skill.name}'"))

    description = str(frontmatter.get("description", "") or "").strip()
    if not description:
        add(Finding(FAIL, "description", "missing description"))
    elif len(description) > K.MAX_DESCRIPTION_LENGTH:
        add(Finding(FAIL, "description", f"longer than {K.MAX_DESCRIPTION_LENGTH} characters"))

    if not frontmatter.get("version"):
        add(Finding(WARN, "version", "no version; the lifecycle will assume 1.0.0"))
    elif K.parse_version(str(frontmatter["version"])) == (0, 0, 0):
        add(Finding(FAIL, "version", f"unparseable version {frontmatter['version']!r}"))

    for platform in F.as_list(frontmatter.get("platforms")):
        if platform not in K.PLATFORMS:
            add(Finding(FAIL, "platforms", f"unknown platform {platform!r} (windows, macos, linux)"))

    if len(text) > K.MAX_SKILL_CONTENT_CHARS:
        add(Finding(FAIL, "size", f"SKILL.md exceeds Hermes's {K.MAX_SKILL_CONTENT_CHARS:,} characters"))
    elif len(text) > LONG_SKILL_CHARS:
        add(Finding(WARN, "size", f"SKILL.md is {len(text):,} characters; move material into references/"))

    for path in skill.files():
        relative = path.relative_to(skill.dir).as_posix()
        if path == skill.skill_md:
            continue
        top = relative.split("/", 1)[0]
        if "/" not in relative:
            add(Finding(WARN, "layout", f"{relative} sits beside SKILL.md; Hermes expects references/, templates/, scripts/ or assets/", relative))
        elif top not in K.ALLOWED_SUBDIRS:
            add(Finding(WARN, "layout", f"{relative} is outside the allowed subdirectories", relative))
        if path.stat().st_size > K.MAX_SKILL_FILE_BYTES:
            add(Finding(FAIL, "size", f"{relative} exceeds 1 MiB", relative))

    lowered = body.lower()
    for section in EXPECTED_SECTIONS:
        if section not in lowered:
            add(Finding(WARN, "section", f"no '{section}' section"))

    headings = [line.strip().lower() for line in body.splitlines() if line.startswith("#")]
    duplicates = sorted({h for h in headings if headings.count(h) > 1})
    if duplicates:
        add(Finding(WARN, "duplicate_section", "repeated headings: " + ", ".join(duplicates)))

    for link in sorted(set(REFERENCE_LINK.findall(body))):
        if not (skill.dir / link).exists():
            add(Finding(FAIL, "broken_reference", f"{link} is referenced but missing"))

    friday = F.friday_metadata(frontmatter)
    for claim in SELF_CLAIMS:
        if claim in friday:
            add(Finding(FAIL, "self_verification",
                        f"metadata.friday.{claim} is set by the skill itself; verification, risk and trust are external"))
    if not F.as_list(friday.get("tests")):
        add(Finding(WARN, "tests", "no metadata.friday.tests; the evaluator can only lint and trigger-test it"))
    if not F.as_list(friday.get("depends_on")):
        add(Finding(WARN, "dependencies", "no metadata.friday.depends_on; a code change can never revalidate it"))

    if K.estimate_tokens(text) > 8_000:
        add(Finding(WARN, "tokens", f"about {K.estimate_tokens(text):,} tokens to load; keep procedures under 8k"))

    for path in skill.files():
        relative = path.relative_to(skill.dir).as_posix()
        if path.suffix.lower() in (".png", ".jpg", ".jpeg", ".gif", ".ico", ".onnx", ".bin", ".zip", ".pdf"):
            continue
        content = path.read_text(encoding="utf-8", errors="replace")
        report.findings.extend(scan_unsafe(content, relative))
        report.findings.extend(scan_secrets(content, relative))
        report.findings.extend(scan_private(content, relative, for_publication=for_publication))
        if path == skill.skill_md:
            report.findings.extend(scan_incident_noise(content, relative))

    return report
