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


def main():
    clarify = os.getenv("FAKE_HERMES_CLARIFY") == "1"
    hang = os.getenv("FAKE_HERMES_HANG") == "1"
    die = os.getenv("FAKE_HERMES_DIE") == "1"
    capped = os.getenv("FAKE_HERMES_CAPPED") == "1"

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

        if method == "session.create":
            sid = uuid.uuid4().hex[:8]
            sessions[sid] = {"steered": []}
            ok(rid, {"session_id": sid, "stored_session_id": f"stored-{sid}",
                     "message_count": 0, "messages": [],
                     "info": {"model": "fake-model", "provider": "fake"}})

        elif method == "prompt.submit":
            ok(rid, {"submitted": True})
            if die:
                os._exit(1)
            if hang:
                continue

            def turn(sid=sid, text=params.get("text", "")):
                event("tool.start", sid, {"name": "read_file",
                                          "args": {"path": "x"}})
                event("tool.complete", sid, {"name": "read_file"})
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
