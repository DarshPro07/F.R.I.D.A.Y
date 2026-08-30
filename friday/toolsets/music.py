"""
Music toolset: play any song by name, with no account and no subscription.

Why this exists. Spotify Free blocks the Web API behind Premium, and desktop
automation proved too unreliable to ship - vision could *see* the right track
at 95% confidence, but focus after the search URI opened was racy, and a music
command that occasionally clicks a random window in another app is worse than
no command at all. That was measured, not assumed:

    blind keystrokes after search       0/5 strategies started playback
    vision locating the track           found it, 95% confidence
    focus after `spotify:search:` URI   failed; the click guard refused to fire

So playback comes from YouTube via yt-dlp instead. It needs no account, plays
essentially anything by name, and every step is verifiable.

The transport path was also measured. A direct stream URL 403s when handed to
an external player, because YouTube issues it to the client that asked:

    stream, player_client=web        403 Forbidden
    stream, player_client=android    plays, ~2s to first audio
    stream, player_client=ios        format unavailable
    download then play               plays, ~3.3s for a 2 MB track

Streaming through the android client is the default; download is the fallback.

Verification is the process plus the resolved title: "ffplay pid 1234 alive,
playing <exact title>". Nothing here can claim a song is playing when no
player is running.
"""

from __future__ import annotations

import ctypes
import os
import shutil
import subprocess
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path

from friday import contracts as c
from friday.policy import PolicyEngine, default_engine
from friday.toolsets.system import APPROVAL_PREFIX

EXECUTION_SCOPE = "local_machine"

#: Clients whose stream URLs an external player is allowed to fetch, in the
#: order they are tried.
#:
#: Measured 2026-08-16 against a real video, extracting and then fetching the
#: resulting URL: `android` and `android_vr` returned 200 and real bytes.
#: `web_safari`, `mweb`, `ios` and `web_embedded` all failed extraction with
#: "Requested format is not available", and `tv` with "This video is DRM
#: protected". The last two are kept as long shots because which clients work
#: changes on YouTube's schedule, not ours - but a list whose fallbacks are all
#: dead is a list with no fallback, which is what this was.
STREAM_CLIENTS = ("android", "android_vr", "web_embedded", "tv")
AUDIO_FORMAT = "bestaudio[ext=m4a]/bestaudio/best"

SEARCH_LIMIT = 8
START_TIMEOUT = 12.0

#: Moods mapped to search phrasing. Deliberately plain text: there is no audio
#: analysis here, so the honest mechanism is a better query, not a fake score.
MOODS = {
    "happy": "upbeat happy feel good songs",
    "relaxing": "relaxing calm chill music",
    "party": "party dance hits",
    "focus": "focus concentration instrumental music",
    "sad": "sad emotional songs",
    "energetic": "high energy workout songs",
    "romantic": "romantic love songs",
}

_NO_WINDOW = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}


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


class MusicError(RuntimeError):
    """Search, resolution or playback failed."""


def ffplay_path() -> str:
    """
    Where ffplay is, PATH or not.

    ADA_FFPLAY exists because PATH is not one thing on Windows. This machine
    has ffplay.exe in three separate directories, all of them in the user PATH
    in the registry, and none of them visible to a process whose environment
    was inherited before those entries were added. "Install ffmpeg" is unhelpful
    advice to give someone who already has it installed three times.
    """
    override = os.getenv("ADA_FFPLAY", "").strip().strip('"')
    if override:
        if not Path(override).is_file():
            raise MusicError(f"ADA_FFPLAY points at {override!r}, which is not a file")
        return override
    found = shutil.which("ffplay")
    if not found:
        raise MusicError(
            "ffplay not found on PATH - install ffmpeg, or set ADA_FFPLAY to "
            "the full path of ffplay.exe if it is installed but not on PATH"
        )
    return found


# ---------------------------------------------------------------------------
# Search and resolve
# ---------------------------------------------------------------------------


def _duration(seconds) -> str:
    total = int(seconds or 0)
    return f"{total // 60}:{total % 60:02d}"


