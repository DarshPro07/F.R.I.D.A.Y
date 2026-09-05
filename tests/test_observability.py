"""
Observability (PRD v3.1 FR-054; 12.3 Operational Diagnostics).

FR-054 acceptance: "A single trace reconstructs what happened without
reading raw model thoughts." So the trace is built from durable state
only - objective ledger, tool results, gateway calls, audit log, worker
runs - and the test drives a REAL objective through the engine (one task
fails and is retried, one succeeds), then checks the trace tells that
story: transitions, the retry, latency, the error, the verification.

12.3: one diagnostic view, every section best-effort, redacted by default.
"""
from __future__ import annotations

import asyncio
import json
import time

import pytest

from friday import contracts as c
from friday import observability as OB
from friday.store import Store


@pytest.fixture
def store(tmp_path):
    return Store(str(tmp_path / "obs.sqlite3"))


async def _run_objective(store, calls):
    """A real ContinuousTaskExecutor run: `flaky` fails once then
    succeeds; `steady` succeeds. Both leave tool-result rows."""
    from friday.continuous import ContinuousTaskExecutor
    from friday.objectives import compile_objective

    attempts = {"flaky": 0}

    async def capability(name, arguments):
        calls.append(name)
        if name == "flaky":
            attempts["flaky"] += 1
            if attempts["flaky"] == 1:
                raise TimeoutError("transient upstream 503")   # TRANSIENT -> retried
        await asyncio.sleep(0.01)
        return {"ok": True, "capability": name}

    run = compile_objective(
        store, request="observability probe",
        tasks=[{"capability": "steady", "arguments": {}},
               {"capability": "flaky", "arguments": {}, "dependencies": ["t1"]}],
        manifest=[{"id": "steady", "description": "steady"},
                  {"id": "flaky", "description": "flaky"}],
        objective_summary="observability probe")
    ex = ContinuousTaskExecutor(store, capability, executor_id="obs-1")
    ex.stop()
    await ex.start(run["run_id"])
    return run["run_id"]


def test_trace_reconstructs_a_real_objective_from_durable_state(store, monkeypatch):
    calls: list[str] = []
    run_id = asyncio.run(_run_objective(store, calls))
    # Something the trace must also see: a capability run recorded against
    # this objective id, a gateway call and a policy decision for it.
    run = c.Run(run_id=run_id, request="probe tool", capability="web")
    started = c.started(run_id, "web.search")
    run.record(c.succeeded(started, output={"hits": 1},
                           verification=c.Verification(method="http", evidence="200 OK")))
    store.save_run(run)
    from friday import trust as T
    T.audit().record(actor="friday", action="files.delete", tier=T.R2, decision="ASK",
                     target="C:/x", objective_id=run_id, detail={"api_key": "sk-abcdefghijklmnopqrstuvwxyz"})
    from friday.model_gateway import GatewayTelemetry
    tel = GatewayTelemetry(store.path.parent / "gw.sqlite3")
    monkeypatch.setattr("friday.model_gateway.GatewayTelemetry", lambda *a, **k: tel)
    tel.record(objective_id=run_id, worker="friday", task_class="SIMPLE", provider="fake",
               model="fake-1", status="ok", latency_ms=12, input_tokens=10, output_tokens=5)

    t = OB.trace(run_id, store=store)
    assert t["found"] and t["status"] == "COMPLETED"
    assert {x["capability"] for x in t["tasks"]} == {"steady", "flaky"}
    flaky = next(x for x in t["tasks"] if x["capability"] == "flaky")
    assert flaky["attempts"] >= 2 and flaky["status"] == "SUCCEEDED"
    assert t["retries"] >= 1
    kinds = {row["kind"] for row in t["timeline"]}
    assert {"objective", "tool", "model", "policy"} <= kinds, kinds
    events = [row["event"] for row in t["timeline"] if row["kind"] == "objective"]
    assert "run.completed" in events
    assert "task.retry" in events, events                    # the transient failure is in the story
    retry = next(row for row in t["timeline"] if row["event"] == "task.retry")
    assert retry["detail"]["kind"] == "TRANSIENT" and "503" in retry["detail"]["reason"]
    assert retry["detail"]["attempt"] == 1
    assert any(row.get("latency_ms") is not None for row in t["timeline"])
    tool = next(row for row in t["timeline"] if row["kind"] == "tool")
    assert tool["verification"] == {"method": "http", "evidence": "200 OK"}
    assert t["verification"]["tasks_with_evidence"] == 2
    assert t["usage"]["gateway_calls"] == 1 and t["usage"]["tokens_in"] == 10 and t["usage"]["tokens_out"] == 5
    policy = next(row for row in t["timeline"] if row["kind"] == "policy")
    assert policy["action"] == "files.delete" and policy["tier"] == "R2"
    # Time order, and no model thoughts anywhere in it.
    ats = [row["at"] for row in t["timeline"]]
    assert ats == sorted(ats)
    assert "sk-abcdefghijklmnopqrstuvwxyz" not in json.dumps(t)
    text = OB.trace_text(run_id, store=store)
    assert text.startswith(f"{run_id} COMPLETED") and "[tool] tool.SUCCEEDED web.search" in text
    assert "[model] gateway.call fake-1" in text


