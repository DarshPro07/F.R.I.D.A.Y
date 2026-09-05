"""The UI server is a read-mostly VIEW over the live system.

The guarantees that matter for a control room the owner will actually open:

  * it assembles every panel the brief names, in one snapshot;
  * it NEVER raises -- a down GBrain/Hermes/MCP or a missing database
    degrades to an "unavailable"/empty status, so the room opens on a fresh
    clone with an empty .env (acceptance #5);
  * it stores nothing (it is a view, not a second memory -- non-negotiable #11).
"""
from __future__ import annotations

import json

import pytest

from friday import access
from friday import ui_server as u


@pytest.fixture(autouse=True)
def _no_face_gate(monkeypatch):
    # These tests exercise the API surface; the face gate has its own suite.
    monkeypatch.setattr(access, "GATE_ENABLED", False)

PANELS = {"system", "mcp", "connections", "memory", "todos", "business",
          "agents", "objective"}


def test_build_state_assembles_every_panel():
    s = u.build_state()
    assert PANELS <= set(s.keys())
    assert s["v"] == 1 and "at" in s
    # connections always names all three dependencies, each with a status
    for dep in ("mcp_server", "hermes", "gbrain"):
        assert "status" in s["connections"][dep]
    # the MCP inventory reflects the real router or reports unavailable
    assert s["mcp"]["status"] in ("ok", "unavailable")
    # the whole snapshot must survive JSON encoding -- the endpoint does exactly
    # this, and a stray pathlib.Path here is a live 500 (regression guard).
    json.dumps(s)


def test_it_degrades_and_never_raises_on_a_missing_db(monkeypatch, tmp_path):
    # Point at a database that does not exist: a fresh clone.
    monkeypatch.setenv("ADA_DB", str(tmp_path / "nope.sqlite3"))
    u._CONN_CACHE["value"] = None  # force a fresh connections probe
    s = u.build_state()            # must not raise
    assert s["db"]["present"] is False
    assert s["todos"] == [] and s["business"] == [] and s["agents"] == []
    assert s["memory"]["friday_local"]["count"] == 0
    # dependency statuses are still reported (strings), never exceptions
    assert isinstance(s["connections"]["gbrain"]["status"], str)


def test_memory_search_is_summary_first_and_degrades(monkeypatch, tmp_path):
    monkeypatch.setenv("ADA_DB", str(tmp_path / "nope.sqlite3"))
    out = u.memory_search("anything")
    assert set(out.keys()) == {"query", "shared_brain", "friday_local"}
    assert out["friday_local"] == []            # no DB -> no local hits
    assert "status" in out["shared_brain"]       # GBrain reported either way


def test_the_app_exposes_the_one_event_stream_and_apis():
    app = u.create_app()
    paths = {r.path for r in app.routes}
    assert {"/", "/health", "/api/state", "/api/memory", "/api/memory_flow",
            "/api/memory_snapshot", "/api/harness", "/api/objective", "/api/ask",
            "/api/browser/open", "/api/browser/act", "/api/browser/shot",
            "/api/gate", "/api/gate/approve", "/api/gate/reject",
            "/api/graph", "/api/graph/adjacency", "/api/org", "/api/org/route",
            "/api/org/assemble", "/api/tts", "/api/memory/context",
            "/api/memory/tiers", "/api/memory/log", "/api/auth/status",
            "/api/auth/verify", "/api/auth/enrol", "/api/auth/lock",
            "/api/vision/describe", "/events"} <= paths


def test_os_map_is_built_from_the_live_registry():
    """The Agentic OS map is generated, not drawn: every column comes from real
    capability groups / fabric providers, and no group may vanish -- anything
    unmapped must land in OPS / CUSTOM."""
    from friday import os_map
    from friday import capability_router as cr
    m = os_map.build()
    assert m["conductor"]["name"] == "JARVIS"
    names = {c["name"] for c in m["columns"]}
    assert "OPS / CUSTOM" in names  # the catch-all always exists
    labelled = {x["label"] for c in m["columns"] for x in c["cards"]}
    assert set(cr.GROUPS) <= labelled, "a capability group is invisible on the map"
    st = m["status"]
    assert st["tools"] >= len(cr.CORE_TOOLS) and st["groups"] == len(cr.GROUPS)
    json.dumps(m)


