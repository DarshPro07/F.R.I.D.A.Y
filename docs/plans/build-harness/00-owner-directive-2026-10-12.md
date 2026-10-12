# Owner directive 2026-10-12: provider keys and model routers in FRIDAY, and /model in Claude Code

Given in the session on 2026-10-12.

## The rulings (verbatim)

> Bro also i want that in the env option for choosiing jarvis or firday api key like claude chatgpt gemini openrouter
> and create ther own api key router if they want ok there will be ui in friday code ok database tpye ok there we can
> create new api key router and connect api keys without openining env and choose the best model from the connectors ok

> bro i want that when i do /model in claude it should show opencdoe free model and codex models too ok and continue
> ih 94 % of claude subscription left

> Continue with the left task and do nt stop until the full prd is built full autonomosuly loop engeenired ... bro do
> the task fast by runnign agnets and otehr things bro the task is too slow ..... i want everything to be done fast
> like in subagents and other use ultracode fully

## How the controller records them

- **FRIDAY product, amendment A19 (KEYS-001, ROUTER-001, both P1).** "jarvis or friday" is FRIDAY itself (PRODUCT.md:3:
  spoken to as "Jarvis" or "Friday").
  - The key panel is the UI surface that `friday/secret_broker.py` already names as the replacement for its interim
    scratch-file entry ("interim surface until the UI panel exists").
  - Keys therefore go through the broker: encrypted, with alias-only metadata. They are never written to the database
    in plaintext, never logged, and never put in model context (NON_NEGOTIABLE 4).
  - Routers and connector metadata are database rows, as the owner asked ("database type").
- **Claude Code's own /model (development environment, not FRIDAY).** Probed on 2026-10-12:
  - OpenCode's API refuses outside clients with `FreeTierError: "OpenCode's free tier can only be used from within
    OpenCode"`.
  - The Codex models on the ChatGPT plan have no API key. A ChatGPT login used anywhere but Codex's own clients would be
    a transplanted token.
  - Neither can therefore back a Claude Code /model entry legitimately.
  - The legitimate equivalent is the session handoff (`scripts/session_handoff.py`, a `/models` command, and a
    StopFailure hook on Claude's usage limit). It continues the session inside Codex or OpenCode.
  - OpenCode's own /models already lists both the free models and the owner's ChatGPT models.
  - A paid-key gateway behind Claude Code's /model would need the owner's decision on billing and data policy. Recorded
    as an open question, not built.