def search(query: str, limit: int = SEARCH_LIMIT) -> list[dict]:
    """
    Fast metadata-only search.

    `extract_flat` with an explicit `ytsearchN:` prefix takes ~1.2s; relying on
    `default_search` returned zero entries, and a full extraction took 13.8s.
    """
    from yt_dlp import YoutubeDL

    with YoutubeDL({"quiet": True, "no_warnings": True, "skip_download": True,
                    "extract_flat": True, "noplaylist": True}) as ydl:
        info = ydl.extract_info(f"ytsearch{limit}:{query}", download=False)

    results = []
    for entry in (info.get("entries") or []):
        if not entry.get("id"):
            continue
        results.append({
            "id": entry["id"],
            "title": (entry.get("title") or "").strip(),
            "channel": (entry.get("channel") or entry.get("uploader") or "").strip(),
            "duration_seconds": int(entry.get("duration") or 0),
            "duration": _duration(entry.get("duration")),
            "url": f"https://www.youtube.com/watch?v={entry['id']}",
        })
    return results


def resolve(video_id: str) -> dict:
    """Get a playable audio stream, trying clients whose URLs work externally."""
    from yt_dlp import YoutubeDL

    url = f"https://www.youtube.com/watch?v={video_id}"
    failures = []
    for client in STREAM_CLIENTS:
        try:
            with YoutubeDL({
                "quiet": True, "no_warnings": True, "format": AUDIO_FORMAT,
                "extractor_args": {"youtube": {"player_client": [client]}},
            }) as ydl:
                info = ydl.extract_info(url, download=False)
        except Exception as exc:
            failures.append(f"{client}: {type(exc).__name__}")
            continue
        if info.get("url"):
            return {
                "stream_url": info["url"],
                "headers": info.get("http_headers") or {},
                "client": client,
                "title": (info.get("title") or "").strip(),
                "channel": (info.get("channel") or info.get("uploader") or "").strip(),
                "duration_seconds": int(info.get("duration") or 0),
                "video_id": video_id,
            }
        failures.append(f"{client}: no url")
    raise MusicError(f"could not resolve a stream for {video_id}: {'; '.join(failures)}")


def download(video_id: str, directory: Path | None = None) -> dict:
    """
    Fallback: fetch the audio, then play the file.

    One client per attempt, like `resolve`. This asked for no client at all
    until 2026-08-16 and reliably died on "HTTP Error 403: Forbidden" - the
    path whose whole job is to rescue a failed stream was the likelier of the
    two to fail. Handing yt-dlp the client *list* does not fix it either: it
    merges the formats from every client and the format selector then picks the
    highest bitrate one, which is routinely from a client whose URLs 403.
    """
    from yt_dlp import YoutubeDL

    target_dir = directory or Path(tempfile.mkdtemp(prefix="ada_music_"))
    target_dir.mkdir(parents=True, exist_ok=True)
    url = f"https://www.youtube.com/watch?v={video_id}"
    failures = []
    for client in STREAM_CLIENTS:
        for stale in target_dir.glob("track.*"):
            stale.unlink(missing_ok=True)  # a partial file is not a success
        try:
            with YoutubeDL({
                "quiet": True, "no_warnings": True, "format": AUDIO_FORMAT,
                "outtmpl": str(target_dir / "track.%(ext)s"), "noprogress": True,
                "extractor_args": {"youtube": {"player_client": [client]}},
            }) as ydl:
                info = ydl.extract_info(url, download=True)
        except Exception as exc:
            failures.append(f"{client}: {type(exc).__name__}")
            continue

        files = [f for f in target_dir.glob("track.*") if f.stat().st_size > 0]
        if not files:
            failures.append(f"{client}: no file")
            continue
        return {"path": str(files[0]), "title": (info.get("title") or "").strip(),
                "channel": (info.get("channel") or "").strip(),
                "duration_seconds": int(info.get("duration") or 0),
                "video_id": video_id, "client": client}
    raise MusicError(f"could not download {video_id}: {'; '.join(failures)}")


