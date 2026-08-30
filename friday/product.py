
"""
Turning a spoken idea into something that can actually be built.

The requirements loop already asked good questions and recorded the answers.
It stopped there, which is one step short of useful: the arc ended exactly
where the build should begin, so the boss still had to write the requirements,
the acceptance criteria and the Claude prompts himself.

    idea          "I want a small desktop game where I control a combat drone"
    claims        "I think Godot is probably best" - a preference, not a fact,
                  and it is checked rather than agreed with
    questions     only where the answer changes what gets built
    requirements  statements with acceptance criteria attached
    readiness     whether the remaining unknowns are cheap enough to proceed on

## A claim is not a fact

"I think Godot is probably the best choice, but verify that instead of just
agreeing with me" is the request this module exists to honour, and agreeing is
the failure mode. A statement the boss makes about the world - this library
supports multiplayer, this API is free - is a *claim*, and the plan is only as
good as whether it is true.

So claims are separated from decisions and carry a verdict: VERIFIED,
CONTRADICTED or UNCERTAIN, with what was read. A preference the boss simply
holds is recorded as a preference and not researched, because "do you prefer
dark mode" has no external truth to check.

## A requirement without acceptance is a wish

"The game should feel good" cannot be built or tested. Every requirement
carries acceptance criteria written as observable outcomes, and one that has
no measurable target - because nobody supplied one and research does not imply
one - is marked NEEDS_TARGET rather than given an invented number. A fabricated
"under 16ms" is worse than an admitted gap: it looks like a decision somebody
made.

## Readiness is not perfection

    NOT_READY                a blocking question is unanswered
    READY_WITH_ASSUMPTIONS   everything left is explicit, reversible and cheap
    READY                    nothing is outstanding

Waiting for READY would mean never starting. The middle state is the normal
one, and it is honest about what it is proceeding on.
"""

from __future__ import annotations

import logging

import re

from dataclasses import dataclass, field

logger = logging.getLogger("friday-agent.product")

FUNCTIONAL = "FUNCTIONAL"

UX = "UX"

PERFORMANCE = "PERFORMANCE"

SECURITY = "SECURITY"

DATA = "DATA"

PLATFORM = "PLATFORM"

INTEGRATION = "INTEGRATION"

OPERATIONS = "OPERATIONS"

BUSINESS = "BUSINESS"

CATEGORIES = (FUNCTIONAL, UX, PERFORMANCE, SECURITY, DATA, PLATFORM,
              INTEGRATION, OPERATIONS, BUSINESS)

PROPOSED = "PROPOSED"

ACCEPTED = "ACCEPTED"

SUPERSEDED = "SUPERSEDED"

REJECTED = "REJECTED"

#: A requirement nobody can measure yet. Marked rather than invented.
NEEDS_TARGET = "NEEDS_TARGET"


@dataclass
class Requirement:
    """One thing the product must do, and how anyone would know it does."""

    statement: str
    category: str = FUNCTIONAL
    rationale: str = ""
    source: str = ""
    acceptance: tuple[str, ...] = ()
    assumptions: tuple[str, ...] = ()
    status: str = PROPOSED
    needs_target: bool = False
    requirement_id: int = 0

    def to_dict(self) -> dict:
        return {
            "requirement_id": self.requirement_id,
            "statement": self.statement,
            "category": self.category,
            "rationale": self.rationale,
            "source": self.source,
            "acceptance": list(self.acceptance),
            "assumptions": list(self.assumptions),
            "status": self.status,
            "needs_target": self.needs_target,
        }

VERIFIED = "VERIFIED"

CONTRADICTED = "CONTRADICTED"

UNCERTAIN = "UNCERTAIN"

PREFERENCE = "PREFERENCE"          # nothing external to check

VERDICTS = (VERIFIED, CONTRADICTED, UNCERTAIN, PREFERENCE)


