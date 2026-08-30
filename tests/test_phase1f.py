"""
Phase 1F: Spotify transport control, verified by state change.

The claim under test: sending a command is not evidence that music changed.
Only Spotify's own window title changing is.
"""

from __future__ import annotations

import pytest

from friday import contracts as c
from friday import policy as p
from friday.toolsets import media as M

live = pytest.mark.live


@pytest.fixture
def run():
    return c.Run.create("test", capability="media")


# ---------------------------------------------------------------------------
# Title parsing - the only now-playing source without the Web API
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("title, playing, artist, track", [
    ("Spotify Free", False, "", ""),
    ("Spotify Premium", False, "", ""),
    ("Spotify", False, "", ""),
    ("", False, "", ""),
    ("   ", False, "", ""),
    ("Hans Zimmer - Cornfield Chase", True, "Hans Zimmer", "Cornfield Chase"),
    ("Anuv Jain - Arz Kiya Hai | Coke Studio Bharat", True,
     "Anuv Jain", "Arz Kiya Hai | Coke Studio Bharat"),
    ("SomeTrackWithNoDash", True, "", "SomeTrackWithNoDash"),
])
def test_parse_title(title, playing, artist, track):
    parsed = M.parse_title(title)
    assert parsed["playing"] is playing
    assert parsed["artist"] == artist
    assert parsed["track"] == track


def test_advertisement_is_playing_but_not_a_track():
    parsed = M.parse_title("Advertisement")
    assert parsed["playing"] is True
    assert parsed.get("advertisement") is True
    assert parsed["track"] == ""


def test_a_track_with_a_dash_in_its_name_keeps_it():
    parsed = M.parse_title("Gajendra Verma - Mann Mera - Original Version")
    assert parsed["artist"] == "Gajendra Verma"
    assert parsed["track"] == "Mann Mera - Original Version"


# ---------------------------------------------------------------------------
# A command that changed nothing is not a success
# ---------------------------------------------------------------------------


def _fixed_state(playing: bool, title: str):
    return {"playing": playing, "raw_title": title, "artist": "", "track": title}


def test_no_state_change_is_partial_not_succeeded(run, monkeypatch):
    """Sending a keystroke is not evidence that music changed."""
    state = _fixed_state(True, "Artist - Track")
    monkeypatch.setattr(M, "current_state", lambda: state)
    monkeypatch.setattr(M, "spotify_window", lambda: (1234, "Artist - Track"))
    monkeypatch.setattr(M, "_send", lambda hwnd, command: "wm_appcommand")
    monkeypatch.setattr(M, "_wait_for_change", lambda before, timeout=0: None)

    result = M.spotify_next(run)
    assert result.status == c.PARTIAL
    assert not result.may_claim_completion
    assert "did not change" in result.error


def test_a_change_to_the_wrong_state_is_partial(run, monkeypatch):
    before = _fixed_state(True, "Artist - Track")
    after = _fixed_state(False, "Spotify Free")
    monkeypatch.setattr(M, "current_state", lambda: before)
    monkeypatch.setattr(M, "spotify_window", lambda: (1, "Artist - Track"))
    monkeypatch.setattr(M, "_send", lambda hwnd, command: "wm_appcommand")
    monkeypatch.setattr(M, "_wait_for_change", lambda before_, timeout=0: after)

    # resume expects "playing"; the title went idle instead
    result = M.spotify_resume(run)
    assert result.status == c.PARTIAL
    assert "not the expected state" in result.error


def test_already_in_the_requested_state_is_success(run, monkeypatch):
    """Asking to pause something already paused is done, not failed."""
    paused = _fixed_state(False, "Spotify Free")
    monkeypatch.setattr(M, "current_state", lambda: paused)
    monkeypatch.setattr(M, "spotify_window", lambda: (1, "Spotify Free"))
    monkeypatch.setattr(M, "_send", lambda hwnd, command: "wm_appcommand")
    monkeypatch.setattr(M, "_wait_for_change", lambda before, timeout=0: None)

    result = M.spotify_pause(run)
    assert result.status == c.SUCCEEDED
    assert result.output["already"] is True
    assert "no change was needed" in result.verification.evidence


def test_successful_transport_records_both_titles(run, monkeypatch):
    before = _fixed_state(False, "Spotify Free")
    after = {"playing": True, "raw_title": "Hans Zimmer - Time",
             "artist": "Hans Zimmer", "track": "Time"}
    monkeypatch.setattr(M, "current_state", lambda: before)
    monkeypatch.setattr(M, "spotify_window", lambda: (1, "Spotify Free"))
    monkeypatch.setattr(M, "_send", lambda hwnd, command: "wm_appcommand")
    monkeypatch.setattr(M, "_wait_for_change", lambda b, timeout=0: after)

    result = M.spotify_resume(run)
    assert result.status == c.SUCCEEDED
    assert "Spotify Free" in result.verification.evidence
    assert "Hans Zimmer - Time" in result.verification.evidence


def test_not_running_fails_rather_than_pretending(run, monkeypatch):
    monkeypatch.setattr(M, "current_state", lambda: None)
    for call in (M.spotify_current, M.spotify_pause, M.spotify_next):
        result = call(run)
        assert result.status == c.FAILED
        assert "not running" in result.error


# ---------------------------------------------------------------------------
# The honest limit: search is not playback
# ---------------------------------------------------------------------------


def test_play_with_a_query_cannot_claim_it_played(run, monkeypatch):
    """
    Without Web API credentials a query can only open a search. The agent must
    not be able to say "playing Interstellar" when it opened a search box.
    """
    monkeypatch.setattr(M, "current_state", lambda: _fixed_state(False, "Spotify Free"))
    monkeypatch.setattr(M, "spotify_window", lambda: (1, "Spotify Free"))
    monkeypatch.setattr(M, "os", M.os)  # keep module os
    monkeypatch.setattr(M.os, "startfile", lambda uri: None, raising=False)

    result = M.spotify_play(run, "Interstellar soundtrack")
    assert result.status == c.PARTIAL
    assert not result.may_claim_completion
    assert "could not start playback" in result.error
    assert result.output["playback_started"] is False


def test_play_without_a_query_is_a_plain_resume(run, monkeypatch):
    calls = []
    monkeypatch.setattr(M, "spotify_resume",
                        lambda r, engine=None: calls.append("resume") or "x")
    M.spotify_play(run, "")
    assert calls == ["resume"]


def test_empty_search_is_refused(run):
    assert M.spotify_search(run, "   ").status == c.FAILED


def test_search_reports_that_playback_did_not_start(run, monkeypatch):
    monkeypatch.setattr(M, "current_state", lambda: _fixed_state(False, "Spotify Free"))
    monkeypatch.setattr(M.os, "startfile", lambda uri: None, raising=False)
    result = M.spotify_search(run, "Interstellar")
    assert result.status == c.SUCCEEDED
    assert result.output["playback_started"] is False
    assert "playback NOT started" in result.verification.evidence


# ---------------------------------------------------------------------------
# Policy
# ---------------------------------------------------------------------------


def test_media_policy_defaults():
    engine = p.PolicyEngine()
    for tool in ("spotify.current", "spotify.play", "spotify.pause",
                 "spotify.next", "spotify.previous", "spotify.search"):
        assert engine.decide(tool).decision == p.AUTO


# ---------------------------------------------------------------------------
# Live
# ---------------------------------------------------------------------------


@live
def test_live_spotify_state_is_readable(run):
    if M.spotify_window() is None:
        pytest.skip("Spotify is not running")
    result = M.spotify_current(run)
    assert result.status == c.SUCCEEDED
    assert "raw_title" in result.output
