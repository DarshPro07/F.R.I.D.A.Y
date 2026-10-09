"""
A fake Hermes TUI gateway, for testing the bridge without a model.

Speaks the same wire protocol as `tui_gateway.entry`: newline-delimited
JSON-RPC on stdio, `gateway.ready` on boot, `event` frames for streaming.
Behaviour is scripted through env vars so one binary covers every test:

    FAKE_HERMES_CLARIFY=1   the first prompt.submit triggers clarify.request
                            before completing
    FAKE_HERMES_HANG=1      never answer prompt.submit (for interrupt tests)
    FAKE_HERMES_DIE=1       exit abruptly after the first prompt.submit
                            (for crash-recovery tests)
    FAKE_HERMES_CAPPED=1    message.complete arrives with status "error"
                            and a rate-limit sentence (quota routing tests)
    FAKE_HERMES_ENV_DUMP=p  before gateway.ready, append this process's own
                            HERMES_* environment as one JSON line to path p
                            (so a test can see what the launching supervisor
                            actually put in each process's environment)
    FAKE_HERMES_IGNORE_TOOLSETS=1
                            a gateway that does not honour HERMES_TUI_TOOLSETS:
                            its turn starts Hermes' own read_file and keeps
                            running it until interrupted
    FAKE_HERMES_REPORT_TOOLS=1
                            session.create answers with the tools the session
                            holds (`info.tools`, toolset -> names), as Hermes'
                            own session info does: Hermes' file/terminal
                            tools unless HERMES_TUI_TOOLSETS is honoured
    FAKE_HERMES_CALL_LOG=p  append each RPC method received to path p, one
                            per line (so a test can see what was never sent)
    FAKE_HERMES_REFUSE_INTERRUPT=1
                            session.interrupt answers with a JSON-RPC error
                            for every session, as a gateway that cannot stop
                            a run does

session.interrupt also answers with an error for a session id this gateway
never created (an empty one included), so a test sees a stop that named no
session. That a real Hermes rejects an unknown session is an assumption, not
something measured.

A gateway that honours HERMES_TUI_TOOLSETS has no native file tools, so with
it set the turn reads through Friday's exec bridge (MCP-prefixed) instead.
"""
import json
import os
import sys
import threading
import uuid


def write(obj):
    sys.stdout.write(json.dumps(obj) + "\n")
    sys.stdout.flush()


def event(kind, sid, payload=None):
    params = {"type": kind, "session_id": sid}
    if payload is not None:
        params["payload"] = payload
    write({"jsonrpc": "2.0", "method": "event", "params": params})


def ok(rid, result):
    write({"jsonrpc": "2.0", "id": rid, "result": result})


def fail(rid, code, message):
    write({"jsonrpc": "2.0", "id": rid,
           "error": {"code": code, "message": message}})


def main():
    clarify = os.getenv("FAKE_HERMES_CLARIFY") == "1"
    hang = os.getenv("FAKE_HERMES_HANG") == "1"
    die = os.getenv("FAKE_HERMES_DIE") == "1"
    capped = os.getenv("FAKE_HERMES_CAPPED") == "1"
    env_dump = os.getenv("FAKE_HERMES_ENV_DUMP")
    ignore_toolsets = os.getenv("FAKE_HERMES_IGNORE_TOOLSETS") == "1"
    # Honouring HERMES_TUI_TOOLSETS: the file tools are not registered, the
    # exec bridge's are (what the friday profile registers as friday_exec).
    native = ignore_toolsets or not os.getenv("HERMES_TUI_TOOLSETS")
    read_tool = "read_file" if native else "mcp__friday_exec__bridge_read_file"
    report_tools = os.getenv("FAKE_HERMES_REPORT_TOOLS") == "1"
    call_log = os.getenv("FAKE_HERMES_CALL_LOG")
    refuse_interrupt = os.getenv("FAKE_HERMES_REFUSE_INTERRUPT") == "1"
    held = ({"file": ["read_file", "write_file", "patch", "search_files"],
             "terminal": ["terminal"]} if native
            else {"mcp-friday_exec": ["mcp__friday_exec__bridge_read_file"]})

    if env_dump:
        seen = {k: v for k, v in os.environ.items() if k.startswith("HERMES_")}
        with open(env_dump, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(seen) + "\n")

    event("gateway.ready", "", {"skin": {}})
    sessions = {}

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        frame = json.loads(line)
        rid = frame.get("id")
        method = frame.get("method", "")
        params = frame.get("params") or {}
        sid = params.get("session_id", "")
        if call_log:
            with open(call_log, "a", encoding="utf-8") as fh:
                fh.write(method + "\n")

        if method == "session.create":
            sid = uuid.uuid4().hex[:8]
            sessions[sid] = {"steered": []}
            info = {"model": "fake-model", "provider": "fake"}
            if report_tools:
                info["tools"] = held
            ok(rid, {"session_id": sid, "stored_session_id": f"stored-{sid}",
                     "message_count": 0, "messages": [],
                     "info": info})

        elif method == "prompt.submit":
            ok(rid, {"submitted": True})
            if die:
                os._exit(1)
            if hang:
                continue

            def turn(sid=sid, text=params.get("text", "")):
                event("tool.start", sid, {"name": read_tool,
                                          "args": {"path": "x"}})
                if ignore_toolsets:
                    return                    # still reading, until interrupted
                event("tool.complete", sid, {"name": read_tool})
                if capped:
                    event("message.complete", sid, {
                        "text": "rate limit reached, resets at 23:59",
                        "status": "error"})
                    return
                if clarify:
                    event("clarify.request", sid, {
                        "request_id": "q1",
                        "question": "Which storage engine should this use?",
                        "options": ["sqlite", "postgres"]})
                    # completion happens after clarify.respond arrives
                    return
                event("message.delta", sid, {"text": "working..."})
                event("session.usage", sid, {
                    "input_tokens": 120, "output_tokens": 30,
                    "cache_read_tokens": 0})
                event("message.complete", sid,
                      {"text": f"DONE: {text[:40]}", "status": "ok"})

            threading.Thread(target=turn, daemon=True).start()

        elif method == "clarify.respond":
            ok(rid, {"status": "ok"})
            # Real contract: the reply text arrives in params["answer"]
            # (tui_gateway server._respond(rid, params, "answer")).
            answer = params.get("answer", "")
            event("message.complete", sid,
                  {"text": f"ANSWERED-WITH: {answer}", "status": "ok"})

        elif method == "session.steer":
            sessions.setdefault(sid, {}).setdefault("steered", []).append(
                params.get("text", ""))
            ok(rid, {"status": "queued", "text": params.get("text", "")})

        elif method == "session.interrupt":
            if refuse_interrupt:
                fail(rid, 5000, "this gateway cannot stop a session")
            elif sid not in sessions:
                fail(rid, 4001, f"session not found: {sid!r}")
            else:
                ok(rid, {"status": "interrupted"})
                event("message.complete", sid,
                      {"text": "interrupted", "status": "error"})

        elif method == "commands.catalog":
            ok(rid, {"pairs": [["/new", "new session"]]})

        elif method == "session.usage":
            ok(rid, {"input_tokens": 120, "output_tokens": 30})

        else:
            ok(rid, {})


if __name__ == "__main__":
    main()
