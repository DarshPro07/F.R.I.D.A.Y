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
from datetime import datetime, timedelta, timezone

from friday import runstate
from friday.contracts import Correlation, Run

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

#: The two tools that remove a file, and the one table of what removing one is
#: called. Each row is a claim word and the other forms a failure sentence uses
#: for the same act ("I tried to recycle it", "the removal failed").
#: CLAIM_EVIDENCE, _ACT_FORMS, _CLAIM_VERBS and the removal-claim test all read
#: this table, so a verb added here is gated in every one of them at once. A
#: removal said in a word that is not here is a way round the gate. Words with
#: a second everyday sense ('cleared', 'dropped', 'destroyed', 'scrapped') are
#: left out on purpose: the narrow check that never fires wrongly beats the
#: broad one. Removal said in more than one word is in _REMOVAL_PHRASE_RE.
REMOVAL_TOOLS = ('files_delete', 'files_recycle')
_REMOVAL_ACTS: dict[str, tuple[str, ...]] = {
    'deleted': ('delete', 'deleting', 'deletion'),
    'removed': ('remove', 'removing', 'removal'),
    'erased': ('erase', 'erasing'),
    'recycled': ('recycle', 'recycling'),
    'trashed': ('trash', 'trashing'),
    'binned': ('binning',),
    'wiped': ('wipe', 'wiping'),
    'purged': ('purge', 'purging'),
    'discarded': ('discard', 'discarding'),
    'shredded': ('shred', 'shredding'),
    'unlinked': ('unlink', 'unlinking'),
}

#: Removal said without a removal verb. "foo.txt is gone", "I got rid of it" and
#: "it was moved to the Recycle Bin" report the same act as 'deleted'. 'gone'
#: alone is no claim ("I've gone through your inbox"), and neither is a bare
#: "moved" ("I moved it to the archive"): only the state assertion and the move
#: into the bin count. "No longer exists" is one although "no longer" is a
#: negation; the only negations kept out of it are the ones it spells itself.
_REMOVAL_PHRASE_RE = re.compile(
    r"\b(?:"
    r"(?:is|are|it's|that's)\s+(?:now\s+)?gone"
    r"|no\s+longer\s+(?:exists?|there|present)"
    r"|(?:doesn['’]t|does\s+not|don['’]t|do\s+not|isn['’]t|is\s+not|"
    r"aren['’]t|are\s+not)\s+(?:exist|there|present)\s+any\s?(?:more|longer)"
    r"|(?:got|gotten)\s+rid\s+of"
    r"|disposed\s+of"
    r"|(?:threw|thrown)\s+away"
    r"|(?P<verb>moved|sent|put|placed|thrown)\b(?P<what>[^;!?]{0,60}?)\b(?:to|into|in)\s+(?:the\s+)?"
    r"(?:recycle\s+bin|recycling\s+bin|trash(?:\s+(?:can|bin))?|wastebasket)"
    r")\b", re.I)

#: What stops a move phrase's object being one thing that was moved. When the
#: text between the move verb and the bin holds any of these, it is more than
#: an object - a second clause or a second verb - so _is_removal_claim keeps it
#: in view instead of treating the whole span as the removal.
_NOT_ONE_OBJECT_RE = re.compile(
    r"[,:–—]|\b(?:and|or|but|then|also|plus|after|before|while|once|when|until|so|because|having|"
    r"mov(?:e|ed|ing)|sen(?:d|t|ding)|put(?:ting)?|plac(?:e|ed|ing)|throw(?:n|ing)?|threw)\b", re.I)

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
    # FRIDAY does delete files, so these are checkable rather than refused.
    # The two removal tools only: `files_` would let a verified files_read or
    # files_write of foo.txt back "I deleted foo.txt", which is the opposite
    # of what that evidence shows. Every word _REMOVAL_ACTS names needs the
    # same proof, or the one left out would be a way round it.
    **{word: REMOVAL_TOOLS for word in _REMOVAL_ACTS},
}

_CLAIM_WORD_RE = {
    word: re.compile(rf"\b{word}\b", re.I) for word in CLAIM_EVIDENCE
}

