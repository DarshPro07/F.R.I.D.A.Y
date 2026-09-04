"""
Friday UI server -- a read-mostly web surface over the live system.

Deliberately a SEPARATE process from server.py (the MCP/agent). It never
writes to the agent state and never imports the live FastMCP server, so it
cannot destabilise a running Friday (the dev-mode/watchfiles landmine stays in
its box). It reads data/ada.sqlite3 READ-ONLY and asks the same modules the
agent already uses for health:

  * capability_router  -> the MCP tool inventory (core + groups)
  * hermes_health      -> the 8-layer Hermes probe
  * brain.SharedBrainAdapter.available() -> GBrain, the canonical shared memory

This is a VIEW, not a second memory (non-negotiable #11): it stores nothing.
Everything degrades to an "unavailable"/empty status rather than raising, so
the control room opens on a fresh clone with an empty .env (acceptance #5).

Run:  .venv/Scripts/python.exe scripts/run_ui.py
"""
from __future__ import annotations

import asyncio
import json
import io
import logging
import os
import socket
import sqlite3
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

from starlette.applications import Starlette
from starlette.responses import FileResponse, JSONResponse, PlainTextResponse
from starlette.middleware import Middleware
from starlette.routing import Mount, Route, WebSocketRoute
from starlette.staticfiles import StaticFiles
from starlette.concurrency import run_in_threadpool

from friday import access
from friday import camera as camera_mod
from friday import desk as desk_mod
from friday.store import DEFAULT_DB

UI_DIR = Path(__file__).resolve().parent.parent / "ui"
MCP_HOST = os.getenv("ADA_MCP_HOST", "127.0.0.1")
MCP_PORT = int(os.getenv("ADA_MCP_PORT", "8000"))

# Health checks are expensive: GBrain's spawns a bun subprocess and Hermes' makes
# an HTTP call. Cache the whole connections block so a 3s UI poll never spawns bun.
_CONN_CACHE = {"at": 0.0, "value": None}
_CONN_TTL = 10.0
_HELPERS_CACHE = {"at": 0.0, "value": None}
_HELPERS_TTL = 10.0
_GBRAIN_TTL = 60.0          # available() spawns bun; once a minute is plenty
_GBRAIN_CACHE = {"at": 0.0, "value": None}
#: Hermes is probed the same way, and for the same reason: the round-trip that
#: proves the bridge is alive is far too slow to sit on a request.
_HERMES_TTL = 20.0
_HERMES_CACHE = {"at": 0.0, "value": None}

# The shared-memory snapshot, kept warm in the background so the UI's center
# panel is instant (GBrain recall spawns a bun subprocess -- never on the
# request path). First call computes once; a daemon refreshes it every 25s.
_MEM_CACHE = {"at": 0.0, "data": None}
_MEM_LOCK = threading.Lock()
_MEM_THREAD = None


def _compute_mem_snapshot():
    gb = {"status": "unavailable", "facts": [], "count": 0}
    try:
        from friday.brain import SharedBrainAdapter
        ans = SharedBrainAdapter().recall("", budget="project").compact()
        facts = [f.get("fact") for f in ans.get("facts", []) if f.get("fact")]
        gb = {"status": "available", "facts": facts[:40], "count": len(facts)}
    except Exception:  # noqa: BLE001
        gb = {"status": "unavailable", "facts": [], "count": 0}
    conn = _connect()
    try:
        cnt = _rows(conn, "SELECT COUNT(*) AS n FROM memories WHERE superseded=0")
        recent = _rows(conn, "SELECT subject, value, kind, confidence, scope "
                             "FROM memories WHERE superseded=0 "
                             "ORDER BY id DESC LIMIT 60")
    finally:
        if conn is not None:
            conn.close()
    return {"gbrain": gb,
            "local": {"count": (cnt[0]["n"] if cnt else 0), "facts": recent},
            "at": _now()}


log = logging.getLogger("friday.ui")


def _mem_refresher():
    """Warm the shared-memory cache. Never dies silently: a broken refresher
    means every reader quietly gets stale data, so the failure is logged once
    and then at most every twentieth cycle while it persists."""
    failures = 0
    while True:
        try:
            snap = _compute_mem_snapshot()
            with _MEM_LOCK:
                _MEM_CACHE["data"], _MEM_CACHE["at"] = snap, time.time()
            if failures:
                log.info("memory snapshot recovered after %d failed refresh(es)", failures)
            failures = 0
        except Exception:  # noqa: BLE001
            failures += 1
            if failures == 1 or failures % 20 == 0:
                log.exception("memory snapshot refresh failed (%d in a row); "
                              "/api/memory_snapshot is serving stale data", failures)
        for cache, ttl, probe in ((_GBRAIN_CACHE, _GBRAIN_TTL, _probe_gbrain),
                                  (_HERMES_CACHE, _HERMES_TTL, _probe_hermes)):
            if time.time() - cache["at"] > ttl:                  # off the request path
                try:
                    cache.update(at=time.time(), value=probe())
                except Exception:  # noqa: BLE001
                    log.exception("health probe failed")
        time.sleep(20)


def memory_snapshot():
    """Instant shared-memory view, served from a background-warmed cache."""
    global _MEM_THREAD
    with _MEM_LOCK:
        data = _MEM_CACHE["data"]
    if _MEM_THREAD is None:
        _MEM_THREAD = threading.Thread(target=_mem_refresher, daemon=True,
                                       name="mem-refresh")
        _MEM_THREAD.start()
    if data is None:  # first ever call: compute once, then it stays warm
        data = _compute_mem_snapshot()
        with _MEM_LOCK:
            _MEM_CACHE["data"], _MEM_CACHE["at"] = data, time.time()
    return data


# --------------------------------------------------------------------------
# read-only data access
# --------------------------------------------------------------------------

def _now():
    return datetime.now(timezone.utc).isoformat()


def _db_path():
    # str(): DEFAULT_DB is a pathlib.Path, and the state dict is JSON-encoded.
    return str(os.getenv("ADA_DB") or DEFAULT_DB)


def _connect():
    """A read-only connection, or None when there is no database yet."""
    path = _db_path()
    if not Path(path).exists():
        return None
    try:
        conn = sqlite3.connect("file:%s?mode=ro" % path, uri=True,
                               check_same_thread=False, timeout=2.0)
        conn.row_factory = sqlite3.Row
        return conn
    except sqlite3.Error:
        return None


def _rows(conn, sql, args=()):
    """Query returning [] on any error or missing table (fresh DB)."""
    if conn is None:
        return []
    try:
        return [dict(r) for r in conn.execute(sql, args).fetchall()]
    except sqlite3.Error:
        return []


