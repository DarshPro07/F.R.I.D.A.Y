"""
The narrow-task benchmark for MiniMind-class models (PRD v7 section 112, LAB-003).

A small model is chosen for a narrow task only when it beats both what FRIDAY
already does and a trivial learned baseline on the same cases, never because it
is a neural language model. For each narrow task this module runs three kinds
of candidate over LAB-002's frozen evaluation set (`friday.lab_dataset`, read
here and never changed):

    routing    the current deterministic classifier (`friday.task_class`)
    linear     a small stdlib multiclass perceptron trained on the train split
    minimind   one or more MiniMind variants (64M dense, 198M MoE), called only
               through LAB-001's fence (`friday.experimental_local.run`)

and files one report per task. The comparison and the winner are
`friday.arena`'s (`build_report`): the earliest-listed of equals wins, routing
and linear are listed first, and a MiniMind variant carries two extra gates -
it must strictly beat routing and strictly beat linear - so a tie is not a
selection. `select` reads the stored report, never a parameter count or a
vendor claim, and refuses a report whose frozen-set hash is not the set it is
asked about.

The frozen rows hold a request fingerprint, the plan's capability sequence and
the stored task class, not the request text (the privacy rule of LAB-002).
So the task-category benchmark classifies the plan: the features are the
capability sequence and the evidence method, the label is the stored task
class, and the deterministic candidate is `task_class.classify` run over the
capability names. The other narrow tasks have no labelled rows in the frozen
set yet; `cases_for` names them `UnsupportedTask` rather than faking cases.

Assumed, revisit if wrong: the 64M and 198M variants are separate candidates
named by the caller, so each gets its own size, RAM, latency and accuracy; RAM
is the process's resident set sampled after each call (`ram_probe`).
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Mapping, Sequence

from friday import arena
from friday import experimental_local as EL
from friday import lab_dataset as LD

log = logging.getLogger("friday.lab_benchmark")

VERSION = 1
ROUTING = "routing"
LINEAR = "linear"
_METHOD = "method:"


class BenchmarkStoreError(RuntimeError):
    """The report file exists but cannot be read as benchmark evidence."""


class _ModelFailure(Exception):
    """The variant's own `generate` raised; only this is a model failure."""

    def __init__(self, exc: Exception) -> None:
        super().__init__(type(exc).__name__)
        self.kind = type(exc).__name__


class SelectionRefused(Exception):
    """No stored report supports selecting a candidate."""


class UnsupportedTask(Exception):
    """The frozen set has no labelled rows for this narrow task."""


# -- cases ------------------------------------------------------------------


@dataclass(frozen=True)
class Case:
    case_id: str
    features: tuple[str, ...]
    label: str
    data_class: str


def cases_for(narrow_task: str, rows: Sequence[Mapping]) -> list[Case]:
    """Labelled cases from LAB-002 rows, in row order. Rows without a label
    are not cases."""
    if narrow_task != EL.TASK_CATEGORY:
        raise UnsupportedTask(
            f"{narrow_task!r}: the frozen set carries no labelled rows for it; "
            f"only {EL.TASK_CATEGORY!r} can be benchmarked")
    out = []
    for row in rows:
        label = str(row.get("task_class") or "")
        if not label:
            continue
        features = [str(s["capability"]) for s in row.get("steps") or []]
        if row.get("evidence_method"):
            features.append(_METHOD + str(row["evidence_method"]))
        out.append(Case(case_id=str(row["objective_id"]), features=tuple(features),
                        label=label, data_class=str(row["data_class"])))
    return out


@dataclass(frozen=True)
class Export:
    train_rows: tuple[dict, ...]
    eval_rows: tuple[dict, ...]
    eval_sha256: str


def load_export(out_dir: str | Path) -> Export:
    """The rows of a LAB-002 export, each file checked against the sha256 its
    manifest recorded. Raises `lab_dataset.EvalSetChanged` for an evaluation
    file that is not what was frozen, `NotAnExport` for anything missing."""
    out = Path(out_dir)
    eval_rows = LD._frozen(out)
    if eval_rows is None:
        raise LD.NotAnExport(f"{out} holds no frozen evaluation set")
    manifest = json.loads((out / LD.MANIFEST).read_text(encoding="utf-8"))
    try:
        data = (out / LD.TRAIN).read_bytes()
        recorded = manifest["files"][LD.TRAIN]["sha256"]
    except (OSError, KeyError, TypeError) as exc:
        raise LD.NotAnExport(f"{out} has no readable training file record "
                             f"({type(exc).__name__})") from exc
    if LD._sha(data) != recorded:
        raise LD.NotAnExport(f"{out / LD.TRAIN} no longer matches its recorded sha256")
    train = tuple(json.loads(line) for line in data.decode("utf-8").splitlines())
    return Export(train_rows=train, eval_rows=tuple(eval_rows),
                  eval_sha256=manifest["files"][LD.EVAL]["sha256"])


