# Owner directive 2026-10-05 (b): Hermes tools stay; signed-in CLIs and free models are FRIDAY's routes; Sarvam speech

Answers given in the session on 2026-10-05, verbatim, after the FRIDAY.exe live test of f222339.

## The questions and the answers

1. *FRIDAY's 'friday' Hermes profile has no sign-in, so the Hermes model route can't answer. How should it get one?*

   > use opencode free models and codex and claude code login on ur own if they are signed in just use them

2. *The cloned 'friday' profile has Hermes's own file and terminal toolsets ON, so every delegation that carries a scope is refused. Should FRIDAY switch those two off in its own profile?*

   > Yes, switch them off (Recommended)

   Superseded minutes later by:

   > Bro i want herems fiels and other thing to be used by friday to and hermes too as they are good the best fits u choose research

3. *Voice needs one working speech key. Which setup should FRIDAY use?*

   > Sarvam in and out (Recommended)

## How the controller reads them

- Hermes' own file/terminal tools stay ON in the `friday` profile, for Hermes and for FRIDAY's ordinary
  delegations. A delegation that carries a scope gets a gateway started with `HERMES_TUI_TOOLSETS`
  minus `file` and `terminal` (Hermes' tui_gateway reads that variable at agent creation), so the scope
  is enforced by FRIDAY's jail-backed file capability and commands stay disabled (ruling 2026-10-03c).
  Recorded as SEC-005's next slice.
- The model routes are the signed-in CLIs and opencode's free models: Claude Code (already FRIDAY's
  first CLI route), opencode free models (`opencode run`, cost 0) and Codex (signed in with ChatGPT,
  upgraded by the controller). Recorded as MODEL-012 slice 3.
- The installed Hermes reports itself 39,306 commits behind; its `call_llm` does not take the OpenAI
  Codex OAuth login and lists only `custom` as usable, so the Hermes model route fails even from the
  signed-in default profile. Updating Hermes is the owner's call and has not been done.
- Speech: `TTS_PROVIDER=sarvam` in FRIDAY's own `.env`; the owner supplies a working `SARVAM_API_KEY`
  (the present one is refused, 403 invalid_api_key_error).
