---
description: Pick a Claude, Codex or OpenCode model and continue this session on it
argument-hint: [number or route]
allowed-tools: Bash(.venv-build/Scripts/python.exe scripts/session_handoff.py:*)
---

Run `!.venv-build/Scripts/python.exe scripts/session_handoff.py models` and show the table.

When `$ARGUMENTS` is given, run `!.venv-build/Scripts/python.exe scripts/session_handoff.py use $ARGUMENTS` and report what it started.
