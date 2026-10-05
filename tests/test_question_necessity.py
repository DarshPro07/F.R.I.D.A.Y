"""
Does Friday ask only what it needs to? (INTENT-002)

`tests/test_requirements.py` shows each gate works on one hand-built example:
a small reversible choice is decided, a question that cannot say why is
dropped, an answered question does not come back. None of that says how often
the boss is asked something that did not need asking, or how often something
that did need asking goes unasked. This measures both.

A labelled set of objectives is run through the real `requirements.understand`
path (the real filter, the real durable decisions, the real answered-question
match) with a counting fake model standing in for the provider. Every
ambiguity in the set carries a label:

    MATERIAL    the answer changes what gets built; it must be asked
    IMMATERIAL  small and reversible; it must be decided, not asked
    ANSWERED    material, but the boss already settled it; it must not return

    unnecessary-ask rate   IMMATERIAL + ANSWERED ambiguities that reached the
                           boss as a question, over all of them
    missed-ask rate        MATERIAL ambiguities that never reached the boss,
                           over all of them

What this does not measure: the model's own materiality judgement. The fake
model is told each label and marks it accordingly, so the numbers are about
what the gates do with a model that follows INSTRUCTIONS. That is why there are
two negative controls, one that asks everything and one that asks nothing: a
benchmark that cannot be made to fail is not measuring anything.

Assumed (revisit if wrong): 10% either way is the ceiling. The fake model is
deterministic, so the calibrated run is expected to sit at 0; the ceiling is
there for a labelled set that grows, or a real model swapped in later.

Found by this benchmark, fixed in `requirements.already_answered`: it pooled the
words of an earlier decision and its rationale. The recorded answer to "Where
should the tool run?" has the rationale "decides how it reaches the pipeline",
and the pool swallowed the unrelated, material "Which CI system does the
pipeline run on?" ("run" from the decision, "pipeline" from the rationale: 2 of
its 4 words, over `_ENOUGH_OVERLAP`). A silent missed ask, 1 of 14. Decision and
rationale are now matched separately; `test_every_material_question_still_reaches_the_boss`
fails on the pooled matcher and passes on this one.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from unittest import mock

import pytest

from friday import requirements as R
from friday.store import Store

MATERIAL, IMMATERIAL, ANSWERED = "MATERIAL", "IMMATERIAL", "ANSWERED"
KINDS = (MATERIAL, IMMATERIAL, ANSWERED)

MIN_OBJECTIVES = 8
MAX_UNNECESSARY_ASK_RATE = 0.10
MAX_MISSED_ASK_RATE = 0.10


class BenchmarkError(RuntimeError):
    """The labelled set or the harness is wrong, so no number can be trusted."""


@dataclass(frozen=True)
class Ambiguity:
    question: str
    why: str
    kind: str
    #: IMMATERIAL: what a model would choose. ANSWERED: what the boss said.
    resolution: str = ""


@dataclass(frozen=True)
class Objective:
    text: str
    ambiguities: tuple[Ambiguity, ...]


def _m(question, why):
    return Ambiguity(question, why, MATERIAL)


def _i(question, why, proposed):
    return Ambiguity(question, why, IMMATERIAL, proposed)


def _a(question, why, answer):
    return Ambiguity(question, why, ANSWERED, answer)


OBJECTIVES = (
    Objective("I want to make a game where you play a lighthouse keeper during a storm", (
        _m("Which platform should the game run on?", "decides the engine and the build"),
        _m("Is it single player or does it need online play?", "online play changes the whole architecture"),
        _i("Should the storm audio be layered?", "nobody will remember this decision", "layered ambient"),
        _i("Which typeface should the dialogue use?", "reversible in a minute", "a clean serif"),
    )),
    Objective("I have an idea for an app that tracks what I read and why I stopped", (
        _m("Should reading history stay on the phone or sync between devices?", "decides whether a server exists"),
        _m("Which sources must be tracked: browser articles, books, or both?", "decides what has to be integrated"),
        _i("Should the empty state show a hint?", "cosmetic", "yes, one line"),
        _a("Who is the audience for the reading app?", "decides whether accounts are needed", "only me, personal use"),
    )),
    Objective("I'm thinking about building a small tool to watch my build pipeline", (
        _m("Which CI system does the pipeline run on?", "decides the API the tool talks to"),
        _m("Should the tool alert you or only display status?", "alerting needs a delivery channel"),
        _i("What colour should a failed build be?", "cosmetic", "red"),
        _i("Should timestamps show in local time?", "a display setting", "local time"),
        _a("Where should the tool run, locally or on a server?", "decides how it reaches the pipeline", "locally on my laptop"),
    )),
    Objective("help me design a system for keeping notes across three machines", (
        _m("Which operating systems are the three machines?", "decides the sync client"),
        _m("Can the notes be stored on a third-party cloud?", "a privacy constraint that rules designs out"),
        _i("Should the note files use the markdown extension?", "a rename away", ".md"),
        _a("How should conflicting edits be resolved?", "decides the merge strategy", "keep both versions"),
    )),
    Objective("I need a script that renames my photo library by date", (
        _m("Should the original files be kept or renamed in place?", "an in-place rename cannot be undone"),
        _i("Should the date in a file name use dashes?", "cosmetic", "yyyy-mm-dd"),
        _i("Should the script print a line per file?", "output noise", "no, a summary"),
    )),
    Objective("we should build a dashboard for the support team's ticket backlog", (
        _m("Which ticketing system holds the backlog?", "decides the data source"),
        _m("Who is allowed to see customer names on the dashboard?", "an access rule that shapes the data"),
        _i("Should the chart default to the last seven days?", "a default that can be changed", "seven days"),
        _a("How often should the dashboard refresh?", "decides load on the source", "every five minutes"),
    )),
    Objective("can we build a voice reminder for my medication schedule", (
        _m("Is the reminder only for you or also for a caregiver?", "decides who is notified"),
        _m("What happens when a dose is missed?", "escalation is a feature, not a setting"),
        _i("Should the reminder voice be male or female?", "a voice setting", "the default voice"),
    )),
    Objective("I want to create a tiny site to show my portfolio", (
        _m("Is the site public or private?", "decides hosting and access"),
        _i("Should the page use a dark theme?", "a stylesheet away", "light theme"),
        _i("Should the footer show the year?", "cosmetic", "yes"),
    )),
)


def validate(objectives) -> None:
    """The set must be unambiguous itself before it can grade anything."""
    texts = [objective.text for objective in objectives]
    if len(set(texts)) != len(texts):
        raise BenchmarkError("two objectives share the same text")
    for objective in objectives:
        keys = [R._key(item.question) for item in objective.ambiguities]
        if "" in keys or len(set(keys)) != len(keys):
            raise BenchmarkError(f"questions are not distinct in {objective.text!r}")
        for item in objective.ambiguities:
            if item.kind not in KINDS:
                raise BenchmarkError(f"unknown label {item.kind!r}")
            if item.kind != MATERIAL and not item.resolution:
                raise BenchmarkError(f"{item.kind} needs a resolution: {item.question!r}")


def calibrated(item: Ambiguity) -> dict:
    """A model that follows INSTRUCTIONS. It re-raises answered questions, as a
    model that has not read the project's decisions would."""
    raw = {"question": item.question, "why": item.why}
    if item.kind == IMMATERIAL:
        return {**raw, "materiality": R.SMALL, "proposed": item.resolution,
                "confidence": R.HIGH, "consequence": R.LOW,
                "validation": "the boss can overrule it"}
    return {**raw, "materiality": R.MATERIAL}