def _val(obj, name, default=None):
    """Read a Report member whether it is a property or a method."""
    v = getattr(obj, name, default)
    try:
        return v() if callable(v) else v
    except Exception:  # noqa: BLE001
        return default


# --------------------------------------------------------------------------
# panels
# --------------------------------------------------------------------------

def _build():
    """The real build identity: commit, capability-registry hash, tool count.
    A cockpit shows what it is actually running; this is read, never invented."""
    try:
        from friday import build_identity
        return {"status": "ok", "text": build_identity.describe()}
    except Exception as exc:  # noqa: BLE001
        return {"status": "unavailable", "error": str(exc)[:80]}


def _system():
    try:
        import psutil
        vm = psutil.virtual_memory()
        return {
            "status": "ok",
            "ram_total": vm.total,
            "ram_used": vm.used,
            "ram_percent": vm.percent,
            "cpu_percent": psutil.cpu_percent(interval=None),
            "processes": len(psutil.pids()),
        }
    except Exception as exc:  # noqa: BLE001
        return {"status": "unavailable", "error": str(exc)}


def _mcp_inventory():
    try:
        from friday import capability_router as cr
        groups = {g: len(names) for g, names in cr.GROUPS.items()}
        return {
            "status": "ok",
            "core_count": len(cr.CORE_TOOLS),
            "core": list(cr.CORE_TOOLS),
            "groups": groups,
            "total": len(cr.CORE_TOOLS) + sum(groups.values()),
        }
    except Exception as exc:  # noqa: BLE001
        return {"status": "unavailable", "error": str(exc)}


def _tcp_up(host, port, timeout=0.6):
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def _probe_gbrain():
    """~7 seconds: it spawns bun and waits. Never call this from a request."""
    try:
        from friday.brain import SharedBrainAdapter
        up = SharedBrainAdapter().available()
        return {"status": "available" if up else "unavailable"}
    except Exception as exc:  # noqa: BLE001
        return {"status": "unavailable", "error": str(exc)[:200]}


def _cached(cache, probe):
    """The warmed value, probing once if this process has never had one."""
    if cache["value"] is None:
        _start_background_health()
        try:
            cache.update(at=time.time(), value=probe())
        except Exception as exc:  # noqa: BLE001
            return {"status": "unavailable", "error": str(exc)[:160]}
    return cache["value"]


def _gbrain_status():
    return _cached(_GBRAIN_CACHE, _probe_gbrain)


def _probe_hermes():
    """
    Is Hermes actually usable?

    The layered probe answers "where is the fault", not "is it up": it sets
    hermes_bridge_ready False on purpose, because reaching MCP is not proof the
    bridge behind it is alive. Its own recovery note says what settles that --
    one hermes_status round-trip, since the supervisor connects on demand and a
    successful answer IS the bridge, verified rather than assumed.

    Reporting the deferred layer as a fault made the control room say "degraded"
    while Hermes was answering perfectly. So we ask.
    """
    try:
        from friday import hermes_health as hh
        report = hh.live_probe_factory("http://%s:%s" % (MCP_HOST, MCP_PORT),
                                       timeout=2.0)()
    except Exception as exc:  # noqa: BLE001
        return {"status": "unavailable", "error": str(exc)[:160]}

    failed = _val(report, "failed_layer", "")
    if _val(report, "healthy", False):
        return {"status": "healthy", "summary": _val(report, "summary", ""),
                "failed_layer": ""}

    if failed in ("hermes_bridge_ready", "active_workrun_reachable"):
        try:
            from friday import control
            answer = control.call("hermes_status", {})
            if isinstance(answer, dict) and answer.get("ok"):
                return {"status": "healthy", "failed_layer": "",
                        "summary": "bridge answered hermes_status"}
            detail = str(answer)[:120]
        except Exception as exc:  # noqa: BLE001
            detail = "%s: %s" % (type(exc).__name__, str(exc)[:100])
        return {"status": "degraded", "failed_layer": failed,
                "summary": "%s; hermes_status did not answer (%s)"
                           % (_val(report, "summary", ""), detail)}

    return {"status": "degraded", "summary": _val(report, "summary", ""),
            "failed_layer": failed}


def _hermes_status():
    return _cached(_HERMES_CACHE, _probe_hermes)


def _connections():
    now = time.time()
    if _CONN_CACHE["value"] is not None and now - _CONN_CACHE["at"] < _CONN_TTL:
        return _CONN_CACHE["value"]
    value = {
        "mcp_server": {
            "target": "%s:%s" % (MCP_HOST, MCP_PORT),
            "status": "up" if _tcp_up(MCP_HOST, MCP_PORT) else "down",
        },
        "hermes": _hermes_status(),
        "gbrain": _gbrain_status(),
    }
    _CONN_CACHE.update(at=now, value=value)
    return value


def _memory(conn, gbrain_up):
    # Shared brain (canonical): availability only here; real recall is on
    # /api/memory so a 3s poll never spawns bun.
    shared = {"status": "available" if gbrain_up else "unavailable",
              "note": "search via the memory window"}
    count = _rows(conn, "SELECT COUNT(*) AS n FROM memories WHERE superseded=0")
    recent = _rows(conn, "SELECT subject, value, kind, confidence "
                         "FROM memories WHERE superseded=0 "
                         "ORDER BY id DESC LIMIT 8")
    return {
        "shared_brain": shared,
        "friday_local": {
            "status": "ok",
            "count": (count[0]["n"] if count else 0),
            "recent": recent,
        },
    }


def _todos(conn):
    todos = _rows(conn, "SELECT question AS text, why, project, asked_at AS at "
                        "FROM open_questions WHERE answer IS NULL "
                        "ORDER BY asked_at DESC LIMIT 20")
    for t in todos:
        t["kind"] = "open_question"
    tasks = _rows(conn, "SELECT description AS text, status, run_id, "
                        "created_at AS at FROM run_tasks WHERE status NOT IN "
                        "('done','completed','cancelled','failed','verified') "
                        "ORDER BY created_at DESC LIMIT 20")
    for t in tasks:
        t["kind"] = "task"
    return todos + tasks


def _business(conn):
    return _rows(conn, "SELECT subject, value, kind, scope FROM memories "
                       "WHERE superseded=0 AND (scope <> 'user' "
                       "OR subject LIKE 'Project %' OR subject LIKE '%business%') "
                       "ORDER BY id DESC LIMIT 20")


