"""ULTRA-005: ultracode verification templates, built on friday/adversarial.py.

What a workflow stage can name:

  skeptic_vote     N skeptics try to refute one claim; a majority refuting kills it.
  perspective_check  one verifier per distinct angle; any dispute fails the claim.
  judge_panel      N independent attempts, adversarial.deliberate over them, one
                   synthesis. `deliberation` is the same panel with the attempts
                   from distinct providers (FAB-009).
  loop_until_dry   repeat a finder until K consecutive rounds add nothing new.
  completeness_critic  what the results do not cover; `verify` always runs it last.

A verifier is never the producer's model or session. Each agent is an `Actor`
that states both, and a verifier that shares either (or states neither) raises
`IsolationError` before a single inference is made.

Like adversarial.py, everything that decides is a pure function of the answers
and `infer` is injectable per actor: the model supplies opinions, this module
supplies what happens to them. Nothing here schedules or budgets; the objective
engine does (ULTRA-002/003).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Callable, Iterable, Mapping, Sequence

from friday import adversarial as A

logger = logging.getLogger("friday.ultracode.templates")

KILLED = "KILLED"            # a majority of skeptics refuted the claim
SURVIVED = "SURVIVED"        # a majority held; the dissent stays on the record
UNVERIFIED = "UNVERIFIED"    # no majority either way: never a pass
COMPLETE = "COMPLETE"
INCOMPLETE = "INCOMPLETE"
UNAVAILABLE = "UNAVAILABLE"

_DECIDED = ("ADOPT", "REJECT")
_REFUTE = "refute it: find the strongest concrete reason the claim is false"


class IsolationError(ValueError):
    """A verifier that is not independent of the producer."""


@dataclass(frozen=True)
class Actor:
    """One agent: who it is (`model`, `session`) and how to reach it (`infer`).
    Independence is checked on the stated identity, so it must be stated."""

    name: str
    model: str
    session: str
    infer: A.Infer = field(compare=False, repr=False)


def _key(value: str) -> str:
    return " ".join((value or "").split()).casefold()


def require_independent(producer: Actor, verifiers: Sequence[Actor]) -> None:
    for who in (producer, *verifiers):
        if not _key(who.model) or not _key(who.session):
            raise IsolationError(f"{who.name or '(unnamed)'}: model and session must be "
                                 "stated, independence cannot be shown without them")
    for verifier in verifiers:
        if _key(verifier.model) == _key(producer.model):
            raise IsolationError(f"verifier {verifier.name!r} runs on the producer's "
                                 f"model {producer.model!r}")
        if _key(verifier.session) == _key(producer.session):
            raise IsolationError(f"verifier {verifier.name!r} shares the producer's "
                                 f"session {producer.session!r}")


def _framing(label: str, text: str, evidence: Sequence[str],
             options: Sequence[str] | None = None) -> str:
    block = "\n".join(f"[E{i}] {item}" for i, item in enumerate(evidence, 1)) \
        or "(no evidence supplied)"
    return (f"{label}: {text}\n"
            + (f"OPTIONS: {'; '.join(options)}\n" if options else "")
            + f"EVIDENCE:\n{block}")


def _first_word(parsed: dict[str, list[str]], tag: str, default: str) -> str:
    return (parsed.get(tag) or [default])[0].split()[0].upper()


# ---------------------------------------------------------------------------
# One check: the unit under skeptics and perspective verifiers
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Check:
    verifier: str
    model: str
    session: str
    perspective: str
    verdict: str                     # CONFIRMED | DISPUTED | INCONCLUSIVE
    findings: tuple[str, ...]
    confidence: int | None
    available: bool = True
    error: str = ""


def _check_prompt(perspective: str) -> str:
    return ("You are an INDEPENDENT VERIFIER. You did not produce this claim. Judge "
            f"it from one perspective only: {perspective}. A claim the evidence does "
            "not support is DISPUTED; what you could not check is INCONCLUSIVE. "
            "Tagged lines only:\nVERDICT: <CONFIRMED | DISPUTED | INCONCLUSIVE>\n"
            "FINDING: <one per line, each concrete; NONE if none>\nCONFIDENCE: <0-100>")


def _check(actor: Actor, perspective: str, user: str, *, template: str,
           objective_id: str) -> Check:
    who = dict(verifier=actor.name, model=actor.model, session=actor.session,
               perspective=perspective)
    try:
        raw = actor.infer(_check_prompt(perspective), user,
                          worker=f"{template}:{actor.name}", objective_id=objective_id)
    except Exception as exc:  # noqa: BLE001 - a missing verifier is recorded, never a pass
        logger.warning("%s verifier %s unavailable: %s", template, actor.name, exc)
        return Check(**who, verdict=A.INCONCLUSIVE, findings=(), confidence=None,
                     available=False, error=str(exc)[:300])
    parsed = A.parse_tagged(raw)
    verdict = _first_word(parsed, "VERDICT", A.INCONCLUSIVE)
    if verdict not in (A.CONFIRMED, A.DISPUTED, A.INCONCLUSIVE):
        verdict = A.INCONCLUSIVE
    findings = tuple(parsed.get("FINDING", []))
    # As in independent_review: findings are the checkable part, so a verifier
    # that lists them while saying CONFIRMED is contradicting itself.
    if verdict == A.CONFIRMED and findings:
        verdict = A.DISPUTED
    return Check(**who, verdict=verdict, findings=findings,
                 confidence=A.confidence_of(parsed))


# ---------------------------------------------------------------------------
# Skeptics: a majority refuting kills the claim
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SkepticReport:
    claim: str
    status: str                      # KILLED | SURVIVED | UNVERIFIED
    checks: tuple[Check, ...]

    @property
    def refuted(self) -> int:
        return sum(c.verdict == A.DISPUTED for c in self.checks)

    @property
    def held(self) -> int:
        return sum(c.verdict == A.CONFIRMED for c in self.checks)

    @property
    def unavailable(self) -> int:
        return sum(not c.available for c in self.checks)


def skeptic_vote(claim: str, evidence: Sequence[str], *, producer: Actor,
                 skeptics: Sequence[Actor], objective_id: str = "") -> SkepticReport:
    """Every skeptic is asked independently. A majority of the N asked must
    refute to kill the claim and a majority must hold for it to survive; a
    skeptic that could not answer is neither, so a split or a thin panel is
    UNVERIFIED rather than a pass."""
    if not skeptics:
        raise ValueError("skeptic_vote needs at least one skeptic")
    require_independent(producer, skeptics)
    user = _framing("CLAIM", claim, evidence)
    checks = tuple(_check(s, _REFUTE, user, template="skeptic", objective_id=objective_id)
                   for s in skeptics)
    refuted = sum(c.verdict == A.DISPUTED for c in checks)
    held = sum(c.verdict == A.CONFIRMED for c in checks)
    status = (KILLED if refuted * 2 > len(checks)
              else SURVIVED if held * 2 > len(checks) else UNVERIFIED)
    return SkepticReport(claim=claim, status=status, checks=checks)


# ---------------------------------------------------------------------------
# Perspective-diverse verifiers
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PerspectiveReport:
    claim: str
    status: str                      # CONFIRMED | DISPUTED | INCONCLUSIVE
    checks: tuple[Check, ...]


def perspective_check(claim: str, evidence: Sequence[str], *, producer: Actor,
                      perspectives: Mapping[str, Actor],
                      objective_id: str = "") -> PerspectiveReport:
    """One verifier per distinct angle (correctness, security, regression...).
    The angles cover different failures, so no vote: any DISPUTED fails the
    claim and only all-CONFIRMED passes it."""
    if not perspectives:
        raise ValueError("perspective_check needs at least one perspective")
    if len({_key(p) for p in perspectives}) != len(perspectives):
        raise ValueError("perspectives must differ")
    require_independent(producer, list(perspectives.values()))
    user = _framing("CLAIM", claim, evidence)
    checks = tuple(_check(actor, angle, user, template="perspective",
                          objective_id=objective_id)
                   for angle, actor in perspectives.items())
    verdicts = {c.verdict for c in checks}
    status = (A.DISPUTED if A.DISPUTED in verdicts
              else A.CONFIRMED if verdicts == {A.CONFIRMED} else A.INCONCLUSIVE)
    return PerspectiveReport(claim=claim, status=status, checks=checks)


# ---------------------------------------------------------------------------
# Judge panel and FAB-009 deliberation
# ---------------------------------------------------------------------------

_ATTEMPT_PROMPT = (
    "You are one of several INDEPENDENT ATTEMPTS at the same question. You "
    "cannot see the others. Answer on your own. Tagged lines only:\n"
    "ANSWER: <one per line>\nCONFIDENCE: <0-100>")

_SYNTHESIS_PROMPT = (
    "You are the JUDGE writing the final answer. The independent attempts and "
    "the panel's verdict are below. Merge what the attempts agree on into one "
    "answer, say which attempt you preferred where they conflict and why, and "
    "do not hide what is still open. Tagged lines only:\n"
    "SYNTHESIS: <one per line>\nCONFIDENCE: <0-100>")


@dataclass(frozen=True)
class Attempt:
    producer: str
    model: str
    session: str
    answer: str
    confidence: int | None
    available: bool = True
    error: str = ""


@dataclass(frozen=True)
class PanelResult:
    question: str
    verdict: str                     # ADOPT | REJECT | DEFER | UNAVAILABLE
    reason: str
    synthesis: str
    disagreements: tuple[str, ...]   # what the panel did not resolve
    attempts: tuple[Attempt, ...]
    decision: A.Decision | None

    @property
    def converged(self) -> bool:
        """True only for a decided verdict with nothing left open. A panel
        that did not converge says so; it does not invent agreement."""
        return (self.verdict in _DECIDED and self.decision is not None
                and self.decision.consensus)


def _attempt(actor: Actor, user: str, objective_id: str) -> Attempt:
    who = dict(producer=actor.name, model=actor.model, session=actor.session)
    try:
        raw = actor.infer(_ATTEMPT_PROMPT, user, worker=f"attempt:{actor.name}",
                          objective_id=objective_id)
    except Exception as exc:  # noqa: BLE001 - a missing attempt is recorded, not invented
        logger.warning("attempt %s unavailable: %s", actor.name, exc)
        return Attempt(**who, answer="", confidence=None, available=False,
                       error=str(exc)[:300])
    parsed = A.parse_tagged(raw)
    answer = " ".join(parsed.get("ANSWER", [])) or (raw or "").strip()
    if not answer:
        return Attempt(**who, answer="", confidence=None, available=False,
                       error="empty answer")
    return Attempt(**who, answer=answer, confidence=A.confidence_of(parsed))


def judge_panel(question: str, evidence: Sequence[str], *, producers: Sequence[Actor],
                judge: Actor, critics: Sequence[Actor] = (),
                options: Sequence[str] | None = None, objective_id: str = "",
                distinct_models: bool = False) -> PanelResult:
    """N independent attempts, then the adversarial panel over them, then one
    synthesis by the judge. The proposer, contrarian, failure analyst and
    evidence checker rotate over `critics` (the judge itself when none); every
    one of them and the judge is a verifier of the attempts, so none may share
    a producer's model or session."""
    if len(producers) < 2:
        raise ValueError("a judge panel needs at least two independent attempts")
    reviewers = (judge, *critics)
    for producer in producers:
        require_independent(producer, reviewers)
    if len({_key(p.session) for p in producers}) < len(producers):
        raise IsolationError("attempts must run in independent sessions")
    if distinct_models and len({_key(p.model) for p in producers}) < len(producers):
        raise IsolationError("a deliberation needs attempts from distinct providers")

    user = _framing("QUESTION", question, evidence, options)
    attempts = tuple(_attempt(p, user, objective_id) for p in producers)
    live = [a for a in attempts if a.available]
    if len(live) < 2:
        return PanelResult(
            question=question, verdict=UNAVAILABLE, synthesis="", disagreements=(),
            reason=f"only {len(live)} of {len(attempts)} attempts were available",
            attempts=attempts, decision=None)

    pool = tuple(critics) or (judge,)
    by_role = {role: pool[i % len(pool)] for i, role in enumerate(
        (A.PROPOSER, A.CONTRARIAN, A.FAILURE_ANALYST, A.EVIDENCE_CHECKER))}
    by_role[A.JUDGE] = judge

    def infer(system, user_text, *, worker, objective_id):
        return by_role[worker.split(":", 1)[1]].infer(
            system, user_text, worker=worker, objective_id=objective_id)

    decision = A.deliberate(
        question, [*evidence, *(f"ATTEMPT {i}: {a.answer}" for i, a in enumerate(live, 1))],
        options=list(options) if options else None, objective_id=objective_id, infer=infer)
    disagreements = tuple(dict.fromkeys((*decision.disagreements, *decision.unresolved)))

    synthesis = ""
    if decision.verdict != UNAVAILABLE:
        brief = (f"{user}\n"
                 + "\n".join(f"ATTEMPT {i}: {a.answer}" for i, a in enumerate(live, 1))
                 + f"\nPANEL VERDICT: {decision.verdict}\nREASON: {decision.reason}\n"
                 + "".join(f"OPEN: {d}\n" for d in disagreements))
        try:
            raw = judge.infer(_SYNTHESIS_PROMPT, brief, worker=f"synthesis:{judge.name}",
                              objective_id=objective_id)
            synthesis = " ".join(A.parse_tagged(raw).get("SYNTHESIS", []))
        except Exception as exc:  # noqa: BLE001 - the verdict and disagreements stand without it
            logger.warning("synthesis by %s unavailable: %s", judge.name, exc)
    return PanelResult(question=question, verdict=decision.verdict, reason=decision.reason,
                       synthesis=synthesis, disagreements=disagreements, attempts=attempts,
                       decision=decision)


