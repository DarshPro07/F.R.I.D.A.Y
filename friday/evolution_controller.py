"""
The Evolution Controller (EVO-001): a concrete trigger becomes exactly one proposal.

Owner ruling 2026-10-08c (docs/plans/build-harness/00-owner-directive-2026-10-08c.md):
"Trigger-based + overnight (Recommended) and Automatic when idle and when find issue
bug problem related to these uograding on it own nt asking". The controller is the
trigger-based half: something concrete happened (a capability was missing, a failure
repeated, a path was slow or costly) and the controller records ONE proposal for it.

Deciding and deduplicating are plain code. No model runs here - not to judge a
trigger, not to write the proposal - so without a trigger FRIDAY spends nothing and
creates nothing. A proposal is stored (`Store.evolution_proposals`), so it survives a
restart, and a repeat of the same trigger only counts one more occurrence.

Admission is EVO-006's `evolution_governor.admit`; this module never reimplements
precedence, capacity or the machine's headroom. The ruling's per-experiment budget is
written into each proposal because, with the daily cap off, it is the only spend bound.

Assumptions, stated rather than asked (revisit if wrong):
  * a failure must repeat MIN_REPEATS times, and a path must be slow (SLOW_MS) or
    costly (COSTLY_USD per call) that many times, before it earns a proposal; a
    missing capability needs one occurrence. The caller supplies the count;
  * a repeated failure is RELIABILITY_REPAIR; the others are APPROVED_IMPROVEMENT;
  * the budget figures below are conservative starting values, per class;
  * a proposal whose components reach a Z4 trust root is recorded but never admitted
    (policy.zone_of, owner ruling: Z4 stays proposal-only);
  * a NEW_RELEASE trigger (a repository or model appeared) is not evidence of anything by
    itself: EVO-004's value gate (evolution_value_gate) must find a measured reason in
    `evidence["reasons"]` before a proposal exists, else it is refused and archived. The
    other kinds are themselves observations of FRIDAY's own behaviour, so they pass as is;
  * a proposal in any status still absorbs its repeats, so a finished or rejected
    one is not proposed again; the archive (EVO-002) decides when to reopen one.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
import time
import uuid
from dataclasses import dataclass, field
from typing import Callable, Sequence

from friday import evolution_archive as A
from friday import evolution_governor as E
from friday import evolution_lab as L
from friday import evolution_value_gate as V
from friday import objective_budget as B
from friday import policy as P
from friday import self_upgrade as SU

# -- trigger kinds -------------------------------------------------------------

MISSING_CAPABILITY = "missing_capability"
REPEATED_FAILURE = "repeated_failure"
SLOW_PATH = "slow_path"
COSTLY_PATH = "costly_path"
NEW_RELEASE = "new_release"
TRIGGER_KINDS = (MISSING_CAPABILITY, REPEATED_FAILURE, SLOW_PATH, COSTLY_PATH, NEW_RELEASE)

#: How often a failure, a slow path or a costly path must be seen to be worth a proposal.
MIN_REPEATS = 3
#: One call slower than this (milliseconds) counts as slow.
SLOW_MS = 5000.0
#: One call dearer than this (US dollars) counts as costly.
COSTLY_USD = 0.50

# -- proposal status -----------------------------------------------------------

PENDING = "pending"
ADMITTED = "admitted"

MAX_SUBJECT = 200
MAX_EVIDENCE_BYTES = 4096

#: Per-experiment ceilings by class. Tool calls and turns come from the objective
#: budget's class table so the run's own enforcement matches the proposal.
_BUDGET_CLASS = {E.RELIABILITY_REPAIR: "STANDARD", E.APPROVED_IMPROVEMENT: "STANDARD",
                 E.EXPLORATION: "SIMPLE"}
_USD = {E.RELIABILITY_REPAIR: 2.0, E.APPROVED_IMPROVEMENT: 3.0, E.EXPLORATION: 1.0}
_WALL_S = {E.RELIABILITY_REPAIR: 1800, E.APPROVED_IMPROVEMENT: 3600, E.EXPLORATION: 1800}
_RISK = {"Z1": "low", "Z2": "medium", "Z3": "medium", "Z4": "high"}


@dataclass(frozen=True)
class Trigger:
    """What happened. `subject` names the capability, tool or path; `count` is how
    often it happened; `observed` is the milliseconds (slow) or dollars (costly) of
    one call; `component` is the file or area a fix would touch, when it is known."""
    kind: str
    subject: str
    count: int = 1
    observed: float = 0.0
    component: str = ""
    evidence: dict = field(default_factory=dict)


@dataclass
class Decision:
    proposed: bool
    created: bool
    reason: str
    proposal: dict | None = None


# -- EVOLAB-004 controlled run ------------------------------------------------

DOES_NOT_BEAT_BASELINE = "DOES_NOT_BEAT_BASELINE"
UNSAFE = "UNSAFE"
PROMOTED = "PROMOTED"
ROLLED_BACK = "ROLLED_BACK"
ALREADY_REJECTED = "ALREADY_REJECTED"
NO_CANDIDATES = "NO_CANDIDATES"
NOT_MEASURED_WINNER = "NOT_MEASURED_WINNER"

_UNSAFE_LAB_VERDICTS = frozenset({"WEAKENED", "TAMPERED", "FABRICATED"})
_SECRET_RE = re.compile(
    r"(?i)(api[_-]?key|secret|token|password)\s*[:=]\s*['\"]?[^'\"\s]{8,}")
_PIN_RE = re.compile(r"(@\s*[0-9a-f]{12,}|sha256[:=][0-9a-f]{32,}|"
                     r"git\+[^\s]+@[0-9a-f]{12,})", re.I)


@dataclass(frozen=True)
class EvolutionCandidate:
    """One built candidate submitted to the protected evolution run."""

    candidate_version: str
    hypothesis: str
    scope: tuple[str, ...]
    files: tuple[str, ...]
    measurements: dict
    diff: str = ""
    lab_verdict: str = "PASS"
    sandbox: str = ""
    base_commit: str = ""
    tests: tuple[str, ...] = ()
    claimed_receipt: dict | None = None


@dataclass(frozen=True)
class PromotionRecord:
    version: str
    rollback_handle: str


@dataclass(frozen=True)
class CandidateRunResult:
    candidate_version: str
    decision: str
    reason: str
    baseline: dict
    candidate: dict
    evaluation: dict
    archive_id: str
    rollback_version: str


@dataclass(frozen=True)
class EvolutionRunResult:
    results: tuple[CandidateRunResult, ...]
    promoted: PromotionRecord | None
    allowance_spent: tuple[dict, ...]


class InMemoryEvolutionAllowance:
    """Small deterministic allowance for tests and dry-runs.

    The production allowance remains `evolution_allowance.EvolutionAllowance`;
    this object only records that the controller charged EVOLAB-004 work to an
    evolution-specific ledger instead of an objective or foreground budget.
    """

    def __init__(self) -> None:
        self.rows: list[dict] = []

    def charge(self, objective_id: str, *, candidate_version: str,
               amount: float = 1.0) -> dict:
        row = {
            "id": f"SPEND-{uuid.uuid4().hex[:12]}",
            "at": time.time(),
            "objective_id": objective_id,
            "candidate_version": candidate_version,
            "dimension": "time",
            "amount": float(amount),
            "unit": "candidate",
        }
        self.rows.append(row)
        return row


def _unsafe(candidate: EvolutionCandidate) -> list[str]:
    reasons: list[str] = []
    for path in candidate.files:
        kernel = SU.is_kernel_path(path)
        if kernel:
            reasons.append(f"{path}: kernel surface ({kernel})")
    if candidate.lab_verdict in _UNSAFE_LAB_VERDICTS:
        reasons.append(f"lab verdict {candidate.lab_verdict}")
    diff = candidate.diff or ""
    if _SECRET_RE.search(diff):
        reasons.append("diff carries a secret-shaped value")
    lowered = diff.casefold()
    if "requires_edge" in lowered or "permissions" in lowered and "+" in diff:
        reasons.append("diff widens a permission surface")
    for path in candidate.files:
        name = path.replace("\\", "/").casefold()
        if name.endswith(("requirements.txt", "pyproject.toml", "setup.cfg", "setup.py")):
            added = [line for line in diff.splitlines()
                     if line.startswith("+") and not line.startswith("+++")]
            for line in added:
                if line.strip("+ ").startswith("#"):
                    continue
                if re.search(r"[a-z0-9_.-]\s*(==|>=|<=|~=|\^|=)", line, re.I) \
                        and not _PIN_RE.search(line):
                    reasons.append(f"{path}: unpinned dependency")
                    break
    return reasons


def _beats_baseline(baseline: dict, candidate: dict, tolerance: float) -> bool:
    for key, old in baseline.items():
        new = candidate.get(key)
        if isinstance(old, (int, float)) and not isinstance(old, bool) \
                and isinstance(new, (int, float)) and not isinstance(new, bool):
            if float(new) > float(old) + float(tolerance):
                return True
    return False


def _improvement(baseline: dict, candidate: dict) -> float:
    scores = [
        float(candidate[k]) - float(baseline[k])
        for k in baseline.keys() & candidate.keys()
        if isinstance(baseline[k], (int, float)) and not isinstance(baseline[k], bool)
        and isinstance(candidate[k], (int, float)) and not isinstance(candidate[k], bool)
    ]
    return max(scores) if scores else float("-inf")


def _lab_judgement(candidate: EvolutionCandidate, lab, lab_runner) -> tuple[str, dict | None]:
    missing = []
    if lab is None:
        missing.append("protected lab")
    if lab_runner is None:
        missing.append("protected lab runner")
    if not candidate.base_commit:
        missing.append("base commit")
    if not candidate.sandbox:
        missing.append("isolated candidate sandbox")
    if not candidate.tests:
        missing.append("frozen held-out tests")
    if missing:
        return f"missing {', '.join(missing)}", None
    try:
        judgement = lab.judge(
            candidate.base_commit,
            candidate.sandbox,
            list(candidate.tests),
            lab_runner,
            claimed=candidate.claimed_receipt,
        )
    except Exception as exc:
        return f"protected lab refused candidate: {exc}", None
    if judgement.verdict != L.PASS:
        reason = "; ".join(str(r) for r in judgement.reasons) or "protected lab did not pass"
        return f"protected lab verdict {judgement.verdict}: {reason}", judgement.receipt
    return "", judgement.receipt


def _record_result(archive, *, objective_id: str, parent_version: str,
                   candidate: EvolutionCandidate, baseline: dict, measurements: dict,
                   decision: str, reason: str, promoted: bool,
                   lab_receipt: dict | None = None) -> CandidateRunResult:
    evaluation = {"decision": decision, "reason": reason}
    if lab_receipt is not None:
        evaluation["lab_receipt_digest"] = lab_receipt.get("digest", "")
        evaluation["lab_receipt_run_id"] = lab_receipt.get("run_id", "")
    row = archive.record(
        objective=objective_id, parent_version=parent_version,
        candidate_version=candidate.candidate_version, hypothesis=candidate.hypothesis,
        scope=candidate.scope, baseline=baseline, candidate=measurements,
        evaluation=evaluation, decision=A.PROMOTE if promoted else A.REJECT,
        rollback_version=parent_version)
    return CandidateRunResult(
        candidate_version=candidate.candidate_version,
        decision=decision,
        reason=reason,
        baseline=dict(baseline),
        candidate=dict(measurements),
        evaluation=evaluation,
        archive_id=row["id"],
        rollback_version=parent_version)


def run_controlled_evolution(*, objective_id: str, parent_version: str,
                             candidates: Sequence[EvolutionCandidate],
                             baseline: dict, tolerance: float, archive,
                             lab=None, lab_runner=None,
                             benchmark: Callable[[EvolutionCandidate], dict],
                             promote: Callable[[EvolutionCandidate], PromotionRecord],
                             rollback: Callable[[PromotionRecord], None],
                             health_check: Callable[[PromotionRecord], bool],
                             allowance) -> EvolutionRunResult:
    """Run one EVOLAB-004 batch.

    Unsafe candidates are rejected before `benchmark` is called. A candidate is
    safe only after the Protected Evaluation Lab has judged its isolated
    sandbox against the frozen base commit and held-out tests. Safe candidates
    must beat the frozen baseline by more than `tolerance`; ties and no-gain
    runs archive a rejection and promote nothing. Every measured candidate is
    archived; only the measured best candidate is promoted, and a failed health
    check immediately invokes the supplied rollback hook and records the
    candidate as rejected for future dedupe.
    """
    already_rejected = {
        A.fingerprint(parent_version=parent_version, hypothesis=c.hypothesis, scope=c.scope)
        for c in candidates
        if archive.find_rejected(parent_version=parent_version,
                                 hypothesis=c.hypothesis, scope=c.scope) is not None
    }
    results: list[CandidateRunResult] = []
    spend: list[dict] = []
    eligible: list[tuple[EvolutionCandidate, dict, dict | None]] = []
    for candidate in candidates:
        key = A.fingerprint(parent_version=parent_version, hypothesis=candidate.hypothesis,
                            scope=candidate.scope)
        if key in already_rejected:
            results.append(CandidateRunResult(
                candidate.candidate_version, ALREADY_REJECTED,
                "same rejected experiment is already archived",
                dict(baseline), {}, {"decision": ALREADY_REJECTED}, "", parent_version))
            continue
        spend.append(allowance.charge(objective_id,
                                      candidate_version=candidate.candidate_version))
        reasons = _unsafe(candidate)
        if reasons:
            results.append(_record_result(
                archive, objective_id=objective_id, parent_version=parent_version,
                candidate=candidate, baseline=baseline, measurements=candidate.measurements,
                decision=UNSAFE, reason="; ".join(reasons), promoted=False))
            continue
        lab_reason, lab_receipt = _lab_judgement(candidate, lab, lab_runner)
        if lab_reason:
            results.append(_record_result(
                archive, objective_id=objective_id, parent_version=parent_version,
                candidate=candidate, baseline=baseline, measurements=candidate.measurements,
                decision=UNSAFE, reason=lab_reason, promoted=False,
                lab_receipt=lab_receipt))
            continue
        measured = benchmark(candidate)
        if not _beats_baseline(baseline, measured, tolerance):
            results.append(_record_result(
                archive, objective_id=objective_id, parent_version=parent_version,
                candidate=candidate, baseline=baseline, measurements=measured,
                decision=DOES_NOT_BEAT_BASELINE,
                reason="candidate did not beat the baseline past tolerance",
                promoted=False, lab_receipt=lab_receipt))
            continue
        eligible.append((candidate, measured, lab_receipt))
    if not eligible:
        return EvolutionRunResult(tuple(results), None, tuple(spend))
    winner, measured, winner_receipt = max(
        eligible, key=lambda item: _improvement(baseline, item[1]))
    for candidate, candidate_measured, lab_receipt in eligible:
        if candidate is winner:
            continue
        results.append(_record_result(
            archive, objective_id=objective_id, parent_version=parent_version,
            candidate=candidate, baseline=baseline, measurements=candidate_measured,
            decision=NOT_MEASURED_WINNER,
            reason="candidate beat baseline but was not the measured winner",
            promoted=False, lab_receipt=lab_receipt))
    record = promote(winner)
    if not health_check(record):
        rollback(record)
        results.append(_record_result(
            archive, objective_id=objective_id, parent_version=parent_version,
            candidate=winner, baseline=baseline, measurements=measured,
            decision=ROLLED_BACK,
            reason="post-promotion health check failed; rolled back",
            promoted=False, lab_receipt=winner_receipt))
        return EvolutionRunResult(tuple(results), None, tuple(spend))
    results.append(_record_result(
        archive, objective_id=objective_id, parent_version=parent_version,
        candidate=winner, baseline=baseline, measurements=measured,
        decision=PROMOTED, reason="measured winner promoted", promoted=True,
        lab_receipt=winner_receipt))
    return EvolutionRunResult(tuple(results), record, tuple(spend))


def _normal(subject: str) -> str:
    return " ".join(str(subject).split()).casefold()


def _validate(trigger: Trigger) -> str:
    """The normalised subject, or ValueError: a trigger is untrusted input."""
    if trigger.kind not in TRIGGER_KINDS:
        raise ValueError(f"unknown trigger kind {trigger.kind!r}; known: {TRIGGER_KINDS}")
    if not isinstance(trigger.subject, str):
        raise ValueError("trigger subject must be text")
    subject = _normal(trigger.subject)
    if not subject:
        raise ValueError("a trigger needs the subject it is about")
    if len(subject) > MAX_SUBJECT:
        raise ValueError(f"trigger subject is longer than {MAX_SUBJECT} characters")
    if not isinstance(trigger.count, int) or isinstance(trigger.count, bool) or trigger.count < 1:
        raise ValueError("trigger count must be a whole number of at least 1")
    observed = trigger.observed
    if (isinstance(observed, bool) or not isinstance(observed, (int, float))
            or not math.isfinite(observed) or observed < 0):
        raise ValueError("trigger observed value must be a finite number of at least 0")
    if not isinstance(trigger.component, str) or len(trigger.component) > MAX_SUBJECT:
        raise ValueError(f"trigger component must be text of at most {MAX_SUBJECT} characters")
    if trigger.kind == NEW_RELEASE and not trigger.component.strip():
        raise ValueError("a new_release trigger must name the component it would change")
    if not isinstance(trigger.evidence, dict):
        raise ValueError("trigger evidence must be a dict")
    try:
        text = json.dumps(trigger.evidence, sort_keys=True, allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"trigger evidence must be plain JSON data ({exc})") from None
    if len(text) > MAX_EVIDENCE_BYTES:
        raise ValueError(f"trigger evidence is larger than {MAX_EVIDENCE_BYTES} bytes")
    return subject


def _threshold(trigger: Trigger) -> str:
    """Why this trigger is not yet worth a proposal, or "" when it is."""
    if trigger.kind in (MISSING_CAPABILITY, NEW_RELEASE):
        return ""
    if trigger.count < MIN_REPEATS:
        return f"{trigger.kind} has not repeated enough ({trigger.count} of {MIN_REPEATS})"
    if trigger.kind == SLOW_PATH and trigger.observed < SLOW_MS:
        return f"path is not slow enough ({trigger.observed:g} ms under {SLOW_MS:g} ms)"
    if trigger.kind == COSTLY_PATH and trigger.observed < COSTLY_USD:
        return f"path is not costly enough (${trigger.observed:g} under ${COSTLY_USD:g})"
    return ""


def _class_of(trigger: Trigger) -> str:
    return E.RELIABILITY_REPAIR if trigger.kind == REPEATED_FAILURE else E.APPROVED_IMPROVEMENT


def _path_like(subject: str) -> bool:
    """A subject that names a file (friday/policy.py) says what a fix touches."""
    return "/" in subject or "\\" in subject or bool(re.search(r"\.(py|js|ts|json|toml|ya?ml|md|sh|ps1|sql|cfg|ini)$", subject))


def _components(trigger: Trigger, subject: str) -> list[str]:
    if trigger.component.strip():
        found = [trigger.component.strip()]
        # the component is the caller's claim; a path-like subject is the trigger's own
        # evidence of what is touched, and the zone must see both
        return found + [subject] if _path_like(subject) and subject not in found else found
    if trigger.kind == MISSING_CAPABILITY:
        slug = re.sub(r"[^a-z0-9_]+", "-", subject).strip("-")
        if not slug:
            raise ValueError("a missing capability needs a name with letters or digits")
        return [f"skills/{slug}/"]
    return [subject]


def _body(trigger: Trigger, subject: str, klass: str, zone: str, components: list[str]) -> dict:
    limits = B.CLASS_LIMITS[_BUDGET_CLASS[klass]]
    budget = {"max_usd": _USD[klass], "max_wall_s": _WALL_S[klass],
              "max_tool_calls": limits["max_tool_calls"], "max_turns": limits["max_turns"]}
    what = {MISSING_CAPABILITY: f"FRIDAY cannot do {subject!r} and was asked to",
            REPEATED_FAILURE: f"{subject!r} failed {trigger.count} times",
            SLOW_PATH: f"{subject!r} took {trigger.observed:g} ms, {trigger.count} times",
            COSTLY_PATH: f"{subject!r} cost ${trigger.observed:g} a call, {trigger.count} times",
            NEW_RELEASE: f"{subject!r} was released and measurably improves {components[0]!r}"}
    return {
        "trigger": {"kind": trigger.kind, "subject": subject, "count": trigger.count,
                    "observed": trigger.observed, "evidence": trigger.evidence},
        "benefit": {"summary": what[trigger.kind] + "; the change removes that cause"},
        "risk": {"level": _RISK[zone], "zone": zone,
                 "note": "a trust root is proposal-only" if zone == "Z4"
                         else "verified independently and rolled back automatically on regression"},
        "cost": {"estimate_usd": budget["max_usd"], "components": len(components)},
        "components": components,
        "acceptance_tests": [
            f"a test that reproduces: {what[trigger.kind]}, failing before the change and passing after",
            "the existing tests of every touched component still pass",
            "an independent verifier, not the builder, confirms both",
        ],
        "budget": budget,
        "termination": {"max_wall_s": budget["max_wall_s"],
                        "stop_when": ["acceptance tests pass", "any budget dimension is exhausted",
                                      "the trajectory shows no progress"]},
    }


def _view(row: dict) -> dict:
    """A stored row as one flat proposal."""
    return {**row["body"], **{k: row[k] for k in (
        "proposal_id", "dedupe_key", "trigger_kind", "subject", "klass", "zone", "status",
        "occurrences", "run_id", "lease", "created_at", "updated_at")},
        "proposal_only": row["zone"] == P.Z4}


def proposals(store, status: str | None = None) -> list[dict]:
    return [_view(row) for row in store.evolution_proposals(status)]


def hypothesis_of(proposal: dict) -> str:
    """The archive identity of a proposal's experiment: one trigger kind about one
    subject, the same pair that dedupes the proposal. Pass it, with the proposal's
    `components` as the scope, to `EvolutionAllowance.authorize_step` and `conclude`
    so the step gate finds what `conclude` archived."""
    return f"{proposal['trigger_kind']}: {proposal['subject']}"


def conclude(archive, proposal: dict, *, parent_version: str, candidate_version: str,
             baseline: dict, candidate: dict, evaluation: dict, promoted: bool) -> dict:
    """Archive a finished experiment (EVO-002): PROMOTE when the independent
    verification passed, REJECT when it did not. A rejected candidate is kept like
    a promoted one, and the version to roll back to is the parent."""
    return archive.record(
        objective=proposal["proposal_id"], parent_version=parent_version,
        candidate_version=candidate_version, hypothesis=hypothesis_of(proposal),
        scope=proposal["components"], baseline=baseline, candidate=candidate,
        evaluation=evaluation, decision=A.PROMOTE if promoted else A.REJECT,
        rollback_version=parent_version)


def _value_gate(trigger: Trigger, subject: str, archive) -> str:
    """Why the Evolution Value Gate refuses a release trigger, or "" when it has a
    measurable reason. The refusal is archived; no model runs."""
    component = trigger.component.strip()
    verdict = V.review(
        hypothesis=f"adopt {subject} for {component}", parent_version="current",
        scope=[component], origin=trigger.evidence.get("origin", "unknown"),
        reasons=trigger.evidence.get("reasons"),
        archive=archive if archive is not None else A.EvolutionArchive())
    return "" if verdict.allowed else verdict.reason


def consider(store, trigger: Trigger | None, *, archive=None) -> Decision:
    """Turn a trigger into a proposal. No trigger, or one below its threshold,
    records nothing. A repeat of a recorded trigger returns the same proposal.
    A release trigger without a measurable reason is refused and archived in `archive`
    (default: the Evolution Archive on disk)."""
    if trigger is None:
        return Decision(False, False, "no trigger: nothing to propose")
    subject = _validate(trigger)
    why = _threshold(trigger)
    if why:
        return Decision(False, False, why)
    key = hashlib.sha256(f"{trigger.kind}\0{subject}".encode("utf-8")).hexdigest()[:32]
    if trigger.kind == NEW_RELEASE:
        existing = store.evolution_proposal(f"evo-{key}")
        if existing is not None and not V.evaluate(trigger.evidence.get("reasons")).allowed:
            # The proposal this release already earned is not refused after the fact, and a
            # repeat that carries no reason adds nothing to it (no count, no components).
            return Decision(False, False, f"already proposed as {existing['proposal_id']}; "
                                          "this repeat has no measurable reason and was not counted")
        why = _value_gate(trigger, subject, archive)
        if why:
            return Decision(False, False, why)
    components = _components(trigger, subject)
    zone = P.zone_of(components)
    klass = _class_of(trigger)
    row, created = store.evolution_proposal_raise(
        f"evo-{key}", key, trigger_kind=trigger.kind, subject=subject, klass=klass,
        zone=zone, body=_body(trigger, subject, klass, zone, components))
    return Decision(True, created,
                    "proposal created" if created else "already proposed; repeat counted",
                    _view(row))


def admit(store, gov, proposal_id: str, run_id: str, *, mode: str, now=None) -> E.Admission:
    """Start the experiment for a pending proposal through EVO-006's admission.
    A higher class still waiting holds this one back; a Z4 proposal never starts."""
    row = store.evolution_proposal(proposal_id)
    if row is None:
        return E.Admission(False, f"unknown proposal {proposal_id!r}")
    if row["zone"] == P.Z4:
        return E.Admission(False, "Z4 trust root: proposal only, the owner applies it")
    if row["status"] != PENDING:
        return E.Admission(False, f"proposal is {row['status']}, not pending")
    waiting: dict[str, int] = {}
    for other in store.evolution_proposals(PENDING):
        if other["proposal_id"] != proposal_id and other["zone"] != P.Z4:
            waiting[other["klass"]] = waiting.get(other["klass"], 0) + 1
    if not store.evolution_proposal_move(proposal_id, PENDING, ADMITTED):
        return E.Admission(False, "proposal was admitted by another caller")
    try:
        admission = E.admit(store, gov, run_id, klass=row["klass"], mode=mode,
                            trigger=f"{row['trigger_kind']}:{row['subject']}",
                            waiting=waiting, now=now)
    except BaseException:
        store.evolution_proposal_move(proposal_id, ADMITTED, PENDING)
        raise
    if not admission.admitted:
        store.evolution_proposal_move(proposal_id, ADMITTED, PENDING)
    else:
        store.evolution_proposal_link(proposal_id, run_id, admission.lease)
    return admission
