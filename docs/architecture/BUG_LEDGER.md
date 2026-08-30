
## B-010 (P2, OPEN) — intermittent instruction drop at session start
- **Repro (2 of 4 runs):** open a LiveKit session, send an instruction shortly
  after Friday's entry greeting. Instead of acting on it, Friday replies with a
  greeting carrying the wrong time of day:
  ```
  13:40 IST  [ 5.69s] Good afternoon, boss. What do you need?   <- on_enter, correct
             [12.26s] You're awake late at night, boss? ...      <- reply to "Say only: DUPCHECK_OK"
  ```
- **Not a duplicate greeting.** The second string is absent from `on_enter`'s
  table, whose late-night line reads "Greetings boss, you're up late at night
  today." It is model-generated, and word-for-word a greeting Friday produced in
  a session the previous night.
- **Hypotheses tested and disconfirmed:**
  - racing `on_enter` — `--ready 45` with two messages 25 s apart: both complied;
  - cold first session after restart — restart then immediate probe: complied
    (`COLD_OK`), greeting took 29 s as the warm-up comments predict.
- **Not the cause on inspection:** `temporal_context()` computes from
  `datetime.now()` at call time.
- **Next direction:** conversation history / briefing recall bleeding a prior
  session's greeting into a fresh turn.
- **Status:** OPEN, intermittent, trigger not found. 4 runs, 2 failures.

## N-004 — CORRECTION: the golden_gate delivery was a pre-fix row, not a live P0
- On finding `dlv-71ca73e2cc` (`origin=golden_gate`, message *"NEGATIVE CONTROL:
  gate probe, must never be spoken"*, `delivery_state=DELIVERED`, 2026-08-27
  16:57:35 -> 16:57:53) I called it a confirmed P0. That was premature.
- `DELIVERABLE_ORIGINS` was committed at 2026-08-27 18:53:36 — 1 h 56 m *after*
  the row was written. `origin` has one writer (`WorkRunLog.create`, from
  `run_origin()`) and no update path, so the row cannot have been re-labelled.
- It is evidence that the bug RC1.1 fixed was real. Today's L20 passes at three
  layers: no delivery row created, marker absent from a 13-minute live console
  session, marker absent after restart and reconnect.
- Lesson kept: date the artefact against the fix before calling it a defect.
