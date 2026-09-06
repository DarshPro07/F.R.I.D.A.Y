"""FR-105: width only for genuinely independent work; depth stays sequential.

The PRD asks for a "Width/Depth Planner". `execution_economics.choose_route`
already is one - it returns DETERMINISTIC / FRIDAY_DIRECT / HERMES_SINGLE /
HERMES_MULTI / HERMES_DEEP, where HERMES_MULTI is width (parallel workers)
and everything else is depth (one execution thread). Building a second
planner beside it would be the drift this project keeps refusing.

So these tests verify the BEHAVIOUR the PRD demands of a width/depth
planner, against the router that already exists. The acceptance criterion
quoted verbatim from PRD §7:

    "FRIDAY SHALL not create parallel agents solely because an objective is
    'complex'."

That is the one that matters. Complexity is a reason to think harder, not
to fan out; fanning out on a stateful task is how two workers edit the same
file and both report success.
"""
from __future__ import annotations

import pytest

from friday import execution_economics as econ


def route_for(text, **kw):
    return econ.choose_route(econ.classify_task(text, **kw))


class TestWidthIsOnlyForIndependentWork:
    @pytest.mark.parametrize("text", [
        "research three competitors in parallel",
        "inspect these independent modules as separate workstreams",
        "compare the providers with worker a and worker b",
    ])
    def test_genuinely_independent_work_may_fan_out(self, text):
        assert route_for(text).level == econ.HERMES_MULTI

    @pytest.mark.parametrize("text", [
        "debug the failing test and fix it",
        "refactor the gateway then run the suite",
        "implement the retry and verify it works",
        "fix the crash in the worker loop",
    ])
    def test_sequential_stateful_work_stays_on_one_thread(self, text):
        """debug -> edit -> run -> inspect -> repair is one thread of
        control; splitting it means two workers fighting over the tree."""
        assert route_for(text).level != econ.HERMES_MULTI

    def test_complexity_alone_does_not_buy_parallelism(self):
        """PRD §7 acceptance criterion, stated as a test.

        A high-consequence core change is the most demanding thing here and
        must still be one thread - it escalates the MODEL, not the worker
        count.
        """
        route = route_for("rewrite the authentication core, this is critical "
                          "and touches production security")
        assert route.level == econ.HERMES_DEEP
        assert route.level != econ.HERMES_MULTI

    def test_high_consequence_escalates_depth_not_width(self):
        deep = route_for("delete the production database schema")
        assert deep.tier == econ.TIER_DEEP
        assert deep.level != econ.HERMES_MULTI


class TestDepthIsTheMinimumCapableRoute:
    """PRD §2.2 eliminate -> automate -> delegate, as routing."""

    def test_mechanical_work_spends_no_model_tokens(self):
        route = route_for("how many files are in the tests directory")
        assert route.level == econ.DETERMINISTIC
        assert "zero model tokens" in route.reason

    def test_a_simple_question_needs_no_worker(self):
        route = route_for("what is the default retry budget")
        assert route.level == econ.FRIDAY_DIRECT

    def test_a_tiny_bounded_change_uses_the_economy_tier(self):
        route = route_for("rename file report.txt to report_old.txt")
        assert route.tier == econ.TIER_ECONOMY

    def test_every_route_explains_itself(self):
        """A routing decision with no reason cannot be audited later."""
        for text in ("how many files are here", "debug the failing test",
                     "research three competitors in parallel",
                     "delete the production database"):
            assert route_for(text).reason.strip()


class TestTheLadderIsMonotonicInConsequence:
    def test_consequence_raises_the_tier_but_never_the_width(self):
        low = route_for("rename file a.txt to b.txt")
        high = route_for("delete the production database and drop the table")
        tiers = [econ.TIER_ECONOMY, econ.TIER_STANDARD, econ.TIER_DEEP]
        assert tiers.index(high.tier) > tiers.index(low.tier)
        assert high.level != econ.HERMES_MULTI
        assert low.level != econ.HERMES_MULTI
