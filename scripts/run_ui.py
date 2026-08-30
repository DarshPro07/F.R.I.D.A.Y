"""Launch the Friday UI server -- the read-mostly web control room.

A SEPARATE process from server.py, so it never destabilises the live agent.
Starting it opens Friday full-screen in Chrome as soon as the server answers;
the page holds a boot screen until the recognition model is in memory, so the
camera never pretends to be scanning while it is still loading.

    .venv\\Scripts\\python.exe scripts\\run_ui.py
    .venv\\Scripts\\python.exe scripts\\run_ui.py --no-browser
    .venv\\Scripts\\python.exe scripts\\run_ui.py --password   (PIN instead of your face,
        for when the camera is to be left alone: a stream, a meeting, a call)

Env: FRIDAY_UI_HOST (127.0.0.1), FRIDAY_UI_PORT (8770), FRIDAY_UI_BROWSER
(path to a Chrome/Edge binary), ADA_MCP_HOST/PORT (where the live MCP server
listens, for the connection panel).
"""
import os
import shutil
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

import uvicorn

CHROME_CANDIDATES = [
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"),
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
]


def _browser() -> str | None:
    explicit = os.getenv("FRIDAY_UI_BROWSER")
    if explicit and Path(explicit).exists():
        return explicit
    for path in CHROME_CANDIDATES:
        if path and Path(path).exists():
            return path
    return shutil.which("chrome") or shutil.which("msedge")


def _wait_until_up(url: str, timeout: float = 25.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=1) as r:
                if r.status == 200:
                    return True
        except (urllib.error.URLError, OSError):
            time.sleep(0.25)
    return False


def _open_fullscreen(url: str) -> None:
    """One app window, full screen, its own profile so it is not a tab in your work window."""
    if not _wait_until_up(url.rstrip("/") + "/health"):
        print("UI did not answer in time; not opening a window", flush=True)
        return
    exe = _browser()
    if not exe:
        import webbrowser
        webbrowser.open(url)
        return
    profile = Path(os.getenv("LOCALAPPDATA", ".")) / "friday-ui-profile"
    subprocess.Popen(
        [exe, "--app=" + url, "--start-fullscreen", "--user-data-dir=" + str(profile),
         "--no-first-run", "--no-default-browser-check",
         # the gate needs the camera and the mic without a prompt on every launch
         "--use-fake-ui-for-media-stream" if os.getenv("FRIDAY_UI_AUTOGRANT") == "1" else "--enable-features=AutoplayIgnoreWebAudio"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    print("Opened Friday full-screen (%s)" % Path(exe).name, flush=True)


def main():
    if "--password" in sys.argv:
        # The owner has said the camera is not Friday's tonight: PIN only, no face, no capture.
        os.environ["FRIDAY_AUTH_MODE"] = "pin"
        print("PIN mode: the camera stays yours.", flush=True)
    host = os.getenv("FRIDAY_UI_HOST", "127.0.0.1")
    port = int(os.getenv("FRIDAY_UI_PORT", "8770"))
    url = "http://%s:%s/" % (host, port)
    print("Friday Control Room -> %s" % url, flush=True)
    if "--no-browser" not in sys.argv:
        threading.Thread(target=_open_fullscreen, args=(url,), daemon=True).start()
    uvicorn.run("friday.ui_server:app", host=host, port=port, log_level="info")


if __name__ == "__main__":
    main()