def _agents(conn):
    return _rows(conn, "SELECT run_id, request, state, capability, created_at "
                       "FROM runs ORDER BY created_at DESC LIMIT 8")


def _objective(conn):
    rows = _rows(conn, "SELECT run_id, request, objective_summary, status, "
                       "created_at FROM objective_runs "
                       "ORDER BY created_at DESC LIMIT 1")
    if rows:
        r = rows[0]
        return {"run_id": r.get("run_id"),
                "objective": r.get("request") or r.get("objective_summary") or "",
                "summary": r.get("objective_summary") or "",
                "state": r.get("status") or "idle",
                "created_at": r.get("created_at")}
    return {"objective": "", "state": "idle"}


OPEN_TASK = ("WHERE status NOT IN "
             "('done','completed','verified','cancelled','failed')")


def _metrics(conn, run_id=None):
    """Metrics for the objective in `_objective`, plus the all-time totals.

    The header used to read "340 tasks open . 1.71M tokens" beside ONE
    objective: both numbers were sums over every row ever written. Scoped keys
    now name `run_id` only. Caveat on tokens: `run_portions.run_id` is the
    continuity engine's id space and `objective_runs.run_id` the objective
    engine's -- they never overlap in the live database, so the scoped token
    count is 0 until one run id spans both. The real total is in `all_time`,
    which is what the header labels.
    """
    # model_tokens lives on run_portions in this schema; [] -> 0 (honest).
    tok = _rows(conn, "SELECT COALESCE(SUM(model_tokens),0) AS t FROM run_portions "
                      "WHERE run_id=?", (run_id,)) if run_id else []
    tokens = (tok[0]["t"] if tok else 0) or 0
    all_tok = _rows(conn, "SELECT COALESCE(SUM(model_tokens),0) AS t FROM run_portions")
    by_state = _rows(conn, "SELECT state, COUNT(*) AS n FROM runs "
                           "GROUP BY state ORDER BY n DESC")
    open_tasks = _rows(conn, "SELECT COUNT(*) AS n FROM objective_tasks "
                             + OPEN_TASK + " AND run_id=?", (run_id,)) if run_id else []
    all_open = _rows(conn, "SELECT COUNT(*) AS n FROM objective_tasks " + OPEN_TASK)
    dur = _rows(conn, "SELECT AVG((julianday(updated_at)-julianday(created_at))"
                      "*86400.0) AS s FROM (SELECT created_at, updated_at FROM "
                      "runs WHERE updated_at IS NOT NULL "
                      "ORDER BY created_at DESC LIMIT 30)")
    return {
        "model_tokens": tokens,
        "runs_by_state": by_state,
        "open_tasks": (open_tasks[0]["n"] if open_tasks else 0),
        "avg_run_secs": round(dur[0]["s"], 1) if dur and dur[0]["s"] else 0,
        "all_time": {"model_tokens": (all_tok[0]["t"] if all_tok else 0) or 0,
                     "open_tasks": (all_open[0]["n"] if all_open else 0)},
    }


def _agency(conn):
    # Jarvis is the manager; the roles catalogue is the staff he can assign;
    # recent runs are live assignments. One-man company: You -> Jarvis -> team.
    staff = []
    try:
        from friday import roles as R
        staff = [{"id": r.id, "title": r.title} for r in R.CATALOGUE]
    except Exception:  # noqa: BLE001
        staff = []
    active = _rows(conn, "SELECT run_id, request, state, capability, created_at "
                         "FROM runs ORDER BY created_at DESC LIMIT 8")
    return {"manager": "Jarvis", "principal": "You",
            "staff": staff, "assignments": active}


def _browser_status():
    try:
        from friday import ui_browser as B
        return B.status()
    except Exception as exc:  # noqa: BLE001
        return {"running": False, "error": str(exc)}


def _durable_gates(conn):
    """Gates the LIVE agent is waiting on. Read-only here -- the agent owns and
    resolves these in its own process (confirmation.Book is per-process); the
    UI surfaces them so nothing waits invisibly."""
    runs = _rows(conn, "SELECT run_id, request, state, created_at FROM runs "
                       "WHERE state LIKE 'waiting%' ORDER BY created_at DESC LIMIT 10")
    qs = _rows(conn, "SELECT id, question, why, project, asked_at FROM open_questions "
                     "WHERE answer IS NULL ORDER BY asked_at DESC LIMIT 10")
    return {"waiting_runs": runs, "open_questions": qs}


def build_state():
    conn = _connect()
    try:
        conns = _connections()
        gbrain_up = conns.get("gbrain", {}).get("status") == "available"
        objective = _objective(conn)
        return {
            "v": 1,
            "at": _now(),
            "db": {"path": _db_path(), "present": conn is not None},
            "system": _system(),
            "mcp": _mcp_inventory(),
            "connections": conns,
            "memory": _memory(conn, gbrain_up),
            "todos": _todos(conn),
            "business": _business(conn),
            "agents": _agents(conn),
            "objective": objective,
            "metrics": _metrics(conn, objective.get("run_id")),
            "agency": _agency(conn),
            "browser": _browser_status(),
            "build": _build(),
        }
    finally:
        if conn is not None:
            conn.close()


def memory_flow(limit=25):
    """The live pulse of the shared brain: recent WRITES (from the canonical
    GBrain ledger, tagged by entity) and recent READS/activity (run events).
    This is what makes the hub graph light up.
    """
    writes = []
    try:
        from friday.brain import SharedBrainAdapter
        path = Path(SharedBrainAdapter()._ledger_path())
        if path.exists():
            lines = path.read_text(encoding="utf-8").splitlines()[-limit:]
            for ln in lines:
                ln = ln.strip()
                if not ln:
                    continue
                try:
                    e = json.loads(ln)
                except ValueError:
                    continue
                writes.append({"entity": e.get("entity") or "friday",
                               "fact": (e.get("fact") or "")[:120],
                               "provenance": e.get("provenance") or "",
                               "at": e.get("recorded_at") or ""})
    except Exception:  # noqa: BLE001
        writes = []
    conn = _connect()
    try:
        reads = _rows(conn, "SELECT run_id, kind, message, created_at AS at "
                            "FROM run_events ORDER BY event_id DESC LIMIT ?",
                      (limit,))
    finally:
        if conn is not None:
            conn.close()
    return {"writes": list(reversed(writes)), "reads": reads}


