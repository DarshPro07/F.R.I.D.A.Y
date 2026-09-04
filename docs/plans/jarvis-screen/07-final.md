# jarvis-screen — final

## Objective

Give Friday the two screen powers from the Jarvis Screen Pack, built to this
codebase's standards rather than as the pack's standalone folder:

1. **Point.** "Where do I click to …" puts an arrow on the actual desktop,
   tip on the control, and says one line.
2. **Take over.** "Take over and …" shows a plan, waits for a real yes, then
   acts one step at a time, narrating, stoppable instantly.

## Architecture

Two capabilities inside the existing fabric, plus one genuinely new primitive:

- `friday/toolsets/screen.py` — pointing. Read-only by construction.
- `friday/toolsets/desktop.py` — the takeover: refusals, plan, step, stop.
- `friday/platform/windows.py` — extended with `SendInput` and cursor/metrics,
  in the module that already binds user32 with declared argtypes.
- `friday/toolsets/vision.py` — extended with `locate_in_frame` (normalised
  coordinates) and a monitor origin on `Frame`.
- `friday/overlay.py` + `scripts/jarvis_overlay.py` — the click-through arrow.
- `friday/tools/screen_control.py` — the MCP adapter.
- `policy.py`, `capabilities.py`, `capability_router.py`, `semantics.py` —
  registration and trust tiers.

## What makes this different from the pack

The pack's protections are prose in a `CLAUDE.md`. This codebase already
records why that is insufficient — *"an instruction is a thing a model can
decide differently about"* — so the same protections are structural:

| Pack | Here |
|---|---|
| "Hard refusals" as prompt text | `forbidden()` in code + `DESKTOP_CREDENTIAL_ENTRY: DENY`, in `NON_APPROVABLE` |
| "Wait for my go" as prompt text | `CONFIRM` tier that **FULL autonomy cannot grant**, spent through a fingerprinted one-shot nonce |
| "Stop" as prompt text | `ABORT` flag checked before every step; `desktop_stop` is AUTO |
| Arrow in a saved picture opened in Preview | Click-through, always-on-top arrow on the live desktop |
| — | A page that says "take over" is refused by provenance before a question exists |
| — | Fresh capture per step; unchanged screen ⇒ `PARTIAL`, never a claimed success |

## Files changed

New: `friday/toolsets/screen.py`, `friday/toolsets/desktop.py`,
`friday/overlay.py`, `scripts/jarvis_overlay.py`,
`friday/tools/screen_control.py`, `tests/test_jarvis_screen.py`,
`docs/plans/jarvis-screen/*`.

Modified: `friday/platform/windows.py`, `friday/toolsets/vision.py`,
`friday/policy.py`, `friday/capabilities.py`, `friday/capability_router.py`,
`friday/semantics.py`, `friday/tools/__init__.py`.

## Results

- New suite: **27 passed**.
- Deterministic gate: **3282 passed** across the three portions; the single
  regression I introduced (routing collisions) was found, fixed and
  re-verified (179 passed).
- 4 remaining failures verified **pre-existing** and unrelated, each checked in
  isolation (see `06-verification.md` §7).
- Live evidence: arrow tip offset **(0, 0)**; cursor control **0 px error**
  across a 3840×1080 dual-monitor desktop including both far corners; full
  ask → approve → act → replay-blocked chain exercised for real.

## Unresolved limitations

1. **Live voice narration is not exercised.** The tools are registered and
   every result carries a `spoken` line, but no LiveKit session has driven
   them. This is the main outstanding item.
2. **Clicking a real control was not performed** — verified with `move` only,
   because the owner was mid-task with a modal dialog open. The click path is
   the same code as `move` plus `send_mouse_click`, which is itself verified,
   but the combination has not been run against a live application.
3. **Coordinate accuracy is proven on one target**, not across applications.
   The confidence floors (0.45 to point, 0.65 to click) exist because of this,
   and a low-confidence locate refuses rather than guessing.
4. macOS/Linux: the capabilities report `NOT_CONFIGURED` rather than working.

## Rollback

Additive. Remove the three policy rows and the four `Capability` entries and
the capabilities disappear from the router and the MCP surface; the new modules
become unreferenced. No migration, no data change, no existing behaviour
altered.

## Verdict

**PARTIALLY_VERIFIED.**

Everything that could be verified without a live voice session was verified
against this machine with recorded evidence, including the whole security
model and the full approve-act-replay chain. It is not marked VERIFIED because
the spoken path and a real click in a real application have not yet been
exercised — and this feature's whole point is that it does not claim more than
it has shown.
