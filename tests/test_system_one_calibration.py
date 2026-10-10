"""
S1-002: System One confidence is calibrated empirically - predicted
confidence and the verified outcome are recorded per decision, bins give a
calibration report, autonomy is gated on a calibrated threshold, and low
confidence or backend disagreement escalates to System Two.
"""
from __future__ import annotations

import pytest

from friday import privacy
from friday import system_one as s1
from friday.system_one_calibration import Calibrator, decide

LOCAL_ONLY_TEXT = "LOCAL_ONLY: the Hartwell settlement terms"


class Fixed:
    """A backend that always gives one choice and one confidence."""

    def __init__(self, name, choice, confidence):
        self.name, self.choice, self.confidence = name, choice, confidence
        self.calls = 0

    def call(self, op, prompt, items):
        self.calls += 1
        return self.choice if op == "choose" else self.confidence


@pytest.fixture(autouse=True)
def _open_mode(monkeypatch):
    monkeypatch.delenv(privacy.ENV_VAR, raising=False)


def calibrated() -> Calibrator:
    """High confidence is right 9/10, low confidence 3/10."""
    cal = Calibrator(bins=5, min_samples=5, target_accuracy=0.85)
    for i in range(10):
        cal.record(f"hi{i}", 0.9, i != 0)
    for i in range(10):
        cal.record(f"lo{i}", 0.3, i < 3)
    return cal


def test_report_has_bins_accuracy_and_ece():
    rep = calibrated().report()
    assert rep["n"] == 20
    by_lo = {b["lo"]: b for b in rep["bins"] if b["n"]}
    assert by_lo[0.8]["n"] == 10 and by_lo[0.8]["accuracy"] == pytest.approx(0.9)
    assert by_lo[0.2]["accuracy"] == pytest.approx(0.3)
    # mean |confidence - accuracy| weighted by bin size: (10*0 + 10*0) / 20
    assert rep["ece"] == pytest.approx(0.0)
    assert rep["threshold"] == pytest.approx(0.8)


def test_overconfidence_shows_in_ece():
    cal = Calibrator(bins=5, min_samples=1)
    for i in range(10):
        cal.record(f"d{i}", 0.9, i < 5)  # claims 0.9, right half the time
    assert cal.report()["ece"] == pytest.approx(0.4)


def test_no_threshold_until_enough_verified_outcomes():
    cal = Calibrator(bins=5, min_samples=5)
    cal.record("a", 0.95, True)
    assert cal.threshold() is None and cal.report()["threshold"] is None


def test_record_validates_and_rejects_a_repeat_decision():
    cal = Calibrator()
    with pytest.raises(ValueError):
        cal.record("a", 1.5, True)
    cal.record("a", 0.5, True)
    with pytest.raises(ValueError):
        cal.record("a", 0.5, False)


def test_confident_agreement_is_decided_without_escalation():
    cal = calibrated()
    a, b = Fixed("ollama", "x", 0.9), Fixed("ollama2", "x", 0.95)
    d = decide([a, b], "pick", ["x", "y"], cal)
    assert d.status == "decided" and d.value == "x" and d.escalate_to == ""
    assert d.confidence == pytest.approx(0.9)


def test_low_confidence_escalates_to_system_two():
    d = decide([Fixed("ollama", "x", 0.4), Fixed("ollama2", "x", 0.95)],
               "pick", ["x", "y"], calibrated())
    assert d.status == "escalated" and d.escalate_to == "system_two"
    assert d.reason == "low_confidence"


def test_backend_disagreement_escalates_even_when_both_are_confident():
    d = decide([Fixed("ollama", "x", 0.95), Fixed("ollama2", "y", 0.95)],
               "pick", ["x", "y"], calibrated())
    assert d.status == "escalated" and d.reason == "disagreement"
    assert sorted(d.answers.values()) == ["x", "y"]


def test_uncalibrated_system_never_acts_autonomously():
    d = decide([Fixed("ollama", "x", 0.99)], "pick", ["x", "y"], Calibrator())
    assert d.status == "escalated" and d.reason == "uncalibrated"


def test_no_usable_answer_escalates_and_local_only_never_reaches_hosted():
    hosted = Fixed("hosted-spy", "x", 0.99)
    d = decide([hosted], LOCAL_ONLY_TEXT, ["x", "y"], calibrated())
    assert d.status == "escalated" and d.reason == "no_answer"
    assert hosted.calls == 0
