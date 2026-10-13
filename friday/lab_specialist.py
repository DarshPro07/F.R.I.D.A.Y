"""
Specialist micro-models, trained offline and promoted only on a strict win
(PRD v7 section 112, LAB-004).

A specialist is a small stdlib linear classifier for the task-category narrow
task. It is trained only from LAB-002's approved export (`friday.lab_dataset`),
judged on LAB-003's frozen evaluation cases (`friday.lab_benchmark`), carried
through NEURAL-001's lifecycle (`friday.neural_fabric`) and promoted by
LEARN-001's ladder (`friday.adaptor_promotion`), on a stored arena report.

    CANDIDATE   every specialist starts here and serves nothing. One that
                only ties the current routing, is worse, or fails any case the
                serving behaviour passed (forgetting) stays here, routing is
                unchanged, and the arena report says which gate it failed.
    PRODUCTION  a strict win with no forgetting. `classify` then answers from
                the specialist; `rollback` is one call back to the version it
                superseded, and past the first version to the routing itself.

The weights are fitted only in `friday/neural/candidates/linear_specialist.py`,
the one path NEURAL-002's boundary test exempts.

Training is a controller job (`run_job`): refused unless the controller starts
it (a conversational turn cannot), admitted through the resource governor as
optional background work (so never under pressure), and recorded with the
hardware it ran on. A trainer handed one interaction refuses, so no single
interaction can move a weight.

A specialist advises on the task category only. It has no seam to approve
anything: `advise` ignores every other purpose, and this module does not import
`friday.confirmation`, which stays the only thing that binds an action.

Assumed, revisit if wrong: a dataset needs at least ten labelled train cases and
ten labelled frozen cases (LAB-003's default minimum) before a specialist is
trained or judged; the "previously passing" cases are those the behaviour
currently serving passes, the active specialist or else the routing; only the
linear classifier is built, a MiniMind adaptor would plug in as another
artifact kind.
"""
from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Mapping

from friday import adaptor_promotion as AP
from friday import arena
from friday import experimental_local as EL
from friday import governor as G
from friday import lab_benchmark as LB
from friday import neural_fabric as NF
from friday.neural.candidates.linear_specialist import (  # noqa: F401  (re-exported)
    MIN_TRAIN_CASES, TrainingRefused, require_enough, train_linear)

log = logging.getLogger("friday.lab_specialist")

#: The only trigger that may start a training job.
CONTROLLER = "controller"

#: The only decision a specialist may advise on. Everything else is ignored.
PURPOSE_TASK_CATEGORY = "task_category"

MIN_EVAL_CASES = 10

JOBS_FILE = "lab_specialist.json"
KIND = "linear_specialist"


class JobRefused(Exception):
    """A training job was not started."""


@dataclass(frozen=True)
class Advice:
    used: bool
    label: str | None
    reason: str = ""


def _accuracy(passed: Mapping[str, bool]) -> float:
    return sum(passed.values()) / len(passed) if passed else 0.0