def memory_search(query, limit=20):
    """Shared memory search: GBrain (canonical) first, Friday-local second.

    Summary-first: GBrain returns packed facts under a server-side budget;
    Friday-local returns subject/value rows. Full content is deliberately
    trimmed here so a search never floods a model context (directive 3.6).
    """
    out = {"query": query, "shared_brain": {"status": "unavailable"},
           "friday_local": []}
    try:
        from friday.brain import SharedBrainAdapter
        b = SharedBrainAdapter()
        if b.available():
            ans = b.recall(query, budget="bounded").compact()
            out["shared_brain"] = {
                "status": "ok",
                "budget_used": ans.get("budget_used", 0),
                "dropped_count": ans.get("dropped_count", 0),
                "facts": [{"fact": f.get("fact"), "entity": f.get("entity"),
                           "provenance": f.get("provenance")}
                          for f in ans.get("facts", [])[:limit]],
                "snippets": ans.get("snippets", [])[:5],
            }
    except Exception as exc:  # noqa: BLE001
        out["shared_brain"] = {"status": "unavailable", "error": str(exc)}
    conn = _connect()
    try:
        like = "%" + query + "%"
        out["friday_local"] = _rows(
            conn,
            "SELECT subject, value, kind, confidence, scope FROM memories "
            "WHERE superseded=0 AND (subject LIKE ? OR value LIKE ?) "
            "ORDER BY id DESC LIMIT ?", (like, like, limit))
    finally:
        if conn is not None:
            conn.close()
    return out


# --------------------------------------------------------------------------
# routes
# --------------------------------------------------------------------------

async def index(request):
    path = UI_DIR / "index.html"
    if not path.exists():
        return PlainTextResponse("ui/index.html missing", status_code=500)
    return FileResponse(path)


async def api_state(request):
    return JSONResponse(await run_in_threadpool(build_state))


async def api_memory(request):
    q = request.query_params.get("q", "")
    return JSONResponse(await run_in_threadpool(memory_search, q))


async def api_memory_flow(request):
    return JSONResponse(await run_in_threadpool(memory_flow))


async def api_memory_snapshot(request):
    return JSONResponse(await run_in_threadpool(memory_snapshot))


async def api_os_map(request):
    from friday import os_map
    return JSONResponse(await run_in_threadpool(os_map.build))


async def api_vault(request):
    from friday import vault
    return JSONResponse(await run_in_threadpool(vault.tree))


async def api_vault_file(request):
    from friday import vault
    doc = await run_in_threadpool(vault.read, request.query_params.get("path", ""))
    if doc is None:
        return JSONResponse({"error": "not found"}, status_code=404)
    return JSONResponse(doc)


async def api_vault_sync(request):
    from friday import vault
    return JSONResponse(await run_in_threadpool(vault.sync))


async def api_control(request):
    from friday import control
    return JSONResponse(await run_in_threadpool(control.status))


async def api_memory_context(request):
    from friday import memory_stack
    return JSONResponse(await run_in_threadpool(
        memory_stack.aggregate, request.query_params.get("task", "")))


async def api_memory_tiers(request):
    from friday import memory_stack
    return JSONResponse(await run_in_threadpool(memory_stack.overview))


async def api_memory_log(request):
    from friday import memory_stack
    try:
        data = await request.json()
    except Exception:  # noqa: BLE001
        data = {}
    return JSONResponse(await run_in_threadpool(
        memory_stack.log_result, data.get("task", ""), data.get("summary", ""),
        data.get("injected")))


def _client_token(request):
    return request.cookies.get(access.COOKIE, "")


async def api_auth_status(request):
    return JSONResponse(access.status(_client_token(request)))


async def api_auth_enrol(request):
    """Open only when nobody is enrolled yet; afterwards it needs a session."""
    try:
        data = await request.json()
    except Exception:  # noqa: BLE001
        data = {}
    if access.enrolled() and not access.session_ok(_client_token(request)):
        access.log({"kind": "enrol_refused", "client": request.client.host if request.client else ""})
        return JSONResponse({"ok": False, "error": "unlock first to change the enrolled face"}, status_code=423)
    return JSONResponse(await run_in_threadpool(access.enrol, data.get("descriptor"), data.get("label") or "owner"))


async def api_auth_verify(request):
    try:
        data = await request.json()
    except Exception:  # noqa: BLE001
        data = {}
    out = await run_in_threadpool(access.verify, data.get("descriptor"))
    token = out.pop("token", None)
    resp = JSONResponse(out)
    if token:
        resp.set_cookie(access.COOKIE, token, httponly=True, samesite="strict",
                        max_age=int(access.SESSION_HOURS * 3600), path="/")
    return resp


async def api_auth_lock(request):
    out = access.lock(_client_token(request))
    resp = JSONResponse(out)
    resp.delete_cookie(access.COOKIE, path="/")
    return resp


async def api_auth_log(request):
    """Client-side events while locked (a voice heard, a hand, an object)."""
    try:
        data = await request.json()
    except Exception:  # noqa: BLE001
        data = {}
    ev = {"kind": "client:" + str(data.get("kind") or "event")[:40],
          "detail": str(data.get("detail") or "")[:200],
          "client": request.client.host if request.client else ""}
    await run_in_threadpool(access.log, ev)
    return JSONResponse({"ok": True})


async def api_auth_recent(request):
    return JSONResponse({"events": await run_in_threadpool(access.recent_log, 60)})


VISION_BRIEF = (
    "You are looking through Friday's eyes for Darsh, who is talking to you out loud. "
    "Answer HIS question about what you can see, in one or two spoken sentences -- "
    "warm, specific and useful, never a list, never a caption of the whole frame. "
    "If he asks for a judgement (which outfit, does this look right, what should I change) "
    "give him a real opinion and one concrete reason, the way a friend with taste would. "
    "If you genuinely cannot see what he means, say so in one line."
)


def _describe_bytes(raw, question):
    try:
        import friday.config  # noqa: F401
    except Exception:  # noqa: BLE001
        pass
    key = os.getenv("GOOGLE_API_KEY")
    if not key:
        return {"ok": False, "error": "no GOOGLE_API_KEY"}
    try:
        from google import genai
        from google.genai import types
        client = genai.Client(api_key=key)
        resp = client.models.generate_content(
            model=os.getenv("ADA_VISION_MODEL", "gemini-2.5-flash"),
            contents=[types.Part.from_bytes(data=raw, mime_type="image/jpeg"),
                      (question or "What am I showing you?")],
            config=types.GenerateContentConfig(
                max_output_tokens=400, temperature=0.6, system_instruction=VISION_BRIEF,
                # perception, not deliberation: thinking tokens were eating the whole
                # budget and truncating her answer mid-sentence
                thinking_config=types.ThinkingConfig(thinking_budget=0)))
        return {"ok": True, "text": (resp.text or "").strip()}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc)[:160]}