@dataclass
class Claim:
    """Something the boss asserted, and whether it turned out to be true."""

    claim: str
    verdict: str = UNCERTAIN
    evidence: str = ""
    sources: tuple[str, ...] = ()
    #: What the sources actually said, readable enough for a model to
    #: judge the claim against - the evidence, not a count of it.
    findings: str = ""

    @property
    def settled(self) -> bool:
        return self.verdict in (VERIFIED, CONTRADICTED, PREFERENCE)

#: Hedges. "I think X is best" is an opinion inviting a check; "use X" is an
#: instruction. Both name a technology and they want opposite treatment.
_HEDGED = re.compile(
    r"\b(?:i think|i believe|probably|might be|maybe|i'?m guessing|"
    r"i assume|presumably|i reckon|as far as i know|pretty sure|"
    r"i heard|apparently|supposedly)\b",
    re.IGNORECASE)

#: Asking to be checked rather than agreed with. The strongest signal there
#: is, and the one in the acceptance script.
_ASKS_TO_BE_CHECKED = re.compile(
    r"\b(?:verify|check|confirm|fact.?check|don'?t just agree|"
    r"instead of (?:just )?agreeing|challenge|push back|"
    r"tell me if i'?m wrong|am i right)\b",
    re.IGNORECASE)

#: A claim about the world rather than about what the boss wants. These have
#: an external truth; "I want dark mode" does not.
_ABOUT_THE_WORLD = re.compile(
    r"\b(?:supports?|works? with|is free|costs?|runs? on|handles?|"
    r"can do|is (?:the )?(?:best|fastest|easiest|standard)|"
    r"requires?|needs?|is deprecated|is faster|outperforms?)\b",
    re.IGNORECASE)


def claims_in(text: str) -> list[Claim]:
    """
    Statements worth checking before they shape a plan.

    Deliberately conservative. A preference has no external truth and
    researching it would be theatre; a hedged claim about the world is exactly
    what "verify that instead of just agreeing with me" is asking for.
    """
    found: list[Claim] = []
    asked_to_check = bool(_ASKS_TO_BE_CHECKED.search(text or ""))

    for sentence in re.split(r"[.;]|\bbut\b|\band\b", text or ""):
        sentence = sentence.strip()
        if len(sentence.split()) < 3:
            continue
        hedged = bool(_HEDGED.search(sentence))
        worldly = bool(_ABOUT_THE_WORLD.search(sentence))
        if not (hedged or worldly):
            continue
        if worldly or asked_to_check:
            found.append(Claim(claim=sentence, verdict=UNCERTAIN))
        elif hedged:
            # A hedged statement with no external content is a preference
            # softly held - "I think I want it simple". Nothing to check.
            found.append(Claim(claim=sentence, verdict=PREFERENCE))
    return found

_HEDGE_PREFIX = re.compile("^\\s*(?:i think|i believe|i reckon|i assume|i'?m guessing|i'?m pretty sure|i heard|apparently|supposedly|presumably|maybe)\\s+", re.IGNORECASE)

_SOFTENER = re.compile('\\b(?:probably|might be|maybe|perhaps|possibly)\\b', re.IGNORECASE)

_SUPERLATIVE = re.compile('\\b(?:best|better|worse|worst|ideal|optimal|superior|right (?:choice|fit|one|tool|call)|the way to go|fastest|easiest|cheapest|simplest)\\b', re.IGNORECASE)

# Restored from the .pyc oracle. Each pattern is a LOAD_CONST string
# and each flag a LOAD_ATTR on `re` in the compiled module - primary
# evidence from the running system, not inference.
_BUILDING = re.compile(
    '\\b(?:build|building|make|making|create|creating|write|writing)\\s+(?:a|an|the|some)?\\s*([^.;,]{3,70})',
    re.IGNORECASE,
)


