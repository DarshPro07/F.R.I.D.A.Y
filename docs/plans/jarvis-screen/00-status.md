# jarvis-screen — status

**Feature:** Friday points at the exact control on screen, and (behind a human
gate) takes the mouse and keyboard, narrating each step.

**Verdict:** `PARTIALLY_VERIFIED` — see `07-final.md`.

| Gate | State |
|---|---|
| 0 Reality | ✅ `01-reality.md` |
| 1 Product | ✅ `02-product.md` |
| 2 Architecture | ✅ `03-architecture.md` |
| 3 Program design | ✅ `04-program-design.md` |
| 4 Slices | ✅ `05-slices.md` |
| 5 Verification | ✅ `06-verification.md` → `07-final.md` |

## Slices

| # | Slice | State | Evidence |
|---|---|---|---|
| 1 | see → locate → draw → evidence | ✅ | Live capture; clock located at (0.953, 0.981) conf 0.95; annotated PNG inspected, tip on target |
| 2 | living arrow (topmost, click-through) | ✅ | Before/after diff: 135 new ink px at the tip, **closest offset (0, 0)**; `WS_EX_TRANSPARENT` read back set |
| 3 | input primitives + danger gate | ✅ | **0 px** error at 7 points across a 3840×1080 dual-monitor desktop; 27 tests on the refusals and tiers |
| 4 | plan → approve → step → stop | ✅ | Live: no-nonce refused, un-approved refused, approved acted, **replay blocked** |
| 5 | narration | ◐ | Every result carries a `spoken` line; **live voice not exercised** |
| 6 | registration & discovery | ✅ | 169 MCP tools; `test_phase0` equality, router group and semantics all pass |

## Verified security claims (measured under `autonomy=FULL`)

```
screen.point               AUTO     allowed=True
desktop.plan               CONFIRM  allowed=False   <- human required
desktop.step               CONFIRM  allowed=False   <- human required
desktop.stop               AUTO     allowed=True    <- stopping is never gated
desktop.credential_entry   DENY     allowed=False   <- never, not grantable
```

Plus: credential entry is in `NON_APPROVABLE`; a `READ_MATERIAL` provenance
(text Friday read somewhere) cannot reach a takeover at all.

## Defects found by verification

1. **DPI scaling** — overlay child was not DPI aware; every arrow would have
   missed by 25% on this machine. Fixed, re-verified to (0, 0).
2. **Layered attributes discarded** by `SetWindowLong`; window composited as
   nothing. Fixed by re-arming `SetLayeredWindowAttributes`.
3. **A false "inaccurate" cursor reading** — the owner's hand was on the mouse.
   Diagnosed rather than "fixed", which would have introduced a real bug.
4. **Multi-monitor origin** dropped by `capture_screen`; a second-monitor
   target would have been clicked 1920 px away. `Frame` now carries it.
5. **My own routing regression** — `desktop_stop` displaced `power_cancel` on
   "stop the restart"; `desktop_plan` lost to `vision_inspect_screen` because
   *screen* is in that capability's name. Both fixed and re-verified.

## 2026-09-02 system audit (follow-up)

`docs/audits/2026-09-02-jarvis-system-audit.md` audited the whole system after
this feature landed and fixed six things this feature touched or exposed: the
face gate now covers WebSocket scope (the `/api/stt` socket was a latent
bypass), every vision call carries a deadline, synthetic typing refuses when
keyboard focus moved, `unlock()` no longer waits on the WebGL orb, the presence
re-lock honours `--bypass-face`, and the speech-engine default migrates
correctly. e2e: Playwright desktop 8 passed / 1 skipped, mobile 7 passed /
1 failed (horizontal overflow — desktop-only HUD by design).

## Next action

A live voice session: say *"where do I click to schedule this email"* and
*"take over and open a new note"*, and confirm the spoken path and a real click
in a real application. That closes slice 5 and the two open items in
`07-final.md`.
