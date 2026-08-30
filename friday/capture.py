
"""
Writing down what the boss said he is building.

`projects_list` says this about the read side:

    "What am I working on?" had no capability behind it at all - the storage
    was there, `store.projects()` was there, and nothing in the registry
    reached either. So the answer came from whatever the model remembered of
    the conversation, which is the failure durable memory exists to prevent.

That was fixed. The write side never was. `record_decision`,
`project_record_decision` and `add_requirement` are called from tests and one
golden script and from nothing on the conversation path, so a project only
ever existed if a test made one.

Measured, tonight: the boss described a combat drone game, Friday researched
the engine choice, delivered a verdict, and stored none of it. Asked "what am
I working on?" twenty minutes later it answered correctly - from the
conversation window. A restart would have lost the lot, and the answer would
have been "halo and lighthouse", from two days ago.

So this is the hook that writes. Two rules keep it from becoming noise:

    Only an intention to build. "I want to build X" is a project. "What is
    the weather" is not, and neither is thinking out loud about one.

    Only a settled decision. A claim that was actually checked, with the
    sources that checked it. An opinion the model formed on the spot is not
    a decision and recording it would put invented history in front of every
    future planning call.

Everything here is best-effort and swallows its own exceptions. Failing to
write a note must never cost the boss his reply.
"""

from __future__ import annotations

import logging

import re

from dataclasses import dataclass

logger = logging.getLogger("friday-agent")

#: An intention to build something, and the thing. Deliberately narrower than
#: `product._BUILDING`: that one is used to *enrich a search* and a false
#: positive costs a slightly odd query, while this one writes to the store
#: and a false positive costs a junk project the boss has to clean up.
_INTENDS_TO_BUILD = re.compile(
    r"\b(?:i(?:'m| am)? (?:want|would like|plan|planning|going|trying|need)"
    r"(?:ing)?\s+to\s+(?:build|make|create|write|start)"
    r"|let'?s (?:build|make|create|start)"
    r"|i(?:'m| am) (?:building|making|creating|writing|starting))"
    r"\s+(?:a|an|the|some|my)?\s*([^.;,!?]{3,80})",
    re.IGNORECASE)

#: Dropped when naming a project. Adjectives of size and quality say nothing
#: about which project this is.
_NOISE = frozenset({
    "a", "an", "the", "some", "my", "our", "new", "small", "simple", "little",
    "quick", "basic", "tiny", "big", "large", "nice", "good", "great",
    "first", "initial", "proper", "real", "actual", "very", "really",
    "where", "which", "that", "who", "what", "with", "for", "from", "into",
    "and", "or", "but", "of", "to", "in", "on", "at", "by", "i", "me", "you",
    "it", "control", "controls", "using", "use", "want", "wants",
})

#: Words that describe the *kind* of thing being built. Kept when present,
#: because "drone game" is a better name than "drone".
_KINDS = frozenset({
    "game", "app", "application", "site", "website", "tool", "script",
    "service", "api", "bot", "library", "extension", "plugin", "dashboard",
    "tracker", "editor", "engine", "server", "client", "cli",
})


def project_named_in(text: str) -> str:
    """
    A short project name from what the boss said he is building, or "".

    "I want to build a small desktop game where I control a combat drone"
    becomes "combat drone game": the kind of thing, plus the words that make
    it this one rather than another.

    Deliberately not asking a model. A name is a label the boss will read
    back and can rename; spending a round trip and a sentence of latency on
    one would be a poor trade, and a wrong guess is cheap to correct.
    """
    match = _INTENDS_TO_BUILD.search(text or "")
    if not match:
        return ""

    phrase = match.group(1).lower()
    words = [w for w in re.findall(r"[a-z0-9]+", phrase) if w not in _NOISE]
    if not words:
        return ""

    kinds = [w for w in words if w in _KINDS]
    rest = [w for w in words if w not in _KINDS]
    # The last two distinctive words are the ones that identify it: a phrase
    # names the thing at the end, after describing it.
    name = " ".join(rest[-2:] + kinds[:1]) if kinds else " ".join(rest[-2:])
    return name.strip()[:60]


def remember_the_project(text: str, *, db=None) -> str:
    """
    Create the project, if the boss just said he is starting one.

    Returns the name it recorded, or "". Idempotent by name: saying it twice
    touches the same project rather than making a second one, because
    `ensure_project` is an upsert and the boss repeats himself.
    """
    name = project_named_in(text)
    if not name:
        return ""
    try:
        store = db or _store()
        store.ensure_project(name)
        logger.info("capture.project name=%r", name)
        return name
    except Exception:                                       # noqa: BLE001
        logger.exception("could not record the project; the turn goes on")
        return ""