def deliberation(question: str, evidence: Sequence[str], *, providers: Sequence[Actor],
                 judge: Actor, critics: Sequence[Actor] = (),
                 options: Sequence[str] | None = None,
                 objective_id: str = "") -> PanelResult:
    """FAB-009: the judge panel with every attempt from a distinct provider,
    returning the verdict and the points still in dispute."""
    return judge_panel(question, evidence, producers=providers, judge=judge,
                       critics=critics, options=options, objective_id=objective_id,
                       distinct_models=True)


# ---------------------------------------------------------------------------
# Loop until dry
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class LoopResult:
    items: tuple[str, ...]
    rounds: int
    stopped: str                     # "dry" (K dry rounds in a row) | "cap" (max_rounds)


def loop_until_dry(find: Callable[[int, tuple[str, ...]], Iterable[str]], *, k: int,
                   max_rounds: int) -> LoopResult:
    """Call `find(round_no, found_so_far)` until K consecutive rounds return
    nothing not already found (compared ignoring case and spacing). A round
    that adds anything resets the count. `max_rounds` is the hard bound."""
    if k < 1:
        raise ValueError("k must be at least 1")
    if max_rounds < k:
        raise ValueError("max_rounds must be at least k or the loop can never go dry")
    items: list[str] = []
    seen: set[str] = set()
    dry = 0
    for round_no in range(1, max_rounds + 1):
        added = False
        for item in find(round_no, tuple(items)):
            key = _key(item)
            if key and key not in seen:
                seen.add(key)
                items.append(item)
                added = True
        dry = 0 if added else dry + 1
        if dry >= k:
            return LoopResult(items=tuple(items), rounds=round_no, stopped="dry")
    return LoopResult(items=tuple(items), rounds=max_rounds, stopped="cap")


