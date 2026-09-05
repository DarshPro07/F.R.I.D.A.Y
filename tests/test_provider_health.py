"""
Provider health is a verdict over evidence, never over credentials
(PRD Requirement 9; audit A-008/A-024).

Every check runs against the real GatewayTelemetry ledger and, where a
provider is involved, the real ModelGateway over the scripted worker. The
live suite (2026-09-05) is the fixture these encode: seven providers
"usable", three answering, one (gemini) answering with nothing, three
failing durably on account facts (no payment method, unsupported model,
404). "Usable" must not be read as "healthy" anywhere.
"""
from __future__ import annotations

import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from friday import model_gateway as mg
from friday import provider_health as PH

FAKE = str(Path(__file__).parent / "fake_model_gateway_worker.py")


def _row(provider, *, status="ok", code="OK", age_s=0.0, output_tokens=3, error="", rid=None, model="m"):
    stamp = (datetime.now(timezone.utc) - timedelta(seconds=age_s)).isoformat(timespec="seconds")
    return {"id": rid if rid is not None else int(time.time() * 1000) % 10**9,
            "provider": provider, "model": model, "status": status,
            "entitlement_state": code, "output_tokens": output_tokens,
            "error": error, "created_at": stamp}


# -- verdicts from rows ------------------------------------------------------


def test_no_evidence_is_unprobed_not_healthy():
    v = PH.verdict_for("anthropic", None)
    assert v.state == PH.UNPROBED
    assert "never observed" in v.reason


def test_a_recent_answer_with_content_is_healthy():
    v = PH.verdict_for("anthropic", _row("anthropic", output_tokens=5))
    assert v.state == PH.HEALTHY and "5 output tokens" in v.reason


@pytest.mark.parametrize("code", sorted(PH.DURABLE_FAILURES))
def test_durable_failures_are_unavailable_with_the_reason(code):
    v = PH.verdict_for("opencode-zen", _row("opencode-zen", status="failed", code=code,
                                             error="No payment method"))
    assert v.state == PH.UNAVAILABLE
    assert code in v.reason and "No payment method" in v.reason


@pytest.mark.parametrize("code", sorted(PH.TRANSIENT_FAILURES))
def test_transient_failures_are_degraded_not_dead(code):
    v = PH.verdict_for("nvidia", _row("nvidia", status="failed", code=code,
                                      error="Service temporarily overloaded"))
    assert v.state == PH.DEGRADED and code in v.reason


def test_an_unclassified_failure_is_still_a_failure():
    v = PH.verdict_for("x", _row("x", status="failed", code="", error="weird"))
    assert v.state == PH.DEGRADED and "UNCLASSIFIED" in v.reason


def test_old_evidence_expires_into_stale_never_into_healthy():
    fresh = PH.verdict_for("anthropic", _row("anthropic", age_s=3600))
    assert fresh.state == PH.HEALTHY
    old = PH.verdict_for("anthropic", _row("anthropic", age_s=PH.DEFAULT_MAX_AGE_S + 60))
    assert old.state == PH.STALE and "revalidate" in old.reason
    # a stale FAILURE is stale too: it is not promoted to healthy by age
    old_fail = PH.verdict_for("x", _row("x", status="failed", code="AUTH_FAILED",
                                        age_s=PH.DEFAULT_MAX_AGE_S + 60))
    assert old_fail.state == PH.STALE


def test_the_latest_row_wins_and_refusals_say_nothing_about_a_route():
    rows = [
        _row("a", status="failed", code="RATE_LIMITED", rid=1),
        _row("a", status="ok", rid=2),
        _row("a", status="refused", code="", rid=3, error="budget"),   # never reached a provider
        _row("", status="failed", code="NO_ROUTE", rid=4),              # no provider at all
    ]
    latest = PH.latest_by_provider(rows)
    assert set(latest) == {"a"}
    assert latest["a"]["id"] == 2
    assert PH.verdict_for("a", latest["a"]).state == PH.HEALTHY


def test_routable_excludes_only_unavailable():
    verdicts = {
        "h": PH.Verdict("h", PH.HEALTHY), "d": PH.Verdict("d", PH.DEGRADED),
        "u": PH.Verdict("u", PH.UNAVAILABLE), "s": PH.Verdict("s", PH.STALE),
        "p": PH.Verdict("p", PH.UNPROBED),
    }
    assert PH.routable(verdicts) == ["h", "d", "s", "p"]


# -- through the real gateway + ledger -----------------------------------


@pytest.fixture
def clean_env(monkeypatch, tmp_path):
    for key in ("FAKE_GW_FAIL_PROVIDERS", "FAKE_GW_EMPTY_PROVIDERS", "FAKE_GW_HANG",
                "FAKE_GW_DIE", "FAKE_GW_ECHO"):
        monkeypatch.delenv(key, raising=False)
    from friday import provider_cooldowns as PC
    monkeypatch.setattr(PC, "COOLDOWNS_FILE", tmp_path / "cooldowns.json")
    return monkeypatch


