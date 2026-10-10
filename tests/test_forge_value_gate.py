"""CAP-FORGE-014: the scored relevance gate behind Capability Forge.

Owner directive 2026-10-03 (f): "An external repository SHALL be rejected when
it provides no material new capability, measurable improvement or required
dependency relative to FRIDAY's existing capability set."
"""
from __future__ import annotations

import pytest

from friday import fabric, forge_value_gate as gate

EXISTING = {
    "playwright": ("navigate", "click", "type", "screenshot", "extract_text"),
    "cua": ("navigate", "click", "type", "screenshot", "desktop_control"),
}


def _dup(name, ops):
    return gate.Candidate(repository=name, capabilities=ops)


def test_three_duplicate_browser_agents_are_rejected_and_the_new_one_is_quarantined():
    candidates = [
        _dup("owner/browser-agent-a", ("navigate", "click", "type")),
        _dup("owner/browser-agent-b", ("navigate", "screenshot", "extract_text")),
        _dup("owner/browser-agent-c", ("click", "type", "desktop_control")),
        gate.Candidate(
            repository="owner/worker-computers",
            capabilities=("navigate", "per_worker_profile", "worker_shell"),
            gains={"user_value": 0.6, "reliability": 0.2},
            costs={"security": 0.2, "integration": 0.2},
            fit="subsystem"),
    ]
    decisions = gate.evaluate(candidates, EXISTING)

    dups, new = decisions[:3], decisions[3]
    for d in dups:
        assert d.decision == gate.REJECT
        assert d.disposition == gate.REJECTED_NO_INSTALL
        assert d.installs is False
        assert d.overlap_evidence, "a rejection carries its overlap evidence"
        assert d.overlap_ratio == 1.0
    assert dups[0].overlap_evidence == {
        "playwright": ["navigate", "click", "type"],
        "cua": ["navigate", "click", "type"]}
    assert new.decision == gate.EXTRACT
    assert new.disposition == gate.QUARANTINE_EVALUATE
    assert new.installs is False
    assert 0 < new.overlap_ratio < 1
    assert new.net > 0


def test_every_decision_carries_a_recorded_score_with_all_twelve_dimensions():
    d = gate.evaluate([_dup("x/y", ("brand_new_thing",))], EXISTING)[0]
    record = d.to_record()
    assert set(record["gains"]) == set(gate.GAINS)
    assert set(record["costs"]) == set(gate.COSTS)
    assert record["net"] == pytest.approx(
        sum(record["gains"].values()) - sum(record["costs"].values()))
    assert record["decision"] in gate.DECISIONS
    assert record["gains"]["new_capability"] == 1.0
    assert record["costs"]["duplicate_overlap"] == 0.0


def test_a_measured_improvement_that_outweighs_its_overlap_is_kept():
    d = gate.evaluate([gate.Candidate(
        repository="x/faster", capabilities=("navigate", "click"),
        gains={"latency": 0.6, "reliability": 0.5}, fit="behind_interface")], EXISTING)[0]
    assert d.decision == gate.WRAP
    assert d.disposition == gate.QUARANTINE_EVALUATE


def test_value_below_cost_is_rejected_even_when_new():
    d = gate.evaluate([gate.Candidate(
        repository="x/heavy", capabilities=("novel",),
        costs={"maintenance": 0.9, "security": 0.9, "runtime": 0.5})],
        EXISTING)[0]
    assert d.decision == gate.REJECT
    assert d.net <= 0


@pytest.mark.parametrize("fit,expected", [
    ("direct", gate.ADOPT), ("hardening", gate.EXTEND),
    ("behind_interface", gate.WRAP), ("subsystem", gate.EXTRACT),
    ("ideas", gate.REFERENCE)])
def test_fit_selects_the_non_reject_decision(fit, expected):
    d = gate.evaluate([gate.Candidate(
        repository="x/y", capabilities=("novel",), fit=fit)], EXISTING)[0]
    assert d.decision == expected


def test_a_repository_with_no_declared_capability_or_gain_is_rejected():
    d = gate.evaluate([gate.Candidate(repository="x/empty")], EXISTING)[0]
    assert d.decision == gate.REJECT


@pytest.mark.parametrize("bad", [-0.1, 1.5, float("nan"), True, "high"])
def test_scores_outside_zero_to_one_are_refused(bad):
    with pytest.raises(gate.ValueGateError):
        gate.Candidate(repository="x/y", capabilities=("a",),
                       gains={"quality": bad})


def test_unknown_dimension_or_fit_is_refused():
    with pytest.raises(gate.ValueGateError):
        gate.Candidate(repository="x/y", gains={"vibes": 0.5})
    with pytest.raises(gate.ValueGateError):
        gate.Candidate(repository="x/y", costs={"quality": 0.5})
    with pytest.raises(gate.ValueGateError):
        gate.Candidate(repository="x/y", fit="yolo")


def test_score_value_is_reusable_for_any_named_dimensions():
    s = gate.score_value({"a": 0.5, "b": 0.25}, {"c": 0.5})
    assert s.gain == 0.75 and s.cost == 0.5 and s.net == 0.25


