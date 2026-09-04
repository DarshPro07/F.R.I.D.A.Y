# Gate 4 — Vertical slices

Each slice is end-to-end and verified before the next begins.

## Slice 1 — Tracer: see → locate → draw → evidence  ✅
Real capture of this desktop, real vision locate with normalized coords, real
arrow drawn by PIL, artifact on disk. No overlay, no input.
**Verify:** run it against the live screen; the artifact exists and the arrow
tip lands on the named control. Low confidence draws nothing.

## Slice 2 — The living arrow (the upgrade over the pack)  ✅
Topmost, click-through, self-dismissing overlay process. Pointing now happens
**on the screen**, not in a saved picture.
**Verify:** overlay appears above other windows, does not steal focus or
clicks, disappears on its own; failure of the overlay still leaves Slice 1
working.

## Slice 3 — Input primitives + the danger gate (no driving yet)  ✅
`SendInput` move/click/type/key in `platform/windows.py`; `forbidden()` in
`desktop.py`; the three policy tiers.
**Verify:** unit tests prove refusals hold under `autonomy=FULL`; takeover is
`CONFIRM` not `ASK`; primitives move the real cursor.

## Slice 4 — Plan → approve → one step → stop  ✅
The state machine, nonce spend per step, fresh capture per step, before/after
digests, abort event.
**Verify:** rehearsal task; replayed nonce is `BLOCKED`; "stop" halts before
the next step (automated); unchanged screen ⇒ `PARTIAL`.

## Slice 5 — Narration + HUD wiring
Spoken line per beat via the existing TTS; plan and steps into the transcript;
quiet/narrate toggle.
**Verify:** in the running UI.

## Slice 6 — Registration & discovery
Capability entries, router group, MCP adapter, `server.py`.
**Verify:** `test_phase0` coverage tests still pass (every registered tool
declared), semantic routing unaffected.
