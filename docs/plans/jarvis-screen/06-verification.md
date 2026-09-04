# Gate 5 — Verification

Everything below was executed against this machine (Windows 11, i9, GTX 1650,
dual 1920×1080 = a 3840×1080 virtual desktop). Nothing here is asserted from
reading the code.

## 1. The security model, measured under the most permissive setting

`PolicyEngine(autonomy=FULL)`:

```
screen.point               AUTO     allowed=True
desktop.plan               CONFIRM  allowed=False    <- a human is required
desktop.step               CONFIRM  allowed=False    <- a human is required
desktop.stop               AUTO     allowed=True     <- stopping is never gated
desktop.credential_entry   DENY     allowed=False    <- never, not grantable
```

Also proven by test:
- `DESKTOP_CREDENTIAL_ENTRY ∈ policy.NON_APPROVABLE` → `approve_for_session`
  raises rather than unlocking it.
- `provenance_verdict` refuses `desktop.plan`/`desktop.step` for
  `READ_MATERIAL` provenance: **a web page cannot ask for a takeover**, and no
  confirmation is ever created for one.

## 2. Pointing — live

`screen_point(run, "the clock in the Windows taskbar")` against the real screen:

```
status  : succeeded
result  : pointed
label   : "10:09 AM"          (read off the screen by the model)
norm    : 0.953, 0.981        (fractions, resolution independent)
pixel   : 1830, 1059          on a 1920x1080 frame
conf    : 0.95
verify  : pointer_drawn_at_normalised_xy
```

The annotated PNG was opened and inspected: the arrow tip is on the taskbar
clock, halo legible against the dark taskbar, label pill at the tail.

## 3. The living arrow — live, with a before/after diff

Requested tip (1400, 600). Screen captured before and after the overlay:

```
new ink pixels within 14px of the requested tip : 135
closest offset                                   : (0, 0)
```

Click-through verified by reading the style back: `WS_EX_TRANSPARENT` is set
(and `WS_EX_NOACTIVATE`, so it never steals focus). The script refuses to show
at all if it cannot make itself click-through, rather than covering the button
it is pointing at.

## 4. Synthetic input — live, across both monitors

Move-and-read-back, immediate:

```
asked (400, 300)     -> got (400, 300)      offset (0,0)
asked (1600, 900)    -> got (1600, 900)     offset (0,0)
asked (960, 540)     -> got (960, 540)      offset (0,0)
asked (3000, 200)    -> got (3000, 200)     offset (0,0)      (second monitor)
asked (100, 1000)    -> got (100, 1000)     offset (0,0)
asked (3839, 1079)   -> got (3839, 1079)    offset (0,0)      (far corner)
asked (0, 0)         -> got (0, 0)          offset (0,0)
worst offset: 0 px
```

## 5. The takeover, end to end — live

With a harmless `move` step (no clicks, because the owner had a modal dialog
open at the time):

| Attempt | Result |
|---|---|
| step with **no** nonce | `cancelled` — `needs_confirmation` |
| step with an **un-approved** nonce | `cancelled` — *"that has not been approved yet"* |
| step with an **approved** nonce | `succeeded` — `move at (1826,1059) on '10:48 AM'`, cursor confirmed there |
| **replay** of the spent nonce | `cancelled` — *"that confirmation has already been used"* |

Verification method recorded on the success: `screen_changed_after_action`,
with the before/after screen sha256 in the evidence.

## 6. Registration

`register_all_tools` exposes **169** tools (was 165). `screen_point`,
`desktop_plan`, `desktop_step`, `desktop_stop` are all live, each with a
`Capability` entry (so `test_phase0`'s exact-equality holds), a router group,
and a derived semantic operation/target.

## 7. Regression (deterministic gate, `-m "not live and not slow"`)

| Portion | Result |
|---|---|
| files 1–78 | 1585 passed, 1 failed → **the failure was mine, fixed** (see below), re-verified |
| files 79–118 | 870 passed, 0 failed |
| files 119–157 | 827 passed, **4 failed — all pre-existing** |
| `test_jarvis_screen.py` (new) | 27 passed |

**The one regression I caused, and fixed:** my `desktop_stop` intent examples
displaced `power_cancel` on "stop the restart", and `desktop_plan` lost its own
phrasing to `vision_inspect_screen` because the word *screen* is in that
capability's name. Fixed by adding `stop the restart` / `cancel the shutdown`
to `desktop_stop`'s negatives and by phrasing the takeover examples without the
word "screen". Re-verified: 179 passed.

**The four remaining failures are not from this feature**, each checked in
isolation:
- `test_reachability` — 3 dead symbols in `fabric_process.py` / `fabric_service.py`;
  both files are **unchanged vs HEAD** and none of the new modules are in the
  dead list.
- `test_reflex::test_the_agent_warms_the_fast_path_at_startup` — **passes in
  isolation**; batch-ordering flakiness in a quarter my tests do not run in.
- `test_upstream_lock` ×2 — `FileNotFoundError` for
  `Friday Stark Demo Main/06_schemas/UPSTREAM_LOCK_TEMPLATE.json`; that whole
  directory appears as deleted in the session's opening `git status`.

## 8. Defects found *by* verification, not by review

1. **DPI scaling — would have broken the feature outright.** The overlay child
   process was not DPI aware, so Tk laid out in logical pixels while
   coordinates arrived in physical ones. A tip requested at (1400, 600) was
   drawn at (1750, 756) — exactly the 1.25 scale factor. Every arrow on this
   machine would have missed by a quarter of the screen. Fixed by declaring
   per-monitor DPI awareness before any window exists; re-verified to (0, 0).
   An earlier probe *hid* this because importing `mss` had already made the
   measuring process DPI aware — the child had not been.
2. **Layered attributes discarded.** Setting `WS_EX_LAYERED` via
   `SetWindowLong` discards the attributes Tk set for `-transparentcolor`,
   after which the window composites as nothing. Fixed by re-arming
   `SetLayeredWindowAttributes`.
3. **A false "inaccurate" reading.** The first cursor test showed 60–98 px
   errors. Diagnosis (not a fix) showed the owner's hand was on the mouse
   between write and read; with immediate read-back the mapping is exact.
   Had I "corrected" the mapping there, I would have introduced a real bug.
4. **Multi-monitor origin.** `capture_screen` discarded the monitor's
   left/top, so a point found on the second monitor would have been clicked
   1920 px away on the first. `Frame` now carries `origin_x/origin_y`.

## Not verified

- **Live voice narration** — the capabilities are registered and every result
  carries a `spoken` line, but the spoken flow has not been exercised through
  a live LiveKit session. Requires a live voice run.
- **Clicking a real control in a real application.** Verified with `move`
  only, deliberately: the owner was mid-task with a modal dialog open.
- Accuracy of the model's coordinates across many different applications; one
  target (the taskbar clock) was measured at 0.95 confidence and landed
  correctly. The confidence floors exist precisely because this is unproven
  in general.