# ---------------------------------------------------------------------------
# Completeness critic
# ---------------------------------------------------------------------------

_CRITIC_PROMPT = (
    "You are the COMPLETENESS CRITIC. You run last, after the verifiers. Do not "
    "re-verify what they checked: say what the task required that no result "
    "covers, and which stated claim has nothing behind it. Tagged lines "
    "only:\nVERDICT: <COMPLETE | INCOMPLETE>\nMISSING: <one per line, each "
    "concrete; NONE if none>\nCONFIDENCE: <0-100>")


@dataclass(frozen=True)
class CriticReport:
    critic: str
    model: str
    session: str
    verdict: str                     # COMPLETE | INCOMPLETE | INCONCLUSIVE
    missing: tuple[str, ...]
    confidence: int | None
    available: bool = True
    error: str = ""


def completeness_critic(task: str, results: Sequence[str], *, producer: Actor,
                        critic: Actor, objective_id: str = "") -> CriticReport:
    require_independent(producer, [critic])
    who = dict(critic=critic.name, model=critic.model, session=critic.session)
    user = (f"TASK: {task}\nRESULTS:\n"
            + ("\n".join(f"[R{i}] {r}" for i, r in enumerate(results, 1))
               or "(no results)"))
    try:
        raw = critic.infer(_CRITIC_PROMPT, user, worker=f"critic:{critic.name}",
                           objective_id=objective_id)
    except Exception as exc:  # noqa: BLE001 - an absent critic is INCONCLUSIVE, never COMPLETE
        logger.warning("completeness critic %s unavailable: %s", critic.name, exc)
        return CriticReport(**who, verdict=A.INCONCLUSIVE, missing=(), confidence=None,
                            available=False, error=str(exc)[:300])
    parsed = A.parse_tagged(raw)
    verdict = _first_word(parsed, "VERDICT", A.INCONCLUSIVE)
    if verdict not in (COMPLETE, INCOMPLETE):
        verdict = A.INCONCLUSIVE
    missing = tuple(parsed.get("MISSING", []))
    if verdict == COMPLETE and missing:
        verdict = INCOMPLETE         # a named gap beats a bare "complete"
    return CriticReport(**who, verdict=verdict, missing=missing,
                        confidence=A.confidence_of(parsed))


