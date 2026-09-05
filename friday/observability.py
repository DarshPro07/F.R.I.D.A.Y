"""
Observability (PRD v3.1 FR-054, 12.3 Operational Diagnostics).

Two things, both read-only and both assembled from durable state only:

  trace(run_id)   one timeline for one objective, reconstructed from the
                  objective ledger (events + tasks), the tool-result
                  table, the model-gateway call ledger, the executor
                  (worker) runs and the trust audit log. No model
                  thoughts, no prompts: state transitions, tool calls,
                  workers, latency, retries, resource use, errors and
                  verification outcomes (FR-054 acceptance: "a single
                  trace reconstructs what happened without reading raw
                  model thoughts").

  diagnostics()   the single operational view (12.3): build identity,
                  objective store health, memory store health, provider
                  status, Hermes/worker health, browser connection, voice
                  gateway, MCP/capability health, queue depth, resource
                  pressure and the most recent critical failures.
                  Redacted by default so the output is safe to share.

Every section is best-effort and never raises: a subsystem that cannot be
probed reports `unavailable` with the reason, which is itself a
diagnostic.
"""
from __future__ import annotations

import logging

import json
import os
import re
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path

from friday.store import Store

# ---------------------------------------------------------------------------
# Redaction
# ---------------------------------------------------------------------------

_SECRET_KEYS = ("password", "passwd", "secret", "token", "api_key", "apikey",
                "authorization", "cookie", "credential", "private_key", "bearer")
_KEY_WORD_RE = re.compile(r"[a-z0-9]+")


def _secret_key(key: str) -> bool:
    """`api_key`, `Authorization`, `access_token` are secrets; `tokens_in`
    and `token_budget` are accounting and must survive redaction."""
    words = set(_KEY_WORD_RE.findall(str(key).lower()))
    joined = "_".join(sorted(words))
    return any(s in words or s == joined for s in _SECRET_KEYS)
_TOKEN_RE = re.compile(
    r"\b(sk-ant-[A-Za-z0-9_-]{8,}|sk-[A-Za-z0-9_-]{16,}|ghp_[A-Za-z0-9]{16,}"
    r"|github_pat_[A-Za-z0-9_]{16,}|AKIA[0-9A-Z]{12,}|AIza[0-9A-Za-z_-]{20,}"
    r"|xox[baprs]-[A-Za-z0-9-]{10,}|ya29\.[A-Za-z0-9_-]{20,}"
    r"|eyJ[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,})")
REDACTED = "[REDACTED]"


