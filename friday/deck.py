"""
friday/deck.py -- the Command Deck: one-click intents, and a queue for the rest.

Two honest classes of intent:

  LIVE   -- this process can actually do it now (memory, vitals, harness,
            gates, browser). Pressing the button runs it and returns a result.
  QUEUED -- it needs the live Friday agent (news brief, plan the day, run an
            objective). Pressing the button writes an intent to the queue and
            says so. It does NOT pretend the work happened.

The queue is a plain JSON file so it survives a restart and a human can read
it. When the control channel lands, the agent drains this queue -- the shape
is deliberately the same one the objective engine already speaks.
"""
from __future__ import annotations

import json
import os
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

QUEUE_PATH = Path(os.getenv("FRIDAY_DECK_QUEUE",
                            str(Path(__file__).resolve().parent.parent /
                                "data" / "deck_queue.json")))
_LOCK = threading.Lock()

LIVE, QUEUED = "live", "queued"
#: AGENT   -- a real MCP tool on the running agent (read-only allowlist)
#: GATED   -- a real MCP tool that changes state: needs an approved gate
AGENT, GATED = "agent", "gated"

#: id, label, what it does, and whether this process can honestly run it.
INTENTS = (
    {"id": "status", "label": "STATUS PULL", "mode": LIVE,
     "says": "GBrain / Hermes / MCP / RAM right now"},
    {"id": "memory_scan", "label": "MEMORY SCAN", "mode": LIVE,
     "says": "how many facts the shared brain holds"},
    {"id": "vitals", "label": "VITALS", "mode": LIVE,
     "says": "RAM, CPU, processes"},
    {"id": "harness", "label": "HARNESS", "mode": LIVE,
     "says": "which browser / computer engine is ready"},
    {"id": "gates", "label": "GATE REVIEW", "mode": LIVE,
     "says": "what is waiting for your approval"},
    {"id": "os_map", "label": "OS MAP", "mode": LIVE,
     "says": "tools, skills and domains on this machine"},
    {"id": "am_report", "label": "AM REPORT", "mode": AGENT,
     "tool": "objective_list", "args": {},
     "says": "what the agent is working on (live tool call)"},
    {"id": "news_brief", "label": "WORLD BRIEF", "mode": AGENT,
     "tool": "get_world_news", "args": {},
     "says": "world news from the agent (live tool call)"},
    {"id": "plan_today", "label": "PLAN TODAY", "mode": GATED,
     "tool": "objective_start",
     "args": {"request": "Plan my top three priorities for today and tell me."},
     "says": "starts a real objective -- asks you to approve first"},
    {"id": "vault_sync", "label": "VAULT SYNC", "mode": LIVE,
     "says": "re-render the memory as readable markdown in vault/"},
)

_BY_ID = {i["id"]: i for i in INTENTS}


def _now():
    return datetime.now(timezone.utc).isoformat()