def _describe_image(b64, question):
    import base64
    try:
        raw = base64.b64decode((b64 or "").split(",")[-1])
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": "bad image: %s" % str(exc)[:80]}
    return _describe_bytes(raw, question)


async def api_vision_describe(request):
    try:
        data = await request.json()
    except Exception:  # noqa: BLE001
        data = {}
    return JSONResponse(await run_in_threadpool(_describe_image, data.get("image"), data.get("question")))


async def api_camera_hold(request):
    """A page with the camera open says so, so the PIN stays shut while a face can be read."""
    access.note_camera_hold()
    return JSONResponse({"ok": True})


def _same_origin(headers) -> bool:
    """True unless a browser says the request came from another site.

    Origin (always sent on WebSocket handshakes and cross-site fetches) or,
    failing that, Referer must name this server. No header at all is a
    same-origin GET from the page, curl or a test client - allowed, as before.
    The session cookie is SameSite=Strict, so with the face gate ON a
    cross-site request never carries it; this closes the same two doors in
    --bypass-face runs: the desk endpoint (a synthetic Ctrl+C is a side
    effect) and the speech socket (paid transcription on the owner's key).
    """
    from urllib.parse import urlsplit
    src = headers.get("origin") or headers.get("referer") or ""
    if not src:
        return True
    return urlsplit(src).netloc == headers.get("host", "")


async def api_desk(request):
    """The clipboard, or the text highlighted in the foreground window.

    Pressing Ctrl+C at another window is a real side effect, so it happens only
    when asked for by name: ?what=selection or ?what=auto.
    """
    what = request.query_params.get("what", "clipboard")
    if what not in ("clipboard", "selection", "auto"):
        return JSONResponse({"ok": False, "error": "what must be clipboard, selection or auto"},
                            status_code=400)
    # Strict here: a cross-site <img referrerpolicy="no-referrer"> sends neither
    # Origin nor Referer, so header-less is treated as cross-site for this
    # endpoint (review, 2026-09-03). The page's own fetch always carries Referer.
    src = request.headers.get("origin") or request.headers.get("referer")
    if not src or not _same_origin(request.headers):
        access.log({"kind": "desk", "what": what, "blocked": "cross-origin"})
        return JSONResponse({"ok": False, "error": "cross-origin blocked"}, status_code=403)
    out = await run_in_threadpool(desk_mod.grab, what)
    access.log({"kind": "desk", "what": what, "ok": bool(out.get("ok")),
                "chars": out.get("chars", 0)})          # what she read, never the text itself
    return JSONResponse(out)


async def api_camera(request):
    """Who holds the camera. Open while locked: the lock screen needs it to explain itself."""
    return JSONResponse(await run_in_threadpool(camera_mod.status))


async def api_pin_set(request):
    """Open only when no PIN exists yet; after that it takes an unlocked session."""
    try:
        data = await request.json()
    except Exception:  # noqa: BLE001
        data = {}
    if access.has_pin() and not access.session_ok(_client_token(request)):
        access.log({"kind": "pin_set_refused"})
        return JSONResponse({"ok": False, "error": "unlock first to change the PIN"}, status_code=423)
    return JSONResponse(await run_in_threadpool(access.set_pin, data.get("pin")))


async def api_pin_verify(request):
    try:
        data = await request.json()
    except Exception:  # noqa: BLE001
        data = {}
    out = await run_in_threadpool(access.verify_pin, data.get("pin"))
    token = out.pop("token", None)
    resp = JSONResponse(out)
    if token:
        resp.set_cookie(access.COOKIE, token, httponly=True, samesite="strict",
                        max_age=int(access.SESSION_HOURS * 3600), path="/")
    return resp


def _grab_screen():
    """Friday looks at the owner's screen herself -- no picker, no permission dance."""
    from PIL import ImageGrab
    img = ImageGrab.grab()
    img.thumbnail((1280, 1280))
    buf = io.BytesIO()
    img.convert("RGB").save(buf, "JPEG", quality=72)
    return buf.getvalue()


async def api_vision_screen(request):
    try:
        data = await request.json()
    except Exception:  # noqa: BLE001
        data = {}

    def work():
        try:
            raw = _grab_screen()
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": "could not read the screen: %s" % str(exc)[:100]}
        return _describe_bytes(raw, data.get("question"))

    return JSONResponse(await run_in_threadpool(work))


async def api_doctor(request):
    """Friday's own check-up: what is healthy, what is not, and what fixes it."""
    return JSONResponse(await run_in_threadpool(_doctor))


def _doctor():
    try:
        import friday.config  # noqa: F401   (.env, so the key check is honest)
    except Exception:  # noqa: BLE001
        pass
    checks = []

    def add(name, ok, detail, fix=""):
        checks.append({"name": name, "ok": bool(ok), "detail": detail, "fix": fix})

    add("owner enrolled", access.enrolled(),
        "%d face descriptors on file" % len(access.load_owner()),
        "run scripts/enrol_face.py <folder of your photos>")
    add("PIN fallback", access.has_pin(), "set" if access.has_pin() else "not set",
        "set one from the lock screen when the camera is busy")
    cam = camera_mod.status()
    add("camera", not cam["busy"], cam["why"] or "free", "close the app holding it, or use the PIN")
    add("vision model", bool(os.getenv("GOOGLE_API_KEY")), "GOOGLE_API_KEY present"
        if os.getenv("GOOGLE_API_KEY") else "no GOOGLE_API_KEY", "add GOOGLE_API_KEY to .env")
    mcp = _connections().get("mcp_server", {})
    add("MCP server", mcp.get("status") == "up", "%s %s" % (mcp.get("status"), mcp.get("target", "")),
        "start server.py")
    add("shared brain", _gbrain_status().get("status") == "available",
        _gbrain_status().get("status", "?"), "check the GBrain bun install")
    try:
        models = sorted(pth.name for pth in (UI_DIR / "models").glob("*.bin"))
    except OSError:
        models = []
    add("recognition models local", len(models) >= 3, "%d weight files served locally" % len(models),
        "re-download into ui/models/ so the gate does not wait on a CDN")
    return {"ok": all(c["ok"] for c in checks), "checks": checks,
            "healthy": sum(1 for c in checks if c["ok"]), "total": len(checks)}


async def api_deck(request):
    from friday import deck
    return JSONResponse(await run_in_threadpool(deck.state))


async def api_deck_run(request):
    from friday import deck
    try:
        data = await request.json()
    except Exception:  # noqa: BLE001
        data = {}
    return JSONResponse(await run_in_threadpool(deck.run, data.get("id", "")))