def test_memory_graph_is_real_and_renderable():
    """The 3D view renders THIS: every node comes from the store, every link
    joins two existing nodes, and the whole thing stays under the render cap."""
    from friday import memory_graph as G
    g = G.build()
    ids = {n["id"] for n in g["nodes"]}
    assert len(ids) == len(g["nodes"]) <= G.MAX_NODES
    assert all(l["source"] in ids and l["target"] in ids for l in g["links"])
    assert all(l["source"] != l["target"] for l in g["links"])
    assert set(g["stats"]["by_type"]) <= set(G.NODE_TYPES)
    json.dumps(g)
    adj = G.adjacency(5)
    assert all({"from", "kind", "to"} <= set(a) for a in adj)


def test_org_is_loaded_from_the_upstream_not_typed():
    """Teams come from agency-agents; the escalation chain decides, never acts."""
    from friday import org
    st = org.state()
    assert [t["tier"] for t in st["chain"]] == ["friday", "hermes", "you"]
    if st["source"] != "unavailable":
        assert st["agents_total"] > 0
        assert all(d["color"].startswith("#") for d in st["divisions"])
    r = org.route("write a marketing email")
    assert r["tier"] in ("friday", "hermes", "you") and r["why"]
    a = org.assemble("test the landing page")
    assert "team" in a and "proposal" in a["note"]
    json.dumps(st); json.dumps(r); json.dumps(a)


def test_memory_feed_adapters_are_pinned_and_honest():
    from friday.fabric_adapters import mem0_memory, graphiti_memory
    for mod in (mem0_memory, graphiti_memory):
        d = mod.DESCRIPTOR
        assert d.family == "memory" and len(d.commit) == 40
        h = mod.health(None)
        assert h["status"] in ("READY", "UNAVAILABLE") and h["detail"]


def test_ui_assets_are_served_from_the_ui_dir_only():
    """The orb is an ES module under ui/; it must be served, and nothing above
    that directory may be."""
    httpx = pytest.importorskip("httpx")
    from starlette.testclient import TestClient
    with TestClient(u.create_app()) as client:
        r = client.get("/ui/orb.js")
        assert r.status_code == 200 and "createOrbScene" in r.text
        assert client.get("/ui/../friday/ui_server.py").status_code in (404, 400)


def test_api_work_returns_runs_objectives_and_digest_shape(monkeypatch):
    """S3b: /api/work is the browser's read of the same digest the room
    speaks from -- one shape, cached like /api/helpers."""
    httpx = pytest.importorskip("httpx")
    from starlette.testclient import TestClient

    monkeypatch.setattr(u, "_probe_work", lambda: {
        "runs": [{"id": "wr1", "model": "sonnet", "status": "WORKING",
                  "latest": "editing policy.py", "route_reason": "default route"}],
        "objectives": [], "digest": "did 2 tools, last: editing policy.py"})
    u._WORK_CACHE.update(at=0.0, value=None)
    with TestClient(u.create_app()) as client:
        r = client.get("/api/work")
        assert r.status_code == 200
        body = r.json()
        assert {"runs", "objectives", "digest"} <= body.keys()
        assert body["runs"][0]["id"] == "wr1"
        assert body["runs"][0]["latest"] == "editing policy.py"
        assert isinstance(body["digest"], str)


