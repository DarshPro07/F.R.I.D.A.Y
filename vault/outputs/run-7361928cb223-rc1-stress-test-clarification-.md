---
title: RC1 stress-test clarification — use this as the test contract.

1. Telemetry / l
source: objective_runs + artifacts
stamp: 7f4db75fd846
canonical: GBrain + data/ada.sqlite3 (this file is a projection)
---

# RC1 stress-test clarification — use this as the test contract.

1. Telemetry / l

- run: `RUN-7361928cb223`
- status: **PARTIAL**
- started: 2026-08-27T05:53:23.410496+00:00
- finished: 2026-08-27T05:53:23.773900+00:00

## Summary

{"succeeded": 3, "failed": 2, "skipped": 1, "interrupted": 0, "attempts": 5, "manual_continue_count": 0, "duration_seconds": 0.4, "failures": [{"task_id": "RUN-7361928cb223-t4", "capability": "files_read", "kind": "STRUCTURAL", "reason": "no such file: E:\\friday-tony-stark-demo-main\\friday-docs-architecture-soak-log-md.txt"}, {"task_id": "RUN-7361928cb223-t5", "capability": "hermes_delegate", "kind": "NOT_CONFIGURED", "reason": "hermes_delegate: this capability's implementation lives in its MCP adapter and returns no verifiable result, so a durable task cannot record evidence for it"}]}

<!-- generated-by: friday.vault -->
