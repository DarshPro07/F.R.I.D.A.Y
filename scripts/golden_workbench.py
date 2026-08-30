#!/usr/bin/env python3
"""
"Create a coffee shop website and show me."

The asked-for version was a Sandpack preview embedded in Friday's chat UI.
Friday has no chat UI - no package.json, no HTML, no frontend of any kind.
So the preview surface is the browser he already uses, which is a better one
anyway: it is the browser the site will actually be viewed in.

    generate -> validate -> serve on loopback -> open in HIS Chrome -> iterate

    python scripts/golden_workbench.py
"""

from __future__ import annotations

import asyncio
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env")

from friday import contracts as c  # noqa: E402
from friday.toolsets import workbench as W  # noqa: E402

PROJECT = "coffee-shop"

PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<title>Kettle &amp; Co</title><link rel="stylesheet" href="css/main.css"></head>
<body>
  <header class="hero">
    <h1>KETTLE &amp; CO</h1>
    <p>Freshly roasted. Every morning.</p>
    <a class="cta" href="#order">Order Now</a>
  </header>
  <main><section id="order"><h2>Today's roast</h2>
  <p>Ethiopian single origin, ground to order.</p></section></main>
</body></html>
"""

CSS = """:root { --ink: #f5efe6; --ground: #2b1d16; }
* { box-sizing: border-box; margin: 0; }
body { font-family: system-ui, sans-serif; background: var(--ground); color: var(--ink); }
.hero { min-height: 70vh; display: grid; place-content: center; text-align: center; gap: 1rem; }
.hero h1 { font-size: clamp(2rem, 8vw, 5rem); letter-spacing: .18em; }
.cta { display: inline-block; padding: .8rem 2rem; border: 1px solid var(--ink);
       color: var(--ink); text-decoration: none; }
main { padding: 3rem; }
"""

DARKER = CSS.replace("--ground: #2b1d16;", "--ground: #140d09;")


def check(passed: bool, message: str) -> bool:
    print(f"  [{'PASS' if passed else 'FAIL'}] {message}")
    return passed


def run_for(label: str) -> c.Run:
    return c.Run.create(label, capability="workbench")


async def journey() -> list[bool]:
    results: list[bool] = []
    with tempfile.TemporaryDirectory() as tmp:
        W.ROOT = Path(tmp) / "workbench"      # never touch the real one
        print(f"  workbench root: {W.ROOT}\n")

        print("=" * 70)
        print('[you] "Create a coffee shop website and show me."')
        print("=" * 70)

        # A preview before anything exists must refuse, not open a blank page.
        early = await W.workbench_preview(run_for("early"), PROJECT)
        print(f"  preview before writing -> {early.status}: {(early.error or '')[:60]}")
        results.append(check(early.status == c.FAILED,
                             "an empty project cannot be shown"))

        await W.workbench_write(run_for("html"), PROJECT, "index.html", PAGE)

        # index.html references css/main.css, which does not exist yet.
        half = await W.workbench_preview(run_for("half"), PROJECT)
        print(f"  preview with a missing stylesheet -> {half.status}: "
              f"{(half.error or '')[:80]}")
        results.append(check(half.status == c.FAILED,
                             "a missing stylesheet is caught before he looks"))

        await W.workbench_write(run_for("css"), PROJECT, "css/main.css", CSS)

        result = await W.workbench_preview(run_for("preview"), PROJECT)
        output = result.output or {}
        print(f"\n  status={result.status}")
        print(f"  url   : {output.get('url')}")
        print(f"  files : {output.get('files')}")
        if result.verification:
            print(f"  proof : {result.verification.evidence}\n")

        results += [
            check(result.status == c.SUCCEEDED, "the site is served"),
            check(bool(output.get("url", "").startswith("http://127.0.0.1:")),
                  "on loopback, not exposed to the network"),
            check(result.verification is not None,
                  "and it was fetched back, not just started"),
        ]

        # The real point: it opens where he can see it, signed in as himself.
        from friday import browser_profiles as BP

        profile = BP.last_used()
        opened, message = (BP.open_url(output["url"], profile) if profile
                           else (False, "no browser profile"))
        print(f"  opened: {message}\n")
        results.append(check(opened, "opened in his own browser, not a blank one"))

        print("=" * 70)
        print('[you] "Make the hero darker."')
        print("=" * 70)
        await W.workbench_write(run_for("edit"), PROJECT, "css/main.css", DARKER)

        import httpx

        served = httpx.get(output["url"] + "css/main.css", timeout=10).text
        print(f"  the served stylesheet now has: "
              f"{[l for l in served.splitlines() if '--ground' in l]}\n")
        results.append(check("#140d09" in served,
                             "the edit is live on the same url - no restart"))

        listing = await W.workbench_list(run_for("list"), PROJECT)
        print(f"  project files: {(listing.output or {}).get('files')}")
        print(f"  ready        : {(listing.output or {}).get('ready')}\n")
        results.append(check((listing.output or {}).get("ready") is True,
                             "the project reports itself ready"))

        stopped = await W.workbench_stop(run_for("stop"), PROJECT)
        print(f"  stopped: {(stopped.output or {}).get('was_running')}")
        results.append(check((stopped.output or {}).get("was_running") is True,
                             "the server stops on request - nothing left listening"))

        try:
            httpx.get(output["url"], timeout=3)
            gone = False
        except Exception:
            gone = True
        results.append(check(gone, "and it really is gone"))
        W.stop_all()
    return results


def main() -> int:
    results = asyncio.run(journey())
    passed = sum(1 for r in results if r)
    print("\n" + "=" * 70)
    print(f"RESULT: {passed}/{len(results)} checks passed")
    print("=" * 70)
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
