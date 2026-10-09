"""
Model Scout (FAB-006, extends SCOUT-001 and MODEL-004): a newly listed model is
recorded, probed, benchmarked against the champion, and promoted only where it
wins - or archived.

    listing -> record -> compatibility probe (model_profiling) ->
    per-task-class benchmark against the champion on held-out cases (arena) ->
    PROMOTED for the classes it beats the champion on, else ARCHIVED

Event-driven and bounded: nothing runs until `scout()` is called with a listing
(no polling, no model call while idle); a (provider, model, version) already
decided is returned from the ledger without a probe; at most `max_cases` cases
run per task class per route.

What the scout does not own, and takes as seams so an absent one is reported
rather than faked:

    completer   how a probe reaches the route (`model_profiling.Completer`).
                None -> UNAVAILABLE, as does a route that cannot be reached.
    check       `check(route_id, case) -> bool`: did the route handle the case.
                The gateway-backed version of it is the next wiring step.
    held_out    the cases per task class. The scout cannot tell whether they
                were held out; the caller owns that.

Promotion here is per (route, task class) and advisory: it is evidence for the
router, not clearance to carry any data. It never widens where a request may go -
a `local_only` request is refused by `model_gateway` when its private route
fails, whatever the scout says about a free endpoint.

Assumptions (revisit if wrong): "beats the champion" is a strictly higher
success rate on the bounded case set (a tie keeps the champion; `arena.compare`
lists the champion first); every benchmarked class needs a named champion, so a
model cannot be promoted into a class nobody currently serves; a new version of
a model is a new listing and is scouted again.
"""
from __future__ import annotations

import json
import os
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Mapping, Sequence

from friday import arena
from friday import model_gateway
from friday import model_profiling

PROMOTED = "PROMOTED"
ARCHIVED = "ARCHIVED"
UNAVAILABLE = "UNAVAILABLE"
#: Outcomes that settle a listing; UNAVAILABLE does not, so it is retried.
_DECIDED = (PROMOTED, ARCHIVED)

DEFAULT_MAX_CASES = 8
MIN_CASES = 3

Check = Callable[[str, object], bool]


class ScoutStoreError(RuntimeError):
    """The ledger file exists but cannot be read as scout records."""


@dataclass(frozen=True)
class ModelListing:
    """What a provider says about a model it has just listed."""

    model_id: str
    provider: str
    licence: str
    capabilities: tuple[str, ...] = ()
    price: str = ""
    context: int | None = None
    version: str = ""
    #: The registry backend the route is served on; defaults to the provider.
    backend: str = ""

    def __post_init__(self):
        if not self.model_id.strip() or not self.provider.strip():
            raise ValueError("a listing names its model and its provider")

    @property
    def route_id(self) -> str:
        return f"{self.provider}/{self.model_id}"


@dataclass(frozen=True)
class ScoutRecord:
    listing: ModelListing
    outcome: str
    reason: str
    probe_status: str = ""
    promoted_for: tuple[str, ...] = ()
    #: task class -> arena report id of the comparison behind the verdict
    reports: Mapping[str, str] = field(default_factory=dict)
    scouted_at: str = ""

    def to_dict(self) -> dict:
        row = {k: getattr(self, k) for k in
               ("outcome", "reason", "probe_status", "scouted_at")}
        row.update(listing=self.listing.__dict__ | {"capabilities": list(self.listing.capabilities)},
                   promoted_for=list(self.promoted_for), reports=dict(self.reports))
        return row

    @classmethod
    def from_dict(cls, row: dict) -> "ScoutRecord":
        listing = dict(row["listing"])
        listing["capabilities"] = tuple(listing["capabilities"])
        return cls(listing=ModelListing(**listing), outcome=str(row["outcome"]),
                   reason=str(row["reason"]), probe_status=str(row["probe_status"]),
                   promoted_for=tuple(row["promoted_for"]), reports=dict(row["reports"]),
                   scouted_at=str(row["scouted_at"]))


_LOCK = threading.RLock()


