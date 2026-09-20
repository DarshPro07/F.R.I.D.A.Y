"""FR-106: fitness routing must rest on evidence, never on opinion.

The PRD asks for a Model Fitness Router that picks "the cheapest healthy
model above a quality threshold". The instruction attached to it was
explicit: do NOT build an opinion-based router - `coding -> Claude,
cheap -> Gemini` is a table of preferences wearing the costume of an
architecture.

Audit first. Two evidence-driven selectors already exist:

  * `executor_router.choose(task, record=...)` picks a CODING EXECUTOR from
    measured pass rates, with an evidence MINIMUM below which it refuses to
    claim a measured winner and says so;
  * `execution_economics.RouteOutcomes` records per-(task_class, tier,
    model) outcomes and scores them with `execution_value` =
    quality x success / (tokens + latency + rework).

So the missing piece is not a router. It is the registry view over
RouteOutcomes that answers "which models have PROVEN they can do this task
class, and what did they cost" - plus the discipline that an unproven model
is not silently promoted.

These tests pin the properties that keep fitness honest. They test the
selectors that exist rather than a new one, and they are the reason FR-106
is recorded PARTIAL rather than MISSING: routing by measured evidence works
for executors today; the model-side registry has the data and not yet the
consumer.
"""
from __future__ import annotations

import pytest

from friday import execution_economics as econ
from friday import executor_router


class TestEvidenceBeatsOpinion:
    def test_without_enough_evidence_no_measured_winner_is_claimed(self):
        """The property that separates a fitness router from a preference
        table: below the evidence minimum it must SAY it is guessing."""
        choice = executor_router.choose("some novel task", record=None)
        assert choice.from_evidence is False
        assert choice.because

    def test_a_choice_made_from_evidence_says_so(self, monkeypatch):
        """With more than one usable executor, a measured winner must be
        marked `from_evidence` and name its pass rate.

        On this machine only one executor is installed, so `choose` returns
        "the only executor available here" before evidence is consulted -
        correct behaviour, and the reason this test patches `usable` to
        create a real contest rather than skipping.
        """
        class Record:
            def scored(self, agent):
                return {"pass_rate": 0.9, "attempts": 12}

            def best_for(self, task, minimum=3):
                return "codex"

        monkeypatch.setattr(executor_router, "usable",
                            lambda: ("claude", "codex"))
        choice = executor_router.choose("debug a failing test", record=Record())
        assert choice.executor == "codex"
        assert choice.from_evidence is True
        assert "measured" in choice.because
        assert "90%" in choice.because

    def test_an_unmeasured_contest_falls_back_and_admits_it(self, monkeypatch):
        """Two executors, no evidence: pick the default and say why."""
        class Empty:
            def scored(self, agent):
                return {}

            def best_for(self, task, minimum=3):
                return ""

        monkeypatch.setattr(executor_router, "usable",
                            lambda: ("claude", "codex"))
        choice = executor_router.choose("novel task", record=Empty())
        assert choice.from_evidence is False
        assert "not enough evidence" in choice.because

    def test_the_fallback_explains_why_it_is_a_fallback(self, monkeypatch):
        """Whatever the host has installed, an unmeasured choice says why.

        This used to read the real host: one executor installed here -> "only
        executor"; two -> "not enough evidence". On a CI runner with NO
        executor installed `choose` returns the install hint, which is also
        honest but not a fallback - the test was asserting the host, not the
        router (red on 342f8fd). Every branch is now pinned explicitly."""
        monkeypatch.setattr(executor_router, "usable", lambda: ("claude", "codex"))
        assert "evidence" in executor_router.choose("anything", record=None).because
        monkeypatch.setattr(executor_router, "usable", lambda: ("claude",))
        assert "only executor" in executor_router.choose("anything", record=None).because
        monkeypatch.setattr(executor_router, "usable", lambda: ())
        nothing = executor_router.choose("anything", record=None)
        assert nothing.executor == "" and "install one of" in nothing.because


