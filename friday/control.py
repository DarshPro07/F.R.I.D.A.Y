"""
friday/control.py -- the control channel: the UI talking to the real agent.

Until now the UI could only observe. This connects it to the running MCP
server (server.py on :8000/sse) as an ordinary MCP CLIENT, so the deck and the
voice path can invoke Friday's real tools instead of queueing forever.

Two rules make this safe rather than a remote-execution hole:

  READ_ONLY  -- an allowlist of tools that only look (news, status, recall,
                list). A deck button may run these directly.
  everything else -- including objective_start, which sets autonomous work
                going -- must be approved through friday.confirmation first,
                bound to the exact tool and arguments. The UI raises a gate
                card; nothing runs until a human approves that exact call.

Friday stays the single control layer: this does not plan, decide or
orchestrate. It carries one approved call to the agent and brings the answer
back. If the server is down, every path reports that plainly and nothing is
silently dropped.
"""
from __future__ import annotations

import asyncio
import os
import socket

MCP_HOST = os.getenv("ADA_MCP_HOST", "127.0.0.1")
MCP_PORT = int(os.getenv("ADA_MCP_PORT", "8000"))
SSE_URL = os.getenv("FRIDAY_MCP_SSE", "http://%s:%s/sse" % (MCP_HOST, MCP_PORT))
TIMEOUT = float(os.getenv("FRIDAY_MCP_TIMEOUT", "45"))

#: Tools a button may run with no further approval: they only read.
READ_ONLY = frozenset({
    "get_world_news", "get_world_finance_news", "web_news",
    "objective_status", "objective_list", "objective_history",
    "memory_recall", "memory_search", "brain_recall",
    "profile_get", "profile_explain",
    "capability_providers", "capability_health", "capability_processes",
    "system_info", "hermes_status",
})


def reachable():
    """Cheap: is anything listening on the MCP port?"""
    try:
        with socket.create_connection((MCP_HOST, MCP_PORT), timeout=0.6):
            return True
    except OSError:
        return False


def _text(result):
    """Flatten an MCP CallToolResult into readable text."""
    parts = []
    for block in getattr(result, "content", None) or []:
        t = getattr(block, "text", None)
        if t:
            parts.append(t)
    if not parts:
        data = getattr(result, "structuredContent", None)
        if data:
            parts.append(str(data))
    return "\n".join(parts).strip()


async def _session(fn):
    from mcp import ClientSession
    from mcp.client.sse import sse_client
    async with sse_client(SSE_URL, timeout=TIMEOUT) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            return await fn(session)


async def list_tools_async():
    async def go(session):
        resp = await session.list_tools()
        return [t.name for t in resp.tools]
    return await _session(go)


async def call_async(tool, arguments=None):
    async def go(session):
        res = await session.call_tool(tool, arguments or {})
        return {"ok": not getattr(res, "isError", False),
                "tool": tool, "text": _text(res)}
    return await _session(go)


def _run(coro):
    """Run an async client call from a worker thread (no loop running here)."""
    try:
        return asyncio.run(asyncio.wait_for(coro, TIMEOUT))
    except asyncio.TimeoutError:
        return {"ok": False, "error": "the MCP server did not answer in %ss"
                                      % int(TIMEOUT)}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": "%s: %s" % (type(exc).__name__,
                                                  str(exc)[:200])}


def status():
    up = reachable()
    out = {"reachable": up, "url": SSE_URL, "tools": 0}
    if up:
        got = _run(list_tools_async())
        if isinstance(got, list):
            out["tools"] = len(got)
            out["sample"] = got[:8]
        elif isinstance(got, dict) and got.get("error"):
            out["error"] = got["error"]
    return out


def call(tool, arguments=None):
    """Invoke a tool. READ_ONLY only -- writes must come through approve()."""
    if not reachable():
        return {"ok": False, "error": "the MCP server is not running on %s:%s"
                                      % (MCP_HOST, MCP_PORT)}
    if tool not in READ_ONLY:
        return {"ok": False, "needs_approval": True, "tool": tool,
                "error": "%r changes state; it needs an approved gate" % tool}
    return _run(call_async(tool, arguments))


def call_approved(tool, arguments=None):
    """Invoke a tool whose gate has already been approved and consumed.

    Callers MUST have consumed a friday.confirmation bound to this exact tool
    and arguments. There is deliberately no path from a model or a page to
    here without that step.
    """
    if not reachable():
        return {"ok": False, "error": "the MCP server is not running on %s:%s"
                                      % (MCP_HOST, MCP_PORT)}
    return _run(call_async(tool, arguments))


# --------------------------------------------------------------------------
# gates for state-changing calls
# --------------------------------------------------------------------------
# A tool that changes state needs a human yes bound to THAT tool and THOSE
# arguments. Same mechanism the browser actions use (friday.confirmation):
# one action, one use, one moment. The Book is per-process, and this process
# is the one that makes the call, which is exactly the property it requires.
from friday import confirmation as _C  # noqa: E402

_book = _C.Book()


def request_call(tool, arguments=None, question=""):
    """Ask permission to run a state-changing tool. Returns a gate."""
    arguments = arguments or {}
    conf = _book.ask(run_id="ui-control", action="mcp.%s" % tool,
                     target=tool, arguments=arguments,
                     question=question or "Run %s on the agent?" % tool,
                     seconds=120.0)
    return {"gated": True, **conf.to_dict()}


def pending():
    now = _C._now()
    return [c.to_dict() for c in list(_book.pending.values())
            if c.state == _C.PENDING and not c.expired(now)]


def approve(nonce):
    """Approve + consume the gate, then make exactly that call."""
    v = _book.approve(nonce)
    if not v.ok:
        return {"ok": False, "reason": v.reason}
    conf = v.confirmation
    cv = _book.consume(nonce, run_id=conf.run_id, action=conf.action,
                       target=conf.target, arguments=conf.arguments)
    if not cv.ok:
        return {"ok": False, "reason": cv.reason}
    out = call_approved(conf.target, conf.arguments)
    out["performed"] = conf.action
    return out


def reject(nonce, reason=""):
    _book.refuse(nonce)
    return {"ok": True, "rejected": nonce, "reason": reason}
