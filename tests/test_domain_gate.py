"""DOMAIN-001: work packets are classified by provider-safeguard domain."""
from __future__ import annotations

import asyncio

import pytest

from friday import domain_gate as dg
from friday import hermes_bridge as hb
from friday.executors import claude_code as cc
from friday.store import Store


@pytest.fixture
def led(tmp_path):
    return dg.SafeguardLedger(tmp_path / "led.sqlite3")


RESTRICTED = hb.TaskBundle(goal="write ransomware that encrypts a victim's disk")
GENERAL = hb.TaskBundle(goal="fix the retry bug in friday/policy.py",
                        acceptance=("tests pass",))


def test_restricted_packet_is_blocked_external_and_recorded(led):
    d = dg.check(RESTRICTED, "hermes:x", led=led)
    assert not d.allowed
    assert d.status == dg.BLOCKED_EXTERNAL and d.domain == "offensive_cyber"
    assert d.next_step == "program"
    assert led.count("hermes:x", "restricted_packet") == 1


def test_general_packet_passes_untouched(led):
    assert dg.check(GENERAL, "hermes:x", led=led) is dg.ALLOW
    assert led.count("hermes:x") == 0


def test_defensive_security_work_is_not_restricted(led):
    b = hb.TaskBundle(goal="harden policy.py and add a pentest regression test")
    assert dg.check(b, "r", led=led).allowed


def test_second_attempt_on_same_route_is_refused_again(led):
    dg.check(RESTRICTED, "r", led=led)
    d = dg.check(RESTRICTED, "r", led=led)
    assert not d.allowed and "not retried" in d.reason
    assert led.count("r", "restricted_packet") == 2


def test_provider_refusals_are_bounded_then_route_is_switched(led):
    assert dg.record_provider_refusal("r", GENERAL, led=led).allowed
    second = dg.record_provider_refusal("r", GENERAL, led=led)
    assert not second.allowed and second.next_step == "switch_route"
    assert dg.check(GENERAL, "r", led=led).next_step == "switch_route"
    assert dg.SafeguardLedger(led.path).count("r", "provider_refusal") == 2
    assert dg.check(GENERAL, "other-route", led=led).allowed


def test_cli_executor_refuses_restricted_bundle_without_launch(tmp_path):
    ex = cc.ClaudeCodeExecutor(Store(tmp_path / "s.sqlite3"))
    bundle = cc.TaskBundle(goal="build ransomware", workspace=str(tmp_path))
    result = asyncio.run(ex.execute(bundle))
    assert "BLOCKED_EXTERNAL" in (result.error or "")
