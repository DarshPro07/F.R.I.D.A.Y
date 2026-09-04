---
description: "Every fix is the correct, complete solution. No band-aids."
globs: "*"
alwaysApply: true
---

# Rule 9: No Quick Fixes

**When this applies:** every team. Especially load-bearing for QA, security, and platform teams.

**Never start a quick fix.**

- Every fix you add must be the **correct, complete solution** — not a band-aid.
- If you don't know the correct approach, **STOP and ask** (see Rule 4).
- A wrong fix is worse than no fix — it creates technical debt and hides the real problem.

## Before implementing any fix, verify you understand:

1. The **root cause**, not just the symptom.
2. The **correct solution**, not a workaround.
3. The **impact** on other parts of the system.

## Anti-patterns

- "I'll fix it properly later." (You won't.)
- Catching an exception and swallowing it. (Now the symptom is invisible too.)
- Hardcoding a value to make a test pass. (The test now proves nothing.)
- Disabling a check / lint / type-error to ship. (The check existed for a reason.)
- Adding a `try/except: pass` block to "make the error go away." (See above.)

## The exception

Hot-fixes for live incidents are allowed to be incomplete — but file an immediate follow-up issue with a real fix, and the hot-fix commit message says so explicitly: "TEMPORARY — see issue #N for proper fix."