def _read_queue():
    try:
        return json.loads(QUEUE_PATH.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return []


def _write_queue(items):
    try:
        QUEUE_PATH.parent.mkdir(parents=True, exist_ok=True)
        QUEUE_PATH.write_text(json.dumps(items[-200:], indent=2),
                              encoding="utf-8")
    except OSError:
        pass


def queue():
    with _LOCK:
        return _read_queue()


def enqueue(intent_id, note=""):
    with _LOCK:
        items = _read_queue()
        items.append({"intent": intent_id, "state": "PENDING",
                      "note": note, "at": _now()})
        _write_queue(items)
        return items[-1]


def _run_live(intent_id):
    from friday import ui_server as U
    if intent_id == "status":
        s = U.build_state()
        c = s["connections"]
        return ("GBrain %s, Hermes %s, MCP %s. %s tools, RAM %s%%" % (
            c["gbrain"]["status"], c["hermes"]["status"],
            c["mcp_server"]["status"], s["mcp"].get("total", "?"),
            s["system"].get("ram_percent", "?")))
    if intent_id == "memory_scan":
        m = U.memory_snapshot()
        return "%s brain facts, %s local facts" % (
            m["gbrain"].get("count", 0), m["local"].get("count", 0))
    if intent_id == "vitals":
        sy = U.build_state()["system"]
        return "RAM %s%%, CPU %s%%, %s processes" % (
            sy.get("ram_percent"), sy.get("cpu_percent"), sy.get("processes"))
    if intent_id == "harness":
        from friday import harness as H
        sel = H.select("browse")
        ready = [e["id"] for e in H.availability() if e["status"] == "ready"]
        return "ready: %s. browse -> %s" % (", ".join(ready) or "none",
                                             sel.get("chosen") or "none")
    if intent_id == "gates":
        from friday import ui_browser as B
        n = len(B.pending_gates())
        return "%d browser gate(s) pending" % n
    if intent_id == "vault_sync":
        from friday import vault
        r = vault.sync()
        note = (", %d hand-edited kept" % len(r["skipped_edited"])
                if r["skipped_edited"] else "")
        return "%d page(s) written, %d unchanged%s" % (
            len(r["written"]), r["unchanged"], note)
    if intent_id == "os_map":
        from friday import os_map
        st = os_map.build()["status"]
        return "%s tools, %s skills, %s groups" % (
            st["tools"], st["skills"], st["groups"])
    return "unknown intent"


def run(intent_id):
    item = _BY_ID.get(intent_id)
    if item is None:
        return {"ok": False, "error": "unknown intent %r" % intent_id}
    if item["mode"] in (AGENT, GATED):
        from friday import control
        if not control.reachable():
            enqueue(intent_id, "MCP server down when requested")
            return {"ok": True, "mode": QUEUED, "intent": intent_id,
                    "label": item["label"],
                    "result": "the agent is not running (MCP down) -- queued, "
                              "not executed"}
        if item["mode"] == GATED:
            g = control.request_call(item["tool"], item.get("args"),
                                     "Run %s on the agent?" % item["label"])
            return {"ok": True, "mode": GATED, "intent": intent_id,
                    "label": item["label"], "gate": g,
                    "result": "approve the gate to run this"}
        out = control.call(item["tool"], item.get("args"))
        text = (out.get("text") or out.get("error") or "")
        return {"ok": out.get("ok", False), "mode": AGENT, "intent": intent_id,
                "label": item["label"], "result": text[:400]}
    if item["mode"] == LIVE:
        try:
            return {"ok": True, "mode": LIVE, "intent": intent_id,
                    "label": item["label"], "result": _run_live(intent_id)}
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "mode": LIVE, "intent": intent_id,
                    "error": str(exc)[:160]}
    enqueue(intent_id, item["says"])
    return {"ok": True, "mode": QUEUED, "intent": intent_id,
            "label": item["label"],
            "result": "queued -- the agent runs this; it has not run yet"}


def schedule(limit=8):
    """Today's real work: running/pending objective tasks + automations."""
    from friday import ui_server as U
    conn = U._connect()
    try:
        tasks = U._rows(conn,
                        "SELECT capability, status, created_at FROM objective_tasks "
                        "WHERE status NOT IN ('done','completed','verified',"
                        "'cancelled','failed') ORDER BY created_at DESC LIMIT ?",
                        (limit,))
        autos = U._rows(conn, "SELECT name, trigger, enabled FROM automations "
                              "ORDER BY created_at DESC LIMIT 5")
    finally:
        if conn is not None:
            conn.close()
    rows = [{"at": (t.get("created_at") or "")[11:16],
             "what": t.get("capability") or "task",
             "state": t.get("status") or ""} for t in tasks]
    for a in autos:
        rows.append({"at": "auto", "what": a.get("name") or "automation",
                     "state": "enabled" if a.get("enabled") else "off"})
    return rows


def state():
    q = queue()
    pending = [i for i in q if i.get("state") == "PENDING"]
    return {"intents": list(INTENTS), "queued": len(pending),
            "queue": pending[-6:], "schedule": schedule()}
