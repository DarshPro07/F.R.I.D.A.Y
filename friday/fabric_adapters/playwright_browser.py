"""
Playwright as the DOM-path computer-use backend (CUA-001).

BUILTIN, not ADAPTER: the code here is Friday's, and Playwright is a pip
dependency pinned by version range, not an upstream cloned and pinned to a
commit in `third_party/UPSTREAM_LOCK.json`.

The browser tools (`browser_*`, `friday/toolsets/web.py`) already drive
Playwright; this registers that same dependency with the fabric so the
action-path router can choose it, and so a machine without the package or the
Chromium build reports UNAVAILABLE with the typed cause from
`browser_readiness` instead of failing inside a tool.

Health is honest about what has been checked. Importing Playwright is cheap
and happens at start; launching Chromium is not, so the first `dom.probe`
(`browser_readiness.probe`, which launches and loads a page) is what proves the
browser build. Until then the backend is DEGRADED, not READY.
"""

from __future__ import annotations

import dataclasses
import importlib

from friday import action_path, browser_readiness, computer_use, fabric

PATHS = (action_path.DOM,)
PROBE = computer_use.probe_operation(action_path.DOM)

#: Bounds on the one caller-supplied argument. The probe URL is not
#: caller-supplied: a probe that loads a chosen address would be a fetch.
_MIN_TIMEOUT_MS, _MAX_TIMEOUT_MS = 1_000, 60_000


def start():
    try:
        importlib.import_module("playwright.sync_api")
    except Exception as exc:                       # noqa: BLE001 - classified
        raise browser_readiness.BrowserUnavailable(
            browser_readiness.classify_failure(exc)) from exc
    return {"readiness": None}


def stop(handle) -> None:
    return None


def health(handle) -> dict:
    readiness = handle["readiness"]
    if readiness is None:
        return {"state": fabric.DEGRADED,
                "detail": f"Playwright imports; the Chromium build is not "
                          f"probed yet (call {PROBE})"}
    if readiness.state == browser_readiness.BROWSER_READY:
        return {"state": fabric.READY, "detail": readiness.detail}
    return {"state": fabric.UNAVAILABLE,
            "detail": f"{readiness.state}: {readiness.detail}"}


def call(operation: str, handle, **arguments):
    if operation != PROBE:
        raise fabric.FabricError(f"playwright_browser has no operation {operation!r}")
    unknown = set(arguments) - {"timeout_ms"}
    if unknown:
        raise fabric.FabricError(
            f"{PROBE} takes only timeout_ms, not {sorted(unknown)}")
    timeout_ms = int(arguments.get("timeout_ms", 20_000))
    if not _MIN_TIMEOUT_MS <= timeout_ms <= _MAX_TIMEOUT_MS:
        raise fabric.FabricError(
            f"timeout_ms must be {_MIN_TIMEOUT_MS}..{_MAX_TIMEOUT_MS}, got {timeout_ms}")
    readiness = browser_readiness.probe(timeout_ms=timeout_ms)
    handle["readiness"] = readiness
    return dataclasses.asdict(readiness)


DESCRIPTOR = fabric.Provider(
    id="playwright_browser",
    family=computer_use.FAMILY,
    upstream="",
    operations=(PROBE,),
    risk="medium",
    license_mode=fabric.BUILTIN_LICENSE,
    integration_mode=fabric.BUILTIN,
    cost_class="cheap",
    notes="Friday's own wrapper over the Playwright package (Apache-2.0), "
          "which is a declared dependency (playwright>=1.59,<1.60 in "
          "pyproject.toml's browser extra), not a cloned upstream, so it has "
          "no commit pin in the lock. Serves the DOM path. Exposes the "
          "readiness probe only; browser actions stay in the policy-gated "
          "browser_* tools.",
)
