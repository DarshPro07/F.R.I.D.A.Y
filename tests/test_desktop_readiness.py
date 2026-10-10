"""
CUA-005: a desktop backend is ready only after a real action whose post-state
was observed independently; a failed step is named, and the router falls back.

The native layer is injected, so none of this needs a desktop or touches the
screen: `FakeLayer` records what the probe asked of it.
"""
from __future__ import annotations

import pytest

from friday import action_path, computer_use, desktop_readiness as dr, fabric
from friday.fabric_adapters import native_desktop

PERMISSION = "desktop.readiness_probe"


class FakeLayer:
    """A scripted native layer; every call is recorded in `calls`."""

    def __init__(self, **overrides):
        self.available = True
        self.size = (1920, 1080)
        self.path = r"C:\Windows\System32\notepad.exe"
        self.signature_result = (True, "Valid")
        self.launch_error = None
        self.pid = 4242
        #: hwnds visible after launch; the window list shrinks on close unless
        #: `close_works` is False.
        self.shown = [101]
        self.close_works = True
        self.enumeration_error = None
        self.calls = []
        for name, value in overrides.items():
            setattr(self, name, value)
        self._open = []

    def native_available(self):
        return self.available

    def screen_size(self):
        return self.size

    def find(self, image):
        self.calls.append(("find", image))
        return self.path

    def signature(self, path):
        self.calls.append(("signature", path))
        return self.signature_result

    def launch(self, path):
        self.calls.append(("launch", path))
        if self.launch_error is not None:
            raise self.launch_error
        self._open = list(self.shown)
        return self.pid

    def windows(self, pid):
        self.calls.append(("windows", pid))
        if self.enumeration_error is not None:
            raise self.enumeration_error
        return list(self._open)

    def close(self, hwnd):
        self.calls.append(("close", hwnd))
        if self.close_works and hwnd in self._open:
            self._open.remove(hwnd)

    def terminate(self, pid):
        self.calls.append(("terminate", pid))

    def names(self):
        return [name for name, *_ in self.calls]


def _probe(layer, timeout_ms=1_000):
    now = [0.0]

    def sleep(seconds):
        now[0] += seconds

    return dr.probe(layer, timeout_ms=timeout_ms, clock=lambda: now[0], sleep=sleep)


# --- the readiness probe -----------------------------------------------------


def test_a_real_action_with_an_observed_post_state_is_ready():
    layer = FakeLayer()
    result = _probe(layer)
    assert result.state == dr.DESKTOP_READY
    assert result.steps == ("installed", "signature", "launch", "windows", "action")
    assert ("close", 101) in layer.calls
    # the post-state is a second enumeration, after the close
    assert layer.names().index("close") < len(layer.names()) - 1
    assert layer.names()[-1] == "terminate"


def test_a_host_without_the_native_layer_is_not_installed_and_nothing_runs():
    layer = FakeLayer(available=False)
    result = _probe(layer)
    assert result.state == dr.NOT_INSTALLED
    assert layer.calls == []


@pytest.mark.parametrize("size", [(0, 0), (1920, 0)])
def test_a_machine_with_no_display_is_named(size):
    layer = FakeLayer(size=size)
    assert _probe(layer).state == dr.NO_DISPLAY
    assert layer.calls == []


def test_a_missing_binary_is_named_and_not_launched():
    layer = FakeLayer(path=None)
    result = _probe(layer)
    assert result.state == dr.BINARY_MISSING
    assert "launch" not in layer.names()


def test_a_binary_whose_signature_is_not_valid_is_never_launched():
    layer = FakeLayer(signature_result=(False, "HashMismatch"))
    result = _probe(layer)
    assert result.state == dr.SIGNATURE_INVALID
    assert "HashMismatch" in result.detail
    assert "launch" not in layer.names()


@pytest.mark.parametrize("winerror", [225, 577, 1260, 4551])
def test_a_binary_blocked_by_code_integrity_is_named(winerror):
    error = OSError(0, "blocked")
    error.winerror = winerror
    layer = FakeLayer(launch_error=error)
    result = _probe(layer)
    assert result.state == dr.BINARY_BLOCKED
    assert str(winerror) in result.detail
    assert "terminate" not in layer.names()


