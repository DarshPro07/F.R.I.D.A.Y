"""
Audio sessions, and knowing which one you are holding.

The live gate proves the transaction against real Core Audio. This proves the
identity model, which is where the mistakes were:

    a session outlives its process
    Windows reports such a session as ACTIVE
    the pid it reports may be only the process that CREATED the session
    several instances of one app share a session identifier

So the primary identity is the session *instance* identifier, and everything
about the process is a label used to resolve what a person said.
"""

from __future__ import annotations

import pytest

from friday import contracts as c
from friday.toolsets import audio as A


class FakeVolume:
    def __init__(self, level=1.0, muted=False, refuses=False):
        self.level, self.muted, self.refuses = level, muted, refuses
        self.contexts: list = []

    def GetMasterVolume(self):          # noqa: N802 - the COM name
        return self.level

    def SetMasterVolume(self, level, context):   # noqa: N802
        if self.refuses:
            raise OSError("the session refused")
        self.contexts.append(context)
        self.level = level

    def GetMute(self):                  # noqa: N802
        return self.muted

    def SetMute(self, muted, context):  # noqa: N802
        if self.refuses:
            raise OSError("the session refused")
        self.muted = bool(muted)


class FakeProcess:
    def __init__(self, name):
        self._name = name

    def name(self):
        return self._name


class FakeSession:
    """A session as pycaw hands one over, with the parts that decide identity."""

    def __init__(self, instance_id, *, process="chrome.exe", pid=100,
                 state=1, display="", shared_id="shared", scope=None,
                 volume=None):
        self.InstanceIdentifier = instance_id
        self.Identifier = shared_id
        self.Process = FakeProcess(process) if process else None
        self.ProcessId = pid
        self.State = state
        self.DisplayName = display
        self._scope = scope or A.SINGLE_PROCESS
        self._volume = volume if volume is not None else FakeVolume()
        self._ctl = self

    def QueryInterface(self, interface):    # noqa: N802 - the COM name
        """
        pycaw asks the session control for IAudioSessionControl2. Without
        this the fake fell into `describe_session`'s exception path and every
        session came back UNKNOWN scope - which is the shape the real thing
        has when the interface is genuinely unavailable, so the fake was
        quietly testing the wrong branch.
        """
        return self


@pytest.fixture
def run():
    return c.Run.create("sort out the sound", capability="system")


@pytest.fixture
def mixer(monkeypatch):
    """A set of sessions this test owns, with the COM layer stubbed out."""
    made: list = []

    def install(*sessions):
        made[:] = list(sessions)
        return made

    monkeypatch.setattr(A, "sessions", lambda: list(made))
    monkeypatch.setattr(A, "_volume_of", lambda s: s._volume)
    monkeypatch.setattr(A, "process_scope",
                        lambda control: (control._scope, control.ProcessId))
    return install


# ---------------------------------------------------------------------------
# Identity
# ---------------------------------------------------------------------------


def test_the_instance_identifier_is_the_identity(mixer, monkeypatch):
    """
    Two windows of one application share the plain session identifier, so it
    cannot name one of them. The instance identifier can.
    """
    mixer(FakeSession("instance-a", shared_id="chrome"),
          FakeSession("instance-b", shared_id="chrome"))
    described = [A.describe_session(s) for s in A.sessions()]
    assert {d["session_id"] for d in described} == {"instance-a", "instance-b"}
    assert {d["shared_id"] for d in described} == {"chrome"}
    assert A.session_by_id("instance-b").InstanceIdentifier == "instance-b"


def test_an_expired_session_is_never_actionable(mixer):
    mixer(FakeSession("gone", state=2))
    assert A.describe_session(A.sessions()[0])["actionable"] is False


def test_a_dead_pid_makes_a_single_process_session_unactionable(mixer,
                                                                monkeypatch):
    """
    Measured on this machine: Windows listed a session whose pid psutil said
    was gone, with State reported as ACTIVE. Setting its volume succeeds and
    changes nothing anybody can hear.
    """
    monkeypatch.setattr("psutil.pid_exists", lambda pid: False)
    mixer(FakeSession("ghost", scope=A.SINGLE_PROCESS, state=1))
    described = A.describe_session(A.sessions()[0])
    assert described["process_alive"] is False
    assert described["actionable"] is False


def test_a_dead_pid_does_NOT_condemn_a_multi_process_session(mixer, monkeypatch):
    """
    The correction that matters. A session spanning several processes reports
    the pid of whichever created it, and that one can exit while the session
    goes on making noise. Reading "pid is gone" as "session is stale" hides a
    live one - and the system-sounds session on this machine is exactly that
    shape: MULTI_PROCESS, pid 0.
    """
    monkeypatch.setattr("psutil.pid_exists", lambda pid: False)
    mixer(FakeSession("shared-session", scope=A.MULTI_PROCESS, state=1))
    described = A.describe_session(A.sessions()[0])
    assert described["process_scope"] == A.MULTI_PROCESS
    assert described["process_alive"] is False
    assert described["actionable"] is True, \
        "a multi-process session was condemned by its creator's pid"


def test_an_uncontrollable_session_is_not_actionable(mixer):
    """If the volume interface cannot be reached, nothing can be set."""
    class Broken(FakeSession):
        pass

    session = Broken("odd")
    session._volume = None
    mixer(session)
    described = A.describe_session(A.sessions()[0])
    assert described["controllable"] is False
    assert described["actionable"] is False