def _building(request: str) -> str:
    """The thing being built, trimmed to something a search box can use."""
    match = _BUILDING.search(request or "")
    if not match:
        return ""
    return " ".join(match.group(1).split()[:12])


def as_question(claim: str, about: str = '') -> str:
    """
    A claim as something worth searching for.

    "I think Godot is probably the best choice," is a sentence about the
    speaker. The hedge is noise to a search engine and the claim underneath
    it is the actual question, so the hedge goes.

    Two things then have to be added back, and measured results for the drone
    request say how much they matter:

        "Godot is the best choice"              2,496 chars, vendor homepage
        "Godot best choice ...combat drone"     5,879 chars, itch.io demos
        "Godot vs alternatives for ...drone"   12,000 chars, four comparisons

    The first is what shipped and it cannot settle anything. The second finds
    people's drone games rather than an opinion about engines. The third asks
    the field instead of the subject, which is the only way a superlative is
    checkable, and it needs no list of rivals to do it - "vs alternatives"
    carries no domain knowledge, so this works the same for a claim about
    databases or headphones.
    """
    text = _HEDGE_PREFIX.sub("", (claim or "").strip())
    text = _SOFTENER.sub("", text)
    text = re.sub(r"\s{2,}", " ", text).strip(" ,.;:-")
    text = text or (claim or "").strip()

    # A plain factual claim searches well as it is: "Godot supports C#" finds
    # the page that says so. A judgement ("the best choice") finds the
    # vendor's own homepage, which agrees with everything, so the judgement
    # is turned into the comparison it is actually asking for.
    if not _SUPERLATIVE.search(text):
        return text

    # Strip the judgement down to its subject, then ask for the comparison.
    subject = _SUPERLATIVE.sub(" ", text)
    subject = re.sub(r"\b(?:is|are|was|were|the|a|an|choice|option|here|for (?:this|that|it|us|me))\b",
                     " ", subject, flags=re.IGNORECASE)
    subject = re.sub(r"\s{2,}", " ", subject).strip(" ,.;:-")
    if subject:
        text = f"{subject} vs alternatives"

    # And the thing being built, which is what the comparison is *for*.
    building = _building(about)
    return f"{text} for a {building}" if building else text


def wants_to_be_challenged(text: str) -> bool:
    """Whether the boss asked to be argued with rather than agreed with."""
    return bool(_ASKS_TO_BE_CHECKED.search(text or ""))


async def verify(claim: Claim, about: str = '') -> Claim:
    """
    Read about a claim and say whether it held up.

    Returns a new `Claim` rather than mutating: a verdict is evidence, and
    evidence with no record of what produced it is an opinion.

    A `PREFERENCE` is returned untouched. "I think I want it simple" has no
    external truth, and researching it would be theatre.

    `about` is the request the claim came out of. A claim is checkable only
    against what it is a claim about, and sentence splitting throws that away
    - see `as_question`.
    """
    import inspect

    if claim.verdict == PREFERENCE:
        return claim
    try:
        from friday import answer as A
        from friday.capability_runtime import CONVERSATION, CapabilityRuntime

        # The question planner picks the research capability the way it
        # would for a spoken question; a claim it cannot place is read by
        # the deep researcher, which is the one that reads whole pages
        # rather than result snippets. Four sources is enough to notice a
        # disagreement and few enough to come back inside a turn.
        question = as_question(claim.claim, about)
        plan = A.plan(question)
        if not plan.capability:
            plan.capability = "web_deep_research"
            plan.arguments = {"question": question, "sources": 4}

        result = CapabilityRuntime(principal=CONVERSATION).execute(
            plan.capability, plan.arguments)
        if inspect.isawaitable(result):
            result = await result
        output = result.output or {}
        sources = tuple(item.get("url", "")
                        for item in (output.get("sources") or [])
                        if isinstance(item, dict))[:5]
        if not sources:
            # Nothing read is nothing learned; the claim stays as it was,
            # and the reason is kept so the turn can say why.
            return Claim(claim=claim.claim, verdict=UNCERTAIN,
                         evidence=f"could not check it: {result.error}"[:200])

        # Read, not judged. Deciding whether a page supports a claim is a
        # language task, and doing it here with keywords produced verdicts
        # that were confidently wrong. So the verdict stays UNCERTAIN and the
        # findings travel with the claim, for the model that raised it to
        # weigh in the same turn.
        partial = "" if result.may_claim_completion else " (partial)"
        # The findings are the point: a source count says the pages exist,
        # the findings say what they claim, and only the second lets the
        # boss's assertion be answered rather than merely flagged.
        return Claim(claim=claim.claim, verdict=UNCERTAIN,
                     evidence=f"{len(sources)} source(s) read{partial}",
                     sources=sources, findings=_readable(output))
    except Exception:                                        # noqa: BLE001
        logger.exception("could not verify a claim; leaving it uncertain")
        return Claim(claim=claim.claim, verdict=UNCERTAIN,
                     evidence="verification failed")

