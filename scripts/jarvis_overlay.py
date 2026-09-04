#!/usr/bin/env python3
"""
The arrow that lives on the screen.

    python scripts/jarvis_overlay.py --x 1830 --y 1059 --label "10:09 AM"

A borderless, always-on-top, click-through window the size of the desktop, with
one arrow drawn on it and everything else transparent. It dismisses itself.

Why a separate process: Tk owns a main loop and wants the main thread, and the
UI server has its own. A short-lived child cannot wedge the server, cannot leak
a loop into it, and dies on its own if anything goes wrong here.

Click-through is the whole point and not a nicety. The user asks where to click
and then *clicks there* - an arrow that eats that click would break the feature
it exists to provide. So the window takes WS_EX_TRANSPARENT (mouse passes
through) and WS_EX_NOACTIVATE (it never steals focus). If those cannot be set,
the overlay refuses to show at all rather than sit in front of the button.
"""

from __future__ import annotations

import argparse
import math
import sys

TRANSPARENT = "#010203"          # a colour nothing on a desktop is likely to be
INK = "#ff4d1a"
HALO = "#ffffff"
PILL = "#120c08"
TEXT = "#fff0e1"

SHAFT, HEAD, WIDTH = 132, 34, 9


def _make_dpi_aware() -> None:
    """Make one pixel here mean one pixel in the screenshot.

    Coordinates arrive in *physical* pixels, because that is what a screenshot
    is measured in. Tk lays out in *logical* pixels, and on a scaled display
    those are not the same number: at 125% a tip asked for at x=1400 is drawn
    at x=1750, and the arrow points a quarter of the screen away from the
    control. Declaring the process DPI aware before any window exists collapses
    the two coordinate systems into one.

    This was measured, not assumed: the first working overlay landed at
    (1750, 756) for a requested (1400, 600) - exactly the 1.25 scale factor.
    """
    import ctypes
    for attempt in (
        # Per-monitor v2 (Windows 10 1703+). DPI_AWARENESS_CONTEXT is a handle,
        # and -4 is the documented sentinel for PER_MONITOR_AWARE_V2.
        lambda: ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4)),
        lambda: ctypes.windll.shcore.SetProcessDpiAwareness(2),
        lambda: ctypes.windll.user32.SetProcessDPIAware(),
    ):
        try:
            if attempt():
                return
        except Exception:  # noqa: BLE001
            continue


def _click_through(root) -> bool:
    """Mouse passes through, and the window never takes focus. Windows only."""
    try:
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.WinDLL("user32", use_last_error=True)
        user32.GetParent.argtypes = (wintypes.HWND,)
        user32.GetParent.restype = wintypes.HWND
        user32.GetWindowLongW.argtypes = (wintypes.HWND, ctypes.c_int)
        user32.GetWindowLongW.restype = ctypes.c_long
        user32.SetWindowLongW.argtypes = (wintypes.HWND, ctypes.c_int, ctypes.c_long)
        user32.SetWindowLongW.restype = ctypes.c_long
        user32.SetLayeredWindowAttributes.argtypes = (
            wintypes.HWND, wintypes.COLORREF, ctypes.c_ubyte, wintypes.DWORD)
        user32.SetLayeredWindowAttributes.restype = wintypes.BOOL

        root.update_idletasks()
        hwnd = root.winfo_id()
        parent = user32.GetParent(hwnd)          # Tk's toplevel sits above the frame
        if parent:
            hwnd = parent

        GWL_EXSTYLE = -20
        WS_EX_LAYERED = 0x00080000
        WS_EX_TRANSPARENT = 0x00000020
        WS_EX_NOACTIVATE = 0x08000000
        TOOLWINDOW = 0x00000080                  # keep it out of alt-tab

        style = user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
        wanted = style | WS_EX_LAYERED | WS_EX_TRANSPARENT | WS_EX_NOACTIVATE | TOOLWINDOW
        if not user32.SetWindowLongW(hwnd, GWL_EXSTYLE, wanted):
            # SetWindowLong returns the previous value; 0 with a real error set
            # means it failed. A previous style of 0 is not plausible here.
            if ctypes.get_last_error():
                return False

        # Re-arm the colour key. Tk set the layered attributes when it applied
        # -transparentcolor, and touching WS_EX_LAYERED through SetWindowLong
        # discards them - after which the window composites as nothing at all
        # and the arrow is invisible to the eye and to a screenshot alike.
        # Windows requires the attributes to be set again after the style
        # changes, so we do it here rather than trusting the earlier call.
        r, g, b = (int(TRANSPARENT[i:i + 2], 16) for i in (1, 3, 5))
        colorref = (b << 16) | (g << 8) | r          # COLORREF is 0x00BBGGRR
        LWA_COLORKEY = 0x00000001
        if not user32.SetLayeredWindowAttributes(hwnd, colorref, 255, LWA_COLORKEY):
            return False

        return bool(user32.GetWindowLongW(hwnd, GWL_EXSTYLE) & WS_EX_TRANSPARENT)
    except Exception:
        return False


