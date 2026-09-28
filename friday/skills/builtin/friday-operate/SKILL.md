---
name: friday-operate
description: Start, stop, diagnose and update the packaged FRIDAY.exe, and use its skills console, without touching the owner's state.
version: 1.0.0
author: friday
license: MIT
platforms: [windows]
metadata:
  hermes:
    tags: [friday, operate, exe, doctor, skills, console]
    related_skills: []
  friday:
    scope: global
    task: operate-friday
    publish: local
    depends_on:
      - pkg:friday-tony-stark-demo
    tests:
      - scripts/check.py
    triggers:
      - start friday
      - stop friday
      - run the friday doctor
      - where does friday keep its data
      - friday skills console
    never_for:
      - build the exe
      - book a table
---

# Operate FRIDAY.exe

## When to use

When asked to start, stop, restart, diagnose or update the packaged
FRIDAY, to find where its state lives, or to manage its skills from the
console.

## Quick reference

- `FRIDAY.exe` starts the MCP server (:8000), the control room (:8770) and
  the voice worker; Ctrl+C stops what it started.
- `FRIDAY.exe --stop` stops every process running from that executable.
- `FRIDAY.exe --status` shows the two ports and the processes.
- `FRIDAY.exe --doctor` exits 0 PASS, 1 DEGRADED, 2 FAIL; it never prints a
  secret value.
- `FRIDAY.exe --paths` prints every resolved location as JSON.
- `FRIDAY-Debug.exe` is the same program with DEBUG logging and fault
  handlers on; `FRIDAY_DEBUG=1` does the same for `FRIDAY.exe`.
- State: `%LOCALAPPDATA%\FRIDAY` (`config\.env`, `data\`, `logs\`,
  `vault\`, `skills\`); `FRIDAY_HOME` overrides it; a `PORTABLE_MODE` file
  beside the executable keeps state beside it instead.
- Skills console: `FRIDAY.exe -m friday.skills.cli doctor|sync|list|lint|activate|revalidate|rollback|publish|select|hints|propose|rescore|facts|hermes-link`.

## Procedure

1. Before anything else run `FRIDAY.exe --doctor` and read the FAIL and
   DEGRADED lines; a missing credential is named by variable, never by
   value.
2. To start, run `FRIDAY.exe` (add `--no-browser` to keep the control room
   from opening a window, `--no-agent` when there are no LiveKit
   credentials yet).
3. To stop, run `FRIDAY.exe --stop`, then `FRIDAY.exe --status` to confirm
   both ports are free and no processes remain.
4. To find or move state, run `FRIDAY.exe --paths`; edit
   `config\.env` under `state_root` for credentials; never edit anything
   under `_internal`.
5. To update the application, replace the `FRIDAY` folder (or unpack the
   new zip over it); state is outside it and survives; the first start
   migrates anything an older build left under `_internal`.
6. To work with skills, run `FRIDAY.exe -m friday.skills.cli sync` after
   adding a skill directory, `activate NAME` to lint, test and activate it,
   `revalidate` after code changes, `doctor` for the summary.

## Pitfalls

- A start that dies silently: use `FRIDAY-Debug.exe`, then read
  `logs\server.log`, `ui.log`, `agent.log` under the state root.
- `--stop` only stops processes started from the same executable file;
  `FRIDAY-Debug.exe --stop` does not stop a `FRIDAY.exe` stack.
- The voice worker cannot register while `LIVEKIT_URL`, `LIVEKIT_API_KEY`
  and `LIVEKIT_API_SECRET` still hold the `.env.example` placeholders; the
  doctor says which are set, the agent log shows a 401 from LiveKit.

## Verification

- `FRIDAY.exe --doctor` exits 0 or 1, never 2, after a fix.
- `FRIDAY.exe --status` after `--stop` reports no processes and free ports.
- `scripts/check.py` (this skill's test) prints the running build's
  version and exits 0.
