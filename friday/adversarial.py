"""
Adversarial reasoning (PRD v3.1 FR-008 contrarian decision mode, FR-012
independent verification worker).

Two things, one mechanism:

  * `deliberate(question, evidence)` - a panel of five fixed roles
    (proposer, contrarian, failure analyst, evidence checker, judge), each
    one bounded inference through the Hermes MODEL_GATEWAY (no agent loop,
    no tools). The output keeps every role's position verbatim, the
    disagreements the judge did not resolve, the evidence each side cited
    and the judge's stated uncertainty. It never collapses into a single
    consensus sentence: that is the failure the PRD names.

  * `independent_review(change)` - a reviewer that is NOT the worker
    that implemented a change reads the diff, the worker's claim and the
    objective verifier's result, and returns CONFIRMED / DISPUTED /
    INCONCLUSIVE with findings. `friday.promotion.decide` refuses a
    DISPUTED change; the implementing worker is never the sole authority.

Everything that decides is deterministic and testable without a model:
the role prompts are fixed, the parse is structural (tagged lines), the
disagreement extraction and the aggregate verdict are pure functions, and
`infer` is injectable. The model supplies opinions; this module supplies
the rules for what happens to them.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Callable

logger = logging.getLogger("friday.adversarial")

PROPOSER = "proposer"
CONTRARIAN = "contrarian"
FAILURE_ANALYST = "failure_analyst"
EVIDENCE_CHECKER = "evidence_checker"
JUDGE = "judge"
ROLES = (PROPOSER, CONTRARIAN, FAILURE_ANALYST, EVIDENCE_CHECKER, JUDGE)

CONFIRMED = "CONFIRMED"
DISPUTED = "DISPUTED"
INCONCLUSIVE = "INCONCLUSIVE"

#: Fixed framing per role. Each answers in tagged lines so the parse is
#: structural, not a second model call. Kept short: five roles x this
#: prompt is the whole context cost of a deliberation.
ROLE_PROMPTS: dict[str, str] = {
    PROPOSER: (
        "You are the PROPOSER. Make the strongest honest case FOR the option "
        "under discussion. Answer in tagged lines only:\n"
        "POSITION: <one sentence>\nARGUMENT: <one per line, at most 4>\n"
        "EVIDENCE: <one per line, cite the evidence items by their [E#] tag>\n"
        "CONFIDENCE: <0-100>"),
    CONTRARIAN: (
        "You are the CONTRARIAN. Your job is to find why this is wrong, "
        "risky or worse than the alternative. Do not soften. Answer in tagged "
        "lines only:\nPOSITION: <one sentence>\nOBJECTION: <one per line, at "
        "most 4, each concrete>\nEVIDENCE: <one per line, cite [E#] tags>\n"
        "CONFIDENCE: <0-100>"),
    FAILURE_ANALYST: (
        "You are the FAILURE ANALYST. Assume the decision was taken and failed "
        "six months later. Name the most likely failure modes and what would "
        "have shown them early. Tagged lines only:\nFAILURE: <one per line, "
        "at most 4>\nEARLY_SIGNAL: <one per line, matching the failures>\n"
        "CONFIDENCE: <0-100>"),
    EVIDENCE_CHECKER: (
        "You are the EVIDENCE CHECKER. For each evidence item [E#], say "
        "whether it actually supports what it is being used for. Tagged "
        "lines only:\nSUPPORTED: <[E#] ... one per line>\nUNSUPPORTED: <[E#] "
        "... one per line, with why>\nMISSING: <evidence that would be needed "
        "but is absent, one per line>\nCONFIDENCE: <0-100>"),
    JUDGE: (
        "You are the JUDGE. You have the proposer, contrarian, failure analyst "
        "and evidence checker in front of you. Decide, but preserve what was "
        "not resolved. Tagged lines only:\nVERDICT: <ADOPT | REJECT | DEFER>\n"
        "REASON: <one sentence>\nUNRESOLVED: <disagreement the record must "
        "keep, one per line; write NONE if truly none>\nUNCERTAINTY: <what "
        "you do not know, one per line>\nCONFIDENCE: <0-100>"),
}

REVIEWER_PROMPT = (
    "You are an INDEPENDENT REVIEWER. You did not write this change. Read "
    "the diff, the worker's claim and the objective verifier's result, and "
    "say whether the claim is supported. A green test the worker chose is "
    "weak evidence; a claim the diff does not implement is a finding. Tagged "
    "lines only:\nVERDICT: <CONFIRMED | DISPUTED | INCONCLUSIVE>\n"
    "FINDING: <one per line, each naming a file or a concrete input; NONE if "
    "none>\nCLAIM_MATCHES_DIFF: <yes | no | partly>\nCONFIDENCE: <0-100>")


# ---------------------------------------------------------------------------
# Inference seam
# ---------------------------------------------------------------------------

#: infer(role_prompt, user_text, *, worker, objective_id) -> str
Infer = Callable[..., str]


def gateway_infer(system: str, user: str, *, worker: str, objective_id: str,
                  task_class: str = "STANDARD", timeout_s: float = 90.0) -> str:
    """One bounded inference through the Hermes MODEL_GATEWAY (FR-079: no
    agent loop). Raises whatever the gateway raises; the panel reports it
    as that role being unavailable rather than inventing a position."""
    from friday import model_gateway as mg
    request = mg.ModelGatewayRequest(
        objective_id=objective_id or "deliberation", task_class=task_class,
        context_package=mg.compile_context(system=system, user=user),
        worker=worker, timeout_s=timeout_s, temperature=0.2)
    result = mg.gateway().infer(request)
    if result.status != "ok":
        raise RuntimeError(f"gateway {result.status}: {result.entitlement_state} "
                           f"{'; '.join(result.warnings)}")
    return result.response


# ---------------------------------------------------------------------------
# Parsing (structural, no model)
# ---------------------------------------------------------------------------

_TAG = re.compile(r"^\s*([A-Z_]+)\s*:\s*(.*)$")


def parse_tagged(text: str) -> dict[str, list[str]]:
    """TAG: value lines -> {tag: [values]}.

    Models answer a list tag two ways: `TAG: item` repeated, or `TAG:` then
    one item per line (optionally bulleted). Both parse to one item per
    line. An indented line continues the previous item. Text before any tag
    is kept under '_' so nothing a role said is silently lost."""
    out: dict[str, list[str]] = {}
    current = "_"
    for raw in (text or "").splitlines():
        line = raw.rstrip()
        if not line.strip():
            continue
        m = _TAG.match(line)
        if m and m.group(1).isupper() and not line[:1].isspace():
            current = m.group(1)
            value = m.group(2).strip()
            out.setdefault(current, [])
            if value and value.upper() != "NONE":
                out[current].append(value)
            continue
        body = _BULLET.sub("", line.strip())
        if current == "_":
            out.setdefault("_", []).append(body)
        elif line[:1].isspace() and out[current]:
            out[current][-1] += " " + body          # indented continuation
        elif body.upper() != "NONE":
            out[current].append(body)               # one item per line
    return out


_BULLET = re.compile(r"^(?:[-*\u2022]|\d+[.)])\s+")


def confidence_of(parsed: dict[str, list[str]]) -> int | None:
    for value in parsed.get("CONFIDENCE", []):
        m = re.search(r"\d{1,3}", value)
        if m:
            return max(0, min(100, int(m.group())))
    return None


_EVIDENCE_TAG = re.compile(r"\[E(\d+)\]")


def cited(parsed: dict[str, list[str]], *tags: str) -> tuple[str, ...]:
    """Every [E#] tag a role cited under the given line tags."""
    found: list[str] = []
    for tag in tags:
        for value in parsed.get(tag, []):
            for n in _EVIDENCE_TAG.findall(value):
                ref = f"E{n}"
                if ref not in found:
                    found.append(ref)
    return tuple(found)


# ---------------------------------------------------------------------------
# Deliberation (FR-008)
# ---------------------------------------------------------------------------


@dataclass
class Position:
    role: str
    raw: str
    parsed: dict[str, list[str]]
    confidence: int | None
    cites: tuple[str, ...]
    available: bool = True
    error: str = ""


@dataclass
class Decision:
    """FR-008 acceptance: disagreements, evidence and uncertainty survive."""

    question: str
    verdict: str                       # ADOPT | REJECT | DEFER | UNAVAILABLE
    reason: str
    positions: dict[str, Position]
    disagreements: tuple[str, ...]     # objections + failures the judge did not resolve
    unresolved: tuple[str, ...]        # what the judge itself listed
    uncertainty: tuple[str, ...]
    evidence: dict[str, str]           # E# -> text
    evidence_supported: tuple[str, ...]
    evidence_unsupported: tuple[str, ...]
    evidence_missing: tuple[str, ...]
    confidence: int | None
    roles_unavailable: tuple[str, ...] = ()

    @property
    def consensus(self) -> bool:
        """True only when nobody objected and nothing is unresolved. Rare,
        and the record says why when it happens."""
        return not self.disagreements and not self.unresolved

    def to_dict(self) -> dict:
        return {
            "question": self.question, "verdict": self.verdict, "reason": self.reason,
            "confidence": self.confidence, "consensus": self.consensus,
            "disagreements": list(self.disagreements),
            "unresolved": list(self.unresolved), "uncertainty": list(self.uncertainty),
            "evidence": dict(self.evidence),
            "evidence_supported": list(self.evidence_supported),
            "evidence_unsupported": list(self.evidence_unsupported),
            "evidence_missing": list(self.evidence_missing),
            "positions": {r: {"raw": p.raw, "confidence": p.confidence,
                              "cites": list(p.cites), "available": p.available,
                              "error": p.error}
                          for r, p in self.positions.items()},
            "roles_unavailable": list(self.roles_unavailable),
        }


def _evidence_block(evidence: list[str]) -> tuple[str, dict[str, str]]:
    tagged = {f"E{i + 1}": text for i, text in enumerate(evidence)}
    block = "\n".join(f"[{k}] {v}" for k, v in tagged.items()) or "(no evidence supplied)"
    return block, tagged


def _ask(infer: Infer, role: str, system: str, user: str, *, objective_id: str) -> Position:
    try:
        raw = infer(system, user, worker=f"panel:{role}", objective_id=objective_id)
    except Exception as exc:  # noqa: BLE001 - a missing role is recorded, not invented
        logger.warning("panel role %s unavailable: %s", role, exc)
        return Position(role=role, raw="", parsed={}, confidence=None, cites=(),
                        available=False, error=str(exc)[:300])
    parsed = parse_tagged(raw)
    return Position(role=role, raw=raw, parsed=parsed, confidence=confidence_of(parsed),
                    cites=cited(parsed, "EVIDENCE", "SUPPORTED", "UNSUPPORTED"))


def deliberate(question: str, evidence: list[str], *, options: list[str] | None = None,
               objective_id: str = "", infer: Infer = gateway_infer) -> Decision:
    """Run the five-role panel over one question. Five bounded inferences,
    sequential (the judge needs the other four). Disagreements are
    computed here, from the record, not asked of the judge."""
    block, tagged = _evidence_block(evidence)
    framing = (f"QUESTION: {question}\n"
               + (f"OPTIONS: {'; '.join(options)}\n" if options else "")
               + f"EVIDENCE:\n{block}")
    positions: dict[str, Position] = {}
    for role in (PROPOSER, CONTRARIAN, FAILURE_ANALYST, EVIDENCE_CHECKER):
        positions[role] = _ask(infer, role, ROLE_PROMPTS[role], framing,
                               objective_id=objective_id)

    transcript = "\n\n".join(
        f"--- {role.upper()} ---\n{positions[role].raw or '(unavailable: ' + positions[role].error + ')'}"
        for role in (PROPOSER, CONTRARIAN, FAILURE_ANALYST, EVIDENCE_CHECKER))
    positions[JUDGE] = _ask(infer, JUDGE, ROLE_PROMPTS[JUDGE],
                            f"{framing}\n\nPANEL:\n{transcript}", objective_id=objective_id)

    judge = positions[JUDGE].parsed
    verdict = (judge.get("VERDICT") or ["DEFER"])[0].split()[0].upper() \
        if positions[JUDGE].available else "UNAVAILABLE"
    if verdict not in ("ADOPT", "REJECT", "DEFER", "UNAVAILABLE"):
        verdict = "DEFER"
    reason = " ".join(judge.get("REASON", [])) or (
        "judge unavailable" if not positions[JUDGE].available else "")
    unresolved = tuple(judge.get("UNRESOLVED", []))
    uncertainty = tuple(judge.get("UNCERTAINTY", []))

    # The record's own disagreement list: every objection and failure mode
    # raised, minus those the judge's reason or unresolved list mentions.
    # A judge that ignores an objection does not make it disappear.
    raised = [f"contrarian: {o}" for o in positions[CONTRARIAN].parsed.get("OBJECTION", [])]
    raised += [f"failure_analyst: {f}" for f in positions[FAILURE_ANALYST].parsed.get("FAILURE", [])]
    judge_text = (reason + " " + " ".join(unresolved)).lower()
    disagreements = tuple(item for item in raised
                          if not _addressed(item.split(": ", 1)[1], judge_text))

    checker = positions[EVIDENCE_CHECKER].parsed
    unavailable = tuple(r for r, p in positions.items() if not p.available)
    return Decision(
        question=question, verdict=verdict, reason=reason, positions=positions,
        disagreements=disagreements, unresolved=unresolved, uncertainty=uncertainty,
        evidence=tagged,
        evidence_supported=cited({"S": checker.get("SUPPORTED", [])}, "S"),
        evidence_unsupported=cited({"U": checker.get("UNSUPPORTED", [])}, "U"),
        evidence_missing=tuple(checker.get("MISSING", [])),
        confidence=positions[JUDGE].confidence, roles_unavailable=unavailable)


def _addressed(item: str, judge_text: str) -> bool:
    """An objection counts as addressed only if the judge's own words carry
    its distinctive terms. Crude on purpose: the cost of a false 'not
    addressed' is one extra line in the record; the cost of a false
    'addressed' is a buried objection."""
    words = [w for w in re.findall(r"[a-z]{5,}", item.lower())
             if w not in _STOP]
    if not words:
        return False
    hits = sum(1 for w in words if w in judge_text)
    return hits >= max(2, len(words) // 2)


_STOP = frozenset({"would", "could", "should", "there", "which", "their", "about",
                   "because", "these", "those", "other", "being", "while", "where"})


# ---------------------------------------------------------------------------
# Independent review (FR-012)
# ---------------------------------------------------------------------------


@dataclass
class ChangeUnderReview:
    goal: str
    claim: str                         # what the implementing worker said it did
    diff: str                          # unified diff, bounded by the caller
    verifier_result: str               # the objective verifier's exit/detail
    implemented_by: str                # worker attribution, e.g. "hermes:friday"
    changed_files: tuple[str, ...] = ()


@dataclass
class ReviewEvidence:
    verdict: str                       # CONFIRMED | DISPUTED | INCONCLUSIVE
    findings: tuple[str, ...]
    claim_matches_diff: str            # yes | no | partly | unknown
    confidence: int | None
    reviewed_by: str
    independent_of: str
    raw: str = ""
    error: str = ""

    @property
    def independent(self) -> bool:
        return self.reviewed_by != self.independent_of

    def to_dict(self) -> dict:
        return {"verdict": self.verdict, "findings": list(self.findings),
                "claim_matches_diff": self.claim_matches_diff,
                "confidence": self.confidence, "reviewed_by": self.reviewed_by,
                "independent_of": self.independent_of, "independent": self.independent,
                "error": self.error}


MAX_DIFF_CHARS = 24_000


def independent_review(change: ChangeUnderReview, *, objective_id: str = "",
                       reviewer: str = "reviewer:model_gateway",
                       infer: Infer = gateway_infer) -> ReviewEvidence:
    """A second authority over a consequential change. The reviewer is a
    different attribution from the implementer by construction; if a
    caller passes the same one, the evidence says so (`independent` is
    False) and promotion treats it as absent."""
    diff = change.diff
    if len(diff) > MAX_DIFF_CHARS:
        diff = diff[:MAX_DIFF_CHARS] + f"\n... [{len(change.diff) - MAX_DIFF_CHARS} chars truncated]"
    user = (f"GOAL: {change.goal}\nIMPLEMENTED_BY: {change.implemented_by}\n"
            f"WORKER_CLAIM: {change.claim}\nVERIFIER_RESULT: {change.verifier_result}\n"
            f"CHANGED_FILES: {', '.join(change.changed_files) or '(unknown)'}\n"
            f"DIFF:\n{diff or '(empty diff)'}")
    try:
        raw = infer(REVIEWER_PROMPT, user, worker=reviewer, objective_id=objective_id)
    except Exception as exc:  # noqa: BLE001
        logger.warning("independent review unavailable: %s", exc)
        return ReviewEvidence(verdict=INCONCLUSIVE, findings=(), claim_matches_diff="unknown",
                              confidence=None, reviewed_by=reviewer,
                              independent_of=change.implemented_by, error=str(exc)[:300])
    parsed = parse_tagged(raw)
    verdict = (parsed.get("VERDICT") or [INCONCLUSIVE])[0].split()[0].upper()
    if verdict not in (CONFIRMED, DISPUTED, INCONCLUSIVE):
        verdict = INCONCLUSIVE
    findings = tuple(parsed.get("FINDING", []))
    matches = (parsed.get("CLAIM_MATCHES_DIFF") or ["unknown"])[0].split()[0].lower()
    # A reviewer that lists findings but says CONFIRMED is contradicting
    # itself; the findings win, because they are the checkable part.
    if verdict == CONFIRMED and (findings or matches == "no"):
        verdict = DISPUTED
    if not diff.strip():
        verdict = INCONCLUSIVE
        findings = findings + ("no diff was available to review",)
    return ReviewEvidence(verdict=verdict, findings=findings, claim_matches_diff=matches,
                          confidence=confidence_of(parsed), reviewed_by=reviewer,
                          independent_of=change.implemented_by, raw=raw)
