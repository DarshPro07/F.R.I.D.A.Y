"""
Technology Scout (SCOUT-001): adoption follows a measured need, never a trend.

    candidate -> measured-need gate -> provenance / licence review ->
    quarantine intake (a seam)

A candidate (a repository, model, CLI, context method, memory system, skill or
MCP server) is recorded with its popularity, and popularity changes nothing. It
passes the need gate only when the caller supplies a `MeasuredNeed`: a named
metric that is currently short of its requirement, with a reference to the
measurement. It then has to be pinned to a commit and carry a recognised licence
(copyleft only behind a process boundary, the fabric's own rule). The outcome of
all that is a hand-off to the quarantine intake, never an install, an import or
a fabric activation: the scout has no code path that puts third-party code on
the machine.

    ARCHIVED     no measured need (or the need is already met); recorded, kept
    REJECTED     failed review (unpinned, unknown licence, copyleft in-process)
    QUARANTINED  reviewed and handed to the intake, which owns what happens next
    UNAVAILABLE  no intake is configured, or it failed; retried on the next call

Event-driven and bounded: nothing runs until `consider()` is called; there is no
polling and no model call. A candidate already QUARANTINED is returned from the
ledger without a second intake.

Seams, so an absent one is reported rather than faked:

    intake      `intake(candidate) -> quarantine_ref`. The forge/quarantine
                intake (CAP-FORGE-004/014) is its real caller. None -> UNAVAILABLE.
    need        the caller owns the measurement. The scout checks the shortfall
                and that evidence is named; it cannot verify the evidence itself.

Not built yet (SCOUT-001 slices): the detect and search front end, the research
step, the post-quarantine adopt/adapt/build decision, and the scheduler.

Assumptions (revisit if wrong): the ledger is a JSON file like `model_scout`'s;
a licence is recognised if it is a common permissive SPDX id or a GPL/AGPL one;
LGPL, MPL and the like are rejected as unrecognised until the owner rules.
"""
from __future__ import annotations

import json
import os
import re
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from friday import provider_states

ARCHIVED = "ARCHIVED"
REJECTED = "REJECTED"
QUARANTINED = "QUARANTINED"
UNAVAILABLE = "UNAVAILABLE"

KINDS = ("repo", "model", "cli", "context_method", "memory", "skill", "mcp")

_PERMISSIVE = frozenset({"mit", "apache-2.0", "bsd-2-clause", "bsd-3-clause", "isc"})
_COPYLEFT = re.compile(r"^a?gpl-[23]")
_SHA = re.compile(r"^[0-9a-f]{40}$")

Intake = Callable[["Candidate"], str]


class ScoutStoreError(RuntimeError):
    """The ledger file exists but cannot be read as tech-scout records."""


@dataclass(frozen=True)
class Candidate:
    name: str
    kind: str
    source: str
    licence: str
    commit: str
    #: Recorded, never weighed.
    stars: int = 0
    #: How it would be integrated if adopted (a `fabric` integration mode).
    integration_mode: str = provider_states.REFERENCE_ONLY

    def __post_init__(self):
        if not self.name.strip() or not self.source.strip():
            raise ValueError("a candidate names itself and its source")
        if self.kind not in KINDS:
            raise ValueError(f"unknown kind {self.kind!r}; known: {list(KINDS)}")
        if self.integration_mode not in provider_states.INTEGRATION_MODES:
            raise ValueError(f"unknown integration mode {self.integration_mode!r}")

    @property
    def candidate_id(self) -> str:
        return f"{self.source}@{self.commit}"


@dataclass(frozen=True)
class MeasuredNeed:
    """A metric that is short of what is required, and where it was measured."""

    metric: str
    observed: float
    required: float
    evidence: str
    higher_is_better: bool = True

    def __post_init__(self):
        if not self.metric.strip() or not self.evidence.strip():
            raise ValueError("a measured need names its metric and its evidence")

    @property
    def unmet(self) -> bool:
        if self.higher_is_better:
            return self.observed < self.required
        return self.observed > self.required


