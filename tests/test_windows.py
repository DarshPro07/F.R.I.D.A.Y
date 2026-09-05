"""
Windows as things with observable state.

The donor sends Win+Left and hopes. Everything here reads the window back
afterwards, which is the only reason this batch can exist at all: a keystroke
produces no observation, and the proof-of-work contract needs one.

These use a fake window rather than the real desktop, deliberately. The suite
runs constantly, and a test run that minimises the boss's Chrome window every
time is a test suite that gets disabled. The real desktop is exercised once,
by scripts/golden_windows.py, against a window Friday opened itself.
"""

from __future__ import annotations

import sys
import types

import pytest

from friday import contracts as c
from friday.toolsets import windows as W


@pytest.fixture(autouse=True)
def _a_window_manager_to_fake(monkeypatch):
    """The tool logic under test is platform-neutral (find, verify, read
    back); only the enumeration is Windows. Off Windows the module has
    `pygetwindow = None` and every tool answers UNSUPPORTED, so give it a
    stand-in module to patch `getAllWindows` on and open the gate. The
    UNSUPPORTED path itself is asserted separately below."""
    if W.pygetwindow is None:
        monkeypatch.setattr(W, "pygetwindow", types.SimpleNamespace(getAllWindows=lambda: []))
        monkeypatch.setattr(W, "AVAILABLE", True)


class FakeWindow:
    """
    A window that behaves like one: operations change state, and reading it
    back reflects the change - unless it has been told to refuse.
    """

    def __init__(self, title, left=100, top=100, width=800, height=600,
                 refuses=()):
        self.title = title
        self.left, self.top = left, top
        self.width, self.height = width, height
        self.isMinimized = False
        self.isMaximized = False
        self.isActive = False
        self.visible = True
        self._refuses = set(refuses)

    def activate(self):
        if "activate" not in self._refuses:
            self.isActive = True

    def minimize(self):
        if "minimize" not in self._refuses:
            self.isMinimized, self.isActive = True, False

    def restore(self):
        if "restore" not in self._refuses:
            self.isMinimized = self.isMaximized = False

    def maximize(self):
        if "maximize" not in self._refuses:
            self.isMaximized = True

    def moveTo(self, left, top):          # noqa: N802 - pygetwindow's name
        if "move" not in self._refuses:
            self.left, self.top = left, top

    def resizeTo(self, width, height):    # noqa: N802
        if "resize" not in self._refuses:
            self.width, self.height = width, height


@pytest.fixture
def run():
    return c.Run.create("sort out my windows", capability="system")


@pytest.fixture
def desktop(monkeypatch):
    """A desktop this test owns, with a screen of a known size."""
    windows = [FakeWindow("Notes - Notepad"),
               FakeWindow("Maths Exam Preparation - Google Chrome"),
               FakeWindow("◐ MarketPulse architecture - Claude")]
    monkeypatch.setattr(W.pygetwindow, "getAllWindows", lambda: windows)

    def displays(run_, *, engine=None):
        return run_.record(c.succeeded(
            c.started(run_.run_id, "system.displays"),
            output={"displays": [{"width": 1920, "height": 1080, "left": 0,
                                  "top": 0, "primary": True}], "count": 1},
            verification=c.Verification(method="fake", evidence="1920x1080")))

    monkeypatch.setattr("friday.toolsets.hardware.system_displays", displays)
    return windows


# ---------------------------------------------------------------------------
# Read
# ---------------------------------------------------------------------------


def test_listing_reports_geometry_and_state(run, desktop):
    result = W.windows_list(run)
    assert result.status == c.SUCCEEDED
    assert result.output["count"] == 3
    first = result.output["windows"][0]
    assert set(first) == {"title", "left", "top", "width", "height",
                          "minimized", "maximized", "active", "visible"}


def test_a_unicode_title_survives_being_returned(run, desktop):
    """
    One window on this machine is titled with U+25D0, and printing it to a
    cp1252 console raises UnicodeEncodeError. Titles are data; the encoding
    belongs to whatever displays them, not to the tool that reads them.
    """
    titles = [w["title"] for w in W.windows_list(run).output["windows"]]
    assert any("◐" in title for title in titles)


def test_listing_can_be_narrowed(run, desktop):
    result = W.windows_list(run, "chrome")
    assert result.output["count"] == 1


def test_furniture_windows_are_not_listed(run, monkeypatch):
    monkeypatch.setattr(W.pygetwindow, "getAllWindows",
                        lambda: [FakeWindow("Program Manager"),
                                 FakeWindow("   "),
                                 FakeWindow("Notes - Notepad")])
    assert W.windows_list(run).output["count"] == 1


# ---------------------------------------------------------------------------
# Naming one window
# ---------------------------------------------------------------------------


