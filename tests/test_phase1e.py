"""
Phase 1E: vision, on demand, without inventing anything.

The structural claim under test: confidence decides the ActionResult status,
so an uncertain identification cannot be asserted. Tests that need a camera or
the vision model are marked `live`.
"""

from __future__ import annotations

import pytest

from friday import contracts as c
from friday import policy as p
from friday.toolsets import vision as V

live = pytest.mark.live


@pytest.fixture
def run():
    return c.Run.create("test", capability="vision")


@pytest.fixture
def captures(tmp_path, monkeypatch):
    monkeypatch.setenv("ADA_VISION_DIR", str(tmp_path / "vision"))
    return tmp_path / "vision"


def fake_frame(source: str = "camera") -> V.Frame:
    return V.Frame(b"\x89PNG\r\n\x1a\n" + b"x" * 512, 640, 480, source)


# ---------------------------------------------------------------------------
# Confidence decides what may be asserted
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("confidence, band", [
    (1.0, "high"), (0.9, "high"), (0.75, "high"),
    (0.74, "medium"), (0.5, "medium"), (0.45, "medium"),
    (0.44, "low"), (0.1, "low"), (0.0, "low"),
])
def test_confidence_bands(confidence, band):
    assert V.confidence_band(confidence) == band


@pytest.mark.parametrize("confidence, opener", [
    (0.92, "I believe that's"),
    (0.60, "It looks like"),
    (0.20, "I'm not confident enough"),
])
def test_spoken_form_matches_the_band(confidence, opener):
    said = V.spoken_form(
        {"confidence": confidence, "identification": "a Raspberry Pi 5",
         "observation": "a small green circuit board"}, "camera")
    assert said.startswith(opener), said


def test_high_confidence_without_an_identification_does_not_hedge():
    """
    Regression. "Read this screen" is answered confidently but names no
    object; an earlier version fell through to the low-confidence text and
    said "I'm not confident enough" beside a 95% confidence and a succeeded
    status - contradicting itself.
    """
    said = V.spoken_form(
        {"confidence": 0.95, "identification": "",
         "observation": "a terminal window", "text_found": "271 passed"},
        "screen")
    assert "not confident" not in said
    assert "271 passed" in said


def test_high_confidence_with_neither_identification_nor_text():
    said = V.spoken_form(
        {"confidence": 0.9, "identification": "", "observation": "a dark room"},
        "camera")
    assert said == "I can see a dark room."


def test_model_sentences_are_tidied_into_ours():
    """'A web browser...' embedded mid-sentence produced 'that's A ...'."""
    said = V.spoken_form(
        {"confidence": 0.9, "identification": "A web browser showing GitHub."},
        "screen")
    assert said == "I believe that's a web browser showing GitHub."
    assert ".." not in said


def test_tidy_leaves_proper_nouns_alone():
    assert V._tidy("Raspberry Pi 5.") == "Raspberry Pi 5"
    assert V._tidy("An Arduino Uno") == "an Arduino Uno"
    assert V._tidy("The ChatGPT website.") == "the ChatGPT website"


def test_low_confidence_asks_for_a_better_view():
    said = V.spoken_form(
        {"confidence": 0.2, "identification": "maybe a board",
         "observation": "a dark blurry shape",
         "suggested_better_view": "Move it into the light."}, "camera")
    assert "Move it into the light." in said


# ---------------------------------------------------------------------------
# The structural rule: uncertain -> PARTIAL -> cannot be asserted
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("confidence, status, claimable", [
    (0.95, c.SUCCEEDED, True),
    (0.75, c.SUCCEEDED, True),
    (0.60, c.PARTIAL, False),
    (0.10, c.PARTIAL, False),
])
def test_uncertain_identifications_cannot_be_claimed(
    run, captures, monkeypatch, confidence, status, claimable
):
    monkeypatch.setattr(V, "capture_camera", lambda *a, **k: fake_frame())
    monkeypatch.setattr(V, "analyse_frame", lambda frame, question: {
        "observation": "a green circuit board",
        "identification": "a Raspberry Pi 5",
        "confidence": confidence,
    })

    result = V.inspect_camera(run, "What am I holding?")
    assert result.status == status
    assert result.may_claim_completion is claimable
    assert result.output["confidence"] == confidence


def test_a_partial_result_still_carries_the_evidence(run, captures, monkeypatch):
    monkeypatch.setattr(V, "capture_camera", lambda *a, **k: fake_frame())
    monkeypatch.setattr(V, "analyse_frame", lambda frame, question: {
        "observation": "something dark", "identification": "possibly a phone",
        "confidence": 0.3, "suggested_better_view": "More light.",
    })
    result = V.inspect_camera(run, "What is this?")
    assert result.status == c.PARTIAL
    assert result.artifacts, "the frame is real even when the answer is uncertain"
    assert "More light." in result.output["spoken_form"]


# ---------------------------------------------------------------------------
# No frame, no answer
# ---------------------------------------------------------------------------


def test_capture_failure_is_failed_not_a_guess(run, captures, monkeypatch):
    def boom(*a, **k):
        raise V.CaptureError("could not open camera 0")

    monkeypatch.setattr(V, "capture_camera", boom)
    result = V.inspect_camera(run, "What am I holding?")
    assert result.status == c.FAILED
    assert not result.may_claim_completion
    assert result.output is None


def test_analysis_failure_keeps_the_frame_but_does_not_answer(
    run, captures, monkeypatch
):
    monkeypatch.setattr(V, "capture_camera", lambda *a, **k: fake_frame())

    def boom(frame, question):
        raise V.CaptureError("vision analysis needs GOOGLE_API_KEY")

    monkeypatch.setattr(V, "analyse_frame", boom)
    result = V.inspect_camera(run, "What am I holding?")
    assert result.status == c.PARTIAL
    assert result.artifacts, "the captured frame is still real"
    assert "confidence" not in (result.output or {})


