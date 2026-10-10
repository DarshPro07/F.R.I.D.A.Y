"""
The Skill Arena: competing candidates for one capability are chosen by how they
perform on the same tasks, never by their descriptions (CAP-MKT-004).

`friday/arena.py` is the shared promotion rule (a stored report, gates, a
winner). This module is the skill-specific half: it runs every candidate on the
same task set, scores each on accuracy, completion, tokens, latency, tool calls,
retries, security and maintenance, builds one `ArenaKind.SKILL` report, and
keeps a roster per capability: the winner ACTIVE, every other candidate
ARCHIVED with the scores it earned. A later challenger is run against the
incumbent on the same tasks and replaces it only by beating it by more than
`REPLACE_MARGIN`; the replaced winner is archived with its scores.

A candidate is chosen by what the ARENA measures, not by what it says:
  * the runner (`runner(task) -> RunOutput`) only executes: it returns the
    output it produced plus its usage (tokens, tool calls, retries). It has no
    field in which to claim accuracy, completion or a clean security record;
  * the arena-owned `grader(task, output) -> Grade` decides accuracy,
    completion and security violations from the output;
  * the arena times every runner call itself (latency_ms), so a runner cannot
    report a speed it did not have.
A runner is third-party code, so its failure is its result: an exception, or
anything that is not a `RunOutput` (likewise a grader failure on its output), is
one failed task (accuracy 0, not completed, counted in `errors`), logged by
type only, and the other candidates are still measured. The module is
synchronous; a caller on an event loop runs it in a worker thread. Runners
execute in the caller's process with its privileges: a real runner for an
untrusted skill must itself go through the skill sandbox or an executor.
Tokens, tool calls and retries are the runner's own usage report, because the
arena cannot observe them without an instrumented executor.

Reports are namespaced by capability (`<capability>/<candidate id>`), so one
skill competing in two capabilities has an independent stored comparison for
each and one run cannot supersede the other's promotion evidence.

Assumptions (revisit if wrong):
  * The metric weights below, `REPLACE_MARGIN` and the "security_clean" gate
    (any security violation disqualifies, whatever the accuracy) are my choice;
    the ledger names the dimensions but not their weights.
  * Cost dimensions (tokens, latency, tool calls, retries) are scored relative
    to the best candidate in the same run, so a score is comparable inside one
    report, not across reports. A task that crashed reports no cost and is left
    out of the cost means; a candidate that never ran a task fails the "ran" gate.
  * `maintenance` (0..1, higher is better; e.g. freshness and dependency health)
    is a property of the candidate supplied by the caller, not of a task.
  * ARCHIVED is this roster's word for "lost the arena, kept with its scores".
    The skill registry has no such state (`contracts.DEPRECATED` is the nearest);
    mapping the roster onto registry transitions is the activation hook, not
    done here.
"""
from __future__ import annotations

import copy
import json
import logging
import math
import os
import threading
import time
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from friday import arena as A

log = logging.getLogger("friday.skill_arena")

ACTIVE = "ACTIVE"
ARCHIVED = "ARCHIVED"

#: Weights of the scored dimensions; they sum to 1.
WEIGHTS: Mapping[str, float] = {
    "accuracy": 0.30, "completion": 0.20, "security": 0.10, "tokens": 0.10,
    "latency": 0.08, "tool_calls": 0.08, "retries": 0.07, "maintenance": 0.07,
}

#: A challenger must beat the incumbent's score by more than this to replace it.
REPLACE_MARGIN = 0.02


class SkillArenaError(RuntimeError):
    """The arena or its roster cannot give a trustworthy answer."""


def _finite(value: Any, name: str, *, low: float = 0.0, high: float | None = None) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(f"{name} must be a finite number, got {value!r}")
    if value < low or (high is not None and value > high):
        raise ValueError(f"{name} out of range: {value!r}")
    return float(value)


