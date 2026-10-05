# Plan: CUA-001 computer-use backend interface

**Date:** 2026-10-05
**Status:** In Progress (build-kernel run; no owner available to approve, so the
assumptions are stated here instead)
**Services:** fabric, mcp-tools (read-only reuse of `toolsets/desktop.py` seams)

## Objective
One interface serves the Playwright browser backend and a desktop backend. Each
registers through the capability fabric; the action-path router picks one by the
path a capability acts through; a backend that is missing reports UNAVAILABLE and
the others keep working.

## Features
### 1. `friday/computer_use.py` - the interface and the router
- **Service:** fabric
- **Small win:** `route("browser_click")` names the DOM backend, `route("desktop_step")`
  names the coordinates backend, and a dead one is reported with its reason.
- **DOD:**
  - [ ] `Backend` protocol (start/stop/health/call + PATHS), `conforms()` check
  - [ ] `route()` walks `fabric.candidates` for `<path>.probe`, activating each
  - [ ] new fabric family `computer_use`

### 2. Two backends as fabric adapters
- `fabric_adapters/playwright_browser.py` - DOM path, wraps `browser_readiness`
- `fabric_adapters/native_desktop.py` - coordinates path, wraps `platform.windows`
  (health is read-only: `screen_size()`; actions stay behind the policy-gated
  `desktop_*` tools, the backend adds no new action surface)

## Assumptions (revisit if wrong)
- Cua Driver is **not** done: the lock has no standalone `cua-driver` entry, only
  a nested `third_party/cua-driver/LICENSE.md` (MIT) inside OpenMausBot. A SHA
  cannot be invented, so it needs the owner to request and pin it
  (`scripts/upstream_lock.py`), then a third adapter serves the same interface.
- `action_path.py` only orders; it never called Playwright or `native.*`, so the
  scout's "stop calling them directly" does not apply. The router is the new layer.
- VISION is not a computer-use path yet: `vision_*` are read-only toolsets, and
  routing them would wrongly mark them unavailable.
- REFERENCE-A measurement needs the live machine and is left to the owner.
