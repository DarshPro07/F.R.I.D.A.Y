# Owner directive 2026-10-08 (e): answers to the unpark triage

Asked in the session on 2026-10-08 after the triage of the 55 parked requirements; the owner's choices, verbatim:

1. Six rows meet their acceptance text with passing tests (CUA-001, EPI-001, FAIL-001, MODEL-003, ONB-001,
   JIT-001); the workers found extra scope beyond it. Close them VERIFIED and split the extras into new rows?
   > **Keep extras in scope** - the six stay PARTIAL until the extra work is built inside each row.
2. CTX-002: download the pinned embedder and reranker weights (hash-verified) so the benchmark can run?
   > **Download pinned weights (Recommended)** - done 2026-10-08: three pins sha256-verified into F:/F.R.I.D.A.Y/cache/models.
3. CODE-001 live A/B size?
   > **Full: 30 runs, ~$7.50+ (Recommended)** - 5 tasks x 2 arms x 3 repeats, run by the controller.
4. INTENT-002: 10% ceilings for unnecessary and missed asks, a stricter already-answered check, close as
   PARTIAL-with-evidence until a live run measures the real classifier?
   > **Accept all three (Recommended)**

Assumed by the controller (the owner may reverse it): a contradicted claim blocks a promotion decision
(EPI-001's open policy question) - the safe default.