async def api_harness(request):
    from friday import harness as H
    hints = ["browse", "autonomous_browse", "computer_use", "scrape"]
    return JSONResponse({"engines": H.availability(),
                         "selection": {h: H.select(h) for h in hints}})


async def api_objective(request):
    # Honest capture: the read-only UI never dispatches side-effectful work.
    # Live objective -> plan -> execute is the gates + harness workstream.
    try:
        data = await request.json()
    except Exception:  # noqa: BLE001
        data = {}
    text = (data.get("objective") or "").strip()
    return JSONResponse({
        "captured": bool(text), "objective": text, "status": "captured",
        "note": "Jarvis will plan and dispatch this once the gate cards and the "
                "browser/PC harness are wired. This UI is read-only by design; "
                "it does not run side-effectful work yet."})


async def api_ask(request):
    from friday import voice_brain as V
    try:
        data = await request.json()
    except Exception:  # noqa: BLE001
        data = {}
    text = (data.get("text") or "").strip()
    if not text:
        return JSONResponse({"reply": "", "empty": True})
    return JSONResponse(await run_in_threadpool(V.reply, text, data.get("history") or []))


async def api_hermes_progress(request):
    """Live progress of the Hermes runs the browser is following.

    The page polls this after a delegation and SPEAKS the line each time
    `seq` changes, then the result once, then stops following. Owner's
    ask (2026-09-02): "it just said 'Sir, I delegated the task to Hermes'
    but it does not tell me anything time to time".
    """
    ids = [i for i in (request.query_params.get("ids") or "").split(",") if i]
    if not ids:
        return JSONResponse({"runs": []})

    def _read():
        from friday.tools.hermes_control import supervisor
        sup = supervisor()
        out = []
        for wid in ids[:8]:
            try:
                out.append(sup.progress(wid))
            except Exception as exc:  # noqa: BLE001
                out.append({"work_run_id": wid, "status": "UNKNOWN",
                            "line": "", "seq": 0, "error": str(exc)[:120]})
        return out
    return JSONResponse({"runs": await run_in_threadpool(_read)})


async def api_browser_open(request):
    from friday import ui_browser as B
    try:
        data = await request.json()
    except Exception:  # noqa: BLE001
        data = {}
    url = (data.get("url") or "").strip()
    if not url:
        return JSONResponse({"status": "error", "error": "no url"}, status_code=400)
    return JSONResponse(await run_in_threadpool(B.open_url, url))


async def api_browser_act(request):
    from friday import ui_browser as B
    try:
        data = await request.json()
    except Exception:  # noqa: BLE001
        data = {}
    return JSONResponse(await run_in_threadpool(
        B.request_act, data.get("kind", ""), data.get("selector", ""),
        data.get("text", "")))


async def api_browser_shot(request):
    from friday import ui_browser as B
    p = B.shot_path(request.query_params.get("name", ""))
    if p is None:
        return PlainTextResponse("not found", status_code=404)
    return FileResponse(p)


async def api_gate(request):
    from friday import ui_browser as B
    from friday import control
    conn = await run_in_threadpool(_connect)
    try:
        durable = _durable_gates(conn)
    finally:
        if conn is not None:
            conn.close()
    return JSONResponse({"browser_gates": B.pending_gates(),
                         "tool_gates": control.pending(), "durable": durable})


async def api_gate_approve(request):
    """Approve one gate. The nonce decides which book owns it -- a browser
    action or an agent tool call; a nonce is only ever in one of them."""
    from friday import ui_browser as B
    from friday import control
    try:
        data = await request.json()
    except Exception:  # noqa: BLE001
        data = {}
    nonce = data.get("nonce", "")
    if any(c["nonce"] == nonce for c in control.pending()):
        return JSONResponse(await run_in_threadpool(control.approve, nonce))
    return JSONResponse(await run_in_threadpool(B.approve_act, nonce))


async def api_gate_reject(request):
    from friday import ui_browser as B
    try:
        data = await request.json()
    except Exception:  # noqa: BLE001
        data = {}
    from friday import control
    nonce = data.get("nonce", "")
    if any(c["nonce"] == nonce for c in control.pending()):
        return JSONResponse(await run_in_threadpool(
            control.reject, nonce, data.get("reason", "")))
    return JSONResponse(await run_in_threadpool(
        B.reject_act, nonce, data.get("reason", "")))


async def api_graph(request):
    from friday import memory_graph
    return JSONResponse(await run_in_threadpool(memory_graph.build))


async def api_graph_adjacency(request):
    from friday import memory_graph
    return JSONResponse(await run_in_threadpool(memory_graph.adjacency))


async def api_org(request):
    from friday import org
    return JSONResponse(await run_in_threadpool(org.state))


async def api_org_route(request):
    from friday import org
    return JSONResponse(await run_in_threadpool(
        org.route, request.query_params.get("task", "")))


async def api_org_assemble(request):
    from friday import org
    return JSONResponse(await run_in_threadpool(
        org.assemble, request.query_params.get("goal", "")))


def _probe_helpers():
    from friday import fabric
    return {"providers": fabric.report(), "families": fabric.family_report(),
            "processes": fabric.processes()}


async def api_helpers(request):
    now = time.time()
    if _HELPERS_CACHE["value"] is not None and \
            now - _HELPERS_CACHE["at"] < _HELPERS_TTL:
        return JSONResponse(_HELPERS_CACHE["value"])
    value = await run_in_threadpool(_probe_helpers)
    _HELPERS_CACHE.update(at=now, value=value)
    return JSONResponse(value)


_TTS_DIR = Path(__file__).resolve().parent.parent / "data" / "tts_cache"


#: One warm HTTP client for Deepgram, so the TLS handshake is paid once, not on
#: every line. A cold call is ~3s; a warm one is ~1s, which is the whole point.
_DG_CLIENT = None


def _deepgram_tts(text, dg_key):
    global _DG_CLIENT
    import httpx
    if _DG_CLIENT is None:
        _DG_CLIENT = httpx.Client(timeout=20)
    model = os.getenv("TTS_DG_MODEL", "aura-2-thalia-en")
    r = _DG_CLIENT.post(
        "https://api.deepgram.com/v1/speak?model=%s&encoding=mp3" % model,
        headers={"Authorization": "Token " + dg_key, "Content-Type": "application/json"},
        json={"text": text})
    r.raise_for_status()
    return r.content


