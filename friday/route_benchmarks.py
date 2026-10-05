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

Assumptions (revisit if wrong): the latest report for a route replaces the
earlier one, so a regression demotes a route and a fix can restore it; a
report does not expire; a route that measured below target is demoted (kept
usable, never promoted) rather than refused, because the gates are aggregate
and a refusal would take every request on that route offline. A caller that
needs proof sets `ModelGatewayRequest.require_measured` and gets a refusal.
"""
from __future__ import annotations

import json
import os
import threading
from dataclasses import asdict, dataclass
from pathlib import Path


class BenchmarkStoreError(RuntimeError):
    """The store file exists but cannot be read as benchmark evidence."""


@dataclass(frozen=True)
class Measurement:
    """What the latest benchmark run said about one route."""

    provider: str
    model: str
    passed: bool
    failed_gates: tuple[str, ...]
    cases: int
    success_rate: float
    commit: str
    measured_at: str

    def to_dict(self) -> dict:
        row = asdict(self)
        row["failed_gates"] = list(self.failed_gates)
        return row

    @classmethod
    def from_dict(cls, row: dict) -> "Measurement":
        return cls(provider=str(row["provider"]), model=str(row["model"]),
                   passed=bool(row["passed"]),
                   failed_gates=tuple(row["failed_gates"]),
                   cases=int(row["cases"]),
                   success_rate=float(row["success_rate"]),
                   commit=str(row["commit"]),
                   measured_at=str(row["measured_at"]))


def _key(provider: str, model: str) -> str:
    return f"{provider}/{model}"


def measurement_from_report(provider: str, model: str, report) -> Measurement:
    """Reduce a `golden.Report` (or its `to_dict()`) to a Measurement.

    `passed` is derived from the gate table, not read from the report's
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
    if not gates:
        raise ValueError("a golden report with no gates cannot promote a route")
    failed = tuple(sorted(name for name, ok in gates.items() if not ok))
    return Measurement(
        provider=provider, model=model, passed=not failed, failed_gates=failed,
        cases=int(metrics.get("cases", 0)),
        success_rate=float(metrics.get("success_rate", 0.0)),
        commit=str(provenance.get("commit", "")),
        measured_at=str(provenance.get("date", "")))


class BenchmarkStore:
    """Latest measurement per route, optionally persisted as JSON.

    With no `path` it is in memory only. The file is read on every `get`, so
    a report recorded by `python -m friday.golden --route ...` in another
    process is seen by a running gateway without a restart.
    """

    def __init__(self, path: Path | str | None = None):
        self.path = Path(path) if path else None
        self._lock = threading.Lock()
        self._rows: dict[str, Measurement] = {}

    def _load(self) -> dict[str, Measurement]:
        if self.path is None:
            return self._rows
        try:
            raw = self.path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return {}
        except OSError as exc:
            raise BenchmarkStoreError(f"cannot read {self.path}: {exc}") from exc
        try:
            return {k: Measurement.from_dict(v) for k, v in json.loads(raw).items()}
        except (ValueError, KeyError, TypeError, AttributeError) as exc:
            raise BenchmarkStoreError(f"{self.path} is not benchmark evidence: {exc!r}") from exc

    def get(self, provider: str, model: str) -> Measurement | None:
        with self._lock:
            return self._load().get(_key(provider, model))

    def record(self, measurement: Measurement) -> Measurement:
        with self._lock:
            rows = dict(self._load())
            rows[_key(measurement.provider, measurement.model)] = measurement
            if self.path is None:
                self._rows = rows
                return measurement
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_name(self.path.name + ".tmp")
            tmp.write_text(json.dumps({k: m.to_dict() for k, m in rows.items()},
                                      indent=1), encoding="utf-8")
            os.replace(tmp, self.path)
            return measurement

    def record_report(self, provider: str, model: str, report) -> Measurement:
        return self.record(measurement_from_report(provider, model, report))


_STORE: BenchmarkStore | None = None


def default_path() -> Path:
    override = os.getenv("FRIDAY_ROUTE_BENCHMARKS")
    if override:
        return Path(override)
    from friday.config import DATA_DIR
    return DATA_DIR / "route_benchmarks.json"


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