_PER_SOURCE = 2500


def _readable(output: dict) -> str:
    """The pages, as something a model can actually reason over."""
    parts = []
    for item in (output.get("sources") or [])[:4]:
        if not isinstance(item, dict):
            continue
        body = (item.get("markdown") or "").strip()
        if not body:
            continue
        parts.append(f"--- {item.get('title') or item.get('url')}\n"
                     f"{item.get('url')}\n\n{body[:_PER_SOURCE]}")
    return "\n\n".join(parts)

# Restored from the .pyc oracle: proven by a LOAD_CONST/STORE_NAME
# pair in the running system's bytecode, present in no source candidate.
CHALLENGE = 'The reading is already done and it is above. Answer from it NOW, in this\nreply. Do not say you are looking into it, will check, or will report\nback - there is nothing left to look into, and saying so describes a\ncheck that did happen as one that did not. Measured: with the sources\npresent but no such line, Friday said "I\'m already looking into a\ncomparison of game engines for you".\n\nThe boss stated something and asked to be checked rather than agreed with.\nFrom the findings above:\n\n  - Say plainly whether the claim holds, does not hold, or cannot be settled\n    from what was read.\n  - If it does not hold, say so before anything else and give the alternative\n    the sources actually support.\n  - Distinguish "the sources disagree" from "the sources say no".\n  - Do not soften a contradiction into a preference. He asked.\n  - Lead with the verdict. Anything you still need to ask him comes after it,\n    not instead of it - measured: a reply that opened with "will this be 2D\n    or 3D?" and reached the claim three sentences later read as agreement\n    with caveats, which is the thing he asked you not to do.\n  - Say that you checked, and what the sources said. A verification nobody\n    can see is indistinguishable from an opinion - measured: holding four\n    engine comparisons, Friday said "Godot is definitely a strong contender",\n    which is true, cites nothing, and is exactly what it would have said\n    having read nothing at all.'

#: Words that describe a feeling rather than an outcome. A requirement built
#: only from these cannot be tested by anybody.
_UNTESTABLE = re.compile(
    r"\b(?:feel|feels?|nice|good|great|clean|modern|intuitive|polished|"
    r"smooth|snappy|fast|responsive|beautiful|simple|elegant)\b",
    re.IGNORECASE)

#: A number with a unit. Its presence is what separates "must be fast" from
#: something a test can check.
_MEASURED = re.compile(
    r"\d+\s*(?:ms|milliseconds?|s|seconds?|fps|hz|mb|gb|kb|%|percent|"
    r"users?|players?|items?|rows?)\b",
    re.IGNORECASE)


