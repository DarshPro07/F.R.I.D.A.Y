"""
Phase 1A: policy engine, app resolution, system toolset.

The load-bearing test is test_open_never_claims_success_without_process_evidence
- it is the unit-level statement of the whole Phase 1 premise.
"""

from __future__ import annotations

import sys

import pytest

from friday import apps
from friday import contracts as c
from friday import policy as p
from friday.toolsets import system as S

WINDOWS_ONLY = pytest.mark.skipif(sys.platform != "win32", reason="Windows-only capability")


@pytest.fixture
def run():
    return c.Run.create("test request", capability="system")


@pytest.fixture
def engine():
    """
    Guarded explicitly. These tests are about the gating machinery, which
    still exists and is still supported; the *default* is now full autonomy
    (see tests/test_autonomy.py for why).
    """
    return p.PolicyEngine(autonomy=p.GUARDED)


# ---------------------------------------------------------------------------
# Policy engine (§20)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("tool_id, expected", [
    ("system.get_info", p.AUTO),
    ("system.list_processes", p.AUTO),
    ("apps.open", p.AUTO),
    ("volume.set", p.AUTO),
    ("search_web", p.AUTO),
    ("memory.recall", p.AUTO),
    ("apps.close", p.ASK),
    ("clipboard.write", p.ASK),
    ("memory.forget", p.ASK),
])
def test_default_decisions(engine, tool_id, expected):
    assert engine.decide(tool_id).decision == expected


def test_unknown_tool_defaults_to_ask_not_auto(engine):
    """Unaudited must never mean allowed."""
    verdict = engine.decide("some.tool.nobody.reviewed")
    assert verdict.decision == p.ASK
    assert "unaudited" in verdict.reason


def test_secret_read_is_denied_and_cannot_be_approved(engine):
    verdict = engine.decide("secrets.read")
    assert verdict.decision == p.DENY
    assert verdict.denied and not verdict.allowed
    with pytest.raises(p.PolicyError, match="cannot be approved"):
        engine.approve_for_session("secrets.read")


def test_deny_tier_is_reachable_not_dead_policy():
    """
    Every declared decision must be reachable by at least one real tool,
    or the policy is decoration. This test caught SECRET_READ having no
    tools mapped to it at all.
    """
    engine = p.PolicyEngine(autonomy=p.GUARDED)
    reachable = {engine.decide(tool).decision for tool in p.TOOL_CATEGORIES}
    assert p.DENY in reachable, "no tool maps to a DENY category"
    assert p.ASK in reachable
    assert p.AUTO in reachable


def test_unknown_tool_cannot_be_approved(engine):
    with pytest.raises(p.PolicyError, match="no declared policy category"):
        engine.approve_for_session("some.tool.nobody.reviewed")


def test_session_approval_upgrades_ask_to_auto(engine):
    assert engine.decide("apps.close").decision == p.ASK
    engine.approve_for_session("apps.close")
    assert engine.decide("apps.close").decision == p.AUTO
    engine.revoke("apps.close")
    assert engine.decide("apps.close").decision == p.ASK


def test_require_raises_on_ask(engine):
    with pytest.raises(p.PolicyError, match="requires explicit approval"):
        engine.require("apps.close")
    engine.approve_for_session("apps.close")
    assert engine.require("apps.close").allowed


def test_decide_accepts_only_a_tool_id():
    """
    §20: the learned user model must never grant authorization. Enforced by
    there being no parameter through which it could arrive.
    """
    import inspect

    params = list(inspect.signature(p.PolicyEngine.decide).parameters)
    assert params == ["self", "tool_id"], (
        f"decide() grew a parameter: {params}. Preferences, personas and "
        "memories must have no path into an authorization decision."
    )


def test_policy_overrides_validated():
    with pytest.raises(ValueError, match="unknown policy category"):
        p.PolicyEngine(overrides={"VIBES": p.AUTO})
    with pytest.raises(ValueError, match="unknown decision"):
        p.PolicyEngine(overrides={p.FILE_WRITE: "MAYBE"})


# ---------------------------------------------------------------------------
# App resolution
# ---------------------------------------------------------------------------


def test_unknown_app_resolves_to_none():
    assert apps.resolve("flurbomatic 9000") is None
    assert apps.resolve("") is None
    assert apps.resolve("   ") is None


@WINDOWS_ONLY
def test_discovery_finds_apps_without_hardcoded_paths():
    """Locations come from the OS, not from a table in our source."""
    registry = apps.app_paths_registry()
    assert registry, "App Paths registry returned nothing"
    for path in registry.values():
        assert path, "registry entry with no path"


