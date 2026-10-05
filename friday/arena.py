"""
Arenas: empirical promotion for every kind of candidate (EVAL-004).

EVAL-003 (`friday/route_benchmarks.py`) made one rule real for model routes: a
claim does not promote, a measurement does. This module is that rule with the
candidate abstracted away, so a skill, a compression backend, a research path or
a capability path is promoted the same way and by the same code.

An arena compares a set of candidates of one `ArenaKind`. A per-kind scorer
returns a `Score` for each: a gate table in the shape of `golden`'s
`summary()["gates"]` plus a number to rank by. `compare` / `build_report` turn
the scores into an `ArenaReport` (provenance, per-candidate result, winner),
`ArenaStore` keeps it, and `promote` says yes only for a candidate that passed
every gate in a stored report of the same kind that is still its latest.

Assumptions (revisit if wrong): the latest report for a (kind, candidate)
replaces the earlier one for promotion, so a regression demotes and a fix can
restore; earlier reports stay in the store by id, so a promotion's cited report
can still be read; a report does not expire. A candidate below target stays
usable, it is just never promoted; deciding what "usable" means is each
consumer's call, and the consumers wired so far (route benchmarks, skill
ladder, compression) all keep an unpromoted candidate available.
"""
from __future__ import annotations

import enum
import hashlib
import json
import logging
import math
import os
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Mapping, Sequence

log = logging.getLogger("friday.arena")


class ArenaKind(str, enum.Enum):
    MODEL_ROUTE = "model_route"
    SKILL = "skill"
    COMPRESSION = "compression"
    RESEARCH = "research"
    CAPABILITY_PATH = "capability_path"
    NEURAL_ADAPTOR = "neural_adaptor"


class ArenaError(RuntimeError):
    """Base of every arena failure."""


class ArenaStoreError(ArenaError):
    """The store file exists but cannot be read as arena evidence."""


class PromotionRefused(ArenaError):
    """There is no passing report from this arena behind the candidate."""


@dataclass(frozen=True)
class Score:
    """What a scorer says about one candidate: `gates` is name -> passed, the
    shape of `golden`'s `summary()["gates"]`; `score` ranks candidates that
    passed (higher wins)."""

    gates: Mapping[str, bool]
    cases: int
    score: float = 0.0
    metrics: Mapping[str, float] = field(default_factory=dict)


Scorer = Callable[[str], Score]


@dataclass(frozen=True)
class CandidateResult:
    candidate_id: str
    passed: bool
    failed_gates: tuple[str, ...]
    cases: int
    score: float
    metrics: dict

    def to_dict(self) -> dict:
        return {"candidate_id": self.candidate_id, "passed": self.passed,
                "failed_gates": list(self.failed_gates), "cases": self.cases,
                "score": self.score, "metrics": dict(sorted(self.metrics.items()))}

    @classmethod
    def from_dict(cls, row: dict) -> "CandidateResult":
        result = cls(candidate_id=str(row["candidate_id"]), passed=bool(row["passed"]),
                     failed_gates=tuple(str(g) for g in row["failed_gates"]),
                     cases=int(row["cases"]), score=float(row["score"]),
                     metrics={str(k): float(v) for k, v in dict(row["metrics"]).items()})
        if result.passed != (not result.failed_gates):
            raise ValueError(f"{result.candidate_id}: passed disagrees with its gates")
        return result


@dataclass(frozen=True)
class ArenaReport:
    """One comparison. `report_id` is a hash of the content, so it names exactly
    this result and a report edited after it was recorded no longer matches."""

    kind: ArenaKind
    results: tuple[CandidateResult, ...]
    winner: str
    commit: str
    date: str

    def _body(self) -> dict:
        return {"kind": self.kind.value, "commit": self.commit, "date": self.date,
                "winner": self.winner, "results": [r.to_dict() for r in self.results]}

    @property
    def report_id(self) -> str:
        blob = json.dumps(self._body(), sort_keys=True, separators=(",", ":"))
        return "arena-" + hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]

    def result(self, candidate_id: str) -> CandidateResult | None:
        return next((r for r in self.results if r.candidate_id == candidate_id), None)

    def to_dict(self) -> dict:
        return {"report_id": self.report_id, **self._body()}

    @classmethod
    def from_dict(cls, row: dict) -> "ArenaReport":
        report = cls(kind=ArenaKind(row["kind"]),
                     results=tuple(CandidateResult.from_dict(r) for r in row["results"]),
                     winner=str(row["winner"]), commit=str(row["commit"]), date=str(row["date"]))
        if report.report_id != row["report_id"]:
            raise ValueError(f"{row['report_id']} does not match its content")
        return report