# ---------------------------------------------------------------------------
# One claim through the templates, critic last
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class VerificationReport:
    claim: str
    skeptics: SkepticReport | None
    perspectives: PerspectiveReport | None
    critic: CriticReport | None
    skipped: tuple[str, ...] = ()    # stages not run because the claim was already killed

    @property
    def passed(self) -> bool:
        """Every configured stage passed. INCONCLUSIVE and UNVERIFIED never do."""
        return ((self.skeptics is None or self.skeptics.status == SURVIVED)
                and (self.perspectives is None or self.perspectives.status == A.CONFIRMED)
                and (self.critic is None or self.critic.verdict == COMPLETE))


def _digest(skeptics: SkepticReport | None,
            perspectives: PerspectiveReport | None) -> list[str]:
    lines: list[str] = []
    if skeptics is not None:
        lines.append(f"skeptics: {skeptics.status} ({skeptics.refuted} refuted, "
                     f"{skeptics.held} held, {skeptics.unavailable} unavailable)")
        lines += [f"skeptic finding: {f}" for c in skeptics.checks for f in c.findings]
    if perspectives is not None:
        for c in perspectives.checks:
            lines.append(f"{c.perspective}: {c.verdict}")
            lines += [f"{c.perspective} finding: {f}" for f in c.findings]
    return lines


