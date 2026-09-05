"""
Audio, at the level Windows actually works at.

Batch 2C-A. The abstraction is the **session**, not the process: Windows Core
Audio groups rendering into sessions, one process can hold more than one, and
the Volume Mixer people see is a list of sessions. So the tools are named for
sessions and the process is a *label* on them - which is what makes "lower
Chrome" answerable when Chrome has three.

Two blast radii, deliberately separate:

    session     one app's slider. Lowering Spotify leaves everything else
                alone, and is the normal thing to want.
    endpoint    the master volume. Microsoft's own documentation says this is
                for clients like the system volume control, and that it
                disrupts every other application. It gets the stronger policy
                and is never the default reading of "turn it down".

Every change goes through `friday.reversible`, so it carries what it was
before it was touched. That is not only for tests: a multi-step task that
lowers the volume and then fails needs the old value to put back, and a local
variable in a function that has returned is not a record.
"""

from __future__ import annotations

import contextlib
import ctypes
import sys

# Windows Core Audio only exists on Windows, and pycaw/comtypes are declared
# `sys_platform == 'win32'` for that reason. The module still has to import
# elsewhere: `friday.tools` registers every toolset unconditionally, and one
# platform-specific ImportError took the whole registry - and with it 57
# unrelated tests - down on the ubuntu CI job (2026-09-05). Off Windows the
# tools stay registered and answer UNSUPPORTED; the audio machinery is simply
# absent.
AVAILABLE = sys.platform == "win32"

if AVAILABLE:
    import comtypes
    from pycaw.pycaw import (AudioUtilities, IAudioSessionControl2,
                             ISimpleAudioVolume)
else:                                                   # pragma: no cover
    comtypes = None
    AudioUtilities = IAudioSessionControl2 = ISimpleAudioVolume = None

from friday import contracts as c
from friday.policy import PolicyEngine, default_engine
from friday.toolsets.system import APPROVAL_PREFIX

EXECUTION_SCOPE = "local_machine"


def _com_errors() -> tuple[type[BaseException], ...]:
    """The exception classes a COM call can raise here; OSError alone off
    Windows, where `comtypes.COMError` does not exist."""
    if comtypes is None:
        return (OSError,)
    return (OSError, comtypes.COMError)

#: Volumes are floats 0.0-1.0 in the API and percentages to everyone else.
#: The conversion happens once, here, rather than at four call sites.
def to_percent(level: float) -> int:
    return int(round(max(0.0, min(1.0, level)) * 100))


def to_level(percent: float) -> float:
    return max(0.0, min(1.0, percent / 100.0))


#: A driver rounds. 30% asked for and 30% read back is not guaranteed, and
#: demanding it would fail on real hardware.
def close_enough(wanted: int, observed: int) -> bool:
    return abs(int(wanted) - int(observed)) <= 2


class AudioError(RuntimeError):
    """No session matched, or more than one did."""


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


def _unsupported_here(run: c.Run, started: c.ActionResult) -> c.ActionResult | None:
    """Off Windows there is no Core Audio to talk to. Said plainly, as
    UNSUPPORTED, rather than raised from inside a COM call that never ran."""
    if AVAILABLE:
        return None
    return run.record(started.finish(
        status=c.UNSUPPORTED,
        error=f"audio sessions need Windows Core Audio; not available on {sys.platform}",
    ))


@contextlib.contextmanager
def com():
    """
    COM, initialised on whichever thread is asking.

    `IAudioSessionControl2` requires the calling thread to have initialised
    COM, and the LiveKit job runner calls tools off the main thread - so
    initialising once at import time and assuming it carries is wrong. The
    pairing has to be explicit, and the uninitialise has to be in a `finally`
    or a raised tool error leaks a COM apartment per call.

    `CoInitialize` returning S_FALSE - already initialised on this thread - is
    not an error and is not treated as one; the matching uninitialise is still
    correct, because the counts are per-thread and balanced.
    """
    initialised = False
    try:
        comtypes.CoInitialize()
        initialised = True
    except OSError:
        # Already initialised with an incompatible apartment model. The work
        # can still proceed on the existing one; what must not happen is
        # uninitialising an apartment this call did not create.
        pass
    try:
        yield
    finally:
        if initialised:
            try:
                comtypes.CoUninitialize()
            except Exception:                           # noqa: BLE001
                pass


# ---------------------------------------------------------------------------
# Sessions
# ---------------------------------------------------------------------------


def _volume_of(session):
    return session._ctl.QueryInterface(ISimpleAudioVolume)


