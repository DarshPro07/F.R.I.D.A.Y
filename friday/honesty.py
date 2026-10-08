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
    # The consequential verbs (PRD FR-POL-003: payments, refunds, price and
    # budget changes, account changes, permanent deletion, commitments).
    #
    # These were the gap. "I refunded order 100238" was not recognised as a
    # completion claim at all, so it never reached this table and went out
    # unexamined - the single highest-stakes sentence the product can say
    # about its own pilot domain was the one nothing checked. Found by
    # tests/test_browser_evidence_chain.py.
    #
    # An empty tuple is not an oversight here; it is the answer. FRIDAY has
    # no capability that refunds, pays, ships or books anything, so the
    # honest verdict for those claims is "nothing in Friday can back this",
    # which is what an empty tuple produces. When such a capability is
    # built, its tool prefix goes here and the claim becomes checkable
    # rather than merely refused.
    'refunded': (),
    'paid': (),
    'charged': (),
    'transferred': (),
    'ordered': (),
    'shipped': (),
    'delivered': (),
    'booked': (),
    'reserved': (),
    'cancelled': (),
    'repriced': (),
    'deployed': (),
    # FRIDAY does delete files, so this one is checkable rather than refused.
    'deleted': ('files_',),
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

#: A clause whose subject is a negative - "Nothing has changed", "no files were
#: deleted", "..., and none of it was sent" - asserts that nothing was done. It
#: is taken out before the sentence is judged, and the rest is judged as usual:
#: "Hermes completed the task; nothing is running" still claims the completion.
#: 2026-10-08: a refusal ending "Nothing has changed since you asked" was read as
#: a claim and replaced with "I have not actually done that". The auxiliary is
#: required, so "I sent it, nothing else needed" stays a claim.
_NEGATIVE_SUBJECT_RE = re.compile(
    r"(?:^|[,;:–—]|\s-\s)\s*(?:and\s+|so\s+|but\s+)?"
    r"(?:nothing|nobody|no one|none(?:\s+of\s+\w+)?|no\s+\w+)\s+"
    r"(?:has|have|had|was|were|is|are|got|been)\b[^,;:–—]*", re.I)

#: A sentence that opens with an imperative - a plan step or an instruction,
#: "- Identify how FRIDAY is started", "1. **Update** the launcher" - tells, it
#: does not report. 2026-10-09: a plan the owner asked for was replaced with "I
#: have not actually done that" because its step held the word "started". One
#: with a first-person subject still reports: "Update: I deployed the fix."
_IMPERATIVE_RE = re.compile(
    r"^\W*(?:\d+[.)]\s*)?\W*(?:add|build|call|check|confirm|create|define|document|"
    r"ensure|extract|find|handle|identify|implement|install|keep|load|make|move|"
    r"open|parse|pass|print|read|remove|rename|replace|return|review|run|save|set|"
    r"show|start|stop|test|update|use|verify|wire|write)\b", re.I)
_FIRST_PERSON_RE = re.compile(r"\b(?:I|I've|I'd|I'm|we|we've)\b")

#: Completion words that describe rather than report. As an adjective after a
#: determiner - "The saved number for E2E Tester is 555-0199" - and in a dated
#: fact about the world - "pytest was first released in 2004", "On Oct 6, 2026,
#: Anthropic published ..." - they say nothing about what FRIDAY did. 2026-10-09
#: end-to-end test: both replaced correct answers with "I have not done it yet".
#: "Your 2026 report has been sent" holds no date expression and stays a claim.
_CLAIM_VERBS = ("created|opened|sent|changed|deleted|printed|built|deployed|completed|learned|"
                "saved|installed|generated|downloaded|wrote|updated|launched|started|released|published")
_ADJECTIVAL_RE = re.compile(
    r"\b(?:the|a|an|your|my|this|that|these|those|its|their|our)\s+(?:recently\s+|newly\s+)?"
    rf"(?:{_CLAIM_VERBS}|{'|'.join(CLAIM_EVIDENCE)})\s+(?=\w)", re.I)
_MONTH = r"(?:jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\.?"
_DATED_RE = re.compile(
    rf"\b(?:in|since|on|by|from|until|during|circa)\s+(?:(?:early|mid|late)[- ]+)?(?:{_MONTH}\s+(?:\d{{1,2}},?\s+)?)?"
    rf"(?:1[89]|20)\d{{2}}\b|\b{_MONTH}\s+\d{{1,2}},?\s+(?:1[89]|20)\d{{2}}\b", re.I)

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
    rest = _NEGATIVE_SUBJECT_RE.sub(" ", sentence)
    if is_negated(rest):
        return False
    mine = _FIRST_PERSON_RE.search(rest)
    if not mine and (_IMPERATIVE_RE.match(rest) or _DATED_RE.search(rest)):
        return False
    rest = _ADJECTIVAL_RE.sub(" ", rest)
    return any(rx.search(rest) for rx in _COMPLETION_RE)


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