def verify(claim: str, evidence: Sequence[str], *, producer: Actor,
           skeptics: Sequence[Actor] = (), perspectives: Mapping[str, Actor] | None = None,
           critic: Actor | None = None, objective_id: str = "") -> VerificationReport:
    """Skeptics, then perspective verifiers, then the critic - always in that
    order, the critic last and seeing what the others found. Independence of
    every verifier is checked before the first inference. A claim the skeptics
    kill skips the later stages (and `skipped` names them): there is nothing
    left to complete."""
    if not skeptics and not perspectives:
        raise ValueError("verify needs skeptics or perspectives: nothing checks the claim")
    require_independent(producer, [*skeptics, *(perspectives or {}).values(),
                                   *([critic] if critic else [])])
    voted = (skeptic_vote(claim, evidence, producer=producer, skeptics=skeptics,
                          objective_id=objective_id) if skeptics else None)
    if voted is not None and voted.status == KILLED:
        skipped = tuple(name for name, configured in
                        (("perspectives", bool(perspectives)), ("critic", critic is not None))
                        if configured)
        return VerificationReport(claim=claim, skeptics=voted, perspectives=None,
                                  critic=None, skipped=skipped)
    checked = (perspective_check(claim, evidence, producer=producer,
                                 perspectives=perspectives, objective_id=objective_id)
               if perspectives else None)
    judged = (completeness_critic(claim, _digest(voted, checked), producer=producer,
                                  critic=critic, objective_id=objective_id)
              if critic else None)
    return VerificationReport(claim=claim, skeptics=voted, perspectives=checked, critic=judged)