def default_path() -> Path:
    override = os.getenv("FRIDAY_MODEL_SCOUT")
    if override:
        return Path(override)
    from friday.config import DATA_DIR
    return DATA_DIR / "model_scout.json"


class ModelScout:
    """The scout and its ledger: the latest record per route, as JSON, read on
    every call and replaced atomically on write (the `arena` store's discipline)."""

    def __init__(self, path: Path | str | None = None, *, max_cases: int = DEFAULT_MAX_CASES,
                 provenance: Mapping[str, object] | None = None):
        self.path = Path(path) if path else default_path()
        self.max_cases = max_cases
        self._provenance = provenance

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
            raise ScoutStoreError(f"{self.path} is not scout evidence: {exc!r}") from exc

    def record(self, route_id: str) -> ScoutRecord | None:
        with _LOCK:
            return self._load().get(route_id)

    def promoted_classes(self, provider: str, model_id: str) -> tuple[str, ...]:
        rec = self.record(f"{provider}/{model_id}")
        return rec.promoted_for if rec and rec.outcome == PROMOTED else ()

    def is_promoted(self, provider: str, model_id: str, task_class: str) -> bool:
        return task_class in self.promoted_classes(provider, model_id)

    def _save(self, record: ScoutRecord) -> ScoutRecord:
        with _LOCK:
            records = self._load()
            records[record.listing.route_id] = record
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_name(self.path.name + ".tmp")
            tmp.write_text(json.dumps({"records": {k: r.to_dict() for k, r in records.items()}},
                                      indent=1), encoding="utf-8")
            os.replace(tmp, self.path)
        return record

    def scout(self, listing: ModelListing, *, completer: model_profiling.Completer | None,
              held_out: Mapping[str, Sequence], champions: Mapping[str, str],
              check: Check) -> ScoutRecord:
        """Decide one listing. Raises ValueError for a task class that does not
        exist or has no champion: those are the caller's mistakes, not verdicts."""
        for task_class in held_out:
            if task_class not in model_gateway.TASK_CLASSES:
                raise ValueError(f"unknown task class {task_class!r}; "
                                 f"known: {list(model_gateway.TASK_CLASSES)}")
            if not champions.get(task_class):
                raise ValueError(f"task class {task_class!r} has no champion to beat")
        prior = self.record(listing.route_id)
        if prior and prior.outcome in _DECIDED and prior.listing.version == listing.version:
            return prior

        def finish(outcome, reason, probe_status="", promoted_for=(), reports=None):
            return self._save(ScoutRecord(
                listing=listing, outcome=outcome, reason=reason, probe_status=probe_status,
                promoted_for=tuple(promoted_for), reports=reports or {},
                scouted_at=datetime.now(timezone.utc).isoformat(timespec="seconds")))

        if completer is None:
            return finish(UNAVAILABLE, "no probe route is configured; nothing was run")
        profile = model_profiling.profile_route(
            listing.backend or listing.provider, listing.model_id, completer,
            provider=listing.provider)
        if profile.status == "unreachable":
            return finish(UNAVAILABLE, "the route could not be reached; will be retried",
                          profile.status)
        if profile.status != "profiled":
            return finish(ARCHIVED, f"failed the compatibility probe ({profile.status})",
                          profile.status)

        promoted, reports = [], {}
        for task_class, cases in held_out.items():
            champion = champions[task_class]
            scorer = arena.replay_scorer(list(cases)[: self.max_cases], check,
                                         minimum_cases=MIN_CASES, success_target=0.0)
            report = arena.compare(arena.ArenaKind.MODEL_ROUTE, [champion, listing.route_id],
                                   scorer, provenance=self._provenance)
            reports[task_class] = report.report_id
            if report.winner == listing.route_id:
                promoted.append(task_class)
        if promoted:
            return finish(PROMOTED, "beat the champion on " + ", ".join(promoted),
                          profile.status, promoted, reports)
        return finish(ARCHIVED, "did not beat the champion on any benchmarked task class",
                      profile.status, (), reports)
