"""Launch the Friday UI server -- the read-mostly web control room.

A SEPARATE process from server.py, so it never destabilises the live agent.
Starting it opens Friday full-screen in Chrome as soon as the server answers;
the page holds a boot screen until the recognition model is in memory, so the
camera never pretends to be scanning while it is still loading.

    .venv\\Scripts\\python.exe scripts\\run_ui.py
    .venv\\Scripts\\python.exe scripts\\run_ui.py --no-browser
    .venv\\Scripts\\python.exe scripts\\run_ui.py --password   (PIN instead of your face,
        for when the camera is to be left alone: a stream, a meeting, a call)
    .venv\\Scripts\\python.exe scripts\\run_ui.py --bypass-face  (face recognition off
        for this run only -- a launch flag, since the gate stands before any chat)
    .venv\\Scripts\\python.exe scripts\\run_ui.py --log logs\\ui_server.log
        (send output to a file. Friday.exe uses this: a GUI launcher has no
        console, so an unredirected server dies on its first print)

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


def _log_to_file(path: str) -> None:
    """Send stdout and stderr to a file, before uvicorn configures logging.

    A GUI launcher has no console, so the server inherits handles it cannot
    write to and dies on its first print. Giving it a real file fixes that and
    leaves somewhere to look when a start goes wrong.
    """
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    stream = open(p, "a", buffering=1, encoding="utf-8", errors="replace")
    sys.stdout = sys.stderr = stream
    print("\n=== Friday UI starting %s ===" % time.strftime("%Y-%m-%d %H:%M:%S"))


def main():
    if "--log" in sys.argv:
        i = sys.argv.index("--log")
        if i + 1 < len(sys.argv):
            _log_to_file(sys.argv[i + 1])
    if "--bypass-face" in sys.argv or "--no-face" in sys.argv:
        # Face recognition off for THIS run only. It is a launch flag, not an
        # in-app command, on purpose: the gate stands before any chat, so the
        # only thing that may lift it is someone at this machine's terminal.
        os.environ["FRIDAY_FACE_GATE"] = "0"
        print("Face recognition bypassed for this run.", flush=True)
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