def test_several_matches_is_a_question_not_a_choice(run, monkeypatch):
    """
    "Bring Chrome forward" with four Chrome windows open is a question. Acting
    on the first is a coin toss the boss did not ask anyone to flip.
    """
    monkeypatch.setattr(W.pygetwindow, "getAllWindows",
                        lambda: [FakeWindow("A - Google Chrome"),
                                 FakeWindow("B - Google Chrome")])
    result = W.windows_focus(run, "chrome")
    assert result.status == c.FAILED
    assert "say which" in result.error
    assert "A - Google Chrome" in result.error


def test_no_match_lists_what_is_open(run, desktop):
    result = W.windows_focus(run, "photoshop")
    assert result.status == c.FAILED
    assert "no open window matches" in result.error
    assert "Notepad" in result.error


def test_a_pattern_is_a_substring_not_a_regex(run, monkeypatch):
    """
    The caller is a model relaying what a person said, and `.*` in a window
    title is a literal far more often than it is an intention.
    """
    monkeypatch.setattr(W.pygetwindow, "getAllWindows",
                        lambda: [FakeWindow("report (final).docx - Word")])
    assert len(W.find(".*")) == 0
    assert len(W.find("(final)")) == 1


# ---------------------------------------------------------------------------
# Every operation is verified by reading the window back
# ---------------------------------------------------------------------------


def test_focus_reports_the_window_it_actually_activated(run, desktop):
    result = W.windows_focus(run, "notepad")
    assert result.status == c.SUCCEEDED
    assert result.output["window"]["active"] is True
    assert "Notepad" in result.verification.evidence


def test_windows_refusing_focus_is_reported_as_refused(run, monkeypatch):
    """
    Windows restricts SetForegroundWindow: a background process may not take
    the keyboard, and the OS can refuse even when the documented conditions
    hold. pygetwindow raises when the call returns 0 - that is the refusal
    arriving as an exception, and it is neither a tool failure nor a success.

    Seen live on this machine: "Error code from Windows: 5 - Access is
    denied." The window was found and was not focused, and both halves have to
    be said.
    """
    class Refuses(FakeWindow):
        def activate(self):
            raise Exception("Error code from Windows: 5 - Access is denied.")

    monkeypatch.setattr(W.pygetwindow, "getAllWindows",
                        lambda: [Refuses("Stubborn - Notepad")])
    result = W.windows_focus(run, "stubborn")

    assert result.status == c.PARTIAL, "an OS policy refusal is not a failure"
    assert not result.may_claim_completion, "it claimed the window was focused"
    assert result.output["focus_granted"] is False
    assert "Access is denied" in result.output["os_refusal"]
    assert "would not give it focus" in result.error


def test_a_refusal_that_still_left_it_foreground_is_a_success(run, monkeypatch):
    """
    The read-back decides, not the exception. Some refusals still end with the
    window in front, and GetForegroundWindow - which is what isActive reads -
    is the authority on which happened.
    """
    class RefusesButWorks(FakeWindow):
        def activate(self):
            self.isActive = True
            raise Exception("Error code from Windows: 5 - Access is denied.")

    monkeypatch.setattr(W.pygetwindow, "getAllWindows",
                        lambda: [RefusesButWorks("Odd - Notepad")])
    result = W.windows_focus(run, "odd")
    assert result.status == c.SUCCEEDED
    assert result.output["focus_granted"] is True


def test_focus_never_works_around_the_os_policy():
    """
    No synthetic ALT presses, no AttachThreadInput, no repeated stealing.
    Those defeat a protection that exists so a background process cannot take
    the keyboard out from under someone mid-sentence, and "the test went
    green" is not a reason to disable it.
    """
    import ast
    import inspect

    tree = ast.parse(inspect.getsource(W))
    called = {node.func.attr for node in ast.walk(tree)
              if isinstance(node, ast.Call)
              and isinstance(node.func, ast.Attribute)}
    assert not called & {"AttachThreadInput", "keybd_event", "press",
                         "hotkey", "SendInput"}, sorted(called)


def test_focusing_a_minimized_window_restores_it_first(run, desktop):
    desktop[0].minimize()
    result = W.windows_focus(run, "notepad")
    assert result.status == c.SUCCEEDED
    assert result.output["window"]["minimized"] is False


def test_minimize_and_restore_are_reversible(run, desktop):
    assert W.windows_minimize(run, "notepad").output["window"]["minimized"]
    restored = W.windows_restore(run, "notepad")
    assert restored.output["window"]["minimized"] is False


def test_maximize_is_verified(run, desktop):
    assert W.windows_maximize(run, "notepad").output["window"]["maximized"]