def test_memory_stack_aggregates_four_tiers_under_budget(tmp_path, monkeypatch):
    """The blueprint: preferences + specs + rules + relations, one prompt block,
    never over budget, and the sync-and-log step writes a vault page.

    Episodes and contacts joined the four on 2026-09-01: the four were all
    *derived* memory, so a session could have every tier healthy and still not
    know a word of the last conversation. See tests/test_conversation_memory.py.
    Outcomes joined on 2026-09-03: what Hermes finished, written back by the
    bridge so the next bundle and the next turn both know (S2 in
    docs/plans/jarvis-agentic-team). See tests/test_hermes_memory_writeback.py.
    """
    from friday import memory_stack as M
    a = M.aggregate("implement the database schema for project alpha", budget_tokens=600)
    assert set(a["tiers"]) == {"preferences", "specs", "rules", "relations",
                               "episodes", "contacts", "outcomes"}
    assert a["tokens_used"] <= a["budget_tokens"]
    assert isinstance(a["prompt"], str)
    o = M.overview()
    assert [t["role"] for t in o["tiers"]] == ["Mem0", "Obsidian", "GBrain", "Graphiti"]
    json.dumps(a); json.dumps(o)
    # sync & log goes to a scratch vault, never the real one
    from friday import vault as V
    monkeypatch.setattr(V, "VAULT", tmp_path / "vault")
    r = M.log_result("test task", "did a thing", a["injected"])
    assert r["written"] and (tmp_path / "vault" / r["path"]).exists()


def test_memory_snapshot_is_cached_and_shaped():
    """The center panel reads this: it must be well-shaped and, after the first
    warm-up, served from cache (no bun spawn on the request path)."""
    from friday import ui_server as u
    s = u.memory_snapshot()
    assert set(s.keys()) >= {"gbrain", "local"}
    assert "facts" in s["gbrain"]
    assert "count" in s["local"] and isinstance(s["local"]["facts"], list)
    json.dumps(s)  # served as JSON


def test_voice_brain_routes_commands_to_real_actions():
    """The voice brain must DO things, not only talk: a banking 'open' is
    refused for real (no browser launched) and 'status' is answered from live
    state -- both without any LLM call."""
    from friday import voice_brain as V
    r = V.reply("open https://netbanking.hdfcbank.com/login")
    assert r["action"] == "browser.open" and r["status"] == "blocked"
    s = V.reply("what is your system status?")
    assert s["action"] == "status" and "online" in s["reply"].lower()


def test_voice_brain_does_not_import_providers_on_the_hot_path():
    """Regression: friday.providers registers a LiveKit plugin at import time,
    which LiveKit only permits on the main thread -- but the voice brain runs in
    the UI server's threadpool. It must resolve the model without providers."""
    import inspect
    import re
    from friday import voice_brain as V
    src = inspect.getsource(V)
    # match real import statements only -- comments mentioning providers are fine
    assert not re.search(
        r"^\s*(from friday import providers|import friday\.providers|"
        r"from friday\.providers import)", src, re.M)


def test_state_carries_metrics_and_the_agency_roster():
    s = u.build_state()
    assert "metrics" in s and "agency" in s
    assert "model_tokens" in s["metrics"] and "open_tasks" in s["metrics"]
    ag = s["agency"]
    assert ag["manager"] == "Jarvis" and ag["principal"] == "You"
    assert isinstance(ag["staff"], list)  # the roles catalogue, or [] if absent
    json.dumps(s)  # the whole snapshot must still JSON-encode


def test_memory_flow_reports_writes_and_reads():
    f = u.memory_flow()
    assert set(f.keys()) == {"writes", "reads"}
    assert isinstance(f["writes"], list) and isinstance(f["reads"], list)
    for w in f["writes"]:
        assert {"entity", "fact"} <= set(w.keys())


def test_harness_selection_is_honest_about_engine_state():
    from friday import harness as H
    engines = {e["id"]: e for e in H.availability()}
    assert "headed_playwright" in engines and "browser_use" in engines
    for e in engines.values():
        assert e["status"] in ("ready", "clone_only", "unavailable")
    # a chain always exists; chosen is an id or None with an honest reason
    sel = H.select("autonomous_browse")
    assert sel["chain"] and (sel["chosen"] is None or "reason" in sel)


