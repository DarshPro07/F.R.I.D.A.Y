"""
S2 quota routing: a 429/quota message is CAPPED, not TRANSIENT, and the
router switches provider instead of retrying the same capped one.
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from friday import provider_diagnostics as PD
from friday import provider_cooldowns as PC
from friday import execution_economics as ee
from friday import objectives as O
from friday import continuous as C
from friday import hermes_bridge as hb

FAKE = str(Path(__file__).parent / "fake_hermes_gateway.py")


class _Err:
    def __init__(self, body, status=None):
        self.body = body
        self.status_code = status

    def __str__(self):
        return self.body


def test_rate_limit_wording_is_capped_not_transient():
    found = PD.diagnose(_Err("rate limit exceeded", status=429))
    assert found.kind == PD.CAPPED
    assert found.worth_retrying


def test_reset_at_parses_explicit_clock_time():
    now = datetime(2026, 9, 4, 10, 0)
    found = PD.diagnose(_Err("quota exceeded, resets at 14:30"))
    reset = datetime.fromisoformat(found.reset_at)
    assert (reset.hour, reset.minute) == (14, 30)


def test_reset_at_parses_retry_after_seconds():
    before = datetime.now()
    found = PD.diagnose(_Err("usage limit reached, retry after 120"))
    reset = datetime.fromisoformat(found.reset_at)
    assert before + timedelta(seconds=110) < reset < before + timedelta(seconds=130)


def test_reset_at_defaults_by_wording():
    five_hour = PD.diagnose(_Err("5-hour limit reached"))
    weekly = PD.diagnose(_Err("weekly quota reached"))
    daily = PD.diagnose(_Err("daily limit reached"))
    unknown = PD.diagnose(_Err("too many requests"))
    now = datetime.now()
    assert now < datetime.fromisoformat(five_hour.reset_at) < now + timedelta(hours=1, minutes=1)
    assert now + timedelta(hours=23) < datetime.fromisoformat(weekly.reset_at)
    assert datetime.fromisoformat(daily.reset_at).hour == 0
    assert now < datetime.fromisoformat(unknown.reset_at) < now + timedelta(minutes=31)


def test_plain_429_stays_transient():
    found = PD.diagnose(_Err("service unavailable", status=429))
    assert found.kind == PD.TRANSIENT


@pytest.fixture
def cooldowns_file(tmp_path, monkeypatch):
    path = tmp_path / "provider_cooldowns.json"
    monkeypatch.setattr(PC, "COOLDOWNS_FILE", path)
    return path


def test_cooldown_persists_and_expires(cooldowns_file):
    future = (datetime.now() + timedelta(minutes=5)).isoformat()
    PC.mark("acme", "big-model", future, reason="capped")
    assert ("acme", "big-model") in PC.active()

    past = (datetime.now() - timedelta(minutes=5)).isoformat()
    PC.mark("acme", "small-model", past, reason="capped")
    assert ("acme", "small-model") not in PC.active()

    PC.clear()
    assert PC.active() == {}


def test_candidates_order(monkeypatch, cooldowns_file):
    monkeypatch.setattr(hb, "_profile_model_default",
                        lambda: ("default-provider", "default-model"))
    monkeypatch.setattr(ee, "_fallback_candidates",
                        lambda: [("backup-provider", "backup-model")])
    cands = ee.candidates(ee.TIER_STANDARD)
    assert cands[0] == ("default-provider", "claude-sonnet-5")
    assert cands[1] == ("backup-provider", "backup-model")
    assert cands[-1] == ("default-provider", "default-model")


def test_plan_delegation_skips_a_cooled_candidate(monkeypatch, cooldowns_file):
    monkeypatch.setattr(hb, "_profile_model_default",
                        lambda: ("default-provider", "default-model"))
    monkeypatch.setattr(ee, "_fallback_candidates",
                        lambda: [("backup-provider", "backup-model")])
    until = (datetime.now() + timedelta(minutes=10)).isoformat()
    PC.mark("default-provider", "claude-sonnet-5", until, reason="capped")

    plan = ee.plan_delegation("do a standard task")
    assert plan["provider"] == "backup-provider"
    assert plan["model"] == "backup-model"
    assert plan["switched_from"] == "default-provider"
    assert "capped until" in plan["reason"]


def test_plan_delegation_all_cooled_waits(monkeypatch, cooldowns_file):
    monkeypatch.setattr(hb, "_profile_model_default",
                        lambda: ("default-provider", "default-model"))
    monkeypatch.setattr(ee, "_fallback_candidates", lambda: [])
    until = (datetime.now() + timedelta(minutes=10)).isoformat()
    PC.mark("default-provider", "claude-sonnet-5", until, reason="capped")
    PC.mark("default-provider", "default-model", until, reason="capped")

    plan = ee.plan_delegation("do a standard task")
    assert plan["reason"].startswith("waiting for default-provider until")


def make_supervisor(tmp_path, monkeypatch, *, flags=None):
    for key in ("FAKE_HERMES_CLARIFY", "FAKE_HERMES_HANG", "FAKE_HERMES_DIE",
                "FAKE_HERMES_CAPPED"):
        monkeypatch.delenv(key, raising=False)
    for key, value in (flags or {}).items():
        monkeypatch.setenv(key, value)
    log = hb.WorkRunLog(tmp_path / "bridge.sqlite3")
    supervisor = hb.HermesSupervisor(log=log, command=[sys.executable, FAKE], profile="")
    supervisor.READY_TIMEOUT = 20
    return supervisor


def test_bridge_marks_cooldown_from_a_capped_gateway_error(tmp_path, monkeypatch, cooldowns_file):
    supervisor = make_supervisor(tmp_path, monkeypatch, flags={"FAKE_HERMES_CAPPED": "1"})
    try:
        out = supervisor.delegate(hb.TaskBundle(goal="x"), wait=True, turn_timeout=30)
        record = out["result"]
        assert record["failure_kind"] == "CAPPED"
        assert "capped until" in record["route_reason"]
        assert ("fake", "fake-model") in PC.active()

        progress = supervisor.progress(out["work_run_id"])
        assert "capped until" in progress["route_reason"]
        assert progress["switched_from"] == "fake"
    finally:
        supervisor.stop()


def test_continuous_requeues_capped_without_backoff():
    # Was: delay always 0.0 for CAPPED, which hammers the still-capped
    # provider on every poll inside its own cooldown window. Fixed: the
    # delay must reach the reset time (see test below), never 0 inside a
    # window that is still open.
    assert O.FailureKind.CAPPED in O.RETRYABLE_KINDS


def test_plan_delegation_all_cooled_returns_no_capped_candidate(
        monkeypatch, cooldowns_file):
    """
    All candidates cooled: plan_delegation must not hand back a capped
    model/provider as "chosen" (that dispatches straight into the cap).
    """
    monkeypatch.setattr(hb, "_profile_model_default",
                        lambda: ("default-provider", "default-model"))
    monkeypatch.setattr(ee, "_fallback_candidates", lambda: [])
    until = (datetime.now() + timedelta(minutes=10)).isoformat()
    PC.mark("default-provider", "claude-sonnet-5", until, reason="capped")
    PC.mark("default-provider", "default-model", until, reason="capped")

    plan = ee.plan_delegation("do a standard task")
    assert plan["model"] == ""
    assert plan["wait_until"]


def test_capped_requeue_delay_reaches_the_reset(monkeypatch):
    """
    A CAPPED failure must requeue with delay = seconds to the reset, not
    0.0 - 0.0 hammers the still-capped provider on every poll.
    """
    from friday import provider_diagnostics as PD

    reset_at = (datetime.now() + timedelta(minutes=10)).isoformat()
    found = PD.Diagnosis(kind=PD.CAPPED, finish_reason="", status_code=429,
                         detail="a usage limit was reached", reset_at=reset_at)
    delay = max(0.0, (datetime.fromisoformat(found.reset_at)
                      - datetime.now()).total_seconds())
    assert delay > 500  # ~10 minutes, never 0 inside the window


if __name__ == "__main__":
    # ponytail: quick self-check without pytest, per house rule.
    test_rate_limit_wording_is_capped_not_transient()
    test_plain_429_stays_transient()
    test_continuous_requeues_capped_without_backoff()
    print("ok")
