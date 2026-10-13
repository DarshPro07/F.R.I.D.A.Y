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

# Assembled at run time: the source carries no provider-shaped key for a
# push-protection scanner, while the code under test still sees the whole shape.
FAKE_KEY = "sk-" + "abcdefghijklmnopqrstuvwxyz"
FAKE_ANTHROPIC_KEY = "sk-" + "ant-abcdefghijklmnop123456"
FAKE_GITHUB_TOKEN = "ghp" + "_abcdefghijklmnopqrstuvwxyz0123"


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
        objective_summary="observability probe", provenance=c.PERSON, attended=True)
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
                     target="C:/x", objective_id=run_id, detail={"api_key": FAKE_KEY})
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
    # The objective's own network grant is audited too (NET-001), so the
    # planted decision is found by its action, not by being the only policy
    # row: audit times are to the second, so which comes first is timing.
    policy = next(row for row in t["timeline"]
                  if row["kind"] == "policy" and row["action"] == "files.delete")
    assert policy["tier"] == "R2"
    grant = [row["event"] for row in t["timeline"]
             if row["kind"] == "policy" and row["action"] == "network.grant"]
    assert grant == ["policy.ISSUED", "policy.REVOKED"], grant
    # Time order, and no model thoughts anywhere in it.
    ats = [row["at"] for row in t["timeline"]]
    assert ats == sorted(ats)
    assert FAKE_KEY not in json.dumps(t)
    text = OB.trace_text(run_id, store=store)
    assert text.startswith(f"{run_id} COMPLETED") and "[tool] tool.SUCCEEDED web.search" in text
    assert "[model] gateway.call fake-1" in text


def test_unknown_run_is_said_not_invented(store):
    t = OB.trace("RUN-nope", store=store)
    assert t == {"run_id": "RUN-nope", "found": False, "say": "no objective RUN-nope in the ledger"}


def _blackbox_run(store):
    """A completed objective with a privileged decision recorded against it."""
    from friday import trust as T
    run_id = asyncio.run(_run_objective(store, []))
    T.audit().record(actor="friday", action="files.delete", tier=T.R2, decision="ASK",
                     target="C:/x", objective_id=run_id)
    return run_id


def test_a_completed_run_is_reconstructed_from_its_black_box(store):
    run_id = _blackbox_run(store)
    box = OB.reconstruct(run_id, store=store)
    assert box["found"] and box["consistent"], box["disagreements"]
    # The outcome comes from the event log, and it is the one the engine reached.
    assert box["outcome"]["status"] == "COMPLETED" and box["outcome"]["event"] == "run.completed"
    assert box["outcome"]["replayed_tally"]["succeeded"] == 2
    assert box["outcome"]["recorded_summary"]["succeeded"] == 2
    assert box["ledger_status"] == "COMPLETED"
    # Each task is replayed from events - including the one that failed once
    # and was retried - and ends where the ledger says it does, with its evidence.
    assert {t["capability"] for t in box["tasks"]} == {"steady", "flaky"}
    assert all(t["replayed_status"] == "SUCCEEDED" and t["agrees"] for t in box["tasks"])
    assert box["evidence"]["tasks_with_evidence"] == 2
    # The audit side: the chain verifies and the decision made for the run is in it.
    assert box["audit"]["chain"]["ok"] and box["audit"]["chain"]["intact"]
    assert [d["action"] for d in box["audit"]["decisions"] if d["action"] == "files.delete"]
    # Every decision carries its audit id, oldest first, so a reader can tell
    # the rows apart and match them to the chain.
    ids = [d["id"] for d in box["audit"]["decisions"]]
    assert ids and all(isinstance(i, int) for i in ids) and ids == sorted(set(ids))


def test_reconstruction_trusts_the_events_not_the_ledger_rows(store):
    run_id = _blackbox_run(store)
    flaky = next(t for t in store.objective_tasks(run_id) if t["capability"] == "flaky")
    with store._tx() as conn:
        conn.execute("UPDATE objective_tasks SET status='FAILED' WHERE task_id=?", (flaky["task_id"],))
        conn.execute("UPDATE objective_runs SET status='RUNNING' WHERE run_id=?", (run_id,))
    box = OB.reconstruct(run_id, store=store)
    # The black box still says what happened; the doctored rows are what is named.
    assert box["outcome"]["status"] == "COMPLETED"
    assert next(t for t in box["tasks"] if t["task_id"] == flaky["task_id"])["replayed_status"] == "SUCCEEDED"
    assert not box["consistent"]
    assert any(flaky["task_id"] in d and "FAILED" in d for d in box["disagreements"])
    assert any("COMPLETED" in d and "RUNNING" in d for d in box["disagreements"])


