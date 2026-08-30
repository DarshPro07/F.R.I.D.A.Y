"""
Vision toolset (Phase 1E): camera and screen, on demand only.

The honesty problem here is different from every other toolset. Elsewhere the
question is "did the action happen?", which a process check or a read-back can
settle. Here the tool genuinely succeeds - a frame really was captured - while
the *answer about it* can still be invented. A vision model asked "what am I
holding?" will always produce a confident-sounding noun.

So three things are enforced structurally rather than by prompt:

1. **No frame, no answer.** Capture failure is FAILED. There is no path that
   answers a question about an image that does not exist.

2. **Confidence decides the status, not the phrasing.** The model must return
   a numeric confidence. Below the high threshold the result is PARTIAL, so
   `may_claim_completion` is False and the agent is not permitted to assert
   the identification - only to relay the hedged `spoken_form`. §15's
   "never guarantee perfect object recognition" becomes a state machine
   instead of a request.

3. **Every capture is an Artifact.** The PNG is written to disk with a
   sha256, so any claim about what was seen has a retrievable image behind it.
   An answer can be checked after the fact.

§26: the camera is opened per call and released immediately. Nothing here
streams, polls, or holds a device open.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path

from friday import contracts as c
from friday.config import DATA_DIR
from friday.policy import PolicyEngine, default_engine
from friday.toolsets.system import APPROVAL_PREFIX

EXECUTION_SCOPE = "local_machine"

#: Above this, the agent may state an identification. Below it the result is
#: PARTIAL and the agent may only hedge.
CONFIDENCE_HIGH = 0.75
#: Below this, no identification is offered at all - only a request for a
#: better view.
CONFIDENCE_MEDIUM = 0.45

#: Frames older than this are not answered about. A question is about now.
MAX_FRAME_AGE_SECONDS = 30.0

CAMERA_WARMUP_FRAMES = 5  # webcams return dark or garbage frames at first

VISION_MODEL = os.getenv("ADA_VISION_MODEL", "gemini-2.5-flash")


def captures_dir() -> Path:
    path = Path(os.getenv("ADA_VISION_DIR") or DATA_DIR / "vision")
    path.mkdir(parents=True, exist_ok=True)
    return path


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


class CaptureError(RuntimeError):
    """No usable frame could be obtained."""


class Frame:
    """A captured image plus everything needed to prove it is real and recent."""

    def __init__(self, png: bytes, width: int, height: int, source: str) -> None:
        self.png = png
        self.width = width
        self.height = height
        self.source = source
        self.captured_at = time.monotonic()
        self.captured_iso = datetime.now(timezone.utc).isoformat()
        self.digest = hashlib.sha256(png).hexdigest()[:16]
        self.path: Path | None = None

    @property
    def age_seconds(self) -> float:
        return time.monotonic() - self.captured_at

    def save(self) -> Path:
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        self.path = captures_dir() / f"{self.source}_{stamp}_{self.digest}.png"
        self.path.write_bytes(self.png)
        return self.path

    def describe(self) -> dict:
        return {
            "source": self.source, "width": self.width, "height": self.height,
            "bytes": len(self.png), "sha256": self.digest,
            "captured_at": self.captured_iso,
            "path": str(self.path) if self.path else None,
        }


# ---------------------------------------------------------------------------
# Capture
# ---------------------------------------------------------------------------


def capture_screen(monitor: int = 1, region: dict | None = None) -> Frame:
    """Grab the screen. Opens, grabs, closes - nothing is held."""
    try:
        import mss
        import mss.tools
    except ImportError as exc:  # pragma: no cover - dependency present in prod
        raise CaptureError(f"mss unavailable: {exc}") from exc

    with mss.MSS() as sct:
        monitors = sct.monitors
        if region:
            box = region
        else:
            if monitor >= len(monitors):
                raise CaptureError(
                    f"monitor {monitor} does not exist ({len(monitors) - 1} attached)"
                )
            box = monitors[monitor]
        shot = sct.grab(box)
        png = mss.tools.to_png(shot.rgb, shot.size)

    if not png:
        raise CaptureError("screen grab produced no image data")
    return Frame(png, shot.width, shot.height, "screen")


def capture_camera(device: int = 0, warmup: int = CAMERA_WARMUP_FRAMES) -> Frame:
    """
    Grab one frame from the webcam, then release it immediately.

    The first frames a webcam returns are usually dark or garbage while
    exposure settles, so a few are read and discarded. Answering a question
    about a black frame would be worse than failing.
    """
    try:
        import cv2
    except ImportError as exc:  # pragma: no cover
        raise CaptureError(f"opencv unavailable: {exc}") from exc

    capture = None
    try:
        # DSHOW opens reliably on Windows; CAP_ANY is the portable fallback.
        backend = getattr(cv2, "CAP_DSHOW", 0) if os.name == "nt" else 0
        capture = cv2.VideoCapture(device, backend)
        if not capture.isOpened():
            capture.release()
            capture = cv2.VideoCapture(device)
        if not capture.isOpened():
            raise CaptureError(f"could not open camera {device}")

        frame = None
        for _ in range(max(warmup, 1)):
            ok, candidate = capture.read()
            if ok and candidate is not None:
                frame = candidate
            time.sleep(0.05)

        if frame is None:
            raise CaptureError("camera opened but returned no frame")

        ok, buffer = cv2.imencode(".png", frame)
        if not ok:
            raise CaptureError("could not encode camera frame as PNG")
        height, width = frame.shape[:2]
        return Frame(buffer.tobytes(), width, height, "camera")
    finally:
        if capture is not None:
            capture.release()  # §26: the device does not stay open


# ---------------------------------------------------------------------------
# Asking a model about a frame
# ---------------------------------------------------------------------------

_ANALYSIS_SCHEMA = {
    "type": "object",
    "properties": {
        "observation": {"type": "string"},
        "identification": {"type": "string"},
        "confidence": {"type": "number"},
        "uncertain_because": {"type": "string"},
        "suggested_better_view": {"type": "string"},
        "text_found": {"type": "string"},
    },
    "required": ["observation", "confidence"],
}

_SYSTEM = """You are analysing a single still image for a voice assistant.