def _result(candidate_id: str, score: Score) -> CandidateResult:
    if not isinstance(candidate_id, str) or not candidate_id.strip():
        raise ValueError("a candidate has a name")
    if not isinstance(score, Score):
        raise TypeError(f"a scorer returns a Score, got {type(score).__name__}")
    if not score.gates:
        raise ValueError(f"candidate {candidate_id!r} has no gates: nothing was "
                         "measured, so it cannot promote")
    value = float(score.score)
    metrics = {str(k): float(v) for k, v in score.metrics.items()}
    if score.cases < 0 or not all(math.isfinite(x) for x in (value, *metrics.values())):
        raise ValueError(f"candidate {candidate_id!r} has a score that is not a number")
    failed = tuple(sorted(name for name, ok in score.gates.items() if not ok))
    return CandidateResult(candidate_id=candidate_id, passed=not failed, failed_gates=failed,
                           cases=int(score.cases), score=value, metrics=metrics)


def _default_provenance() -> Mapping[str, object]:
    from friday import golden
    return golden.provenance()


def build_report(kind, scores: Mapping[str, Score], *,
                 provenance: Mapping[str, object] | None = None) -> ArenaReport:
    """The report for scores already in hand. Pass and fail come from each gate
    table, never from a flag; the winner is the highest score among candidates
    that passed, the earliest of equals, and empty when none passed."""
    kind = ArenaKind(kind)
    if not scores:
        raise ValueError("an arena compares at least one candidate")
    results = tuple(_result(cid, score) for cid, score in scores.items())
    best: CandidateResult | None = None
    for r in results:
        if r.passed and (best is None or r.score > best.score):
            best = r
    prov = _default_provenance() if provenance is None else provenance
    return ArenaReport(kind=kind, results=results, winner=best.candidate_id if best else "",
                       commit=str(prov.get("commit", "")), date=str(prov.get("date", "")))


def compare(kind, candidate_ids: Sequence[str], scorer: Scorer, *,
            provenance: Mapping[str, object] | None = None) -> ArenaReport:
    """Score every candidate with `scorer` and report. List the incumbent first:
    it keeps a tie. A scorer that raises fails that candidate (gate `scored`) and
    the comparison goes on, because a candidate is often third-party code and one
    broken one must not stop the others being measured."""
    ids = list(candidate_ids)
    if not ids or len(set(ids)) != len(ids):
        raise ValueError("an arena compares a non-empty set of distinct candidates")
    scores: dict[str, Score] = {}
    for cid in ids:
        try:
            scores[cid] = scorer(cid)
        except Exception as exc:  # a candidate is third-party: its failure is its result
            log.warning("arena: scoring %s failed: %s", cid, type(exc).__name__)
            scores[cid] = Score(gates={"scored": False}, cases=0)
    return build_report(kind, scores, provenance=provenance)


def replay_scorer(cases: Sequence, check: Callable[[str, object], bool], *,
                  minimum_cases: int, success_target: float = 1.0) -> Scorer:
    """The scorer for kinds measured by replaying cases: a candidate passes when
    enough cases were run and enough of them succeeded; its score is the success
    rate. The same baseline-versus-candidate shape as `context_lab.run()`."""
    cases = list(cases)

    def scorer(candidate_id: str) -> Score:
        ok = sum(1 for case in cases if check(candidate_id, case))
        rate = ok / len(cases) if cases else 0.0
        return Score(gates={"minimum_cases": len(cases) >= minimum_cases,
                            "success_rate": rate >= success_target},
                     cases=len(cases), score=rate, metrics={"success_rate": rate})
    return scorer


_LOCK = threading.RLock()