#: What Windows says a session is doing. ACTIVE means at least one stream in
#: the session is running; EXPIRED means the session holds no streams at all.
SESSION_STATES = {0: "inactive", 1: "active", 2: "expired"}

#: `IAudioSessionControl2::GetProcessId` returns this *success* code when the
#: session spans more than one process, and the pid it hands back is then only
#: the process that created the session. comtypes raises on failure codes and
#: silently discards success codes, so the pycaw `ProcessId` property cannot
#: tell the two apart - the raw vtable call can, and does below.
AUDCLNT_S_NO_SINGLE_PROCESS = 0x0889000D

SINGLE_PROCESS = "SINGLE_PROCESS"
MULTI_PROCESS = "MULTI_PROCESS"
UNKNOWN_SCOPE = "UNKNOWN"


def process_scope(control) -> tuple[str, int]:
    """
    Whether this session belongs to one process, and which.

    The distinction matters because a dead pid does NOT mean a dead session.
    A session spanning several processes reports the pid of whichever created
    it, and that one can exit while the session goes on making noise. Treating
    "pid is gone" as "session is stale" would hide a live one.
    """
    raw = getattr(control, "_IAudioSessionControl2__com_GetProcessId", None)
    if raw is None:
        # No way to see the success code. Say so rather than inferring scope
        # from a pid, which is the guess this whole function exists to avoid.
        try:
            return UNKNOWN_SCOPE, control.GetProcessId()
        except Exception:                               # noqa: BLE001
            return UNKNOWN_SCOPE, 0
    pid = ctypes.c_uint32()
    try:
        result = raw(ctypes.byref(pid)) & 0xFFFFFFFF
    except Exception:                                   # noqa: BLE001
        return UNKNOWN_SCOPE, 0
    if result == 0:
        return SINGLE_PROCESS, pid.value
    if result == AUDCLNT_S_NO_SINGLE_PROCESS:
        return MULTI_PROCESS, pid.value
    return UNKNOWN_SCOPE, pid.value


def describe_session(session) -> dict:
    """
    A session as data, identified by the thing Windows says is unique.

    `session_id` is the **session instance identifier**, which Microsoft
    documents as unique across individual session instances - unlike the plain
    session identifier, which several instances of the same application share.
    That is the identity; the pid and the process name are labels for
    resolving what a person said into one of these.

    Learned the hard way, three times now in this project. A pid is not a
    process identity (the executor), a window title is not window ownership
    (the Notepad incident), and a session pid is not a session identity: this
    machine listed a session whose pid psutil said was gone, with `State`
    reported as ACTIVE.
    """
    try:
        control = session._ctl.QueryInterface(IAudioSessionControl2)
        scope, pid = process_scope(control)
    except Exception:                                   # noqa: BLE001
        scope, pid = UNKNOWN_SCOPE, session.ProcessId

    process = session.Process
    try:
        volume = _volume_of(session)
        percent, muted = to_percent(volume.GetMasterVolume()), bool(volume.GetMute())
        controllable = True
    except Exception:                                   # noqa: BLE001
        percent, muted, controllable = None, None, False

    alive = None
    if pid:
        try:
            import psutil

            alive = psutil.pid_exists(pid)
        except Exception:                               # noqa: BLE001
            alive = None

    state = SESSION_STATES.get(getattr(session, "State", None), "unknown")

    # Deliberately NOT `state == active and process_alive`. That reading hides
    # a legitimate multi-process session whose creating process has exited.
    if state == "expired" or not controllable:
        actionable = False
    elif scope == SINGLE_PROCESS:
        actionable = alive is not False
    elif scope == MULTI_PROCESS:
        actionable = True          # the control is live; the pid is a hint
    else:
        actionable = state in ("active", "inactive")

    return {
        "session_id": (session.InstanceIdentifier or "").strip(),
        "shared_id": (session.Identifier or "").strip(),
        "pid": pid,
        "process": process.name() if process else "",
        "label": (session.DisplayName or "").strip(),
        "system_sounds": process is None,
        "state": state,
        "process_scope": scope,
        "process_alive": alive,
        "actionable": actionable,
        "controllable": controllable,
        "volume_percent": percent,
        "muted": muted,
    }


def session_by_id(session_id: str):
    """The one session with this instance identifier, or None."""
    wanted = (session_id or "").strip()
    if not wanted:
        return None
    for session in sessions():
        if (session.InstanceIdentifier or "").strip() == wanted:
            return session
    return None


