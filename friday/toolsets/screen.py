"""
Pointing at the screen.

The question this answers is "where do I click to do X" - and the answer is an
arrow whose tip is on the control, not a paragraph describing where to look.

Two rules shape everything here:

  1. Coordinates are fractions, never pixels. `vision._downscaled` shrinks the
     image before it goes to the model, so a pixel the model named belongs to a
     coordinate space that no longer exists by the time we draw. Fractions
     survive the resize; they are converted to device pixels once, at the end,
     against the real captured frame.

  2. An arrow is a claim. Drawing one at a guessed position is worse than
     saying "I can't see it" - the user trusts the arrow and clicks it. So a
     low-confidence locate draws nothing and says so, and "not visible" is a
     first-class answer rather than a failure.

This module only ever looks and draws. Nothing here can move a mouse; that
lives in `friday.toolsets.desktop` behind a much stricter gate.
"""

from __future__ import annotations

import math
import os
from datetime import datetime
from pathlib import Path

from friday import contracts as c
from friday.policy import PolicyEngine, default_engine
from friday.toolsets import vision
from friday.toolsets.system import APPROVAL_PREFIX

EXECUTION_SCOPE = "local_machine"

#: Below this, we do not draw. Tuned to be permissive for *pointing* - a wrong
#: arrow costs a glance and a correction. Clicking uses a stricter floor (see
#: `desktop.CLICK_MIN_CONFIDENCE`), because a wrong click costs an action.
POINT_MIN_CONFIDENCE = float(os.getenv("JARVIS_POINT_MIN_CONFIDENCE", "0.45"))

#: Arrow geometry, in pixels on the real screenshot.
_SHAFT = 132
_HEAD = 34
_WIDTH = 9

#: Vivid orange-red on a white halo: legible over dark UIs, light UIs and
#: photographs alike, and still inside Friday's warm palette.
_INK = (255, 77, 26)
_HALO = (255, 255, 255)
_PILL = (18, 12, 8)


def _gate(run: c.Run, tool_id: str, engine: PolicyEngine) -> c.ActionResult | None:
    verdict = engine.decide(tool_id)
    if verdict.allowed:
        return None
    return run.record(c.started(run.run_id, tool_id).finish(
        status=c.CANCELLED,
        error=f"{APPROVAL_PREFIX}: {verdict.reason} [{verdict.decision}]",
    ))


def _scoped(payload: dict) -> dict:
    return {"execution_scope": EXECUTION_SCOPE, **payload}


