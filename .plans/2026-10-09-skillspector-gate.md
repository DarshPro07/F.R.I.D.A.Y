# Plan: SkillSpector pre-activation gate (CAP-MKT-002)

**Date:** 2026-10-09
**Status:** In Progress
**Services:** fabric (capability forge)

## Objective
Every forged candidate is scanned by the NVIDIA SkillSpector CLI before it can
be verified, registered or enabled. Findings are stored on the candidate. A
high-severity finding (prompt injection, exfiltration, privilege escalation,
tool/memory poisoning, dangerous code, vulnerable dependency) rejects it.

## Features
### Feature 1: scanner gate in `friday/forge.py`
- **Service:** fabric
- **Small win:** `Registry.verify` runs the scan first; BLOCKED -> REJECTED.
- **DOD:**
  - [x] `run_skillspector` (CLI wrapper, fail closed) + `ScanReport`
  - [x] `Registry.scan`; `verify` calls it; register/enable refuse an unscanned or stale-scanned candidate
  - [x] tests: planted malicious skill rejected, clean passes, scanner absent/garbled stays CANDIDATE

## Technical notes
- Findings live in the existing `verification` JSON column under `security_scan`
  (scan version, date, source sha, findings, report hash - PRD v7 s47). No schema change.
- Assumption (revisit if wrong): the SkillSpector CLI is not pinned or installed
  here and its flags are unknown. The command is `FRIDAY_SKILLSPECTOR_CMD` (JSON
  list, `{target}` placeholder); default `skillspector scan --format json {target}`.
  Output parsing is strict: anything not recognisably "findings list" is ERROR,
  never CLEAN.
- Missing/failed scanner = fail closed: the candidate stays CANDIDATE and verify raises.
- Static scan PASS is not runtime-safe (PRD s47); it is one layer next to the AST gate.