# ---------------------------------------------------------------------------
# The player
# ---------------------------------------------------------------------------


@dataclass
class Player:
    """One ffplay process, plus the queue around it."""

    process: subprocess.Popen | None = None
    track: dict | None = None
    started_at: float = 0.0
    paused: bool = False
    queue: list[dict] = field(default_factory=list)
    history: list[dict] = field(default_factory=list)
    _handle: int | None = None

    @property
    def alive(self) -> bool:
        return self.process is not None and self.process.poll() is None

    @property
    def playing(self) -> bool:
        return self.alive and not self.paused

    def start(self, source: str, headers: dict | None = None) -> None:
        self.stop()
        command = [ffplay_path(), "-nodisp", "-autoexit", "-loglevel", "error"]
        if headers:
            command += ["-headers",
                        "".join(f"{k}: {v}\r\n" for k, v in headers.items())]
        command.append(source)
        self.process = subprocess.Popen(
            command, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
            **_NO_WINDOW)
        self.started_at = time.monotonic()
        self.paused = False
        self._handle = None

    def _open_handle(self) -> int:
        if self._handle is None and self.process is not None:
            self._handle = ctypes.windll.kernel32.OpenProcess(
                0x1F0FFF, False, self.process.pid)
        return self._handle or 0

    def pause(self) -> bool:
        """
        Suspend the player process. ffplay has no IPC control channel, so the
        process itself is paused - audio stops immediately and resumes from
        the same point.
        """
        if not self.alive or self.paused or os.name != "nt":
            return False
        ctypes.WinDLL("ntdll").NtSuspendProcess(self._open_handle())
        self.paused = True
        return True

    def resume(self) -> bool:
        if not self.alive or not self.paused or os.name != "nt":
            return False
        ctypes.WinDLL("ntdll").NtResumeProcess(self._open_handle())
        self.paused = False
        return True

    def stop(self) -> bool:
        was = self.alive
        if self.process is not None:
            if self.paused:
                self.resume()
            try:
                self.process.terminate()
                self.process.wait(timeout=5)
            except Exception:
                try:
                    self.process.kill()
                except Exception:
                    pass
        if self._handle:
            ctypes.windll.kernel32.CloseHandle(self._handle)
        self.process = None
        self._handle = None
        self.paused = False
        if self.track:
            self.history.append(self.track)
        self.track = None
        return was

    def elapsed(self) -> float:
        return round(time.monotonic() - self.started_at, 1) if self.alive else 0.0


player = Player()


def _start_track(entry: dict) -> dict:
    """Resolve and start a track, falling back to download if streaming fails."""
    try:
        resolved = resolve(entry["id"])
        player.start(resolved["stream_url"], resolved["headers"])
        method = f"stream:{resolved['client']}"
        info = resolved
    except MusicError:
        info = download(entry["id"])
        player.start(info["path"])
        method = "download"

    # ffplay exits immediately on a bad source; give it a moment and check.
    deadline = time.monotonic() + 6
    while time.monotonic() < deadline:
        time.sleep(0.3)
        if player.alive:
            break
    if not player.alive:
        detail = ""
        if player.process is not None and player.process.stderr:
            try:
                detail = (player.process.stderr.read() or b"").decode(
                    "utf-8", "replace")[:200]
            except Exception:
                pass
        raise MusicError(f"player exited immediately: {detail or 'no output'}")

    player.track = {
        "title": info.get("title") or entry.get("title", ""),
        "channel": info.get("channel") or entry.get("channel", ""),
        "video_id": entry["id"],
        "duration_seconds": info.get("duration_seconds")
                            or entry.get("duration_seconds", 0),
        "method": method,
    }
    return player.track


# ---------------------------------------------------------------------------
# Capabilities
# ---------------------------------------------------------------------------


