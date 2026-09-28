"""
What a skill is to FRIDAY, on top of what it is to Hermes.

Hermes's contract (kept verbatim so every FRIDAY skill is a valid Hermes
skill): a directory holding `SKILL.md` with YAML frontmatter (`name`,
`description`, optional `version`, `author`, `license`, `platforms`,
`metadata.hermes.*`), optional `references/ templates/ scripts/ assets/`,
name `^[a-z0-9][a-z0-9._-]*$` of at most 64 characters, description of at
most 1024, SKILL.md of at most 100k characters, files of at most 1 MiB.

FRIDAY's additions live under `metadata.friday` in the same frontmatter
(a Hermes install ignores keys it does not know) and in the registry:

    scope        who the procedure is for (user, project, repository, ...)
    depends_on   files, globs, packages, skills this procedure relies on
    tests        commands that prove the procedure still works
    triggers     phrases that should select it (and `never_for`: should not)
    task         the task key two skills may compete for (conflict detection)
    risk         classified by FRIDAY from the content - never taken from
                 the skill's own claim
    publish      how far this may travel: local, private, public

States, scopes, risk classes and the transition table are the whole policy
surface; every module in this package imports them from here so there is
exactly one spelling of each.
"""

from __future__ import annotations

import re
import secrets
import time
from dataclasses import asdict, dataclass, field

# ---------------------------------------------------------------------------
# Hermes's rules, unchanged
# ---------------------------------------------------------------------------

NAME_RE = re.compile(r"^[a-z0-9][a-z0-9._-]*$")
MAX_NAME_LENGTH = 64
MAX_DESCRIPTION_LENGTH = 1024
MAX_SKILL_CONTENT_CHARS = 100_000
MAX_SKILL_FILE_BYTES = 1_048_576
ALLOWED_SUBDIRS = ("references", "templates", "scripts", "assets")
PLATFORMS = ("windows", "macos", "linux")

#: Hermes's own comment: ~2.75 characters per token. Good enough for a budget.
CHARS_PER_TOKEN = 2.75

# ---------------------------------------------------------------------------
# Lifecycle
# ---------------------------------------------------------------------------

DISCOVERED = "DISCOVERED"          # seen on disk, not yet judged
DRAFT = "DRAFT"                    # proposed or written, not linted
STAGED = "STAGED"                  # lint passed, not yet tested
TESTING = "TESTING"                # tests declared, evaluation pending
ACTIVE = "ACTIVE"                  # evaluated, selectable
NEEDS_REVALIDATION = "NEEDS_REVALIDATION"   # a dependency changed
DEGRADED = "DEGRADED"              # quality fell below the threshold
STALE = "STALE"                    # revalidation found the procedure outdated
BROKEN = "BROKEN"                  # revalidation failed
QUARANTINED = "QUARANTINED"        # unsafe content, or untrusted origin not yet validated
DEPRECATED = "DEPRECATED"          # superseded, kept for history
DISABLED = "DISABLED"              # switched off by the owner

STATES = (DISCOVERED, DRAFT, STAGED, TESTING, ACTIVE, NEEDS_REVALIDATION, DEGRADED,
          STALE, BROKEN, QUARANTINED, DEPRECATED, DISABLED)

#: Which moves are legal. Everything else is refused with the reason; a
#: state that is not in the table cannot be left (DEPRECATED is terminal
#: except for a deliberate revival to DRAFT).
TRANSITIONS: dict[str, tuple[str, ...]] = {
    DISCOVERED: (DRAFT, QUARANTINED, DISABLED),
    DRAFT: (STAGED, QUARANTINED, DEPRECATED, DISABLED),
    STAGED: (TESTING, ACTIVE, DRAFT, QUARANTINED, DISABLED),
    TESTING: (ACTIVE, DRAFT, BROKEN, QUARANTINED, DISABLED),
    ACTIVE: (NEEDS_REVALIDATION, DEGRADED, DEPRECATED, QUARANTINED, DISABLED, DRAFT),
    NEEDS_REVALIDATION: (ACTIVE, STALE, BROKEN, DRAFT, DISABLED, QUARANTINED),
    # DEGRADED / STALE / BROKEN -> ACTIVE is a rollback to a version that has
    # its own recorded evaluation; the lifecycle demands that evidence.
    DEGRADED: (ACTIVE, DRAFT, DEPRECATED, DISABLED),
    STALE: (ACTIVE, DRAFT, DEPRECATED, DISABLED),
    BROKEN: (ACTIVE, DRAFT, DEPRECATED, DISABLED),
    # Never straight to ACTIVE: quarantine is left through lint and tests.
    QUARANTINED: (DRAFT, DEPRECATED, DISABLED),
    DEPRECATED: (DRAFT,),
    DISABLED: (DRAFT, ACTIVE),
}

