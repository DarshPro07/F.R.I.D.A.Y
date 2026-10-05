"""
The Context Lab (PRD v7 19.11, EVAL-002): every listed scenario is run against
the full-history baseline or reported as unexercised with a reason, and the
golden release report carries the resulting context metrics.
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path

import pytest

from friday import context_lab as L
from friday import golden as G

PRD = Path(__file__).resolve().parent.parent / "docs" / "PRD_V7.md"


@pytest.fixture(scope="module")
def results():
    return L.run()


def test_the_scenarios_are_the_prd_19_11_list():
    text = PRD.read_text(encoding="utf-8")
    section = text.split("### 19.11 Context Lab", 1)[1]
    line = re.search(r"^Benchmark: (.+)$", section, re.M).group(1).rstrip(".")
    assert tuple(x.strip() for x in line.split(";")) == L.SCENARIOS


def test_every_scenario_is_run_or_explained_never_both():
    named = {L.slug(n) for n in L.SCENARIOS}
    explained = {L.slug(n) for n in L.UNEXERCISED}
    assert set(L.RUNNERS) | explained == named
    assert not set(L.RUNNERS) & explained
    assert all(reason.strip() for reason in L.UNEXERCISED.values())


def test_every_exercised_scenario_passes(results):
    assert [r["scenario"] for r in results] == list(L.SCENARIOS)
    failed = [r["scenario"] for r in results if r["exercised"] and not r["ok"]]
    assert failed == []
    assert sum(r["exercised"] for r in results) == len(L.RUNNERS)


def test_the_managed_strategy_holds_what_the_full_history_baseline_loses(results):
    by = {L.slug(r["scenario"]): r for r in results if r["exercised"]}
    for key in ("lost_in_middle_positions", "tool_schema_bloat", "fresh_handoff",
                "exact_literal_retention", "poisoned_context", "compaction_fidelity"):
        assert by[key]["managed"]["held"] == 1.0, key
        assert by[key]["baseline"]["held"] < 1.0, key
    # nothing the managed strategy does is worse than the baseline at holding
    assert all(r["managed"]["held"] >= r["baseline"]["held"] for r in by.values())


def test_the_summary_counts_coverage_and_savings(results):
    s = L.summarize(results)
    assert s["scenarios"] == len(L.SCENARIOS) == 14
    assert s["exercised"] + len(s["unexercised"]) == s["scenarios"]
    assert s["coverage"] == round(s["exercised"] / s["scenarios"], 4) < 1.0
    assert s["passed"] == s["exercised"] and s["failed"] == 0
    assert s["held_managed"] > s["held_baseline"]
    assert 0 < s["token_ratio"] < 1
    assert set(s["by_scenario"]) == {L.slug(n) for n in L.SCENARIOS}
    assert all("reason" in s["by_scenario"][L.slug(n)] for n in s["unexercised"])


def test_a_compiler_that_drops_the_facts_fails_the_lab(monkeypatch):
    real = L.G.compile_working_set
    monkeypatch.setattr(L.G, "compile_working_set", lambda **kw: real(**{**kw, "facts": None}))
    broken = L.lost_in_middle_positions()
    assert broken["ok"] is False and broken["managed"]["held"] < 1.0
    assert L.summarize([broken])["failed"] == 1


def test_a_run_uses_a_throwaway_vault_and_puts_the_environment_back(monkeypatch):
    monkeypatch.setenv("FRIDAY_ARTIFACT_VAULT", "sentinel")
    seen = []

    def probe():
        seen.append(os.environ["FRIDAY_ARTIFACT_VAULT"])
        return L.poisoned_context()

    monkeypatch.setattr(L, "SCENARIOS", ("poisoned context",))
    monkeypatch.setattr(L, "RUNNERS", {"poisoned_context": probe})
    L.run()
    assert seen[0] != "sentinel" and not Path(seen[0]).exists()
    assert os.environ["FRIDAY_ARTIFACT_VAULT"] == "sentinel"


def _case_results(n=10):
    return [G.CaseResult(case_id=f"GO-general-{i:03d}", category="general", passed=True,
                         correctness=True, evidence=True, policy=True, latency=True,
                         interventions_ok=True, false_completion=False, unauthorized=False,
                         seconds=0.1, model_calls=0, status="COMPLETED", run_id="R")
            for i in range(n)]


def test_the_report_summary_carries_context_metrics_and_gates_on_them(results):
    context = L.summarize(results)
    s = G.Report(results=_case_results(), provenance={}, context=context).summary()
    assert s["context"]["exercised"] == context["exercised"] and s["gates"]["context_lab"] is True
    failing = dict(context, failed=1)
    s = G.Report(results=_case_results(), provenance={}, context=failing).summary()
    assert s["gates"]["context_lab"] is False and s["all_gates_pass"] is False


def test_a_report_without_the_lab_has_no_context_gate():
    s = G.Report(results=_case_results(), provenance={}).summary()
    assert s["context"] == {} and "context_lab" not in s["gates"]


def test_the_golden_release_report_file_includes_context_metrics(results, monkeypatch, tmp_path):
    monkeypatch.setattr(G, "load_all", lambda: [])
    monkeypatch.setattr(L, "run", lambda: results)
    out = tmp_path / "latest.json"
    G.main(["--out", str(out)])
    context = json.loads(out.read_text(encoding="utf-8"))["summary"]["context"]
    assert {"coverage", "held_managed", "held_baseline", "token_ratio", "by_scenario",
            "unexercised"} <= set(context)
    assert set(context["by_scenario"]) == {L.slug(n) for n in L.SCENARIOS}