def test_unknown_scope_falls_back_to_the_state(mixer, monkeypatch):
    monkeypatch.setattr("psutil.pid_exists", lambda pid: False)
    mixer(FakeSession("mystery", scope=A.UNKNOWN_SCOPE, state=1))
    assert A.describe_session(A.sessions()[0])["actionable"] is True
    mixer(FakeSession("mystery", scope=A.UNKNOWN_SCOPE, state=2))
    assert A.describe_session(A.sessions()[0])["actionable"] is False


# ---------------------------------------------------------------------------
# Resolving what a person said
# ---------------------------------------------------------------------------


def test_a_name_finds_the_session(mixer):
    mixer(FakeSession("a", process="spotify.exe"),
          FakeSession("b", process="chrome.exe"))
    assert len(A.find_sessions("spotify")) == 1


def test_an_instance_id_finds_exactly_one(mixer):
    mixer(FakeSession("instance-a", process="chrome.exe"),
          FakeSession("instance-b", process="chrome.exe"))
    assert len(A.find_sessions("instance-b")) == 1


def test_live_sessions_are_preferred_over_ghosts(mixer, monkeypatch):
    """A ghost and a real one both called chrome: act on the real one."""
    monkeypatch.setattr("psutil.pid_exists", lambda pid: pid != 999)
    mixer(FakeSession("ghost", process="chrome.exe", pid=999, state=1),
          FakeSession("live", process="chrome.exe", pid=1, state=1))
    found = A.find_sessions("chrome")
    assert [s.InstanceIdentifier for s in found] == ["live"]


def test_when_all_of_them_are_ghosts_they_are_still_returned(mixer, monkeypatch):
    """
    So that "nothing happened" stays answerable. Returning nothing would say
    "no such application", which is a different and wrong answer.
    """
    monkeypatch.setattr("psutil.pid_exists", lambda pid: False)
    mixer(FakeSession("ghost", process="chrome.exe", state=1))
    assert len(A.find_sessions("chrome")) == 1


def test_two_matches_is_a_question_not_a_choice(run, mixer, monkeypatch):
    monkeypatch.setattr("psutil.pid_exists", lambda pid: True)
    mixer(FakeSession("a", process="chrome.exe", pid=1),
          FakeSession("b", process="chrome.exe", pid=2))
    result = A.audio_session_volume(run, "chrome", 30)
    assert result.status == c.FAILED
    assert "say which" in result.error


def test_nothing_matching_names_what_is_playing(run, mixer, monkeypatch):
    monkeypatch.setattr("psutil.pid_exists", lambda pid: True)
    mixer(FakeSession("a", process="spotify.exe"))
    result = A.audio_session_volume(run, "photoshop", 30)
    assert result.status == c.FAILED
    assert "spotify.exe" in result.error


# ---------------------------------------------------------------------------
# Changing it
# ---------------------------------------------------------------------------


def test_setting_a_volume_reports_what_it_was(run, mixer, monkeypatch):
    """A task that fails later needs the old value; a local variable is gone."""
    monkeypatch.setattr("psutil.pid_exists", lambda pid: True)
    mixer(FakeSession("a", process="spotify.exe",
                      volume=FakeVolume(level=0.8)))
    result = A.audio_session_volume(run, "spotify", 30)
    assert result.status == c.SUCCEEDED
    assert result.output["previous_percent"] == 80
    assert result.output["observed_percent"] == 30
    assert result.output["reversible"] is True


def test_a_volume_outside_the_range_is_refused(run, mixer, monkeypatch):
    monkeypatch.setattr("psutil.pid_exists", lambda pid: True)
    mixer(FakeSession("a", process="spotify.exe"))
    for bad in (-1, 101):
        assert A.audio_session_volume(run, "spotify", bad).status == c.FAILED


def test_a_session_that_refuses_is_a_failure_not_a_silent_success(run, mixer,
                                                                  monkeypatch):
    monkeypatch.setattr("psutil.pid_exists", lambda pid: True)
    mixer(FakeSession("a", process="spotify.exe",
                      volume=FakeVolume(refuses=True)))
    result = A.audio_session_volume(run, "spotify", 30)
    assert result.status == c.FAILED
    assert not result.may_claim_completion


def test_mute_reports_what_it_was(run, mixer, monkeypatch):
    monkeypatch.setattr("psutil.pid_exists", lambda pid: True)
    mixer(FakeSession("a", process="spotify.exe",
                      volume=FakeVolume(muted=False)))
    result = A.audio_session_mute(run, "spotify", True)
    assert result.output["previous_muted"] is False
    assert result.output["observed_muted"] is True


def test_percentages_and_levels_convert_both_ways():
    for percent in (0, 1, 30, 50, 99, 100):
        assert A.to_percent(A.to_level(percent)) == percent
    assert A.to_level(-5) == 0.0 and A.to_level(500) == 1.0


def test_close_enough_allows_a_driver_to_round_but_not_to_ignore():
    assert A.close_enough(30, 31)
    assert A.close_enough(30, 28)
    assert not A.close_enough(30, 40)


def test_every_audio_tool_is_declared_to_the_policy_engine():
    from friday.policy import DEVICE_SETTING, READ_LOCAL_SAFE, TOOL_CATEGORIES

    assert TOOL_CATEGORIES["audio.sessions"] == READ_LOCAL_SAFE
    for name in ("session_volume", "session_mute", "master_volume"):
        assert TOOL_CATEGORIES[f"audio.{name}"] == DEVICE_SETTING