def acceptance_for(statement: str) -> tuple[tuple[str, ...], bool]:
    """
    (criteria, needs_target) for one requirement.

    Given/when/then because it forces an outcome. "Call move_drone()" is an
    implementation detail wearing acceptance clothes - it says what the code
    does rather than what the product does, and it passes while the product
    is broken.
    """
    statement = (statement or "").strip().rstrip(".")
    if not statement:
        return (), False

    needs_target = bool(_UNTESTABLE.search(statement)) and \
        not _MEASURED.search(statement)

    subject = re.sub(r"^(?:the |a |an )?(?:system|product|app|game|user)\s+"
                     r"(?:must|should|can|will)\s+", "", statement,
                     flags=re.IGNORECASE)
    criteria = [
        f"Given the product is running, when {subject}, then it is observably "
        f"so without the tester inspecting the code.",
    ]
    if needs_target:
        criteria.append(
            f"{NEEDS_TARGET}: no measurable target was given for this and "
            f"none was inferred - ask before treating it as testable.")
    return tuple(criteria), needs_target

#: Pairs that cannot both be true of one product. Deliberately short and
#: obvious: a clever contradiction detector that is wrong is worse than a
#: blunt one that is right, because it would refuse work for no reason.
_OPPOSED = (
    ({"offline", "no internet", "without a connection"},
     {"multiplayer", "online", "cloud", "realtime sync"}),
    ({"single-player", "single player", "solo only"},
     {"multiplayer", "co-op", "versus"}),
    ({"no accounts", "anonymous", "no sign-in"},
     {"sign in", "login", "user accounts"}),
    ({"free", "no payment"}, {"subscription", "paid", "in-app purchase"}),
)


def conflicts(statements) -> list[tuple[str, str]]:
    """Pairs that contradict each other. Raised, never silently resolved."""
    found: list[tuple[str, str]] = []
    lowered = [(text, (text or "").lower()) for text in statements]
    for left_words, right_words in _OPPOSED:
        lefts = [text for text, low in lowered
                 if any(word in low for word in left_words)]
        rights = [text for text, low in lowered
                  if any(word in low for word in right_words)]
        for left in lefts:
            for right in rights:
                if left != right:
                    found.append((left, right))
    return found

NOT_READY = "NOT_READY"

READY_WITH_ASSUMPTIONS = "READY_WITH_ASSUMPTIONS"

READY = "READY"


@dataclass
class Readiness:
    """Whether building can start, and what it would be starting on."""

    state: str = NOT_READY
    blockers: tuple[str, ...] = ()
    assumptions: tuple[str, ...] = ()
    unverified: tuple[str, ...] = ()
    #: The areas of the project an open question is holding up.
    blocked: tuple[str, ...] = ()
    because: str = ""

    @property
    def can_build(self) -> bool:
        return self.state in (READY, READY_WITH_ASSUMPTIONS)


def _touches(question_row, statement: str) -> bool:
    """
    Whether an open question actually decides anything about `statement`.

    Matched on the question's `impact` - what it says it affects - and
    failing that on the words of the question itself. Word overlap is a blunt
    instrument and it is the right one here: the alternative is asking a
    model, which would make readiness cost a round trip and vary between
    identical calls.
    """
    words = set(re.findall(r"[a-z0-9]{4,}",
                           ((question_row.get("impact") or "") + " "
                            + (question_row.get("question") or "")).lower()))
    words -= _READINESS_NOISE
    if not words:
        # A question with no content words is about everything, which is
        # the safe reading: it blocks rather than being silently ignored.
        return True
    target = (statement or "").lower()
    return any(word in target for word in words)

_READINESS_NOISE = frozenset(
    {
        'about',
        'been',
        'being',
        'build',
        'building',
        'built',
        'could',
        'does',
        'first',
        'from',
        'have',
        'into',
        'just',
        'make',
        'need',
        'over',
        'project',
        'should',
        'than',
        'that',
        'then',
        'there',
        'thing',
        'this',
        'used',
        'using',
        'version',
        'want',
        'were',
        'what',
        'when',
        'which',
        'will',
        'with',
        'would',
        'your',
        'yours',
    },
)