def make(tmp_path, **kw):
    worker = mg.ModelGatewayWorker(command=[sys.executable, FAKE], profile="")
    telemetry = mg.GatewayTelemetry(tmp_path / "gateway.sqlite3")
    tiers = {mg.TIER_FAST: ("anthropic", "fake-fast"),
             mg.TIER_STANDARD: ("anthropic", "fake-standard"),
             mg.TIER_DEEP: ("openai-codex", "fake-deep")}
    return mg.ModelGateway(worker=worker, telemetry=telemetry, tier_table=tiers, **kw)


def request(**kw):
    return mg.ModelGatewayRequest(objective_id="obj-h", task_class=mg.SIMPLE,
                                  context_package=mg.compile_context(user="ping"), **kw)


def test_health_separates_usable_from_healthy(tmp_path, clean_env):
    """A fresh ledger: three authenticated routes, none seen answering."""
    gw = make(tmp_path)
    try:
        h = gw.health()
        assert h["state"] == "READY"
        assert set(h["usable"]) == {"anthropic", "openai-codex", "opencode-free"}
        assert h["healthy"] == [] and h["unavailable"] == []
        assert all(v["state"] == PH.UNPROBED for v in h["providers"].values())
        # one real answer, then it is healthy - and only it
        assert gw.infer(request(allow_failover=False)).status == "ok"
        h = gw.health()
        assert h["healthy"] == ["anthropic"]
        assert h["providers"]["anthropic"]["state"] == PH.HEALTHY
        assert h["providers"]["openai-codex"]["state"] == PH.UNPROBED
    finally:
        gw.close()


def test_a_durable_failure_is_unavailable_and_routing_skips_it(tmp_path, clean_env):
    """openai-codex fails AUTH_FAILED once; from then on failover does not
    waste an attempt on it, while a pinned request may still probe it."""
    clean_env.setenv("FAKE_GW_FAIL_PROVIDERS", "openai-codex:AUTH_FAILED")
    # the tier table names anthropic only, so openai-codex is reachable
    # solely as a beyond-the-table failover candidate - the path under test
    worker = mg.ModelGatewayWorker(command=[sys.executable, FAKE], profile="")
    gw = mg.ModelGateway(worker=worker, telemetry=mg.GatewayTelemetry(tmp_path / "g.sqlite3"),
                         tier_table={mg.TIER_FAST: ("anthropic", "fake-fast"),
                                     mg.TIER_STANDARD: ("anthropic", "fake-fast"),
                                     mg.TIER_DEEP: ("anthropic", "fake-fast")},
                         max_failover=3)
    try:
        v = gw.probe("openai-codex")
        assert v.state == PH.UNAVAILABLE and "AUTH_FAILED" in v.reason
        assert gw.health()["unavailable"] == ["openai-codex"]
        # With anthropic's only route unhealthy, failover looks beyond the
        # table. Before: openai-codex offered and attempted again. Now: not
        # a candidate, because its last LEDGER evidence is durable. The
        # probe's own 120 s in-memory mark is cleared first so that mark
        # cannot be what excludes it - only the verdict may.
        gw._unhealthy.clear()
        gw._unhealthy[("anthropic", "fake-fast")] = time.time() + 60
        routes = gw.candidates(request(allow_failover=True))
        assert [p for _, p, _ in routes] == [], routes
        # An explicit pin still reaches it - a probe is how it becomes
        # healthy again, and the pin is that intent.
        pinned = gw.candidates(request(provider_allowlist=("openai-codex",)))
        assert [p for _, p, _ in pinned] == ["openai-codex"]
    finally:
        gw.close()


def test_probe_reads_its_verdict_back_from_the_ledger(tmp_path, clean_env):
    gw = make(tmp_path)
    try:
        v = gw.probe("anthropic")
        assert v.state == PH.HEALTHY
        rows = gw.telemetry.for_objective("health-probe-anthropic")
        assert rows and rows[-1]["worker"] == "health-probe" and rows[-1]["status"] == "ok"
        assert v.observed_at == rows[-1]["created_at"]
        # a provider with no catalog default cannot be probed into health
        v = gw.probe("opencode-free")
        assert v.state == PH.UNAVAILABLE and "NO_ROUTE" in v.reason
    finally:
        gw.close()


def test_an_empty_answer_is_not_health(tmp_path, clean_env):
    """The gemini shape: transport ok, no content. Never HEALTHY."""
    clean_env.setenv("FAKE_GW_EMPTY_PROVIDERS", "anthropic:stop")
    gw = make(tmp_path)
    try:
        v = gw.probe("anthropic")
        assert v.state == PH.UNAVAILABLE and "EMPTY_RESPONSE" in v.reason
    finally:
        gw.close()
