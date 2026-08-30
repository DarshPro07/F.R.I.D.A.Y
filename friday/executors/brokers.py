
"""
The two brokers. Deliberately separate, and they must stay separate.

A question broker decides **what is true**. A permission broker decides **what
is allowed**. Collapsing them is how a preference becomes an authorisation:
"he likes it when I just get on with it" is an answer to neither
"which database?" nor "may I push?".

## Question Broker

Claude asks; ADA answers from project evidence if it can, asks the boss once
if it cannot, and persists the answer so it is never asked twice.

What may answer a material question is restricted on purpose, because the
memory has a known weakness - a goal like "running locally by the end of the
month" is stored at confidence 1.0 with no horizon and will still read as
current in December:

    decides         FACT the user stated, and accepted project decisions
    context only    PREFERENCE, PATTERN, and anything undated
    never decides   INFERENCE, and any subject with an open contradiction

An inference answering a database choice would be the user model quietly
making architecture decisions, which is exactly the failure the kind/authority
split was built to prevent.

## Permission Broker

Installed-CLI reality (see cli.py): the `PermissionRequest` hook never fires
in `-p`, so permission cannot be brokered per call. It is brokered *before
launch* instead, by computing --allowedTools / --disallowedTools from the
policy, and read back afterwards from `permission_denials`.

This is stricter than the hook design, not looser. The set is fixed for the
run, it is enforced by the CLI outside the model, and the default for anything
not named is refusal.
"""

from __future__ import annotations

import json

import logging

import re

from dataclasses import dataclass, field

from friday.store import FACT, PREFERENCE, Store

logger = logging.getLogger("friday-agent.executors.brokers")

READ_TOOLS: tuple[str, ...] = ('Read', 'Glob', 'Grep', 'NotebookRead', 'TodoWrite', 'WebFetch', 'WebSearch')

WRITE_TOOLS: tuple[str, ...] = ('Write', 'Edit', 'NotebookEdit')

NEVER: tuple[str, ...] = (
    'Bash(sudo *)',
    'Bash(rm -rf *)',
    'PowerShell(Remove-Item *)',
    'PowerShell(Start-Process *)',
    'PowerShell(Invoke-Expression *)',
    'PowerShell(iex *)',
    'PowerShell(Set-ExecutionPolicy *)',
)

BUILD_COMMANDS: tuple[str, ...] = (
    'git status',
    'git diff *',
    'git log *',
    'git add *',
    'git stash *',
    'pytest *',
    'python -m pytest *',
    'npm test*',
    'npm run lint*',
    'npm run build*',
)


def _for_both_shells(commands: tuple[str, ...]) -> tuple[str, ...]:
    """`pytest *` -> `Bash(pytest *)` and `PowerShell(pytest *)`."""
    return tuple(f"{shell}({command})"
                 for command in commands
                 for shell in ("Bash", "PowerShell"))


@dataclass(frozen=True)
class Profile:
    """What one run may do. Fixed before it starts."""

    name: str
    allowed: tuple[str, ...]
    disallowed: tuple[str, ...] = NEVER

    def as_launch_kwargs(self) -> dict:
        return {"allowed_tools": self.allowed, "disallowed_tools": self.disallowed}

#: read-only: explore, explain, review. Cannot change anything.
EXPLORE = Profile("explore", allowed=READ_TOOLS)

#: implement: read, write, and run the checks that prove the work.
#:
#: Commands are named individually rather than opened up. A broad `Bash(*)`
#: with narrower entries after it does not work the way it reads - a wide rule
#: wins over a specific one - so the only safe form is an explicit list.
BUILD = Profile('build', allowed=READ_TOOLS + WRITE_TOOLS + _for_both_shells(BUILD_COMMANDS))

PROFILES = {p.name: p for p in (EXPLORE, BUILD)}

#: Asked for by name, never inferred from a task description. `git push`,
#: deleting trees, touching services or credentials stay outside every profile:
#: they are the boss's to run, and an executor that wants one has to say so and
#: be told yes.
OUT_OF_SCOPE = ("git push", "git reset --hard", "rmdir", "Remove-Item -Recurse",
                "npm publish", "pip install", "reg add", "sc.exe")