#: States in which the router may select a skill.
SELECTABLE = (ACTIVE, NEEDS_REVALIDATION)

# ---------------------------------------------------------------------------
# Scope and tiers
# ---------------------------------------------------------------------------

GLOBAL = "GLOBAL"
USER = "USER"
ORGANIZATION = "ORGANIZATION"
PROJECT = "PROJECT"
REPOSITORY = "REPOSITORY"
SESSION = "SESSION"
TEMPORARY = "TEMPORARY"
SCOPES = (GLOBAL, USER, ORGANIZATION, PROJECT, REPOSITORY, SESSION, TEMPORARY)

#: More specific wins a conflict; equal specificity falls through to
#: version recency, then to an explicit CONFLICT.
SCOPE_SPECIFICITY = {REPOSITORY: 6, PROJECT: 5, SESSION: 4, TEMPORARY: 4,
                     USER: 3, ORGANIZATION: 2, GLOBAL: 1}

#: Codebase knowledge. Never leaves the machine or the private tap.
CODEBASE_SCOPES = (PROJECT, REPOSITORY)

BUILT_IN = "BUILT_IN"
LOCAL_USER = "LOCAL_USER"
PROJECT_TIER = "PROJECT"
SHARED_PRIVATE = "SHARED_PRIVATE"
PUBLISHED_PUBLIC = "PUBLISHED_PUBLIC"
TIERS = (BUILT_IN, LOCAL_USER, PROJECT_TIER, SHARED_PRIVATE, PUBLISHED_PUBLIC)

# ---------------------------------------------------------------------------
# Risk and trust
# ---------------------------------------------------------------------------

READ_ONLY = "READ_ONLY"
LOW_RISK_WRITE = "LOW_RISK_WRITE"
EXTERNAL_WRITE = "EXTERNAL_WRITE"
DESTRUCTIVE = "DESTRUCTIVE"
SECURITY_SENSITIVE = "SECURITY_SENSITIVE"
FINANCIAL = "FINANCIAL"
ACCOUNT_CONTROL = "ACCOUNT_CONTROL"
RISK_CLASSES = (READ_ONLY, LOW_RISK_WRITE, EXTERNAL_WRITE, DESTRUCTIVE,
                SECURITY_SENSITIVE, FINANCIAL, ACCOUNT_CONTROL)
RISK_ORDER = {name: index for index, name in enumerate(RISK_CLASSES)}

#: Classes that never activate without the owner's explicit approval, and
#: never through an agent-created path at all.
HIGH_RISK = (DESTRUCTIVE, SECURITY_SENSITIVE, FINANCIAL, ACCOUNT_CONTROL)

TRUST_BUILT_IN = "built_in"      # shipped with FRIDAY
TRUST_OWNER = "owner"            # written or approved by the owner
TRUST_AGENT = "agent"            # written by FRIDAY/Hermes, validated here
TRUST_COMMUNITY = "community"    # installed from outside
TRUST_LEVELS = (TRUST_BUILT_IN, TRUST_OWNER, TRUST_AGENT, TRUST_COMMUNITY)

# ---------------------------------------------------------------------------
# Extension levels and publishing
# ---------------------------------------------------------------------------

LEVEL_SKILL = "SKILL"            # instructions + existing tools
LEVEL_CAPABILITY = "CAPABILITY"  # approved code/API/CLI/MCP (friday.capabilities)
LEVEL_PLUGIN = "PLUGIN"          # installed Python extension (friday.forge)

PUBLISH_LOCAL = "local"
PUBLISH_PRIVATE = "private"
PUBLISH_PUBLIC = "public"
PUBLISH_LEVELS = (PUBLISH_LOCAL, PUBLISH_PRIVATE, PUBLISH_PUBLIC)

# ---------------------------------------------------------------------------
# Records
# ---------------------------------------------------------------------------


class SkillError(ValueError):
    """A refused operation, with the reason in the message."""