def test_an_edited_audit_row_makes_the_black_box_untrusted(store):
    import sqlite3

    from friday import trust as T
    run_id = _blackbox_run(store)
    other = T.audit().record(actor="friday", action="files.delete", tier=T.R2,
                             decision="ASK", objective_id="RUN-other")
    # An edit after this run's rows leaves them provable ...
    with sqlite3.connect(T.audit().path) as conn:
        conn.execute("UPDATE audit_log SET decision='AUTO' WHERE id=?", (other,))
    later = OB.reconstruct(run_id, store=store)
    assert not later["audit"]["chain"]["ok"] and later["audit"]["chain"]["intact"]
    assert later["consistent"], later["disagreements"]
    # ... an edit to one of this run's own rows does not.
    mine = T.audit().query(objective_id=run_id, limit=1)[0]["id"]
    with sqlite3.connect(T.audit().path) as conn:
        conn.execute("UPDATE audit_log SET decision='AUTO' WHERE id=?", (mine,))
    box = OB.reconstruct(run_id, store=store)
    assert not box["audit"]["chain"]["intact"] and not box["consistent"]
    assert any("audit chain" in d for d in box["disagreements"])


def test_an_unreadable_audit_trail_is_not_a_consistent_black_box(store, monkeypatch):
    from friday import trust as T
    run_id = _blackbox_run(store)
    assert OB.reconstruct(run_id, store=store)["consistent"]

    def unreadable(self, **kw):
        raise OSError("audit store locked")

    # The chain can still be walked, so only the read of this run's rows fails.
    monkeypatch.setattr(T.AuditLog, "query", unreadable)
    box = OB.reconstruct(run_id, store=store)
    # No decisions came back, and that must not read as "none were made".
    assert box["audit"]["decisions"] == [] and box["sources"]["audit_rows"] == 0
    assert not box["consistent"]
    assert any("audit rows could not be read" in d and "audit store locked" in d
               for d in box["disagreements"])


def test_unknown_run_has_no_black_box(store):
    assert OB.reconstruct("RUN-nope", store=store) == {
        "run_id": "RUN-nope", "found": False, "say": "no objective RUN-nope in the ledger"}


class _CappedStore:
    """A store that answers an event query with at most `cap` rows, as a
    long log does when it is read a page at a time."""

    def __init__(self, store, cap):
        self._store, self._cap = store, cap

    def objective_events(self, run_id, limit=1000, **kw):
        return self._store.objective_events(run_id, limit=min(limit, self._cap), **kw)

    def __getattr__(self, name):
        return getattr(self._store, name)


def test_a_long_event_log_is_replayed_whole_not_cut_off(store, monkeypatch):
    run_id = _blackbox_run(store)
    whole = OB.reconstruct(run_id, store=store)
    n = whole["sources"]["objective_events"]
    assert n > 3                      # more events than one page below
    monkeypatch.setattr(OB, "_EVENT_PAGE", 3)
    paged = OB.reconstruct(run_id, store=_CappedStore(store, 3))
    # The closing event sits at the end of the log; a cut-off replay loses it.
    assert paged["sources"]["objective_events"] == n
    assert paged["outcome"]["status"] == "COMPLETED" and paged["consistent"], paged["disagreements"]
    assert paged["outcome"]["replayed_tally"] == whole["outcome"]["replayed_tally"]


def test_a_long_audit_log_is_paged_whole(store, monkeypatch):
    from friday import trust as T
    run_id = _blackbox_run(store)
    for i in range(3):
        T.audit().record(actor="friday", action=f"files.move{i}", tier=T.R2, decision="ASK",
                         target="C:/y", objective_id=run_id)
    logged = sorted(r["id"] for r in T.audit().query(objective_id=run_id, limit=1000))
    assert len(logged) > 4                         # the run's own decisions, plus these
    whole = OB.reconstruct(run_id, store=store)
    monkeypatch.setattr(OB, "_AUDIT_PAGE", 1)      # every row is a page of its own
    paged = OB.reconstruct(run_id, store=store)
    assert paged["consistent"], paged["disagreements"]
    # Every row of the log, oldest first, none read twice.
    assert [d["id"] for d in paged["audit"]["decisions"]] == logged
    assert paged["sources"]["audit_rows"] == len(logged)
    assert paged["audit"]["decisions"] == whole["audit"]["decisions"]


def test_the_objective_trace_faces_describe_the_black_box_replay():
    from friday import capabilities as C, tool_cards as TC
    assert "blackbox" in C.CAPABILITIES["objective_trace"].description
    assert any("black box" in e for e in C.CAPABILITIES["objective_trace"].intent_examples)
    card = TC.CARDS["objective_trace"]
    assert "blackbox" in card.does and "black box" in card.aliases