# -- candidates -------------------------------------------------------------


def current_routing(case: Case) -> str:
    """The deterministic router over the plan's capability names."""
    from friday import task_class
    text = " ".join(f.replace("_", " ") for f in case.features if not f.startswith(_METHOD))
    return task_class.classify(text).task_class


class LinearClassifier:
    """A multiclass perceptron over capability and capability-pair features.
    No randomness: the same cases give the same weights."""

    def __init__(self, epochs: int = 25) -> None:
        self.epochs = epochs
        self._labels: list[str] = []
        self._w: dict[str, dict[str, float]] = {}

    @staticmethod
    def _feats(case: Case) -> list[str]:
        f = list(case.features)
        return ["<bias>", *f, *(f"{a}>{b}" for a, b in zip(f, f[1:]))]

    def _argmax(self, feats: Sequence[str]) -> str:
        best, best_score = self._labels[0], None
        for label in self._labels:
            w = self._w[label]
            s = sum(w.get(x, 0.0) for x in feats)
            if best_score is None or s > best_score:
                best, best_score = label, s
        return best

    def fit(self, cases: Sequence[Case]) -> "LinearClassifier":
        self._labels = sorted({c.label for c in cases})
        self._w = {label: {} for label in self._labels}
        if not self._labels:
            return self
        for _ in range(self.epochs):
            errors = 0
            for c in cases:
                feats = self._feats(c)
                pred = self._argmax(feats)
                if pred != c.label:
                    errors += 1
                    for x in feats:
                        self._w[c.label][x] = self._w[c.label].get(x, 0.0) + 1.0
                        self._w[pred][x] = self._w[pred].get(x, 0.0) - 1.0
            if not errors:
                break
        return self

    def to_dict(self) -> dict:
        """The trained weights as plain JSON (what NEURAL-001 stores)."""
        return {"kind": LINEAR, "epochs": self.epochs, "labels": list(self._labels),
                "weights": {k: dict(v) for k, v in self._w.items()}}

    @classmethod
    def from_dict(cls, doc: Mapping) -> "LinearClassifier":
        clf = cls(epochs=int(doc["epochs"]))
        clf._labels = [str(x) for x in doc["labels"]]
        clf._w = {str(k): {str(f): float(w) for f, w in v.items()}
                  for k, v in dict(doc["weights"]).items()}
        return clf

    def predict(self, case: Case) -> str:
        if not self._labels:
            raise ValueError("the classifier has no training cases")
        return self._argmax(self._feats(case))


@dataclass(frozen=True)
class MiniMind:
    """One MiniMind variant: `generate(prompt)` returns the model's text.
    `size_gb` is the artifact size the pool would register."""

    name: str
    size_gb: float
    generate: Callable[[str], str]


def _rss_gb() -> float:
    import psutil
    return psutil.Process(os.getpid()).memory_info().rss / 1024 ** 3


# -- training figures -------------------------------------------------------


@dataclass(frozen=True)
class TrainingFigure:
    """A training time, always with the hardware it ran on and the MiniMind
    variant it trained (the 64M dense and the 198M MoE are never pooled)."""

    stage: str
    seconds: float
    hardware: str
    model: str

    def __post_init__(self) -> None:
        if not (self.stage.strip() and self.hardware.strip() and self.model.strip()):
            raise ValueError("a training figure names its stage, the hardware it ran on "
                             "and the model it trained")
        if not self.seconds >= 0:
            raise ValueError("a training time is not negative")

    def to_dict(self) -> dict:
        return {"stage": self.stage, "seconds": float(self.seconds), "hardware": self.hardware,
                "model": self.model}


# -- report -----------------------------------------------------------------