@WINDOWS_ONLY
def test_calculator_resolves_with_uwp_process_aliases():
    target = apps.resolve("calculator")
    assert target is not None
    # calc.exe is a stub that hands off to the UWP process; verification must
    # look for the real one or every launch would report unconfirmed.
    assert "CalculatorApp.exe" in target.expected_processes


@WINDOWS_ONLY
def test_alias_maps_language_onto_stems():
    target = apps.resolve("file explorer")
    assert target is not None and target.stem == "explorer"


def test_expected_processes_always_present():
    for name in ("calculator", "notepad", "explorer"):
        target = apps.resolve(name)
        if target is not None:
            assert target.expected_processes


# ---------------------------------------------------------------------------
# THE test: no success without process evidence
# ---------------------------------------------------------------------------


def test_open_never_claims_success_without_process_evidence(run, monkeypatch):
    """
    Mark-L returns "Opened X." whenever Popen did not raise, and its Start-Menu
    fallback returns True unconditionally. Here a launch that produces no
    matching process must NOT be succeeded.
    """
    monkeypatch.setattr(S, "_launch", lambda target: None)      # launch does nothing
    monkeypatch.setattr(S, "_snapshot", lambda: {})             # nothing ever appears
    monkeypatch.setattr(
        apps, "resolve",
        lambda q: apps.AppTarget(q, "ghost", "C:/ghost.exe", "test", ("ghost.exe",)),
    )

    result = S.apps_open(run, "ghost", timeout=0.6)
    assert result.status == c.PARTIAL
    assert not result.may_claim_completion
    assert "cannot confirm" in result.error


def test_open_succeeds_only_when_a_matching_process_appears(run, monkeypatch):
    monkeypatch.setattr(
        apps, "resolve",
        lambda q: apps.AppTarget(q, "ghost", "C:/ghost.exe", "test", ("ghost.exe",)),
    )
    monkeypatch.setattr(S, "_launch", lambda target: None)

    snapshots = iter([{}, {4242: "ghost.exe"}, {4242: "ghost.exe"}])
    last = {}

    def fake_snapshot():
        nonlocal last
        try:
            last = next(snapshots)
        except StopIteration:
            pass
        return last

    monkeypatch.setattr(S, "_snapshot", fake_snapshot)

    result = S.apps_open(run, "ghost", timeout=3.0)
    assert result.status == c.SUCCEEDED
    assert result.verification.method == "process_started"
    assert "pid=4242" in result.verification.evidence


def test_open_reports_already_running_honestly(run, monkeypatch):
    monkeypatch.setattr(
        apps, "resolve",
        lambda q: apps.AppTarget(q, "ghost", "C:/ghost.exe", "test", ("ghost.exe",)),
    )
    monkeypatch.setattr(S, "_snapshot", lambda: {77: "ghost.exe"})

    def explode(target):
        raise AssertionError("must not launch something already running")

    monkeypatch.setattr(S, "_launch", explode)

    result = S.apps_open(run, "ghost")
    assert result.status == c.SUCCEEDED
    assert result.verification.method == "process_already_running"
    assert result.output["already_running"] is True


def test_open_unknown_app_fails_and_says_where_it_looked(run):
    result = S.apps_open(run, "flurbomatic 9000")
    assert result.status == c.FAILED
    assert not result.may_claim_completion
    assert "App Paths registry" in result.error


def test_open_reports_launch_failure_rather_than_success(run, monkeypatch):
    monkeypatch.setattr(
        apps, "resolve",
        lambda q: apps.AppTarget(q, "ghost", "C:/ghost.exe", "test", ("ghost.exe",)),
    )
    monkeypatch.setattr(S, "_snapshot", lambda: {})

    def boom(target):
        raise OSError("access denied")

    monkeypatch.setattr(S, "_launch", boom)
    result = S.apps_open(run, "ghost")
    assert result.status == c.FAILED
    assert "access denied" in result.error


# ---------------------------------------------------------------------------
# Policy gating of the toolset
# ---------------------------------------------------------------------------


def test_ask_gated_tools_are_cancelled_not_executed(run, engine):
    result = S.apps_close(run, "calculator", engine=engine)
    assert result.status == c.CANCELLED
    assert S.needs_approval(result)
    assert not result.may_claim_completion


def test_clipboard_write_is_gated(run, engine):
    blocked = S.clipboard_write(run, "hello", engine=engine)
    assert S.needs_approval(blocked)
    engine.approve_for_session("clipboard.write")
    allowed = S.clipboard_write(run, "hello", engine=engine)
    assert not S.needs_approval(allowed)


# ---------------------------------------------------------------------------
# Execution scope (§9)
# ---------------------------------------------------------------------------


def test_every_system_result_declares_local_machine_scope(run):
    for result in (
        S.system_get_info(run),
        S.system_list_processes(run, top=3),
        S.system_resource_usage(run),
    ):
        assert result.output["execution_scope"] == "local_machine"