@dataclass(frozen=True)
class ScoutRecord:
    candidate: Candidate
    outcome: str
    reason: str
    need: MeasuredNeed | None = None
    quarantine_ref: str = ""
    decided_at: str = ""

    def to_dict(self) -> dict:
        return {"candidate": self.candidate.__dict__, "outcome": self.outcome,
                "reason": self.reason, "quarantine_ref": self.quarantine_ref,
                "decided_at": self.decided_at,
                "need": self.need.__dict__ if self.need else None}

    @classmethod
    def from_dict(cls, row: dict) -> "ScoutRecord":
        need = row["need"]
        return cls(candidate=Candidate(**row["candidate"]), outcome=str(row["outcome"]),
                   reason=str(row["reason"]), quarantine_ref=str(row["quarantine_ref"]),
                   decided_at=str(row["decided_at"]),
                   need=MeasuredNeed(**need) if need else None)


_LOCK = threading.RLock()


def default_path() -> Path:
    override = os.getenv("FRIDAY_TECH_SCOUT")
    if override:
        return Path(override)
    from friday.config import DATA_DIR
    return DATA_DIR / "tech_scout.json"


def review(candidate: Candidate) -> str:
    """Why the candidate fails provenance/licence review, or "" if it passes."""
    if not _SHA.match(candidate.commit):
        return "not pinned to a full commit sha; an unpinned upstream has no audit"
    licence = candidate.licence.strip().lower()
    if licence in _PERMISSIVE:
        return ""
    if _COPYLEFT.match(licence):
        if candidate.integration_mode in provider_states.ISOLATED_MODES:
            return ""
        return (f"{candidate.licence} is copyleft and needs an isolated mode "
                f"({sorted(provider_states.ISOLATED_MODES)}), not {candidate.integration_mode}")
    return f"unrecognised or missing licence {candidate.licence!r}"


class TechScout:
    """The scout and its ledger: the latest record per candidate, as JSON, read on
    every call and replaced atomically on write."""

    def __init__(self, path: Path | str | None = None):
        self.path = Path(path) if path else default_path()

    def _load(self) -> dict[str, ScoutRecord]:
        try:
            raw = self.path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return {}
        except OSError as exc:
            raise ScoutStoreError(f"cannot read {self.path}: {exc}") from exc
        try:
            return {str(k): ScoutRecord.from_dict(v) for k, v in json.loads(raw)["records"].items()}
        except (ValueError, KeyError, TypeError) as exc:
            raise ScoutStoreError(f"{self.path} is not tech-scout evidence: {exc!r}") from exc

    def record(self, candidate_id: str) -> ScoutRecord | None:
        with _LOCK:
            return self._load().get(candidate_id)

    def _save(self, record: ScoutRecord) -> ScoutRecord:
        with _LOCK:
            records = self._load()
            records[record.candidate.candidate_id] = record
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_name(self.path.name + ".tmp")
            tmp.write_text(json.dumps({"records": {k: r.to_dict() for k, r in records.items()}},
                                      indent=1), encoding="utf-8")
            os.replace(tmp, self.path)
        return record

    def consider(self, candidate: Candidate, *, need: MeasuredNeed | None,
                 intake: Intake | None) -> ScoutRecord:
        """Decide one candidate. Nothing is installed, imported or activated."""
        prior = self.record(candidate.candidate_id)
        if prior and prior.outcome == QUARANTINED:
            return prior

        def finish(outcome, reason, quarantine_ref=""):
            return self._save(ScoutRecord(
                candidate=candidate, outcome=outcome, reason=reason, need=need,
                quarantine_ref=quarantine_ref,
                decided_at=datetime.now(timezone.utc).isoformat(timespec="seconds")))

        if need is None:
            return finish(ARCHIVED, "no measured need; popularity is not a need")
        if not need.unmet:
            return finish(ARCHIVED, f"no measured need: {need.metric} already meets "
                                    f"{need.required} (observed {need.observed})")
        problem = review(candidate)
        if problem:
            return finish(REJECTED, problem)
        if intake is None:
            return finish(UNAVAILABLE, "no quarantine intake is configured; nothing was taken in")
        try:
            ref = intake(candidate)
        except Exception as exc:  # the intake is a seam; its failure is reported, then retried
            return finish(UNAVAILABLE, f"quarantine intake failed: {exc}")
        return finish(QUARANTINED, f"measured need on {need.metric}; handed to quarantine", ref)
