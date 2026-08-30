"""
Media toolset (Phase 1F): Spotify transport control, verified by state change.

§16 says to pick the integration from the actual installed environment. Here
that is: Spotify installed and running, and **no Web API credentials**. So
this is desktop control, with the Web API left as an upgrade path rather than
a requirement nobody has satisfied.

Two things make it more than "send a key and hope":

**Targeted, not global.** Media keys are routed by Windows to whichever app
owns the current media session, so a browser playing video can swallow them.
Instead the command is posted straight to Spotify's own window with
`WM_APPCOMMAND`, found by enumerating windows and matching the owning process.
Global media keys remain the fallback.

**Verified by state change.** Spotify writes the current track into its window
title - "Artist - Track" while playing, "Spotify Free"/"Spotify Premium" when
not. So a pause can be confirmed by the title going idle, and a skip by the
title becoming a *different* track. A command that produced no change is
PARTIAL, never succeeded: sending a keystroke is not evidence that music
changed.

That title is also the only "now playing" source available without the API,
which is why `spotify.current` works at all on this machine.
"""

from __future__ import annotations

import ctypes
import os
import time
import urllib.parse
from ctypes import wintypes

from friday import contracts as c
from friday.policy import PolicyEngine, default_engine
from friday.toolsets.system import APPROVAL_PREFIX

EXECUTION_SCOPE = "local_machine"

#: Titles Spotify shows when nothing is playing.
IDLE_TITLES = frozenset({"spotify", "spotify free", "spotify premium"})
#: Free-tier adverts occupy the title too; they are not a track.
AD_TITLES = frozenset({"advertisement", "spotify advertisement"})

WM_APPCOMMAND = 0x0319
APPCOMMAND = {
    "play_pause": 14,
    "next": 11,
    "previous": 12,
    "stop": 13,
}
#: Virtual key codes, used only when no Spotify window can be targeted.
MEDIA_VK = {"play_pause": 0xB3, "next": 0xB0, "previous": 0xB1, "stop": 0xB2}

STATE_SETTLE_SECONDS = 2.5
STATE_POLL = 0.15

_IS_WINDOWS = os.name == "nt"
if _IS_WINDOWS:
    _user32 = ctypes.WinDLL("user32", use_last_error=True)
    _EnumWindowsProc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
else:  # pragma: no cover - the toolset reports unsupported instead
    _user32 = None
    _EnumWindowsProc = None


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


# ---------------------------------------------------------------------------
# Finding Spotify
# ---------------------------------------------------------------------------


def spotify_pids() -> set[int]:
    import psutil

    pids = set()
    for proc in psutil.process_iter(["pid", "name"]):
        try:
            if (proc.info["name"] or "").lower() == "spotify.exe":
                pids.add(proc.info["pid"])
        except Exception:
            continue
    return pids


def _windows_for(pids: set[int]) -> list[tuple[int, str]]:
    """Visible titled windows owned by the given processes."""
    if not _IS_WINDOWS or not pids:
        return []
    found: list[tuple[int, str]] = []

    def callback(hwnd, _lparam):
        if not _user32.IsWindowVisible(hwnd):
            return True
        pid = wintypes.DWORD()
        _user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if pid.value in pids:
            length = _user32.GetWindowTextLengthW(hwnd)
            if length:
                buffer = ctypes.create_unicode_buffer(length + 1)
                _user32.GetWindowTextW(hwnd, buffer, length + 1)
                if buffer.value.strip():
                    found.append((hwnd, buffer.value))
        return True

    _user32.EnumWindows(_EnumWindowsProc(callback), 0)
    return found


def spotify_window() -> tuple[int, str] | None:
    """The Spotify window handle and its title, or None if not running."""
    windows = _windows_for(spotify_pids())
    return windows[0] if windows else None


def parse_title(title: str) -> dict:
    """
    Spotify's window title is the only now-playing source without the API.

    "Artist - Track" while playing; "Spotify Free" / "Spotify Premium" when
    paused or idle.
    """
    raw = (title or "").strip()
    lowered = raw.lower()
    if not raw or lowered in IDLE_TITLES:
        return {"playing": False, "raw_title": raw, "artist": "", "track": ""}
    if lowered in AD_TITLES:
        return {"playing": True, "raw_title": raw, "artist": "", "track": "",
                "advertisement": True}
    artist, separator, track = raw.partition(" - ")
    if not separator:
        return {"playing": True, "raw_title": raw, "artist": "", "track": raw}
    return {"playing": True, "raw_title": raw,
            "artist": artist.strip(), "track": track.strip()}


