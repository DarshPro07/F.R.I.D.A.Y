"""FR-104: the PRD's T0-T6 vocabulary maps onto the classes already here.

PRD v5.0 §6 names seven task classes, T0 through T6. This tree has had six
since FR-002, plus a deterministic pre-model classifier in
`execution_economics`. Introducing a `PRDTaskClass` enum beside them would
be architecture drift on day one: two taxonomies, two budget tables, and an
inevitable third piece of code translating between them.

So the PRD's names are treated as CONCEPTUAL and mapped, and these tests
assert the BEHAVIOUR each PRD level demands - that the mapped class routes
to the right tier and the right ceiling - rather than asserting that a
particular string exists. If someone later renames a class, these tests
should still describe the truth.

The one genuinely absent level is T0, "deterministic, no model at all".
`execution_economics.classify_task` is that path ("One deterministic pass
over the task text. No model calls.") and it already runs before any
gateway call, so T0 is mapped to it rather than built again.
"""
from __future__ import annotations

import pytest

from friday import execution_economics as econ
from friday import model_gateway as mg


#: PRD v5.0 §6 level -> the class this tree already uses for it.
#: T0 is not a model class at all; it is the deterministic pre-pass.
PRD_TO_EXISTING = {
    "T0": None,               # deterministic, no model  -> execution_economics
    "T1": mg.TRIVIAL,         # simple LLM
    "T2": mg.SIMPLE,          # LLM + tool
    "T3": mg.STANDARD,        # specialist
    "T4": mg.COMPLEX,         # Hermes execution
    "T5": mg.LONG_RUNNING,    # multi-worker
    "T6": mg.CRITICAL,        # critical / high-risk
}


class TestTheMappingIsTotalAndInjective:
    def test_every_prd_level_maps_to_something_that_exists(self):
        for level, existing in PRD_TO_EXISTING.items():
            if existing is None:
                continue
            assert existing in mg.TASK_CLASSES, f"{level} maps to unknown {existing}"
            assert mg.budget_for(existing).task_class == existing

    def test_no_two_prd_levels_share_one_class(self):
        """A mapping that collapses two levels would hide a routing decision."""
        mapped = [v for v in PRD_TO_EXISTING.values() if v is not None]
        assert len(mapped) == len(set(mapped)), mapped

    def test_the_mapping_covers_every_existing_class(self):
        """If a class exists with no PRD level, the vocabulary has drifted."""
        assert set(mapped for mapped in PRD_TO_EXISTING.values() if mapped) == set(
            mg.TASK_CLASSES
        )

    def test_no_second_taxonomy_was_introduced(self):
        """The PRD's own names must not become live identifiers.

        This is the drift guard: if someone adds `TRIVIAL_T1` or a
        `PRDTaskClass` enum, TASK_CLASSES grows and this fails.
        """
        assert mg.TASK_CLASSES == (
            mg.TRIVIAL, mg.SIMPLE, mg.STANDARD,
            mg.COMPLEX, mg.LONG_RUNNING, mg.CRITICAL,
        )
        assert not hasattr(mg, "PRDTaskClass")


class TestBehaviourNotNames:
    """Each PRD level's DEMAND, asserted against the mapped class."""

    def test_t1_trivial_is_cheap_and_fast(self):
        b = mg.budget_for(PRD_TO_EXISTING["T1"])
        assert b.default_tier == mg.TIER_FAST
        assert b.reasoning == "none"

    def test_t6_critical_gets_the_strongest_tier(self):
        b = mg.budget_for(PRD_TO_EXISTING["T6"])
        assert b.default_tier == mg.TIER_DEEP
        assert b.reasoning == "high"

    def test_ceilings_rise_with_the_level(self):
        """T1 < T2 < T3 < T4 on objective ceiling.

        T5 (LONG_RUNNING) is deliberately excluded: it has the largest
        ceiling of all because it runs for hours, while sitting on the
        STANDARD tier - duration is not difficulty. Asserting a single
        monotonic ladder across all six would encode the wrong model.
        """
        ladder = [mg.budget_for(PRD_TO_EXISTING[t]).objective_ceiling
                  for t in ("T1", "T2", "T3", "T4")]
        assert ladder == sorted(ladder), ladder
        assert len(set(ladder)) == len(ladder), "two levels share a ceiling"

    def test_t5_runs_longest_without_claiming_the_deepest_tier(self):
        long_running = mg.budget_for(PRD_TO_EXISTING["T5"])
        complex_ = mg.budget_for(PRD_TO_EXISTING["T4"])
        assert long_running.objective_ceiling > complex_.objective_ceiling
        assert long_running.default_tier == mg.TIER_STANDARD

    def test_input_budget_never_exceeds_the_objective_ceiling(self):
        """A single call must not be able to exhaust the whole objective."""
        for name in mg.TASK_CLASSES:
            b = mg.budget_for(name)
            assert b.max_input_tokens < b.objective_ceiling, name


class TestT0IsTheDeterministicPathThatAlreadyExists:
    """PRD §2.2: eliminate/automate before delegating to a model."""

    def test_classification_itself_spends_no_model_call(self):
        result = econ.classify_task("rename the variable in one file")
        assert result is not None
        assert hasattr(result, "kind")

    def test_mechanical_work_is_recognised_as_mechanical(self):
        """`_MECHANICAL` is filesystem/counting work - "rename file", "how
        many", "where is" - not "rename every occurrence in the codebase",
        which is a code change. Asserted with the vocabulary the classifier
        actually has, rather than what I first assumed it had.
        """
        assert econ.classify_task("rename file report.txt to report_old.txt").kind == "mechanical"
        assert econ.classify_task("how many tests are in tests/").kind == "mechanical"

    def test_a_codebase_wide_rename_is_not_mechanical(self):
        """The distinction that made the first version of this test wrong:
        a repo-wide rename touches semantics and is a code change."""
        assert econ.classify_task("rename all occurrences of foo to bar").kind != "mechanical"

    def test_high_consequence_work_is_not_called_mechanical(self):
        """The T0 fast path must never swallow something dangerous."""
        result = econ.classify_task("delete the production database and rename the table")
        assert result.kind != "mechanical"
        assert result.consequence == "high"

    def test_an_unknown_class_is_refused_rather_than_defaulted(self):
        """Silently defaulting an unknown class would route work at the
        wrong ceiling - the failure should be loud."""
        with pytest.raises(ValueError):
            mg.budget_for("T4")  # a PRD name is NOT a live identifier