def profile_for(intent: str) -> Profile:
    """
    Pick a profile from what the task is for. Defaults to read-only.

    Anything unrecognised gets the smallest set, because the failure mode of
    guessing wide is a change nobody authorised.
    """
    text = (intent or "").lower()
    if any(word in text for word in
           ("implement", "fix", "write", "refactor", "add", "build", "migrate",
            "update", "change", "create")):
        return BUILD
    return EXPLORE


def denials_from(result: dict) -> list[dict]:
    """What the CLI refused, in its own words."""
    return [
        {"tool": entry.get("tool_name", "?"),
         "input": json.dumps(entry.get("tool_input", {}))[:400]}
        for entry in (result.get("permission_denials") or [])
    ]

# Restored from the .pyc oracle: proven by a LOAD_CONST/STORE_NAME
# pair in the running system's bytecode, present in no source candidate.
ANSWER_FROM_PROJECT = 'ANSWER_FROM_PROJECT'

ANSWER_LOW_RISK_DEFAULT = 'ANSWER_LOW_RISK_DEFAULT'

WAIT_USER = 'WAIT_USER'

CONFLICT = 'CONFLICT'

OUTCOMES = (ANSWER_FROM_PROJECT, ANSWER_LOW_RISK_DEFAULT, WAIT_USER, CONFLICT)

IMPLEMENTATION_DECISION = 'IMPLEMENTATION_DECISION'

USER_DECISION = 'USER_DECISION'

_MUST_ASK = re.compile('\\b(?:delete|drop (?:the )?(?:table|database)|destroy|wipe|overwrite|force[- ]push|rewrite history|reset --hard|publish|deploy|release|push to (?:main|master|production)|go live|charge|payment|invoice|price|billing|subscription|refund|password|secret|api key|token|credential|auth|email (?:the|our|his|her|them)|send (?:an )?email|notify (?:the )?users|licen[cs]e|terms|privacy policy|gdpr|migrat\\w+|schema change|breaking change|backwards[- ]incompat\\w+)\\b', re.IGNORECASE)

# Restored from the .pyc oracle. Each pattern is a LOAD_CONST string
# and each flag a LOAD_ATTR on `re` in the compiled module - primary
# evidence from the running system, not inference.
_LOW_RISK = re.compile('\\b(?:variable|function|file|folder|directory|module|class) name|\\bwhat should i (?:call|name) (?:it|the|this)|\\bnaming convention|\\b(?:tabs or spaces|indentation|formatting|code style|lint)|\\bwhere should (?:i )?(?:put|place) (?:the|this)|\\b(?:order|arrangement) of the|\\bcomment style', re.IGNORECASE)


@dataclass(frozen=True)
class Verdict:
    """What to do about one question, and why that."""

    outcome: str
    answer: str = ""
    #: Where the answer came from - "decision", "fact", "default" - or "".
    source: str = ""
    because: str = ""
    #: The accepted decisions that disagree, when the outcome is CONFLICT.
    conflicting: tuple = ()

    @property
    def answered(self) -> bool:
        return self.outcome in (ANSWER_FROM_PROJECT, ANSWER_LOW_RISK_DEFAULT)

    @property
    def authority(self) -> str:
        """
        Whose decision this is, for the record.

        Empty when nothing was decided. An unanswered question reporting
        IMPLEMENTATION_DECISION reads as though Friday had already chosen,
        which is the opposite of what WAIT_USER means.
        """
        if not self.answered:
            return ""
        return (USER_DECISION if self.outcome == ANSWER_FROM_PROJECT
                else IMPLEMENTATION_DECISION)

    def as_dict(self) -> dict:
        return {"outcome": self.outcome, "answer": self.answer,
                "source": self.source, "because": self.because,
                "authority": self.authority,
                "conflicting": list(self.conflicting)}


def must_ask(question: str) -> bool:
    """
    Whether this has to reach him regardless of what memory says.

    Checked before the evidence, not after. A project decision saying
    "deploy to production" must not authorise a *different* deploy later, and
    the only safe way to guarantee that is to never let this class of
    question be answered from a keyword match.
    """
    return bool(_MUST_ASK.search(question or ""))


def low_risk(question: str) -> bool:
    """Whether being wrong about this is cheap and reversible."""
    return bool(_LOW_RISK.search(question or "")) and not must_ask(question)


