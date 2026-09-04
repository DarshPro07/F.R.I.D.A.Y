# Gate 1 — Product

## User problem

Friday can already *talk* about the screen ("I can see a dashboard"), but she
cannot **point**, and she cannot **act**. Two concrete failures:

1. *"Where do I click to schedule this email instead of sending it now?"* —
   today the answer is a paragraph of prose the user must then translate into a
   hunt around the UI. The button is often one they never knew existed.
2. *"Just do it for me."* — today the answer is instructions. The user still
   does the clicking.

## User

The owner, at his own desktop, hands often busy, frequently asking about
unfamiliar UI (settings mazes, Gmail's hidden scheduling, unsubscribe flows).
Windows 11, i9 + GTX 1650, 1080p. Not a developer session — he is *using* the
computer and wants help on the screen in front of him.

## Current behavior

- `vision.inspect_screen` captures and describes, in prose, with a confidence.
- No pointing. No clicking. No typing.
- Any "do it for me" ends in instructions.

## Desired outcome

**A. Point (read-only, everyday).**
He asks "where do I click to X" and within a couple of seconds an **arrow
appears on the actual screen**, tip on the control, with a short label, and
Friday says one line: *"Right there, sir."* If it is slightly off he says "a
little further left" and it re-points.

**B. Take over (gated, deliberate).**
He says "take over and X". Friday **shows a plan first** (3–6 steps) and waits
for a real yes. Then she executes **one step at a time** — look, act, say what
she did — and the word **"stop"** halts her within one step, hands down.

**C. Narrate.**
Both modes speak one short sentence per beat, in Friday's existing voice. A
quiet mode silences it.

## Success evidence

- Pointer: on a real screenshot of this machine, the arrow tip lands **on the
  named control** for a set of fixtures; misses are *reported as low
  confidence*, not silently drawn wrong.
- Takeover: a rehearsal task ("open a new note titled …") completes with each
  step evidenced by a before/after capture.
- **"stop" halts within one step, provably** — an automated test, not a vibe.
- Dangerous instructions are refused **by code**, verifiable with a test that
  the refusal cannot be turned off by autonomy settings or by asking nicely.

## Failure conditions (this feature is still wrong if…)

- It clicks somewhere it was not told to, or clicks on a *guess* presented as
  certainty.
- "stop" is advisory rather than immediate.
- The danger refusals live only in a prompt and can be talked around.
- It silently uses stale screen state (acts on what the screen looked like
  seconds ago).
- It becomes another always-on GPU/CPU load on a machine already fighting lag.

## User journeys

1. **Where do I click** — ask → arrow on screen + one spoken line.
2. **Correction** — "a little further left" → re-point, same target memory.
3. **Not found** — the control is not on screen → say so honestly, draw nothing.
4. **Plan a takeover** — "take over and …" → plan shown, nothing touched yet.
5. **Approve and drive** — "go" → step, narrate, step, narrate → done.
6. **Stop mid-drive** — "stop" → halts, says so, leaves the mouse alone.
7. **Refusal** — "take over and pay this invoice" → refused, with the reason.

## Interaction

- Arrow is drawn **on the real desktop**, above all windows, click-through, and
  self-dismisses. It is a pointer, not a window to manage.
- The plan and every narration line also appear in the existing HUD transcript,
  so there is a written record of what she did.

## Non-goals

- Not a general RPA/macro recorder. One task, confirmed, at a time.
- No unattended or scheduled driving. A human starts every takeover.
- No OCR/accessibility-tree scraping in v1 (vision + coordinates only).
- No cross-platform parity in v1 — Windows first (macOS path noted in ADR).
- Not replacing browser automation (`ui_browser`) for things a browser can do
  properly; the desktop driver is for the rest of the OS.