def test_a_window_manager_refusal_is_partial_not_success(run, monkeypatch):
    """
    A modal dialog will not minimise. The call not raising is not evidence
    that anything happened, which is the whole reason for the read-back.
    """
    monkeypatch.setattr(W.pygetwindow, "getAllWindows",
                        lambda: [FakeWindow("Stubborn", refuses=("minimize",))])
    result = W.windows_minimize(run, "stubborn")
    assert result.status == c.PARTIAL
    assert not result.may_claim_completion
    assert "may have refused" in result.error


# ---------------------------------------------------------------------------
# Arranging, which is where the donor's keystroke cannot say what happened
# ---------------------------------------------------------------------------


def test_arranging_computes_the_rectangle_from_the_real_screen(run, desktop):
    result = W.windows_arrange(run, "notepad", "right")
    assert result.status == c.SUCCEEDED
    window = result.output["window"]
    assert (window["left"], window["top"]) == (960, 0)
    assert (window["width"], window["height"]) == (960, 1080)


@pytest.mark.parametrize("side,expected", [
    ("left", (0, 0, 960, 1080)),
    ("right", (960, 0, 960, 1080)),
    ("top", (0, 0, 1920, 540)),
    ("bottom", (0, 540, 1920, 540)),
    ("full", (0, 0, 1920, 1080)),
])
def test_every_side_lands_where_it_says(run, desktop, side, expected):
    window = W.windows_arrange(run, "notepad", side).output["window"]
    assert (window["left"], window["top"],
            window["width"], window["height"]) == expected


def test_an_unknown_side_is_refused_with_the_list(run, desktop):
    result = W.windows_arrange(run, "notepad", "diagonally")
    assert result.status == c.FAILED
    assert "unknown side" in result.error


def test_a_maximized_window_is_restored_before_being_moved(run, desktop):
    """A maximised window ignores moveTo, and "it did not move" is confusing."""
    desktop[0].maximize()
    result = W.windows_arrange(run, "notepad", "left")
    assert result.status == c.SUCCEEDED
    assert result.output["window"]["maximized"] is False


def test_a_window_manager_that_places_it_elsewhere_is_partial(run, monkeypatch,
                                                              desktop):
    monkeypatch.setattr(W.pygetwindow, "getAllWindows",
                        lambda: [FakeWindow("Fixed", refuses=("move", "resize"))])
    result = W.windows_arrange(run, "fixed", "left")
    assert result.status == c.PARTIAL
    assert "placed it differently" in result.error


def test_arranging_without_a_known_screen_fails_rather_than_guessing(
        run, desktop, monkeypatch):
    def no_displays(run_, *, engine=None):
        return run_.record(c.failed(
            c.started(run_.run_id, "system.displays"), "no displays reported"))

    monkeypatch.setattr("friday.toolsets.hardware.system_displays", no_displays)
    result = W.windows_arrange(run, "notepad", "left")
    assert result.status == c.FAILED
    assert "without knowing the screen size" in result.error


# ---------------------------------------------------------------------------
# The boundary
# ---------------------------------------------------------------------------


def test_there_is_no_way_to_close_a_window_here(run, desktop):
    """
    Closing is not reversible - unsaved work disappears and no read-back
    brings it back - so it is not in the reversible batch. Its absence is a
    decision, which makes it a test.
    """
    assert not hasattr(W, "windows_close")
    assert "close" not in dir(W)


def test_every_operation_is_declared_to_the_policy_engine():
    from friday.policy import TOOL_CATEGORIES

    for name in ("list", "focus", "minimize", "restore", "maximize", "arrange"):
        assert f"windows.{name}" in TOOL_CATEGORIES, name


def test_nothing_here_presses_a_key():
    import ast
    import inspect

    tree = ast.parse(inspect.getsource(W))
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])
    assert "pyautogui" not in imported


def test_off_windows_every_tool_is_unsupported_not_broken(run, monkeypatch):
    """pygetwindow raises NotImplementedError at import on Linux, which
    took the whole tool registry down on the Ubuntu CI job. Now the module
    imports everywhere and each tool says UNSUPPORTED - the honest class for
    a machine with no window manager Friday can drive - with the platform
    named, before touching anything."""
    monkeypatch.setattr(W, "AVAILABLE", False)
    monkeypatch.setattr(W, "pygetwindow", None)
    for call in (lambda: W.windows_list(run), lambda: W.windows_focus(run, "Notepad"),
                 lambda: W.windows_minimize(run, "Notepad"), lambda: W.windows_restore(run, "Notepad"),
                 lambda: W.windows_maximize(run, "Notepad"), lambda: W.windows_arrange(run, "Notepad", "left")):
        result = call()
        assert result.status == c.UNSUPPORTED, result
        assert "not available on" in result.error and sys.platform in result.error
    assert W._all_windows() == []