def redact(value):
    """Secrets out of any nested structure: by key name and by token shape."""
    if isinstance(value, dict):
        return {k: (REDACTED if _secret_key(k) and not isinstance(v, (int, float, bool))
                    else redact(v)) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [redact(v) for v in value]
    if isinstance(value, str):
        return _TOKEN_RE.sub(REDACTED, value)
    return value


# ---------------------------------------------------------------------------
# Trace
# ---------------------------------------------------------------------------

def _parse(text):
    if text is None or text == "":
        return None
    if isinstance(text, (dict, list)):
        return text
    try:
        return json.loads(text)
    except (TypeError, ValueError):
        return text


def _utc(iso: str | None) -> datetime | None:
    """Every ledger timestamp as an aware UTC datetime. Naive stamps (older
    rows written with local time) are taken as local and converted, so one
    timeline orders correctly across stores."""
    if not iso:
        return None
    try:
        dt = datetime.fromisoformat(str(iso).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if dt.tzinfo is None:
        dt = dt.astimezone()          # local -> aware
    return dt.astimezone(timezone.utc)


def _stamp(iso: str | None) -> str:
    dt = _utc(iso)
    return dt.isoformat(timespec="milliseconds") if dt else ""


def _ms_between(a: str | None, b: str | None) -> int | None:
    da, db_ = _utc(a), _utc(b)
    if da is None or db_ is None:
        return None
    return int((db_ - da).total_seconds() * 1000)


def _gateway_calls(run_id: str) -> list[dict]:
    try:
        from friday.model_gateway import GatewayTelemetry
        return GatewayTelemetry().for_objective(run_id)
    except Exception as exc:  # noqa: BLE001
        return [{"error": f"gateway ledger unavailable: {exc}"}]


def _audit_rows(run_id: str) -> list[dict]:
    try:
        from friday import trust as T
        return T.audit().query(objective_id=run_id, limit=500)
    except Exception as exc:  # noqa: BLE001
        return [{"error": f"audit log unavailable: {exc}"}]


def _executor_runs(store: Store, run_id: str, task_rows: list[dict]) -> list[dict]:
    """Worker runs referenced by the objective's tasks (work_run_id in
    their result), plus any executor run whose bundle names the run."""
    wanted: set[str] = set()
    for task in task_rows:
        result = _parse(task.get("result"))
        if isinstance(result, dict) and result.get("work_run_id"):
            wanted.add(str(result["work_run_id"]))
    rows: list[dict] = []
    try:
        for row in store.executor_runs(limit=200):
            bundle = row.get("task_bundle") or ""
            if row.get("run_id") in wanted or run_id in str(bundle):
                rows.append({
                    "worker_run_id": row.get("run_id"),
                    "executor": row.get("executor_type"),
                    "status": row.get("status"),
                    "pid": row.get("pid"),
                    "started_at": row.get("started_at"),
                    "ended_at": row.get("ended_at"),
                    "resume_count": row.get("resume_count"),
                    "exit_code": row.get("exit_code"),
                    "summary": (row.get("summary") or "")[:300],
                    "completion_evidence": (row.get("completion_evidence") or "")[:300],
                })
    except Exception as exc:  # noqa: BLE001
        rows.append({"error": f"executor runs unavailable: {exc}"})
    return rows


def _tool_results(store: Store, run_id: str, task_rows: list[dict]) -> list[dict]:
    """Capability calls recorded against this objective or its tasks'
    capability runs (`tool_results` rows keyed by contracts.Run ids)."""
    ids = {run_id}
    for task in task_rows:
        result = _parse(task.get("result"))
        if isinstance(result, dict):
            for key in ("run_id", "capability_run_id"):
                if result.get(key):
                    ids.add(str(result[key]))
    out: list[dict] = []
    try:
        conn = store._conn
        marks = ",".join("?" for _ in ids)
        for r in conn.execute(
                f"SELECT run_id, tool_id, status, started_at, completed_at, error, "
                f"verify_method, verify_evidence FROM tool_results WHERE run_id IN ({marks}) "
                f"ORDER BY id", tuple(ids)):
            row = dict(r)
            row["latency_ms"] = _ms_between(row.get("started_at"), row.get("completed_at"))
            out.append(row)
    except Exception as exc:  # noqa: BLE001
        out.append({"error": f"tool results unavailable: {exc}"})
    return out


def trace(run_id: str, *, store: Store | None = None, redacted: bool = True) -> dict:
    """The whole story of one objective, in time order, from durable state."""
    db = store or Store()
    run = db.objective_run(run_id)
    if run is None:
        return {"run_id": run_id, "found": False,
                "say": f"no objective {run_id} in the ledger"}
    task_rows = db.objective_tasks(run_id)
    events = db.objective_events(run_id, limit=5000)
    gateway = _gateway_calls(run_id)
    audit = _audit_rows(run_id)
    workers = _executor_runs(db, run_id, task_rows)
    tools = _tool_results(db, run_id, task_rows)

    timeline: list[dict] = []
    for e in events:
        timeline.append({"at": e.get("at"), "kind": "objective", "event": e.get("event"),
                         "task_id": e.get("task_id"), "detail": _parse(e.get("detail"))})
    for t in tools:
        if "error" in t and "tool_id" not in t:
            continue
        timeline.append({"at": t.get("started_at"), "kind": "tool", "event": f"tool.{t['status']}",
                         "tool_id": t.get("tool_id"), "latency_ms": t.get("latency_ms"),
                         "error": t.get("error"),
                         "verification": {"method": t.get("verify_method"),
                                          "evidence": t.get("verify_evidence")}})
    for g in gateway:
        if "error" in g and "created_at" not in g:
            continue
        timeline.append({"at": g.get("created_at"), "kind": "model", "event": "gateway.call",
                         "provider": g.get("provider"), "model": g.get("model"),
                         "worker": g.get("worker"), "task_class": g.get("task_class"),
                         "status": g.get("status"), "latency_ms": g.get("latency_ms"),
                         "tokens_in": g.get("input_tokens"), "tokens_out": g.get("output_tokens"),
                         "retries": g.get("retries"), "failover_count": g.get("failover_count"),
                         "error": g.get("error") or None})
    for a in audit:
        if "error" in a and "at" not in a:
            continue
        timeline.append({"at": a.get("at"), "kind": "policy", "event": f"policy.{a.get('decision')}",
                         "actor": a.get("actor"), "action": a.get("action"), "tier": a.get("tier"),
                         "target": a.get("target"), "result": a.get("result")})
    for w in workers:
        if "error" in w:
            continue
        timeline.append({"at": w.get("started_at"), "kind": "worker", "event": "worker.started",
                         "executor": w.get("executor"), "worker_run_id": w.get("worker_run_id"),
                         "pid": w.get("pid")})
        if w.get("ended_at"):
            timeline.append({"at": w.get("ended_at"), "kind": "worker",
                             "event": f"worker.{w.get('status')}",
                             "worker_run_id": w.get("worker_run_id"),
                             "exit_code": w.get("exit_code"),
                             "resume_count": w.get("resume_count"),
                             "completion_evidence": w.get("completion_evidence")})
    for row in timeline:
        row["at"] = _stamp(row.get("at"))
    timeline.sort(key=lambda row: row["at"])

    tasks = []
    for t in task_rows:
        tasks.append({
            "task_id": t.get("task_id"), "capability": t.get("capability"),
            "status": t.get("status"), "attempts": t.get("attempts"),
            "failure_kind": t.get("failure_kind"),
            "latency_ms": _ms_between(t.get("started_at"), t.get("finished_at")),
            "evidence": (t.get("evidence") or "")[:400],
            "blocked_by": t.get("blocked_by"),
        })
    retries = sum(max(0, int(t.get("attempts") or 0) - 1) for t in task_rows)
    errors = [row for row in timeline if row.get("error") or
              str(row.get("event", "")).endswith((".failed", ".FAILED", ".error"))]
    verification = {
        "tasks_with_evidence": sum(1 for t in task_rows if t.get("evidence")),
        "tasks": len(task_rows),
        "tool_calls_verified": sum(1 for t in tools if t.get("verify_method")),
        "tool_calls": sum(1 for t in tools if "tool_id" in t),
    }
    usage = {
        "gateway_calls": sum(1 for g in gateway if "created_at" in g),
        "tokens_in": sum(int(g.get("input_tokens") or 0) for g in gateway if "created_at" in g),
        "tokens_out": sum(int(g.get("output_tokens") or 0) for g in gateway if "created_at" in g),
        "model_retries": sum(int(g.get("retries") or 0) for g in gateway if "created_at" in g),
        "failovers": sum(int(g.get("failover_count") or 0) for g in gateway if "created_at" in g),
        "workers": len([w for w in workers if "error" not in w]),
    }
    out = {
        "run_id": run_id, "found": True,
        "request": run.get("request"), "status": run.get("status"),
        "created_at": run.get("created_at"), "finished_at": run.get("finished_at"),
        "total_ms": _ms_between(run.get("created_at"), run.get("finished_at") or run.get("updated_at")),
        "task_class": run.get("task_class"), "risk_tier": run.get("risk_tier"),
        "lease": {"executor_id": run.get("lease_executor_id"),
                  "generation": run.get("lease_generation")},
        "tasks": tasks, "retries": retries, "usage": usage,
        "verification": verification,
        "errors": errors[:50],
        "timeline": timeline,
        "sources": {"objective_events": len(events), "tool_results": len(tools),
                    "gateway_calls": usage["gateway_calls"],
                    "audit_rows": len([a for a in audit if "at" in a]),
                    "worker_runs": usage["workers"]},
    }
    return redact(out) if redacted else out


def trace_text(run_id: str, *, store: Store | None = None) -> str:
    """The same trace as lines a person (or the voice) can read."""
    t = trace(run_id, store=store)
    if not t.get("found"):
        return t.get("say", "no such objective")
    lines = [f"{t['run_id']} {t['status']} - {t['request']}",
             f"tasks {len(t['tasks'])}  retries {t['retries']}  "
             f"model calls {t['usage']['gateway_calls']}  workers {t['usage']['workers']}  "
             f"evidence {t['verification']['tasks_with_evidence']}/{t['verification']['tasks']}"]
    for row in t["timeline"]:
        at = (row.get("at") or "")[11:19]
        what = str(row.get("event") or "")
        if what.startswith("tool."):
            what = "tool." + what[5:].upper()
        extra = row.get("tool_id") or row.get("model") or row.get("action") or row.get("task_id") or ""
        lat = f" {row['latency_ms']}ms" if row.get("latency_ms") is not None else ""
        err = f" ! {row['error']}" if row.get("error") else ""
        lines.append(f"  {at} [{row['kind']}] {what} {extra}{lat}{err}".rstrip())
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Diagnostics (12.3)
# ---------------------------------------------------------------------------

def _timed(fn):
    """Run one probe; never raise; report how long it took."""
    started = time.monotonic()
    try:
        out = fn()
        if not isinstance(out, dict):
            out = {"value": out}
        out.setdefault("status", "ok")
    except Exception as exc:  # noqa: BLE001
        out = {"status": "unavailable", "error": f"{type(exc).__name__}: {exc}"}
    out["probe_ms"] = int((time.monotonic() - started) * 1000)
    return out


def _build():
    from friday import build_identity as B
    build = B.current()
    return {"describe": B.describe(), "commit": build.commit, "dirty": build.dirty,
            "registry_hash": build.registry_hash, "capabilities": build.capabilities,
            "pid": build.pid}


def _sqlite_health(path: Path) -> dict:
    if not path.exists():
        return {"status": "missing", "path": str(path)}
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=2)
    try:
        check = conn.execute("PRAGMA quick_check").fetchone()[0]
        journal = conn.execute("PRAGMA journal_mode").fetchone()[0]
        return {"status": "ok" if check == "ok" else "corrupt", "quick_check": check,
                "journal_mode": journal, "bytes": path.stat().st_size, "path": str(path)}
    finally:
        conn.close()


def _objective_store(store: Store):
    path = Path(str(getattr(store, "path", "") or ":memory:"))
    out = (_sqlite_health(path) if str(path) != ":memory:" and path.exists()
           else {"status": "ok", "storage": "in-memory" if str(path) == ":memory:" else "missing",
                 "path": str(path)})
    runs = store.objective_runs(limit=200)
    by_status: dict[str, int] = {}
    for r in runs:
        by_status[r["status"]] = by_status.get(r["status"], 0) + 1
    out["runs_by_status"] = by_status
    out["active"] = [r["run_id"] for r in runs
                     if r["status"] not in ("COMPLETED", "FAILED", "CANCELLED", "PARTIAL")][:10]
    return out


def _memory_store(store: Store):
    conn = store._conn
    counts = {}
    for table in ("memories", "messages", "observations", "contradictions"):
        try:
            counts[table] = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        except sqlite3.Error as exc:
            counts[table] = f"error: {exc}"
    return {"counts": counts}


def _providers():
    """Provider inventory as the MODEL_GATEWAY last saw it (cached up to
    60 s; a cold gateway is reported as such rather than probed here -
    diagnostics must stay cheap and side-effect free)."""
    from friday import model_gateway as G
    out: dict = {}
    try:
        gw = G.ModelGateway()
        cached = getattr(gw, "_providers_cache", None)
        inventory = cached[1] if cached else {}
        out = {"cached": bool(cached), "inventory": inventory}
    except Exception as exc:  # noqa: BLE001
        out = {"error": f"gateway not constructible: {exc}"}
    keys = {name: bool(os.getenv(name)) for name in
            ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GOOGLE_API_KEY", "GROQ_API_KEY",
             "SARVAM_API_KEY", "DEEPGRAM_API_KEY")}
    return {"gateway_providers": out, "keys_present": keys}