def remember_the_decision(project: str, claim, *, db=None) -> bool:
    """
    Record a decision that was actually checked.

    `claim` is a verified `product.Claim`. The sources go in the rationale,
    so a decision that later looks wrong can be traced to what it was made
    from - a decision with no record of what produced it is an opinion, which
    is the same rule `product.verify` follows.

    An unverified claim is not recorded. Putting the model's on-the-spot
    opinion into durable memory would mean every future planning call reading
    invented history as fact.
    """
    if not project or claim is None or not getattr(claim, "sources", ()):
        return False
    try:
        from friday import product as P

        store = db or _store()
        store.record_decision(
            project=project,
            decision=P.as_question(claim.claim) or claim.claim,
            source="checked against sources during a conversation",
            rationale="; ".join(claim.sources[:3])[:500])
        logger.info("capture.decision project=%r", project)
        return True
    except Exception:                                       # noqa: BLE001
        logger.exception("could not record the decision; the turn goes on")
        return False

_REQUIREMENT = re.compile('\\b(?:it (?:should|must|needs? to|has to|ought to)|i (?:want|need) it to|the (?:first version|mvp|app|game|site|tool) (?:should|must|needs? to)|(?:should|must) (?:be able to|support|handle|work)|make (?:it|sure it))\\s+([^.;!?]{4,160})', re.IGNORECASE)

# Restored from the .pyc oracle. Each pattern is a LOAD_CONST string
# and each flag a LOAD_ATTR on `re` in the compiled module - primary
# evidence from the running system, not inference.
_REMOVES = re.compile('\\b(?:actually,?\\s*)?(?:remove|drop|cut|forget|scrap|lose|no)\\s+(?:the\\s+)?([a-z0-9][^.;!?]{2,60}?)(?:\\s+(?:from|for|in)\\s+(?:the\\s+)?(?:first version|mvp|v1|now))?\\s*[.;!?]?$', re.IGNORECASE)

_REPLACES = (
    re.compile(
        '\\b(?:change|switch|move|set)\\s+(?:the\\s+)?([^.;!?]{2,60}?)\\s+from\\s+[^.;!?]{1,60}?\\s+to\\s+([^.;!?]{1,60})',
        re.IGNORECASE,
    ),
    re.compile(
        '\\bmake it\\s+([^.;!?]{1,60}?)\\s+instead of\\s+([^.;!?]{1,60})',
        re.IGNORECASE,
    ),
    re.compile(
        '\\b(?:change|switch|move|set)\\s+(?:the\\s+)?([^.;!?]{2,60}?)\\s+to\\s+([^.;!?]{1,60})',
        re.IGNORECASE,
    ),
)

# Restored from the .pyc oracle: proven by a LOAD_CONST/STORE_NAME
# pair in the running system's bytecode, present in no source candidate.
REMOVE = 'REMOVE'

REPLACE = 'REPLACE'


@dataclass(frozen=True)
class Change:
    """A requirement change he asked for, and what it touches."""

    kind: str
    #: What the change is about, tidied so it can be matched against the
    #: recorded requirements and decisions. Never the whole sentence: "remove
    #: multiplayer from the lighthouse game" is about multiplayer.
    subject: str
    replacement: str = ""
    said: str = ""

    def describe(self) -> str:
        if self.kind == REPLACE:
            return f"{self.subject} -> {self.replacement}"
        return f"remove {self.subject}"


def requirements_in(text: str) -> list[str]:
    """
    Statements about what the thing must do.

    Deliberately conservative. A requirement shapes what gets built and is
    checked at the end, so a false positive is a spec entry nobody agreed to
    - much worse than a missed one, which he will simply say again.
    """
    found = []
    for match in _REQUIREMENT.finditer(text or ""):
        statement = " ".join(match.group(0).split()).strip(" ,.;:")
        if len(statement.split()) >= 3 and statement not in found:
            found.append(statement[:200])
    return found


def change_in(text: str) -> Change | None:
    """
    A requirement change, or None.

    Only a *change*, never a new requirement: "remove multiplayer" is a
    change and "it should support multiplayer" is not, and conflating them is
    how a spec grows a requirement that says to delete something.
    """
    said = (text or "").strip()

    for index, pattern in enumerate(_REPLACES):
        match = pattern.search(said)
        if not match:
            continue
        first, second = _tidy(match.group(1)), _tidy(match.group(2))
        # "make it X instead of Y" names the replacement first; the other
        # two patterns name the subject first.
        subject, replacement = (second, first) if index == 1 else (first, second)
        if subject:
            return Change(kind=REPLACE, subject=subject,
                          replacement=replacement, said=said)

    match = _REMOVES.search(said)
    if match:
        subject = _tidy(match.group(1))
        # A subject that is a whole clause is a sentence being misread.
        if subject and len(subject.split()) <= 6 and subject not in _NOT_A_SUBJECT:
            return Change(kind=REMOVE, subject=subject, said=said)
    return None

