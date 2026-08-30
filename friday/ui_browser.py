"""
friday/ui_browser.py -- the UI's own persistent, headed browser, driven and gated.

This is the "replace the ChatGPT browser" surface: a headed Chromium with a
PERSISTENT profile (logins survive between runs), driven from the control room.

Every navigation passes friday.browser_capability, which blocks banking /
authenticated-financial pages and private network addresses BEFORE any capture
(no DOM text, no screenshot of a blocked page) and redacts secret-shaped text
from anything read. Every state-changing action (click / type / submit) raises
a friday.confirmation gate bound to the EXACT action; nothing changes the page
without an approved, unexpired, single-use yes. Reads are ungated.

Playwright handles are loop/thread-affine, so the browser lives on ONE dedicated
worker thread; HTTP handlers hand it a callable and wait. It runs inside the UI
server process, so the confirmation Book and the code that acts are the same
process -- which is exactly what confirmation.Book requires (it is deliberately
in-memory, per-process). The live LiveKit agent is untouched.
"""
from __future__ import annotations

import logging
import os
import queue
import threading
import time
from pathlib import Path

from friday import browser_capability as bc
from friday import confirmation as C

logger = logging.getLogger("friday-agent.ui_browser")

_PROFILE_DIR = Path(os.getenv("FRIDAY_BROWSER_PROFILE",
                              str(Path.home() / ".friday" / "browser-profile")))
_SHOT_DIR = Path(os.getenv("FRIDAY_BROWSER_SHOTS",
                           str(Path(__file__).resolve().parent.parent /
                               "data" / "browser_shots")))

# Conservative default: any action that changes page state is gated. Reads
# (open_url) are not. The owner can relax this later; dry-run stays the default.
_GATED_KINDS = ("click", "type", "submit", "download", "post", "send")

_book = C.Book()                 # process-local confirmations (this process acts)
_events = []                     # browser.action / gate.* frames for the SSE
_events_lock = threading.Lock()


def _headless() -> bool:
    return os.getenv("FRIDAY_BROWSER_HEADLESS", "false").strip().lower() in (
        "1", "true", "yes")


def _emit(kind, payload):
    with _events_lock:
        _events.append({"type": kind, "at": time.time(), "payload": payload})
        del _events[:-200]


def drain_events(after_ts=0.0):
    with _events_lock:
        return [e for e in _events if e["at"] > after_ts]


class _Worker:
    """One thread that owns the Playwright persistent context."""

    def __init__(self):
        self._q = queue.Queue()
        self._thread = None
        self._boot_error = None
        self._page = None

    def _ensure(self):
        if self._thread is None:
            self._thread = threading.Thread(target=self._loop, daemon=True,
                                            name="friday-browser")
            self._thread.start()

    def _loop(self):
        try:
            from playwright.sync_api import sync_playwright
            self._pw = sync_playwright().start()
            _PROFILE_DIR.mkdir(parents=True, exist_ok=True)
            self._ctx = self._pw.chromium.launch_persistent_context(
                str(_PROFILE_DIR), headless=_headless(),
                viewport={"width": 1380, "height": 860})
            self._page = (self._ctx.pages[0] if self._ctx.pages
                          else self._ctx.new_page())
        except Exception as exc:  # noqa: BLE001
            self._boot_error = exc
            logger.warning("browser boot failed: %s", exc)
            return
        while True:
            fn, box = self._q.get()
            if fn is None:
                break
            try:
                box["result"] = fn(self._page)
            except Exception as exc:  # noqa: BLE001
                box["error"] = exc
            finally:
                box["done"].set()

    def call(self, fn, timeout=45):
        self._ensure()
        box = {"done": threading.Event()}
        self._q.put((fn, box))
        if not box["done"].wait(timeout):
            if self._boot_error is not None:
                raise RuntimeError("browser did not start: %s" % self._boot_error)
            raise TimeoutError("browser op timed out")
        if "error" in box:
            raise box["error"]
        return box.get("result")

    @property
    def running(self):
        return self._page is not None


_worker = _Worker()