def _hermes():
    from friday import hermes_bridge as hb
    from friday.tools import hermes_control as hc
    sup = hc.supervisor()
    health = sup.health()
    try:
        active = sup.active()
    except Exception:  # noqa: BLE001
        active = []
    return {"health": health, "active_work_runs": len(active),
            "state": getattr(sup, "state", None),
            "native_tools_healthy": None if not health.get("alive") else hb.native_tools_healthy(timeout=5.0)}


def _browser():
    from friday.toolsets import web as W
    session = getattr(W, "session", None)
    page = getattr(session, "_page", None)
    connected = bool(page is not None and getattr(session, "_browser", None) is not None)
    url = None
    if connected:
        try:
            url = page.url
        except Exception:  # noqa: BLE001
            url = "unreadable"
    return {"connected": connected, "url": url,
            "headless": os.getenv("ADA_BROWSER_HEADLESS", ""),
            "playwright": _importable("playwright")}


def _importable(name: str) -> bool:
    try:
        __import__(name)
        return True
    except Exception:  # noqa: BLE001
        return False


def _voice():
    keys = {"LIVEKIT_URL": bool(os.getenv("LIVEKIT_URL")),
            "LIVEKIT_API_KEY": bool(os.getenv("LIVEKIT_API_KEY")),
            "LIVEKIT_API_SECRET": bool(os.getenv("LIVEKIT_API_SECRET"))}
    import psutil
    workers = []
    for p in psutil.process_iter(["pid", "cmdline", "create_time"]):
        try:
            cmd = " ".join(p.info.get("cmdline") or [])
        except Exception:  # noqa: BLE001 - a process that vanished mid-iteration
            cmd = ""
        if "agent_friday.py" in cmd:
            workers.append({"pid": p.info["pid"],
                            "uptime_s": int(time.time() - (p.info.get("create_time") or time.time()))})
    return {"livekit_configured": all(keys.values()), "keys_present": keys,
            "voice_workers": workers, "ui_port": os.getenv("FRIDAY_UI_PORT", "8770")}