# ---------------------------------------------------------------------------
# target correlation (FR-TRU-001)
# ---------------------------------------------------------------------------
#
# The kind check above asks "did the right sort of thing succeed". It does not
# ask "did it happen to the thing you just named", and the PRD is explicit
# that those are different questions:
#
#     Success evidence for foo.txt cannot justify "I created bar.txt."
#
# Before this, it could. A verified `files_write` on foo.txt satisfied every
# requirement for the sentence "I created bar.txt" - right verb, right tool
# family, real verification - and the claim went out. That is the most
# plausible-sounding false claim this system can make, because every part of
# it is true except the one the user cares about.
#
# The rule is deliberately narrow, because the alternative is worse. It fires
# only when a claim names something CONCRETE - a filename with an extension, a
# path, a URL, or a quoted string - and compares that against the targets the
# succeeded actions actually recorded. A sentence with no concrete target
# ("I processed the catalogue") is left to the kind check, as before. A narrow
# rule that never fires wrongly is worth more than a broad one that cries
# wolf and gets switched off.

#: Something a claim can name that a reader could go and check. Not "the
#: file" or "your catalogue" - those are descriptions, and the kind check
#: owns them.
_CONCRETE_TARGET_RE = (
    re.compile(r"\b[\w.-]+\.(?:txt|md|csv|json|ya?ml|xlsx?|docx?|pdf|png|jpe?g"
               r"|py|js|ts|html?|sql|log|zip)\b", re.I),
    # A drive letter is one letter, so it must not be preceded by another:
    # without the lookbehind, the `s` of `https:/` matches and a URL gets
    # reported as a Windows path.
    re.compile(r"(?:(?<![A-Za-z])[A-Za-z]:[\\/]|\.{1,2}[\\/])[\w .\\/-]{2,}"),
    re.compile(r"https?://[^\s,;)\]]+", re.I),
    re.compile(r"['\"\u2018\u2019]([^'\"\u2018\u2019]{3,64})['\"\u2018\u2019]"),
)


def named_targets(sentence: str) -> list[str]:
    """The concrete things a sentence claims to have acted on."""
    out: list[str] = []
    for rx in _CONCRETE_TARGET_RE:
        for match in rx.finditer(sentence or ""):
            value = (match.group(1) if rx.groups else match.group(0)).strip()
            if value and value not in out:
                out.append(value)
    return out


def touched_targets(result) -> list[str]:
    """Everything a succeeded action can show for what it acted on.

    Artifacts and side effects are the authoritative record - an artifact
    exists because the work happened - and the output and verification
    evidence are included because most tools name their target in one of
    them. All four, because a tool that records its target in only one place
    should not thereby become unauditable.
    """
    out: list[str] = []
    for artifact in getattr(result, "artifacts", ()) or ():
        for value in (artifact.path_or_uri, artifact.title):
            if value:
                out.append(str(value))
    out.extend(str(effect) for effect in
               (getattr(result, "side_effects", ()) or ()) if effect)
    output = getattr(result, "output", None)
    if isinstance(output, dict):
        for key in ("path", "target", "url", "file", "name", "destination",
                    "written", "created", "opened"):
            value = output.get(key)
            if isinstance(value, (str, int, float)) and str(value).strip():
                out.append(str(value))
    verification = getattr(result, "verification", None)
    if verification is not None and getattr(verification, "evidence", ""):
        # Only the target-shaped parts of the evidence sentence. Evidence is
        # prose - "the call returned", "412 rows processed" - and comparing a
        # claimed filename against free text either matches nothing useful
        # or, worse, matches by accident. The same extractor is used on both
        # sides so the comparison is between like and like.
        out.extend(named_targets(str(verification.evidence)))
    return out


def _same_target(named: str, touched: str) -> bool:
    """Whether a named target and a touched one are the same thing.

    Compared on the normalised tail of a path, so an absolute Windows path,
    a relative one and a bare filename all match each other - the user says
    the short name and the tool records the long one. Case-insensitive and
    separator-insensitive, because Windows paths are both.
    """
    def norm(value: str) -> str:
        return str(value).strip().strip("'" + chr(34)).replace(chr(92), "/").lower()

    a, b = norm(named), norm(touched)
    if not a or not b:
        return False
    if a in b or b in a:
        return True
    return a.rsplit("/", 1)[-1] == b.rsplit("/", 1)[-1]


def _target_mismatch(sentence: str, backed) -> str:
    """Why this claim's named target is not one the run actually touched."""
    named = named_targets(sentence)
    if not named:
        return ""
    touched = [value for result in backed for value in touched_targets(result)]
    if not touched:
        # The kind check has already accepted these actions, but not one of
        # them can show WHAT it touched. That is not evidence for a claim
        # about a specific thing, and saying so beats assuming it is fine.
        return (f"the claim names {named[0]!r} but no succeeded action "
                f"recorded what it touched")
    for candidate in named:
        if any(_same_target(candidate, value) for value in touched):
            return ""
    shown = sorted({v for v in touched if len(v) < 120})[:4] or ["nothing nameable"]
    return (f"the claim names {named[0]!r}; what succeeded touched {shown} - "
            f"evidence for one target does not back a claim about another")


def audit(text: str, run: Run | None) -> ClaimAudit:
    """
    Check every completion claim in `text` against `run`.

    Three conditions, each added because the one before it was not enough.
    Something must have succeeded in the run; for the verbs in
    CLAIM_EVIDENCE the thing that succeeded must be of the KIND the verb
    names - a verified `files_read` is real work and real evidence, and it is
    not evidence that a catalogue was processed; and when the sentence names
    a concrete TARGET, one of those actions must have touched it. Success on
    foo.txt is not permission to say bar.txt was created (FR-TRU-001).
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
             if (why := _missing_evidence(claim, backed)
                 or _target_mismatch(claim, backed))]
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