def test_unknown_run_is_said_not_invented(store):
    t = OB.trace("RUN-nope", store=store)
    assert t == {"run_id": "RUN-nope", "found": False, "say": "no objective RUN-nope in the ledger"}


def test_redaction_by_key_and_by_token_shape():
    raw = {"api_key": "plain", "nested": {"Authorization": "Bearer x", "note": "token sk-ant-abcdefghijklmnop123456",
                                          "access_token": "abc", "tokens_in": 10, "token_budget": 4000},
           "list": ["ghp_abcdefghijklmnopqrstuvwxyz0123", "fine"], "n": 3}
    out = OB.redact(raw)
    assert out["api_key"] == OB.REDACTED and out["nested"]["Authorization"] == OB.REDACTED
    assert out["nested"]["access_token"] == OB.REDACTED
    assert "sk-ant-" not in out["nested"]["note"] and OB.REDACTED in out["nested"]["note"]
    assert out["nested"]["tokens_in"] == 10 and out["nested"]["token_budget"] == 4000   # accounting survives
    assert out["list"] == [OB.REDACTED, "fine"] and out["n"] == 3


def test_diagnostics_reports_every_section_and_never_raises(store, monkeypatch):
    # A section that blows up must come back as unavailable, not as a crash.
    monkeypatch.setattr(OB, "_hermes", lambda: (_ for _ in ()).throw(ConnectionError("gateway down")))
    started = time.monotonic()
    d = OB.diagnostics(store=store)
    took = time.monotonic() - started
    expected = {"build", "objective_store", "memory_store", "providers", "hermes", "browser",
                "voice", "mcp_capabilities", "queue", "resource_pressure", "recent_failures"}
    assert expected <= set(d)
    assert d["hermes"]["status"] == "unavailable" and "gateway down" in d["hermes"]["error"]
    assert d["healthy"] is False and d["unavailable"] == ["hermes"]
    assert d["redacted"] is True
    for name in expected:
        assert "probe_ms" in d[name], name
    assert d["objective_store"]["quick_check"] == "ok"
    assert d["resource_pressure"]["pressure"]["level"] in ("NORMAL", "ELEVATED", "HIGH", "CRITICAL")
    assert took < 60, took


def test_diagnostics_surfaces_recent_critical_failures(store):
    from friday.objectives import compile_objective
    run = compile_objective(store, request="doomed", tasks=[{"capability": "x", "arguments": {}}],
                            manifest=[{"id": "x", "description": "x"}], objective_summary="doomed")
    store.append_objective_event(run["run_id"], "task.failed", detail={"error": "boom", "password": "hunter2"})
    d = OB.diagnostics(store=store, sections=("recent_failures", "queue"))
    items = d["recent_failures"]["items"]
    assert any(i.get("event") == "task.failed" for i in items)
    assert "hunter2" not in json.dumps(d)
    assert d["queue"]["objective_tasks_pending"] >= 1


def test_the_tool_faces_are_wired():
    from friday import capabilities as C, capability_router as R, policy as P, semantics as S
    for tool in ("system_diagnostics", "objective_trace"):
        assert tool in C.CAPABILITIES and tool in P.TOOL_CATEGORIES
        assert any(tool in members for members in R.GROUPS.values())
    assert S.for_capability("system_diagnostics")[0] == "READ"
    assert S.for_capability("objective_trace")[0] == "READ"


def test_toolset_faces_run(store, monkeypatch):
    from friday.toolsets import model_gateway as MG
    from friday.toolsets import objectives as OT
    monkeypatch.setattr(OT, "store", lambda: store)
    run_id = asyncio.run(_run_objective(store, []))
    run = c.Run.create("trace", capability="objective")
    out = MG.objective_trace(run, run_id)
    assert out.status == c.SUCCEEDED and out.output["status"] == "COMPLETED"
    run = c.Run.create("trace text", capability="objective")
    out = MG.objective_trace(run, run_id, as_text=True)
    assert out.output["text"].startswith(run_id)
    run = c.Run.create("nothing", capability="objective")
    assert MG.objective_trace(run, "RUN-missing").status == c.FAILED
    run = c.Run.create("diag", capability="system")
    out = MG.system_diagnostics(run, sections="queue,memory_store")
    assert out.status == c.OBSERVED and set(out.output) >= {"queue", "memory_store", "healthy"}