@dataclass(frozen=True)
class BenchmarkReport:
    """One narrow task's comparison, versioned by commit, date and set hash."""

    task: str
    eval_sha256: str
    case_ids: tuple[str, ...]
    candidates: dict
    arena: arena.ArenaReport
    training: tuple[TrainingFigure, ...] = ()
    version: int = VERSION

    @property
    def commit(self) -> str:
        return self.arena.commit

    @property
    def date(self) -> str:
        return self.arena.date

    def _body(self) -> dict:
        return {"version": self.version, "task": self.task,
                "eval_sha256": self.eval_sha256, "case_ids": list(self.case_ids),
                "candidates": {k: self.candidates[k] for k in sorted(self.candidates)},
                "training": [t.to_dict() for t in self.training],
                "arena": self.arena.to_dict()}

    @property
    def report_id(self) -> str:
        blob = json.dumps(self._body(), sort_keys=True, separators=(",", ":"))
        return "bench-" + hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]

    def to_dict(self) -> dict:
        return {"report_id": self.report_id, **self._body()}

    @classmethod
    def from_dict(cls, row: dict) -> "BenchmarkReport":
        report = cls(task=str(row["task"]), eval_sha256=str(row["eval_sha256"]),
                     case_ids=tuple(str(i) for i in row["case_ids"]),
                     candidates={str(k): dict(v) for k, v in dict(row["candidates"]).items()},
                     arena=arena.ArenaReport.from_dict(row["arena"]),
                     training=tuple(TrainingFigure(**t) for t in row["training"]),
                     version=int(row["version"]))
        if report.report_id != row["report_id"]:
            raise ValueError(f"{row['report_id']} does not match its content")
        return report


def figures_for(report: BenchmarkReport, hardware: str,
                model: str | None = None) -> list[TrainingFigure]:
    """Only the training figures measured on exactly `hardware` (and, when
    given, for exactly `model`): a figure from another machine or another
    variant is never presented as this one's."""
    return [t for t in report.training
            if t.hardware == hardware and (model is None or t.model == model)]


# -- running ----------------------------------------------------------------


def _prompt(task: str, labels: Sequence[str], case: Case) -> str:
    return json.dumps({"task": task, "labels": list(labels), "features": list(case.features)},
                      sort_keys=True)


def run_task(narrow_task: str, train: Sequence[Case], eval_cases: Sequence[Case],
             eval_sha256: str, *, minimind: Sequence[MiniMind], labels: Sequence[str],
             routing: Callable[[Case], str] | None = None,
             provenance: Mapping[str, object] | None = None, minimum_cases: int = 10,
             training: Sequence[TrainingFigure] = (),
             ram_probe: Callable[[], float] | None = None,
             clock: Callable[[], float] = time.perf_counter) -> BenchmarkReport:
    """Measure every candidate on the same `eval_cases` and report.

    A MiniMind output that is not valid for the label schema counts as a wrong
    answer and is tallied as `rejected`; a model that raises fails its own
    candidate (gate `scored`) and the others are still measured. Only the
    variant's own `generate` raising counts as that; a refused task, a failing
    RAM probe or clock propagate, since they are not the model's result."""
    if narrow_task not in EL.NARROW_TASKS:
        raise UnsupportedTask(f"{narrow_task!r} is not a narrow task")
    names = [v.name for v in minimind]
    if any(not n.strip() for n in names):
        raise ValueError("a MiniMind variant needs a name")
    if len(set(names)) != len(names):
        raise ValueError("MiniMind variant names must be unique: the 64M dense and "
                         "198M MoE are separate candidates")
    if {ROUTING, LINEAR} & set(names):
        raise ValueError(f"a MiniMind variant cannot be named {ROUTING!r} or {LINEAR!r}: "
                         "those are the baselines")
    stray = {t.model for t in training} - set(names)
    if stray:
        raise ValueError(f"training figures for models that were not measured: {sorted(stray)}")
    routing = routing or current_routing
    ram_probe = ram_probe or _rss_gb
    cases = list(eval_cases)
    schema = {"type": "object", "required": ["label"],
              "properties": {"label": {"type": "string", "enum": list(labels)}}}
    linear = LinearClassifier().fit(list(train))
    ids = [c.case_id for c in cases]
    details: dict[str, dict] = {}
    scores: dict[str, arena.Score] = {}

    def measure(name: str, kind: str, predict: Callable[[Case], str | None],
                extra: Mapping[str, float] | None = None, probe: bool = False) -> float:
        hits, ms, ram = 0, 0.0, 0.0
        for c in cases:
            t0 = clock()
            pred = predict(c)
            ms += (clock() - t0) * 1000.0
            hits += pred == c.label
            if probe:
                ram = max(ram, float(ram_probe()))
        acc = hits / len(cases) if cases else 0.0
        metrics = {"accuracy": acc, "latency_ms": ms / len(cases) if cases else 0.0,
                   **(extra or {})}
        if probe:
            metrics["ram_gb"] = ram
        details[name] = {"kind": kind, "case_ids": ids}
        scores[name] = arena.Score(gates={"minimum_cases": len(cases) >= minimum_cases},
                                   cases=len(cases), score=acc, metrics=metrics)
        return acc

    base = {name: measure(name, kind, fn) for name, kind, fn in
            ((ROUTING, "routing", routing), (LINEAR, "linear", linear.predict))}

    for variant in minimind:
        rejected = 0

        def ask(c: Case, variant: MiniMind = variant) -> str | None:
            nonlocal rejected
            prompt = _prompt(narrow_task, labels, c)

            def call(_task: str) -> str:
                try:
                    return variant.generate(prompt)
                except Exception as exc:  # a model is third-party code
                    raise _ModelFailure(exc) from exc

            try:
                out = EL.run(narrow_task, c.data_class, call, schema)
            except EL.OutputRejected:
                rejected += 1
                return None
            return out["label"]

        try:
            acc = measure(variant.name, "minimind", ask, probe=True,
                          extra={"size_gb": float(variant.size_gb)})
        except _ModelFailure as failure:  # the model's own failure is its result
            log.warning("lab_benchmark: %s failed on %s: %s", variant.name,
                        narrow_task, failure.kind)
            details[variant.name] = {"kind": "minimind", "case_ids": [],
                                     "failure": failure.kind}
            scores[variant.name] = arena.Score(gates={"scored": False}, cases=0)
            continue
        prior = scores[variant.name]
        scores[variant.name] = arena.Score(
            gates={**prior.gates, "beats_routing": acc > base[ROUTING],
                   "beats_linear": acc > base[LINEAR]},
            cases=prior.cases, score=acc,
            metrics={**prior.metrics, "rejected": float(rejected)})

    report = arena.build_report(arena.ArenaKind.MODEL_ROUTE, scores, provenance=provenance)
    return BenchmarkReport(task=narrow_task, eval_sha256=eval_sha256, case_ids=tuple(ids),
                           candidates=details, arena=report, training=tuple(training))


