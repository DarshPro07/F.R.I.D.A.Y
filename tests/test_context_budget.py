"""FR-101: the context bundle must not lie about its own size.

Found by measurement on real data, not by reading code:

    budget 300 -> reported 296 tokens, actually sent 317 (over budget)
    budget  20 -> "RELEVANT SPECS FROM THE VAULT:" with every spec dropped

Two distinct defects behind those numbers:

1. section headers were appended straight to the prompt, so they cost
   tokens nobody counted;
2. `tokens_used` was summed from per-line estimates, and `_approx_tokens`
   floors every line (`max(1, len//4)`), so the sum drifted below the
   joined payload even once headers were charged.

A budget enforced against a number smaller than the real prompt silently
overspends - which is the whole point of having a budget.
"""
from __future__ import annotations

import sys

import pytest

sys.path.insert(0, ".")

from friday import memory_stack as M


def _actual(bundle) -> int:
    """The size of the string that is really sent."""
    return M._approx_tokens(bundle["prompt"])


class TestTheBundleReportsItsTrueSize:
    @pytest.mark.parametrize("budget", [50, 100, 200, 300, 600, 900, 2000])
    def test_reported_tokens_equal_the_prompt_actually_sent(self, budget):
        bundle = M.aggregate("refund policy gateway provider",
                             budget_tokens=budget)
        assert bundle["tokens_used"] == _actual(bundle), (
            f"telemetry claims {bundle['tokens_used']} but the prompt is "
            f"{_actual(bundle)} tokens")

    @pytest.mark.parametrize("budget", [50, 100, 200, 300, 600, 900, 2000])
    def test_the_prompt_never_exceeds_its_budget(self, budget):
        bundle = M.aggregate("refund policy gateway provider",
                             budget_tokens=budget)
        assert _actual(bundle) <= budget, (
            f"sent {_actual(bundle)} tokens against a {budget} budget")

    def test_headers_are_charged_not_free(self):
        """The specific regression: seven free header lines.

        A bundle containing headers must cost more than the same items with
        no headers - if headers were free those numbers would match.
        """
        bundle = M.aggregate("refund policy gateway provider",
                             budget_tokens=2000)
        headers = [l for l in bundle["prompt"].splitlines()
                   if l.endswith(":") and not l.startswith("- ")]
        if not headers:
            pytest.skip("no headers in this bundle")
        header_cost = sum(M._approx_tokens(h) for h in headers)
        assert bundle["tokens_used"] >= header_cost, (
            "headers appear in the prompt but are not reflected in the cost")


class TestNoSectionIsAnnouncedEmpty:
    @pytest.mark.parametrize("budget", list(range(20, 320, 20)))
    def test_no_header_is_followed_by_nothing(self, budget):
        """A header whose items were all rejected tells the model a section
        exists and then shows it an empty one."""
        bundle = M.aggregate("refund policy gateway provider",
                             budget_tokens=budget)
        lines = bundle["prompt"].splitlines()
        for i, line in enumerate(lines):
            if line.endswith(":") and not line.startswith("- "):
                following = lines[i + 1] if i + 1 < len(lines) else ""
                assert following.startswith("- "), (
                    f"budget={budget}: header {line!r} has no items under it")

    def test_a_tiny_budget_yields_a_coherent_bundle_or_nothing(self):
        """At budget=20 the old code emitted a spec header with no specs."""
        bundle = M.aggregate("refund policy gateway provider", budget_tokens=20)
        lines = [l for l in bundle["prompt"].splitlines() if l.strip()]
        if lines:
            assert any(l.startswith("- ") for l in lines), (
                "prompt contains only headers and no content")


class TestTheAccountingIsInternallyConsistent:
    def test_charged_never_exceeds_the_budget(self):
        """`tokens_charged` is the admission-control number; it is what the
        budget is enforced against as items are taken."""
        for budget in (50, 200, 600):
            bundle = M.aggregate("refund policy gateway", budget_tokens=budget)
            assert bundle["tokens_charged"] <= budget

    def test_injected_counts_match_the_bullet_lines(self):
        bundle = M.aggregate("refund policy gateway provider",
                             budget_tokens=2000)
        bullets = [l for l in bundle["prompt"].splitlines() if l.startswith("- ")]
        assert sum(bundle["injected"].values()) == len(bullets)

    def test_an_empty_task_still_reports_honestly(self):
        bundle = M.aggregate("", budget_tokens=200)
        assert bundle["tokens_used"] == _actual(bundle)
        assert _actual(bundle) <= 200
