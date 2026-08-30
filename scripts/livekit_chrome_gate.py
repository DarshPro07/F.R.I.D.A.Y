"""
Prove the LiveKit path works in a real browser, not just in a Python client.

Run:  .venv/Scripts/python.exe scripts/livekit_chrome_gate.py "Reply with exactly: FRIDAY_CHROME_OK"

## Why this is not the hosted playground

`agents-playground.livekit.io` now redirects to `cloud.livekit.io/login`. That
is the user's sign-in and their MFA, and it is a true stop boundary - measured,
not assumed: Chrome lands on "Sign in | LiveKit Cloud" with the playground
never rendering.

What the playground *is*, though, is a browser page holding a LiveKit room: it
publishes on the `lk.chat` text stream and renders `lk.transcription` back.
This serves exactly that page from localhost with a freshly minted token, and
drives it with the real Chrome installed on this machine. Same SDK, same
transport, same worker, same production entrypoint - and a DOM, a console and a
network log a human's browser would also produce.

It is the difference between "the Python client got an answer" and "a browser
rendered one", which is the claim the validation actually needs.

## What it checks

  page          the room reaches `connected` in the browser's own state
  transcript    Friday's reply appears in the DOM, not merely in a callback
  console       browser console errors are captured and reported
  network       failed requests are captured and reported

A reply that arrives but never reaches the DOM is a UI defect and is reported
as one. Screenshots are not taken: the assertion is about text in the document,
which the accessibility tree answers more cheaply and more precisely than
vision.
"""

from __future__ import annotations

import argparse
import http.server
import json
import os
import pathlib
import sys
import threading
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv                                   # noqa: E402
from livekit import api                                          # noqa: E402

load_dotenv()

ROOT = pathlib.Path(__file__).resolve().parent.parent
#: Chrome as actually installed here. The x86 path is where this machine has it.
CHROME_CANDIDATES = (
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
)
IDENTITY = "friday-chrome-probe"

PAGE = """<!doctype html>
<html><head><meta charset="utf-8"><title>Friday LiveKit gate</title></head>
<body>
<h1 id="heading">Friday LiveKit gate</h1>
<div id="state">connecting</div>
<ul id="transcript"></ul>
<script src="https://cdn.jsdelivr.net/npm/livekit-client@2/dist/livekit-client.umd.min.js"></script>
<script>
const CONFIG = __CONFIG__;
const stateEl = document.getElementById('state');
const listEl = document.getElementById('transcript');
window.__errors = [];
window.addEventListener('error', e => window.__errors.push(String(e.message)));

function line(who, text) {
  const li = document.createElement('li');
  li.className = 'msg';
  li.dataset.who = who;
  li.textContent = text;
  listEl.appendChild(li);
}

(async () => {
  try {
    const room = new LivekitClient.Room();
    room.registerTextStreamHandler('lk.transcription', async (reader, info) => {
      const text = await reader.readAll();
      if (info.identity === CONFIG.identity) return;
      line(info.identity, text);
    });
    room.on(LivekitClient.RoomEvent.Disconnected, () => { stateEl.textContent = 'disconnected'; });
    await room.connect(CONFIG.url, CONFIG.token);
    stateEl.textContent = 'connected';
    // Wait for Friday's greeting before typing: the chat handler is registered
    // after session.start(), and a message sent into that window is dropped.
    const deadline = Date.now() + CONFIG.readyMs;
    while (Date.now() < deadline && listEl.children.length === 0) {
      await new Promise(r => setTimeout(r, 300));
    }
    window.__greetings = listEl.children.length;
    await room.localParticipant.sendText(CONFIG.message, { topic: 'lk.chat' });
    stateEl.textContent = 'sent';
  } catch (err) {
    stateEl.textContent = 'error';
    window.__errors.push(String(err && err.message || err));
  }
})();
</script>
</body></html>
"""


def token(room: str) -> str:
    grant = api.VideoGrants(room_join=True, room=room, can_publish=True,
                            can_subscribe=True, can_publish_data=True)
    return (api.AccessToken(os.environ["LIVEKIT_API_KEY"],
                            os.environ["LIVEKIT_API_SECRET"])
            .with_identity(IDENTITY).with_name("Chrome Probe")
            .with_grants(grant).to_jwt())


