"""
friday/desk.py -- what the owner is looking at right now.

Two questions Friday could not answer before:

    "what's on my clipboard?"
    "read what I've selected"

The clipboard is easy: the system toolset already has a hardened reader that
survives the Windows clipboard lock, so this reuses it rather than opening a
second one.

The selection is harder, because Windows has no "give me the highlighted text"
call that works across applications. Every reliable tool does the same thing:
press Ctrl+C at whatever has focus and read what lands. That is what happens
here -- with one rule that matters. The owner's clipboard is his, so it is put
back exactly as it was, whatever the outcome, including when nothing was
selected and the copy did nothing at all.

Nothing here types into anything or clicks anything. It presses Ctrl+C and
reads. If the foreground window has no selection, Friday says so instead of
returning whatever happened to be on the clipboard already.
"""
from __future__ import annotations

import sys
import time

MAX_CHARS = 20_000          # a spoken answer never needs more; keeps prompts sane

#: Signatures of "another process is holding the clipboard", across pyperclip,
#: the Win32 layer and the PowerShell fallback. A clipboard-history tool or a
#: sync client causes exactly this, and no amount of retrying beats a process
#: that will not let go -- so it earns a plain sentence, not a stack trace.
_BUSY = ("requested clipboard operation did not succeed", "openclipboard",
         "access is denied", "cannot open clipboard",
         "the operation completed successfully")


def _busy(exc) -> bool:
    """Check if an exception indicates the clipboard is busy."""
    return any(sig in str(exc).lower() for sig in _BUSY)


def _read():
    from friday.toolsets.system import read_clipboard
    return read_clipboard() or ""


def _write(text):
    from friday.toolsets.system import write_clipboard
    write_clipboard(text)


def clipboard():
    """{\"ok\", \"text\", \"chars\", \"truncated\"} -- the clipboard, untouched.

    Retries briefly, because a clipboard held open is usually held for under a
    second. A persistent holder gets a clean message the UI can speak, never
    the raw Win32/PowerShell error.
    """
    last = None
    for attempt in range(3):
        try:
            text = _read()
            return {"ok": True, "text": text[:MAX_CHARS], "chars": len(text),
                    "truncated": len(text) > MAX_CHARS, "source": "clipboard"}
        except Exception as exc:  # noqa: BLE001
            last = exc
            time.sleep(0.4 * (attempt + 1))
    if _busy(last):
        return {"ok": False, "busy": True,
                "message": "Something else is holding the clipboard right now, sir. "
                           "Close it or give me a moment and ask again."}
    return {"ok": False, "error": "I could not read the clipboard (%s)." % str(last)[:100]}


def _tap_copy():
    """Press Ctrl+C at the foreground window. Windows only."""
    import ctypes
    user32 = ctypes.windll.user32
    VK_CONTROL, VK_C, KEYUP = 0x11, 0x43, 0x0002
    user32.keybd_event(VK_CONTROL, 0, 0, 0)
    user32.keybd_event(VK_C, 0, 0, 0)
    time.sleep(0.03)
    user32.keybd_event(VK_C, 0, KEYUP, 0)
    user32.keybd_event(VK_CONTROL, 0, KEYUP, 0)


def selection(timeout=0.8):
    """
    The text highlighted in whatever window has focus.

    Returns {\"ok\", \"text\", ...} or {\"ok\": False, \"reason\": \"nothing selected\"}.
    The clipboard is always restored to what it held before the call.
    """
    if not sys.platform.startswith("win"):
        return {"ok": False, "error": "reading a selection is implemented for Windows only"}

    try:
        before = _read()
    except Exception:  # noqa: BLE001
        before = ""

    # A sentinel makes "nothing was selected" distinguishable from "the selection
    # happens to equal what was already on the clipboard".
    sentinel = "\x00friday-selection-probe\x00"
    try:
        _write(sentinel)
    except Exception as exc:  # noqa: BLE001
        if _busy(exc):
            return {"ok": False, "busy": True,
                    "message": "Something else is holding the clipboard, sir, so I "
                               "cannot read your selection just now. Try again in a moment."}
        return {"ok": False, "error": "I could not use the clipboard (%s)." % str(exc)[:100]}

    got, deadline = "", time.time() + timeout
    try:
        _tap_copy()
        while time.time() < deadline:
            time.sleep(0.05)
            try:
                now = _read()
            except Exception:  # noqa: BLE001
                continue
            if now and now != sentinel:
                got = now
                break
    finally:
        try:
            _write(before)          # his clipboard, put back, always
        except Exception:  # noqa: BLE001
            pass

    if not got.strip():
        return {"ok": False, "reason": "nothing selected",
                "message": "I could not find any selected text in the window you are on."}
    return {"ok": True, "text": got[:MAX_CHARS], "chars": len(got),
            "truncated": len(got) > MAX_CHARS, "source": "selection"}


def grab(what="auto"):
    """
    what = \"clipboard\" | \"selection\" | \"auto\".

    \"auto\" tries the selection first and falls back to the clipboard, which is
    what \"read this for me\" usually means: something is highlighted, and if it
    is not, the last thing copied is the next best guess.
    """
    if what == "clipboard":
        return clipboard()
    if what == "selection":
        return selection()
    sel = selection()
    if sel.get("ok"):
        return sel
    clip = clipboard()
    if clip.get("ok") and clip.get("text", "").strip():
        clip["fell_back"] = True
        return clip
    return sel


if __name__ == "__main__":                       # a runnable self-check
    import json
    print("clipboard ->", json.dumps({k: v for k, v in clipboard().items() if k != "text"}))
    before = _read()
    out = selection()
    print("selection ->", json.dumps({k: v for k, v in out.items() if k != "text"}))
    assert _read() == before, "the clipboard was NOT restored -- that is a bug"
    print("clipboard restored intact:", len(before), "chars")
