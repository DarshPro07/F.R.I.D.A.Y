"""
Measured benchmark evidence for model routes (EVAL-003, PRD Model Arena).

`friday/model_capabilities.py` records what a vendor *says* a model can do,
and `model_gateway.capability_verdict` filters on that. A claim is not a
measurement: a route declared capable has not been shown to complete a
single objective. This store holds the other half - the outcome of a
Golden Objective run (`golden.Report`) against one route - so routing can
tell a route that has been measured from one that has only been described.

The rule it exists to enforce: a vendor claim alone cannot promote a route.
Only a report whose every gate passed does. Pass and fail are read from the
report's own `summary()["gates"]`, which `golden.TARGETS` and
`golden.MINIMUM_CASES` already define; thresholds are not restated here.

Since EVAL-004 this is the `model_route` adapter of `friday/arena.py`, the one
owner of that logic: a golden report becomes a single-candidate `ArenaReport`
filed under the key `provider/model`, so the same store, report ids and
latest-replaces rule serve every arena kind. The public API is unchanged.

Assumptions (revisit if wrong): the latest report for a route replaces the
earlier one, so a regression demotes a route and a fix can restore it; a
report does not expire; a route that measured below target is demoted (kept
usable, never promoted) rather than refused, because the gates are aggregate
and a refusal would take every request on that route offline. A caller that
needs proof sets `ModelGatewayRequest.require_measured` and gets a refusal.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from friday import arena


class BenchmarkStoreError(RuntimeError):
    """The store file exists but cannot be read as benchmark evidence."""


@dataclass(frozen=True)
class Measurement:
    """What the latest benchmark run said about one route. `report_id` names the
    arena report it was read from."""

    provider: str
    model: str
    passed: bool
    failed_gates: tuple[str, ...]
    cases: int
    success_rate: float
    commit: str
    measured_at: str
    report_id: str = ""


def _key(provider: str, model: str) -> str:
    return f"{provider}/{model}"


def _route_report(provider: str, model: str, report) -> arena.ArenaReport:
    """Reduce a `golden.Report` (or its `to_dict()`) to a one-route arena report.

    Pass is derived from the gate table, not read from the report's
    `all_gates_pass` flag, so a report whose flag and gates disagree cannot
    promote a route; an empty gate table is refused outright because "no
    gates failed" over zero gates proves nothing.
    """
    if not provider or not model:
        raise ValueError("a benchmark measures one route: provider and model are required")
    payload = report.to_dict() if hasattr(report, "to_dict") else report
    try:
        summary = payload["summary"]
        gates = dict(summary["gates"])
        metrics = summary["metrics"]
        provenance = payload.get("provenance") or {}
    except (KeyError, TypeError) as exc:
        raise ValueError(f"not a golden report: missing {exc}") from exc
    success_rate = float(metrics.get("success_rate", 0.0))
    score = arena.Score(gates={name: bool(ok) for name, ok in gates.items()},
                        cases=int(metrics.get("cases", 0)), score=success_rate,
                        metrics={"success_rate": success_rate})
    return arena.build_report(arena.ArenaKind.MODEL_ROUTE, {_key(provider, model): score},
                              provenance=provenance)


def _measurement(provider: str, model: str, report: arena.ArenaReport) -> Measurement:
    result = report.result(_key(provider, model))
    return Measurement(
        provider=provider, model=model, passed=result.passed,
        failed_gates=result.failed_gates, cases=result.cases,
        success_rate=result.metrics.get("success_rate", 0.0),
        commit=report.commit, measured_at=report.date, report_id=report.report_id)


class BenchmarkStore:
    """Latest measurement per route, optionally persisted as JSON.

    With no `path` it is in memory only. The file is read on every `get`, so
    a report recorded by `python -m friday.golden --route ...` in another
    process is seen by a running gateway without a restart.
    """

    def __init__(self, path: Path | str | None = None):
        self.path = Path(path) if path else None
        self._arena = arena.ArenaStore(self.path)

    def get(self, provider: str, model: str) -> Measurement | None:
        try:
            report = self._arena.latest(arena.ArenaKind.MODEL_ROUTE, _key(provider, model))
        except arena.ArenaStoreError as exc:
            raise BenchmarkStoreError(str(exc)) from exc
        return None if report is None else _measurement(provider, model, report)

    def record_report(self, provider: str, model: str, report) -> Measurement:
        filed = _route_report(provider, model, report)
        try:
            self._arena.record(filed)
        except arena.ArenaStoreError as exc:
            raise BenchmarkStoreError(str(exc)) from exc
        return _measurement(provider, model, filed)


_STORE: BenchmarkStore | None = None


def default_path() -> Path:
    override = os.getenv("FRIDAY_ROUTE_BENCHMARKS")
    return Path(override) if override else arena.default_path()


def store() -> BenchmarkStore:
    """The process's benchmark store."""
    global _STORE
    if _STORE is None:
        _STORE = BenchmarkStore(default_path())
    return _STORE


def reset_store(replacement: BenchmarkStore | None = None) -> None:
    """Point the process at another store (tests, a bench); None restores
    the default on next use."""
    global _STORE
    _STORE = replacement