class TransitionRefused(SkillError):
    """The lifecycle said no."""


def now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def parse_version(text: str) -> tuple[int, int, int]:
    """`1.2.3` -> (1, 2, 3). Missing parts are zero; anything else is 0.0.0."""
    parts = str(text or "").strip().split(".")
    numbers: list[int] = []
    for part in parts[:3]:
        digits = "".join(ch for ch in part if ch.isdigit())
        numbers.append(int(digits) if digits else 0)
    while len(numbers) < 3:
        numbers.append(0)
    return numbers[0], numbers[1], numbers[2]


def bump_version(text: str, kind: str) -> str:
    """Semantic bump: `patch` (non-behavioural), `minor` (compatible
    procedure improvement), `major` (breaking workflow change)."""
    major, minor, patch = parse_version(text)
    if kind == "major":
        return f"{major + 1}.0.0"
    if kind == "minor":
        return f"{major}.{minor + 1}.0"
    if kind == "patch":
        return f"{major}.{minor}.{patch + 1}"
    raise SkillError(f"unknown version bump '{kind}' (patch, minor, major)")


def estimate_tokens(text: str) -> int:
    return int(len(text or "") / CHARS_PER_TOKEN) + 1


@dataclass
class SkillRecord:
    """One skill as the registry knows it. The SKILL.md is the source for
    name/description/version; everything else is FRIDAY's judgment."""

    name: str
    description: str = ""
    version: str = "1.0.0"
    scope: str = USER
    tier: str = LOCAL_USER
    state: str = DISCOVERED
    path: str = ""                       # the skill directory
    source: str = ""                     # where it came from (objective id, install, owner)
    source_url: str = ""
    author: str = ""
    trust: str = TRUST_AGENT
    risk: str = READ_ONLY
    content_hash: str = ""
    category: str = ""
    tags: list[str] = field(default_factory=list)
    platforms: list[str] = field(default_factory=list)
    permissions: list[str] = field(default_factory=list)
    dependencies: list[str] = field(default_factory=list)
    tests: list[str] = field(default_factory=list)
    triggers: list[str] = field(default_factory=list)
    never_for: list[str] = field(default_factory=list)
    task: str = ""
    publish: str = PUBLISH_LOCAL
    quality_score: float = 0.0
    usage_count: int = 0
    success_count: int = 0
    failure_count: int = 0
    correction_count: int = 0
    created_at: str = field(default_factory=now_iso)
    updated_at: str = field(default_factory=now_iso)
    last_verified: str = ""
    codebase_id: str = ""
    codebase_commit: str = ""

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "SkillRecord":
        allowed = {name for name in cls.__dataclass_fields__}
        return cls(**{key: value for key, value in data.items() if key in allowed})


@dataclass
class SkillProposal:
    """What the candidate detector hands to the curator: a reason, the
    evidence, and a draft SKILL.md. Not a skill yet."""

    name: str
    reason: str
    criteria: list[str]
    expected_reuse: str
    scope: str
    risk: str
    content: str                         # a complete SKILL.md draft
    dependencies: list[str] = field(default_factory=list)
    tests: list[str] = field(default_factory=list)
    evidence: list[str] = field(default_factory=list)
    source: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class EvaluationReport:
    """
    The only evidence the lifecycle accepts for activation. Produced by
    friday.skills.evaluator; carries a nonce so a report cannot be forged
    from inside a skill file or replayed for another version.
    """

    skill: str
    version: str
    content_hash: str
    passed: bool
    checks: list[tuple[str, str, str]]   # (name, PASS|WARN|FAIL, detail)
    token_estimate: int = 0
    at: str = field(default_factory=now_iso)
    nonce: str = field(default_factory=lambda: secrets.token_hex(8))

    def failures(self) -> list[str]:
        return [f"{name}: {detail}" for name, state, detail in self.checks if state == "FAIL"]

    def to_dict(self) -> dict:
        return asdict(self)


def valid_name(name: str) -> str | None:
    """None when the name is acceptable to Hermes; the reason otherwise."""
    if not name:
        return "empty name"
    if len(name) > MAX_NAME_LENGTH:
        return f"name longer than {MAX_NAME_LENGTH} characters"
    if not NAME_RE.match(name):
        return "name must be lowercase letters, digits, '.', '_' or '-', starting with a letter or digit"
    return None