def _count(value: Any, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{name} must be a non-negative integer, got {value!r}")
    return value


@dataclass(frozen=True)
class RunOutput:
    """What a candidate produced on one task and the usage it reports. Nothing
    here is a quality claim: the arena's grader judges `output`."""

    output: Any
    tokens: int = 0
    tool_calls: int = 0
    retries: int = 0

    def __post_init__(self) -> None:
        for name in ("tokens", "tool_calls", "retries"):
            _count(getattr(self, name), name)


@dataclass(frozen=True)
class Grade:
    """The arena's judgement of one output. Validated: a grader cannot report
    numbers that are not measurements."""

    accuracy: float
    completed: bool
    security_violations: int = 0

    def __post_init__(self) -> None:
        _finite(self.accuracy, "accuracy", high=1.0)
        if not isinstance(self.completed, bool):
            raise ValueError("completed must be a bool")
        _count(self.security_violations, "security_violations")


Runner = Callable[[Any], RunOutput]
Grader = Callable[[Any, Any], Grade]


@dataclass(frozen=True)
class Candidate:
    id: str
    runner: Runner
    maintenance: float = 1.0

    def __post_init__(self) -> None:
        if not isinstance(self.id, str) or not self.id.strip():
            raise ValueError("a candidate has a name")
        _finite(self.maintenance, "maintenance", high=1.0)


# -- roster ---------------------------------------------------------------------------

_LOCK = threading.RLock()


class Roster:
    """Per capability: which candidate is ACTIVE and what every other candidate
    scored. In memory with no `path`; otherwise JSON, read on every call and
    written by atomic replace (the same shape as `arena.ArenaStore`)."""

    def __init__(self, path: Path | str | None = None) -> None:
        self.path = Path(path) if path else None
        self._memory: dict[str, dict] = {}

    def _load(self) -> dict[str, dict]:
        if self.path is None:
            return self._memory
        try:
            raw = self.path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return {}
        except OSError as exc:
            raise SkillArenaError(f"cannot read {self.path}: {exc}") from exc
        try:
            data = json.loads(raw)["capabilities"]
            for cap, row in data.items():
                active = row["active"]
                entries = row["entries"]
                if not isinstance(active, str) or not isinstance(entries, dict):
                    raise ValueError(f"{cap}: malformed")
                for cid, entry in entries.items():
                    if entry["status"] not in (ACTIVE, ARCHIVED):
                        raise ValueError(f"{cap}/{cid}: unknown status")
                    float(entry["score"])
                    entry["metrics"], entry["report_id"]
                if active and (active not in entries or entries[active]["status"] != ACTIVE):
                    raise ValueError(f"{cap}: active candidate is not an ACTIVE entry")
        except (ValueError, KeyError, TypeError, AttributeError) as exc:
            raise SkillArenaError(f"{self.path} is not roster evidence: {exc!r}") from exc
        return data

    def active(self, capability: str) -> str:
        with _LOCK:
            return self._load().get(capability, {}).get("active", "")

    def entry(self, capability: str, candidate_id: str) -> dict | None:
        with _LOCK:
            found = self._load().get(capability, {}).get("entries", {}).get(candidate_id)
            return copy.deepcopy(found)     # never a handle on the roster's own state

    def apply(self, capability: str, report: A.ArenaReport) -> None:
        """Make the report's winner ACTIVE and every other candidate it measured
        ARCHIVED with its score; candidates from earlier runs keep their rows."""
        with _LOCK:
            data = json.loads(json.dumps(self._load()))   # a copy to edit
            row = data.setdefault(capability, {"active": "", "entries": {}})
            prefix = capability + "/"
            for result in report.results:
                if not result.candidate_id.startswith(prefix):
                    raise SkillArenaError(f"{result.candidate_id!r} is not a {capability} candidate")
                row["entries"][result.candidate_id[len(prefix):]] = {
                    "status": ACTIVE if result.candidate_id == report.winner else ARCHIVED,
                    "score": result.score, "passed": result.passed,
                    "failed_gates": list(result.failed_gates),
                    "metrics": dict(result.metrics), "report_id": report.report_id}
            row["active"] = report.winner[len(prefix):] if report.winner else ""
            if self.path is None:
                self._memory = data
                return
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_name(self.path.name + ".tmp")
            tmp.write_text(json.dumps({"capabilities": data}, indent=1), encoding="utf-8")
            os.replace(tmp, self.path)


# -- the arena ------------------------------------------------------------------------

@dataclass(frozen=True)
class SkillArenaOutcome:
    capability: str
    report: A.ArenaReport
    winner: str
    replaced: str                       # the incumbent that lost its place, else ""
    entries: Mapping[str, dict] = field(default_factory=dict)

    def entry(self, candidate_id: str) -> dict:
        return self.entries[candidate_id]

    def status(self, candidate_id: str) -> str:
        return self.entries[candidate_id]["status"]


def _measure(candidate: Candidate, tasks: Sequence, grader: Grader,
             clock: Callable[[], float]) -> dict:
    done: list[tuple[RunOutput, Grade, float]] = []
    errors = 0
    for task in tasks:
        try:
            started = clock()
            out = candidate.runner(task)
            elapsed_ms = max(0.0, (clock() - started) * 1000.0)
            if not isinstance(out, RunOutput):
                raise TypeError(f"a runner returns a RunOutput, got {type(out).__name__}")
            grade = grader(task, out.output)
            if not isinstance(grade, Grade):
                raise TypeError(f"a grader returns a Grade, got {type(grade).__name__}")
        except Exception as exc:    # third-party code: its failure is its result
            errors += 1
            log.warning("skill arena: %s failed a task: %s", candidate.id, type(exc).__name__)
            continue
        done.append((out, grade, elapsed_ms))
    n = len(tasks)

    def mean(get: Callable[[tuple[RunOutput, Grade, float]], float]) -> float:
        return sum(get(d) for d in done) / len(done) if done else 0.0

    return {
        "ran": bool(done), "accuracy": sum(g.accuracy for _, g, _ in done) / n,
        "completion": sum(1 for _, g, _ in done if g.completed) / n,
        "tokens": mean(lambda d: d[0].tokens), "latency_ms": mean(lambda d: d[2]),
        "tool_calls": mean(lambda d: d[0].tool_calls), "retries": mean(lambda d: d[0].retries),
        "security_violations": float(sum(g.security_violations for _, g, _ in done)),
        "maintenance": candidate.maintenance, "errors": float(errors)}


def _cost_score(value: float, best: float, ran: bool) -> float:
    if not ran:
        return 0.0
    return 1.0 if value <= best else best / value


def _scores(measured: Mapping[str, dict], cases: int) -> dict[str, A.Score]:
    costs = {"tokens": "tokens", "latency": "latency_ms", "tool_calls": "tool_calls",
             "retries": "retries"}
    best = {key: min((m[attr] for m in measured.values() if m["ran"]), default=0.0)
            for key, attr in costs.items()}
    scores: dict[str, A.Score] = {}
    for cid, m in measured.items():
        parts = {"accuracy": m["accuracy"], "completion": m["completion"],
                 "security": 1.0 if m["security_violations"] == 0 else 0.0,
                 "maintenance": m["maintenance"]}
        parts.update({key: _cost_score(m[attr], best[key], m["ran"]) for key, attr in costs.items()})
        total = sum(WEIGHTS[key] * parts[key] for key in WEIGHTS)
        scores[cid] = A.Score(
            gates={"ran": m["ran"], "security_clean": m["security_violations"] == 0},
            cases=cases, score=total, metrics={k: v for k, v in m.items() if k != "ran"})
    return scores


def qualified_id(capability: str, candidate_id: str) -> str:
    """The id a candidate carries in the stored arena report, and so in
    `arena.promote`: bound to its capability."""
    return f"{capability}/{candidate_id}"


def default_roster_path() -> Path:
    return A.default_path().with_name("skill_arena_roster.json")


class SkillArena:
    def __init__(self, *, roster: Roster | None = None, store: A.ArenaStore | None = None,
                 provenance: Mapping[str, object] | None = None,
                 margin: float = REPLACE_MARGIN,
                 clock: Callable[[], float] = time.perf_counter) -> None:
        # The roster is durable by default: an incumbent forgotten on restart
        # would let the next challenger be crowned without being run against it.
        self.roster = roster if roster is not None else Roster(default_roster_path())
        self.store = store if store is not None else A.default_store()
        self.provenance = provenance
        self.margin = _finite(margin, "margin")
        self.clock = clock

    def run(self, capability: str, candidates: Sequence[Candidate], tasks: Sequence,
            grader: Grader) -> SkillArenaOutcome:
        """Run every candidate on every task, grade the outputs with `grader`,
        record the report, update the roster. The current ACTIVE candidate must be
        among `candidates`, so it is re-measured on the same tasks as its
        challengers."""
        if not isinstance(capability, str) or not capability.strip() or "/" in capability:
            raise ValueError("an arena is for a named capability (no '/')")
        if not callable(grader):
            raise ValueError("an arena needs a grader to judge outputs")
        candidates, tasks = tuple(candidates), tuple(tasks)
        ids = [c.id for c in candidates]
        if not ids or len(set(ids)) != len(ids):
            raise ValueError("an arena compares a non-empty set of distinct candidates")
        if not tasks:
            raise ValueError("an arena needs at least one task")
        incumbent = self.roster.active(capability)
        if incumbent and incumbent not in ids:
            raise SkillArenaError(f"{capability}: the incumbent {incumbent!r} must run the same "
                                  "tasks as its challengers")

        measured = {qualified_id(capability, c.id): _measure(c, tasks, grader, self.clock)
                    for c in candidates}
        report = A.build_report(A.ArenaKind.SKILL, _scores(measured, len(tasks)),
                                provenance=self.provenance)
        held = report.result(qualified_id(capability, incumbent)) if incumbent else None
        winner = report.winner
        if held is not None and held.passed and winner != held.candidate_id:
            if report.result(winner).score <= held.score + self.margin:
                winner = held.candidate_id      # not better enough to displace it
        report = replace(report, winner=winner)
        bare = winner[len(capability) + 1:] if winner else ""
        with _LOCK:
            # The decision above holds only against the incumbent it was made
            # against: a concurrent run for this capability may have changed it.
            if self.roster.active(capability) != incumbent:
                raise SkillArenaError(f"{capability}: the incumbent changed during the run; "
                                      "run the arena again")
            self.store.record(report)
            self.roster.apply(capability, report)
            entries = {cid: self.roster.entry(capability, cid) for cid in ids}
        return SkillArenaOutcome(capability=capability, report=report, winner=bare,
                                 replaced=incumbent if incumbent and bare != incumbent else "",
                                 entries=entries)