def asks_everything(item: Ambiguity) -> dict:
    """Negative control: every ambiguity is MATERIAL."""
    return {"question": item.question, "why": item.why, "materiality": R.MATERIAL}


def asks_nothing(item: Ambiguity) -> dict:
    """Negative control: every ambiguity is small and decided."""
    return {"question": item.question, "why": item.why, "materiality": R.SMALL,
            "proposed": item.resolution or "whatever is usual"}


class CountingModel:
    """Stands in for the provider and counts what the real path asks of it."""

    def __init__(self, objectives, policy):
        self.objectives = {objective.text: objective for objective in objectives}
        self.policy = policy
        self.calls = 0
        self.raised = 0

    def __call__(self):
        outer = self

        class Models:
            def generate_content(self, *, model, contents, config):
                objective = outer.objectives[contents.rsplit("what they said:\n", 1)[1]]
                questions = [outer.policy(item) for item in objective.ambiguities]
                outer.calls += 1
                outer.raised += len(questions)
                return type("A", (), {"text": json.dumps(
                    {"subject": objective.text[:40], "questions": questions})})()
        return (type("C", (), {"models": Models()})(), "counting-fake")


@dataclass(frozen=True)
class Report:
    objectives: int
    model_calls: int
    raised: int
    asked: int
    decided: int
    needed: int
    not_needed: int
    unnecessary: int
    missed: int
    missed_questions: tuple[str, ...] = ()

    @property
    def dropped(self) -> int:
        """Raised by the model and never reached the boss, decided or not."""
        return self.raised - self.asked - self.decided

    @property
    def unnecessary_ask_rate(self) -> float:
        return self.unnecessary / self.not_needed if self.not_needed else 0.0

    @property
    def missed_ask_rate(self) -> float:
        return self.missed / self.needed if self.needed else 0.0

    def gates(self) -> dict[str, bool]:
        return {
            # No gates failing over an empty or one-sided set proves nothing.
            "enough_cases": (self.objectives >= MIN_OBJECTIVES
                             and self.needed > 0 and self.not_needed > 0),
            "unnecessary_asks": self.unnecessary_ask_rate <= MAX_UNNECESSARY_ASK_RATE,
            "missed_asks": self.missed_ask_rate <= MAX_MISSED_ASK_RATE,
        }


