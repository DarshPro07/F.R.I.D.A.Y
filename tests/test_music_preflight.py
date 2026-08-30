"""
Check the player before doing the work that needs it.

Measured on a machine with no ffmpeg installed: `music_play("daft punk")` took
**39.3 seconds** to answer "ffplay not found on PATH". A YouTube search and a
stream resolve, both thrown away, to discover something knowable in a
microsecond - and the boss waited forty seconds for an answer that existed
before he finished speaking.

The agentability gate is what surfaced it. Routing was correct throughout: the
model reached `music_play` every time. The capability underneath it was the
thing that was broken, which is exactly the distinction the four-layer model
exists to keep separate.
"""

from __future__ import annotations

import time

import pytest

from friday import contracts as c
from friday import policy as P
from friday.toolsets import music as M


@pytest.fixture
def run():
    return c.Run.create("play something", capability="music")


@pytest.fixture
def no_player(monkeypatch):
    """A machine with no ffmpeg, and no override pointing at one."""
    monkeypatch.delenv("ADA_FFPLAY", raising=False)
    monkeypatch.setattr(M.shutil, "which", lambda name: None)


def test_a_missing_player_is_reported_before_the_search(run, no_player,
                                                        monkeypatch):
    def explode(*args, **kwargs):
        raise AssertionError("searched YouTube before checking for a player")

    monkeypatch.setattr(M, "search", explode)

    started = time.monotonic()
    result = M.music_play(run, "daft punk")
    assert result.status == c.FAILED
    assert "ffplay" in (result.error or "")
    assert time.monotonic() - started < 1.0, "still doing the slow work first"


def test_the_message_says_what_to_do_about_it(run, no_player):
    """
    "Install ffmpeg" is unhelpful advice for someone who has it installed
    three times in directories a stale PATH cannot see.
    """
    result = M.music_play(run, "daft punk")
    assert "ADA_FFPLAY" in (result.error or "")


def test_a_mood_request_fails_the_same_way(run, no_player, monkeypatch):
    monkeypatch.setattr(M, "search", lambda *a, **k: (_ for _ in ()).throw(
        AssertionError("searched before checking for a player")))
    result = M.music_play_mood(run, "focus")
    assert result.status == c.FAILED
    assert "ffplay" in (result.error or "")


def test_an_override_pointing_nowhere_is_refused(run, monkeypatch):
    monkeypatch.setenv("ADA_FFPLAY", r"C:\nowhere\ffplay.exe")
    result = M.music_play(run, "daft punk")
    assert result.status == c.FAILED
    assert "not a file" in (result.error or "")


def test_a_missing_player_never_reports_success(run, no_player):
    """The proof-of-work rule, at the one place it matters most here."""
    result = M.music_play(run, "daft punk")
    assert not result.may_claim_completion
    assert result.verification is None
