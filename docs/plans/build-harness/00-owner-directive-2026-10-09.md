# Owner directive 2026-10-09: amendment A18, the SkillSpector and packaging rulings, no daily cap

Given in the session on 2026-10-09. The owner pasted three design notes (UltraCode repositories and self-acquired
capabilities; Vercel Skills and MiniMind; mytechnotalent/Reverse-Engineering) and ruled on them:

> Add everything as P1

The owner's rulings on the open CAP-MKT-002 and PKG-001 questions were given in the same session. They are recorded
below as decisions, not as quotations.

## How the controller records them

- **No daily spend cap.** The standing entry in day_budget.json from 2026-10-05 stays. The ledger still records
  every paid call. Evolution work gets its own allowance under EVOLAB-002, which is not a day cap.
- **CAP-MKT-002 (SkillSpector) rulings.**
  - Pin SkillSpector v2.12.0 under third_party/upstream at the SHA verified at clone time, record it in
    third_party/UPSTREAM_LOCK.json, and run it in its own isolated Python 3.12 environment.
  - Scan every skill source: external, built-in, user and generated skills. Built-in skills get a one-time
    baseline audit.
  - For a new or changed skill, the gate fails closed when a scan is missing, failed, incomplete or over the
    threshold.
  - During a scanner outage, skills that were already verified and are unchanged stay usable.
  - Cache results by content hash, scanner version and policy version.
  - Stand-in scanners are for unit tests only.
  - No model judgment can bypass the gate.
- **PKG-001 rulings.**
  - The packaging proof runs in a real Windows Sandbox or a clean VM. A simulation is preliminary evidence only.
  - It runs when memory admits it.
  - It produces the evidence list.
  - EXE-06 and EXE-07 stay offline or mock-only until the owner approves live LiveKit.
  - No credentials are ever embedded in the package.
- **A18 = everything from the three notes, at P1.** It is recorded as docs/PRD_V7.md section 112, with 42 rows:
  INTAKE-001..007, AGENT-001..004, ACC-001..006, UCW-001..003/005/006/008..010, EVOLAB-001..004,
  SKMKT-001..004, LAB-001..004 and BIN-001..005. Rows that duplicated each other were merged (LAB-005 into
  ACC-006, UCW-004 into INTAKE-005, UCW-007 into EVOLAB-004). Ideas that already have rows are cited, not
  restated. Build order follows note 1 section 7: intake, Agent Composer, auto-build, Command Center, then
  controlled self-evolution. A18 runs behind the P0 burn-down.
- **Where the notes live.** The three notes are kept verbatim in
  docs/plans/a18-capability-agents/design-notes.md. Every A18 row cites the note and section it came from.
