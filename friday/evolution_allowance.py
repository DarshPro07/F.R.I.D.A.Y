"""
The Evolution Budget Governor: self-evolution's own allowance (EVOLAB-002).

Amendment A18 (docs/plans/build-harness/00-owner-directive-2026-10-09.md): the
owner keeps no daily spend cap, so evolution is not held back by one. It gets
an allowance of its own, in three separate dimensions per period:

    money   US dollars
    ram     GB-seconds held (gigabytes resident x seconds held)
    time    wall-clock seconds

What this module adds, over EVO-006's admission and stops (`evolution_governor`):

  * a durable spend ledger of its own (`<root>/spend.jsonl`, one labelled record
    per spend: id, objective id, dimension, amount, period). The reported total
    of a dimension is the sum of those records, nothing else. It never reads or
    writes the objective budget, the model-gateway telemetry or any other
    ledger, so an exhausted evolution allowance cannot borrow from the person's
    work or from another dimension, and the person's own task budget is the
    same before and after any evolution spend;
  * `authorize_step`: before an experiment step, refuse it, and archive the
    refusal in the Evolution Archive (EVO-002) as REFUSED, when any one
    dimension is used up, or when no trigger caused it (idle research with no
    trigger spends nothing);
  * `charge`: spend is drawn only on an admitted evolution lease the governor
    still holds;
  * `yield_to_foreground`: when a foreground task asks to start, every
    evolution lease is stopped and released at once, synchronously, so the
    foreground admit never waits on evolution. (Evolution's lease is OPTIONAL
    work, which the governor never counts against a foreground worker.)

Assumptions, stated rather than asked (revisit if wrong):
  * the period is the UTC calendar day of the `now` the caller passes;
  * the defaults below are placeholders for the owner to set, not rulings;
  * an allowance is exhausted when the spend reaches the limit (`>=`); a step
    already running may overshoot it, because real spend is recorded as it is,
    and the next step is refused.
"""
from __future__ import annotations

import json
import math
import os
import threading
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from friday import evolution_archive as A
from friday import evolution_governor as E
from friday import governor as G
from friday import objectives as O
from friday import productivity
from friday.config import DATA_DIR

MONEY = "money"
RAM = "ram"
TIME = "time"
DIMENSIONS = (MONEY, RAM, TIME)
UNITS = {MONEY: "usd", RAM: "gb_s", TIME: "s"}

REASON_NO_TRIGGER = "no_trigger"
REASON_EXHAUSTED = "allowance_exhausted"

_LEDGER_LOCK = threading.RLock()


class AllowanceError(ValueError):
    """A limit, a spend or a charge is malformed, or the ledger is damaged."""


@dataclass(frozen=True)
class AllowanceLimits:
    """What evolution may spend per period, per dimension."""

    usd: float = 5.0
    ram_gb_s: float = 4.0 * 3600
    wall_s: float = 2.0 * 3600

    def __post_init__(self) -> None:
        for name in ("usd", "ram_gb_s", "wall_s"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, (int, float)) \
                    or not math.isfinite(value) or value < 0:
                raise AllowanceError(f"{name} must be a finite number >= 0, not {value!r}")

    def of(self, dimension: str) -> float:
        return {MONEY: self.usd, RAM: self.ram_gb_s, TIME: self.wall_s}[dimension]


@dataclass
class StepDecision:
    allowed: bool
    reason: str = ""
    dimension: str = ""
    archived: str = ""          # the archive record id of a refusal


def period_of(now: datetime | None = None) -> str:
    return (now or datetime.now(timezone.utc)).astimezone(timezone.utc).date().isoformat()


def _amount(dimension: str, value) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) \
            or not math.isfinite(value) or value < 0:
        raise AllowanceError(f"{dimension} spend must be a finite number >= 0, not {value!r}")
    return float(value)