def test_a_stale_frame_is_refused(run, captures, monkeypatch):
    """A question is about now."""
    stale = fake_frame()
    stale.captured_at -= V.MAX_FRAME_AGE_SECONDS + 5
    monkeypatch.setattr(V, "capture_camera", lambda *a, **k: stale)
    result = V.inspect_camera(run, "What am I holding?")
    assert result.status == c.FAILED
    assert "older than" in result.error


def test_missing_monitor_fails(run, captures):
    result = V.screen_capture(run, monitor=99)
    if result.status == c.UNSUPPORTED:
        # No display on this machine (headless CI runner): the index check
        # cannot be reached because mss cannot open at all. That verdict is
        # asserted by test_no_display_is_unsupported_not_a_failed_capture.
        pytest.skip(result.error)
    assert result.status == c.FAILED
    assert "does not exist" in result.error


def test_no_display_is_unsupported_not_a_failed_capture(run, captures, monkeypatch):
    """The ubuntu CI runner (2026-09-05): mss raised "Cannot connect to
    display: display is unset or invalid (check $DISPLAY)" at construction,
    and the tool called it a FAILED capture of monitor 99. A machine with no
    screen is a fact about the machine, reported as UNSUPPORTED - the same
    class system.volume_* uses for "no audio endpoint"."""
    import mss

    class Headless:
        def __init__(self, *a, **k):
            raise mss.exception.ScreenShotError(
                "Cannot connect to display: display is unset or invalid (check $DISPLAY)")
    monkeypatch.setattr(mss, "MSS", Headless)
    result = V.screen_capture(run, monitor=1)
    assert result.status == c.UNSUPPORTED, result
    assert "no display" in result.error
    # a different construction failure is still a FAILURE, with the reason
    class Broken:
        def __init__(self, *a, **k):
            raise mss.exception.ScreenShotError("XGetImage() failed")
    monkeypatch.setattr(mss, "MSS", Broken)
    result = V.screen_capture(run, monitor=1)
    assert result.status == c.FAILED and "XGetImage" in result.error


# ---------------------------------------------------------------------------
# Provenance
# ---------------------------------------------------------------------------


def test_every_answer_names_the_frame_it_answered_about(run, captures, monkeypatch):
    frame = fake_frame()
    monkeypatch.setattr(V, "capture_camera", lambda *a, **k: frame)
    monkeypatch.setattr(V, "analyse_frame", lambda f, q: {
        "observation": "x", "identification": "a mug", "confidence": 0.9})

    result = V.inspect_camera(run, "What is this?")
    assert result.output["answered_about_frame"] == frame.digest
    assert frame.digest in result.verification.evidence
    assert result.output["frame"]["captured_at"] == frame.captured_iso


def test_capture_writes_a_retrievable_artifact(run, captures, monkeypatch):
    from pathlib import Path

    monkeypatch.setattr(V, "capture_camera", lambda *a, **k: fake_frame())
    result = V.camera_frame(run)
    assert result.status == c.SUCCEEDED
    artifact = result.artifacts[0]
    assert Path(artifact.path_or_uri).exists()
    assert artifact.type == "image"


def test_screen_artifacts_are_typed_as_screenshots(run, captures, monkeypatch):
    monkeypatch.setattr(V, "capture_screen", lambda *a, **k: fake_frame("screen"))
    result = V.screen_capture(run)
    assert result.artifacts[0].type == "screenshot"


def test_frame_digest_changes_with_content():
    assert fake_frame().digest == fake_frame().digest  # same bytes
    other = V.Frame(b"different bytes entirely", 1, 1, "camera")
    assert other.digest != fake_frame().digest


# ---------------------------------------------------------------------------
# Policy
# ---------------------------------------------------------------------------


def test_vision_policy_defaults():
    engine = p.PolicyEngine()
    for tool in ("vision.screen_capture", "vision.inspect_screen",
                 "vision.camera_frame", "vision.inspect_camera"):
        assert engine.decide(tool).decision == p.AUTO


def test_camera_can_be_gated_without_gating_screenshots():
    """The two categories exist so the camera can be locked down alone."""
    engine = p.PolicyEngine(overrides={p.CAMERA_CAPTURE: p.ASK})
    assert engine.decide("vision.camera_frame").decision == p.ASK
    assert engine.decide("vision.inspect_camera").decision == p.ASK
    assert engine.decide("vision.screen_capture").decision == p.AUTO


def test_gated_camera_is_cancelled_not_executed(run, captures, monkeypatch):
    from friday.toolsets.system import needs_approval

    def explode(*a, **k):
        raise AssertionError("camera opened despite policy")

    monkeypatch.setattr(V, "capture_camera", explode)
    engine = p.PolicyEngine(overrides={p.CAMERA_CAPTURE: p.ASK})
    result = V.camera_frame(run, engine=engine)
    assert needs_approval(result)


# ---------------------------------------------------------------------------
# Live hardware
# ---------------------------------------------------------------------------


@live
def test_live_screen_capture_produces_a_real_image(run):
    result = V.screen_capture(run)
    assert result.status == c.SUCCEEDED
    assert result.output["width"] > 0 and result.output["height"] > 0
    assert result.output["bytes"] > 1000


@live
def test_live_camera_is_released_between_captures(run):
    """§26: a held-open device would make the second grab fail."""
    first = V.camera_frame(run)
    if first.status != c.SUCCEEDED:
        pytest.skip(f"no usable camera: {first.error}")
    second = V.camera_frame(run)
    assert second.status == c.SUCCEEDED, "camera was not released after the first grab"
    assert second.output["sha256"] != first.output["sha256"]
