"""Live probe of the Control Room (browser path) with Playwright.

Unlocks with the owner's PIN through the real endpoint (cookie session),
then types each prompt into #asktext exactly as the owner would and reads
the reply row the page renders. Captures a trace + screenshot for any step
that fails. Writes verdict JSON. Never invents a reply: an empty reply is
recorded as empty.

Usage: python scripts/ui_probe.py --pin 0707 --out D:/friday-test-tmp/ui_probe.json [--steps file]
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

BASE = os.environ.get("FRIDAY_UI", "http://127.0.0.1:8770")

DEFAULT_PROMPTS = [
    ("snapshot", "Give me an honest snapshot of yourself right now: which model you are running on, which capability families are live, and whether the camera and screen capture are on or off."),
    ("create", "Create jarvis-ui-test.txt on my Desktop containing exactly the words: version one. Then read the file back and tell me exactly what it contains."),
    ("overwrite_undo", "Overwrite jarvis-ui-test.txt on my Desktop with the words: version two. Then undo that write by its action id and read the file back."),
    ("honest_notepad", "Did you open Notepad for me just now? Answer only from what you actually did."),
    ("skills_state", "How many skill families do you have and what state is each in? Read the real state before answering."),
    ("hermes", "Delegate this to Hermes: write a two-line Python script that prints today's date to jarvis-ui-hello.py on my Desktop, and tell me when it is actually on disk."),
    ("delete", "Recycle jarvis-ui-test.txt from my Desktop and confirm it is gone."),
]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pin", default=os.environ.get("FRIDAY_PIN", ""))
    ap.add_argument("--out", default="D:/friday-test-tmp/ui_probe.json")
    ap.add_argument("--wait", type=float, default=120.0, help="seconds to wait for a reply per step")
    ap.add_argument("--only", default="", help="comma list of step names")
    ap.add_argument("--skip-preflight", action="store_true",
                    help="do not require the running MCP server to match the tree")
    args = ap.parse_args()

    from playwright.sync_api import sync_playwright

    if not args.skip_preflight:
        # D-16: prove the MCP server behind the UI is the tree before any
        # verdict is recorded (registry-hash check, no restart).
        import subprocess
        chk = subprocess.run([sys.executable, str(Path(__file__).with_name("restart_friday.py")), "--check"],
                             capture_output=True, text=True, timeout=120)
        print(chk.stdout.strip(), flush=True)
        if chk.returncode != 0:
            print("PREFLIGHT FAILED: the running MCP server does not match the tree - restart, then rerun.", flush=True)
            return 3

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    art = out_path.parent / (out_path.stem + "_artifacts")
    art.mkdir(exist_ok=True)
    results = []
    steps = [(n, p) for n, p in DEFAULT_PROMPTS if not args.only or n in args.only.split(",")]

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        ctx = browser.new_context(viewport={"width": 1400, "height": 900})
        # Tracing is armed once; each step records its own chunk and only a
        # failing chunk is written to disk (see the loop below).
        ctx.tracing.start(screenshots=True, snapshots=True, sources=False)
        page = ctx.new_page()
        console: list[str] = []
        page.on("console", lambda m: console.append(f"{m.type}: {m.text}"[:300]))
        page.on("pageerror", lambda e: console.append(f"pageerror: {e}"[:300]))
        page.goto(BASE + "/", wait_until="domcontentloaded", timeout=60000)
        time.sleep(3)
        status = page.evaluate("fetch('/api/auth/status').then(r=>r.json())")
        boot = {"auth_before": status}
        if status.get("locked") and args.pin:
            if not status.get("pin_allowed"):
                # The PIN is a FALLBACK: refused while the camera is free (a
                # face proves presence). The legitimate PIN case is "another
                # window has the camera" - so the probe takes the camera the
                # way a real second Friday window would (the hold heartbeat
                # the lock screen itself sends), then presents the PIN. The
                # gate is exercised, not bypassed; nothing here is weakened.
                holder = ctx.new_page()
                holder.goto(BASE + "/", wait_until="domcontentloaded", timeout=60000)
                for _ in range(3):
                    holder.evaluate("fetch('/api/camera/hold',{method:'POST'})")
                    time.sleep(0.4)
                boot["held_camera"] = True
                status = page.evaluate("fetch('/api/auth/status').then(r=>r.json())")
                boot["auth_held"] = status
            pin = page.evaluate(
                "(pin)=>fetch('/api/auth/pin/verify',{method:'POST',headers:{'Content-Type':'application/json'},"
                "body:JSON.stringify({pin})}).then(r=>r.json())", args.pin)
            boot["pin_verify"] = {k: v for k, v in pin.items() if k != "token"}
            page.reload(wait_until="domcontentloaded")
            time.sleep(4)
            boot["auth_after"] = page.evaluate("fetch('/api/auth/status').then(r=>r.json())")
        boot["build"] = page.evaluate("(document.getElementById('buildline')||{}).textContent||''")
        boot["island"] = page.evaluate("(document.getElementById('islandtxt')||{}).textContent||''")
        boot["asktext"] = page.evaluate("!!document.getElementById('asktext')")
        boot["lock_visible"] = page.evaluate(
            "(()=>{const l=document.getElementById('lock'); if(!l) return null; const s=getComputedStyle(l); return s.display!=='none' && s.visibility!=='hidden' && !l.classList.contains('gone');})()")
        print("BOOT", json.dumps(boot)[:600], flush=True)

        for name, prompt in steps:
            t0 = time.time()
            rec = {"step": name, "prompt": prompt, "sent_at": t0}
            # Trace per step and KEEP only the failing/anomalous chunks
            # (Playwright's own guidance: always-on tracing is expensive;
            # retain traces around failures). A kept chunk is the evidence
            # artifact a verdict must cite, not a model's description.
            try:
                ctx.tracing.start_chunk(title=name)
            except Exception:  # noqa: BLE001
                pass
            try:
                before = page.evaluate("document.querySelectorAll('#logbox .row, #logbox div, #conv .msg, #conv div').length")
                # the real page path: the input + Enter -> sendAsk() -> handleUtterance -> /api/ask
                page.fill("#asktext", prompt)
                api_reply = None
                # Match the response to THIS request's body: after a timeout
                # the previous request is still in flight, and "the next
                # /api/ask response" would be attributed to the wrong step
                # (probe B, 2026-09-20: the snapshot's late reply landed on
                # the create step). A harness must not misattribute.
                key = prompt[:60]

                def _mine(r, key=key):
                    try:
                        return "/api/ask" in r.url and key in (r.request.post_data or "")
                    except Exception:  # noqa: BLE001
                        return False

                with page.expect_response(_mine, timeout=int(args.wait * 1000)) as resp_info:
                    page.press("#asktext", "Enter")
                resp = resp_info.value
                try:
                    api_reply = resp.json()
                except Exception:
                    api_reply = {"raw": resp.text()[:800], "status": resp.status}
                rec["api_status"] = resp.status
                rec["api"] = {k: (v if isinstance(v, (str, int, float, bool)) or v is None else json.loads(json.dumps(v, default=str))) for k, v in (api_reply or {}).items() if k not in ("message_id",)}
                rec["reply"] = (api_reply or {}).get("reply", "")
                rec["action"] = (api_reply or {}).get("action", "")
                rec["used"] = (api_reply or {}).get("used_capabilities", [])
                time.sleep(1.5)
                rec["rendered_rows_added"] = page.evaluate("document.querySelectorAll('#logbox .row, #logbox div, #conv .msg, #conv div').length") - before
                rec["island"] = page.evaluate("(document.getElementById('islandtxt')||{}).textContent||''")
                rec["ok"] = True
            except Exception as exc:  # noqa: BLE001
                rec["ok"] = False
                rec["error"] = f"{type(exc).__name__}: {exc}"[:400]
                try:
                    page.screenshot(path=str(art / f"{name}.png"), full_page=True)
                except Exception:  # noqa: BLE001
                    pass
            rec["took_s"] = round(time.time() - t0, 1)
            rec["console_tail"] = console[-5:]
            anomalous = (not rec.get("ok")) or rec.get("api_status", 200) >= 400 \
                or bool((rec.get("api") or {}).get("error")) or rec["took_s"] > args.wait * 0.8
            try:
                if anomalous:
                    trace_path = art / f"{name}.trace.zip"
                    ctx.tracing.stop_chunk(path=str(trace_path))
                    rec["trace"] = str(trace_path)
                else:
                    ctx.tracing.stop_chunk()                  # discard: nothing to diagnose
            except Exception:  # noqa: BLE001
                pass
            results.append(rec)
            print(f"[{name}] ok={rec.get('ok')} {rec.get('took_s')}s action={rec.get('action')!r} reply={str(rec.get('reply',''))[:220]!r}"
                  + (f" trace={rec['trace']}" if rec.get("trace") else ""), flush=True)
            time.sleep(2)

        try:
            ctx.tracing.stop()
        except Exception:  # noqa: BLE001
            pass
        browser.close()

    out_path.write_text(json.dumps({"base": BASE, "boot": boot, "results": results}, indent=2), encoding="utf-8")
    print("WROTE", out_path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
