# Owner rulings 2026-10-04: CTX-002 (model key, holdout, live wiring)

Answers to the three owner decisions CTX-002's run of 2026-10-04 (wave 74) blocked on.

1. Model key: "we will try to use claude code or codex and antigravity instead of api key if user wants then u can give"
   - the stage-2 chooser runs through a coding-agent CLI (Claude Code, Codex, Antigravity) rather than a raw API key;
   a key may come later if the person chooses to give one.
2. Holdout: "Yes, once, after dev is fixed."
3. Live wiring: option 1 (behind a flag, off by default) plus shadow mode - tool_router_mode legacy | shadow | staged,
   default legacy; shadow runs the staged router beside legacy and logs, never affecting execution; stage 2 in shadow
   only on a bounded evaluation sample or when an evaluator is configured; promote to staged only when held-out and
   shadow evidence meet the gates (Success@1 >= 0.90, Success@3 >= 0.97, ambiguous-intent handling, acceptable p95
   routing latency and paid chooser-call rate, no golden-journey regression); automatic fallback to legacy on error or
   timeout; track the chooser invocation rate - a chooser called on most requests fails the efficiency goal.
