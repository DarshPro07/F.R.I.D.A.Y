"""
Multi-model deliberation (FAB-009): two or more different models each propose,
each judges the others' proposals with a reason, a disputed idea is revised, and
FRIDAY returns the ideas every model endorses plus the disagreements that are
still open.

The owner, 2026-10-08 (docs/plans/build-harness/00-owner-directive-2026-10-08c.md),
verbatim: "i also want some mode where to models talk toghether and give the
final output too like i said give me the best ideas to add so one will do
anotehr will do both will argu judge each other and then give final verdict
where bo agree .... like this".

    propose -> judge (each model, the others' ideas) -> endorsed by all?
      yes -> agreed (locked)   no -> disputed -> proposer revises -> judge again
    ends CONVERGED when nothing is disputed and at least one idea is agreed;
    ends NOT_CONVERGED at the round or call limit, naming what is still disputed.

A model is a callable `model(kind, question, items)` injected by the caller, so
this module makes no provider call and a test drives it with stand-ins:

    kind "propose"  items = ()                           -> list of idea strings
    kind "judge"    items = idea strings by other models -> list of Judgment, one
                    per idea, each with a non-empty reason
    kind "revise"   items = ((idea, (reason, ...)), ...) -> the replacement ideas
                    (an empty list withdraws the idea)

Agreement is never invented: no endorsed idea, a withdrawn dispute, or an
exhausted round / call budget is reported as NOT_CONVERGED. A model that fails
raises; the caller owns fallback. Two entries that are the same callable, or the
same name in another case, are one model and refused.

Assumptions (revisit if wrong): an idea is endorsed only when every model that
did not propose it endorses it (a proposer endorses its own); two models that
propose the same wording count as one idea with two proposers; ideas are
compared by case- and whitespace-insensitive text. Production wiring through
`model_gateway` (one route per model, distinct provider and model ids) is the
next step and is not here.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Mapping

CONVERGED = "CONVERGED"
NOT_CONVERGED = "NOT_CONVERGED"

MAX_MODELS = 4
MAX_ROUNDS = 5
MAX_IDEAS = 8          # per model, per propose / revise call
DEFAULT_ROUNDS = 3
DEFAULT_CALLS = 40

Model = Callable[[str, str, tuple], Any]


class DeliberationError(ValueError):
    """The deliberation was asked for wrongly, or a model answered malformed."""


@dataclass(frozen=True)
class Judgment:
    idea: str
    endorse: bool
    reason: str


@dataclass(frozen=True)
class Agreed:
    idea: str
    proposers: tuple
    reasons: Mapping[str, str]       # judge -> why it endorses


@dataclass(frozen=True)
class Dispute:
    idea: str
    proposers: tuple
    endorsed_by: tuple               # proposers and judges that endorse it
    rejected_by: Mapping[str, str]   # judge -> why it rejects


@dataclass(frozen=True)
class Deliberation:
    question: str
    status: str
    reason: str
    rounds: int
    calls: int
    models: tuple
    agreed: tuple = ()
    disagreements: tuple = ()

    @property
    def answer(self) -> str:
        lines = []
        if self.status == CONVERGED:
            lines.append("The models converged after %d round(s)." % self.rounds)
        else:
            lines.append("The models did not converge: %s." % self.reason)
        lines.append("Endorsed by every model:" if self.agreed else "Endorsed by every model: none.")
        lines += ["- " + a.idea for a in self.agreed]
        if self.disagreements:
            lines.append("Still disputed:")
            for dis in self.disagreements:
                why = "; ".join("%s: %s" % kv for kv in dis.rejected_by.items())
                lines.append("- %s (rejected - %s)" % (dis.idea, why))
        return "\n".join(lines)

    def to_dict(self) -> dict:
        return {
            "question": self.question, "status": self.status, "reason": self.reason,
            "rounds": self.rounds, "calls": self.calls, "models": list(self.models),
            "agreed": [{"idea": a.idea, "proposers": list(a.proposers),
                        "reasons": dict(a.reasons)} for a in self.agreed],
            "disagreements": [{"idea": x.idea, "proposers": list(x.proposers),
                               "endorsed_by": list(x.endorsed_by),
                               "rejected_by": dict(x.rejected_by)} for x in self.disagreements],
        }


class _OutOfBudget(Exception):
    pass


def _key(idea: str) -> str:
    return " ".join(idea.split()).casefold()


@dataclass
class _Run:
    models: dict
    max_calls: int
    calls: int = 0
    # key -> [text, proposers]: the ideas still being decided this round
    open: dict = field(default_factory=dict)

    def call(self, name: str, kind: str, question: str, items: tuple):
        if self.calls >= self.max_calls:
            raise _OutOfBudget
        self.calls += 1
        return self.models[name](kind, question, items)


def _ideas(raw: Any, who: str) -> list:
    if not isinstance(raw, (list, tuple)) or len(raw) > MAX_IDEAS:
        raise DeliberationError("%s must return a list of at most %d ideas" % (who, MAX_IDEAS))
    out = []
    for item in raw:
        if not isinstance(item, str) or not item.strip():
            raise DeliberationError("%s returned an empty or non-text idea" % who)
        out.append(item.strip())
    return out


def _judgments(raw: Any, items: list, who: str) -> dict:
    by_key = {}
    for j in raw if isinstance(raw, (list, tuple)) else ():
        if (not isinstance(j, Judgment) or not isinstance(j.reason, str)
                or not j.reason.strip() or _key(j.idea) in by_key):
            raise DeliberationError("%s gave a malformed judgment: each needs a reason" % who)
        by_key[_key(j.idea)] = j
    if set(by_key) != {_key(i) for i in items}:
        raise DeliberationError("%s must judge every idea it was shown, once" % who)
    return by_key


def _add(run: _Run, name: str, ideas: list, agreed: dict) -> None:
    for text in ideas:
        k = _key(text)
        if k in agreed:
            continue
        slot = run.open.setdefault(k, [text, []])
        if name not in slot[1]:
            slot[1].append(name)


def deliberate(question: str, models: Mapping[str, Model], *,
               max_rounds: int = DEFAULT_ROUNDS, max_calls: int = DEFAULT_CALLS) -> Deliberation:
    if not isinstance(question, str) or not question.strip():
        raise DeliberationError("a question is required")
    names = list(models)
    if not 2 <= len(names) <= MAX_MODELS:
        raise DeliberationError("deliberation needs 2 to %d different models" % MAX_MODELS)
    if (len({n.casefold() for n in names}) != len(names)
            or len({id(models[n]) for n in names}) != len(names)):
        raise DeliberationError("the models must be different: one model cannot take two seats")
    if not 1 <= max_rounds <= MAX_ROUNDS:
        raise DeliberationError("max_rounds must be 1 to %d" % MAX_ROUNDS)
    if max_calls < 1:
        raise DeliberationError("max_calls must be positive")

    run = _Run(dict(models), max_calls)
    agreed: dict = {}                 # key -> Agreed, in the order agreed
    disputes: list = []               # from the last completed judging
    rounds = 0
    try:
        for n in names:
            _add(run, n, _ideas(run.call(n, "propose", question, ()), n), agreed)
        while True:
            rounds += 1
            verdicts = {k: {} for k in run.open}      # key -> judge -> Judgment
            for judge in names:
                shown = [slot[0] for slot in run.open.values() if judge not in slot[1]]
                if shown:
                    got = _judgments(run.call(judge, "judge", question, tuple(shown)),
                                     shown, judge)
                    for k, j in got.items():
                        verdicts[k][judge] = j
            disputes = []
            for k, (text, proposers) in run.open.items():
                no = {n: v.reason.strip() for n, v in verdicts[k].items() if not v.endorse}
                if no:
                    yes = tuple(n for n in names if n in proposers
                                or (n in verdicts[k] and verdicts[k][n].endorse))
                    disputes.append(Dispute(text, tuple(proposers), yes, no))
                else:
                    agreed[k] = Agreed(text, tuple(proposers),
                                       {n: v.reason.strip() for n, v in verdicts[k].items()})
            if not disputes:
                if agreed:
                    return _done(question, CONVERGED, "every proposed idea was endorsed",
                                 rounds, run, names, agreed, disputes)
                return _done(question, NOT_CONVERGED, "no idea was endorsed by every model",
                             rounds, run, names, agreed, disputes)
            if rounds >= max_rounds:
                return _done(question, NOT_CONVERGED,
                             "the round limit (%d) was reached with %d idea(s) still disputed"
                             % (max_rounds, len(disputes)), rounds, run, names, agreed, disputes)
            run.open = {}
            for n in names:
                mine = tuple((x.idea, tuple(x.rejected_by.values()))
                             for x in disputes if n in x.proposers)
                if mine:
                    _add(run, n, _ideas(run.call(n, "revise", question, mine), n), agreed)
    except _OutOfBudget:
        return _done(question, NOT_CONVERGED,
                     "the call budget (%d) was spent before the models agreed" % max_calls,
                     rounds, run, names, agreed, disputes)


def _done(question, status, reason, rounds, run, names, agreed, disputes) -> Deliberation:
    return Deliberation(question.strip(), status, reason, rounds, run.calls, tuple(names),
                        tuple(agreed.values()), tuple(disputes))