def _mcp():
    import socket
    from friday import fabric
    listening = False
    try:
        with socket.create_connection(("127.0.0.1", 8000), timeout=1.0):
            listening = True
    except OSError:
        listening = False
    report = fabric.family_report()
    states: dict[str, int] = {}
    for row in report:
        states[row.get("state", "?")] = states.get(row.get("state", "?"), 0) + 1
    from friday import capabilities as C
    return {"mcp_port_8000_listening": listening,
            "capabilities_declared": len(C.CAPABILITIES),
            "fabric_families": len(report), "fabric_states": states,
            "unavailable": [r.get("family") for r in report if r.get("state") == "UNAVAILABLE"][:20]}


def _queue(store: Store):
    from friday.governor import governor
    g = governor()
    pending_tasks = 0
    try:
        pending_tasks = store._conn.execute(
            "SELECT COUNT(*) FROM objective_tasks WHERE status IN ('QUEUED','READY','WAITING','INTERRUPTED')"
        ).fetchone()[0]
    except sqlite3.Error:
        pass
    return {"governor_queue_depth": g.queue_depth(),
            "governor_active_leases": len(g.active_leases()),
            "objective_tasks_pending": pending_tasks}


def _pressure():
    from friday.governor import governor
    return {"pressure": governor().pressure(fresh=True).to_dict()}


