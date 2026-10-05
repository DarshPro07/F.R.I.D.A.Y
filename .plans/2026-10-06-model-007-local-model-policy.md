# Plan: MODEL-007 local model policy

**Date:** 2026-10-06
**Status:** In Progress
**Services:** objective-engine (governor-side residency), mcp-tools (none)

## Objective
Default local model artifacts stay under 2 GB, at most one general local model is
resident, vision loads on demand, local context defaults to 4K-8K and is minimised
before model size is raised. Enforced at onboarding (`ExpertPool.register`) and by
the residency manager (`ExpertPool.load`), which already reads the device's memory.

## Features
### Feature 1: `friday/local_model_policy.py` (one owner of the numbers)
- **Small win:** `check_onboarding` refuses a 3.7 GB artifact, accepts it on owner override.
- **DOD:** [ ] cap/override  [ ] context clamp, `fit_context`, `choose_model`  [ ] tests

### Feature 2: `ExpertPool` enforces it
- register() refuses over-cap unless `owner_override`; records the override, shown by
  `Outcome.override` and `status()`.
- load(): a second general model swaps out the first; a vision load evicts a resident
  general model before other LRU victims when memory requires; vision experts are
  unloaded when idle (`reap_idle`, also run from `reconcile`).
- **DOD:** [ ] tests on the 8 GB Machine simulator  [ ] 8 GB vs 16 GB decisions differ

### Feature 3: `hardware_profile.derive_policy` gains `model.default_ctx`
- Derived from the measured room: the largest context in 4096..8192 whose KV cache fits
  beside the model budget; the floor when none does.

## Technical notes
- `MODEL_ARTIFACT_CAP_GB` moves to `local_model_policy.ARTIFACT_CAP_GB`; `hardware_profile`
  keeps the name as an alias (no import cycle, one owner).
- Existing 3 GB / 4 GB test experts register with an owner override in the fixture, they
  are exactly the over-cap case.
- Assumption: no confirmation.py binding for the override yet; `register` has no live
  caller (local_experts is FUTURE in reachability), so the owner-authenticated caller
  passes `owner_override`. Revisit when the loaders are wired.
