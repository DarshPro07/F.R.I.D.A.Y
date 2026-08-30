"""
Which specialists a development run gets, and why those.

The idea is Agency Agents': a library of specialist role definitions, so that
"review this for security" comes with the framing a security reviewer would
actually bring rather than being a generic model told to be careful.

The thing that library gets wrong for Friday's purposes is scale. A hundred
role definitions loaded into every run is not a hundred experts; it is a
hundred prompts competing for the same context window, and the runtime drops
whatever does not fit. So:

    A role is not a process.
    A role is not a model.
    A role is scoped instructions applied to a selected executor.

One run gets between one and four of them, chosen for the work, and the
choice is recorded so that "why was a security reviewer on this?" has an
answer. Trivial work gets one role, because spawning a committee to rename a
variable is theatre that costs real minutes.

The catalogue here is small and curated on purpose. Roles are cheap to add
and expensive to choose between - a list of eighty means the selection step
becomes its own hard problem, which is the problem this module exists to
avoid.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field

logger = logging.getLogger("friday-agent")

#: How much work this is, which decides how many people look at it.
TRIVIAL = "TRIVIAL"        # rename, formatting, a constant
SMALL = "SMALL"            # one function, one file, a clear bug
MEDIUM = "MEDIUM"          # a feature across a few files
LARGE = "LARGE"            # architecture, migration, anything irreversible

#: How many roles each size is allowed. The cap is the point of the table.
TEAM_SIZE = {TRIVIAL: 1, SMALL: 2, MEDIUM: 3, LARGE: 4}


@dataclass(frozen=True)
class Role:
    """One specialist, as instructions rather than as a runtime."""

    id: str
    title: str
    #: One line, in the voice the role would use. Prepended to the task.
    focus: str
    #: What this role is for. Matched against the goal.
    triggers: tuple[str, ...] = ()
    #: What it is expected to produce.
    delivers: str = ""
    #: Roughly how much of the context window its framing costs, in
    #: characters. Selection is capped by this as well as by count.
    cost: int = 400
    #: A reviewing role must not also be the implementing role - see
    #: `compile_team`.
    reviews: bool = False

    def instructions(self) -> str:
        return f"[{self.title}] {self.focus}"


#: The curated set. Engineering and product only; Friday does not need a
#: Reddit community ninja to change a Python module.
CATALOGUE: tuple[Role, ...] = (
    Role(id="architect", title="Software Architect",
         focus="Decide the shape before the code. Name the trade-off you are "
               "making and what it costs. Prefer the design that is easiest "
               "to delete.",
         triggers=("architecture", "design", "restructure", "refactor",
                   "migrate", "redesign", "subsystem", "schema", "protocol"),
         delivers="a design with its trade-offs named", cost=380),

    Role(id="minimal", title="Minimal Change Engineer",
         focus="Fix what was asked and nothing else. Three similar lines beat "
               "a premature abstraction. If the diff is growing, stop and say "
               "why.",
         triggers=("fix", "bug", "patch", "broken", "regression", "hotfix",
                   "crash", "error", "fails"),
         delivers="the smallest diff that works", cost=300),

    Role(id="implementer", title="Senior Developer",
         focus="Write it the way the surrounding code is written. Match its "
               "naming, its idiom and its comment density.",
         triggers=("implement", "build", "add", "create", "write", "feature",
                   "support"),
         delivers="working code in the codebase's own style", cost=320),

    Role(id="reviewer", title="Code Reviewer",
         focus="Look for what breaks, not for style. Name a concrete failing "
               "input for every problem you raise; if you cannot, it is a "
               "preference, so say so.",
         triggers=("review", "check", "audit", "verify", "correctness"),
         delivers="findings with a reproducing case each",
         cost=340, reviews=True),

    Role(id="security", title="Security and Privacy Engineer",
         focus="Assume the input is hostile and the caller is untrusted. "
               "Check the trust boundary, the secret handling and what gets "
               "logged.",
         triggers=("security", "auth", "authentication", "permission",
                   "credential", "secret", "token", "password", "privacy",
                   "encrypt", "sandbox", "untrusted", "sanitise", "sanitize"),
         delivers="the trust boundaries and what crosses them",
         cost=360, reviews=True),

    Role(id="tests", title="Test Engineer",
         focus="One runnable check that fails if the logic breaks. Test the "
               "behaviour, not the implementation - a test that asserts on "
               "source text measures the prose.",
         triggers=("test", "tests", "coverage", "flaky", "regression",
                   "verify", "assert"),
         delivers="tests that fail for the right reason", cost=300),

    Role(id="tooling", title="Developer Tooling Engineer",
         focus="Make the workflow shorter. A script somebody must remember to "
               "run is a script that does not run.",
         triggers=("build", "ci", "pipeline", "script", "tooling", "lint",
                   "package", "release", "deploy", "install"),
         delivers="a workflow with a step removed", cost=300),

    Role(id="prompt", title="Prompt Engineer",
         focus="An instruction competes with every other instruction present. "
               "Prefer making the wrong move impossible over asking for the "
               "right one.",
         triggers=("prompt", "instruction", "system message", "llm", "model "
                   "behaviour", "tool use", "agent behaviour"),
         delivers="an instruction that survives the others", cost=320),

    Role(id="voice", title="Voice AI Integration Engineer",
         focus="Latency is the feature. Every extra round trip is heard. Say "
               "what the turn costs before adding to it.",
         triggers=("voice", "speech", "stt", "tts", "livekit", "audio",
                   "latency", "turn", "realtime"),
         delivers="the turn, without the extra round trip", cost=320),

    Role(id="ux", title="UX Researcher",
         focus="Describe what the person is actually trying to do, and what "
               "they will believe happened. A feature nobody can find has not "
               "shipped.",
         triggers=("ux", "user", "journey", "usability", "onboarding",
                   "confusing", "workflow", "experience"),
         delivers="the journey, and where it breaks", cost=300),

    Role(id="data", title="Data and Storage Engineer",
         focus="Migrations run on real data that is already wrong. A "
               "migration that raises has its priorities backwards.",
         triggers=("database", "sqlite", "migration", "schema", "store",
                   "query", "index", "table", "persist"),
         delivers="a migration that survives bad rows", cost=320),
)

BY_ID = {role.id: role for role in CATALOGUE}

#: Signals that a change is bigger than it sounds. Any of these lifts the
#: estimate, because the expensive mistakes are the ones that looked small.
_LARGE = re.compile(
    r"\b(?:architect\w*|redesign|rewrite|migrat\w+|restructur\w+|"
    r"across the (?:codebase|project)|every|all of|breaking change|"
    r"irreversible|production)\b", re.IGNORECASE)

_TRIVIAL = re.compile(
    r"\b(?:rename|typo|whitespace|formatting|comment|docstring|"
    r"bump|constant|one[- ]liner)\b", re.IGNORECASE)

_MEDIUM = re.compile(
    r"\b(?:feature|implement|add support|integrate|endpoint|refactor|"
    r"module|subsystem)\b", re.IGNORECASE)


def size_of(goal: str, *, files: int = 0) -> str:
    """
    How big this is, from what was asked and how much it touches.

    File count wins when it is known, because it is a measurement and the
    words are a description. "Just a small fix" across nineteen files is not
    a small fix.
    """
    text = goal or ""
    if files >= 10:
        return LARGE
    if _LARGE.search(text):
        return LARGE
    if _TRIVIAL.search(text) and files <= 1:
        return TRIVIAL
    if files >= 4 or _MEDIUM.search(text):
        return MEDIUM
    if files >= 2:
        return MEDIUM
    return SMALL


@dataclass
class Team:
    """Who is on this run, and the reason each of them is."""

    size: str
    roles: tuple[Role, ...] = ()
    because: dict[str, str] = field(default_factory=dict)

    @property
    def cost(self) -> int:
        return sum(role.cost for role in self.roles)

    def instructions(self) -> str:
        return "\n".join(role.instructions() for role in self.roles)

    def as_dict(self) -> dict:
        return {"size": self.size,
                "roles": [r.id for r in self.roles],
                "because": self.because,
                "context_cost": self.cost}


def compile_team(goal: str, *, files: int = 0,
                 budget: int = 1200) -> Team:
    """
    Choose the specialists for one run.

    Selection is by evidence in the goal, then capped twice - by how big the
    work is, and by how much context the framing may consume. Both caps
    matter: the count stops a committee, and the budget stops four verbose
    roles from crowding out the actual task.

    An implementing role always comes first and a reviewing role never
    replaces it. A run with only a Code Reviewer on it reviews nothing,
    which is the failure mode of choosing roles purely by keyword.
    """
    text = (goal or "").lower()
    size = size_of(goal, files=files)
    limit = TEAM_SIZE[size]

    scored: list[tuple[int, Role]] = []
    for role in CATALOGUE:
        hits = sum(1 for trigger in role.triggers if trigger in text)
        if hits:
            scored.append((hits, role))
    scored.sort(key=lambda pair: (-pair[0], pair[1].id))

    chosen: list[Role] = []
    because: dict[str, str] = {}

    # Somebody has to write it. Without this a goal saying "review the auth
    # code" fields two reviewers and no author.
    doers = [role for _, role in scored if not role.reviews]
    lead = doers[0] if doers else BY_ID["implementer"]
    chosen.append(lead)
    because[lead.id] = ("matched the goal" if doers
                        else "every run needs somebody to do the work")

    for hits, role in scored:
        if len(chosen) >= limit:
            break
        if role.id in because:
            continue
        if sum(r.cost for r in chosen) + role.cost > budget:
            continue
        chosen.append(role)
        matched = [t for t in role.triggers if t in text]
        because[role.id] = f"the goal mentions {', '.join(matched[:3])}"

    # Anything past TRIVIAL gets a second pair of eyes, even if the goal
    # never used a reviewing word. Nobody writes "and check it for bugs".
    if size != TRIVIAL and len(chosen) < limit and \
            not any(r.reviews for r in chosen):
        reviewer = BY_ID["reviewer"]
        if sum(r.cost for r in chosen) + reviewer.cost <= budget:
            chosen.append(reviewer)
            because[reviewer.id] = "work of this size gets reviewed"

    team = Team(size=size, roles=tuple(chosen), because=because)
    logger.info("roles.team size=%s roles=%s cost=%d",
                size, ",".join(r.id for r in chosen), team.cost)
    return team