_NOT_A_SUBJECT = frozenset(
    {
        'chance',
        'doubt',
        'idea',
        'it',
        'less',
        'more',
        'need',
        'one',
        'problem',
        'rush',
        'thanks',
        'that',
        'this',
        'way',
        'worries',
    },
)


def _tidy(phrase: str) -> str:
    return " ".join((phrase or "").split()).strip(" ,.;:-").lower()[:60]


def remember_the_requirements(project: str, text: str, *, db=None) -> list[int]:
    """Record what he said the thing must do. Returns the new row ids."""
    if not project:
        return []
    statements = requirements_in(text)
    if not statements:
        return []
    from friday import product as P

    ids = []
    try:
        store = db or _store()
        existing = {row["statement"].lower()
                    for row in store.requirements(project)}
        for statement in statements:
            if statement.lower() in existing:
                continue
            criteria, needs_target = P.acceptance_for(statement)
            ids.append(store.add_requirement(
                project, statement, acceptance=criteria,
                needs_target=needs_target, source="said in conversation"))
        if ids:
            logger.info("capture.requirements project=%r added=%d",
                        project, len(ids))
    except Exception:                                        # noqa: BLE001
        logger.exception("could not record the requirements; the turn goes on")
    return ids


def apply_change(project: str, change: Change, *, db=None) -> dict:
    """
    Carry out a requirement change, and report everything it touched.

    Superseded, never deleted. "Why did we remove multiplayer?" needs the old
    row and the reason, and a DELETE answers it with silence - which is why
    `supersede_requirement` already takes a `why`.

    What is *not* touched matters as much: a change to one requirement must
    leave the other decisions alone. So this reports both, and the caller can
    say "multiplayer is out; the engine choice and the offline requirement
    stand" instead of leaving him to wonder.
    """
    report = {"change": change.describe(), "superseded": [], "added": [],
              "dependent_decisions": [], "untouched_decisions": []}
    if not project or change is None:
        return report

    try:
        store = db or _store()
        words = [w for w in re.findall(r"[a-z0-9]+", change.subject)
                 if len(w) > 2]
        if not words:
            return report

        for row in store.requirements(project):
            statement = (row["statement"] or "").lower()
            if not any(word in statement for word in words):
                continue
            store.supersede_requirement(
                row["id"], why=f"he said: {change.said[:160]}")
            report["superseded"].append(row["statement"])

        if change.kind == REPLACE and change.replacement:
            from friday import product as P

            statement = f"it should {change.subject} {change.replacement}"
            criteria, needs_target = P.acceptance_for(statement)
            report["added"].append(statement)
            store.add_requirement(
                project, statement, acceptance=criteria,
                needs_target=needs_target,
                source=f"changed from: {change.subject}")

        # Decisions are not superseded by a requirement change - a decision
        # about the engine survives multiplayer being cut - but the ones that
        # mention the subject are reported, so he can say whether they still
        # stand.
        for row in store.decisions(project):
            decision = (row["decision"] or "").lower()
            bucket = ("dependent_decisions"
                      if any(word in decision for word in words)
                      else "untouched_decisions")
            report[bucket].append(row["decision"])

        logger.info("capture.changed project=%r %s superseded=%d dependent=%d",
                    project, change.describe(), len(report["superseded"]),
                    len(report["dependent_decisions"]))
    except Exception:                                        # noqa: BLE001
        logger.exception("could not apply the change; the turn goes on")
    return report


def _store():
    """
    The live store - the one everything else already uses.

    Deferred to `toolsets.memory.store()` rather than opening a path of its
    own. A first version here built `Store(DATA_DIR / "friday.db")`, which is
    a perfectly reasonable-looking guess and the wrong file: the real
    database is `DEFAULT_DB`, `data/ada.sqlite3`, and `ADA_DB` may move it.

    So the writes landed, the log said `capture.project name='combat drone
    game'`, and `projects_list` went on reporting halo and lighthouse from
    two days ago. Two databases, one of them invented, and nothing failing
    loudly enough to notice. Imported late so this module stays cheap.
    """
    from friday.toolsets.memory import store

    return store()
