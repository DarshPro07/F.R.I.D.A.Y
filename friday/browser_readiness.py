"""
Whether FRIDAY can really drive a browser on this machine (PRD v7 BROW-001).

The owner's invariant (2026-09-29): the browser extra installed, its Chromium
build present, and a real launch that loads a harmless public page and reads
it back - all three - is BROWSER_READY. Importing Playwright is not readiness:
a package whose browser was never installed imports fine and fails at launch.

    python -m friday.browser_readiness        # probe, print the state

The probe page is held to the network guard like any other outbound request,
and every step has a timeout.
"""
from __future__ import annotations

import time
from dataclasses import asdict, dataclass

BROWSER_READY = "BROWSER_READY"
NOT_INSTALLED = "BROWSER_NOT_INSTALLED"
BINARY_MISSING = "BROWSER_BINARY_MISSING"
LAUNCH_FAILED = "BROWSER_LAUNCH_FAILED"

#: A harmless public page that exists to be fetched.
PROBE_URL = "https://example.com/"


@dataclass(frozen=True)
class Readiness:
    state: str
    detail: str
    version: str = ""
    title: str = ""
    seconds: float = 0.0
    #: Resident memory of the processes the probe started (the Playwright
    #: driver and Chromium), measured with the page open.
    memory_mb: float = 0.0


def _started_processes_mb() -> float:
    import psutil

    total = 0
    for child in psutil.Process().children(recursive=True):
        try:
            total += child.memory_info().rss
        except psutil.Error:
            continue
    return round(total / 1024**2, 1)


def probe(*, url: str = PROBE_URL, timeout_ms: int = 20_000) -> Readiness:
    """Launch the browser for real and read `url`; say what stood in the way."""
    from friday import netguard

    netguard.check(url)
    started = time.monotonic()
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        return Readiness(NOT_INSTALLED, f"Playwright is not installed ({exc}); install the "
                                        f"browser extra: uv pip install -e .[browser]")
    import importlib.metadata

    try:
        version = importlib.metadata.version("playwright")
    except importlib.metadata.PackageNotFoundError:
        version = "unknown"
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True, timeout=timeout_ms)
            try:
                page = browser.new_page()
                page.goto(url, timeout=timeout_ms)
                title = page.title()
                memory_mb = _started_processes_mb()
            finally:
                browser.close()
    except Exception as exc:  # noqa: BLE001 - every launch failure is reported, typed below
        reason = f"{type(exc).__name__}: {exc}"[:400]
        if "Executable doesn't exist" in str(exc):
            return Readiness(BINARY_MISSING, f"the Chromium build Playwright {version} drives is "
                                             f"missing ({reason}); run: python -m playwright "
                                             f"install chromium", version)
        return Readiness(LAUNCH_FAILED, reason, version)
    seconds = round(time.monotonic() - started, 2)
    if not title:
        return Readiness(LAUNCH_FAILED, f"Chromium launched but read nothing from {url}",
                         version, seconds=seconds, memory_mb=memory_mb)
    return Readiness(BROWSER_READY, f"launched Chromium and read {url}", version, title,
                     seconds, memory_mb)


def main() -> int:
    import json

    readiness = probe()
    print(json.dumps(asdict(readiness), indent=1))
    return 0 if readiness.state == BROWSER_READY else 1


if __name__ == "__main__":
    raise SystemExit(main())
