# Gate 0 — Reality: what already exists

Feature: **jarvis-screen** — Friday looks at the screen and points at the exact
control; and, behind a human gate, takes the mouse and keyboard and does the
task itself, narrating each step.

Origin: the "Jarvis Screen Pack" (three copy-paste prompts, macOS, standalone
`screen-guide` folder + CLAUDE.md prose rules). This document records what
Friday **already** has, so the feature is built into the existing architecture
rather than bolted on beside it.

Evidence labels: REPRODUCED (ran it), STATICALLY_CONFIRMED (read the code),
INFERRED, UNKNOWN.

---

## Already built — reuse, do not duplicate

| Capability | Where | Evidence |
|---|---|---|
| Full-screen capture (mss), multi-monitor, region | `friday/toolsets/vision.py::capture_screen` | STATICALLY_CONFIRMED |
| Capture provenance (sha256, size, captured_at, save to artifacts) | `vision.py::Frame` | STATICALLY_CONFIRMED |
| Vision model call, **structured JSON** via `response_schema`, honest `confidence` required | `vision.py::analyse_frame` (`gemini-2.5-flash`) | STATICALLY_CONFIRMED |
| Spoken phrasing of a visual result | `vision.py::spoken_form`, `confidence_band` | STATICALLY_CONFIRMED |
| Typed Win32 bindings + `_bind()` helper, degrade to `None` off-Windows | `friday/platform/windows.py` | STATICALLY_CONFIRMED |
| Policy tiers AUTO / ASK / CONFIRM / DENY + `PolicyEngine.decide` | `friday/policy.py` | STATICALLY_CONFIRMED |
| Nonce confirmation book (`ask` / `approve` / `consume`, fingerprinted) | `friday/confirmation.py` | STATICALLY_CONFIRMED |
| ActionResult contracts; `succeeded()` **requires** `verification=` | `friday/contracts.py` | REPRODUCED (hit the error this cycle) |
| Capability registry + keyword router + MCP adapters | `capabilities.py`, `capability_router.py`, `friday/tools/*` | STATICALLY_CONFIRMED |
| Low-latency TTS (Deepgram Aura) and a browser voice that already speaks | `ui_server.py::_tts_bytes`, `ui/index.html::speak()` | STATICALLY_CONFIRMED |
| Artifacts directory for evidence | `vision.py::captures_dir`, `config.ARTIFACTS_DIR` | STATICALLY_CONFIRMED |

**Conclusion:** the pack's "eyes" (capture) and "voice" (narration) are ~80%
already present. Building a separate `screen-guide` folder would duplicate
three subsystems Friday already owns.

## Missing — this is the actual new work

| Gap | Evidence |
|---|---|
| **No desktop input control of any kind** — no mouse move/click, no synthetic keystrokes, anywhere in the tree | STATICALLY_CONFIRMED (searched `friday/`, `scripts/`, `server.py`; only browser-level clicks in `web.py` and window *management* in `platform/windows.py`) |
| No way to get **coordinates** from vision — `analyse_frame`'s schema returns prose (`observation`, `identification`), never a position | STATICALLY_CONFIRMED (`_ANALYSIS_SCHEMA`) |
| No image annotation (arrow/label) | STATICALLY_CONFIRMED |
| No on-screen overlay | STATICALLY_CONFIRMED |
| No step-wise "drive" loop, plan-then-execute, or stop word | STATICALLY_CONFIRMED |

## Environment facts (REPRODUCED — ran the imports)

| Package | `.venv` (runtime) | Consequence |
|---|---|---|
| `mss` | yes | capture is solved |
| `PIL` (Pillow) | yes | **arrow drawing needs no new dependency** |
| `ctypes` | yes | **input needs no new dependency** |
| `pyautogui` | **no** | do not reach for it |
| `pynput` | **no** | do not reach for it |

House rule confirmed in `toolsets/hardware.py`: *"Deliberately not a new
dependency: `EnumDisplayMonitors` is three ctypes…"*. Driving input through
`SendInput` in the existing typed-binding module is the codebase's own idiom,
not an exception to it.

## The constraint that shapes the whole design

`vision.py::_downscaled()` shrinks the image before upload (long edge capped,
JPEG q85). **Any pixel coordinate the model returns is in downscaled space and
is meaningless upstream.** Therefore the locate contract must be **normalized
0.0–1.0**, converted to device pixels only at the very end, against the real
captured frame size. INFERRED → this is a correctness requirement, not a style
choice.

## Safety reality (the reason this feature is not "just automation")

`policy.py` already encodes the exact hazard, in its own words:

> `CONFIRM` — "Requires a human to say yes, and no autonomy mode says it for
> them… FULL autonomy turns every ASK into a yes… What makes this necessary
> rather than fussy is the routing evidence. Four batches in a row, a request
> landed on a capability that shared its vocabulary with the intended one…
> A misrouted 'shut it down' when he meant the music is not hypothetical here,
> and AUTO would carry it out."

A misrouted **takeover** is strictly worse than a misrouted shutdown: it can
click and type anything, in any application, including irreversible things the
policy engine never sees. And `SECRET_READ: DENY` establishes the "never, and
cannot be granted at all" tier.

The Screen Pack's protections are **prose in CLAUDE.md**. This codebase already
states why that is insufficient (`agent_friday.py`): *"an instruction is a
thing a model can decide differently about."* So the refusals must be
structural.

## Known limitation carried in

Vision-model coordinates are approximate. The pack's answer is a human
correction loop ("a little further left"). That is necessary but not
sufficient for a system that will then *click* the point. UNKNOWN → accuracy
on this user's 1080p desktop must be measured, not assumed.
