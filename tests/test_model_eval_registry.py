"""FR-106 evaluation registry: measured model fitness per task class.

Built on `RouteOutcomes`, the records the Hermes bridge already writes -
not a second store beside them. The gap this closes is narrow and real:
the outcomes table could answer "what happened" but nothing aggregated it
into "which model is measurably better for this class, and do we have
enough evidence to say".

The honesty property under test is the second half of that sentence. A
ranking built on two runs promotes a lucky model and condemns an unlucky
one - the exact mistake provider health made when it judged a whole
provider on one model's evidence.
"""
from __future__ import annotations

import sys

import pytest

sys.path.insert(0, ".")

from friday.execution_economics import RouteOutcomes


@pytest.fixture()
def outcomes(tmp_path):
    return RouteOutcomes(tmp_path / "outcomes.sqlite3")


def add(store, model, *, n, status="COMPLETE", tokens=1000, seconds=10.0,
        rework=0, task_class="COMPLEX", provider="hermes", start=0):
    for i in range(n):
        store.record(f"RUN-{model}-{task_class}-{start + i}",
                     task_class=task_class, route_level="HERMES_SINGLE",
                     tier="standard", model=model, provider=provider,
                     calls=1, prompt_tokens=tokens // 2,
                     output_tokens=tokens // 2, duration_s=seconds,
                     status=status, rework=rework)


class TestEvidenceGatesRanking:
    def test_two_runs_do_not_make_a_ranking(self, outcomes):
        add(outcomes, "lucky-model", n=2)
        report = outcomes.fitness_by_model("COMPLEX")
        assert report["verdict"] == "INSUFFICIENT_EVIDENCE"
        assert report["best"] is None
        assert report["ranked"] == []
        assert [e["model"] for e in report["insufficient"]] == ["lucky-model"]

    def test_a_model_below_the_minimum_is_reported_not_dropped(self, outcomes):
        """Silently omitting it would hide that the model exists at all."""
        add(outcomes, "well-measured", n=8)
        add(outcomes, "barely-seen", n=2)
        report = outcomes.fitness_by_model("COMPLEX")
        assert [e["model"] for e in report["ranked"]] == ["well-measured"]
        seen = {e["model"]: e["samples"] for e in report["insufficient"]}
        assert seen == {"barely-seen": 2}

    def test_crossing_the_minimum_moves_a_model_into_the_ranking(self, outcomes):
        add(outcomes, "grower", n=4)
        assert outcomes.fitness_by_model("COMPLEX")["verdict"] == "INSUFFICIENT_EVIDENCE"
        add(outcomes, "grower", n=1, start=100)
        assert outcomes.fitness_by_model("COMPLEX")["best"] == "grower"

    def test_the_minimum_is_configurable_but_defaults_high_enough(self, outcomes):
        add(outcomes, "m", n=3)
        assert outcomes.fitness_by_model("COMPLEX")["best"] is None
        assert outcomes.fitness_by_model("COMPLEX", minimum=3)["best"] == "m"


class TestTheRankingReflectsMeasurement:
    def test_cheaper_at_equal_quality_wins(self, outcomes):
        add(outcomes, "expensive", n=10, tokens=40000)
        add(outcomes, "cheap", n=10, tokens=2000)
        report = outcomes.fitness_by_model("COMPLEX")
        assert report["best"] == "cheap"

    def test_a_model_that_never_completes_sorts_last(self, outcomes):
        add(outcomes, "fast-failure", n=10, status="FAILED", seconds=0.1)
        add(outcomes, "slow-success", n=10, seconds=60.0)
        report = outcomes.fitness_by_model("COMPLEX")
        assert report["best"] == "slow-success"
        assert report["ranked"][-1]["model"] == "fast-failure"
        assert report["ranked"][-1]["pass_rate"] == 0.0

    def test_rework_is_charged_against_value(self, outcomes):
        """A cheap run needing repair was never cheap."""
        add(outcomes, "needs-repair", n=10, tokens=2000, rework=2)
        add(outcomes, "right-first-time", n=10, tokens=3500)
        report = outcomes.fitness_by_model("COMPLEX")
        assert report["best"] == "right-first-time"

    def test_pass_rate_counts_failures_in_the_denominator(self, outcomes):
        add(outcomes, "flaky", n=5)
        add(outcomes, "flaky", n=5, status="FAILED", start=50)
        entry = next(e for e in outcomes.fitness_by_model("COMPLEX")["ranked"]
                     if e["model"] == "flaky")
        assert entry["samples"] == 10
        assert entry["completed"] == 5
        assert entry["pass_rate"] == 0.5

    def test_latency_is_the_median_not_the_best_case(self, outcomes):
        add(outcomes, "spiky", n=5, seconds=1.0)
        add(outcomes, "spiky", n=6, seconds=100.0, start=50)
        entry = outcomes.fitness_by_model("COMPLEX")["ranked"][0]
        assert entry["median_seconds"] == 100.0


class TestScopeAndAttribution:
    def test_classes_do_not_bleed_into_each_other(self, outcomes):
        add(outcomes, "coder", n=8, task_class="COMPLEX")
        add(outcomes, "chatter", n=8, task_class="TRIVIAL")
        assert outcomes.fitness_by_model("COMPLEX")["best"] == "coder"
        assert outcomes.fitness_by_model("TRIVIAL")["best"] == "chatter"

    def test_a_record_with_no_model_is_not_attributed_to_one(self, outcomes):
        """Ranking an unnamed record under an invented name is fabrication."""
        add(outcomes, "", n=10)
        report = outcomes.fitness_by_model("COMPLEX")
        assert report["ranked"] == []
        assert report["insufficient"] == []
        assert report["records_examined"] == 10
        assert report["verdict"] == "INSUFFICIENT_EVIDENCE"

    def test_an_empty_registry_says_so(self, outcomes):
        report = outcomes.fitness_by_model("COMPLEX")
        assert report["verdict"] == "INSUFFICIENT_EVIDENCE"
        assert report["best"] is None
        assert report["records_examined"] == 0