#: Every word above is also a completion claim, so the two lists cannot drift
#: apart: a word with an evidence requirement that is not detected as a claim
#: would be a requirement nothing ever checks. The removal phrases are claims
#: for the same reason.
_COMPLETION_RE = tuple(re.compile(p, re.I) for p in COMPLETION_PATTERNS) + (
    re.compile(rf"\b(?:{'|'.join(CLAIM_EVIDENCE)})\b", re.I),
    _REMOVAL_PHRASE_RE,
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
_CLAIM_VERBS = ("created|opened|sent|changed|printed|built|deployed|completed|learned|"
                "saved|installed|generated|downloaded|wrote|updated|launched|started|released|published|"
                + "|".join(_REMOVAL_ACTS))
_ADJECTIVAL_RE = re.compile(
    r"\b(?:the|a|an|your|my|this|that|these|those|its|their|our)\s+(?:recently\s+|newly\s+)?"
    rf"(?:{_CLAIM_VERBS}|{'|'.join(CLAIM_EVIDENCE)})\s+(?=\w)", re.I)
_MONTH = r"(?:jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\.?"
_DATED_RE = re.compile(
    rf"\b(?:in|since|on|by|from|until|during|circa)\s+(?:(?:early|mid|late)[- ]+)?(?:{_MONTH}\s+(?:\d{{1,2}},?\s+)?)?"
    rf"(?:1[89]|20)\d{{2}}\b|\b{_MONTH}\s+\d{{1,2}},?\s+(?:1[89]|20)\d{{2}}\b", re.I)

#: Template text and list labels describe too. A placeholder - "[What was
#: completed since the last update]" - is text for the reader to fill in; a
#: short list item that is a participle and a bare noun - "- installed package"
#: - labels something. 2026-10-09 end-to-end test: status-update guidance and
#: a plan were replaced with "I have not actually done that" on exactly these.
#: An item with an object - "- Created the note", "- Sent it" - still reports.
_PLACEHOLDER_RE = re.compile(r"\[[^\]]*\]|\{\{[^}]*\}\}|<[^<>]*>")
_LABEL_RE = re.compile(
    rf"^\W*(?:\d+[.)]\s*)?\W*(?:{_CLAIM_VERBS})\s+"
    r"(?!(?:the|a|an|your|my|it|them|this|that|these|those|his|her|their|our)\b)"
    r"[a-z]+(?:\s+[a-z]+){0,2}\W*$", re.I)

_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+|\n+")

#: Clause boundaries inside one sentence. "I created foo.txt but the upload
#: failed" makes two claims; read as one sentence, the word "failed" negates
#: the whole of it and the creation goes out unchecked.
_CLAUSE_SPLIT = re.compile(
    r"\s*;\s*|,?\s+(?:but|however|although|whereas)\b,?\s*", re.I)


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


def clauses(text: str) -> list[str]:
    """Each sentence, split where it joins a second claim to the first."""
    return [part.strip() for sentence in sentences(text)
            for part in _CLAUSE_SPLIT.split(sentence) if part.strip()]


def is_negated(sentence: str) -> bool:
    return any(rx.search(sentence) for rx in _NEGATION_RE)


def is_completion_claim(sentence: str) -> bool:
    """An affirmative, past/perfect assertion that something now exists."""
    rest = _NEGATIVE_SUBJECT_RE.sub(" ", _PLACEHOLDER_RE.sub(" ", sentence))
    # "foo.txt no longer exists" is a removal claim, so the negation it spells
    # is not read as a denial; any other negation in the sentence still is.
    if is_negated(_REMOVAL_PHRASE_RE.sub(" ", rest)):
        return False
    mine = _FIRST_PERSON_RE.search(rest)
    if not mine and (_IMPERATIVE_RE.match(rest) or _DATED_RE.search(rest) or _LABEL_RE.match(rest)):
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
    needed = {word: CLAIM_EVIDENCE[word]
              for word, rx in _CLAIM_WORD_RE.items() if rx.search(sentence)}
    for phrase in _REMOVAL_PHRASE_RE.finditer(sentence):
        needed[" ".join(phrase.group(0).lower().split())] = REMOVAL_TOOLS
    return needed


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


def _unbackable(sentence: str) -> str:
    """Why no action at all could back this claim, or '' if one could."""
    for word, prefixes in required_evidence(sentence).items():
        if not prefixes:
            return (f"nothing in Friday can back {word!r} - there is no "
                    f"capability that does that yet, so no action could")
    return ""


def _missing_evidence(sentence: str, backed) -> str:
    """Why this specific claim is not supported, or '' if it is."""
    impossible = _unbackable(sentence)
    if impossible:
        return impossible
    for word, prefixes in required_evidence(sentence).items():
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
    should not thereby become unauditable. A failed attempt usually names its
    target only in its error, so that is read too.

    A target recorded in the action's Correlation overrides all of them: it is
    the one thing the action was about, and a filename its evidence mentions
    in passing ("copied bar.txt to foo.txt") is not a second target.
    """
    correlation = getattr(result, "correlation", None)
    if correlation is not None and correlation.target:
        return [correlation.target]
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
    error = getattr(result, "error", None)
    if error:
        out.extend(named_targets(str(error)))
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


# ---------------------------------------------------------------------------
# per-type proof shapes (ACT-001)
# ---------------------------------------------------------------------------
#
# "Verified" is not one thing. A file write is proven by reading it back, a
# code change by a diff AND a test run, a web action by the page's state
# afterwards, an email by the provider's message id, a simulation by the
# artifact it produced. Each shape lists groups of patterns that must ALL be
# present in the verification text (method + evidence); a shape may also need
# a recorded artifact.

@dataclass(frozen=True)
class ProofShape:
    name: str
    prefixes: tuple[str, ...]
    #: Each entry: what the proof is called, and patterns any one of which
    #: satisfies it. Every entry must be satisfied.
    needs: tuple[tuple[str, tuple[str, ...]], ...] = ()
    needs_artifact: bool = False


#: Proof is a `key=value` token with a real value, never a bare word: prose
#: such as "no diff, tests not run" or "12 bytes" names the words without
#: proving anything. Counts must be non-zero; a hash must be hex; a value of
#: none, null, false, unknown or zero is the absence of proof, not proof.
_POS = r"(?:0*[1-9]\d*)"
_VALUE = r"(?!(?:0+|none|null|false|no|nil|unknown|n/a)(?![^\s,;]))[^\s,;]+"
PROOF_SHAPES: tuple[ProofShape, ...] = (
    # A removal has no content to hash: it is proven by the path being gone.
    ProofShape("removal", ("files_delete", "files_recycle"),
               needs=(("post_state=absent", (r"\bpost_state=absent\b",)),)),
    ProofShape("file", ("files_",),
               needs=(("sha256=<hash> or readback=ok",
                       (r"\bsha256[=:][0-9a-f]{16,}\b", r"\breadback=(?:ok|match)\b")),)),
    ProofShape("code", ("claude_code", "hermes_", "executor_", "code_",
                          "executor.claude_code", "executor.hermes"),
               needs=(("diff_files=<n>", (rf"\bdiff_files={_POS}\b",)),
                      ("tests_passed=<n>", (rf"\btests_passed={_POS}\b",)))),
    ProofShape("web", ("browser_",),
               needs=(("post_state=<state> or readback=ok",
                       (rf"\bpost_state={_VALUE}", r"\breadback=(?:ok|match)\b")),)),
    ProofShape("email", ("email_", "gmail_", "mail_"),
               needs=(("message_id=<id>", (rf"\bmessage_id=(?=[^\s,;]{{3,}}){_VALUE}",)),)),
    ProofShape("simulation", ("sim_", "simulation_", "cad_", "cad."), needs_artifact=True),
)

#: Every action without a typed shape above: at least one structured
#: observation with a real value - `pid=1234`, `rows=412`, `status=200`, the
#: form Verification's own docstring asks for. "Launched" or "the call
#: returned" describes; it does not observe. A key inside a URL's query
#: string is not an observation, so the key must start a word of its own.
OBSERVATION = ProofShape(
    "observed", ("",),
    needs=(("an observed key=value", (rf"(?:^|\s)[a-z][a-z0-9_]*={_VALUE}",)),))


def proof_shape_for(tool_id: str) -> ProofShape:
    """The proof this tool's verification must show. Every tool has one."""
    return next((s for s in PROOF_SHAPES
                 if any(tool_id.startswith(p) for p in s.prefixes)), OBSERVATION)


def proof_gap(result) -> str:
    """What the action's verification lacks for its type, or '' if complete."""
    shape = proof_shape_for(result.tool_id)
    v = result.verification
    text = f"{getattr(v, 'method', '')} {getattr(v, 'evidence', '')}".lower()
    text = re.sub(r"\b(?:no|not|never|without|zero)\s+\w+\s*", " ", text)
    for label, patterns in shape.needs:
        if not any(re.search(p, text) for p in patterns):
            return (f"{result.tool_id} is a {shape.name} action; its "
                    f"verification does not show {label}")
    if shape.needs_artifact and not result.artifacts:
        return (f"{result.tool_id} is a {shape.name} action; it recorded "
                f"no artifact")
    return ""


# ---------------------------------------------------------------------------
# correlation (ACT-001)
# ---------------------------------------------------------------------------

#: How far past this machine's clock evidence may be dated before it reads as
#: invented rather than skewed.
_CLOCK_SKEW = timedelta(minutes=1)


def _as_time(stamp: str) -> datetime | None:
    """An ISO timestamp as an aware UTC time, or None if it is not one."""
    try:
        parsed = datetime.fromisoformat(stamp)
    except (TypeError, ValueError):
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _timestamp_gap(result, run: Run) -> str:
    """Why this evidence is not dated inside `run`, or '' if it is.

    Compared as times, never as strings: '10:00+05:30' is before '06:00Z'.
    Every stamp the evidence carries is checked, and evidence that carries
    none, or one that is not a time, is refused rather than waved through.
    """
    corr = getattr(result, "correlation", None)
    stamps = [s for s in (getattr(corr, "timestamp", ""), result.started_at,
                          result.completed_at) if s]
    if not stamps:
        return f"{result.tool_id} evidence carries no timestamp"
    began = _as_time(run.created_at)
    if began is None:
        return (f"{run.run_id} has no readable start time, so no evidence can "
                f"be placed inside it")
    latest = datetime.now(timezone.utc) + _CLOCK_SKEW
    for stamp in stamps:
        when = _as_time(stamp)
        if when is None:
            return f"{result.tool_id} evidence is dated {stamp!r}, which is not a time"
        if when < began:
            return (f"{result.tool_id} evidence is dated {stamp}, before "
                    f"{run.run_id} began")
        if when > latest:
            return f"{result.tool_id} evidence is dated {stamp}, in the future"
    return ""


def _same_correlated_target(want: str, have: str) -> bool:
    """A full path matches only itself; a bare name matches a path's tail."""
    def norm(value: str) -> str:
        return str(value).strip().replace(chr(92), "/").rstrip("/").lower()

    a, b = norm(want), norm(have)
    if not a or not b:
        return False
    if "/" in a and "/" in b:
        return a == b
    return a.rsplit("/", 1)[-1] == b.rsplit("/", 1)[-1]


def correlation_gap(result, expect: dict | None = None,
                    run: Run | None = None) -> str:
    """Why this evidence is not about the action expected, or '' if it is.

    Always, given the run: the evidence belongs to it and is dated inside
    it. Then every key the run declared in its Correlation, and every key in
    `expect` (workspace, objective, version, action, target, executor,
    operation), must be recorded on the evidence with the same value. The two
    are checked separately, so a caller cannot widen what the run declared.
    Evidence that recorded no correlation cannot match an expectation.
    """
    if run is not None:
        if result.run_id != run.run_id:
            return f"{result.tool_id} belongs to {result.run_id}, not {run.run_id}"
        gap = _timestamp_gap(result, run)
        if gap:
            return gap
    declared = getattr(run, "correlation", None)
    pairs = [(key, getattr(declared, key)) for key in Correlation.KEYS
             if declared is not None and getattr(declared, key)]
    pairs += list((expect or {}).items())
    if not pairs:
        return ""
    corr = getattr(result, "correlation", None)
    if corr is None:
        return f"{result.tool_id} recorded no correlation key"
    for key, want in pairs:
        if key not in Correlation.KEYS:
            raise ValueError(f"unknown correlation key {key!r}")
        have = getattr(corr, key)
        same = (_same_correlated_target(str(want), str(have)) if key == "target"
                else bool(have) and str(have) == str(want))
        if not same:
            return (f"{result.tool_id} was recorded for {key}={have!r}, "
                    f"not {want!r}")
    return ""


# ---------------------------------------------------------------------------
# failure claims (ACT-001)
# ---------------------------------------------------------------------------
#
# "The upload failed" is a claim too: that an upload was tried, and that it
# went wrong. It needs an attempt on record that did go wrong, of the kind
# the sentence names, on the target it names - exactly as a success claim
# does. A verified email is not evidence that an upload failed.

_FAILURE_CLAIM_RE = re.compile(
    r"\b(?:failed|tried|attempted|errored|crashed|timed out)\b", re.I)

#: An attempt that was made and went wrong. SUCCEEDED and OBSERVED are not
#: here, and neither are RUNNING and INITIATED, which have not gone anywhere
#: yet.
_WENT_WRONG = ("failed", "partial", "cancelled", "not_carried_out",
               "not_permitted", "unsupported", "not_configured")

#: The other forms a failure sentence uses for the act each CLAIM_EVIDENCE
#: word names: "the upload failed", "refunding it timed out", "I tried to
#: delete it". Keyed by every CLAIM_EVIDENCE word - a missing one is a
#: KeyError at import - so an act gains a kind check for its failures the
#: moment it gains one for its successes. Nouns that also name something
#: else ("process", "import", "book") are left out: the narrow check that
#: never fires wrongly beats the broad one.
_ACT_FORMS: dict[str, tuple[str, ...]] = {
    'processed': ('processing',),
    'imported': ('importing',),
    'exported': ('exporting',),
    'scheduled': ('schedule', 'scheduling'),
    'automated': ('automate', 'automating', 'automation'),
    'remembered': ('remember', 'remembering'),
    'learned': ('learn', 'learning'),
    'played': ('play', 'playing', 'playback'),
    'printed': ('print', 'printing'),
    'posted': ('post', 'posting'),
    'published': ('publish', 'publishing'),
    'uploaded': ('upload', 'uploading'),
    'subscribed': ('subscribe', 'subscribing', 'subscription'),
    'emailed': ('emailing',),
    'tweeted': ('tweet', 'tweeting'),
    'refunded': ('refund', 'refunding'),
    'paid': ('pay', 'paying', 'payment'),
    'charged': ('charge',),
    'transferred': ('transfer', 'transferring'),
    'ordered': (r'(?<!in )order', 'ordering'),
    'shipped': ('ship', 'shipping', 'shipment'),
    'delivered': ('deliver', 'delivering', 'delivery'),
    'booked': ('booking',),
    'reserved': ('reserve', 'reserving', 'reservation'),
    'cancelled': ('cancel', 'cancelling', 'canceling', 'cancellation'),
    'repriced': ('reprice', 'repricing'),
    'deployed': ('deploy', 'deploying', 'deployment'),
    **_REMOVAL_ACTS,
}
_ACT_RE = {word: re.compile(rf"\b(?:{'|'.join((word,) + _ACT_FORMS[word])})\b", re.I)
           for word in CLAIM_EVIDENCE}


def is_failure_claim(clause: str) -> bool:
    """An assertion that an attempt was made and went wrong."""
    return bool(_FAILURE_CLAIM_RE.search(clause)) and not re.search(
        r"\b(?:if|when|whether|might|may|could)\b", clause, re.I)


def failure_claims(text: str) -> list[str]:
    """Clauses asserting that an attempt was made and went wrong."""
    return [part for part in clauses(text) if is_failure_claim(part)]


def _failure_mismatch(claim: str, went_wrong) -> str:
    """Why no attempt on record is the one this failure claim describes."""
    if not went_wrong:
        return ("a failure or attempt was claimed but no attempt that went "
                "wrong is recorded in this run")
    candidates = list(went_wrong)
    for word, rx in _ACT_RE.items():
        if not rx.search(claim):
            continue
        prefixes = CLAIM_EVIDENCE[word]
        if not prefixes:
            return (f"nothing in Friday can do what {word!r} names, so no "
                    f"attempt at it can have been made")
        candidates = [r for r in candidates if r.tool_id.startswith(prefixes)]
        if not candidates:
            tried = sorted({r.tool_id for r in went_wrong})
            return (f"a failure of {word!r} needs an attempt by one of "
                    f"{list(prefixes)}; what went wrong was {tried}")
    named = named_targets(claim)
    if named:
        touched = [value for r in candidates for value in touched_targets(r)]
        if not any(_same_target(n, t) for n in named for t in touched):
            return (f"the failure claimed names {named[0]!r}; no attempt that "
                    f"went wrong was about it")
    return ""


#: Every claim verb except the removal verbs. The claim-to-evidence link runs
#: both ways: the kind check above stops a read from backing "I deleted
#: foo.txt", and this stops the removal's own proof - the path being absent -
#: from backing any other verb. Without it, post_state=absent became proof of
#: creation, because 'created', 'changed' and 'saved' have no CLAIM_EVIDENCE
#: entry and so get no kind check at all.
_NON_REMOVAL_VERB_RE = re.compile(
    rf"\b(?!(?:{'|'.join(_REMOVAL_ACTS)})\b)(?:{_CLAIM_VERBS}|{'|'.join(CLAIM_EVIDENCE)})\b",
    re.I)


#: The only completion claims a removal is evidence for: the removal verbs, the
#: removal phrases (_REMOVAL_PHRASE_RE) and a bare 'done'.
_REMOVAL_CLAIMS = frozenset({*_REMOVAL_ACTS, "done", "all done"})


def _own_words(m: re.Match) -> str:
    """What of a removal phrase is not part of the removal claim itself."""
    what = m.group("what")
    if what is None:
        return " "
    if _NOT_ONE_OBJECT_RE.search(what):
        return f" {m.group('verb')} {what} "
    return f" {what} "


def _is_removal_claim(sentence: str) -> bool:
    """True when every completion claim in the sentence is a removal claim.
    A state assertion ('foo.txt is ready', 'here it is') is not one, so it
    cannot ride on a removal even when it names the removed file."""
    # A removal phrase may hold a verb of its own - "sent foo.txt to the
    # Recycle Bin" - so it is read as one claim and taken out before the rest
    # of the sentence is held to the removal verbs. Only its own words go: when
    # the span between the move verb and the bin is more than one object ("sent
    # the invoice to Bob and moved foo.txt to the bin") the verb and the span
    # stay in view, so a claim verb hiding inside them is still held to account.
    phrases = [" ".join(m.group(0).lower().split())
               for m in _REMOVAL_PHRASE_RE.finditer(sentence)]
    rest = _REMOVAL_PHRASE_RE.sub(_own_words, sentence)
    claimed = phrases + [" ".join(m.group(0).lower().split())
                         for rx in _COMPLETION_RE for m in rx.finditer(rest)]
    return (bool(claimed)
            and all(c in _REMOVAL_CLAIMS or c in phrases for c in claimed)
            and not _NON_REMOVAL_VERB_RE.search(rest))


def _usable(sentence: str, backed) -> list:
    """A removal proves only that a path is gone: it backs 'deleted' and a
    bare 'done', never 'created', 'changed' or a state assertion such as
    'foo.txt is ready' - foo.txt being gone contradicts all of them."""
    if _is_removal_claim(sentence):
        return list(backed)
    return [r for r in backed if proof_shape_for(r.tool_id).name != "removal"]


def _completion_mismatch(claims, run: Run, in_scope, strict_proof: bool,
                         out_of_scope: dict) -> tuple[list, list]:
    """(claim, why) for each unbacked completion claim, and what backs the rest."""
    # A verb no capability can do is refused for that reason first: no proof
    # from some other action, however good, could change the answer.
    wrong = [(claim, why) for claim in claims if (why := _unbackable(claim))]
    claims = [claim for claim in claims if not _unbackable(claim)]
    if not claims:
        return wrong, []
    succeeded = [r for r in run.results if r.may_claim_completion]
    backed = [r for r in in_scope if r.may_claim_completion]
    if succeeded and not backed:
        why = "; ".join(dict.fromkeys(out_of_scope[id(r)] for r in succeeded))
        return wrong + [(claim, why) for claim in claims], []
    if strict_proof:
        # Only actions whose verification matches their type's proof shape
        # may back a claim; the rest are kept out as if unverified.
        gaps = [proof_gap(r) for r in backed]
        if any(gaps):
            backed = [r for r, g in zip(backed, gaps) if not g]
            if not backed:
                why = "; ".join(dict.fromkeys(g for g in gaps if g))
                return wrong + [(claim, why) for claim in claims], []
    if not backed:
        why = (f"completion claimed but no action succeeded in {run.run_id} "
               f"({len(run.results)} action(s) recorded, none verified)")
        return wrong + [(claim, why) for claim in claims], []
    # Something succeeded. That is not yet permission to claim *this* -
    # files_read succeeding is not evidence that a catalogue was processed,
    # and a file being gone is not evidence that it was written.
    for claim in claims:
        usable = _usable(claim, backed)
        if not usable:
            wrong.append((claim, (
                "what succeeded was a removal (files_delete/files_recycle); a "
                "path being gone backs 'deleted' and nothing else")))
            continue
        why = _missing_evidence(claim, usable) or _target_mismatch(claim, usable)
        if why:
            wrong.append((claim, why))
    return wrong, backed


def audit(text: str, run: Run | None, *, strict_proof: bool = True,
          correlate: dict | None = None) -> ClaimAudit:
    """
    Check every completion and failure claim in `text` against `run`.

    The conditions, each added because the one before it was not enough.
    Something must have succeeded in the run; for the verbs in
    CLAIM_EVIDENCE the thing that succeeded must be of the KIND the verb
    names - a verified `files_read` is real work and real evidence, and it is
    not evidence that a catalogue was processed; and when the sentence names
    a concrete TARGET, one of those actions must have touched it. Success on
    foo.txt is not permission to say bar.txt was created (FR-TRU-001). The
    kind rule runs both ways: a removal is proven by the path being gone,
    which backs 'deleted' and contradicts every other verb.

    And (ACT-001): its verification must show its type's proof shape; it
    must be correlated to this run - dated inside it, and carrying the key
    the run declared plus any `correlate` adds; and a failure claim needs a
    correlated attempt that went wrong, of the kind and on the target it
    names. Text is read clause by clause, so one claim cannot hide another.
    """
    parts = clauses(text)
    claims = tuple(p for p in parts if is_completion_claim(p))
    failures = tuple(p for p in parts if is_failure_claim(p))
    said = claims + failures
    if not said:
        return ClaimAudit(ok=True, reason="no completion claims")

    if run is None:
        return ClaimAudit(
            ok=False, claims=said, unbacked=said,
            reason=("completion claimed with no Run at all" if claims else
                    "a failure or attempt was claimed with no Run at all, so "
                    "no attempt is on record"),
        )

    out_of_scope = {id(r): correlation_gap(r, correlate, run) for r in run.results}
    in_scope = [r for r in run.results if not out_of_scope[id(r)]]

    wrong, backed = _completion_mismatch(claims, run, in_scope, strict_proof,
                                         out_of_scope) if claims else ([], [])
    went_wrong = [r for r in in_scope if r.status in _WENT_WRONG]
    for claim in failures:
        why = _failure_mismatch(claim, went_wrong)
        if why and not went_wrong:
            stray = [out_of_scope[id(r)] for r in run.results
                     if r.status in _WENT_WRONG and out_of_scope[id(r)]]
            why = "; ".join(dict.fromkeys([why] + stray))
        if why:
            wrong.append((claim, why))

    evidence = tuple(r.verification.evidence for r in backed if r.verification)
    if wrong:
        return ClaimAudit(
            ok=False, claims=said,
            unbacked=tuple(claim for claim, _ in wrong),
            reason="; ".join(dict.fromkeys(why for _, why in wrong)),
            evidence=evidence,
        )
    reasons = []
    if claims:
        reasons.append(f"{len(backed)} verified action(s) in {run.run_id}")
    if failures:
        reasons.append(f"{len(went_wrong)} attempt(s) that went wrong recorded")
    return ClaimAudit(ok=True, claims=said, reason="; ".join(reasons),
                      evidence=evidence)


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