Report only what is visible. Do not infer brand, model or contents that the
pixels do not show. If the image is dark, blurred, cropped or ambiguous, say
so and give a low confidence - an honest "I can't tell" is correct and useful,
a confident wrong noun is not.

confidence is your calibrated probability, 0.0 to 1.0, that your
identification is correct. Use it honestly:
  0.9+  unmistakable
  0.75  clearly identifiable
  0.5   plausible but could be several things
  0.2   guessing from shape alone
  0.0   cannot tell

suggested_better_view: if a different angle, closer distance, better light or
a visible label would settle it, say which. Otherwise leave empty.
text_found: any text legible in the image, verbatim. Otherwise empty."""


#: Longest edge sent to the model. A full 1920x1080 screenshot is up to ~850KB
#: of PNG on every call; uploading that made inspect_screen average two
#: minutes and fail outright on the first attempt in a twice-run verification.
#: The model does not need the extra pixels to read a screen, and the
#: full-resolution image is still written to disk as the artifact.
ANALYSIS_MAX_EDGE = 1280


def _downscaled(png: bytes, width: int, height: int) -> bytes:
    """Shrink for upload. Returns the original if it is already small enough."""
    longest = max(width, height)
    if longest <= ANALYSIS_MAX_EDGE:
        return png
    try:
        import cv2
        import numpy as np

        image = cv2.imdecode(np.frombuffer(png, np.uint8), cv2.IMREAD_COLOR)
        if image is None:
            return png
        scale = ANALYSIS_MAX_EDGE / longest
        resized = cv2.resize(
            image, (int(width * scale), int(height * scale)),
            interpolation=cv2.INTER_AREA)
        # JPEG for the wire: a screenshot at quality 85 is several times
        # smaller than PNG and the model reads it identically.
        ok, buffer = cv2.imencode(".jpg", resized,
                                  [int(cv2.IMWRITE_JPEG_QUALITY), 85])
        if not ok:
            return png
        # A mostly-flat screen compresses to a few KB as PNG, and JPEG of the
        # same content comes out larger. Send whichever is actually smaller.
        smaller = buffer.tobytes()
        return smaller if len(smaller) < len(png) else png
    except Exception:
        return png  # never let an optimisation break the capability


def analyse_frame(frame: Frame, question: str) -> dict:
    """Ask the vision model about a frame. Returns the parsed analysis."""
    if not os.getenv("GOOGLE_API_KEY", "").strip():
        raise CaptureError("vision analysis needs GOOGLE_API_KEY")

    from google import genai
    from google.genai import types

    payload = _downscaled(frame.png, frame.width, frame.height)
    mime = "image/jpeg" if payload is not frame.png else "image/png"

    client = genai.Client(api_key=os.getenv("GOOGLE_API_KEY"))
    response = client.models.generate_content(
        model=VISION_MODEL,
        contents=[
            types.Part.from_bytes(data=payload, mime_type=mime),
            types.Part(text=question or "What is in this image?"),
        ],
        config=types.GenerateContentConfig(
            system_instruction=_SYSTEM,
            response_mime_type="application/json",
            response_schema=_ANALYSIS_SCHEMA,
            temperature=0.1,
        ),
    )
    text = (response.text or "").strip()
    if not text:
        raise CaptureError("vision model returned nothing")
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as exc:
        raise CaptureError(f"vision model returned unparseable JSON: {exc}") from exc

    confidence = parsed.get("confidence")
    if not isinstance(confidence, (int, float)) or not 0.0 <= confidence <= 1.0:
        raise CaptureError(f"vision model gave no usable confidence: {confidence!r}")
    return parsed


#: Leading words that are safe to lowercase when a model's sentence is
#: embedded mid-phrase. Anything else may be a proper noun ("Raspberry Pi").
_SAFE_TO_LOWER = ("a ", "an ", "the ")


def _tidy(phrase: str) -> str:
    """Make a model sentence fit inside ours: no double stop, no stray capital."""
    phrase = (phrase or "").strip().rstrip(".").strip()
    lowered = phrase.lower()
    if any(lowered.startswith(word) for word in _SAFE_TO_LOWER):
        phrase = phrase[0].lower() + phrase[1:]
    return phrase


def spoken_form(analysis: dict, source: str) -> str:
    """
    Phrase the answer according to confidence. This is the sentence the agent
    should say; it is derived, not chosen by the model.

    Note the high-confidence branch handles a missing identification. A
    question like "read this screen" is answered confidently but names no
    object, and an earlier version fell through to the low-confidence text -
    reporting "I'm not confident enough" alongside a 95% confidence and a
    succeeded status, which contradicted itself.
    """
    confidence = float(analysis.get("confidence", 0.0))
    identification = _tidy(analysis.get("identification") or "")
    observation = _tidy(analysis.get("observation") or "")
    text_found = (analysis.get("text_found") or "").strip()
    better = (analysis.get("suggested_better_view") or "").strip()
    thing = "on your screen" if source == "screen" else "there"

    if confidence >= CONFIDENCE_HIGH:
        if identification:
            return f"I believe that's {identification}."
        if text_found:
            return f"I can read: {text_found}"
        if observation:
            return f"I can see {observation}."
        return f"I can see something {thing}, but nothing I can name."

    if confidence >= CONFIDENCE_MEDIUM:
        hedge = (f"It looks like {identification}, but I'm not certain."
                 if identification
                 else f"I can see {observation}, but I'm not certain what it is."
                 if observation else "I'm not sure what I'm looking at.")
        return f"{hedge} {better}".strip() if better else hedge

    if observation:
        base = (f"I'm not confident enough to identify what's {thing}. "
                f"I can see {observation}.")
        return f"{base} {better}".strip() if better else base
    return f"I can't make out what's {thing} clearly enough to say."


def confidence_band(confidence: float) -> str:
    if confidence >= CONFIDENCE_HIGH:
        return "high"
    return "medium" if confidence >= CONFIDENCE_MEDIUM else "low"


# ---------------------------------------------------------------------------
# Capabilities
# ---------------------------------------------------------------------------


def _capture_result(
    run: c.Run, tool_id: str, engine: PolicyEngine, grab, label: str
) -> c.ActionResult:
    blocked = _gate(run, tool_id, engine)
    if blocked:
        return blocked
    started = c.started(run.run_id, tool_id)

    try:
        frame = grab()
        path = frame.save()
    except CaptureError as exc:
        return run.record(c.failed(started, str(exc)))
    except Exception as exc:
        return run.record(c.failed(started, f"{label} capture failed: {exc}"))

    artifact = c.new_artifact(
        run_id=run.run_id, type="screenshot" if frame.source == "screen" else "image",
        title=f"{label} capture {frame.captured_iso}", path_or_uri=str(path),
        producer=tool_id,
        verification=c.Verification(
            method="image_written",
            evidence=f"{frame.width}x{frame.height} PNG, {len(frame.png)} bytes, "
                     f"sha256:{frame.digest}",
        ),
    )
    return run.record(c.succeeded(
        started,
        output=_scoped(frame.describe()),
        artifacts=(artifact,),
        side_effects=(f"captured a {label} frame to {path}",),
        verification=c.Verification(
            method=f"{frame.source}_frame_captured",
            evidence=f"{frame.width}x{frame.height} frame at {frame.captured_iso}, "
                     f"sha256:{frame.digest}, saved to {path}",
        ),
    ))


def camera_frame(
    run: c.Run, *, device: int = 0, engine: PolicyEngine = default_engine
) -> c.ActionResult:
    """Take one photo with the webcam and save it. Device released immediately."""
    return _capture_result(run, "vision.camera_frame", engine,
                           lambda: capture_camera(device), "camera")


def screen_capture(
    run: c.Run, *, monitor: int = 1, engine: PolicyEngine = default_engine
) -> c.ActionResult:
    """Capture the screen and save it."""
    return _capture_result(run, "vision.screen_capture", engine,
                           lambda: capture_screen(monitor), "screen")


def _inspect(
    run: c.Run, tool_id: str, engine: PolicyEngine, grab, question: str, label: str
) -> c.ActionResult:
    blocked = _gate(run, tool_id, engine)
    if blocked:
        return blocked
    started = c.started(run.run_id, tool_id)

    try:
        frame = grab()
        path = frame.save()
    except CaptureError as exc:
        return run.record(c.failed(started, str(exc)))
    except Exception as exc:
        return run.record(c.failed(started, f"{label} capture failed: {exc}"))

    # A question is about now. Refuse to answer about a frame that has aged.
    if frame.age_seconds > MAX_FRAME_AGE_SECONDS:
        return run.record(c.failed(
            started,
            f"frame is {frame.age_seconds:.0f}s old, older than the "
            f"{MAX_FRAME_AGE_SECONDS:.0f}s limit - capture again",
        ))

    artifact = c.new_artifact(
        run_id=run.run_id, type="screenshot" if frame.source == "screen" else "image",
        title=f"{label}: {question[:60]}", path_or_uri=str(path), producer=tool_id,
        verification=c.Verification(
            method="image_written",
            evidence=f"{frame.width}x{frame.height} PNG, sha256:{frame.digest}",
        ),
    )

    try:
        analysis = analyse_frame(frame, question)
    except CaptureError as exc:
        # The frame is real even though the analysis failed - keep the artifact.
        return run.record(c.partial(
            started, f"captured the frame but could not analyse it: {exc}",
            output=_scoped({"question": question, "frame": frame.describe()}),
            artifacts=(artifact,),
        ))

    confidence = float(analysis["confidence"])
    band = confidence_band(confidence)
    said = spoken_form(analysis, frame.source)
    payload = _scoped({
        "question": question,
        "frame": frame.describe(),
        "answered_about_frame": frame.digest,
        "frame_age_seconds": round(frame.age_seconds, 2),
        "observation": analysis.get("observation", ""),
        "identification": analysis.get("identification", ""),
        "confidence": confidence,
        "confidence_band": band,
        "uncertain_because": analysis.get("uncertain_because", ""),
        "suggested_better_view": analysis.get("suggested_better_view", ""),
        "text_found": analysis.get("text_found", ""),
        "spoken_form": said,
    })

    # The structural rule: only high confidence may be asserted. Anything less
    # is PARTIAL, so may_claim_completion is False and the agent must hedge.
    if band != "high":
        return run.record(c.partial(
            started,
            f"identification is {band} confidence ({confidence:.0%}) - "
            f"not certain enough to assert",
            output=payload, artifacts=(artifact,),
        ))

    return run.record(c.succeeded(
        started, output=payload, artifacts=(artifact,),
        verification=c.Verification(
            method="frame_analysed",
            evidence=f"answered from a {frame.width}x{frame.height} {frame.source} "
                     f"frame captured {frame.captured_iso} "
                     f"(sha256:{frame.digest}, {frame.age_seconds:.1f}s old); "
                     f"confidence {confidence:.0%}",
        ),
    ))


def inspect_camera(
    run: c.Run, question: str = "What am I holding?", *, device: int = 0,
    engine: PolicyEngine = default_engine,
) -> c.ActionResult:
    """Capture a fresh camera frame and answer a question about it."""
    return _inspect(run, "vision.inspect_camera", engine,
                    lambda: capture_camera(device), question, "camera")


def inspect_screen(
    run: c.Run, question: str = "What is on this screen?", *, monitor: int = 1,
    engine: PolicyEngine = default_engine,
) -> c.ActionResult:
    """Capture the screen and answer a question about it."""
    return _inspect(run, "vision.inspect_screen", engine,
                    lambda: capture_screen(monitor), question, "screen")