def current_state() -> dict | None:
    window = spotify_window()
    if window is None:
        return None
    _, title = window
    return parse_title(title)


def _send(hwnd: int | None, command: str) -> str:
    """Post a transport command. Returns the method used, for the evidence."""
    if hwnd is not None:
        _user32.SendMessageW(hwnd, WM_APPCOMMAND, hwnd, APPCOMMAND[command] << 16)
        return "wm_appcommand"
    # No Spotify window: fall back to the global media key, which Windows
    # routes to whatever owns the media session - possibly not Spotify.
    vk = MEDIA_VK[command]
    _user32.keybd_event(vk, 0, 0x0001, 0)
    _user32.keybd_event(vk, 0, 0x0001 | 0x0002, 0)
    return "global_media_key"


def _wait_for_change(before: dict, timeout: float = STATE_SETTLE_SECONDS) -> dict | None:
    """Poll the title until it differs from `before`, or give up."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        time.sleep(STATE_POLL)
        now = current_state()
        if now and now["raw_title"] != before["raw_title"]:
            return now
    return None


# ---------------------------------------------------------------------------
# Capabilities
# ---------------------------------------------------------------------------


def _require_running(run: c.Run, started: c.ActionResult):
    if not _IS_WINDOWS:
        return None, run.record(c.failed(
            started, f"media control not implemented for {os.name}"
        ))
    state = current_state()
    if state is None:
        return None, run.record(c.failed(
            started, "Spotify is not running - open it first with apps.open"
        ))
    return state, None


def spotify_open(run: c.Run, *, engine: PolicyEngine = default_engine) -> c.ActionResult:
    """Open Spotify, verified by process. Delegates to the Phase 1A launcher."""
    from friday.toolsets import system as S

    return S.apps_open(run, "spotify", engine=engine)


def spotify_current(
    run: c.Run, *, engine: PolicyEngine = default_engine
) -> c.ActionResult:
    tool_id = "spotify.current"
    blocked = _gate(run, tool_id, engine)
    if blocked:
        return blocked
    started = c.started(run.run_id, tool_id)

    state, failure = _require_running(run, started)
    if failure:
        return failure

    descriptor = (f"{state['artist']} - {state['track']}" if state["artist"]
                  else state["track"] or "nothing")
    return run.record(c.succeeded(
        started,
        output=_scoped({**state, "source": "window_title"}),
        verification=c.Verification(
            method="spotify_window_title",
            evidence=f"Spotify window title is {state['raw_title']!r} "
                     f"-> {'playing ' + descriptor if state['playing'] else 'not playing'}",
        ),
    ))


def _transport(
    run: c.Run, tool_id: str, command: str, engine: PolicyEngine,
    *, expect: str,
) -> c.ActionResult:
    """
    Send a transport command and confirm it from Spotify's own state.

    `expect` is one of "playing", "paused", "different" - what the title must
    become for the command to count as done.
    """
    blocked = _gate(run, tool_id, engine)
    if blocked:
        return blocked
    started = c.started(run.run_id, tool_id)

    before, failure = _require_running(run, started)
    if failure:
        return failure

    window = spotify_window()
    hwnd = window[0] if window else None
    method = _send(hwnd, command)

    after = _wait_for_change(before)

    if after is None:
        # Already in the requested state? Then nothing needed to change.
        if expect == "playing" and before["playing"]:
            return run.record(c.succeeded(
                started,
                output=_scoped({**before, "already": True, "method": method}),
                verification=c.Verification(
                    method="spotify_already_in_state",
                    evidence=f"already playing {before['raw_title']!r}; "
                             f"no change was needed",
                ),
            ))
        if expect == "paused" and not before["playing"]:
            return run.record(c.succeeded(
                started,
                output=_scoped({**before, "already": True, "method": method}),
                verification=c.Verification(
                    method="spotify_already_in_state",
                    evidence="already paused; no change was needed",
                ),
            ))
        return run.record(c.partial(
            started,
            f"sent {command} via {method} but Spotify's title did not change "
            f"within {STATE_SETTLE_SECONDS}s - cannot confirm it took effect",
            output=_scoped({"before": before, "method": method}),
        ))

    satisfied = (
        (expect == "playing" and after["playing"])
        or (expect == "paused" and not after["playing"])
        or (expect == "different" and after["raw_title"] != before["raw_title"])
    )
    if not satisfied:
        return run.record(c.partial(
            started,
            f"Spotify changed to {after['raw_title']!r}, which is not the "
            f"expected state ({expect})",
            output=_scoped({"before": before, "after": after, "method": method}),
        ))

    descriptor = (f"{after['artist']} - {after['track']}" if after["artist"]
                  else after["track"] or "nothing")
    return run.record(c.succeeded(
        started,
        output=_scoped({"before": before, "after": after, "method": method}),
        side_effects=(f"sent {command} to Spotify via {method}",),
        verification=c.Verification(
            method="spotify_title_changed",
            evidence=f"title {before['raw_title']!r} -> {after['raw_title']!r} "
                     f"after {command} via {method}"
                     + (f"; now playing {descriptor}" if after["playing"] else ""),
        ),
    ))


def spotify_pause(run: c.Run, *, engine: PolicyEngine = default_engine) -> c.ActionResult:
    return _transport(run, "spotify.pause", "play_pause", engine, expect="paused")


def spotify_resume(run: c.Run, *, engine: PolicyEngine = default_engine) -> c.ActionResult:
    return _transport(run, "spotify.resume", "play_pause", engine, expect="playing")


def spotify_next(run: c.Run, *, engine: PolicyEngine = default_engine) -> c.ActionResult:
    return _transport(run, "spotify.next", "next", engine, expect="different")


def spotify_previous(run: c.Run, *, engine: PolicyEngine = default_engine) -> c.ActionResult:
    return _transport(run, "spotify.previous", "previous", engine, expect="different")


def spotify_search(
    run: c.Run, query: str, *, engine: PolicyEngine = default_engine
) -> c.ActionResult:
    """
    Open a search in the Spotify desktop app.

    Without Web API credentials this can show results but cannot start
    playback, and that limit is reported rather than glossed over.
    """
    tool_id = "spotify.search"
    blocked = _gate(run, tool_id, engine)
    if blocked:
        return blocked
    started = c.started(run.run_id, tool_id)

    if not (query or "").strip():
        return run.record(c.failed(started, "empty search query"))
    if not _IS_WINDOWS:
        return run.record(c.failed(started, "media control not implemented here"))

    before = current_state()
    uri = f"spotify:search:{urllib.parse.quote(query.strip())}"
    try:
        os.startfile(uri)  # noqa: S606 - registered protocol handler
    except OSError as exc:
        return run.record(c.failed(started, f"could not open {uri}: {exc}"))

    deadline = time.monotonic() + 6
    state = None
    while time.monotonic() < deadline:
        time.sleep(0.25)
        state = current_state()
        if state is not None:
            break

    if state is None:
        return run.record(c.partial(
            started, f"opened {uri} but Spotify did not appear",
            output=_scoped({"query": query, "uri": uri}),
        ))

    return run.record(c.succeeded(
        started,
        output=_scoped({"query": query, "uri": uri, "state": state,
                        "playback_started": False,
                        "limitation": "Spotify Web API credentials are not "
                                      "configured, so results can be shown but "
                                      "playback cannot be started from here"}),
        side_effects=(f"opened Spotify search for {query!r}",),
        verification=c.Verification(
            method="spotify_search_uri_opened",
            evidence=f"handed {uri} to Spotify; window present with title "
                     f"{state['raw_title']!r}. Search shown, playback NOT started.",
        ),
    ))


def spotify_play(
    run: c.Run, query: str = "", *, engine: PolicyEngine = default_engine
) -> c.ActionResult:
    """
    Resume playback, or search for something specific.

    With no query this is a plain resume. With a query it opens the search -
    and says plainly that it could not start that specific track, because
    without the Web API it cannot.
    """
    if not (query or "").strip():
        return spotify_resume(run, engine=engine)

    result = spotify_search(run, query, engine=engine)
    if result.status != c.SUCCEEDED:
        return result

    # The search succeeded; starting *that* track did not happen. Downgrade so
    # the agent cannot say "playing Interstellar" when it only opened a search.
    return run.record(c.partial(
        c.started(run.run_id, "spotify.play"),
        f"opened a Spotify search for {query!r} but could not start playback - "
        f"that needs Spotify Web API credentials (SPOTIFY_CLIENT_ID / "
        f"SPOTIFY_CLIENT_SECRET) and a Premium account",
        output=result.output,
    ))