def _snap(page):
    _SHOT_DIR.mkdir(parents=True, exist_ok=True)
    name = "shot-%d.png" % int(time.time() * 1000)
    page.screenshot(path=str(_SHOT_DIR / name))
    return {"screenshot": name, "title": page.title(), "final_url": page.url}


def open_url(url):
    """Open a URL in the persistent headed browser -- a READ, policy-gated.

    browser_capability.observe_page runs classify_url FIRST and only calls the
    capture callable for an allowed page, so a banking/auth/private URL is never
    navigated-into for capture. A redirect INTO a bank raises SensitiveRedirect
    from inside capture and is reported blocked with zero content.
    """
    def capture(page):
        page.goto(url, wait_until="domcontentloaded", timeout=30000)
        final = page.url
        verdict = bc.classify_url(final)
        if verdict.decision == bc.BLOCK_SENSITIVE:
            raise bc.SensitiveRedirect(
                "BLOCKED_SENSITIVE_DOMAIN: redirected to %s" % final)
        return page.inner_text("body")

    out = bc.observe_page(url, lambda: _worker.call(lambda page: capture(page)))
    out["url"] = url
    if out.get("status") == "ok":
        try:
            out.update(_worker.call(_snap))
        except Exception as exc:  # noqa: BLE001
            out["screenshot_error"] = str(exc)[:120]
    _emit("browser.action", {"action": "open", "url": url,
                             "status": out.get("status"),
                             "verdict": out.get("verdict", ""),
                             "screenshot": out.get("screenshot", "")})
    return out


def request_act(kind, selector, text=""):
    """Ask to change page state. Returns a gate (confirmation) to approve."""
    kind = (kind or "").lower()
    if kind not in _GATED_KINDS:
        return {"gated": False, "error": "unknown or non-actionable kind %r" % kind}
    conf = _book.ask(run_id="ui-browser", action="browser.%s" % kind,
                     target=selector,
                     arguments={"text": text} if text else {},
                     question="Approve %s on %s?" % (kind, selector))
    _emit("gate.raised", {"nonce": conf.nonce, "action": conf.action,
                          "target": selector, "question": conf.question})
    return {"gated": True, **conf.to_dict()}


def approve_act(nonce):
    v = _book.approve(nonce)
    if not v.ok:
        return {"ok": False, "reason": v.reason}
    conf = v.confirmation
    # Consume against the EXACT action -- the fingerprint is recomputed here.
    cv = _book.consume(nonce, run_id=conf.run_id, action=conf.action,
                       target=conf.target, arguments=conf.arguments)
    if not cv.ok:
        return {"ok": False, "reason": cv.reason}
    kind = conf.action.split(".", 1)[-1]
    selector, text = conf.target, conf.arguments.get("text", "")

    def do(page):
        if kind == "type":
            page.fill(selector, text)
        elif kind in ("click", "submit"):
            page.click(selector)
        else:
            raise RuntimeError("action %r not performable yet" % kind)
        return _snap(page)

    try:
        shot = _worker.call(do)
    except Exception as exc:  # noqa: BLE001
        _emit("gate.resolved", {"nonce": nonce, "resolved": "approved_but_failed",
                                "error": str(exc)[:120]})
        return {"ok": False, "reason": "action failed: %s" % str(exc)[:120]}
    _emit("gate.resolved", {"nonce": nonce, "resolved": "approved",
                            "action": conf.action})
    return {"ok": True, "performed": conf.action, **shot}


def reject_act(nonce, reason=""):
    _book.refuse(nonce)
    _emit("gate.resolved", {"nonce": nonce, "resolved": "rejected",
                            "reason": reason})
    return {"ok": True}


def pending_gates():
    now = C._now()
    return [c.to_dict() for c in list(_book.pending.values())
            if c.state == C.PENDING and not c.expired(now)]


def shot_path(name):
    """Resolve a screenshot name to a path, refusing anything but a shot file."""
    if not name or "/" in name or "\\" in name or not name.startswith("shot-"):
        return None
    p = _SHOT_DIR / name
    return p if p.exists() else None


def status():
    return {"running": _worker.running, "headless": _headless(),
            "profile": str(_PROFILE_DIR), "pending_gates": len(pending_gates())}
