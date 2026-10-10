"""CUA-003: a computer-use session acts only on its listed windows and
applications; an action on any other is refused and recorded, at the router
(`computer_use.route`) that every computer-use action goes through."""
from __future__ import annotations

import time

import pytest

from friday import computer_use, fabric
from friday import computer_use_sessions as S
from friday import governor as G
from friday.fabric_adapters import native_desktop

TOTAL_GB = 32.0
IDLE = G.Baseline(ram_available_gb=TOTAL_GB * 0.7, cpu_percent=10.0,
                  readings=12, provisional=False)


def _gov() -> G.Governor:
    s = G.Sample(at=time.time(), cpu_percent=10.0, ram_percent=30.0,
                 ram_available_gb=TOTAL_GB * 0.7, disk_free_gb=100.0,
                 browser_processes=0, friday_rss_mb=200.0, ram_total_gb=TOTAL_GB)
    return G.Governor(thresholds=G.Thresholds(), sampler=lambda: s,
                      sample_ttl_s=0.0, baseline=IDLE)


@pytest.fixture
def reg():
    return S.SessionRegistry(governor=_gov(), clock=lambda: 1_000_000.0)


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


def _open(reg, windows=("notepad:1",), apps=("notepad",)):
    return reg.open(objective_id="obj-1", worker="w-1", windows=windows, apps=apps)


# -- the session manifest -----------------------------------------------------


def test_the_manifest_lists_the_allowed_windows_and_applications(reg):
    s = _open(reg, windows=("Notepad:1", "calc:2"), apps=("Notepad", "Calc"))
    d = reg.describe(s.session_id)
    assert d["windows"] == ["calc:2", "notepad:1"]
    assert d["apps"] == ["calc", "notepad"]


# -- the registry refuses an application outside the set -----------------------


def test_an_application_outside_the_set_is_refused_and_recorded(reg):
    s = _open(reg)
    with pytest.raises(S.SessionRefused) as e:
        reg.authorize(s.session_id, "notepad:1", app="chrome")
    assert e.value.record["reason_code"] == S.APP_NOT_IN_SET
    assert reg.refusals()[-1]["resource"] == "chrome"
    assert reg.describe(s.session_id)["actions"] == 0


def test_an_action_naming_no_application_is_refused_when_the_session_lists_some(reg):
    s = _open(reg)
    with pytest.raises(S.SessionRefused) as e:
        reg.authorize(s.session_id, "notepad:1")
    assert e.value.record["reason_code"] == S.APP_NOT_IN_SET


def test_a_listed_window_and_application_is_allowed_and_counted(reg):
    s = _open(reg)
    reg.authorize(s.session_id, " Notepad:1", app="NOTEPAD")
    assert reg.describe(s.session_id)["actions"] == 1
    assert reg.refusals() == []


# -- through the router --------------------------------------------------------


def test_a_routed_action_on_a_listed_window_and_application_is_served(reg, screen):
    s = _open(reg)
    route = computer_use.route("desktop_step", session=s.session_id,
                               window="notepad:1", app="notepad", sessions=reg)
    assert route.usable and route.backend == "native_desktop"
    assert reg.describe(s.session_id)["actions"] == 1


def test_a_routed_action_on_another_window_is_refused_recorded_and_not_served(reg, screen):
    s = _open(reg)
    with pytest.raises(S.SessionRefused) as e:
        computer_use.route("desktop_step", session=s.session_id,
                           window="calc:2", app="notepad", sessions=reg)
    assert e.value.record["reason_code"] == S.NOT_IN_SET
    assert reg.refusals()[-1]["resource"] == "calc:2"
    # refused before any backend was started
    assert fabric.health("native_desktop")["state"] != fabric.READY


def test_a_routed_action_on_another_application_is_refused_and_recorded(reg, screen):
    s = _open(reg)
    with pytest.raises(S.SessionRefused) as e:
        computer_use.route("desktop_step", session=s.session_id,
                           window="notepad:1", app="chrome", sessions=reg)
    assert e.value.record["reason_code"] == S.APP_NOT_IN_SET
    assert len(reg.refusals()) == 1


def test_a_routed_action_with_no_window_named_is_not_served(reg, screen):
    s = _open(reg)
    with pytest.raises(ValueError):
        computer_use.route("desktop_step", session=s.session_id, window="", sessions=reg)
    assert reg.describe(s.session_id)["actions"] == 0


def test_a_routed_action_of_a_closed_session_is_not_served(reg, screen):
    s = _open(reg)
    reg.close(s.session_id)
    with pytest.raises(S.SessionStopped):
        computer_use.route("desktop_step", session=s.session_id,
                           window="notepad:1", app="notepad", sessions=reg)


def test_a_capability_outside_computer_use_is_not_a_session_action(reg):
    s = _open(reg)
    assert computer_use.route("connector_gmail_send", session=s.session_id,
                              window="x", sessions=reg) is None
    assert reg.describe(s.session_id)["actions"] == 0


def test_the_router_uses_the_process_registry_when_none_is_given(reg, screen):
    S.configure(reg)
    try:
        s = _open(reg)
        with pytest.raises(S.SessionRefused):
            computer_use.route("desktop_step", session=s.session_id,
                               window="calc:2", app="notepad")
    finally:
        S.configure(None)


def test_a_route_without_a_session_is_unchanged(screen):
    assert computer_use.route("desktop_step").usable
