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


# ---------------------------------------------------------------------------
# A-018 / A-019: health is a property of a ROUTE, not of a provider.
#
# Keyed by provider alone the verdict is wrong in both directions, and both
# were reproduced against this module before it changed:
#
#   * one unsupported MODEL condemns the provider - the live suite recorded
#     `opencode-free` UNAVAILABLE because `laguna-s-2.1-free` is unsupported,
#     a model fact filed as a provider fact, which makes every other model on
#     that provider unreachable;
#   * one working model HIDES a broken one - a 401 on `gpt-5.4-mini` followed
#     by a success on `gpt-5.4` reads HEALTHY, and routing keeps choosing the
#     model that cannot authenticate.
# ---------------------------------------------------------------------------

STAMP = "2026-09-06T12:00:00+00:00"
NOW = 1_788_696_060.0          # a minute after STAMP, so nothing reads STALE


def _ledger(tmp_path, rows):
    from friday.model_gateway import GatewayTelemetry
    t = GatewayTelemetry(tmp_path / "calls.sqlite3")
    for r in rows:
        t.record(objective_id="probe", worker="w", task_class="TRIVIAL",
                 created_at=STAMP, **r)
    return t


def _route(provider, model, status, *, code="", error="", out=7,
           credential="profile:friday"):
    return dict(provider=provider, model=model, credential=credential,
                status=status, entitlement_state=code, error=error,
                output_tokens=out)


def test_a_broken_model_does_not_condemn_the_provider(tmp_path):
    """The `opencode-free` shape: the newest row is a model-specific failure."""
    from friday import provider_health as H
    t = _ledger(tmp_path, [
        _route("gemini", "gemini-3.6-flash", "ok"),
        _route("gemini", "gemini-experimental", "failed",
               code="UNSUPPORTED_MODEL", error="model not found", out=0),
    ])
    good = H.verdict_for_route(t, "gemini", "gemini-3.6-flash", "profile:friday", now=NOW)
    bad = H.verdict_for_route(t, "gemini", "gemini-experimental", "profile:friday", now=NOW)
    assert good.state == H.HEALTHY, (
        f"a working model is unreachable because another model failed: {good}")
    assert bad.state != H.HEALTHY, bad


def test_a_working_model_does_not_hide_a_broken_one(tmp_path):
    """The other direction, which is the dangerous one: routing keeps picking
    a model that cannot authenticate because a sibling answered."""
    from friday import provider_health as H
    t = _ledger(tmp_path, [
        _route("openai-api", "gpt-5.4-mini", "failed",
               code="AUTH_FAILED", error="401 unauthorized", out=0),
        _route("openai-api", "gpt-5.4", "ok"),
    ])
    broken = H.verdict_for_route(t, "openai-api", "gpt-5.4-mini", "profile:friday", now=NOW)
    fine = H.verdict_for_route(t, "openai-api", "gpt-5.4", "profile:friday", now=NOW)
    assert broken.state == H.UNAVAILABLE, (
        f"the 401 is invisible behind a sibling model's success: {broken}")
    assert fine.state == H.HEALTHY, fine


def test_evidence_under_one_credential_is_not_evidence_under_another(tmp_path):
    """A revoked key, an exhausted account or a second profile is a different
    entitlement. The fallback is the PROVIDER's view, never the other
    credential's row."""
    from friday import provider_health as H
    t = _ledger(tmp_path, [
        _route("openai-api", "gpt-5.4", "failed", code="AUTH_FAILED",
               error="401", out=0, credential="profile:revoked"),
    ])
    other = H.verdict_for_route(t, "openai-api", "gpt-5.4", "profile:friday", now=NOW)
    same = H.verdict_for_route(t, "openai-api", "gpt-5.4", "profile:revoked", now=NOW)
    assert same.state == H.UNAVAILABLE, same
    assert other.state != H.UNAVAILABLE, (
        "one credential's 401 was read as another credential's state: " + str(other))


def test_the_provider_level_view_still_answers_can_this_be_reached(tmp_path):
    """`assess` is kept deliberately: failover and the control room ask about
    the provider, not one route. A gate that only answered per route would
    have no answer for "is anthropic reachable at all"."""
    from friday import provider_health as H
    t = _ledger(tmp_path, [_route("anthropic", "claude-opus-5", "ok")])
    verdicts = H.assess(t, ["anthropic", "never-called"], now=NOW)
    assert verdicts["anthropic"].state == H.HEALTHY
    assert verdicts["never-called"].state == H.UNPROBED


def test_a_ledger_written_before_the_credential_column_still_opens(tmp_path):
    """`CREATE TABLE IF NOT EXISTS` does nothing to an existing table, so the
    owner's live ledger would keep its old shape and the first INSERT naming
    `credential` would fail on HIS machine. Migrate at open, and create the
    route index only AFTER the column exists - an index inside the schema
    script runs against the old table and dies with "no such column"."""
    import sqlite3
    from friday.model_gateway import GATEWAY_CALLS_SCHEMA, GatewayTelemetry

    old = tmp_path / "old.sqlite3"
    raw = sqlite3.connect(str(old))
    raw.executescript(GATEWAY_CALLS_SCHEMA.replace(
        "    credential        TEXT NOT NULL DEFAULT '',\n", ""))
    raw.execute("INSERT INTO gateway_calls (objective_id,worker,task_class,status,"
                "created_at) VALUES ('old','w','TRIVIAL','ok',?)", (STAMP,))
    raw.commit()
    raw.close()
    assert "credential" not in {
        r[1] for r in sqlite3.connect(str(old)).execute("PRAGMA table_info(gateway_calls)")}

    t = GatewayTelemetry(old)                     # opening migrates
    assert "credential" in {
        r[1] for r in sqlite3.connect(str(old)).execute("PRAGMA table_info(gateway_calls)")}
    t.record(objective_id="new", worker="w", task_class="TRIVIAL", status="ok",
             provider="gemini", model="gemini-3.6-flash",
             credential="profile:friday", output_tokens=7, created_at=STAMP)
    assert len(t.recent(limit=10)) == 2, "the pre-existing row did not survive"


def test_the_credential_label_is_never_the_secret():
    """NON_NEGOTIABLE 4: no key material anywhere a model can read - and a
    hash of a key is still derived from key material. The label names the
    profile, which is the identity that selects the credentials."""
    from friday.model_gateway import ModelGateway, ModelGatewayWorker

    gateway = ModelGateway(worker=ModelGatewayWorker(profile="friday"))
    label = gateway.credential_label("openai-api")
    assert label == "profile:friday", label
    assert "sk-" not in label and len(label) < 64