def readiness(project: str, *, scope: str = '', db=None) -> Readiness:
    """
    Whether this project can be built now.

    Waiting for READY would mean never starting, so the middle state is the
    normal one - and it names what it is proceeding on rather than quietly
    proceeding.

    `scope` is the work being asked about. Without it the answer covers the
    project; with it, only the questions that actually bear on that work can
    block it.

    That distinction is the point. One unanswered question used to make the
    whole project NOT_READY, so "is this single or multiplayer?" stopped the
    menu, the controls and the build pipeline as effectively as it stopped
    the netcode. A question blocks its dependent work and nothing else, and
    `blocked` reports which areas are actually stuck so the rest can proceed.
    """
    from friday.toolsets import memory as M

    store = db or M.store()
    open_questions = list(store.blocking_questions(project))
    assumed = [f"{row['question']} -> {row['assumption']}"
               for row in store.assumptions(project)]
    contradicted = [row["decision"] for row in store.decisions(project)
                    if CONTRADICTED in (row.get("rationale") or "")]

    # Only the questions that bear on the work being asked about can block
    # it; the rest are reported as areas that are stuck, not as blockers.
    if scope:
        relevant = [row for row in open_questions if _touches(row, scope)]
    else:
        relevant = open_questions
    blocking = [row["question"] for row in relevant]
    # What each open question is holding up, by its own account. Reported
    # whether or not it blocks this scope, so "the menu can proceed" comes
    # with "the netcode cannot".
    stuck = tuple(sorted({(row.get("impact") or row["question"])[:80]
                          for row in open_questions}))

    if blocking:
        return Readiness(
            state=NOT_READY, blockers=tuple(blocking),
            assumptions=tuple(assumed), unverified=tuple(contradicted),
            blocked=stuck,
            because=f"{len(blocking)} question(s) change what gets built"
                    + (f" for {scope}" if scope else ""))
    if assumed or contradicted or stuck:
        return Readiness(
            state=READY_WITH_ASSUMPTIONS, assumptions=tuple(assumed),
            unverified=tuple(contradicted), blocked=stuck,
            because=f"nothing blocks {scope or 'this'}; "
                    + (f"proceeding on {len(assumed)} assumption(s)"
                       if assumed else "")
                    + (f"{'; ' if assumed else ''}{len(stuck)} other area(s) "
                       f"waiting on an answer" if stuck else ""))
    if not store.decisions(project):
        return Readiness(
            state=NOT_READY,
            because="nothing has been decided about this yet")
    return Readiness(state=READY, because="everything asked has been answered")


def brief(project: str, *, db=None) -> dict:
    """
    The canonical product summary, built from structured state.

    Not regenerated from the conversation - that is the whole point. It has to
    be the same tomorrow, after a restart, in a session that never saw the
    original idea.
    """
    from friday.toolsets import memory as M

    store = db or M.store()
    row = next((item for item in store.projects()
                if item["name"] == project), None)
    if row is None:
        return {}

    ready = readiness(project, db=store)
    requirements = store.requirements(project)
    statements = [item["statement"] for item in requirements]

    return {
        "project": project,
        "vision": row.get("summary") or "",
        "requirements": requirements,
        "decisions": [item["decision"] for item in store.decisions(project)],
        "assumptions": list(ready.assumptions),
        "blocking_questions": list(ready.blockers),
        "conflicts": conflicts(statements),
        "readiness": ready.state,
        "because": ready.because,
        "counts": {
            "requirements": len(requirements),
            "with_acceptance": sum(1 for item in requirements
                                   if item.get("acceptance")),
            "needs_target": sum(1 for item in requirements
                                if item.get("needs_target")),
        },
    }


# ---------------------------------------------------------------------------
# Requirements
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Claims the boss makes about the world
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Acceptance
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Conflicts
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Readiness
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# The brief
# ---------------------------------------------------------------------------