def measure(objectives, policy, *, db, remember: bool = True) -> Report:
    """
    Run every objective through `requirements.understand` and grade the result.

    `remember=False` withholds the boss's earlier answers from the store, which
    is what a system with no durable memory looks like.
    """
    validate(objectives)
    model = CountingModel(objectives, policy)
    asked = decided = needed = not_needed = unnecessary = 0
    missed_questions = []
    with mock.patch.object(R, "_model", model):
        for index, objective in enumerate(objectives):
            project = f"necessity-{index}"
            if remember:
                for item in objective.ambiguities:
                    if item.kind == ANSWERED:
                        R.remember_answer(project, R.Question(item.question, item.why),
                                          item.resolution, db=db)
            found = R.understand(objective.text, project=project, db=db)

            labelled = {R._key(item.question): item for item in objective.ambiguities}
            reached = {question.key for question in found.questions}
            if reached - labelled.keys():
                raise BenchmarkError(f"an unlabelled question reached the boss: {reached - labelled.keys()}")
            asked += len(found.questions)
            decided += len(found.assumptions)
            for key, item in labelled.items():
                if item.kind == MATERIAL:
                    needed += 1
                    if key not in reached:
                        missed_questions.append(item.question)
                else:
                    not_needed += 1
                    unnecessary += key in reached

    if model.calls != len(objectives):
        raise BenchmarkError(f"{model.calls} model calls for {len(objectives)} objectives: "
                             "the objectives did not all go through the real path")
    return Report(objectives=len(objectives), model_calls=model.calls, raised=model.raised,
                  asked=asked, decided=decided, needed=needed, not_needed=not_needed,
                  unnecessary=unnecessary, missed=len(missed_questions),
                  missed_questions=tuple(missed_questions))


def _store(tmp_path, name: str) -> Store:
    return Store(tmp_path / f"{name}.sqlite3")