def music_search(
    run: c.Run, query: str, *, limit: int = SEARCH_LIMIT,
    engine: PolicyEngine = default_engine,
) -> c.ActionResult:
    tool_id = "music.search"
    blocked = _gate(run, tool_id, engine)
    if blocked:
        return blocked
    started = c.started(run.run_id, tool_id)

    if not (query or "").strip():
        return run.record(c.failed(started, "empty query"))
    try:
        results = search(query, limit)
    except Exception as exc:
        return run.record(c.failed(started, f"search failed: {exc}"))
    if not results:
        return run.record(c.failed(started, f"nothing found for {query!r}"))

    return run.record(c.succeeded(
        started,
        output=_scoped({"query": query, "count": len(results), "results": results}),
        verification=c.Verification(
            method="youtube_search",
            evidence=f"{len(results)} result(s) for {query!r}; first is "
                     f"{results[0]['title'][:70]!r} ({results[0]['duration']})",
        ),
    ))


def music_play(
    run: c.Run, query: str, *, engine: PolicyEngine = default_engine
) -> c.ActionResult:
    """Find a song by name and actually play it."""
    tool_id = "music.play"
    blocked = _gate(run, tool_id, engine)
    if blocked:
        return blocked
    started = c.started(run.run_id, tool_id)

    if not (query or "").strip():
        return run.record(c.failed(started, "empty query"))
    # Before the search, not after it.
    #
    # Measured: 39.3 seconds to report "ffplay not found on PATH" - a full
    # YouTube search and stream resolve, all of it thrown away, to discover
    # something knowable in a microsecond. The boss waited forty seconds for
    # an answer that was available before he finished speaking.
    try:
        ffplay_path()
    except MusicError as exc:
        return run.record(c.failed(started, str(exc)))
    try:
        results = search(query, SEARCH_LIMIT)
    except Exception as exc:
        return run.record(c.failed(started, f"search failed: {exc}"))
    if not results:
        return run.record(c.failed(started, f"nothing found for {query!r}"))

    player.queue = results[1:]
    try:
        track = _start_track(results[0])
    except MusicError as exc:
        return run.record(c.failed(started, str(exc)))

    return run.record(c.succeeded(
        started,
        output=_scoped({"query": query, "now_playing": track,
                        "queued": len(player.queue), "pid": player.process.pid}),
        side_effects=(f"started playback of {track['title']!r}",),
        verification=c.Verification(
            method="player_process_running",
            evidence=f"ffplay pid={player.process.pid} alive, playing "
                     f"{track['title']!r} via {track['method']}",
        ),
    ))


def music_play_mood(
    run: c.Run, mood: str, *, engine: PolicyEngine = default_engine
) -> c.ActionResult:
    """
    Play something matching a mood.

    The mechanism is an honest one: a better search query. There is no audio
    analysis here - Spotify's audio-features endpoint is deprecated for new
    apps - so this does not pretend to have measured energy or valence.
    """
    tool_id = "music.play_mood"
    blocked = _gate(run, tool_id, engine)
    if blocked:
        return blocked
    started = c.started(run.run_id, tool_id)

    key = (mood or "").strip().lower()
    if key not in MOODS:
        return run.record(c.failed(
            started, f"unknown mood {mood!r}; known: {sorted(MOODS)}"))

    result = music_play(run, MOODS[key], engine=engine)
    if result.status == c.SUCCEEDED and isinstance(result.output, dict):
        result.output["mood"] = key
        result.output["selected_by"] = "search phrasing, not audio analysis"
    return result


def music_pause(run: c.Run, *, engine: PolicyEngine = default_engine) -> c.ActionResult:
    tool_id = "music.pause"
    blocked = _gate(run, tool_id, engine)
    if blocked:
        return blocked
    started = c.started(run.run_id, tool_id)

    if not player.alive:
        return run.record(c.failed(started, "nothing is playing"))
    if player.paused:
        return run.record(c.succeeded(
            started, output=_scoped({"already_paused": True}),
            verification=c.Verification(method="player_state",
                                        evidence="already paused"),
        ))
    if not player.pause():
        return run.record(c.failed(started, "could not pause the player"))
    return run.record(c.succeeded(
        started,
        output=_scoped({"paused": True, "track": player.track,
                        "elapsed_seconds": player.elapsed()}),
        side_effects=("suspended the player process",),
        verification=c.Verification(
            method="player_suspended",
            evidence=f"ffplay pid={player.process.pid} suspended; "
                     f"playing={player.playing}",
        ),
    ))