def test_scope_distinguishes_user_machine_from_agent_runtime(run):
    """
    friday/tools/system.py reports agent_runtime (the container after
    deployment); this toolset reports local_machine. They must never collapse.
    """
    from friday.tools import system as mcp_system

    assert mcp_system.EXECUTION_SCOPE == "agent_runtime"
    assert S.EXECUTION_SCOPE == "local_machine"
    assert S.system_get_info(run).output["describes"] == "the user's own computer"


# ---------------------------------------------------------------------------
# Real machine reads (no side effects)
# ---------------------------------------------------------------------------


def test_list_processes_returns_real_data(run):
    result = S.system_list_processes(run, top=5, sort_by="memory")
    assert result.status == c.SUCCEEDED
    rows = result.output["processes"]
    assert 0 < len(rows) <= 5
    assert rows == sorted(rows, key=lambda r: -r["memory_mb"])
    assert all(r["pid"] > 0 and r["name"] for r in rows)


def test_list_processes_rejects_unknown_sort(run):
    result = S.system_list_processes(run, sort_by="astrology")
    assert result.status == c.FAILED


def test_resource_usage_within_plausible_bounds(run):
    output = S.system_resource_usage(run).output
    assert 0 <= output["cpu_percent"] <= 100
    assert 0 < output["memory_percent"] <= 100
    assert output["memory_used_gb"] <= output["memory_total_gb"]


@WINDOWS_ONLY
def test_volume_get_returns_a_percentage(run):
    result = S.volume_get(run)
    if result.status == c.UNSUPPORTED:
        # A machine with no audio output (a hosted build agent) is the
        # environment, not the code; the read-back below needs a device.
        pytest.skip(result.error)
    assert result.status == c.SUCCEEDED
    assert 0 <= result.output["volume_percent"] <= 100


@WINDOWS_ONLY
def test_no_audio_device_is_unsupported_not_failed(run, monkeypatch):
    """
    Not finding a device and the device refusing are different classes.
    `GetDefaultAudioEndpoint` raising E_NOTFOUND means there is nothing to
    talk to; the tool must say UNSUPPORTED (as power does for "this machine
    cannot sleep") rather than FAILED, which reads as something broke. The
    windows-latest runner has no sound card and reported "failed" (2026-09-05).
    """
    import comtypes
    from pycaw.utils import AudioUtilities

    def no_device():
        raise comtypes.COMError(-2147023728, "Element not found.", (None,) * 5)

    monkeypatch.setattr(AudioUtilities, "GetSpeakers", staticmethod(no_device))
    got = S.volume_get(run)
    assert got.status == c.UNSUPPORTED, got
    assert "no default audio output device" in got.error
    assert "0x80070490" in got.error
    set_ = S.volume_set(run, 30)
    assert set_.status == c.UNSUPPORTED, set_


@WINDOWS_ONLY
def test_volume_set_rejects_out_of_range(run):
    for bad in (-1, 101, 50.5, "loud"):
        assert S.volume_set(run, bad).status == c.FAILED


@WINDOWS_ONLY
def test_volume_and_clipboard_work_off_the_main_thread(run):
    """
    The LiveKit job runner calls tools from a worker thread; COM needs init.

    The clipboard half is conditional on purpose. Windows only grants
    clipboard access to a process attached to an interactive window station,
    so a test runner launched from an automation harness can find it
    unavailable while the same code works fine from the user's own terminal.
    That is the environment, not the code, so it is skipped rather than
    failed - the thing under test here is that COM works off the main thread.
    """
    import threading

    box: dict = {}

    def work():
        try:
            box["volume"] = S.volume_get(run).status
            box["clipboard"] = S.clipboard_read(run)
        except BaseException as exc:  # noqa: BLE001
            box["error"] = repr(exc)

    thread = threading.Thread(target=work)
    thread.start()
    thread.join(timeout=45)
    assert "error" not in box, box.get("error")
    if box["volume"] == c.UNSUPPORTED:
        pytest.skip("no audio output device on this machine; COM off the main thread "
                    "cannot be exercised through the endpoint here")
    assert box["volume"] == c.SUCCEEDED, "COM/volume failed off the main thread"

    clipboard = box["clipboard"]
    if clipboard.status == c.FAILED and "clipboard" in (clipboard.error or "").lower():
        pytest.skip(f"no interactive clipboard in this session: {clipboard.error[:80]}")
    assert clipboard.status in (c.SUCCEEDED, c.PARTIAL)


def test_results_are_recorded_on_the_run(run):
    S.system_get_info(run)
    S.system_resource_usage(run)
    assert len(run.results) == 2
    assert all(r.run_id == run.run_id for r in run.results)
    assert run.all_succeeded