def sessions() -> list:
    """Every rendering session, system sounds included."""
    if not AVAILABLE:
        return []
    try:
        with com():
            return list(AudioUtilities.GetAllSessions())
    except _com_errors():
        return []


def find_sessions(pattern: str, *, active_only: bool = True) -> list:
    """
    Sessions whose process name, label or pid matches, case-insensitively.

    One app can hold several sessions - a browser with three tabs playing is
    three - so this returns all of them and the caller decides. "Lower Chrome"
    meaning "all of Chrome's sessions" is a reasonable reading; acting on one
    of the three at random is not.

    `active_only` prefers sessions that are actually rendering. An expired
    session is a ghost: setting its volume succeeds and changes nothing
    anybody can hear. The fallback to inactive ones is deliberate, so that
    "nothing happened" stays answerable rather than becoming "no such app".
    """
    needle = (pattern or "").strip().lower()
    if not needle:
        return []
    matched = []
    for session in sessions():
        process = session.Process
        haystack = (f"{process.name() if process else ''} "
                    f"{session.DisplayName or ''} {session.ProcessId} "
                    f"{session.InstanceIdentifier or ''}").lower()
        if needle in haystack:
            matched.append(session)
    if not active_only:
        return matched
    live = [s for s in matched if describe_session(s)["actionable"]]
    return live or matched


def audio_sessions(
    run: c.Run, pattern: str = "", *, engine: PolicyEngine = default_engine
) -> c.ActionResult:
    """What is making sound, and how loud each one is."""
    tool_id = "audio.sessions"
    blocked = _gate(run, tool_id, engine)
    if blocked:
        return blocked
    started = c.started(run.run_id, tool_id)
    unsupported = _unsupported_here(run, started)
    if unsupported:
        return unsupported

    found = find_sessions(pattern) if pattern.strip() else sessions()
    described = [describe_session(s) for s in found]
    apps = [d for d in described if not d["system_sounds"]]
    return run.record(c.succeeded(
        started,
        output=_scoped({"sessions": described, "count": len(described),
                        "applications": len(apps)}),
        verification=c.Verification(
            method="wasapi_session_enumeration",
            evidence=(f"{len(described)} session(s), {len(apps)} from an "
                      f"application: "
                      + ", ".join(f"{d['process'] or 'system'}"
                                  f"@{d['volume_percent']}%" for d in described[:5])
                      ) if described else "no audio sessions are open"),
    ))


def audio_session_volume(
    run: c.Run, pattern: str, percent: int, *,
    engine: PolicyEngine = default_engine
) -> c.ActionResult:
    """
    Set one application's volume, and read it back.

    Refuses when the name matches several sessions: "lower Chrome" with three
    Chrome sessions is a question, and picking one is a coin toss with an
    audible result.
    """
    tool_id = "audio.session_volume"
    blocked = _gate(run, tool_id, engine)
    if blocked:
        return blocked
    started = c.started(run.run_id, tool_id)
    unsupported = _unsupported_here(run, started)
    if unsupported:
        return unsupported

    if not 0 <= percent <= 100:
        return run.record(c.failed(
            started, f"volume must be 0-100, not {percent}"))

    matched = find_sessions(pattern)
    if not matched:
        open_now = [d["process"] for d in
                    (describe_session(s) for s in sessions()) if d["process"]]
        return run.record(c.failed(
            started, f"nothing called {pattern!r} is making sound. "
                     f"Playing now: {open_now or 'nothing'}"))
    if len(matched) > 1:
        names = [f"{s.ProcessId}:{s.Process.name() if s.Process else 'system'}"
                 for s in matched]
        return run.record(c.failed(
            started, f"{len(matched)} sessions match {pattern!r} - say which: "
                     f"{names}"))

    session = matched[0]
    with com():
        volume = _volume_of(session)
        before = to_percent(volume.GetMasterVolume())
        try:
            volume.SetMasterVolume(to_level(percent), None)
        except _com_errors() as exc:
            return run.record(c.failed(started, f"the session refused: {exc}"))
        after = to_percent(volume.GetMasterVolume())
    described = describe_session(session)
    payload = _scoped({"session": described, "previous_percent": before,
                       "requested_percent": percent,
                       "observed_percent": after, "reversible": True})
    if not close_enough(percent, after):
        return run.record(c.partial(
            started,
            f"asked for {percent}% and it is {after}%",
            output=payload))
    return run.record(c.succeeded(
        started, output=payload,
        side_effects=(f"{described['process'] or 'system sounds'} volume "
                      f"{before}% -> {after}%",),
        verification=c.Verification(
            method="session_volume_read_back",
            evidence=f"{described['process'] or 'system sounds'} was {before}%"
                     f" and reads {after}%"),
    ))