def music_resume(run: c.Run, *, engine: PolicyEngine = default_engine) -> c.ActionResult:
    tool_id = "music.resume"
    blocked = _gate(run, tool_id, engine)
    if blocked:
        return blocked
    started = c.started(run.run_id, tool_id)

    if not player.alive:
        return run.record(c.failed(started, "nothing to resume"))
    if not player.paused:
        return run.record(c.succeeded(
            started, output=_scoped({"already_playing": True}),
            verification=c.Verification(method="player_state",
                                        evidence="already playing"),
        ))
    player.resume()
    return run.record(c.succeeded(
        started,
        output=_scoped({"track": player.track}),
        verification=c.Verification(
            method="player_resumed",
            evidence=f"ffplay pid={player.process.pid} resumed; "
                     f"playing={player.playing}",
        ),
    ))


def music_stop(run: c.Run, *, engine: PolicyEngine = default_engine) -> c.ActionResult:
    tool_id = "music.stop"
    blocked = _gate(run, tool_id, engine)
    if blocked:
        return blocked
    started = c.started(run.run_id, tool_id)

    track = player.track
    if not player.stop():
        return run.record(c.succeeded(
            started, output=_scoped({"was_playing": False}),
            verification=c.Verification(method="player_state",
                                        evidence="nothing was playing"),
        ))
    return run.record(c.succeeded(
        started, output=_scoped({"was_playing": True, "stopped": track}),
        side_effects=("stopped playback",),
        verification=c.Verification(
            method="player_stopped",
            evidence=f"player terminated; alive={player.alive}",
        ),
    ))


def music_next(run: c.Run, *, engine: PolicyEngine = default_engine) -> c.ActionResult:
    tool_id = "music.next"
    blocked = _gate(run, tool_id, engine)
    if blocked:
        return blocked
    started = c.started(run.run_id, tool_id)

    if not player.queue:
        return run.record(c.failed(started, "nothing queued - play something first"))
    previous = player.track
    entry = player.queue.pop(0)
    try:
        track = _start_track(entry)
    except MusicError as exc:
        return run.record(c.failed(started, str(exc)))

    return run.record(c.succeeded(
        started,
        output=_scoped({"previous": previous, "now_playing": track,
                        "queued": len(player.queue)}),
        side_effects=(f"skipped to {track['title']!r}",),
        verification=c.Verification(
            method="player_process_running",
            evidence=f"now playing {track['title']!r} "
                     f"(was {(previous or {}).get('title', 'nothing')!r}); "
                     f"ffplay pid={player.process.pid} alive",
        ),
    ))


def music_current(run: c.Run, *, engine: PolicyEngine = default_engine) -> c.ActionResult:
    tool_id = "music.current"
    blocked = _gate(run, tool_id, engine)
    if blocked:
        return blocked
    started = c.started(run.run_id, tool_id)

    if not player.alive:
        return run.record(c.succeeded(
            started, output=_scoped({"playing": False, "track": None}),
            verification=c.Verification(
                method="player_state", evidence="no player process is running"),
        ))
    return run.record(c.succeeded(
        started,
        output=_scoped({"playing": player.playing, "paused": player.paused,
                        "track": player.track,
                        "elapsed_seconds": player.elapsed(),
                        "queued": len(player.queue)}),
        verification=c.Verification(
            method="player_state",
            evidence=f"ffplay pid={player.process.pid} alive, "
                     f"paused={player.paused}, playing "
                     f"{(player.track or {}).get('title', '?')!r} "
                     f"for {player.elapsed()}s",
        ),
    ))
