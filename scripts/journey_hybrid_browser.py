"""
One objective, a structured step and a real browser step (PRD v7 BROW-001,
section 48), with what the browser costs this machine (Amendment A4).

Run:  .venv-verify/Scripts/python.exe scripts/journey_hybrid_browser.py --out <file.json>

Needs the `browser` extra (Playwright) and its Chromium build, and the
network: it reads a public page. The objective fetches the page over HTTP
(native-API path), opens the same page in a headless browser and reads it
back through the DOM (DOM path), then closes the browser - four steps of one
objective, driven by the real executor and capability runtime. While it runs,
the machine's RAM and the browser processes' memory are sampled.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import tempfile
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

URL = "https://example.com/"
_BROWSER_NAMES = ("chrome", "chromium", "headless_shell")


class _Sampler:
    """RAM in use and the browser processes' resident memory, every 0.2 s."""

    def __init__(self) -> None:
        import psutil

        self.samples: list[tuple[float, float]] = []
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)
        # Browsers already running (the owner's own) are not the journey's.
        self._before = {proc.pid for proc in psutil.process_iter(["name"])
                        if any(b in (proc.info.get("name") or "").lower() for b in _BROWSER_NAMES)}

    def _browser_mb(self) -> float:
        import psutil

        total = 0
        for proc in psutil.process_iter(["name", "memory_info"]):
            name = (proc.info.get("name") or "").lower()
            if (proc.pid not in self._before and any(b in name for b in _BROWSER_NAMES)
                    and proc.info.get("memory_info")):
                total += proc.info["memory_info"].rss
        return total / 1024**2

    def _run(self) -> None:
        import psutil

        while not self._stop.is_set():
            used = psutil.virtual_memory().used / 1024**2
            self.samples.append((used, self._browser_mb()))
            self._stop.wait(0.2)

    def __enter__(self):
        self._thread.start()
        return self

    def __exit__(self, *exc):
        self._stop.set()
        self._thread.join(timeout=5)


def run(url: str = URL, *, timeout_s: float = 90.0) -> dict:
    # Everything this run writes stays in its own folder: the store (where
    # its network grant lives) and the audit log. Set before FRIDAY loads.
    workdir = Path(tempfile.mkdtemp(prefix="friday-journey-"))
    os.environ["ADA_DB"] = str(workdir / "journey.sqlite3")
    os.environ["ADA_AUDIT_DB"] = str(workdir / "audit.sqlite3")
    os.environ.setdefault("ADA_BROWSER_HEADLESS", "1")
    import psutil

    from friday import action_path
    from friday import capability_runtime as CR
    from friday import contracts as c
    from friday import objectives as O
    from friday import policy as PO
    from friday.continuous import ContinuousTaskExecutor
    from friday.store import Store
    from friday.toolsets import objectives as OT

    store = Store(os.environ["ADA_DB"])
    OT.reset_store(store)
    tasks = [
        {"id": "t1", "capability": "web_fetch", "arguments": {"url": url}},
        {"id": "t2", "capability": "browser_open", "arguments": {"url": url},
         "dependencies": ["t1"]},
        {"id": "t3", "capability": "browser_inspect", "arguments": {}, "dependencies": ["t2"]},
        {"id": "t4", "capability": "browser_close", "arguments": {}, "dependencies": ["t3"]},
    ]
    run_id = O.compile_objective(
        store, request=f"read {url} over the web and in the browser",
        objective_summary="read a page two ways", tasks=tasks,
        manifest=[{"id": t["capability"], "description": t["capability"]} for t in tasks],
        provenance=c.PERSON, attended=True)["run_id"]
    dispatch = CR.build_dispatch(engine=PO.PolicyEngine(autonomy=PO.FULL))
    before = psutil.virtual_memory().used / 1024**2

    async def drive():
        executor = ContinuousTaskExecutor(store, call_capability=dispatch)
        try:
            loop = asyncio.get_running_loop()
            deadline = loop.time() + timeout_s
            while loop.time() < deadline:
                status = store.objective_run(run_id)["status"]
                if status in O.RUN_TERMINAL or status in O.RUN_WAITING_STATUSES:
                    return
                await executor.start(run_id)
                await asyncio.sleep(0.05)
        finally:
            executor.stop()

    started = time.monotonic()
    with _Sampler() as sampler:
        asyncio.run(drive())
    seconds = time.monotonic() - started
    time.sleep(1.0)
    after = psutil.virtual_memory().used / 1024**2

    steps = []
    for task in store.objective_tasks(run_id):
        result = task.get("result") or {}
        output = result.get("output") if isinstance(result, dict) else {}
        text = json.dumps(output, default=str) if output else ""
        steps.append({"capability": task["capability"],
                      "path": action_path.path_of(task["capability"]),
                      "status": task["status"],
                      "saw_the_page": "Example Domain" in text,
                      "evidence": str(task.get("evidence") or "")[:200]})
    peak_used = max((s[0] for s in sampler.samples), default=before)
    peak_browser = max((s[1] for s in sampler.samples), default=0.0)
    return {"url": url, "run_status": store.objective_run(run_id)["status"],
            "seconds": round(seconds, 2), "steps": steps,
            "resources": {"ram_used_before_mb": round(before), "ram_used_peak_mb": round(peak_used),
                          "ram_used_after_mb": round(after),
                          "peak_over_start_mb": round(peak_used - before),
                          "browser_processes_peak_mb": round(peak_browser),
                          "ram_total_mb": round(psutil.virtual_memory().total / 1024**2)}}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--out", required=True)
    parser.add_argument("--url", default=URL)
    args = parser.parse_args(argv)
    result = run(args.url)
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(result, indent=1), encoding="utf-8")
    print(json.dumps(result, indent=1))
    ok = result["run_status"] == "COMPLETED" and all(s["status"] == "SUCCEEDED" for s in result["steps"])
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
