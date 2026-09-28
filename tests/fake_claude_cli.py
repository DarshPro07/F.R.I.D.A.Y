"""
A fake Claude Code CLI for tests: records the argv and the full stdin it was
given, then answers with the minimum valid stream-json `cli.py` needs to
parse a run to completion. Modeled on `fake_hermes_cli.py` - same shape, a
different protocol, because the real CLI's is JSON lines on stdout rather
than one JSON blob per subcommand.

If the prompt were ever passed on the command line instead of over stdin,
this script's own argv log is exactly where that regression would show up -
see test_prompt_transport.py.

Log format: one JSON object, written once, to $FAKE_CLAUDE_LOG:
{"argv": [...], "stdin_len": N}. $FAKE_CLAUDE_SESSION_ID sets the session id
the fake `init` event reports (default "fake-session-id");
$FAKE_CLAUDE_EXIT forces a non-zero exit for tests that need a failure.
"""
from __future__ import annotations

import json
import os
import sys


def main() -> int:
    argv = sys.argv[1:]
    prompt = sys.stdin.read()

    log_path = os.environ.get("FAKE_CLAUDE_LOG")
    if log_path:
        with open(log_path, "w", encoding="utf-8") as fh:
            json.dump({"argv": argv, "stdin_len": len(prompt)}, fh)

    exit_code = int(os.environ.get("FAKE_CLAUDE_EXIT", "0") or "0")
    if exit_code:
        sys.stderr.write("fake_claude_cli: forced non-zero exit\n")
        return exit_code

    session_id = os.environ.get("FAKE_CLAUDE_SESSION_ID", "fake-session-id")
    sys.stdout.write(json.dumps({
        "type": "system", "subtype": "init", "session_id": session_id,
    }) + "\n")
    sys.stdout.write(json.dumps({
        "type": "result", "result": "ok", "is_error": False,
        "total_cost_usd": 0.0, "num_turns": 1,
    }) + "\n")
    sys.stdout.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
