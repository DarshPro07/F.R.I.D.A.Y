# ADR-002 — Quota-aware routing: detect, cool down, switch, say so

**Date:** 2026-09-04 · **Status:** accepted (owner: "auto-switch and say so")

## Context
Providers cap usage (Claude weekly, GPT 5-hour, OpenCode daily). Today a capped provider is
classified TRANSIENT and retried after a backoff, burning the attempt budget and the owner's
time. Hermes 0.20.6 ships a native `fallback` chain; Friday's `execution_economics` resolves one
model per tier.

## Decision
1. `provider_diagnostics` recognises a cap (429 with limit/quota wording, "resets at", "weekly",
   "daily", "5-hour") as kind `CAPPED` with a parsed or defaulted `reset_at`.
2. `provider_cooldowns` remembers `(provider, model) → until` durably (store-backed); a cap is
   never retried on the same provider inside its window.
3. `execution_economics.candidates(tier)` is an ordered list (profile `routing.tiers`, then the
   Hermes `fallback` chain, then the provider cache); `plan_delegation` skips cooled candidates
   and records `switched_from` + `route_reason`.
4. The bridge records the effective route on the work run; the progress digest speaks the
   switch once: "Claude is capped until 14:00, sir; GPT-5.x has this job."
5. Hermes's own fallback config stays aligned (the same chain), so a switch inside Hermes and a
   switch by Friday describe the same order.

## Consequences
A capped provider costs one failed attempt, not the attempt budget. The owner always hears which
model did the work and why. When every candidate is cooled, the job waits for the earliest reset
and says so, instead of failing.