def _tail_direction(x: float, y: float, w: int, h: int) -> tuple[float, float]:
    return (1.0 if x < w / 2 else -1.0), (1.0 if y < h / 2 else -1.0)


def draw(canvas, x: int, y: int, label: str, w: int, h: int) -> None:
    dx, dy = _tail_direction(x, y, w, h)
    ang = math.radians(38)
    ux, uy = dx * math.cos(ang), dy * math.sin(ang)
    tail = (x + ux * SHAFT, y + uy * SHAFT)
    join = (x + ux * HEAD, y + uy * HEAD)
    px, py = -uy, ux
    half = HEAD * 0.42
    head = [x, y,
            join[0] + px * half, join[1] + py * half,
            join[0] - px * half, join[1] - py * half]

    canvas.create_line(join[0], join[1], tail[0], tail[1],
                       fill=HALO, width=WIDTH + 6, capstyle="round")
    canvas.create_polygon(head, fill=HALO, outline=HALO, width=6)
    canvas.create_line(join[0], join[1], tail[0], tail[1],
                       fill=INK, width=WIDTH, capstyle="round")
    canvas.create_polygon(head, fill=INK, outline=INK)

    text = (label or "here").strip()[:42]
    if not text:
        return
    tx, ty = tail[0] + ux * 12, tail[1] + uy * 12
    item = canvas.create_text(tx, ty, text=text, fill=TEXT,
                              font=("Segoe UI", 15, "bold"))
    x0, y0, x1, y1 = canvas.bbox(item)
    pad = 10
    box = canvas.create_rectangle(x0 - pad, y0 - pad, x1 + pad, y1 + pad,
                                  fill=PILL, outline=INK, width=2)
    canvas.tag_raise(item, box)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--x", type=int, required=True)
    ap.add_argument("--y", type=int, required=True)
    ap.add_argument("--label", default="")
    ap.add_argument("--seconds", type=float, default=4.0)
    args = ap.parse_args()

    _make_dpi_aware()          # before any window exists, or the arrow is off by the scale factor

    try:
        import tkinter as tk
    except Exception as exc:                      # headless / no Tk
        print(f"overlay unavailable: {exc}", file=sys.stderr)
        return 2

    root = tk.Tk()
    root.overrideredirect(True)
    root.attributes("-topmost", True)
    try:
        root.attributes("-transparentcolor", TRANSPARENT)
    except tk.TclError:
        print("overlay unavailable: no transparent colour on this platform",
              file=sys.stderr)
        return 2

    w = root.winfo_screenwidth()
    h = root.winfo_screenheight()
    root.geometry(f"{w}x{h}+0+0")
    root.configure(bg=TRANSPARENT)

    canvas = tk.Canvas(root, width=w, height=h, bg=TRANSPARENT,
                       highlightthickness=0, bd=0)
    canvas.pack(fill="both", expand=True)

    if not _click_through(root):
        # Refuse rather than park an opaque-to-clicks window over the control.
        root.destroy()
        print("overlay unavailable: could not make the window click-through",
              file=sys.stderr)
        return 3

    draw(canvas, args.x, args.y, args.label, w, h)
    root.after(int(max(0.5, args.seconds) * 1000), root.destroy)
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