def serve(page: str) -> tuple[str, http.server.HTTPServer]:
    """
    A one-page server on an ephemeral port.

    A `file://` page cannot hold a WebSocket to LiveKit Cloud under Chrome's
    origin rules, so the page needs an http origin even though it is local.
    """
    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):                                   # noqa: N802
            body = page.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_args):                      # quiet
            return

    server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return f"http://127.0.0.1:{server.server_port}/", server


def chrome() -> str:
    for candidate in CHROME_CANDIDATES:
        if pathlib.Path(candidate).exists():
            return candidate
    raise FileNotFoundError(
        "no Chrome found; looked in " + ", ".join(CHROME_CANDIDATES))


def run(message: str, *, room_name: str, wait: float, ready_ms: int,
        headed: bool) -> dict:
    from playwright.sync_api import sync_playwright

    config = {"url": os.environ["LIVEKIT_URL"], "token": token(room_name),
              "identity": IDENTITY, "message": message, "readyMs": ready_ms}
    url, server = serve(PAGE.replace("__CONFIG__", json.dumps(config)))

    console: list[str] = []
    failed_requests: list[str] = []
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(executable_path=chrome(),
                                        headless=not headed)
            page = browser.new_page()
            page.on("console", lambda m: console.append(f"{m.type}: {m.text}"[:300]))
            page.on("requestfailed",
                    lambda r: failed_requests.append(
                        f"{r.method} {r.url[:120]} {r.failure}"))
            page.goto(url, wait_until="domcontentloaded", timeout=60_000)

            # inspect -> act -> observe -> verify. The state element is the
            # browser's own view of the room, not our inference about it.
            page.wait_for_function(
                "document.getElementById('state').textContent === 'sent'"
                " || document.getElementById('state').textContent === 'error'",
                timeout=90_000)
            room_state = page.inner_text("#state")

            deadline = time.time() + wait
            while time.time() < deadline:
                count = page.eval_on_selector_all(".msg", "els => els.length")
                greetings = page.evaluate("window.__greetings || 0")
                if count > greetings:
                    break
                page.wait_for_timeout(1000)

            rendered = page.eval_on_selector_all(
                ".msg", "els => els.map(e => ({who: e.dataset.who, text: e.textContent}))")
            greetings = page.evaluate("window.__greetings || 0")
            errors = page.evaluate("window.__errors || []")
            title = page.title()
            browser.close()
    finally:
        server.shutdown()

    return {
        "room": room_name,
        "page_title": title,
        "room_state": room_state,
        "greeting_messages": rendered[:greetings],
        "reply_messages": rendered[greetings:],
        "page_errors": errors,
        "console_errors": [c for c in console if c.startswith("error")],
        "console_all": console[:20],
        "failed_requests": failed_requests,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("message", nargs="*")
    parser.add_argument("--room", default="chrome")
    parser.add_argument("--wait", type=float, default=90.0)
    parser.add_argument("--ready-ms", type=int, default=30_000)
    parser.add_argument("--headed", action="store_true",
                        help="show the browser window")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    message = " ".join(args.message).strip() or "Reply with exactly: FRIDAY_CHROME_OK"
    result = run(message, room_name=f"friday-chrome-{args.room}",
                 wait=args.wait, ready_ms=args.ready_ms, headed=args.headed)

    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print(f"  page title      {result['page_title']}")
        print(f"  room state      {result['room_state']}")
        print(f"  greetings       {len(result['greeting_messages'])}")
        print(f"  replies in DOM  {len(result['reply_messages'])}")
        for entry in result["reply_messages"]:
            print(f"    [{entry['who']}] {entry['text'][:300]}")
        print(f"  page errors     {result['page_errors'] or 'none'}")
        print(f"  console errors  {result['console_errors'] or 'none'}")
        print(f"  failed requests {result['failed_requests'] or 'none'}")
    return 0 if result["reply_messages"] else 1


if __name__ == "__main__":
    sys.exit(main())
