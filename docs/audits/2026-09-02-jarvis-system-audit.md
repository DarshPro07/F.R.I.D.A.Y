# Friday / Jarvis — deep system audit, refactor and live validation

**Date:** 2026-09-02 · **Target:** `E:\friday-tony-stark-demo-main` (Windows 11, i9, GTX 1650, dual 1920×1080) · **UI under test:** `http://127.0.0.1:8770`

This audit was run against the *real* codebase, not the template's imagined
one. Two calibrations apply throughout:

1. **No invented numbers.** Anything not measured is marked `UNKNOWN`. The
   template pre-asserted "60% lower latency" and a "gBrain → Mem0 migration";
   neither claim is supported by evidence here and neither is repeated.
2. **Evidence labels** on every important conclusion: `REPRODUCED` (ran it),
   `STATICALLY_CONFIRMED` (read the code), `OBSERVED` (seen live), `INFERRED`,
   `UNKNOWN`.

Everything marked **FIXED** below was applied in this pass and is covered by a
test that fails on the old behaviour and passes on the new.

---

## Phase 1 — Architectural gap analysis (against reality)

### The layers the template presumes missing already exist

| Template's "missing layer" | What Friday actually has | Evidence |
|---|---|---|
| Intent router / semantic dispatch | `friday/capability_router.py` — keyword router with name/description/intent/negative scoring, group activation, "started work" promotion, and a learning loop; `friday/semantics.py` derives operation/target per capability with a capped override table (`≤25`, test-enforced) | STATICALLY_CONFIRMED |
| Execution planner / state machine | `friday/continuous.py::ContinuousTaskExecutor` + `friday/continuity.py::ContinuityManager` — durable runs, leases, wakes, portion budgets, exactly-one-wake invariant | STATICALLY_CONFIRMED |
| Deterministic authorization state | `friday/policy.py` tiers `AUTO/ASK/CONFIRM/DENY` (CONFIRM survives FULL autonomy) + `friday/confirmation.py` fingerprinted one-shot nonces + `provenance_verdict` (text Friday *read* cannot reach destructive tools) | STATICALLY_CONFIRMED, REPRODUCED |
| Circuit breaker / fallback for the LLM | `friday/resilience.py` — retry → provider fallback → rescue line | STATICALLY_CONFIRMED |
| Memory | GBrain is the **canonical** shared memory by explicit decision (non-negotiable #11: the UI is a view, not a second memory); a router-and-memory audit was completed 2026-09-01 | STATICALLY_CONFIRMED |

**Memory migration (Mem0/Zep): not recommended on current evidence.** GBrain is
a deliberate architectural decision, a dedicated memory audit was done the day
before this one, and no retrieval-latency or recall-failure measurement exists
that would justify a second memory system. What *would* justify it: measured
recall misses on the transcript-amnesia fixture, or p95 recall latency on the
request path. Until measured: `UNKNOWN`, and the non-negotiable stands.

### Genuine gaps (found and, where marked, fixed)

| Gap | Severity | State |
|---|---|---|
| Face gate did not cover WebSocket scope — latent until the first socket route (`/api/stt`) was added | High | **FIXED** |
| No deadline on any vision-model call (pointing, planning) — a stalled request hangs the capability | High | **FIXED** |
| Synthetic typing ignored keyboard focus — keystrokes could land in whichever window took focus between steps | High | **FIXED** |
| `unlock()` waited on the WebGL orb (CDN import + shader compile) before releasing the lock — "face verified but unresponsive" on weak GPUs | High (UX) | **FIXED** |
| Presence re-lock ran even with the face gate disabled (`--bypass-face`) | Medium | **FIXED** |
| Speech-engine default persisted before "auto" existed — existing profiles pinned to the laptop mic, silently defeating the headset behaviour | Medium | **FIXED** |
| No backpressure / rate limit on paid endpoints (`/api/ask` → Gemini, `/api/tts` and `/api/stt` → Deepgram) | Medium | proposed (§2.7) |
| Takeover plan state is a plain module dict mutated from async handlers | Low | proposed (§2.8) |
| HUD overflows horizontally on a phone viewport (`scrollWidth 808` vs `412`) | Low — desktop-only product by design | recorded (§5) |
| Dead subsystems flagged by reachability: `HistoryAwareFallbackStream`, `VoiceInputGate`, `DestinationVerification` (+ pre-existing `fabric_process`/`fabric_service` symbols) | Low | recorded |

### Single points of failure under load (STATICALLY_CONFIRMED)

- `ui_server.py` has **no concurrency cap** on `/api/ask`, `/api/tts`, `/api/stt`.
  A runaway client or a reconnect loop can fan out paid upstream calls without
  bound. No crash — the cost is money and latency, not availability.
- Vision calls had **no timeout** (fixed): a stalled upstream held the request
  worker and left the pointer/planner spinning.

---

## Phase 2 — Bug audit & vulnerability matrix

Format per finding: location · severity · root cause · verification · fix.

### 2.1 WebSocket bypassed the face gate — **FIXED**

- **Location:** `friday/access.py:305` (`GateMiddleware.__call__`)
- **Severity:** High (local-only: the UI binds `127.0.0.1` by default)
- **Root cause:** `if scope["type"] != "http" or not GATE_ENABLED: return await self.app(...)` — every non-HTTP scope passed through. Harmless while no WebSocket route existed; the moment `/api/stt` (microphone → Deepgram) was added, any local process could open it while the UI was *locked* and stream audio through Friday to a paid recogniser.
- **Verification:** `tests/test_jarvis_screen.py::test_a_locked_gate_refuses_the_speech_websocket` — with the gate on and no session, `websocket_connect("/api/stt")` must raise `WebSocketDisconnect`. Fails on the old code, passes now.
- **Fix (before → after):**

```python
# before
if scope["type"] != "http" or not GATE_ENABLED:
    return await self.app(scope, receive, send)

# after
kind = scope["type"]
if kind not in ("http", "websocket") or not GATE_ENABLED:
    return await self.app(scope, receive, send)
...
if kind == "websocket":
    await receive()                          # the websocket.connect event
    await send({"type": "websocket.close", "code": 1008})   # deny before accept
    return
```

### 2.2 No deadline on vision calls — **FIXED**

- **Location:** `friday/toolsets/vision.py` (`analyse_frame`, `locate_in_frame`), `friday/toolsets/desktop.py::_propose`
- **Severity:** High
- **Root cause:** three call sites each built `genai.Client(api_key=...)` with default (unbounded) HTTP options.
- **Verification:** `test_every_vision_call_carries_a_deadline` — the shared client factory passes `HttpOptions(timeout=VISION_TIMEOUT_MS)`. Confirmed against the SDK: `HttpOptions.timeout` is `Optional[int]`, **milliseconds**.
- **Fix:** one factory, `vision._client()`, `ADA_VISION_TIMEOUT_MS` (default 30 000), used by all three sites.

### 2.3 Typing ignored keyboard focus — **FIXED**

- **Location:** `friday/toolsets/desktop.py::desktop_step` (`type` / `key` branch)
- **Severity:** High
- **Root cause:** `SendInput` keystrokes go to whatever has focus. Focus can move between two steps (alt-tab, a notification, a dialog). A fresh screenshot proves nothing about focus. Text intended for a note could land in a password field.
- **Verification:** `test_typing_refuses_when_the_focus_moved_since_the_last_step` — with the foreground window changed since the previous step, the step returns `OBSERVED/focus_moved`, `send_text` is never called, and the plan index does not advance.
- **Fix:** the plan records the foreground `HWND` when made and after every action; before `type`/`key`, `GetForegroundWindow()` must equal it. Uses the binding that already existed in `platform/windows.py`.

### 2.4 Unlock waited on the orb — **FIXED**

- **Location:** `ui/index.html::unlock()` (`await bootOrb()` before `setLocked(false)`)
- **Severity:** High (user-facing latency; on this machine the shader compile was the measured ~20 s freeze earlier in the project)
- **Root cause:** `bootOrb()` is a CDN import of three.js plus a WebGL scene build. It sat between "your face is verified" and "you can talk to me".
- **Verification:** the Playwright prompt tests (`round-trips`, `brain is down`, `slow brain`) failed with the old ordering and pass after it — with the suite made hermetic (see §5).
- **Fix:** `bootOrb().catch(()=>{})` — kicked off, not awaited. `bootOrb` is idempotent (`if(orbApi||orbBooting||!LIB)return`), so nothing else changes.

### 2.5 Presence re-lock ignored a disabled gate — **FIXED**

- **Location:** `ui/index.html::startPresence()` (`RELOCK_MS = 120000`)
- **Severity:** Medium
- **Root cause:** after unlock, a presence watcher re-locks the room after two minutes without a face. It ran whenever a camera existed, with no check of `AUTH.gate`. `--bypass-face` means "do not gate on my face"; re-locking for not seeing a face is the same gate under another name.
- **Verification:** OBSERVED in headless runs (fake camera → no face → re-lock); guarded by the e2e boot test under `gate:false`.
- **Fix:** `if(AUTH&&AUTH.gate===false){ $("sidefacet").textContent="off"; return; }` at the top of `startPresence`. The camera stays for sight and gestures.

### 2.6 Speech-engine default persisted before "auto" existed — **FIXED**

- **Location:** `ui/index.html` (`STT_MODE` initialiser, `toggleSTT`)
- **Severity:** Medium (functional)
- **Root cause:** the load-time `setSTTMode(STT_MODE)` persisted the then-default `"browser"`. When `"auto"` (headset via Deepgram when attached) was introduced, every existing profile already had `"browser"` stored and never got the new behaviour — the headset fix silently did not apply.
- **Verification:** OBSERVED live 2026-09-02: the transcript header read `voice: browser` on a profile that had never been switched, with `listening on Microphone Array (Intel® SST)`.
- **Fix:** a stored value counts only if the boss chose it by clicking (`fridaySTTChosen=1`); otherwise the mode is `auto`.

### 2.7 No rate limit / backpressure on paid endpoints — **proposed**

- **Location:** `friday/ui_server.py` — `api_ask`, `api_tts`, `api_stt`
- **Severity:** Medium (spend and latency, not availability)
- **Root cause:** none of the three has a concurrency cap or per-client budget. `grep -nE "Semaphore|RateLimit|throttle"` over the file returns nothing.
- **Verification:** open N parallel `POST /api/ask`; all N reach Gemini.
- **Proposed fix** (not applied — behaviour change on a live path; needs the owner's go-ahead on the numbers):

```python
# friday/ui_server.py
_ASK_SLOTS = asyncio.Semaphore(int(os.getenv("FRIDAY_ASK_CONCURRENCY", "2")))
_TTS_SLOTS = asyncio.Semaphore(int(os.getenv("FRIDAY_TTS_CONCURRENCY", "2")))
_STT_SESSIONS = asyncio.Semaphore(int(os.getenv("FRIDAY_STT_SESSIONS", "1")))

async def api_ask(request):
    if _ASK_SLOTS.locked():                      # do not queue a talkative client
        return JSONResponse({"error": "busy, try again in a moment"}, status_code=429,
                            headers={"Retry-After": "2"})
    async with _ASK_SLOTS:
        ...existing body...

async def api_stt(ws):
    if _STT_SESSIONS.locked():                   # one microphone stream at a time
        await ws.close(code=1013)                # "try again later"
        return
    async with _STT_SESSIONS:
        ...existing body...
```

### 2.8 Takeover plan state is a bare module dict — **proposed**

- **Location:** `friday/toolsets/desktop.py::_PLANS`
- **Severity:** Low (single user; `ABORT` is already a thread-safe `Event`)
- **Root cause:** `_PLANS` is mutated from request handlers; the MCP server may execute tools on worker threads.
- **Proposed fix:** `_PLANS_LOCK = threading.Lock()` around every `_PLANS[...] =`, `.pop`, `.clear`. Five call sites, mechanical.

### 2.9 Checked and **cleared** (no finding)

- **XSS in the transcript and control room.** `esc()` (line 410) is applied at every data site sampled: `_logRow` (transcript rows), and every `row(k, v, r)` caller that passes server data (`esc(r.request)`, `esc(f)`, `esc(r.subject)`, `esc(t.text)`, `esc(c.hermes.summary)` — lines 1103–1122); `k` and `r` are escaped inside `row()`. 27 `innerHTML` sites exist; the data-bearing ones were inspected, so this is *sampled*, not exhaustive.
- **Nonce replay / retarget.** `confirmation.book.consume` recomputes the fingerprint; REPRODUCED: a spent nonce is refused ("already been used"), an un-approved one is refused ("not been approved yet").

### 2.10 Pre-existing, not from this pass (each checked in isolation)

- `test_reachability` — 3 dead symbols in `fabric_process.py` / `fabric_service.py` (files unchanged vs HEAD).
- `test_upstream_lock` ×2 — `FileNotFoundError` on `Friday Stark Demo Main/06_schemas/UPSTREAM_LOCK_TEMPLATE.json`; that directory shows as deleted in the session's opening `git status`.
- `test_reflex::test_the_agent_warms_the_fast_path_at_startup` — passes in isolation; batch-ordering flake.

---

## Phase 3 — Upgrades worth building (injection points named)

1. **Accessibility-tree locate before vision** — `friday/toolsets/screen.py`, a strategy chain in front of `vision.locate_in_frame`: Windows UI Automation gives exact control rectangles for native and most Electron/browser UIs, removing the coordinate-guessing risk entirely and using the model only as fallback. This is the single highest-value upgrade to the screen powers.
2. **Semantic locate cache** — key `(screen sha256, target)`; a repeated "where is X" on an unchanged screen costs nothing. Fits in `screen_point` before the model call; `Frame.digest` already exists.
3. **Circuit breaker on the vision client** — wrap `vision._client()` calls: open after N consecutive failures/timeouts, half-open probe, `NOT_CONFIGURED` with an honest line while open. Complements the deadline added in 2.2.
4. **Spend meter in the doctor panel** — count Gemini/Deepgram calls and bytes per session; surface in `/api/doctor`. Turns 2.7 from a guess into a number.
5. **Wire `VoiceInputGate`** — it is built (detach audio input when every mic is muted) and dead; attach it in the entrypoint next to continuity.
6. **Responsive HUD** — only if a phone/tablet surface ever becomes a goal: the absolute panels (`.b-log`, `.b-mem`, `.b-org`, `.dock{min-width:360px}`) need a stacked layout under ~900 px (measured need: 808 px).

---

## Phase 4 — Trade-off matrix (measured where measured)

| Dimension | Before | After / proposed | Net gain — evidence |
|---|---|---|---|
| Gate coverage | HTTP only | HTTP + WebSocket | Closed a live bypass — test-proven |
| Vision calls | no deadline | 30 s deadline, one factory | Bounded worst case; good-path latency unchanged — test-proven config |
| Typing safety | focus-blind | focus must be where the last step left it | Eliminates wrong-window keystrokes — test-proven |
| Unlock latency | serialised behind CDN + shader compile | orb boots concurrently | Prompt tests fail→pass; wall-clock gain `UNKNOWN` (not timed) |
| Headset selection | pinned to legacy default | explicit-choice only | Observed live; fixes the reported "still using the laptop mic" |
| Paid endpoints | unbounded | semaphores + 429/1013 | Bounded spend; latency impact under normal use expected ≈0 — `UNKNOWN` until measured |
| Locate accuracy | vision only (one target measured: conf 0.95, tip offset 0,0) | UIA first, vision fallback | `UNKNOWN` across applications until measured |
| Memory | GBrain canonical | unchanged | N/A — no evidence supports a migration |
| Routing | keyword router + derived semantics | unchanged | N/A — regression-tested (`test_semantics`, `test_capability_routing`) |

---

## Phase 5 — Live validation

### Part A — Claude Chrome (what was executed, and how to repeat it)

Executed 2026-09-02 against the running UI, read-only:

1. `/chrome` → `tabs_context_mcp(createIfEmpty)` → `navigate http://127.0.0.1:8770` → wait 4–6 s → screenshot → `read_console_messages(onlyErrors)` → `read_network_requests("/api/")` → `read_page(interactive)`.
2. **Observed:** first load sat on the boot screen `PREPARING — warming up` with the real gate **locked** (correct). On reload the gate had recognised the owner: full HUD, status `listening`, header `Agent · 169 tools` (the new registration count), transcript panel with `voice:` and `lite:` toggles and the prompt textbox + `SEND`, camera capture bottom-right, **transcript and camera panels not overlapping**.
3. **Console:** zero errors with tracking armed across the load. **Network:** 16 `/api/` requests, all `200` (`auth/status`, `camera`, `camera/hold`, `state`, `org`, `memory_snapshot`, `deck`, `gate`, `control`, `os_map`, `graph`, `auth/recent`, `doctor`).
4. **Interactive checks to repeat after any UI change:** type a prompt in the textbox → expect a `you` row then a `friday` row; click `voice:` → cycles `auto → deepgram → browser`; click `lite:` → reloads into low-power mode; relaunch without `--bypass-face` → lock screen with the loading sweep.

Environmental note: during the audit another session modified `friday/ui_server.py`; the live server **re-locked** (every poll returned `423`, which the client correctly treats as "locked" — `jget/jpost`, lines 435–436) and later **went down** for ~2 minutes (`ERR_CONNECTION_REFUSED`). Both events were visible in the e2e results and are the reason the suite was made hermetic.

### Part B — Playwright (TypeScript), written and executed

Files: `tests/e2e/playwright.config.ts`, `tests/e2e/friday-ui.spec.ts`. Dynamic locators only (`getByRole`, `getByPlaceholder`, ids), web-first assertions, **no `waitForTimeout`**. Network is intercepted hermetically: a catch-all `**/api/**` → `{}` registered first, then `/api/auth/status` (unlocked), `/api/tts` (204), and per-test `/api/ask` for success / `500` / 2.5 s latency. Chromium runs with fake media devices so no permission prompt hangs.

Specs: boot unlocked + no own runtime errors · prompt round-trip (you / friday / `used screen_point` rows, box clears) · brain down → "could not reach my brain" · slow brain still answers · empty prompt sends nothing · engine toggle cycles and persists across reload · transcript never covered by the camera (bounding boxes) · locked gate shows the lock screen · mobile: no horizontal overflow.

**Results (against the live server):**

| Project | Result |
|---|---|
| desktop (Desktop Chrome) | **8 passed, 1 skipped** (the mobile-only overflow test) |
| mobile (Pixel 7) | **7 passed, 1 failed, 1 skipped** — fails `no horizontal page overflow`: `scrollWidth 808` vs viewport `412`. Every *functional* test passes at phone width; the layout is fullscreen-desktop by design (§3.6). |

Real-test rule, satisfied: the three prompt specs **failed** under the old `await bootOrb()` ordering and **pass** after the fix; the boot spec **failed** while the suite depended on the real gate state and **passes** once hermetic.

Commands:

```powershell
# from the repo root; the UI must be running (Friday.exe or scripts\run_ui.py)
npx playwright test -c tests/e2e/playwright.config.ts                      # both projects, headless
npx playwright test -c tests/e2e/playwright.config.ts --project=desktop    # desktop only
npx playwright test -c tests/e2e/playwright.config.ts --headed --project=desktop
npx playwright show-report playwright-report                               # HTML report
npx playwright show-trace test-results\<failed-test-dir>\trace.zip         # trace viewer on a failure
```

(Run these from PowerShell; the Git Bash `npx` shim on this machine mangles arguments.)

### Lower-level regression (this pass)

`tests/test_jarvis_screen.py` (31) + `tests/test_face_gate.py` (4): **35 passed**. Earlier in the cycle, the deterministic gate: 3 282 passed across three portions; the four remaining failures are the pre-existing ones in §2.10.

---

## Phase 6 — Prioritised checklist

**Phase 1 — critical blockers & safety (done in this pass)**
- [x] Gate WebSocket scope (`access.py`) — test-proven
- [x] Deadline on every vision call (`vision._client`) — test-proven
- [x] Focus check before synthetic typing (`desktop_step`) — test-proven
- [x] Unlock no longer waits on the orb (`unlock()`) — e2e-proven
- [x] Presence re-lock honours a disabled gate (`startPresence`)
- [x] Speech-engine default migration (`STT_MODE`)

**Phase 2 — architecture (evidence-gated, not yet applied)**
- [ ] Rate limits / concurrency caps on `/api/ask`, `/api/tts`, `/api/stt` (§2.7) — agree the numbers, then apply with a spend meter (§3.4) to measure
- [ ] `_PLANS` lock (§2.8)
- [ ] Circuit breaker on the vision client (§3.3)
- [ ] Memory: **measure** recall misses and p95 latency before any migration discussion

**Phase 3 — features**
- [ ] UIA locate ahead of vision (§3.1) — the real fix for coordinate risk
- [ ] Semantic locate cache (§3.2)
- [ ] Wire `VoiceInputGate` (§3.5)
- [ ] Responsive HUD only if a mobile surface becomes a goal (§3.6)

**Phase 4 — live validation**
- [x] Claude Chrome pass (Part A) — recorded above
- [x] Playwright desktop + mobile (Part B) — recorded above
- [ ] Re-run Part B in CI with the UI started by `webServer` in the config
- [ ] Live voice session to exercise the spoken path of the screen powers (still the main open item from `docs/plans/jarvis-screen/07-final.md`)

## Not verified in this pass

- Rate-limit proposal (§2.7) — not applied, not measured.
- Unlock latency improvement — fixed by construction; not timed on this machine.
- XSS review is sampled (data-bearing sites), not a full pass over all 27 `innerHTML` uses.
- Physical mobile devices — only Pixel-7 emulation.
