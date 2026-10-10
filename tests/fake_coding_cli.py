"""
A coding CLI, faked (FAB-002): codex (JSONL events) or gemini (one JSON
document). The tests put `<kind> <mode> <write> <log>` in front of the real
arguments, because a worker's environment is an allowlist and carries nothing
else across.

    kind   codex | gemini
    mode   ok | auth | crash | hang | signed_out
    write  a relative path the "agent" creates in its working directory ("-" none)
    log    where to record what the CLI was asked and where it ran
"""
import json
import sys
import time
from pathlib import Path

kind, mode, write, log, *rest = sys.argv[1:]

if rest[:2] == ["login", "status"]:
    if mode == "signed_out":
        print("Not logged in")
        sys.exit(1)
    print("Logged in using ChatGPT")
    sys.exit(0)

prompt = sys.stdin.read()
Path(log).write_text(json.dumps({"prompt": prompt, "cwd": str(Path.cwd()),
                                 "args": rest}), encoding="utf-8")

if mode == "hang":
    time.sleep(60)
if mode == "crash":
    print("segfault", file=sys.stderr)
    sys.exit(3)
if mode == "auth":
    if kind == "codex":
        print(json.dumps({"type": "turn.failed",
                          "error": {"message": "401 Unauthorized: please log in again"}}))
    else:
        print(json.dumps({"error": {"type": "AuthError",
                                    "message": "401 Unauthorized: sign in required"}}))
    sys.exit(1)

if write != "-":
    target = Path(write)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("cache = {}\n", encoding="utf-8")

if kind == "codex":
    for event in (
            {"type": "thread.started", "thread_id": "t-1"},
            {"type": "item.completed", "item": {"type": "command_execution"}},
            {"type": "item.completed", "item": {"type": "agent_message",
                                                "text": "Added the cache."}},
            {"type": "turn.completed",
             "usage": {"input_tokens": 120, "output_tokens": 30,
                       "cached_input_tokens": 40, "reasoning_output_tokens": 5}}):
        print(json.dumps(event))
else:
    print("Loaded cached credentials.")
    print(json.dumps({"response": "Added the cache.", "stats": {"models": {
        "gemini-2.5-pro": {"tokens": {"prompt": 200, "candidates": 50,
                                      "cached": 10, "thoughts": 7}}}}}))
