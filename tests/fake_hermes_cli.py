"""
A fake `hermes` executable for tests: records every argv it was called
with and answers canned JSON keyed by the subcommand. Point `HERMES_EXE`
at this script's companion `.bat`/shim, or invoke it directly with the
same interpreter as the test process.

Log format: one JSON line per call, appended to $FAKE_HERMES_LOG.
Answers: read from $FAKE_HERMES_ANSWERS (a JSON file: {"profile list":
{...}, "kanban create": {...}, ...}, matched by the first two argv
tokens joined with a space; falls back to {} meaning empty stdout).
"""
from __future__ import annotations

import json
import os
import sys


def main() -> int:
    argv = sys.argv[1:]
    log_path = os.environ.get("FAKE_HERMES_LOG")
    if log_path:
        with open(log_path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(argv) + "\n")

    answers_path = os.environ.get("FAKE_HERMES_ANSWERS")
    answers = {}
    if answers_path and os.path.exists(answers_path):
        with open(answers_path, encoding="utf-8") as fh:
            answers = json.load(fh)

    key = " ".join(argv[:2])
    answer = answers.get(key, {})
    if isinstance(answer, dict) and answer.get("__exit__"):
        sys.stderr.write(answer.get("__stderr__", ""))
        return int(answer["__exit__"])
    sys.stdout.write(json.dumps(answer))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