def test_the_fabric_overlap_record_carries_the_value_gate_decision():
    provider = fabric.Provider(
        id="dup_browser", family="browser", upstream="dup/browser",
        operations=("navigate", "click"), risk="low", license_mode="NONE",
        integration_mode=fabric.ADAPTER, commit="a" * 40)
    recorded = fabric._recorded_by_overlap(
        provider, [("playwright", "navigate"), ("playwright", "click")])
    assert "VALUE_GATE: REJECT" in recorded.notes
    assert "overlap=100%" in recorded.notes


# --- review fixes: the real intake front door, required dependency, REFERENCE


def _sha(ch):
    return ch * 40


def _drive(url, decider):
    from friday import intake
    run = intake.IntakeRun(url, "need a browser capability",
                           resolver=lambda o, r, ref: _sha("a"),
                           decider=decider)
    for stage in ("UNDERSTAND", "QUARANTINE", "INSPECT", "DECIDE"):
        run.advance(stage)
    return run


def test_acceptance_through_intake_runs_duplicates_halt_and_new_one_proceeds():
    candidates = [
        _dup("owner/browser-agent-a", ("navigate", "click", "type")),
        _dup("owner/browser-agent-b", ("navigate", "screenshot", "extract_text")),
        _dup("owner/browser-agent-c", ("click", "type", "desktop_control")),
        gate.Candidate(
            repository="owner/worker-computers",
            capabilities=("navigate", "per_worker_profile", "worker_shell"),
            gains={"user_value": 0.6, "reliability": 0.2}, fit="direct"),
    ]
    decider = gate.intake_decider(candidates, EXISTING)
    for name in ("browser-agent-a", "browser-agent-b", "browser-agent-c"):
        run = _drive(f"https://github.com/owner/{name}", decider)
        assert run.decision.kind == "REJECT"
        assert run.next_stage is None
        assert run.installed == ()
    new = _drive("https://github.com/owner/worker-computers", decider)
    assert new.decision.kind == "USE"          # ADOPT, in intake's vocabulary
    assert new.next_stage == "BUILD"
    assert new.installed == ()                 # nothing installed at DECIDE


def test_every_gate_decision_maps_to_an_intake_decision():
    from friday import intake
    assert set(gate.INTAKE_KIND) == set(gate.DECISIONS)
    assert set(gate.INTAKE_KIND.values()) == set(intake.DECISIONS)


def test_an_undescribed_repository_is_rejected_at_intake():
    run = _drive("https://github.com/owner/unknown",
                 gate.intake_decider([], EXISTING))
    assert run.decision.kind == "REJECT" and run.next_stage is None


def test_the_intake_reason_is_capped():
    from friday import intake
    d = gate.assess(_dup("x/y", ("a",)), EXISTING)
    long = gate.ValueDecision(d.repository, d.decision, d.gains, d.costs,
                              d.overlap_ratio, d.overlap_evidence, "r" * 900)
    assert len(long.to_intake_decision().reason) <= intake.REASON_MAX


def test_a_required_dependency_is_kept_though_it_adds_nothing_new():
    plain = gate.Candidate("x/lib", ("navigate", "click"))
    needed = gate.Candidate("x/lib", ("navigate", "click"),
                            gains={"user_value": 0.5},
                            required_by=("playwright",))
    assert gate.assess(plain, EXISTING).decision == gate.REJECT
    kept = gate.assess(needed, EXISTING)
    assert kept.decision == gate.WRAP
    assert "required by playwright" in kept.reason


def test_a_required_dependency_still_loses_when_cost_exceeds_value():
    d = gate.assess(gate.Candidate(
        "x/lib", ("navigate",), costs={"security": 0.9, "maintenance": 0.9},
        required_by=("playwright",)), EXISTING)
    assert d.decision == gate.REJECT and d.net <= 0


def test_ideas_from_a_fully_covered_repository_are_reference_not_reject():
    d = gate.assess(gate.Candidate("x/ideas", ("navigate", "click"),
                                   fit="ideas"), EXISTING)
    assert d.decision == gate.REFERENCE
    assert d.overlap_ratio == 1.0
    assert d.disposition == gate.QUARANTINE_EVALUATE


def test_ideas_with_nothing_declared_is_still_rejected():
    d = gate.assess(gate.Candidate("x/none", fit="ideas"), EXISTING)
    assert d.decision == gate.REJECT


@pytest.mark.parametrize("bad", [None, 1, ""])
def test_capability_and_required_by_names_must_be_strings(bad):
    with pytest.raises(gate.ValueGateError):
        gate.Candidate("x/y", capabilities=(bad,))
    with pytest.raises(gate.ValueGateError):
        gate.Candidate("x/y", capabilities=("a",), required_by=(bad,))


def test_the_fabric_record_says_the_provider_is_kept_as_reference_only():
    provider = fabric.Provider(
        id="dup_browser2", family="browser", upstream="dup/browser2",
        operations=("navigate",), risk="low", license_mode="NONE",
        integration_mode=fabric.ADAPTER, commit="a" * 40)
    recorded = fabric._recorded_by_overlap(provider, [("playwright", "navigate")])
    assert f"VALUE_GATE: REJECT (kept as a {fabric.REFERENCE_ONLY} record)" \
        in recorded.notes