def audio_session_mute(
    run: c.Run, pattern: str, muted: bool = True, *,
    engine: PolicyEngine = default_engine
) -> c.ActionResult:
    """Mute or unmute one application, and read it back."""
    tool_id = "audio.session_mute"
    blocked = _gate(run, tool_id, engine)
    if blocked:
        return blocked
    started = c.started(run.run_id, tool_id)
    unsupported = _unsupported_here(run, started)
    if unsupported:
        return unsupported

    matched = find_sessions(pattern)
    if not matched:
        return run.record(c.failed(
            started, f"nothing called {pattern!r} is making sound"))
    if len(matched) > 1:
        return run.record(c.failed(
            started, f"{len(matched)} sessions match {pattern!r} - say which"))

    session = matched[0]
    with com():
        volume = _volume_of(session)
        before = bool(volume.GetMute())
        try:
            volume.SetMute(bool(muted), None)
        except _com_errors() as exc:
            return run.record(c.failed(started, f"the session refused: {exc}"))
        after = bool(volume.GetMute())
    described = describe_session(session)
    payload = _scoped({"session": described, "previous_muted": before,
                       "requested_muted": bool(muted),
                       "observed_muted": after, "reversible": True})
    if after != bool(muted):
        return run.record(c.partial(
            started, f"asked to set mute={muted} and it is {after}",
            output=payload))
    return run.record(c.succeeded(
        started, output=payload,
        side_effects=(f"{described['process'] or 'system sounds'} "
                      f"{'muted' if after else 'unmuted'}",),
        verification=c.Verification(
            method="session_mute_read_back",
            evidence=f"{described['process'] or 'system sounds'} mute was "
                     f"{before} and reads {after}"),
    ))


# ---------------------------------------------------------------------------
# The endpoint: everything at once
# ---------------------------------------------------------------------------


def endpoint_volume():
    """
    The master volume control.

    Reuses what `friday.toolsets.system` already worked out rather than
    reinventing it: `AudioUtilities.GetSpeakers().EndpointVolume` on the
    installed pycaw, and `CoInitialize` because COM is per-thread and the
    LiveKit job runner calls tools off the main one. The version written from
    memory - `speakers.Activate(IID, CLSCTX_ALL, None)` - is the older pycaw
    shape and raises AttributeError here.
    """
    comtypes.CoInitialize()          # paired by the caller's `with com()`
    return AudioUtilities.GetSpeakers().EndpointVolume


def audio_master_volume(
    run: c.Run, percent: int, *, engine: PolicyEngine = default_engine
) -> c.ActionResult:
    """
    Set the master volume - everything on this machine at once.

    A bigger hammer than it sounds. Microsoft's documentation is explicit that
    the endpoint volume is for clients like the system volume control and that
    changing it disrupts every other application, so "turn it down" should
    reach for a session first and only land here when the boss means the
    machine rather than the thing playing.
    """
    tool_id = "audio.master_volume"
    blocked = _gate(run, tool_id, engine)
    if blocked:
        return blocked
    started = c.started(run.run_id, tool_id)
    unsupported = _unsupported_here(run, started)
    if unsupported:
        return unsupported

    if not 0 <= percent <= 100:
        return run.record(c.failed(
            started, f"volume must be 0-100, not {percent}"))
    try:
        with com():
            control = endpoint_volume()
            before = to_percent(control.GetMasterVolumeLevelScalar())
            control.SetMasterVolumeLevelScalar(to_level(percent), None)
            after = to_percent(control.GetMasterVolumeLevelScalar())
    except _com_errors() as exc:
        return run.record(c.failed(
            started, f"the audio endpoint refused or went away: {exc}"))

    payload = _scoped({"previous_percent": before, "requested_percent": percent,
                       "observed_percent": after, "reversible": True,
                       "scope": "every application on this machine"})
    if not close_enough(percent, after):
        return run.record(c.partial(
            started, f"asked for {percent}% and the endpoint is {after}%",
            output=payload))
    return run.record(c.succeeded(
        started, output=payload,
        side_effects=(f"master volume {before}% -> {after}%",),
        verification=c.Verification(
            method="endpoint_volume_read_back",
            evidence=f"master volume was {before}% and reads {after}%"),
    ))