def _openai_tts(text, key):
    from openai import OpenAI
    client = OpenAI(api_key=key)
    resp = client.audio.speech.create(
        model=os.getenv("TTS_MODEL", "tts-1"),
        voice=os.getenv("TTS_VOICE", "nova"),
        # Same default as the LiveKit agent (friday.providers / TTS_SPEED) so
        # she does not talk faster in the browser than in the room.
        speed=float(os.getenv("TTS_SPEED", "1.0")),
        input=text, response_format="mp3")
    return resp.content if hasattr(resp, "content") else resp.read()


def _tts_provider():
    """OpenAI tts-1 'nova' unless told otherwise - the voice the LiveKit agent
    uses, so Friday sounds like one person whichever way the boss talks to her.

    Deepgram Aura is faster (~1s warm against ~4s) and is worth having, but it
    is a different voice, and picking it automatically because a key happened
    to be in .env is how her voice changed under the owner without anyone
    asking. So it is opt-in: TTS_PROVIDER=deepgram."""
    forced = os.getenv("TTS_PROVIDER", "").lower()
    if forced in ("deepgram", "openai"):
        return forced
    return "openai"


def _tts_bytes(text):
    """Friday's spoken voice, cached by (provider, voice, text) so switching
    provider never serves the wrong cached clip. Empty bytes if no key."""
    import hashlib
    text = (text or "").strip()[:800]
    if not text:
        return b""
    try:
        import friday.config  # noqa: F401  loads .env
    except Exception:  # noqa: BLE001
        pass
    provider = _tts_provider()
    voice = (os.getenv("TTS_DG_MODEL", "aura-2-thalia-en") if provider == "deepgram"
             else os.getenv("TTS_VOICE", "nova"))
    _TTS_DIR.mkdir(parents=True, exist_ok=True)
    tag = "%s|%s|%s" % (provider, voice, text)
    f = _TTS_DIR / (hashlib.sha256(tag.encode("utf-8")).hexdigest()[:24] + ".mp3")
    if f.exists():
        return f.read_bytes()
    try:
        if provider == "deepgram":
            data = _deepgram_tts(text, os.getenv("DEEPGRAM_API_KEY"))
        else:
            if not os.getenv("OPENAI_API_KEY"):
                return b""
            data = _openai_tts(text, os.getenv("OPENAI_API_KEY"))
        if data:
            f.write_bytes(data)
        return data
    except Exception:  # noqa: BLE001
        # A failed fast provider should not leave Friday mute: fall back to OpenAI.
        if provider == "deepgram" and os.getenv("OPENAI_API_KEY"):
            try:
                data = _openai_tts(text, os.getenv("OPENAI_API_KEY"))
                return data
            except Exception:  # noqa: BLE001
                return b""
        return b""


async def api_tts(request):
    from starlette.responses import Response
    data = await run_in_threadpool(_tts_bytes, request.query_params.get("text", ""))
    if not data:
        return PlainTextResponse("tts unavailable", status_code=503)
    return Response(data, media_type="audio/mpeg",
                    headers={"Cache-Control": "private, max-age=86400"})


async def health(request):
    return PlainTextResponse("ok")


def _event_frame(kind, payload, run_id=""):
    body = {"v": 1, "type": kind, "run_id": run_id, "at": _now(),
            "payload": payload}
    return {"event": kind, "data": json.dumps(body)}


async def events(request):
    """One SSE stream the whole UI subscribes to (directive 6.2).

    Tails run_events + objective_events by autoincrement id and emits a
    model.health heartbeat so the UI always shows a live connection, even on
    a fresh DB with no runs yet.
    """
    from sse_starlette.sse import EventSourceResponse

    async def gen():
        conn = await run_in_threadpool(_connect)
        try:
            r = _rows(conn, "SELECT MAX(event_id) AS m FROM run_events")
            cur_run = (r[0]["m"] or 0) if r else 0
            o = _rows(conn, "SELECT MAX(id) AS m FROM objective_events")
            cur_obj = (o[0]["m"] or 0) if o else 0
        finally:
            if conn is not None:
                conn.close()
        yield _event_frame("model.health", {"connected": True,
                                             "connections": _connections()})
        beat = 0
        last_bts = 0.0
        while True:
            if await request.is_disconnected():
                break
            conn = await run_in_threadpool(_connect)
            try:
                new_run = _rows(conn, "SELECT event_id, run_id, kind, message, "
                                      "created_at FROM run_events "
                                      "WHERE event_id > ? ORDER BY event_id "
                                      "LIMIT 50", (cur_run,))
                for e in new_run:
                    cur_run = e["event_id"]
                    yield _event_frame("step.progress", {
                        "kind": e["kind"], "message": e["message"],
                        "at": e["created_at"]}, run_id=e["run_id"] or "")
                new_obj = _rows(conn, "SELECT id, run_id, event, detail, at "
                                      "FROM objective_events WHERE id > ? "
                                      "ORDER BY id LIMIT 50", (cur_obj,))
                for e in new_obj:
                    cur_obj = e["id"]
                    yield _event_frame("run.planned", {
                        "event": e["event"], "detail": e["detail"],
                        "at": e["at"]}, run_id=e["run_id"] or "")
            finally:
                if conn is not None:
                    conn.close()
            try:  # browser.action / gate.* from the UI's own driver
                from friday import ui_browser as B
                for be in B.drain_events(last_bts):
                    last_bts = be["at"]
                    yield _event_frame(be["type"], be["payload"])
            except Exception:  # noqa: BLE001
                pass
            beat += 1
            if beat % 4 == 0:  # ~every 6s
                yield _event_frame("model.health",
                                   {"connected": True,
                                    "connections": _connections()})
            await asyncio.sleep(1.5)

    return EventSourceResponse(gen())


def _start_background_health():
    """Start the warmer at boot.

    It used to start lazily, on the first memory_snapshot() call, so a control
    room that never opened the memory view showed GBrain and Hermes as
    "checking" for as long as it was open.
    """
    global _MEM_THREAD
    with _MEM_LOCK:
        if _MEM_THREAD is not None:
            return
        _MEM_THREAD = threading.Thread(target=_mem_refresher, daemon=True,
                                       name="health-refresh")
        _MEM_THREAD.start()