class SpecialistLab:
    def __init__(self, state_dir: str | Path, *, ladder: AP.AdaptorLadder,
                 arena_store: arena.ArenaStore, governor: G.Governor, hardware: str,
                 provenance: Mapping[str, object] | None = None,
                 clock: Callable[[], float] = time.perf_counter) -> None:
        if not ladder.strict:
            raise ValueError("a specialist is promoted by a strict ladder: "
                             "a tie with the current routing is not a win")
        if not hardware.strip():
            raise ValueError("a training job is recorded with the hardware it ran on")
        self._dir = Path(state_dir)
        self._fabric = NF.NeuralFabric(self._dir)
        self._ladder = ladder
        self._store = arena_store
        self._gov = governor
        self._hardware = hardware.strip()
        self._provenance = provenance
        self._clock = clock

    # -- jobs ----------------------------------------------------------------

    def jobs(self) -> list[dict]:
        return self._load_jobs()

    def _load_jobs(self) -> list[dict]:
        try:
            return json.loads((self._dir / JOBS_FILE).read_text(encoding="utf-8"))["jobs"]
        except FileNotFoundError:
            return []

    def _save_jobs(self, jobs: list[dict]) -> None:
        self._dir.mkdir(parents=True, exist_ok=True)
        path = self._dir / JOBS_FILE
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(json.dumps({"jobs": jobs}, sort_keys=True, indent=1) + "\n",
                       encoding="utf-8")
        os.replace(tmp, path)

    def _record(self, job: dict) -> None:
        jobs = self._load_jobs()
        jobs = [j for j in jobs if j["id"] != job["id"]] + [job]
        self._save_jobs(sorted(jobs, key=lambda j: j["id"]))

    def run_job(self, name: str, dataset_dir: str | Path, *, trigger: str) -> dict:
        """Train a specialist from `dataset_dir` and judge it. Only the
        controller starts this, and only when the governor admits background
        work; the job and the hardware it ran on are recorded either way."""
        if trigger != CONTROLLER:
            log.warning("lab_specialist: training refused for trigger %r", trigger)
            raise JobRefused(f"training runs as a controller job; {trigger!r} cannot start it")
        decision = self._gov.admit(G.OPTIONAL, label=f"specialist-train:{name}",
                                   priority=G.BACKGROUND)
        job = {"id": len(self._load_jobs()) + 1, "name": name, "trigger": trigger,
               "hardware": self._hardware, "started_at": time.time(), "version": None}
        if not decision.admitted:
            self._record({**job, "status": "REFUSED", "reason": decision.reason})
            raise JobRefused(f"training deferred, governor {decision.decision} "
                             f"under {decision.pressure} pressure: {decision.reason}")
        try:
            self._record({**job, "status": "RUNNING", "lease": decision.lease})
            outcome, seconds = self._judge(name, job["id"], dataset_dir)
        except Exception as exc:
            self._record({**job, "status": "FAILED", "error": type(exc).__name__})
            raise
        finally:
            self._gov.release(decision.lease)
        figure = LB.TrainingFigure("train", seconds, self._hardware, LB.LINEAR)
        self._record({**job, "version": outcome["version"], "status": "COMPLETED",
                      "report_id": outcome["report_id"],
                      "outcome": outcome["status"], "figure": figure.to_dict()})
        return {**outcome, "hardware": self._hardware, "seconds": seconds}

    # -- train, evaluate, promote ----------------------------------------------

    def _judge(self, name: str, job_id: int, dataset_dir: str | Path) -> tuple[dict, float]:
        export = LB.load_export(dataset_dir)          # each file against its manifest sha256
        require_enough(len(LB.cases_for(EL.TASK_CATEGORY, export.train_rows)))
        # Named by the job, which is unique, so a failed job's registration can
        # never collide with a later one; the ladder alone numbers the versions.
        network = f"{name}@job{job_id}"
        started = self._clock()
        self._fabric.register(network, KIND, dataset_dir)
        self._fabric.train(network, train_linear)
        seconds = self._clock() - started

        serve = self._predictor(name)
        seen: dict[str, dict[str, bool]] = {}

        def score(artifact, rows) -> float:
            clf = LB.LinearClassifier.from_dict(artifact)
            seen["candidate"] = {c.case_id: clf.predict(c) == c.label
                                 for c in LB.cases_for(EL.TASK_CATEGORY, rows)}
            return _accuracy(seen["candidate"])

        def baseline(rows) -> float:
            seen["serving"] = {c.case_id: serve(c) == c.label
                               for c in LB.cases_for(EL.TASK_CATEGORY, rows)}
            return _accuracy(seen["serving"])

        rec = self._fabric.evaluate(network, score, baseline)
        candidate, serving = rec["holdout"]["candidate"], rec["holdout"]["baseline"]
        forgotten = sorted(i for i, ok in seen["serving"].items()
                           if ok and not seen["candidate"][i])
        captured = self._ladder.capture(
            name, holdout=candidate, cases=rec["holdout"]["rows"], note=network)
        if captured.get("status") != "captured":
            raise RuntimeError(f"ladder refused the candidate: {captured}")
        version = captured["version"]
        result = self._ladder.promote(
            name, store=self._store, version=version, baseline=serving,
            provenance=self._provenance,
            extra_gates={"no_forgetting": not forgotten,
                         "minimum_cases": rec["holdout"]["rows"] >= MIN_EVAL_CASES})
        if result["status"] not in ("promoted", "blocked"):
            raise RuntimeError(f"unexpected ladder result {result}")
        promoted = result["status"] == "promoted"
        if promoted:
            # the ladder judged the same numbers strictly; the fabric records the
            # win. If it does not (or raises, e.g. the export changed on disk),
            # the ladder must not keep serving a version `use` refuses: one
            # rollback restores what served before, then the failure propagates.
            try:
                if self._fabric.promote(network, margin=0.0, strict=True)["status"] != NF.PRODUCTION:
                    raise RuntimeError(f"{network}: the ladder promoted what the fabric did not")
            except Exception:
                undone = self._ladder.rollback(name)
                log.warning("lab_specialist: %s v%s not recorded by the fabric, ladder %s",
                            network, version, undone)
                raise
        failed: list[str] = []
        if not promoted:
            report = self._store.report(result["report_id"])
            failed = list(report.result(AP.arena_id(name, version)).failed_gates)
        return ({"status": "promoted" if promoted else "candidate", "version": version,
                 "network": network, "report_id": result["report_id"],
                 "failed_gates": failed, "forgotten": forgotten,
                 "accuracy": {"candidate": candidate, "serving": serving}}, seconds)

    # -- serving ---------------------------------------------------------------

    def _predictor(self, name: str) -> Callable[[LB.Case], str]:
        """The behaviour serving `name`: its active specialist, else routing."""
        active = self._ladder.active(name)
        if active is None:
            return LB.current_routing
        artifact = self._fabric.use(active["note"])
        return LB.LinearClassifier.from_dict(artifact).predict

    def classify(self, name: str, case: LB.Case) -> str:
        return self._predictor(name)(case)

    def advise(self, name: str, case: LB.Case, purpose: str) -> Advice:
        """The specialist's task category for `case`, for that purpose only. An
        approval or confirmation decision (or any other purpose) is ignored:
        the specialist's output is never an input to one."""
        if purpose != PURPOSE_TASK_CATEGORY:
            log.warning("lab_specialist: advice on %r ignored", purpose)
            return Advice(False, None, f"a specialist advises on {PURPOSE_TASK_CATEGORY} only")
        return Advice(True, self.classify(name, case))

    def rollback(self, name: str) -> dict:
        """One call back to the previous version, or to the routing itself."""
        active = self._ladder.active(name)
        out = self._ladder.rollback(name)
        if out["status"] == "rolled_back":
            self._fabric.demote(active["note"], "rolled back")
        return out