def _count(kind: str) -> int:
    return sum(item.kind == kind for objective in OBJECTIVES for item in objective.ambiguities)


def test_the_labelled_set_is_big_enough_to_grade_anything():
    validate(OBJECTIVES)
    assert len(OBJECTIVES) >= MIN_OBJECTIVES
    assert all(_count(kind) > 0 for kind in KINDS)


def test_a_model_that_follows_its_instructions_is_never_asked_what_it_need_not_ask(tmp_path):
    report = measure(OBJECTIVES, calibrated, db=_store(tmp_path, "calibrated"))

    assert report.model_calls == len(OBJECTIVES)
    assert report.raised == _count(MATERIAL) + _count(IMMATERIAL) + _count(ANSWERED)
    assert report.decided == _count(IMMATERIAL)
    assert report.unnecessary == 0
    assert all(report.gates().values()), report


def test_every_material_question_still_reaches_the_boss(tmp_path):
    """Red before `already_answered` matched decision and rationale separately:
    the answer to 'Where should the tool run?' (why: 'reaches the pipeline')
    pooled its words with the rationale and swallowed the unrelated, material
    'Which CI system does the pipeline run on?'."""
    report = measure(OBJECTIVES, calibrated, db=_store(tmp_path, "calibrated"))

    assert report.missed_questions == (), report.missed_questions
    assert report.asked == _count(MATERIAL)


def test_a_model_that_asks_everything_fails_on_unnecessary_asks(tmp_path):
    """Negative control. No memory and no judgement: every ambiguity is a question."""
    report = measure(OBJECTIVES, asks_everything, db=_store(tmp_path, "everything"),
                     remember=False)

    assert report.asked == report.raised
    assert report.unnecessary_ask_rate == 1.0
    assert report.missed_ask_rate == 0.0
    assert report.gates()["unnecessary_asks"] is False
    assert not all(report.gates().values())


def test_a_model_that_asks_nothing_fails_on_missed_asks(tmp_path):
    """The other side: silence is not a low unnecessary-ask rate worth having."""
    report = measure(OBJECTIVES, asks_nothing, db=_store(tmp_path, "nothing"))

    assert report.asked == 0
    assert report.unnecessary_ask_rate == 0.0
    assert report.missed_ask_rate == 1.0
    assert report.gates()["missed_asks"] is False
    assert not all(report.gates().values())


def test_the_durable_decisions_are_what_keep_answered_questions_from_returning(tmp_path):
    """The same everything-asking model, with and without the boss's earlier
    answers in the store: the difference is exactly the answered questions."""
    forgetful = measure(OBJECTIVES, asks_everything, db=_store(tmp_path, "a"), remember=False)
    remembering = measure(OBJECTIVES, asks_everything, db=_store(tmp_path, "b"))

    assert forgetful.unnecessary - remembering.unnecessary == _count(ANSWERED)


def test_an_empty_set_cannot_pass(tmp_path):
    report = measure((), calibrated, db=_store(tmp_path, "empty"))

    assert report.gates()["enough_cases"] is False


def test_a_set_with_no_ambiguity_to_ask_about_cannot_pass(tmp_path):
    only_small = tuple(
        Objective(f"I want to build thing number {n}", (_i(f"Which shade number {n}?", "cosmetic", "grey"),))
        for n in range(MIN_OBJECTIVES))

    assert measure(only_small, calibrated, db=_store(tmp_path, "small")).gates()["enough_cases"] is False


@pytest.mark.parametrize("bad", [
    Objective("an idea", (_m("Which engine?", "decides the build"), _m("What engine should we use?", "decides it"))),
    Objective("an idea", (_i("Which font?", "minor", ""),)),
    Objective("an idea", (Ambiguity("Which font?", "minor", "UNSURE", "serif"),)),
])
def test_a_mislabelled_set_is_refused_rather_than_graded(bad, tmp_path):
    with pytest.raises(BenchmarkError):
        measure((bad,), calibrated, db=_store(tmp_path, "bad"))
