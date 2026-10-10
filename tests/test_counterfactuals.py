"""
Tests for friday/counterfactuals.py (CF-001, PRD section 95).

An important plan is never judged alone: the record must carry doing nothing,
deferring and running an experiment beside it, each option with the claims for
it, and each claim's evidence class kept apart rather than blended.
"""
from __future__ import annotations

import pytest

from friday import counterfactuals as CF
from friday import epistemic as E


def _claim(text, status=E.INFERRED, **kw):
    if status == E.SOURCED:
        kw.setdefault("sources", ("https://a.example",))
    return E.LedgerClaim(claim=text, status=status, **kw)


def _options(**override):
    base = {
        CF.PROCEED: CF.Option(CF.PROCEED, "Migrate the store now", (
            _claim("Migration takes two days", E.MEASURED),
            _claim("Users will not notice", E.SPECULATIVE))),
        CF.DO_NOTHING: CF.Option(CF.DO_NOTHING, "Keep the current store", (
            _claim("The current store loses writes under load", E.OBSERVED),)),
        CF.DEFER: CF.Option(CF.DEFER, "Wait for the next release", (
            _claim("The next release ships a fix", E.SOURCED),)),
        CF.EXPERIMENT: CF.Option(CF.EXPERIMENT, "Migrate one workspace first", (
            _claim("One workspace is enough to see the load issue", E.INFERRED),)),
    }
    base.update(override)
    return list(base.values())


def test_the_record_lists_every_option_with_its_claims_and_evidence_classes():
    record = CF.DecisionRecord(plan="Migrate the store", options=_options(),
                               chosen=CF.EXPERIMENT, rationale="cheapest test")

    shown = record.as_dict()

    assert [o["kind"] for o in shown["options"]] == [
        CF.PROCEED, CF.DO_NOTHING, CF.DEFER, CF.EXPERIMENT]
    proceed = shown["options"][0]
    assert [(c["claim"], c["evidence_class"]) for c in proceed["claims"]] == [
        ("Migration takes two days", E.MEASURED),
        ("Users will not notice", E.SPECULATIVE)]
    assert shown["chosen"] == CF.EXPERIMENT
    assert shown["rationale"] == "cheapest test"
    assert proceed["claims"][0]["id"].startswith("clm_")


def test_evidence_classes_are_counted_apart_never_blended():
    record = CF.DecisionRecord(plan="p", options=_options(), chosen=CF.PROCEED)

    proceed = record.as_dict()["options"][0]

    assert proceed["evidence_classes"] == {E.MEASURED: 1, E.SPECULATIVE: 1}
    assert "score" not in proceed and "confidence" not in proceed


@pytest.mark.parametrize("missing", [CF.DO_NOTHING, CF.DEFER, CF.EXPERIMENT])
def test_a_plan_without_a_baseline_option_is_refused(missing):
    kept = [o for o in _options() if o.kind != missing]

    with pytest.raises(ValueError, match=missing):
        CF.DecisionRecord(plan="p", options=kept, chosen=CF.PROCEED)


def test_a_plan_with_no_option_for_itself_is_refused():
    kept = [o for o in _options() if o.kind != CF.PROCEED]

    with pytest.raises(ValueError, match=CF.PROCEED):
        CF.DecisionRecord(plan="p", options=kept, chosen=CF.DEFER)


def test_an_option_with_no_claims_is_refused():
    with pytest.raises(ValueError):
        CF.Option(CF.DEFER, "Wait", ())


def test_an_unknown_kind_or_chosen_option_is_refused():
    with pytest.raises(ValueError):
        CF.Option("wishful", "x", (_claim("c"),))
    with pytest.raises(ValueError):
        CF.DecisionRecord(plan="p", options=_options(), chosen="somewhere-else")


def test_extra_alternatives_are_kept_and_can_be_chosen():
    alt = CF.Option(CF.ALTERNATIVE, "Use a hosted store", (
        _claim("Hosted costs more", E.SOURCED),), name="hosted")

    record = CF.DecisionRecord(plan="p", options=_options() + [alt],
                               chosen="hosted")

    assert record.as_dict()["chosen"] == "hosted"
    assert len(record.as_dict()["options"]) == 5


def test_an_empty_plan_is_refused():
    with pytest.raises(ValueError):
        CF.DecisionRecord(plan="  ", options=_options(), chosen=CF.PROCEED)
