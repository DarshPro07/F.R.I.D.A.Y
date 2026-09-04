# Gate 3 — Program Design

## Files

| File | Change | Why |
|---|---|---|
| `friday/platform/windows.py` | extend | Typed `SendInput` + cursor/metrics bindings beside the existing user32 binds |
| `friday/toolsets/vision.py` | extend | `LOCATE_SCHEMA`, `_LOCATE_SYSTEM`, `locate_in_frame()` reusing the existing client/downscale path |
| `friday/toolsets/screen.py` | new | `draw_pointer()`, `screen_point()` (AUTO) |
| `friday/overlay.py` | new | `show(x, y, label, seconds)` — spawn/replace overlay process, never raises |
| `scripts/jarvis_overlay.py` | new | Tk topmost, transparent, click-through arrow; self-dismisses |
| `friday/toolsets/desktop.py` | new | danger gate, `desktop_plan/step/stop`, abort event, state |
| `friday/policy.py` | extend | `SCREEN_POINT: AUTO`, `DESKTOP_CONTROL: CONFIRM`, `DESKTOP_CREDENTIAL_ENTRY: DENY` + tool ids |
| `friday/capabilities.py` | extend | Capability entries (intent/negative examples, risk) |
| `friday/capability_router.py` | extend | `"screen"` group |
| `friday/tools/screen_control.py` | new | MCP adapter (`screen_point`, `desktop_plan`, `desktop_step`, `desktop_stop`) |
| `server.py` | extend | register the adapter |
| `tests/test_jarvis_screen.py` | new | the tests below |

## Types and contracts

```python
# vision.py
LOCATE_SCHEMA = {"type":"object","properties":{
    "found":{"type":"boolean"}, "x":{"type":"number"}, "y":{"type":"number"},
    "label":{"type":"string"}, "confidence":{"type":"number"},
    "why":{"type":"string"}}, "required":["found","confidence"]}
def locate_in_frame(frame: Frame, target: str, hint: str = "") -> dict

# screen.py
POINT_MIN_CONFIDENCE = 0.45          # below: say "not sure", draw nothing
def draw_pointer(png: bytes, w: int, h: int, nx: float, ny: float, label: str) -> bytes
def screen_point(run, target: str, *, hint: str = "", monitor: int = 1,
                 overlay: bool = True, engine=default_engine) -> ActionResult

# desktop.py
CLICK_MIN_CONFIDENCE = 0.65          # strictly higher than pointing
class Refusal(NamedTuple): refused: bool; reason: str; matched: str
def forbidden(step: dict) -> Refusal          # structural danger gate
def desktop_plan(run, task: str, *, engine) -> ActionResult   # CANCELLED + nonce
def desktop_step(run, nonce: str, *, engine) -> ActionResult  # one step
def desktop_stop(run, *, engine) -> ActionResult              # sets ABORT

# platform/windows.py
def send_mouse_move_abs(nx, ny); send_mouse_click(button="left", double=False)
def send_text(text); send_key(name); cursor_pos(); screen_size()
```

A **step** is a dict: `{"action": "click"|"double_click"|"type"|"key"|"move",
"target": str, "text": str, "say": str}`.

## Call flows

**screen_point**
1. gate(`screen.point`) → 2. `capture_screen(monitor)` → 3. `locate_in_frame`
→ 4. if `not found` → `OBSERVED` + honest line, no draw
→ 5. if `confidence < POINT_MIN_CONFIDENCE` → `OBSERVED` "not sure enough"
→ 6. `draw_pointer` → save artifact → 7. `overlay.show` (best-effort)
→ 8. `succeeded(verification=Verification("pointer_drawn_at_normalised_xy", ...))`

**desktop_plan** → gate(`desktop.control`) → capture → vision proposes steps →
`forbidden()` over **every** step (any refusal ⇒ whole plan refused) →
`confirmation.book.ask(...)` → `CANCELLED` with `output.confirm`.

**desktop_step(nonce)** → gate → `consume(nonce)` → `ABORT` check →
`forbidden(step)` → **fresh** capture → locate (needs `CLICK_MIN_CONFIDENCE`)
→ `SendInput` → capture again → compare digests → narrate → result.

## State transitions

`IDLE → PLANNED → DRIVING → DONE | STOPPED | REFUSED | FAILED`
`ABORT` is checked at the top of every step; `desktop_stop` sets it and is
**AUTO** — stopping is never gated behind an approval.

## Error model

| Category | Status | Example |
|---|---|---|
| Not configured | `NOT_CONFIGURED` | no `GOOGLE_API_KEY`; not Windows |
| Refused by policy/danger gate | `CANCELLED` + `result:"refused"` | "pay the invoice" |
| Awaiting human | `CANCELLED` + `confirm` | plan proposed |
| Honest uncertainty | `OBSERVED` | not found / low confidence |
| Acted, no effect | `PARTIAL` | clicked, screen digest unchanged |
| Acted, verified | `SUCCEEDED` + `verification` | screen changed after click |

## Test design (each with its known failure mode)

| Test | Fails if the defect returns |
|---|---|
| `test_low_confidence_never_draws` | a confident wrong arrow is drawn |
| `test_not_found_says_so` | invents a location |
| `test_normalised_coords_survive_downscale` | coords taken in downscaled space |
| `test_danger_gate_refuses_under_full_autonomy` | prose-only refusal, defeatable |
| `test_takeover_is_CONFIRM_not_ASK` | FULL autonomy self-approves a takeover |
| `test_step_requires_unspent_nonce` | replay of an approval |
| `test_stop_halts_before_next_step` | "stop" is advisory |
| `test_unchanged_screen_is_partial_not_success` | claims success having done nothing |
| `test_off_windows_is_not_configured` | import/boot breaks on non-Windows |

## Least-confident decisions

1. **Vision coordinate accuracy at 1080p** — the feature's load-bearing
   assumption. Measured in Gate 5 against real fixtures; thresholds tuned from
   the measurement, not guessed.
2. Tk click-through overlay behavior over full-screen apps — degrade to artifact.
3. `SendInput` absolute coordinates on multi-monitor (normalized to the *virtual*
   desktop, not the primary) — must be handled explicitly.

## Rollback

Additive only. Remove the three policy rows + the capability entries and the
capabilities vanish from the router; the new modules become unreferenced. No
migration, no data change.