class ArenaStore:
    """Reports by id, and the latest report per (kind, candidate), optionally
    persisted as JSON.

    With no `path` it is in memory only. The file is read on every call, so a
    report recorded by another process is seen without a restart. Writes are an
    atomic replace under one process-wide lock; two processes recording at once
    can still lose one report, as in EVAL-003. Every report is kept: they are the
    evidence behind promotions already made, and there is one per arena run.
    """

    def __init__(self, path: Path | str | None = None):
        self.path = Path(path) if path else None
        self._memory: tuple[dict[str, ArenaReport], dict[str, str]] = ({}, {})

    @staticmethod
    def _key(kind: ArenaKind, candidate_id: str) -> str:
        return f"{kind.value}/{candidate_id}"

    def _load(self) -> tuple[dict[str, ArenaReport], dict[str, str]]:
        if self.path is None:
            return self._memory
        try:
            raw = self.path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return {}, {}
        except OSError as exc:
            raise ArenaStoreError(f"cannot read {self.path}: {exc}") from exc
        try:
            data = json.loads(raw)
            reports = {}
            for rid, row in data["reports"].items():
                report = ArenaReport.from_dict(row)
                if report.report_id != rid:
                    raise ValueError(f"{rid} is filed under another report's id")
                reports[rid] = report
            latest = {str(k): str(v) for k, v in data["latest"].items()}
            for key, rid in latest.items():
                kind, _, candidate_id = key.partition("/")
                held = reports[rid]
                if held.kind.value != kind or held.result(candidate_id) is None:
                    raise ValueError(f"{key} points at a report that does not list it")
        except (ValueError, KeyError, TypeError, AttributeError) as exc:
            raise ArenaStoreError(f"{self.path} is not arena evidence: {exc!r}") from exc
        return reports, latest

    def report(self, report_id: str) -> ArenaReport | None:
        with _LOCK:
            return self._load()[0].get(report_id)

    def latest(self, kind, candidate_id: str) -> ArenaReport | None:
        """The newest report that listed this candidate, or None."""
        key = self._key(ArenaKind(kind), candidate_id)
        with _LOCK:
            reports, latest = self._load()
            return reports[latest[key]] if key in latest else None

    def record(self, report: ArenaReport) -> ArenaReport:
        with _LOCK:
            reports, latest = self._load()
            reports, latest = dict(reports), dict(latest)
            reports[report.report_id] = report
            for r in report.results:
                latest[self._key(report.kind, r.candidate_id)] = report.report_id
            if self.path is None:
                self._memory = (reports, latest)
                return report
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_name(self.path.name + ".tmp")
            tmp.write_text(json.dumps(
                {"reports": {rid: r.to_dict() for rid, r in reports.items()},
                 "latest": latest}, indent=1), encoding="utf-8")
            os.replace(tmp, self.path)
            return report


_STORE: ArenaStore | None = None


def default_path() -> Path:
    override = os.getenv("FRIDAY_ARENA_REPORTS")
    if override:
        return Path(override)
    from friday.config import DATA_DIR
    return DATA_DIR / "arena_reports.json"


def default_store() -> ArenaStore:
    """The process's arena store."""
    global _STORE
    if _STORE is None:
        _STORE = ArenaStore(default_path())
    return _STORE


def reset_store(replacement: ArenaStore | None = None) -> None:
    """Point the process at another store (tests, a bench); None restores the
    default on next use."""
    global _STORE
    _STORE = replacement


@dataclass(frozen=True)
class Promotion:
    """The evidence a promotion stands on."""

    kind: ArenaKind
    candidate_id: str
    report_id: str
    commit: str
    date: str


def promote(kind, candidate_id: str, *, report_id: str, store: ArenaStore | None = None,
            exclusive: bool = False) -> Promotion:
    """The `Promotion` for a candidate, or `PromotionRefused` saying why not.

    Yes needs a stored report of this kind, naming this candidate, in which it
    passed every gate, and that is still the candidate's latest. `exclusive` is
    for kinds with one default (a compression backend): the candidate must also
    be that report's winner. A declaration, a flag or free text has no way in; an
    unreadable store refuses rather than reading as empty.
    """
    kind = ArenaKind(kind)
    if not (report_id or "").strip():
        raise PromotionRefused("a declaration alone does not qualify; "
                               "an arena report id is required")
    if store is None:
        store = default_store()
    try:
        report = store.report(report_id)
        latest = store.latest(kind, candidate_id)
    except ArenaStoreError as exc:
        raise PromotionRefused(f"arena store unreadable; not promoted: {exc}") from exc
    if report is None:
        raise PromotionRefused(f"no arena report {report_id!r}")
    if report.kind is not kind:
        raise PromotionRefused(f"{report_id} is a {report.kind.value} report, not {kind.value}")
    result = report.result(candidate_id)
    if result is None:
        raise PromotionRefused(f"{report_id} did not measure {candidate_id!r}")
    if not result.passed:
        raise PromotionRefused(f"{candidate_id!r} measured below target "
                               f"({', '.join(result.failed_gates)}) in {report_id}")
    if latest is None or latest.report_id != report_id:
        raise PromotionRefused(f"{report_id} is superseded by a later report for {candidate_id!r}")
    if exclusive and report.winner != candidate_id:
        raise PromotionRefused(f"{candidate_id!r} passed but is not the winner of {report_id} "
                               f"({report.winner or 'none'})")
    return Promotion(kind=kind, candidate_id=candidate_id, report_id=report_id,
                     commit=report.commit, date=report.date)