def _recent_failures(store: Store):
    out: list[dict] = []
    try:
        for e in store._conn.execute(
                "SELECT run_id, task_id, event, detail, at FROM objective_events "
                "WHERE event LIKE '%fail%' OR event LIKE '%orphan%' OR event LIKE '%interrupt%' "
                "OR event LIKE '%cancel%' ORDER BY id DESC LIMIT 10"):
            row = dict(e)
            row["detail"] = _parse(row.get("detail"))
            out.append(row)
    except sqlite3.Error as exc:
        out.append({"error": str(exc)})
    try:
        from friday import trust as T
        for a in T.audit().query(min_tier=T.R3, limit=10):
            if str(a.get("decision", "")).upper() in ("DENY", "REFUSED", "BLOCK", "BLOCKED"):
                out.append({"audit": a})
    except Exception as exc:  # noqa: BLE001
        out.append({"audit_error": str(exc)})
    return {"items": out}


def diagnostics(*, store: Store | None = None, redacted: bool = True,
                sections: tuple[str, ...] | None = None) -> dict:
    """The one view (12.3). Every section is independently best-effort."""
    db = store or Store()
    probes = {
        "build": _build,
        "objective_store": lambda: _objective_store(db),
        "memory_store": lambda: _memory_store(db),
        "providers": _providers,
        "hermes": _hermes,
        "browser": _browser,
        "voice": _voice,
        "mcp_capabilities": _mcp,
        "queue": lambda: _queue(db),
        "resource_pressure": _pressure,
        "recent_failures": lambda: _recent_failures(db),
    }
    wanted = sections or tuple(probes)
    report = {"at": datetime.now().isoformat(timespec="seconds"), "redacted": redacted}
    for name in wanted:
        if name in probes:
            report[name] = _timed(probes[name])
    report["healthy"] = all(
        report[n].get("status") == "ok" for n in wanted if n in report and n not in ("recent_failures",))
    report["unavailable"] = [n for n in wanted if n in report and report[n].get("status") != "ok"]
    return redact(report) if redacted else report
