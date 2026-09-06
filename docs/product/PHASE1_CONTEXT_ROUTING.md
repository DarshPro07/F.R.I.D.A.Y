# Phase 1 — Context + Routing Intelligence

Branch `product/phase1-context`, worktree `D:/friday-product-phase1`, forked
from `030795f`. Track B of PRD v5.0 §0: **nothing here merges into the release
candidate until A-051's result is preserved and this work passes its own
gates.**

## Ground rules for this worktree

- The soak candidate at `E:/friday-tony-stark-demo-main` is frozen while
  A-051 attempt 3 runs. Do not edit it, do not run its suite, do not touch
  `data/ada.sqlite3` or the live Friday processes (PRD §76).
- `cd D:/friday-product-phase1` before every command. The verify venv has an
  editable `.pth` pointing at E:, and cwd is what makes `import friday`
  resolve here instead. Verified: `friday.__file__` →
  `D:\friday-product-phase1\friday\__init__.py`.
- Use `E:/friday-tony-stark-demo-main/.venv-verify/Scripts/python.exe`
  (3.11.15, no pip). Do not create a second venv — nothing new is needed.
- pytest temp goes to `D:/friday-test-tmp` (`--basetemp`), never C:.

## What the PRD asks for vs what the tree already has

Checked before writing any code, because AGENTS.md forbids a second registry
or a duplicate router and half of Wave 1 turned out to exist already.

| FR | PRD asks for | Already in the tree | Real gap |
|----|--------------|---------------------|----------|
| FR-101 | Context Budget Manager | `model_gateway.TokenBudget`, `budget_for(task_class)` per class; `memory_stack.aggregate()` enforces a token budget with per-tier telemetry and drops the least important tier first | Budget is per *task class*, not per *objective*; no checkpoint/compact when the live context nears its threshold |
| FR-102 | Context provenance (source, scope, timestamp, confidence, freshness, trust) | Rows carry created_at, project scope, supersede state; `memory_promotion` gates what becomes durable | No provenance struct travels *with* a fragment into a prompt; trust_level is not represented at all |
| FR-103 | Retrieval router: filter / SQL / full-doc / semantic / code / connector / web | Every tier is lexical overlap scoring (`preferences`, `specs`, `rules`, `relations`, `episodes`, `contacts`, `hermes_outcomes`) | **No strategy selection exists.** A count/sum question and a conceptual question take the identical path. This is the genuine Wave 1 gap |
| FR-104 | Task complexity T0–T6 | `TASK_CLASSES = (TRIVIAL, SIMPLE, STANDARD, COMPLEX, LONG_RUNNING, CRITICAL)` — six classes, already routing tier + budget | Naming differs; T0 "deterministic, no model" has no equivalent. Map, don't rebuild |
| FR-105 | Width/depth planner | `planner.py`, `planner_model.py`, `executor_router.py`, `capability_router.py` | Need to read these before claiming a gap |
| FR-106 | Model fitness router | `provider_health` route-keyed verdicts (6314dca), privacy via `TransportModel` + endpoint-proven locality (f81c2d4) | Ranking is not fitness-based: no "models known to pass this task class" signal |

## Sequence

1. **FR-104 mapping** — smallest, and everything else routes through it. Map
   T0–T6 onto `TASK_CLASSES` rather than introducing a parallel taxonomy;
   add the deterministic T0 path (EAD "automate before delegate", §2.2).
2. **FR-103 retrieval router** — the real gap. A classifier in front of
   `memory_stack`, with SQL/filter paths for aggregation questions so a
   "how many" question stops being answered from lexically-scored chunks.
3. **FR-101 objective-scoped budget** — extend the existing budget rather
   than replacing it; add the checkpoint/compact trigger.
4. **FR-102 provenance** — attach to fragments once the retrieval router is
   the thing producing them.
5. **FR-106 fitness** — needs a task-class → model-eval signal that does not
   exist yet; last, and probably its own phase.

## Phase 1 exit gate (PRD §77)

All must be *proven*, not asserted:

- context budget works — a bundle over budget drops the right tier, measured
- retrieval routing works — aggregation questions do not use lexical chunks
- local-only isolation works — the f81c2d4 regression test still passes
- model fitness routing works
- no cross-provider model leak
- no regression baseline failure — the full suite, on a frozen tree

## Standing evidence rules (carried from Track A)

- Red-then-green for every fix: plant the defect, watch the test fail,
  restore, watch it pass. A guard that has never failed proves nothing.
- A suite run that overlaps a plant/restore cycle is void.
- Full-suite gates run on a checksum-frozen tree (`md5sum` before,
  `md5sum -c` after).
- No "green" claim written anywhere before the run that earns it.
