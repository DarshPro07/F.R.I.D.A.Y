"""
Narration guard: catch completion claims that no ActionResult backs.

`contracts.py` makes an *unverified success* impossible to construct. This
module closes the other half of the hole: the agent generating the sentence
"Boss, the Arc Reactor design is ready" without any tool having run at all.
No contract can prevent a language model from emitting that token sequence,
so it is checked after generation and before it reaches the user.

The rule (§5): a completion claim requires a succeeded ActionResult in the
current Run. Present-tense progress ("I'm building it", "working on that") is
always allowed - that is what the agent should say while a run is open.

Deliberately conservative in both directions:
  - only past/perfect completion forms count as claims, not bare verbs, so
    "I can open Spotify for you" is not a claim;
  - negated and hypothetical sentences are not claims, so "I couldn't open
    Spotify" and "I don't have CAD connected" pass untouched. Those are the
    honest sentences and must never be suppressed.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from friday import runstate
from friday.contracts import Run

#: Past/perfect forms of the verbs §5 lists as requiring `succeeded`.
COMPLETION_PATTERNS = (
    r"\b(created|opened|sent|changed|deleted|printed|built|deployed|completed|learned)\b",
    r"\b(saved|installed|generated|downloaded|wrote|updated|launched|started)\b",
    # state assertions: "Spotify is open", "that's done", "it is now live"
    r"\b(?:is|are|it's|that's)\s+(?:now\s+)?"
    r"(?:ready|done|complete|finished|live|open|running|installed|up)\b",
    r"\ball\s+(?:set|done)\b",
    r"\bhere\s+(?:it|they)\s+(?:is|are)\b",
    # bare acknowledgements: "Done.", "All set.", "Ready."
    r"^\s*(?:done|finished|complete|ready)\b",
)

#: If any of these appear in the sentence, it is not an affirmative claim.
NEGATION_PATTERNS = (
    r"\b(?:not|never|unable|cannot|can't|couldn't|won't|wouldn't|didn't|"
    r"don't|doesn't|haven't|hasn't|isn't|aren't|no longer|failed|failing|"
    r"unsuccessful|without)\b",
    r"\bn't\b",
    # conditionals / offers - "I can create that", "shall I open it", "once I..."
    r"\b(?:can|could|will|would|shall|should|may|might|want me to|"
    r"if you|once|when|before|until|going to|about to|trying to)\b",
    r"\?",
)

#: Claim word -> the tool ids that can back it, as prefixes.
#:
#: "Any succeeded action backs any claim" was too weak, and it cost a false
#: report: asked to process a catalogue, the model read the CSV with
#: files_read - which genuinely succeeded - and said "successfully processed".
#: A verified read is not evidence of processing. Nothing had been processed;
#: no run existed.
#:
#: An EMPTY tuple means no capability in Friday can back that word, so the
#: claim is always unbacked. That is the honest state for "posted" and
#: "uploaded" today, and the entry is what turns adding the capability into a
#: deliberate act rather than a sentence the model is free to invent.
CLAIM_EVIDENCE: dict[str, tuple[str, ...]] = {
    'processed': ('product_',),
    'imported': ('product_', 'files_'),
    'exported': ('product_export', 'files_write', 'workbench_'),
    'scheduled': ('automations_', 'reminders_'),
    'automated': ('automations_',),
    'remembered': ('memory_', 'profile_'),
    'learned': ('memory_', 'profile_'),
    'played': ('music_',),
    'printed': (),
    'posted': (),
    'published': (),
    'uploaded': (),
    'subscribed': (),
    'emailed': (),
    'tweeted': (),
}

_CLAIM_WORD_RE = {
    word: re.compile(rf"\b{word}\b", re.I) for word in CLAIM_EVIDENCE
}

#: Every word above is also a completion claim, so the two lists cannot drift
#: apart: a word with an evidence requirement that is not detected as a claim
#: would be a requirement nothing ever checks.
_COMPLETION_RE = tuple(re.compile(p, re.I) for p in COMPLETION_PATTERNS) + (
    re.compile(rf"\b(?:{'|'.join(CLAIM_EVIDENCE)})\b", re.I),
)
_NEGATION_RE = tuple(re.compile(p, re.I) for p in NEGATION_PATTERNS)

_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+|\n+")


@dataclass(frozen=True)
class ClaimAudit:
    ok: bool
    claims: tuple[str, ...] = ()
    unbacked: tuple[str, ...] = ()
    reason: str = ""
    evidence: tuple[str, ...] = field(default=())

    def __bool__(self) -> bool:
        return self.ok


def sentences(text: str) -> list[str]:
    return [s.strip() for s in _SENTENCE_SPLIT.split(text or "") if s.strip()]


def is_negated(sentence: str) -> bool:
    return any(rx.search(sentence) for rx in _NEGATION_RE)


def is_completion_claim(sentence: str) -> bool:
    """An affirmative, past/perfect assertion that something now exists."""
    if is_negated(sentence):
        return False
    return any(rx.search(sentence) for rx in _COMPLETION_RE)


def find_claims(text: str) -> list[str]:
    return [s for s in sentences(text) if is_completion_claim(s)]


def required_evidence(sentence: str) -> dict[str, tuple[str, ...]]:
    """
    Claim words in this sentence that name what must have actually run.

    A sentence with none of them keeps the old rule - any succeeded action
    backs it - because most claims are about the one thing the turn did.
    """
    return {word: CLAIM_EVIDENCE[word]
            for word, rx in _CLAIM_WORD_RE.items() if rx.search(sentence)}


def _still_going(result) -> bool:
    """
    Did the work this action started actually finish?

    A tool call returning SUCCEEDED means the call succeeded. It does not mean
    the *work* is over: a run can come back RUNNING, or INTERRUPTED by a crash
    halfway. Any tool reporting `execution_state` is answering that second
    question, and a claim of completion needs the second answer, not the
    first. Tools that report nothing are unchanged - absence is not evidence
    of an unfinished run.
    """
    state = (result.output or {}).get("execution_state") if isinstance(
        result.output, dict) else None
    return bool(state) and state not in runstate.TERMINAL


def _missing_evidence(sentence: str, backed) -> str:
    """Why this specific claim is not supported, or '' if it is."""
    for word, prefixes in required_evidence(sentence).items():
        if not prefixes:
            return (f"nothing in Friday can back {word!r} - there is no "
                    f"capability that does that yet, so no action could")
        matching = [result for result in backed
                    if any(result.tool_id.startswith(p) for p in prefixes)]
        if not matching:
            ran = sorted({r.tool_id for r in backed}) or ["nothing"]
            return (f"{word!r} needs one of {list(prefixes)} to have "
                    f"succeeded; what succeeded was {ran}")
        if all(_still_going(result) for result in matching):
            states = sorted({(r.output or {}).get("execution_state", "?")
                             for r in matching})
            return (f"{word!r} needs work that is over; the run is {states} - "
                    f"say what it is doing, not that it is done")
    return ""


def audit(text: str, run: Run | None) -> ClaimAudit:
    """
    Check every completion claim in `text` against `run`.

    Two conditions, and the second was added because the first was not enough.
    Something must have succeeded in the run - and for the verbs in
    CLAIM_EVIDENCE, the thing that succeeded must be of the kind the verb
    names. A verified `files_read` is real work and real evidence, and it is
    not evidence that a catalogue was processed.
    """
    claims = tuple(find_claims(text))
    if not claims:
        return ClaimAudit(ok=True, reason="no completion claims")

    if run is None:
        return ClaimAudit(
            ok=False, claims=claims, unbacked=claims,
            reason="completion claimed with no Run at all",
        )

    backed = [r for r in run.results if r.may_claim_completion]
    if not backed:
        attempted = len(run.results)
        return ClaimAudit(
            ok=False, claims=claims, unbacked=claims,
            reason=(
                f"completion claimed but no action succeeded in {run.run_id} "
                f"({attempted} action(s) recorded, none verified)"
            ),
        )

    # Something succeeded. That is not yet permission to claim *this* -
    # files_read succeeding is not evidence that a catalogue was processed.
    wrong = [(claim, why) for claim in claims
             if (why := _missing_evidence(claim, backed))]
    if wrong:
        return ClaimAudit(
            ok=False, claims=claims,
            unbacked=tuple(claim for claim, _ in wrong),
            reason="; ".join(dict.fromkeys(why for _, why in wrong)),
            evidence=tuple(r.verification.evidence
                           for r in backed if r.verification),
        )

    return ClaimAudit(
        ok=True, claims=claims,
        reason=f"{len(backed)} verified action(s) in {run.run_id}",
        evidence=tuple(r.verification.evidence for r in backed if r.verification),
    )


def safe_alternative(run: Run | None) -> str:
    """
    What the agent should say instead, derived from actual run state.

    Never invents progress: if nothing ran, it says nothing ran.
    """
    if run is None or not run.results:
        return (
            "I haven't actually done that yet - I don't have a working "
            "capability for it connected."
        )
    unverified = run.unverified
    if unverified and not [r for r in run.results if r.may_claim_completion]:
        first = unverified[0]
        if first.status == "running":
            return "I'm still working on that."
        return first.honest_summary()
    return "; ".join(r.honest_summary() for r in run.results)