def test_another_launch_failure_is_a_launch_failure_not_a_block():
    layer = FakeLayer(launch_error=FileNotFoundError("gone"))
    result = _probe(layer)
    assert result.state == dr.LAUNCH_FAILED
    assert "FileNotFoundError" in result.detail


def test_a_process_that_never_shows_a_window_is_named_and_cleaned_up():
    layer = FakeLayer(shown=[])
    result = _probe(layer)
    assert result.state == dr.NO_WINDOW
    assert "close" not in layer.names()
    assert layer.names()[-1] == "terminate"


def test_a_window_that_is_still_there_after_the_close_is_not_ready():
    layer = FakeLayer(close_works=False)
    result = _probe(layer)
    assert result.state == dr.ACTION_UNOBSERVED
    assert layer.names()[-1] == "terminate"


def test_a_failing_enumeration_is_named_with_its_cause():
    layer = FakeLayer(enumeration_error=OSError("EnumWindows failed"))
    result = _probe(layer)
    assert result.state == dr.ENUMERATION_FAILED
    assert "EnumWindows failed" in result.detail
    assert layer.names()[-1] == "terminate"


# --- the backend reports it ---------------------------------------------------


@pytest.fixture(autouse=True)
def clean_fabric():
    fabric.reload()
    yield
    fabric.deactivate_all()


@pytest.fixture
def screen(monkeypatch):
    native = native_desktop._native()
    monkeypatch.setattr(native, "AVAILABLE", True)
    monkeypatch.setattr(native, "screen_size", lambda: (1920, 1080))
    return native


def _use(monkeypatch, layer):
    monkeypatch.setattr(native_desktop, "_layer", lambda: layer)
    return layer


def test_the_desktop_is_degraded_until_a_real_action_has_been_observed(screen):
    route = computer_use.route("desktop_step")
    assert (route.backend, route.state) == ("native_desktop", fabric.DEGRADED)
    assert "not probed yet" in route.detail


def test_an_observed_action_makes_the_desktop_ready(screen, monkeypatch):
    layer = _use(monkeypatch, FakeLayer())
    computer_use.route("desktop_step")
    result = fabric.call("native_desktop", native_desktop.READINESS,
                         authorized=frozenset({PERMISSION}))
    assert result.error is None
    assert result.output["state"] == dr.DESKTOP_READY
    route = computer_use.route("desktop_step")
    assert (route.backend, route.state) == ("native_desktop", fabric.READY)
    assert ("close", 101) in layer.calls


def test_a_blocked_binary_makes_the_route_unavailable_with_the_named_state(
        screen, monkeypatch):
    error = OSError(0, "blocked")
    error.winerror = 4551
    _use(monkeypatch, FakeLayer(launch_error=error))
    computer_use.route("desktop_step")
    fabric.call("native_desktop", native_desktop.READINESS,
                authorized=frozenset({PERMISSION}))
    route = computer_use.route("desktop_step")
    assert route.state == fabric.UNAVAILABLE and not route.usable
    assert dr.BINARY_BLOCKED in route.detail
    # an unavailable desktop path is ordered after the ones that answer
    assert action_path.COORDINATES in computer_use.unavailable_paths(["desktop_step"])


def test_a_later_passing_probe_recovers_the_route(screen, monkeypatch):
    layer = FakeLayer(shown=[])
    _use(monkeypatch, layer)
    computer_use.route("desktop_step")
    grant = frozenset({PERMISSION})
    fabric.call("native_desktop", native_desktop.READINESS, authorized=grant)
    assert computer_use.route("desktop_step").state == fabric.UNAVAILABLE
    layer.shown = [7]
    fabric.call("native_desktop", native_desktop.READINESS, authorized=grant)
    assert computer_use.route("desktop_step").state == fabric.READY


def test_the_readiness_action_needs_its_own_grant_but_the_screen_probe_does_not(
        screen, monkeypatch):
    layer = _use(monkeypatch, FakeLayer())
    refused = fabric.call("native_desktop", native_desktop.READINESS)
    assert refused.error is not None and "not granted" in str(refused.error)
    assert layer.calls == []
    assert fabric.call("native_desktop", native_desktop.PROBE).error is None


@pytest.mark.parametrize("arguments", [{"target": "calc.exe"}, {"timeout_ms": 999},
                                       {"timeout_ms": 60_001}])