def _font(size: int):
    from PIL import ImageFont
    for name in ("segoeui.ttf", "arial.ttf", "DejaVuSans.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except Exception:  # noqa: BLE001
            continue
    return ImageFont.load_default()


def _tail_direction(nx: float, ny: float) -> tuple[float, float]:
    """Point the arrow from whichever corner leaves the shaft on the screen.

    An arrow whose tail runs off the top-left edge is a arrow you cannot see,
    so the quadrant decides the approach: the tail comes from the side with
    room for it.
    """
    dx = 1.0 if nx < 0.5 else -1.0          # tail to the right of a left-side target
    dy = 1.0 if ny < 0.5 else -1.0          # tail below a top-side target
    return dx, dy


def draw_pointer(png: bytes, width: int, height: int,
                 nx: float, ny: float, label: str) -> bytes:
    """Draw an arrow whose TIP is exactly at (nx, ny), plus a label pill.

    `nx`/`ny` are fractions of the image. Returns PNG bytes.
    """
    from PIL import Image, ImageDraw
    import io

    image = Image.open(io.BytesIO(png)).convert("RGBA")
    layer = Image.new("RGBA", image.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)

    tip_x = max(0, min(width - 1, int(round(nx * width))))
    tip_y = max(0, min(height - 1, int(round(ny * height))))

    dx, dy = _tail_direction(nx, ny)
    angle = math.radians(38)
    ux, uy = dx * math.cos(angle), dy * math.sin(angle)
    tail_x = int(tip_x + ux * _SHAFT)
    tail_y = int(tip_y + uy * _SHAFT)

    # Stop the shaft short of the tip so the head is a clean point.
    join_x = int(tip_x + ux * _HEAD)
    join_y = int(tip_y + uy * _HEAD)

    # Arrowhead: tip plus two shoulders square to the shaft.
    px, py = -uy, ux                                   # perpendicular
    half = _HEAD * 0.42
    head = [(tip_x, tip_y),
            (join_x + px * half, join_y + py * half),
            (join_x - px * half, join_y - py * half)]

    # Halo first, ink over it: readable on any background.
    draw.line([(join_x, join_y), (tail_x, tail_y)], fill=_HALO, width=_WIDTH + 6)
    draw.polygon([(head[0][0] + px * 3, head[0][1] + py * 3),
                  (head[1][0] + px * 4, head[1][1] + py * 4),
                  (head[2][0] - px * 4, head[2][1] - py * 4)], fill=_HALO)
    draw.line([(join_x, join_y), (tail_x, tail_y)], fill=_INK, width=_WIDTH)
    draw.polygon(head, fill=_INK)

    text = (label or "here").strip()[:42]
    font = _font(22)
    box = draw.textbbox((0, 0), text, font=font)
    tw, th = box[2] - box[0], box[3] - box[1]
    pad_x, pad_y = 14, 9
    # The pill hangs off the tail, further along the same line.
    cx = tail_x + ux * 10
    cy = tail_y + uy * 10
    left = int(cx - (tw / 2 if dx > 0 else tw / 2))
    top = int(cy - th / 2)
    left = max(6, min(width - tw - pad_x * 2 - 6, left - pad_x))
    top = max(6, min(height - th - pad_y * 2 - 6, top - pad_y))
    draw.rounded_rectangle(
        [left, top, left + tw + pad_x * 2, top + th + pad_y * 2],
        radius=9, fill=_PILL + (238,), outline=_INK + (255,), width=2)
    draw.text((left + pad_x - box[0], top + pad_y - box[1]), text,
              font=font, fill=(255, 240, 225, 255))

    out = io.BytesIO()
    Image.alpha_composite(image, layer).convert("RGB").save(out, format="PNG")
    return out.getvalue()


def screen_point(
    run: c.Run, target: str, *, hint: str = "", monitor: int = 1,
    overlay: bool = True, engine: PolicyEngine = default_engine,
) -> c.ActionResult:
    """Find `target` on screen and put an arrow on it.

    `hint` carries a correction from the last attempt ("a little further left"),
    so a re-point refines the previous answer instead of starting again.
    """
    tool_id = "screen.point"
    blocked = _gate(run, tool_id, engine)
    if blocked:
        return blocked
    started = c.started(run.run_id, tool_id)

    wanted = (target or "").strip()
    if not wanted:
        return run.record(c.failed(started, "nothing named to point at"))

    try:
        frame = vision.capture_screen(monitor=monitor)
    except vision.CaptureError as exc:
        return run.record(started.finish(
            status=c.NOT_CONFIGURED, error=str(exc),
            output=_scoped({"result": "no_capture", "target": wanted})))
    except Exception as exc:  # noqa: BLE001
        return run.record(c.failed(started, f"screen capture failed: {exc}"))

    try:
        found = vision.locate_in_frame(frame, wanted, hint=hint)
    except vision.CaptureError as exc:
        return run.record(started.finish(
            status=c.NOT_CONFIGURED, error=str(exc),
            output=_scoped({"result": "no_vision", "target": wanted})))
    except Exception as exc:  # noqa: BLE001
        return run.record(c.failed(started, f"could not look for {wanted!r}: {exc}"))

    confidence = float(found.get("confidence") or 0.0)
    why = (found.get("why") or "").strip()

    if not found.get("found"):
        # Honest absence. This is a real answer, not a failure.
        return run.record(started.finish(
            status=c.OBSERVED,
            output=_scoped({"result": "not_visible", "target": wanted,
                            "confidence": confidence, "why": why,
                            "spoken": f"I can't see {wanted} on this screen, sir."
                                      + (f" {why}" if why else "")})))

    if confidence < POINT_MIN_CONFIDENCE:
        # Told, and unable: the arrow is a claim, so we decline to make it.
        return run.record(started.finish(
            status=c.OBSERVED,
            output=_scoped({"result": "unsure", "target": wanted,
                            "confidence": confidence, "why": why,
                            "spoken": "I'm not sure enough to point at that, sir. "
                                      "Tell me roughly where it is and I'll try again."})))

    nx, ny = float(found["x"]), float(found["y"])
    label = (found.get("label") or wanted).strip()

    try:
        drawn = draw_pointer(frame.png, frame.width, frame.height, nx, ny, label)
    except Exception as exc:  # noqa: BLE001
        return run.record(c.failed(started, f"could not draw the pointer: {exc}"))

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = vision.captures_dir() / f"pointer_{stamp}_{frame.digest}.png"
    try:
        path.write_bytes(drawn)
    except OSError as exc:
        return run.record(c.failed(started, f"could not save the pointer: {exc}"))

    # Image-relative for the drawing, desktop-absolute for the arrow and for
    # anything that later clicks: a capture of the second monitor begins at
    # x=1920, and a click does not know about "the image".
    px = frame.origin_x + int(round(nx * frame.width))
    py = frame.origin_y + int(round(ny * frame.height))
    shown = False
    if overlay:
        # Best effort, always. The picture on disk is the guaranteed answer;
        # the on-screen arrow is the better one when the desktop allows it.
        try:
            from friday import overlay as overlay_mod
            shown = bool(overlay_mod.show(px, py, label))
        except Exception:  # noqa: BLE001
            shown = False

    artifact = c.new_artifact(
        run_id=run.run_id, type="screenshot",
        title=f"pointer at {label} {frame.captured_iso}", path_or_uri=str(path),
        producer=tool_id,
        verification=c.Verification(
            method="pointer_image_written",
            evidence=f"{frame.width}x{frame.height} PNG, {len(drawn)} bytes, "
                     f"arrow tip at ({px},{py})",
        ),
    )
    return run.record(c.succeeded(
        started,
        output=_scoped({
            "result": "pointed", "target": wanted, "label": label,
            "x": nx, "y": ny, "pixel_x": px, "pixel_y": py,
            "confidence": confidence, "on_screen_overlay": shown,
            "screen": {"width": frame.width, "height": frame.height},
            "image": str(path),
            "spoken": "Right there, sir.",
        }),
        artifacts=(artifact,),
        side_effects=(f"pointed at {label}"
                      + (" on screen" if shown else " in a saved image"),),
        verification=c.Verification(
            method="pointer_drawn_at_normalised_xy",
            evidence=f"located {label!r} at ({nx:.4f},{ny:.4f}) confidence "
                     f"{confidence:.2f}; drawn at pixel ({px},{py}) on a "
                     f"{frame.width}x{frame.height} frame sha256:{frame.digest}; "
                     f"saved {path}",
        ),
    ))