class TestExecutionValueIsCostAware:
    """`execution_value` = 1,000,000 / (tokens x rework-multiplier + latency).

    Read from the implementation rather than assumed: the record keys are
    `status`, `prompt_tokens`, `output_tokens`, `duration_s`, `rework`, and
    a run that is not COMPLETE scores 0. My first version of these tests
    invented `success`/`quality`/`tokens` and failed against real code -
    recorded because guessing a schema is exactly how a test ends up
    asserting something the system never promised.
    """

    def _outcomes(self, tmp_path):
        return econ.RouteOutcomes(tmp_path / "outcomes.sqlite3")

    def test_an_incomplete_run_scores_zero_however_cheap_it_was(self, tmp_path):
        out = self._outcomes(tmp_path)
        assert out.execution_value({
            "status": "FAILED", "prompt_tokens": 10, "output_tokens": 0,
            "duration_s": 0.1, "rework": 0}) == 0.0

    def test_rework_reduces_value_rather_than_being_ignored(self, tmp_path):
        out = self._outcomes(tmp_path)
        clean = out.execution_value({"status": "COMPLETE", "prompt_tokens": 500,
                                     "output_tokens": 500, "duration_s": 10, "rework": 0})
        redone = out.execution_value({"status": "COMPLETE", "prompt_tokens": 500,
                                      "output_tokens": 500, "duration_s": 10, "rework": 1})
        assert redone < clean

    def test_a_repair_costs_about_twice_the_original(self, tmp_path):
        """The documented arithmetic: one rework triples the cost basis
        (1 + 2x1), so value falls to roughly a third."""
        out = self._outcomes(tmp_path)
        clean = out.execution_value({"status": "COMPLETE", "prompt_tokens": 1000,
                                     "output_tokens": 0, "duration_s": 0, "rework": 0})
        redone = out.execution_value({"status": "COMPLETE", "prompt_tokens": 1000,
                                      "output_tokens": 0, "duration_s": 0, "rework": 1})
        assert 0.30 < (redone / clean) < 0.36

    def test_a_cheaper_equal_quality_route_scores_higher(self, tmp_path):
        out = self._outcomes(tmp_path)
        cheap = out.execution_value({"status": "COMPLETE", "prompt_tokens": 250,
                                     "output_tokens": 250, "duration_s": 5, "rework": 0})
        dear = out.execution_value({"status": "COMPLETE", "prompt_tokens": 2500,
                                    "output_tokens": 2500, "duration_s": 50, "rework": 0})
        assert cheap > dear

    def test_latency_is_priced_not_free(self, tmp_path):
        """Two runs of identical token cost must not score identically when
        one took ten times as long."""
        out = self._outcomes(tmp_path)
        fast = out.execution_value({"status": "COMPLETE", "prompt_tokens": 1000,
                                    "output_tokens": 0, "duration_s": 1, "rework": 0})
        slow = out.execution_value({"status": "COMPLETE", "prompt_tokens": 1000,
                                    "output_tokens": 0, "duration_s": 60, "rework": 0})
        assert fast > slow


class TestRoutingNeverInventsAModel:
    def test_the_tier_table_only_names_models_the_profile_lists(self):
        """A router that can name a model the provider does not have will
        fail at call time and look like a provider outage."""
        known = econ.known_models()
        if not known:
            pytest.skip("no provider cache on this machine")
        for tier in (econ.TIER_ECONOMY, econ.TIER_STANDARD, econ.TIER_DEEP):
            model = econ.resolve_model(tier)
            if model:
                assert model in known, f"{tier} -> {model} is not in the cache"

    def test_an_absent_cache_disables_validation_rather_than_refusing_all(self):
        """A fresh install has no cache; refusing every model then would
        make the product unusable on day one."""
        assert isinstance(econ.known_models(), frozenset)