def test_redaction_by_key_and_by_token_shape():
    raw = {"api_key": "plain", "nested": {"Authorization": "Bearer x", "note": "token " + FAKE_ANTHROPIC_KEY,
                                          "access_token": "abc", "tokens_in": 10, "token_budget": 4000},
           "list": [FAKE_GITHUB_TOKEN, "fine"], "n": 3}
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
                            manifest=[{"id": "x", "description": "x"}], objective_summary="doomed", provenance=c.PERSON, attended=True)
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
    run = c.Run.create("trace blackbox", capability="objective")
    out = MG.objective_trace(run, run_id, blackbox=True)
    assert out.status == c.SUCCEEDED and out.output["consistent"]
    assert out.output["outcome"]["status"] == "COMPLETED"
    assert "consistent=True" in out.verification.evidence
    run = c.Run.create("nothing", capability="objective")
    assert MG.objective_trace(run, "RUN-missing").status == c.FAILED
    run = c.Run.create("diag", capability="system")
    out = MG.system_diagnostics(run, sections="queue,memory_store")
    assert out.status == c.OBSERVED and set(out.output) >= {"queue", "memory_store", "healthy"}


# ---------------------------------------------------------------------------
# UX-001: Normal and Developer Mode - "Visibility differs, authority does not."
# ---------------------------------------------------------------------------

def _ui_client(store, monkeypatch, *, gate=False):
    pytest.importorskip("httpx")
    from starlette.testclient import TestClient
    from friday import access
    from friday import ui_server as u

    monkeypatch.setattr(access, "GATE_ENABLED", gate)
    monkeypatch.setenv("ADA_DB", str(store.path))
    return TestClient(u.create_app(), base_url="http://127.0.0.1:8770")


def test_normal_mode_hides_what_developer_mode_shows(store, monkeypatch):
    run_id = asyncio.run(_run_objective(store, []))
    with _ui_client(store, monkeypatch) as client:
        default = client.get("/api/observability", params={"run_id": run_id}).json()
        normal = client.get("/api/observability", params={"run_id": run_id, "mode": "normal"}).json()
        dev = client.get("/api/observability", params={"run_id": run_id, "mode": "developer"}).json()
    assert default == normal and normal["mode"] == "normal"
    assert normal["status"] == "COMPLETED" and normal["retries"] >= 1
    assert {t["capability"] for t in normal["tasks"]} == {"steady", "flaky"}
    for hidden in ("timeline", "usage", "lease", "sources", "errors"):
        assert hidden not in normal
    assert all(set(t) == {"capability", "status"} for t in normal["tasks"])
    assert dev["mode"] == "developer"
    assert {"timeline", "usage", "lease", "sources"} <= dev.keys()
    assert any(row["event"] == "task.retry" for row in dev["timeline"])


def test_unknown_mode_falls_back_to_normal_never_to_developer(store, monkeypatch):
    run_id = asyncio.run(_run_objective(store, []))
    with _ui_client(store, monkeypatch) as client:
        for odd in ("admin", "", "developer;normal", "1", "dev"):
            body = client.get("/api/observability", params={"run_id": run_id, "mode": odd}).json()
            assert body["mode"] == "normal" and "timeline" not in body, odd


def test_developer_mode_is_still_redacted(store, monkeypatch):
    run_id = asyncio.run(_run_objective(store, []))
    from friday import trust as T
    T.audit().record(actor="friday", action="files.delete", tier=T.R2, decision="ASK",
                     target="C:/x", objective_id=run_id, detail={"api_key": FAKE_KEY})
    with _ui_client(store, monkeypatch) as client:
        raw = client.get("/api/observability", params={"run_id": run_id, "mode": "developer"}).text
    assert FAKE_KEY not in raw and "policy.ASK" in raw


def test_mode_does_not_change_authority(store, monkeypatch):
    run_id = asyncio.run(_run_objective(store, []))
    # The gate answers the same for every mode: a locked session gets 423 for
    # normal, developer and unknown alike, and an unknown run is not-found in both.
    with _ui_client(store, monkeypatch, gate=True) as locked:
        codes = {m: locked.get("/api/observability", params={"run_id": run_id, "mode": m}).status_code
                 for m in ("normal", "developer", "bogus")}
    assert codes == {"normal": 423, "developer": 423, "bogus": 423}
    with _ui_client(store, monkeypatch) as client:
        miss = {m: client.get("/api/observability", params={"run_id": "RUN-none", "mode": m})
                for m in ("normal", "developer")}
    assert {r.status_code for r in miss.values()} == {200}
    assert all(r.json()["found"] is False for r in miss.values())


def test_diagnostics_normal_is_a_summary_developer_is_the_full_view(store, monkeypatch):
    full = {"at": "2026-10-10T00:00:00", "redacted": True, "healthy": False,
            "unavailable": ["hermes"], "hermes": {"status": "unavailable", "error": "x"},
            "build": {"commit": "abc"}}
    monkeypatch.setattr(OB, "diagnostics", lambda **k: dict(full))
    with _ui_client(store, monkeypatch) as client:
        normal = client.get("/api/observability").json()
        dev = client.get("/api/observability", params={"mode": "developer"}).json()
    assert normal == {"mode": "normal", "at": full["at"], "healthy": False,
                      "unavailable": ["hermes"]}
    assert dev["mode"] == "developer" and dev["hermes"] == full["hermes"] and dev["build"] == full["build"]
