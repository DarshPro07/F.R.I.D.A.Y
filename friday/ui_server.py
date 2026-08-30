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
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles
from starlette.concurrency import run_in_threadpool

from friday import access
from friday import camera as camera_mod
from friday.store import DEFAULT_DB

UI_DIR = Path(__file__).resolve().parent.parent / "ui"
MCP_HOST = os.getenv("ADA_MCP_HOST", "127.0.0.1")
MCP_PORT = int(os.getenv("ADA_MCP_PORT", "8000"))

# ponytail: health checks spawn a bun subprocess (GBrain) / do HTTP (Hermes).
# Cache the whole connections block briefly so a 3s UI poll never spawns bun.
_CONN_CACHE = {"at": 0.0, "value": None}
_CONN_TTL = 10.0
_GBRAIN_TTL = 60.0          # available() spawns bun; once a minute is plenty
_GBRAIN_CACHE = {"at": 0.0, "value": None}

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


def _mem_refresher():
    while True:
        try:
            snap = _compute_mem_snapshot()
            with _MEM_LOCK:
                _MEM_CACHE["data"], _MEM_CACHE["at"] = snap, time.time()
        except Exception:  # noqa: BLE001
            pass
        time.sleep(60)


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


def _gbrain_status():
    now = time.time()
    if _GBRAIN_CACHE["value"] is not None and now - _GBRAIN_CACHE["at"] < _GBRAIN_TTL:
        return _GBRAIN_CACHE["value"]
    try:
        from friday.brain import SharedBrainAdapter
        up = SharedBrainAdapter().available()
        value = {"status": "available" if up else "unavailable"}
    except Exception as exc:  # noqa: BLE001
        value = {"status": "unavailable", "error": str(exc)}
    _GBRAIN_CACHE.update(at=now, value=value)
    return value


def _hermes_status():
    try:
        from friday import hermes_health as hh
        probe = hh.live_probe_factory("http://%s:%s" % (MCP_HOST, MCP_PORT),
                                      timeout=2.0)
        report = probe()
        healthy = bool(_val(report, "healthy", False))
        return {
            "status": "healthy" if healthy else "degraded",
            "summary": _val(report, "summary", ""),
            "failed_layer": _val(report, "failed_layer", ""),
        }
    except Exception as exc:  # noqa: BLE001
        return {"status": "unavailable", "error": str(exc)}


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


def _metrics(conn):
    # model_tokens lives on run_portions in this schema; [] -> 0 (honest).
    tok = _rows(conn, "SELECT COALESCE(SUM(model_tokens),0) AS t FROM run_portions")
    tokens = (tok[0]["t"] if tok else 0) or 0
    by_state = _rows(conn, "SELECT state, COUNT(*) AS n FROM runs "
                           "GROUP BY state ORDER BY n DESC")
    open_tasks = _rows(conn, "SELECT COUNT(*) AS n FROM objective_tasks "
                             "WHERE status NOT IN "
                             "('done','completed','verified','cancelled','failed')")
    dur = _rows(conn, "SELECT AVG((julianday(updated_at)-julianday(created_at))"
                      "*86400.0) AS s FROM (SELECT created_at, updated_at FROM "
                      "runs WHERE updated_at IS NOT NULL "
                      "ORDER BY created_at DESC LIMIT 30)")
    return {
        "model_tokens": tokens,
        "runs_by_state": by_state,
        "open_tasks": (open_tasks[0]["n"] if open_tasks else 0),
        "avg_run_secs": round(dur[0]["s"], 1) if dur and dur[0]["s"] else 0,
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
            "objective": _objective(conn),
            "metrics": _metrics(conn),
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


_TTS_DIR = Path(__file__).resolve().parent.parent / "data" / "tts_cache"


def _tts_bytes(text):
    """The LiveKit Friday voice, exactly: OpenAI tts-1 / nova / 1.15x. Cached
    by text hash so a repeated line costs nothing. Empty bytes if no key."""
    import hashlib
    text = (text or "").strip()[:800]
    if not text:
        return b""
    try:
        import friday.config  # noqa: F401  loads .env
    except Exception:  # noqa: BLE001
        pass
    key = os.getenv("OPENAI_API_KEY")
    if not key:
        return b""
    _TTS_DIR.mkdir(parents=True, exist_ok=True)
    f = _TTS_DIR / (hashlib.sha256(text.encode("utf-8")).hexdigest()[:24] + ".mp3")
    if f.exists():
        return f.read_bytes()
    try:
        from openai import OpenAI
        client = OpenAI(api_key=key)
        resp = client.audio.speech.create(
            model=os.getenv("TTS_MODEL", "tts-1"),
            voice=os.getenv("TTS_VOICE", "nova"),
            speed=float(os.getenv("TTS_SPEED", "1.15")),
            input=text, response_format="mp3")
        data = resp.content if hasattr(resp, "content") else resp.read()
        f.write_bytes(data)
        return data
    except Exception:  # noqa: BLE001
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


def create_app():
    # The face gate is middleware: while locked, only "/", /ui/*, /health and
    # /api/auth/* answer; everything else is 423 and written to the access log.
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
        Route("/api/camera/hold", api_camera_hold, methods=["POST"]),
        Route("/api/auth/pin/set", api_pin_set, methods=["POST"]),
        Route("/api/auth/pin/verify", api_pin_verify, methods=["POST"]),
        Route("/api/doctor", api_doctor),
        Route("/api/deck", api_deck),
        Route("/api/deck/run", api_deck_run, methods=["POST"]),
        Route("/api/harness", api_harness),
        Route("/api/objective", api_objective, methods=["POST"]),
        Route("/api/ask", api_ask, methods=["POST"]),
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