def test_the_readiness_action_takes_no_caller_chosen_target_and_a_bounded_timeout(
        screen, monkeypatch, arguments):
    layer = _use(monkeypatch, FakeLayer())
    handle = native_desktop.start()
    with pytest.raises(fabric.FabricError):
        native_desktop.call(native_desktop.READINESS, handle, **arguments)
    assert layer.calls == []


# --- the real NativeLayer must never touch what the probe did not start -----

class _Error(Exception):
    pass


class _NoSuchProcess(_Error):
    pass


class _FakePsutil:
    """Just enough psutil: a process table with parents and an image name."""

    NoSuchProcess = _NoSuchProcess
    Error = _Error

    def __init__(self, table):
        #: pid -> (image name, parent pid or None)
        self.table = dict(table)
        self.killed = []

    def Process(self, pid):  # noqa: N802 - psutil's own spelling
        if pid not in self.table:
            raise _NoSuchProcess(pid)
        outer = self

        class _Process:
            def name(self):
                return outer.table[pid][0]

            def children(self, recursive=False):
                found = []
                frontier = [pid]
                while frontier:
                    parent = frontier.pop()
                    for child, (_, up) in outer.table.items():
                        if up == parent:
                            found.append(outer.Process(child))
                            found[-1].pid = child
                            frontier.append(child)
                return found

            def kill(self):
                outer.killed.append(pid)
                outer.table.pop(pid, None)

        process = _Process()
        process.pid = pid
        return process


class _FakeWindowsApi:
    AVAILABLE = True
    WM_CLOSE = 0x10

    def __init__(self, windows):
        self.windows = list(windows)          # (hwnd, pid)
        self.posted = []

    def screen_size(self):
        return (1920, 1080)

    def top_level_windows(self):
        return list(self.windows)

    def PostMessageW(self, hwnd, message, wparam, lparam):  # noqa: N802
        self.posted.append(hwnd)
        self.windows = [w for w in self.windows if w[0] != hwnd]


class _Popen:
    def __init__(self, argv, **_):
        self.pid = 100


def test_the_native_layer_never_closes_or_kills_a_notepad_it_did_not_start():
    # The user already has Notepad open (hwnd 10, pid 500). The probe launches
    # pid 100 (window 30, child 101). Another Notepad (pid 501, window 20)
    # appears during the probe: it is not tied to the launch.
    api = _FakeWindowsApi([(10, 500)])
    ps = _FakePsutil({500: ("notepad.exe", None), 100: ("notepad.exe", None),
                      101: ("notepad.exe", 100), 501: ("notepad.exe", None)})

    class Layer(dr.NativeLayer):
        def find(self, image):
            return r"C:\Windows\System32\notepad.exe"

        def signature(self, path):
            return True, "Valid"

    layer = Layer(windows_api=api, psutil_api=ps, popen=_Popen)
    original_launch = layer.launch

    def launch(path):
        pid = original_launch(path)
        api.windows += [(30, 100), (20, 501)]         # appear after the launch
        return pid

    layer.launch = launch
    result = dr.probe(layer, timeout_ms=500, sleep=lambda _s: None)

    assert result.state == dr.DESKTOP_READY, result
    assert api.posted == [30], "only the window of the launched process is closed"
    assert 10 in [h for h, _ in api.windows], "the user's window is untouched"
    assert 500 not in ps.killed and 501 not in ps.killed
    assert sorted(ps.killed) == [100, 101], "only the launch and its children die"


def test_a_preexisting_window_is_never_offered_as_the_probes_own():
    api = _FakeWindowsApi([(10, 500)])
    ps = _FakePsutil({500: ("notepad.exe", None), 100: ("notepad.exe", None)})
    layer = dr.NativeLayer(windows_api=api, psutil_api=ps, popen=_Popen)
    layer.launch(r"C:\Windows\System32\notepad.exe")
    assert layer.windows(100) == []
    api.windows.append((40, 600))                       # not Notepad at all
    ps.table[600] = ("explorer.exe", None)
    assert layer.windows(100) == []


def test_a_non_numeric_timeout_is_a_refusal_not_a_crash(monkeypatch):
    layer = _use(monkeypatch, FakeLayer())
    handle = native_desktop.start()
    with pytest.raises(fabric.FabricError):
        native_desktop.call(native_desktop.READINESS, handle, timeout_ms="soon")
    assert layer.calls == []
