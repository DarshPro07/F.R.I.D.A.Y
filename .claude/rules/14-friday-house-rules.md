---
description: "Friday-repo house rules that mirror AGENTS.md — the non-negotiables every generated agent must follow."
globs: "*"
alwaysApply: true
---

# Rule 14: Friday House Rules

**When this applies:** every agent in this team, every task.

- Never touch `data/ada.sqlite3`.
- Tests run in `.venv-verify`, never the live `.venv`.
- Agents never commit (the orchestrator asks the owner).
- Hermes is the execution engine, Friday the manager.
- The capability fabric is the only registry for third-party code.
- Every upstream is pinned in `third_party/UPSTREAM_LOCK.json`.
- Reports are <= 250 words with file:line evidence.

Source of truth: `AGENTS.md` at the repo root. If this file and `AGENTS.md`
ever disagree, `AGENTS.md` wins — update this file to match, don't improvise
around the mismatch.