# -- store and selection ----------------------------------------------------


class ReportStore:
    """Every benchmark report ever filed, oldest first. An unreadable file is
    an error, never an empty store."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def _read(self) -> list[BenchmarkReport]:
        if not self.path.exists():
            return []
        try:
            doc = json.loads(self.path.read_text(encoding="utf-8"))
            return [BenchmarkReport.from_dict(r) for r in doc["reports"]]
        except (OSError, ValueError, KeyError, TypeError) as exc:
            raise BenchmarkStoreError(
                f"{self.path} is not readable benchmark evidence: {type(exc).__name__}: {exc}"
            ) from exc

    def add(self, report: BenchmarkReport) -> None:
        reports = [r for r in self._read() if r.report_id != report.report_id]
        reports.append(report)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_name(self.path.name + ".tmp")
        tmp.write_text(json.dumps({"reports": [r.to_dict() for r in reports]},
                                  sort_keys=True, indent=1) + "\n", encoding="utf-8")
        os.replace(tmp, self.path)

    def latest(self, task: str) -> BenchmarkReport | None:
        mine = [r for r in self._read() if r.task == task]
        return mine[-1] if mine else None


@dataclass(frozen=True)
class Selection:
    task: str
    candidate: str
    report_id: str
    neural: bool


def select(narrow_task: str, store: ReportStore, *, eval_sha256: str) -> Selection:
    """The task's winner from its latest stored report. A neural winner is
    re-checked against the report's own scores: it must have passed and
    strictly beaten routing and linear. `eval_sha256` is required: it is the
    frozen set the caller is asking about, and the report must be measured on it."""
    report = store.latest(narrow_task)
    if report is None:
        raise SelectionRefused(f"no benchmark report is stored for {narrow_task!r}")
    if not eval_sha256 or report.eval_sha256 != eval_sha256:
        raise SelectionRefused(f"{report.report_id} was measured on another evaluation set")
    winner = report.arena.winner
    if not winner:
        raise SelectionRefused(f"no candidate passed in {report.report_id}")
    neural = report.candidates.get(winner, {}).get("kind") == "minimind"
    if neural:
        won = report.arena.result(winner)
        if not (won.passed and all(won.score > report.arena.result(b).score
                                   for b in (ROUTING, LINEAR))):
            raise SelectionRefused(f"{winner!r} did not beat routing and linear in {report.report_id}")
    return Selection(task=narrow_task, candidate=winner, report_id=report.report_id,
                     neural=neural)
