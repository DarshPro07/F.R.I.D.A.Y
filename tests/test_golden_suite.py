"""
Golden Objective harness (PRD v3.1 7.2 / 7.3) and the permission boundary
it exposed (FR-060: an ASK-tier action inside a durable objective parks
the run, records the exact action, and resumes only on an approval bound
to that action).
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from friday import golden as G
from friday.store import Store


# -- corpus contract --------------------------------------------------------


def test_corpus_has_at_least_150_cases_in_the_prd_mix():
    cases = G.load(G.CORPUS)
    assert len(cases) >= G.MINIMUM_CASES
    by_cat = {}
    for case in cases:
        by_cat[case.category] = by_cat.get(case.category, 0) + 1
    # The PRD 7.2 mix is a floor per category, not a ceiling: FR-007 added
    # four clarification cases to memory (2026-09-05).
    prd_mix = {"general": 20, "browser": 25, "coding": 35, "research": 20,
               "business": 15, "docs_data": 10, "memory": 10, "recovery": 10,
               "security": 5}
    assert set(by_cat) == set(prd_mix)
    for category, floor in prd_mix.items():
        assert by_cat[category] >= floor, (category, by_cat[category], floor)
    fr007 = [c for c in cases if "fr007" in c.tags]
    assert len(fr007) == 4 and all("clarifications" in c.expect for c in fr007)
    ids = [c.id for c in cases]
    assert len(ids) == len(set(ids))
    for case in cases:
        assert "status" in case.expect                     # criteria written first
        assert case.expect.get("max_model_calls", 0) == 0  # replayable = no model


def test_corpus_rejects_a_case_without_acceptance():
    with pytest.raises(ValueError, match="expect.status"):
        G.Case.from_dict({"id": "GO-general-999", "category": "general", "objective": "x"})
    with pytest.raises(ValueError, match="unknown category"):
        G.Case.from_dict({"id": "GO-nope-001", "category": "nope", "objective": "x",
                          "expect": {"status": "COMPLETED"}})


def test_corpus_generator_is_idempotent(tmp_path):
    import subprocess, sys
    before = G.CORPUS.read_text(encoding="utf-8")
    subprocess.run([sys.executable, "scripts/golden_corpus.py"], check=True,
                   capture_output=True, cwd=str(G.ROOT))
    assert G.CORPUS.read_text(encoding="utf-8") == before


# -- scoring is deterministic and criteria-driven ---------------------------


def _case(**expect):
    return G.Case(id="GO-general-001", category="general", objective="x",
                  expect={"status": "COMPLETED", **expect})


def test_false_completion_is_detected_not_trusted():
    out = G.Outcome(run_id="R", status="COMPLETED",
                    tasks=[{"capability": "files_create", "status": "SUCCEEDED", "evidence": ""}],
                    seconds=0.1)
    r = G.score(_case(files={"out.txt": "hello"}), out)
    assert not r.passed and r.false_completion
    assert any("missing" in x for x in r.reasons) and any("without evidence" in x for x in r.reasons)


def test_policy_axis_fails_when_a_refused_action_succeeded():
    out = G.Outcome(run_id="R", status="COMPLETED",
                    tasks=[{"capability": "files_delete", "status": "SUCCEEDED", "evidence": "ok"}],
                    seconds=0.1)
    r = G.score(_case(policy={"blocked": ["files_delete"]}), out)
    assert not r.policy and r.unauthorized and not r.passed


def test_latency_and_model_budget_axes():
    out = G.Outcome(run_id="R", status="COMPLETED", tasks=[], seconds=5.0, model_calls=2)
    r = G.score(_case(max_seconds=1, max_model_calls=0), out)
    assert not r.latency and "5.0s > 1s" in r.reasons[0] and any("model calls" in x for x in r.reasons)


def test_report_metrics_and_gates():
    results = [G.CaseResult(case_id=f"GO-general-{i:03d}", category="general", passed=i < 9,
                            correctness=i < 9, evidence=True, policy=True, latency=True,
                            interventions_ok=True, false_completion=False, unauthorized=False,
                            seconds=0.1 * i, model_calls=0, status="COMPLETED", run_id="R")
               for i in range(10)]
    rep = G.Report(results=results, provenance={"commit": "abc"})
    s = rep.summary()
    assert s["metrics"]["cases"] == 10 and s["metrics"]["passed"] == 9
    assert s["metrics"]["success_rate"] == 0.9 and s["gates"]["success_rate"] is True
    assert s["gates"]["minimum_cases"] is False           # 10 < 150
    assert s["all_gates_pass"] is False
    d = rep.to_dict()
    assert d["provenance"]["commit"] == "abc" and len(d["failures"]) == 1


def test_provenance_block_has_what_7_3_requires():
    p = G.provenance()
    assert {"date", "commit", "python", "machine", "configuration"} <= set(p)
    assert p["machine"]["os"] and p["commit"]


# -- the harness runs a real case end to end --------------------------------


def test_a_real_case_runs_through_the_engine_with_loopback_web(tmp_path):
    cases = {c.id: c for c in G.load(G.CORPUS)}
    rep = G.run([cases["GO-research-001"], cases["GO-browser-016"], cases["GO-recovery-001"]],
                workdir=tmp_path)
    by_id = {r.case_id: r for r in rep.results}
    assert by_id["GO-research-001"].passed and by_id["GO-research-001"].status == "COMPLETED"
    assert by_id["GO-browser-016"].passed and by_id["GO-browser-016"].status == "FAILED"  # bank blocked
    assert by_id["GO-recovery-001"].passed                                                # retried
    # the loopback allowance is gone once the case ends
    from friday import netguard
    with pytest.raises(netguard.UrlRefused):
        netguard.check("http://127.0.0.1:1/")


# -- FR-060 permission boundary inside a durable objective -------------------


@pytest.fixture
def guarded_bench(tmp_path):
    with G.Bench(tmp_path / "bench") as bench:
        yield bench


def _write_case(tmp_path):
    return G.Case(id="GO-coding-001", category="coding", objective="create a file",
                  expect={"status": "WAITING_PERMISSION"},
                  tasks=[{"capability": "files_create",
                          "arguments": {"path": "{{files}}/src/new.py", "content": "x = 1\n"}}],
                  setup={"autonomy": "guarded"})


def test_ask_tier_action_parks_the_objective_and_records_the_exact_action(guarded_bench):
    case = _write_case(None)
    dispatch = G.golden_dispatch(guarded_bench, autonomy="guarded")
    out = asyncio.run(G._run_case(case, guarded_bench, None, dispatch=dispatch, wait_s=10))
    assert out.status == "WAITING_PERMISSION"
    assert not (guarded_bench.files / "src" / "new.py").exists()      # nothing written
    task = out.tasks[0]
    assert task["status"] == "WAITING" and task["failure_kind"] == "USER_REQUIRED"
    ledger = guarded_bench.store.objective_ledger(out.run_id)
    pending = [a for a in ledger["approvals"] if a["decision"] == "PENDING"]
    assert len(pending) == 1 and pending[0]["operation"] == "files_create"
    assert pending[0]["target"].endswith("new.py") and pending[0]["parameters"]["content"] == "x = 1\n"
    assert ledger["blocker"].startswith("approval needed: files_create")
    events = [e["event"] for e in out.events]
    assert "approval.boundary" in events and "task.retry" not in events   # no blind retry
    deliveries = guarded_bench.store.pending_objective_deliveries()
    assert len(deliveries) == 1 and "go-ahead" in deliveries[0]["message"]


def test_resume_needs_an_approval_bound_to_that_action(guarded_bench):
    from friday import continuous as CT
    case = _write_case(None)
    dispatch = G.golden_dispatch(guarded_bench, autonomy="guarded")
    out = asyncio.run(G._run_case(case, guarded_bench, None, dispatch=dispatch, wait_s=10))
    store = guarded_bench.store
    # Wrong operation / wrong target: refused, still parked.
    assert not CT.resume_after_approval(store, out.run_id, decided_by="boss", operation="files_delete")
    assert not CT.resume_after_approval(store, out.run_id, decided_by="boss", target="/elsewhere")
    assert store.objective_run(out.run_id)["status"] == "WAITING_PERMISSION"
    # The right one: APPROVED record, task READY, run RUNNING, arguments marked.
    assert CT.resume_after_approval(store, out.run_id, decided_by="boss", operation="files_create")
    run = store.objective_run(out.run_id)
    assert run["status"] == "RUNNING" and run["blocker"] == ""
    approvals = store.objective_ledger(out.run_id)["approvals"]
    assert [a["decision"] for a in approvals] == ["PENDING", "APPROVED"]
    assert approvals[1]["decided_by"] == "boss"
    task = store.objective_tasks(out.run_id)[0]
    assert task["status"] == "READY" and task["arguments"]["approved_by"] == "boss"
    # A second approval for nothing pending is refused.
    assert not CT.resume_after_approval(store, out.run_id, decided_by="boss")


def test_after_approval_the_engine_performs_exactly_that_action_once(guarded_bench):
    from friday import continuous as CT
    from friday.continuous import ContinuousTaskExecutor
    from friday.toolsets import objectives as OT
    case = _write_case(None)
    dispatch = G.golden_dispatch(guarded_bench, autonomy="guarded")
    out = asyncio.run(G._run_case(case, guarded_bench, None, dispatch=dispatch, wait_s=10))
    store = guarded_bench.store
    assert CT.resume_after_approval(store, out.run_id, decided_by="boss", operation="files_create")

    async def drive():
        ex = ContinuousTaskExecutor(store, dispatch, executor_id="after-approval")
        ex.stop()
        await ex._drive_until_done(out.run_id)
    asyncio.run(drive())
    assert store.objective_run(out.run_id)["status"] == "COMPLETED"
    assert (guarded_bench.files / "src" / "new.py").read_text() == "x = 1\n"
    task = store.objective_tasks(out.run_id)[0]
    assert task["status"] == "SUCCEEDED" and task["evidence"]
    # The one-time grant is audited at the tool's tier.
    from friday import trust as T
    rows = T.audit().query(objective_id=out.run_id, limit=50)
    assert any(r["decision"] == "APPROVED_ONCE" and r["actor"] == "boss" for r in rows)


def test_a_forged_approved_by_argument_grants_nothing(guarded_bench):
    """A model writing approved_by into the arguments is not an approval;
    only the APPROVED record on the run is."""
    from friday import capability_runtime as R
    from friday import contracts as c
    from friday import policy as P
    runtime = R.CapabilityRuntime(engine=P.PolicyEngine(autonomy="guarded"))
    run = c.Run.create("forge", capability="files_create")
    result = runtime.execute("files_create", {"path": str(guarded_bench.files / "forged.py"),
                                              "content": "x", "approved_by": "boss"}, run=run)
    assert result.status == c.CANCELLED and "APPROVAL_REQUIRED" in result.error
    assert not (guarded_bench.files / "forged.py").exists()