async def api_stt(ws):
    """Device-selectable speech-to-text. The browser streams PCM from the mic
    it chose (the headset when present); this relays it to Deepgram and streams
    transcripts back. Opt-in from the UI -- the default path is the browser's
    own recogniser, which cannot pick a device. The gate leaves websocket scope
    alone, and only the owner's own microphone audio crosses this socket.
    """
    # Browsers always send Origin on a WebSocket handshake, so no Origin at all
    # is not a page: it is curl, a script, or something on this machine that
    # wants transcription on the owner's key. Fail closed here (the HTTP
    # endpoints keep allowing header-less same-origin GETs; a socket is not one).
    if not ws.headers.get("origin") or not _same_origin(ws.headers):
        await ws.close(code=4403)      # another site's page: not this owner's mic
        return
    await ws.accept()
    try:
        import friday.config  # noqa: F401  loads .env
    except Exception:  # noqa: BLE001
        pass
    key = os.getenv("DEEPGRAM_API_KEY")
    if not key:
        try:
            await ws.send_json({"type": "error", "error": "no DEEPGRAM_API_KEY"})
        finally:
            await ws.close()
        return
    rate = ws.query_params.get("rate", "48000")
    try:
        int(rate)
    except (TypeError, ValueError):
        rate = "48000"
    model = os.getenv("STT_DG_MODEL", "nova-2")
    # Deepgram's `endpointing` is the quiet (ms) after which it CLOSES a
    # phrase and marks it final. 300 fired a final on every breath, and the
    # page used to send each one straight to the brain - the "pause treated
    # as a complete sentence" bug. The page now accumulates finals behind
    # its own pause window; this just stops the stream fragmenting on a
    # breath in the first place. Tunable: STT_DG_ENDPOINTING_MS.
    endpointing = os.getenv("STT_DG_ENDPOINTING_MS", "1000")
    if not endpointing.isdigit():
        endpointing = "1000"
    dg_url = ("wss://api.deepgram.com/v1/listen?model=%s&encoding=linear16"
              "&sample_rate=%s&channels=1&interim_results=true&smart_format=true"
              "&punctuate=true&endpointing=%s" % (model, rate, endpointing))
    import websockets as _wslib
    try:
        try:
            dg = await _wslib.connect(dg_url, additional_headers={"Authorization": "Token " + key})
        except TypeError:                       # websockets < 14 used extra_headers
            dg = await _wslib.connect(dg_url, extra_headers={"Authorization": "Token " + key})
    except Exception as exc:  # noqa: BLE001
        try:
            await ws.send_json({"type": "error", "error": "deepgram connect failed: %s" % exc})
        finally:
            await ws.close()
        return

    async def browser_to_dg():
        try:
            while True:
                msg = await ws.receive()
                if msg.get("type") == "websocket.disconnect":
                    break
                chunk = msg.get("bytes")
                if chunk:
                    await dg.send(chunk)
                elif msg.get("text") == "__close__":
                    break
        except Exception:  # noqa: BLE001
            pass
        try:
            await dg.send(json.dumps({"type": "CloseStream"}))
        except Exception:  # noqa: BLE001
            pass

    async def dg_to_browser():
        try:
            async for raw in dg:
                try:
                    data = json.loads(raw)
                except Exception:  # noqa: BLE001
                    continue
                alts = (data.get("channel") or {}).get("alternatives") or []
                text = (alts[0].get("transcript") if alts else "") or ""
                if text:
                    await ws.send_json({"type": "transcript", "text": text,
                                        "final": bool(data.get("is_final"))})
        except Exception:  # noqa: BLE001
            pass

    try:
        await asyncio.gather(browser_to_dg(), dg_to_browser())
    finally:
        try:
            await dg.close()
        except Exception:  # noqa: BLE001
            pass
        try:
            await ws.close()
        except Exception:  # noqa: BLE001
            pass


def create_app():
    # The face gate is middleware: while locked, only "/", /ui/*, /health and
    # /api/auth/* answer; everything else is 423 and written to the access log.
    _start_background_health()
    return Starlette(middleware=[Middleware(access.GateMiddleware)], routes=[
        Route("/", index),
        Route("/health", health),
        Route("/api/state", api_state),
        Route("/api/memory", api_memory),
        Route("/api/memory_flow", api_memory_flow),
        Route("/api/memory_snapshot", api_memory_snapshot),
        Route("/api/os_map", api_os_map),
        Route("/api/vault", api_vault),
        Route("/api/vault/file", api_vault_file),
        Route("/api/vault/sync", api_vault_sync, methods=["POST"]),
        Route("/api/control", api_control),
        Route("/api/graph", api_graph),
        Route("/api/graph/adjacency", api_graph_adjacency),
        Route("/api/org", api_org),
        Route("/api/org/route", api_org_route),
        Route("/api/org/assemble", api_org_assemble),
        Route("/api/helpers", api_helpers),
        Route("/api/tts", api_tts),
        Route("/api/memory/context", api_memory_context),
        Route("/api/memory/tiers", api_memory_tiers),
        Route("/api/memory/log", api_memory_log, methods=["POST"]),
        Route("/api/auth/status", api_auth_status),
        Route("/api/auth/enrol", api_auth_enrol, methods=["POST"]),
        Route("/api/auth/verify", api_auth_verify, methods=["POST"]),
        Route("/api/auth/lock", api_auth_lock, methods=["POST"]),
        Route("/api/auth/log", api_auth_log, methods=["POST"]),
        Route("/api/auth/recent", api_auth_recent),
        Route("/api/vision/describe", api_vision_describe, methods=["POST"]),
        Route("/api/vision/screen", api_vision_screen, methods=["POST"]),
        Route("/api/camera", api_camera),
        Route("/api/desk", api_desk),
        Route("/api/camera/hold", api_camera_hold, methods=["POST"]),
        Route("/api/auth/pin/set", api_pin_set, methods=["POST"]),
        Route("/api/auth/pin/verify", api_pin_verify, methods=["POST"]),
        Route("/api/doctor", api_doctor),
        Route("/api/deck", api_deck),
        Route("/api/deck/run", api_deck_run, methods=["POST"]),
        Route("/api/harness", api_harness),
        Route("/api/objective", api_objective, methods=["POST"]),
        Route("/api/ask", api_ask, methods=["POST"]),
        Route("/api/hermes/progress", api_hermes_progress),
        WebSocketRoute("/api/stt", api_stt),
        Route("/api/browser/open", api_browser_open, methods=["POST"]),
        Route("/api/browser/act", api_browser_act, methods=["POST"]),
        Route("/api/browser/shot", api_browser_shot),
        Route("/api/gate", api_gate),
        Route("/api/gate/approve", api_gate_approve, methods=["POST"]),
        Route("/api/gate/reject", api_gate_reject, methods=["POST"]),
        Route("/events", events),
        # the orb module and any other page assets; only files under ui/
        Mount("/ui", app=StaticFiles(directory=str(UI_DIR)), name="ui"),
    ])


app = create_app()