@dataclass(frozen=True)
class Answer:
    text: str
    source: str          # decision | fact | user | unknown
    evidence: str = ""

    @property
    def grounded(self) -> bool:
        return self.source in ("decision", "fact", "user")

_STOPWORDS = frozenset({
    "the", "and", "for", "with", "should", "would", "which", "what", "this",
    "that", "are", "you", "use", "using", "want", "need", "have", "does", "can",
    "our", "your", "any", "how", "why", "when", "from", "into", "will",
})


def keywords(question: str) -> list[str]:
    return [w for w in re.split(r"[^a-z0-9]+", (question or "").lower())
            if len(w) > 2 and w not in _STOPWORDS]


@dataclass
class QuestionBroker:
    """
    Answers Claude from project evidence, or asks the boss exactly once.

    `ask_user` is how it reaches him. It returns his answer, or None if he
    cannot be reached - in which case the question goes back unanswered rather
    than being guessed at.
    """

    store: Store
    project: str = ""
    ask_user: object = None          # Callable[[str, list[str]], str | None]
    asked: dict = field(default_factory=dict)   # question -> Answer, this run

    # -- evidence ---------------------------------------------------------

    def from_decisions(self, question: str) -> Answer | None:
        """An accepted project decision outranks everything else."""
        if not self.project:
            return None
        try:
            rows = self.store.decisions(self.project)
        except Exception:                                    # noqa: BLE001
            return None
        terms = set(keywords(question))
        if not terms:
            return None
        # Scored by how much of the decision the question's terms cover, not
        # by raw hits: a long rationale that happens to contain three common
        # words is not an answer, and a two-word decision that matches both
        # of them is. The raw score still gates below, so a single shared
        # word never carries a decision on its own.
        best, best_strength, best_score = None, 0.0, 0
        for row in rows:
            if row.get("superseded"):
                continue
            haystack = f"{row.get('decision', '')} {row.get('rationale', '')}".lower()
            score = sum(1 for term in terms if term in haystack)
            if not score:
                continue
            content = [w for w in re.split(r"[^a-z0-9]+", haystack)
                       if len(w) > 2 and w not in _STOPWORDS]
            strength = score / max(len(set(content)), 1)
            if strength > best_strength:
                best, best_strength, best_score = row, strength, score
        if best is None or (best_score < 2 and best_strength < 0.25):
            return None
        return Answer(
            text=str(best["decision"]),
            source="decision",
            evidence=f"decided for {self.project}: {best.get('rationale', '')}"[:200])

    def from_facts(self, question: str) -> Answer | None:
        """
        A thing he actually stated. FACT only.

        A PREFERENCE may colour how work is done; it may not settle what gets
        built. An INFERENCE may not do either - that is the user model
        deciding architecture on a hunch.
        """
        terms = keywords(question)
        if not terms:
            return None
        seen: dict[str, dict] = {}
        for term in terms:
            try:
                rows = self.store.search_memories(term, limit=8)
            except Exception:
                continue
            for row in rows:
                if row["kind"] != FACT or row.get("superseded"):
                    continue
                seen.setdefault(f"{row['subject']}={row['value']}", row)

        if not seen:
            return None
        if len(seen) > 1:
            # Two stated facts both look relevant. Choosing between them is
            # the thing the contradiction machinery exists to refuse.
            logger.info("question matched %d facts; not deciding", len(seen))
            return None

        row = next(iter(seen.values()))
        return Answer(text=str(row["value"]), source="fact",
                      evidence=f"{row['subject']} (stated: {row['source']})"[:200])

    def context(self, question: str, *, limit: int = 4) -> list[str]:
        """
        Preferences and patterns: offered to Claude as colour, never as a
        decision. Explicitly labelled so it cannot be read as settled.
        """
        out: list[str] = []
        for term in keywords(question)[:4]:
            try:
                rows = self.store.search_memories(term, limit=6)
            except Exception:
                continue
            for row in rows:
                if row["kind"] in (PREFERENCE, "PATTERN") and not row.get("superseded"):
                    line = f"{row['subject']}: {row['value']} [{row['kind'].lower()}]"
                    if line not in out:
                        out.append(line)
        return out[:limit]

    # -- the decision -----------------------------------------------------

    def answer(self, question: str, options: list[str] | None = None) -> Answer:
        key = (question or "").strip().lower()
        if key in self.asked:
            return self.asked[key]

        found = self.from_decisions(question) or self.from_facts(question)
        if found is None and self.ask_user is not None:
            reply = self.ask_user(question, options or [])
            if reply:
                found = Answer(text=str(reply), source="user",
                               evidence="he was asked, once")
                self.persist(question, found)

        if found is None:
            found = Answer(
                text="", source="unknown",
                evidence="nothing in project memory settles this, and the boss "
                         "could not be reached")
        self.asked[key] = found
        return found

    """
    The verdict layer. Everything above answers "what does memory say";
    what follows decides what to do about it, in a fixed order.
    """

    def classify(self, question: str, options: list[str] | None = None) -> Verdict:
        """
        Decide what to do about one question. Never a guess.

        Order matters. The must-ask check runs before the evidence, so a
        keyword match in project memory can never authorise a deploy, a
        deletion or a charge. Then conflicts, because two accepted facts that
        disagree mean the project state is wrong and picking one hides that.
        Then the evidence. Then, only for genuinely cheap choices, a default.
        """
        if must_ask(question):
            return Verdict(
                outcome=WAIT_USER,
                because="irreversible, visible to someone else, or about money - "
                        "he decides these whatever memory says")

        clash = self.conflicting(question)
        if clash:
            return Verdict(
                outcome=CONFLICT, conflicting=clash,
                because="project memory holds two incompatible accepted facts; "
                        "choosing one would make Friday the author of a decision "
                        "nobody made")

        found = self.from_decisions(question) or self.from_facts(question)
        if found is not None and found.grounded:
            return Verdict(outcome=ANSWER_FROM_PROJECT, answer=found.text,
                           source=found.source, because=found.evidence)

        if low_risk(question):
            chosen = (options or [""])[0]
            return Verdict(
                outcome=ANSWER_LOW_RISK_DEFAULT, answer=chosen,
                source="default",
                because="reversible, no user-visible behaviour change, recorded "
                        "as an implementation decision")

        return Verdict(
            outcome=WAIT_USER,
            because="nothing in project memory settles it and it is not a detail "
                    "Friday may decide alone")

    def conflicting(self, question: str) -> tuple:
        """
        Accepted decisions about this question that disagree with each other.

        Only among decisions that actually bear on the question - the whole
        project will always contain decisions that are simply about different
        things, and calling those a conflict would stop every run.
        """
        if not self.project:
            return ()
        try:
            rows = self.store.decisions(self.project)
        except Exception:                                    # noqa: BLE001
            return ()
        terms = set(keywords(question))
        if not terms:
            return ()
        # Relevant means the decision itself shares at least two of the
        # question's terms; the rationale is not consulted here because it
        # is where decisions explain themselves in everybody else's words.
        relevant = [str(row["decision"]) for row in rows
                    if not row.get("superseded")
                    and sum(1 for t in terms
                            if t in str(row.get("decision", "")).lower()) >= 2]
        if len(relevant) < 2:
            return ()
        # The product module already knows what "two facts disagree" means.
        from friday import product as P

        opposed = P.conflicts(relevant)
        return tuple(f"{left} vs {right}" for left, right in opposed)

    def persist(self, question: str, answer: Answer) -> None:
        """
        Record it so the same question is never asked twice.

        Stored as a project decision rather than a user fact: it is a choice
        about this project, and it should die with the project rather than
        follow him into the next one.
        """
        try:
            if self.project:
                self.store.ensure_project(self.project)
                self.store.record_decision(
                    self.project, decision=answer.text,
                    source="the user answered a development question",
                    rationale=f"asked during a development run: {question}"[:400])
            else:
                self.store.remember(
                    subject=f"decision.{re.sub(r'[^a-z0-9]+', '_', question.lower())[:60]}",
                    value=answer.text, kind=FACT,
                    source=f"he answered during a development run: {question}"[:200],
                    scope="goals")
        except Exception:
            logger.exception("could not persist the answer; it will be asked again")


# ---------------------------------------------------------------------------
# Permission Broker
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Question Broker
# ---------------------------------------------------------------------------