class EvolutionAllowance:
    """The evolution allowance and its ledger in `<root>/spend.jsonl`."""

    def __init__(self, root: Path | str | None = None, limits: AllowanceLimits | None = None) -> None:
        self.root = Path(root) if root is not None else DATA_DIR / "evolution_allowance"
        self.path = self.root / "spend.jsonl"
        self.limits = limits or AllowanceLimits()

    # -- the ledger --------------------------------------------------------

    def records(self, period: str | None = None, *, objective_id: str = "") -> list[dict]:
        """Every spend record, oldest first, optionally of one period or objective."""
        with _LEDGER_LOCK:
            if not self.path.exists():
                return []
            rows = []
            with open(self.path, "rb") as handle:
                for number, raw in enumerate(handle, start=1):
                    try:
                        row = json.loads(raw.decode("utf-8"))
                    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                        raise AllowanceError(
                            f"{self.path} line {number} is not a valid record: {exc}") from exc
                    self._check_row(number, row)
                    rows.append(row)
        return [r for r in rows
                if (period is None or r["period"] == period)
                and (not objective_id or r["objective_id"] == objective_id)]

    def _check_row(self, number: int, row) -> None:
        """A row that cannot be trusted fails the whole ledger closed: a NaN or
        negative amount would otherwise hide or cancel real spend."""
        where = f"{self.path} line {number}"
        if not isinstance(row, dict) or row.get("dimension") not in DIMENSIONS:
            raise AllowanceError(f"{where} is not a spend record")
        try:
            _amount(row["dimension"], row.get("amount"))
        except AllowanceError as exc:
            raise AllowanceError(f"{where}: {exc}") from exc
        for key in ("id", "period", "objective_id"):
            if not isinstance(row.get(key), str) or not row[key].strip():
                raise AllowanceError(f"{where} has no {key}")
        if row.get("unit") != UNITS[row["dimension"]]:
            raise AllowanceError(f"{where} has unit {row.get('unit')!r}, "
                                 f"expected {UNITS[row['dimension']]!r}")

    def spent(self, period: str, dimension: str) -> float:
        if dimension not in DIMENSIONS:
            raise AllowanceError(f"unknown dimension {dimension!r}; known: {DIMENSIONS}")
        return math.fsum(r["amount"] for r in self.records(period) if r["dimension"] == dimension)

    def exhausted(self, period: str) -> list[str]:
        """The dimensions with nothing left this period."""
        return [d for d in DIMENSIONS if self.spent(period, d) >= self.limits.of(d)]

    def report(self, period: str) -> dict:
        """What the owner sees: limits, spend and the records that add up to it."""
        rows = self.records(period)
        spent = {d: math.fsum(r["amount"] for r in rows if r["dimension"] == d)
                 for d in DIMENSIONS}
        return {
            "period": period,
            "units": dict(UNITS),
            "limits": {d: self.limits.of(d) for d in DIMENSIONS},
            "spent": spent,
            "remaining": {d: max(self.limits.of(d) - spent[d], 0.0) for d in DIMENSIONS},
            "exhausted": [d for d in DIMENSIONS if spent[d] >= self.limits.of(d)],
            "records": rows,
        }

    def _append(self, rows: list[dict]) -> None:
        data = b"".join((json.dumps(r, sort_keys=True, allow_nan=False) + "\n").encode("utf-8")
                        for r in rows)
        with _LEDGER_LOCK:
            self.records()                    # never append to a damaged ledger
            self.root.mkdir(parents=True, exist_ok=True)
            with open(self.path, "ab") as handle:
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())

    def charge(self, gov: G.Governor, objective_id: str, *, money_usd: float = 0.0,
               ram_gb_s: float = 0.0, wall_s: float = 0.0,
               now: datetime | None = None) -> list[dict]:
        """Record what a step spent, one labelled record per dimension that
        spent anything. Refused unless the governor still holds the admitted
        evolution lease of `objective_id`: evolution draws only on a lease."""
        if not objective_id or not objective_id.strip():
            raise AllowanceError("a spend needs the objective id it is for")
        label = f"{E._LABEL}{objective_id}"
        if not any(lease["label"] == label for lease in gov.active_leases()):
            raise AllowanceError(f"{objective_id} holds no admitted evolution lease; "
                                 f"evolution spends only on one")
        amounts = {MONEY: _amount(MONEY, money_usd), RAM: _amount(RAM, ram_gb_s),
                   TIME: _amount(TIME, wall_s)}
        stamp = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
        rows = [{"id": f"SPEND-{uuid.uuid4().hex[:12]}", "at": stamp.isoformat(),
                 "period": period_of(stamp), "objective_id": objective_id,
                 "dimension": d, "amount": amount, "unit": UNITS[d]}
                for d, amount in amounts.items() if amount > 0]
        if rows:
            self._append(rows)
        return rows

    # -- the gate ----------------------------------------------------------

    def authorize_step(self, archive: A.EvolutionArchive, *, objective_id: str, trigger: str,
                       parent_version: str, candidate_version: str, hypothesis: str, scope,
                       now: datetime | None = None) -> StepDecision:
        """May the next experiment step run? A step with no trigger is refused
        and spends nothing. A step with any one dimension exhausted is refused
        and archived (decision REFUSED, which never counts as a lost experiment)
        with the dimension, the spend and the limit."""
        if not (trigger or "").strip():
            return StepDecision(False, REASON_NO_TRIGGER)
        period = period_of(now)
        used = self.exhausted(period)
        if not used:
            return StepDecision(True)
        dimension = used[0]
        spent = {d: self.spent(period, d) for d in DIMENSIONS}
        row = archive.record(
            objective=objective_id, parent_version=parent_version,
            candidate_version=candidate_version, hypothesis=hypothesis, scope=scope,
            baseline={f"spent_{d}": spent[d] for d in DIMENSIONS},
            candidate={"steps_run": 0},
            evaluation={"refused": REASON_EXHAUSTED, "dimension": dimension,
                        "exhausted": used, "period": period, "trigger": trigger,
                        "limit": self.limits.of(dimension), "spent": spent[dimension],
                        "unit": UNITS[dimension]},
            decision=A.REFUSED, rollback_version=parent_version)
        return StepDecision(False, REASON_EXHAUSTED, dimension, row["id"])


def yield_to_foreground(store, gov: G.Governor, *, stop=None) -> list[dict]:
    """A foreground task is asking to start: stop every running evolution
    experiment now. `stop(run_id)` is the caller's own way of halting the
    experiment's work; every lease is released and run cancelled whether or
    not a stop raises, and the first error is raised afterwards, not swallowed. Returns what was stopped."""
    stopped, errors = [], []
    for owned in gov.owned_processes():
        if owned["owner"] != "evolution" or not str(owned["label"]).startswith(E._LABEL):
            continue
        run_id = owned["objective_id"]
        evidence = {"reason": "preempted", "blocked_by": E.ACTIVE_TASK, "foreground": True}
        started = time.monotonic()
        try:
            if stop is not None:
                stop(run_id)
        except Exception as exc:          # surfaced after every lease is released
            errors.append(exc)
        finally:
            store.append_objective_event(run_id, E.EVENT_STOPPED, detail=evidence)
            run = store.objective_run(run_id)
            if run is not None and run["status"] not in O.RUN_TERMINAL:
                store.finish_objective_run(run_id, status=O.RUN_CANCELLED,
                                           summary={"evolution_stop": evidence})
                productivity.report_finished_run(store, run_id)
            gov.release(owned["lease"])
        stopped.append({"run_id": run_id, "lease": owned["lease"],
                        "seconds": time.monotonic() - started})
    if errors:
        raise errors[0]
    return stopped