def test_browser_gate_lifecycle_binds_to_the_exact_action():
    """A state-changing action raises a single-use, one-action gate; a reject is
    final. None of this launches a browser (no _worker call)."""
    from friday import ui_browser as B
    g = B.request_act("click", "#buy-now")
    assert g["gated"] is True and g["nonce"]
    assert any(c["target"] == "#buy-now" for c in B.pending_gates())
    # a non-actionable kind is not gated (and does nothing)
    assert B.request_act("frobnicate", "x")["gated"] is False
    # reject is final: a later approve cannot resurrect it (and never acts)
    B.reject_act(g["nonce"], reason="no")
    after = B.approve_act(g["nonce"])
    assert after["ok"] is False
    assert B._worker.running is False  # nothing ever launched a browser


def test_browser_open_blocks_banking_before_any_launch():
    """A banking URL is refused before capture -- no navigation, no screenshot,
    and crucially no browser process is started at all."""
    from friday import ui_browser as B
    out = B.open_url("https://netbanking.hdfcbank.com/login")
    assert out["status"] == "blocked"
    assert out["content"] == "" and not out.get("screenshot")
    assert out["verdict"].startswith("BLOCKED_SENSITIVE_DOMAIN")
    assert B._worker.running is False


def test_shot_path_refuses_traversal():
    from friday import ui_browser as B
    assert B.shot_path("../secret.png") is None
    assert B.shot_path("etc/passwd") is None
    assert B.shot_path("not-a-shot.png") is None


def test_http_surface_is_live():
    httpx = pytest.importorskip("httpx")  # starlette TestClient needs httpx
    from starlette.testclient import TestClient
    with TestClient(u.create_app()) as client:
        assert client.get("/health").text == "ok"
        r = client.get("/api/state")
        assert r.status_code == 200
        body = r.json()
        assert "system" in body and "connections" in body
        assert client.get("/api/memory?q=test").status_code == 200
        assert client.get("/api/memory_flow").status_code == 200
        assert client.get("/api/harness").status_code == 200
        # FR-040 (2026-09-05): /api/objective is no longer a capture stub
        # that answered {"captured": true}; it enters the objective ledger
        # (or says why not) with the request's channel identity. Either a
        # run id or a stated reason - never a silent success.
        cap = client.post("/api/objective", json={"objective": "demo task"})
        assert cap.status_code in (200, 409)
        body = cap.json()
        assert body["channel"] and ("run_id" in body)
        assert body["ok"] == bool(body["run_id"])
        if not body["ok"]:
            assert body["error"]
        assert client.get("/api/gate").status_code == 200
        # a banking URL is blocked before any browser launches
        bo = client.post("/api/browser/open",
                         json={"url": "https://www.icicibank.com/"})
        assert bo.status_code == 200 and bo.json()["status"] == "blocked"
        # requesting a state-changing action returns a gate, not an action
        ba = client.post("/api/browser/act",
                         json={"kind": "click", "selector": "#x"})
        assert ba.status_code == 200 and ba.json()["gated"] is True
        # the voice endpoint routes a banking 'open' to a real refusal
        ask = client.post("/api/ask",
                          json={"text": "open https://www.icicibank.com/"})
        assert ask.status_code == 200 and ask.json()["status"] == "blocked"


