# Gate 2 — Architecture

## Guiding decision

Do **not** build the pack's standalone `screen-guide` folder. Friday already
owns capture, vision, artifacts, voice, policy, confirmation and contracts.
The feature is two new *capabilities* inside the existing fabric, plus one
genuinely new primitive (synthetic input) placed in the module that already
exists for exactly that kind of thing.

## Proposed fit

```
ask ─► capability_router ─► toolset fn ─► PolicyEngine.decide ─► ActionResult
                                              │
   screen.screen_point   (AUTO)               │  read-only: look + draw
   desktop.desktop_plan  (CONFIRM)            │  proposes, touches nothing
   desktop.desktop_step  (CONFIRM + nonce)    │  one action, then stops
   desktop.desktop_stop  (AUTO)               │  always allowed to stop
```

| Layer | File | New? |
|---|---|---|
| Win32 input primitives (`SendInput`, cursor, metrics) | `friday/platform/windows.py` | **extended** |
| Locate-in-image (normalized coords from vision) | `friday/toolsets/vision.py` | **extended** |
| Pointing capability + arrow drawing | `friday/toolsets/screen.py` | new |
| Takeover capability (plan / step / stop) | `friday/toolsets/desktop.py` | new |
| On-screen overlay launcher | `friday/overlay.py` | new |
| Overlay process (Tk, topmost, click-through) | `scripts/jarvis_overlay.py` | new |
| MCP adapter | `friday/tools/screen_control.py` | new |
| Policy categories | `friday/policy.py` | extended |
| Capability + router registration | `capabilities.py`, `capability_router.py` | extended |

## Data flow

**Point:**
`capture_screen()` → `Frame` (real px, sha256) → `_downscaled()` → vision with
**locate schema** → `{found, x, y, label, confidence}` *normalized* →
denormalize against `Frame.width/height` → PIL arrow → artifact PNG →
`overlay.show(x_px, y_px, label)` → ActionResult(+`spoken_form`).

**Drive:**
`desktop_plan(task)` → capture → vision → ordered steps → **`confirmation.book.ask`**
→ CANCELLED + nonce (nothing touched).
`desktop_step(nonce)` → `consume(nonce)` → abort check → **danger gate** →
capture (fresh) → locate → `SendInput` → capture again → verify changed →
narrate → ActionResult. Repeat per step; each step spends its own approval.

## Interfaces (contracts, not bodies)

```python
# vision.py
def locate_in_frame(frame: Frame, target: str, hint: str = "") -> dict
    # -> {"found": bool, "x": float, "y": float, "label": str,
    #     "confidence": float, "why": str}     x,y are 0.0-1.0

# platform/windows.py
def send_mouse_move_abs(nx: float, ny: float) -> None
def send_mouse_click(button: str = "left", double: bool = False) -> None
def send_text(text: str) -> None            # unicode via KEYEVENTF_UNICODE
def send_key(name: str) -> None             # enter/tab/esc/win/ctrl+...
def cursor_pos() -> tuple[int, int]
def screen_size() -> tuple[int, int]
```

## State

Takeover is a small state machine, held in one place and inspectable:

`IDLE → PLANNED(nonce, steps) → DRIVING(step_i) → {DONE | STOPPED | REFUSED | FAILED}`

- `PLANNED` expires (same TTL discipline as `confirmation`), so an approved
  plan cannot be executed an hour later against a different screen.
- `DRIVING` checks a process-global `ABORT` event **before every step**.

## Security — the core of this design

Three tiers, all enforced in code, none defeatable by prompt:

1. `SCREEN_POINT` → **AUTO**. Read-only. Captures and draws; never inputs.
2. `DESKTOP_CONTROL` → **CONFIRM**. Per `policy.py`, CONFIRM "requires a human
   to say yes, and no autonomy mode says it for them" — FULL autonomy cannot
   self-approve a takeover. Chosen deliberately over ASK, on the codebase's own
   misrouting evidence: a misrouted takeover is worse than a misrouted shutdown.
3. `DESKTOP_CREDENTIAL_ENTRY` → **DENY**. The tier `SECRET_READ` already uses:
   never, at any autonomy level, not grantable.

Plus a structural **danger gate** in `desktop.py` that inspects each step
before execution and refuses payment / card / bank / password / credential /
delete / send-without-preview intents. The Screen Pack puts these in prose;
this codebase already documents why that fails ("an instruction is a thing a
model can decide differently about"). Refusals here are `DENY` results, and a
test asserts they hold under `autonomy=FULL`.

**Freshness:** every step re-captures. Acting on a stale frame is treated as a
defect, not an optimisation.

## Failure paths

| Failure | Behavior |
|---|---|
| No `GOOGLE_API_KEY` | `NOT_CONFIGURED`, no draw, honest line |
| Vision confidence < threshold | `OBSERVED` "I'm not sure enough to point" — **never** a confident wrong arrow |
| Target not on screen | `found:false` → say so, draw nothing |
| Not Windows / user32 unavailable | `NOT_CONFIGURED` (module already degrades to `None`) |
| Overlay process fails | pointing still succeeds via the artifact; overlay is an enhancement, never a hard dependency |
| Nonce reused / retargeted | `BLOCKED` (existing `confirmation` fingerprinting) |
| Screen unchanged after a click | `PARTIAL` — "I clicked but nothing changed", not a success |

## Observability

Every step writes a before/after capture to artifacts with sha256, and the
ActionResult carries `verification=Verification(method=..., evidence=...)`.
A takeover is therefore reconstructable after the fact from disk.

## Compatibility

Purely additive. No existing capability changes behavior. Off Windows the new
capabilities report `NOT_CONFIGURED` and boot is unaffected (NON_NEGOTIABLE 15:
an absent upstream must never break boot).

## Alternatives considered

- **pyautogui/pynput** — rejected: not installed, and `hardware.py` sets the
  house precedent of ctypes over a new dependency for a few Win32 calls.
- **Accessibility tree (UIA) instead of vision** — genuinely more accurate and
  the right eventual answer; rejected for v1 as a much larger surface. Recorded
  as the upgrade path in the ADR, because it removes the coordinate-guessing
  risk entirely.
- **Pack's approach (save PNG, open Preview)** — rejected: it is not "pointing
  at the screen", it is showing a picture of the screen. The overlay is the
  product.
- **Drive without per-step approval** — rejected: blast radius.

## Architecture risks

1. **Coordinate accuracy is the whole feature.** If the model cannot reliably
   land on a button at 1080p, pointing is annoying and clicking is dangerous.
   Mitigation: confidence floor, human correction loop, and *clicking* requires
   a higher floor than *pointing*. Must be **measured** (Gate 5), not assumed.
2. Overlay always-on-top may fight full-screen exclusive apps. Accepted; it
   degrades to the artifact.
3. Added GPU/CPU load on a machine already lagging → overlay is short-lived and
   spawned only on demand; nothing polls.
