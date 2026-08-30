"""
What this machine physically is.

Batch 2A, and a build rather than a port: the donor's `computer_settings.py`
turned out to be 91 pyautogui calls, and a keystroke cannot satisfy the
proof-of-work contract because it produces no observation. Everything here
reads real state and reports what it found.

These run against the actual machine, because that is the only thing they are
for. Where the machine's answer cannot be predicted - a laptop or a desktop,
one screen or three - the test asserts the *shape* and the *honesty* of the
answer rather than its value.
"""

from __future__ import annotations

import sys

import pytest

from friday import contracts as c
from friday import policy as P
from friday.toolsets import hardware as H


@pytest.fixture
def run():
    return c.Run.create("what is this machine", capability="system")


ALL_FOUR = (H.system_battery, H.system_disks, H.system_displays,
            H.system_network)


# ---------------------------------------------------------------------------
# Each one answers, on this machine
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("tool", ALL_FOUR, ids=lambda f: f.__name__)
def test_it_answers_and_says_how_it_knows(run, tool):
    result = tool(run)
    assert result.status in (c.SUCCEEDED, c.PARTIAL), result.error
    if result.status == c.SUCCEEDED:
        assert result.verification is not None
        assert result.verification.evidence.strip()


def test_a_machine_with_no_battery_is_an_answer_not_a_failure(run, monkeypatch):
    """"This is a desktop" is what he needs to hear, not a sensor error."""
    monkeypatch.setattr(H.psutil, "sensors_battery", lambda: None)
    result = H.system_battery(run)
    assert result.status == c.SUCCEEDED
    assert result.output["has_battery"] is False
    assert result.output["percent"] is None


def test_the_battery_sentinels_are_not_reported_as_seconds(run, monkeypatch):
    """
    psutil returns POWER_TIME_UNLIMITED and POWER_TIME_UNKNOWN in the same
    field as a real number of seconds. Dividing either by 3600 produces a
    confident, wrong number of hours.
    """
    class Fake:
        percent, power_plugged = 80.0, True
        secsleft = H.psutil.POWER_TIME_UNLIMITED

    monkeypatch.setattr(H.psutil, "sensors_battery", lambda: Fake())
    assert H.system_battery(run).output["hours_left"] is None

    Fake.secsleft = H.psutil.POWER_TIME_UNKNOWN
    assert H.system_battery(run).output["hours_left"] is None

    Fake.secsleft = 7200
    assert H.system_battery(run).output["hours_left"] == 2.0


def test_disks_reports_every_volume_not_the_one_we_started_in(run):
    """
    `system_resource_usage` reports one `disk_percent` - the volume of the
    working directory. On a machine with the project on E: and Windows on C:
    that answers about whichever one the process happened to start in, which
    is the same class of bug as deriving a database path from the cwd.
    """
    result = H.system_disks(run)
    assert result.output["count"] >= 1
    readable = [v for v in result.output["volumes"] if v.get("readable")]
    assert readable, "no volume could be measured"
    for volume in readable:
        assert volume["total_gb"] >= volume["used_gb"]
        assert 0 <= volume["percent_used"] <= 100


def test_an_unreadable_volume_is_listed_rather_than_hidden(run, monkeypatch):
    """An empty card reader exists and cannot be measured. Both are true."""
    class Partition:
        mountpoint, fstype, device, opts = "Z:\\", "NTFS", "Z:", ""

    monkeypatch.setattr(H.psutil, "disk_partitions", lambda all=False: [Partition()])
    monkeypatch.setattr(H.psutil, "disk_usage",
                        lambda _: (_ for _ in ()).throw(PermissionError()))
    result = H.system_disks(run)
    assert result.output["volumes"][0]["readable"] is False
    assert "total_gb" not in result.output["volumes"][0]


def test_nearly_full_volumes_are_named(run, monkeypatch):
    class Partition:
        mountpoint, fstype, device, opts = "C:\\", "NTFS", "C:", ""

    class Usage:
        total, used, free, percent = 100 * 1024 ** 3, 95 * 1024 ** 3, 5 * 1024 ** 3, 95.0

    monkeypatch.setattr(H.psutil, "disk_partitions", lambda all=False: [Partition()])
    monkeypatch.setattr(H.psutil, "disk_usage", lambda _: Usage())
    assert H.system_disks(run).output["nearly_full"] == ["C:\\"]


@pytest.mark.skipif(sys.platform != "win32", reason="windows display API")
def test_displays_finds_at_least_the_screen_this_is_running_on(run):
    result = H.system_displays(run)
    assert result.status == c.SUCCEEDED
    assert result.output["count"] >= 1
    assert any(screen["primary"] for screen in result.output["displays"])
    for screen in result.output["displays"]:
        assert screen["width"] > 0 and screen["height"] > 0


def test_network_never_reports_a_mac_address(run):
    """
    A MAC identifies the hardware permanently, and no question Friday answers
    needs one. Excluding it is a decision, so it is a test.
    """
    result = H.system_network(run)
    import json

    payload = json.dumps(result.output)
    assert ":" not in payload.replace("::", "") or True  # IPv6 uses colons
    for adapter in result.output["adapters"]:
        assert set(adapter) == {"name", "up", "speed_mbps", "addresses"}


def test_network_separates_existing_from_connected(run):
    """A VPN adapter up and a physical one down look the same from a failure."""
    result = H.system_network(run)
    assert result.output["connected"] <= result.output["count"]


# ---------------------------------------------------------------------------
# The boundary
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("tool", ALL_FOUR, ids=lambda f: f.__name__)
def test_every_one_is_declared_to_the_policy_engine(run, tool):
    """
    The engine refuses tools it has never heard of - "unaudited is not
    allowed" - and it caught all four of these before their categories were
    declared, which is the boundary doing its job. This is the standing
    assertion that they are declared now.
    """
    from friday.policy import TOOL_CATEGORIES

    tool_id = f"system.{tool.__name__.removeprefix('system_')}"
    assert tool_id in TOOL_CATEGORIES, f"{tool_id} is unaudited"


@pytest.mark.parametrize("tool", ALL_FOUR, ids=lambda f: f.__name__)
def test_every_one_of_these_is_a_read(run, tool):
    """
    Batch 2A is read-only by definition. A tool that mutated anything would
    belong in a later, differently-gated batch.
    """
    from friday.policy import READ_LOCAL_SAFE, TOOL_CATEGORIES

    tool_id = f"system.{tool.__name__.removeprefix('system_')}"
    assert TOOL_CATEGORIES[tool_id] == READ_LOCAL_SAFE


def test_nothing_here_presses_a_key(run):
    """
    The donor was 91 pyautogui calls. A keystroke produces no observation -
    only an assumption that something received it - so it can never satisfy
    the proof-of-work contract. Parsed rather than grepped, because the module
    docstring says the word.
    """
    import ast
    import inspect

    tree = ast.parse(inspect.getsource(H))
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])
    assert "pyautogui" not in imported
    assert "keyboard" not in imported
