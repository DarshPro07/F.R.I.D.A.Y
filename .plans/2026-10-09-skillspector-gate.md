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

### Feature 2: contract verified, import seam
- **Service:** fabric
- **DOD:**
  - [x] default command and parser match the v2.12.0 contract (below)
  - [x] `scan_import` / `scan_admits` / `tree_digest`: the seam for repos, MCP servers, packages
  - [ ] SkillSpector installed and run for real (needs Python >= 3.12; build venv is 3.11)
  - [ ] SkillSpector in `third_party/UPSTREAM_LOCK.json` + a fabric adapter (needs a clone; see notes)
  - [ ] an importer that calls `scan_import` (none exists in this tree; CAP-MKT-001/004)
  - [ ] `friday/skills/lifecycle.py` `SkillLifecycle.activate` is not gated (owner decision, see notes)

## Technical notes
- Findings live in the existing `verification` JSON column under `security_scan`
  (scan version, date, source sha, findings, report hash - PRD v7 s47). No schema change.
- Pin (chosen 2026-10-09, the latest tag then; the owner has not signed it off):
  NVIDIA/SkillSpector `v2.12.0` = `c7958a3268d9498644b22edb75d0f051bbc8cbfc`,
  Apache-2.0. Constants in `friday/forge.py`; each stored scan records
  `pinned_release` and `matches_pin`, and a mismatch is recorded, not blocked.
- Contract, read from that tag's README ("Integrating SkillSpector") and
  `src/skillspector/{cli,models}.py` - not from a running binary:
  `skillspector scan <dir> --no-llm --format json` prints a report to stdout;
  exit 0 or 1 (1 = risk_score > 50) carry a report, exit 2 is an error.
  Report: `issues[]` (`id`, `category`, `severity` HIGH..., `location.file`,
  `location.start_line`, `explanation`), `risk_assessment` (`score`,
  `severity`, `recommendation` SAFE|CAUTION|DO_NOT_INSTALL),
  `metadata.skillspector_version`, best-effort `analysis_completeness`.
- Decisions that follow: `--no-llm` (static, no file contents sent to a provider);
  exit codes other than 0/1 are ERROR; `DO_NOT_INSTALL` or HIGH/CRITICAL blocks
  even when no single issue does (the score sums them); a report missing
  `issues` or `risk_assessment` is ERROR; `is_complete: false` is ERROR unless a
  blocking finding already condemns the candidate.
- Data egress: with `--no-llm` the scanner still sends declared dependency names
  to api.osv.dev (falls back to a bundled list offline). The scanner gets
  `scrubbed_env()`, so a build that needs HOME/USERPROFILE would fail closed
  (ERROR) - unverified until the binary runs here.
- Lock/adapter blocker: the lock is derived from clones in
  `third_party/upstream/` (`scripts/upstream_lock.py`); regenerating it without
  the clone would mark entries NOT_CLONED, and adding the gitlink needs git
  commands outside this run. Needs: NEW_UPSTREAM_SET.json entry, clone at the
  pinned SHA, regenerate the lock, update `tests/test_upstream_lock.py`'s
  requested set, then the adapter.
- Open question for the owner (rule 4): `SkillLifecycle.activate` (the other
  skill lifecycle, STAGED -> ACTIVE) never calls SkillSpector. Should built-in
  skills be scanned too, and is a missing scanner allowed to block boot-time
  adoption? Not guessed.
- Missing/failed scanner = fail closed: the candidate stays CANDIDATE and verify raises.
- Static scan PASS is not runtime-safe (PRD s47); it is one layer next to the AST gate.
