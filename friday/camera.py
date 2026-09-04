"""
friday/camera.py -- who is using the camera, and may Friday have it.

The rule the owner set: the camera is the first way in, but it is never taken
from him. If he is in a meeting, on a stream, or has Friday's own voice agent
running in a terminal, Friday does not fight for the device -- she asks, and
falls back to a PIN.

Windows tells us this honestly. Every app that opens the camera writes to
CapabilityAccessManager\\ConsentStore\\webcam; while it holds the device its
LastUsedTimeStop is 0. That is a fact from the OS, not a guess from a process
list -- though we read the process list too, to name what we found and to know
whether letting go matters (a meeting) or not (a photo app left open).

Nothing here ever stops another program. Friday asks; the owner decides.
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

# what a holder means for the answer we give the owner
MEETING = {
    "zoom.exe": "Zoom", "teams.exe": "Teams", "ms-teams.exe": "Teams",
    "webex.exe": "Webex", "atmgr.exe": "Webex", "skype.exe": "Skype",
    "slack.exe": "Slack", "discord.exe": "Discord", "gotomeeting.exe": "GoToMeeting",
    "bluejeans.exe": "BlueJeans", "ringcentral.exe": "RingCentral",
}
STREAM = {
    "obs64.exe": "OBS", "obs32.exe": "OBS", "obs.exe": "OBS",
    "streamlabs obs.exe": "Streamlabs", "xsplit.core.exe": "XSplit",
    "nvidia broadcast.exe": "NVIDIA Broadcast", "camtasia.exe": "Camtasia",
    "streamlabs desktop.exe": "Streamlabs",
}
BROWSER = {"chrome.exe": "Chrome", "msedge.exe": "Edge", "firefox.exe": "Firefox",
           "brave.exe": "Brave", "opera.exe": "Opera"}
CAMERA_APP = {"windowscamera.exe": "the Camera app", "microsoft.windows.camera": "the Camera app"}
OURS = {"python.exe", "pythonw.exe"}          # Friday's own voice agent in a terminal

CONSENT = r"SOFTWARE\Microsoft\Windows\CurrentVersion\CapabilityAccessManager\ConsentStore\webcam"


def _registry_holders():
    """Apps the OS says are holding the camera right now (LastUsedTimeStop == 0)."""
    if not sys.platform.startswith("win"):
        return []
    try:
        import winreg
    except ImportError:
        return []
    found = []
    for root in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
        for sub in (CONSENT, CONSENT + r"\NonPackaged"):
            try:
                key = winreg.OpenKey(root, sub)
            except OSError:
                continue
            with key:
                i = 0
                while True:
                    try:
                        name = winreg.EnumKey(key, i)
                    except OSError:
                        break
                    i += 1
                    if name == "NonPackaged":
                        continue
                    try:
                        with winreg.OpenKey(key, name) as k:
                            stop, _ = winreg.QueryValueEx(k, "LastUsedTimeStop")
                    except OSError:
                        continue
                    if stop == 0:                       # 0 = still open
                        found.append(name.replace("#", "\\"))
    return found


def _classify(token: str):
    """token is an exe path, a package family name, or a bare process name."""
    exe = Path(token.replace("#", "\\")).name.lower() or token.lower()
    low = token.lower()
    for table, kind in ((MEETING, "meeting"), (STREAM, "stream"), (CAMERA_APP, "app"), (BROWSER, "browser")):
        for needle, label in table.items():
            if exe == needle or needle.split(".")[0] in low:
                return {"process": exe, "label": label, "kind": kind}
    if exe in OURS:
        return {"process": exe, "label": "Friday's own voice agent", "kind": "friday"}
    return {"process": exe, "label": exe.replace(".exe", "") or token, "kind": "app"}


def _running():
    """Names of processes that are known camera users, for naming a holder."""
    try:
        import psutil
    except ImportError:
        return []
    names = set()
    for p in psutil.process_iter(["name"]):
        n = (p.info.get("name") or "").lower()
        if n in MEETING or n in STREAM or n in CAMERA_APP:
            names.add(n)
    return sorted(names)


def status():
    """
    {"busy": bool, "holders": [...], "yield_to": "meeting"|"stream"|None, "why": str}

    `yield_to` is the reason Friday must NOT ask for the camera at all: a meeting
    or a stream is the owner at work, and interrupting that is worse than a PIN.
    """
    holders = [_classify(t) for t in _registry_holders()]
    if not holders:                                     # registry said nothing; name what is running
        holders = [_classify(n) for n in _running() if _classify(n)["kind"] in ("meeting", "stream")]
        busy = False                                    # running is not the same as holding
    else:
        busy = True
    yield_to = None
    for h in holders:
        if h["kind"] in ("meeting", "stream"):
            yield_to = h["kind"]
            break
    names = ", ".join(dict.fromkeys(h["label"] for h in holders))
    if yield_to == "meeting":
        why = "%s is using the camera. I will not interrupt a meeting." % names
    elif yield_to == "stream":
        why = "%s is using the camera. I will not interrupt a stream." % names
    elif busy:
        why = "%s is using the camera." % names
    else:
        why = ""
    return {"busy": busy, "holders": holders, "yield_to": yield_to, "why": why,
            "checked_at": time.time(), "platform": sys.platform}


def free_enough():
    """True when Friday may open the camera without asking anyone."""
    if os.getenv("FRIDAY_AUTH_MODE", "").lower() == "pin":
        return False
    s = status()
    return not s["busy"] and not s["yield_to"]


if __name__ == "__main__":                              # a runnable self-check
    import json
    s = status()
    print(json.dumps(s, indent=2))
    assert isinstance(s["busy"], bool) and isinstance(s["holders"], list)
    assert s["yield_to"] in (None, "meeting", "stream")
    print("free_enough ->", free_enough())
