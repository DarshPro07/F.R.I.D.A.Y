"""
System One calibration (S1-002): a backend's stated confidence is only worth
what its verified outcomes say it is.

`Calibrator` records, per decision, the confidence that was predicted and
whether the decision later verified as right. It bins them into a calibration
report (per-bin accuracy, expected calibration error) and derives the lowest
confidence at which the verified accuracy meets the target - the calibrated
threshold that gates autonomy. Until enough outcomes exist there is no
threshold and nothing is autonomous.

`decide` asks each backend through its own `SystemOne` (so the data-class
seam still applies per backend), and escalates to System Two when no backend
answers, the answers disagree, the system is uncalibrated, or the weakest
confidence is under the calibrated threshold.

Records are held in memory; making them durable is a later slice.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from friday.system_one import SystemOne

ESCALATE_TO = "system_two"


class Calibrator:
    def __init__(self, bins: int = 10, min_samples: int = 20,
                 target_accuracy: float = 0.9) -> None:
        if bins < 1 or min_samples < 1 or not 0.0 < target_accuracy <= 1.0:
            raise ValueError("bins and min_samples must be >= 1 and target_accuracy in (0, 1]")
        self.bins, self.min_samples, self.target_accuracy = bins, min_samples, target_accuracy
        self._records: dict[str, tuple[float, bool]] = {}

    def record(self, decision_id: str, predicted: float, correct: bool) -> None:
        """One verified outcome for one decision; a decision is recorded once."""
        if isinstance(predicted, bool) or not isinstance(predicted, (int, float)) \
                or not 0.0 <= predicted <= 1.0:
            raise ValueError(f"predicted confidence {predicted!r} is not a number in [0, 1]")
        if decision_id in self._records:
            raise ValueError(f"decision {decision_id!r} already has a recorded outcome")
        self._records[decision_id] = (float(predicted), bool(correct))

    def _bin(self, predicted: float) -> int:
        return min(int(predicted * self.bins), self.bins - 1)

    def report(self) -> dict:
        rows = [{"lo": i / self.bins, "hi": (i + 1) / self.bins, "n": 0,
                 "mean_confidence": 0.0, "accuracy": 0.0} for i in range(self.bins)]
        sums = [[0.0, 0] for _ in rows]
        for predicted, correct in self._records.values():
            i = self._bin(predicted)
            rows[i]["n"] += 1
            sums[i][0] += predicted
            sums[i][1] += correct
        n = len(self._records)
        ece = 0.0
        for row, (conf, right) in zip(rows, sums):
            if row["n"]:
                row["mean_confidence"] = conf / row["n"]
                row["accuracy"] = right / row["n"]
                ece += row["n"] * abs(row["mean_confidence"] - row["accuracy"]) / n
        return {"n": n, "bins": rows, "ece": ece, "target_accuracy": self.target_accuracy,
                "min_samples": self.min_samples, "threshold": self.threshold()}

    def threshold(self) -> float | None:
        """Lowest bin edge from which verified accuracy meets the target, else None."""
        edges = sorted({self._bin(p) for p, _ in self._records.values()})
        for i in edges:
            lo = i / self.bins
            kept = [c for p, c in self._records.values() if self._bin(p) >= i]
            if len(kept) >= self.min_samples and sum(kept) / len(kept) >= self.target_accuracy:
                return lo
        return None


@dataclass
class Decision:
    status: str                      # decided | escalated
    value: object = None
    confidence: float | None = None  # the weakest confidence among the answers
    escalate_to: str = ""
    reason: str = ""                 # no_answer | disagreement | uncalibrated | low_confidence
    answers: dict = field(default_factory=dict)       # backend -> choice
    confidences: dict = field(default_factory=dict)   # backend -> confidence


def decide(backends, prompt: str, options, calibrator: Calibrator) -> Decision:
    answers: dict = {}
    confidences: dict = {}
    for backend in backends:
        one = SystemOne([backend])
        choice = one.choose(prompt, options)
        if choice.status != "ok":
            continue
        sure = one.confidence(str(choice.value), prompt)
        if sure.status != "ok":
            continue
        answers[backend.name] = choice.value
        confidences[backend.name] = sure.value

    def escalate(reason: str, confidence: float | None = None) -> Decision:
        return Decision("escalated", None, confidence, ESCALATE_TO, reason, answers, confidences)

    if not answers:
        return escalate("no_answer")
    weakest = min(confidences.values())
    if len(set(answers.values())) > 1:
        return escalate("disagreement", weakest)
    threshold = calibrator.threshold()
    if threshold is None:
        return escalate("uncalibrated", weakest)
    if weakest < threshold:
        return escalate("low_confidence", weakest)
    return Decision("decided", next(iter(answers.values())), weakest, "", "", answers, confidences)
