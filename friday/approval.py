"""
How a person's "yes" reaches a confirmation, and why the model cannot supply one.

`friday/tools/system_control.py` has said the important half of this since
Phase 1A:

    They are deliberately NOT self-approvable: there is no MCP tool that
    grants permission, because the agent could then call it unprompted and
    approve itself.

That was right, and it left a hole: every capability in the process and power
subsystem is CONFIRM, CONFIRM needs a live human yes, and there was no way for
a human to give one. Six destructive capabilities that no amount of correct
implementation could make reachable.

This is the missing half. It lives above the capability boundary - the voice
layer imports it, nothing under `friday/tools/` does - so the arrangement is
structural rather than remembered:

    the model   may ask for approval, and may report what happened
    the person  answers
    this module matches the answer to the one pending question and spends it

The nonce travels through the model, and that is safe, because it is a
correlation handle rather than a credential. `Book.consume` refuses anything
whose state is not APPROVED, and only `Book.approve` sets that. Holding the
nonce lets the model say *which* question is being answered. It does not let
it answer.
"""

from __future__ import annotations

import re

from friday import confirmation as CF

#: Said to a machine that has just asked a yes/no question, this is a yes.
#:
#: Deliberately short and deliberately anchored. "yes" appears inside
#: "yesterday"; "ok" inside "okay hold on"; "sure" inside "surely not". The
#: whole utterance has to be the agreement, not contain it.
AFFIRMATIVES = frozenset({
    "yes", "yeah", "yep", "yup", "y", "ok", "okay", "sure", "confirm",
    "confirmed", "do it", "go ahead", "go for it", "please do", "affirmative",
    "correct", "that's right", "thats right", "approved", "approve",
})

#: And the other answer. Listed rather than inferred from "not affirmative",
#: because silence, a cough and a question are all "not affirmative" and none
#: of them is a refusal.
NEGATIVES = frozenset({
    "no", "nope", "nah", "n", "don't", "dont", "do not", "cancel", "stop",
    "never mind", "nevermind", "forget it", "abort", "negative", "no thanks",
    "no thank you", "leave it", "don't do that", "dont do that",
})

_TIDY = re.compile(r"[^a-z' ]+")


def normalise(said: str) -> str:
    """Lowercase, strip punctuation and filler, collapse spaces."""
    text = _TIDY.sub(" ", (said or "").lower()).strip()
    for filler in ("um", "uh", "er", "well", "please", "friday", "hey"):
        text = re.sub(rf"\b{filler}\b", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def reads_as_yes(said: str) -> bool:
    return normalise(said) in AFFIRMATIVES


def reads_as_no(said: str) -> bool:
    return normalise(said) in NEGATIVES


class Ambiguous(Exception):
    """More than one thing is waiting to be answered."""

    def __init__(self, pending: list[CF.Confirmation]) -> None:
        self.pending = pending
        super().__init__(
            "there is more than one thing waiting: "
            + "; ".join(c.question for c in pending))


def awaiting(book: CF.Book, run_id: str = "") -> list[CF.Confirmation]:
    """What is still waiting for an answer, newest last."""
    live = [c for c in book.pending.values()
            if c.state == CF.PENDING and not c.expired()]
    if run_id:
        live = [c for c in live if c.run_id == run_id]
    return sorted(live, key=lambda c: c.expires_at)


def answer(book: CF.Book, said: str, *, run_id: str = "") -> CF.Verdict:
    """
    Match what the person said to the one thing waiting, and settle it.

    Three ways this deliberately does nothing:

    - **nothing is pending.** "Yes" in ordinary conversation is not an
      approval of anything. It has to be an answer to a question that was
      asked, or it is just a word.
    - **more than one thing is pending.** Raises rather than picking. Two
      destructive questions and one ambiguous yes is exactly the situation
      where guessing is worst, and asking which costs a sentence.
    - **it was not a yes or a no.** Silence, a cough, a question back. None of
      those is agreement, and only the listed words count.
    """
    waiting = awaiting(book, run_id)
    if not waiting:
        return CF.Verdict(False, "nothing is waiting to be answered")
    if len(waiting) > 1:
        raise Ambiguous(waiting)

    confirmation = waiting[0]
    if reads_as_no(said):
        return book.refuse(confirmation.nonce)
    if reads_as_yes(said):
        return book.approve(confirmation.nonce)
    return CF.Verdict(
        False,
        f"{said.strip()!r} is neither a yes nor a no, so nothing was "
        f"approved")