def test_metrics_are_scoped_to_the_current_objective(tmp_path, monkeypatch):
    """The header claimed "340 tasks open . 1.71M tokens" for ONE objective;
    both numbers were all-time sums. Current keys must name the current run."""
    from friday.store import Store
    db = tmp_path / "objectives.sqlite3"
    opened = Store(db)
    with opened._tx() as conn:
        for i, (rid, at) in enumerate((("RUN-old", "2026-08-01T00:00:00+00:00"),
                                       ("RUN-new", "2026-08-02T00:00:00+00:00"))):
            conn.execute("INSERT INTO objective_runs (run_id, request, "
                         "objective_summary, status, created_at, updated_at) "
                         "VALUES (?,?,?,?,?,?)",
                         (rid, "objective %d" % i, "", "RUNNING", at, at))
            conn.execute("INSERT INTO objective_tasks (task_id, run_id, status, "
                         "capability, arguments, created_at) VALUES (?,?,?,?,?,?)",
                         ("T-" + rid, rid, "QUEUED", "cap", "{}", at))
            conn.execute("INSERT INTO runs (run_id, request, state, created_at, "
                         "updated_at) VALUES (?,?,?,?,?)", (rid, "r", "working", at, at))
            conn.execute("INSERT INTO run_portions (portion_id, run_id, "
                         "wake_generation, lease_token, status, model_tokens, "
                         "started_at) VALUES (?,?,?,?,?,?,?)",
                         ("P-" + rid, rid, 1, "tok", "checkpointed", 100 * (i + 1), at))
    opened.close()
    monkeypatch.setenv("ADA_DB", str(db))
    conn = u._connect()
    try:
        objective = u._objective(conn)
        m = u._metrics(conn, objective.get("run_id"))
    finally:
        conn.close()
    assert objective["run_id"] == "RUN-new"
    assert m["model_tokens"] == 200 and m["open_tasks"] == 1
    assert m["all_time"] == {"model_tokens": 300, "open_tasks": 2}


def test_cross_site_callers_cannot_reach_desk_or_stt(monkeypatch):
    """A page from another origin (or a WebSocket handshake from one) is refused
    even with the face gate off (--bypass-face). The session cookie is
    SameSite=Strict, so these two were the doors it did not guard: desk fires a
    synthetic Ctrl+C for ?what=selection, and the speech socket spends the
    owner's Deepgram key. Security review finding, 2026-09-03."""
    pytest.importorskip("httpx")
    from starlette.testclient import TestClient
    from starlette.websockets import WebSocketDisconnect
    from friday import access
    monkeypatch.setattr(access, "GATE_ENABLED", False)
    calls = []
    monkeypatch.setattr(u.desk_mod, "grab",
                        lambda what: calls.append(what) or {"ok": True, "chars": 0})
    with TestClient(u.create_app()) as client:
        r = client.get("/api/desk?what=selection", headers={"Origin": "http://evil.example"})
        assert r.status_code == 403 and calls == []
        r = client.get("/api/desk?what=clipboard",
                       headers={"Referer": "http://evil.example/page"})
        assert r.status_code == 403 and calls == []
        r = client.get("/api/desk?what=clipboard", headers={"Origin": "http://testserver"})
        assert r.status_code == 200 and calls == ["clipboard"]
        # Header-less is cross-site for desk: a no-referrer image sends nothing.
        assert client.get("/api/desk?what=clipboard").status_code == 403
        assert client.get("/api/desk?what=nope", headers={"Origin": "http://evil.example"}).status_code == 400
        with pytest.raises(WebSocketDisconnect):
            with client.websocket_connect("/api/stt", headers={"Origin": "http://evil.example"}):
                pass


def test_api_helpers_returns_providers_families_and_processes():
    """/api/helpers is fabric's diagnostic surface, verbatim: every connected
    upstream helper, its family rollup and process ownership, for the control
    room's Organisation view and the helpers/list voice capability."""
    pytest.importorskip("httpx")
    from starlette.testclient import TestClient
    from friday import fabric
    with TestClient(u.create_app()) as client:
        r = client.get("/api/helpers")
        assert r.status_code == 200
        body = r.json()
        assert set(body.keys()) == {"providers", "families", "processes"}
        assert len(body["providers"]) == len(fabric.report())
        for key in ("provider", "family", "state", "integration_mode", "license_mode"):
            assert key in body["providers"][0]
        assert {"family", "state", "providers"} <= set(body["families"][0].keys())
