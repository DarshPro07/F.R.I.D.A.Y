"""
Hermes MODEL_GATEWAY (PRD v3.1 §4.9): proven against a scripted worker.

Every test runs the REAL gateway - real subprocess, real JSON-lines
transport, real budget/growth/failover/telemetry logic - against
`fake_model_gateway_worker.py`, which speaks the worker protocol. What is
faked is the provider, not the gateway.

    FR-069/079  inference-only: one bounded request, one reply, no loop
    FR-070      envelope in, envelope out, Friday owns the objective id
    FR-071/072  provider inventory is queried, route kinds distinguished
    FR-074      boundary is labelled truthfully
    FR-076      compiled context is bounded
    FR-077      over-budget context is refused without explicit escalation
    FR-078      growth guard stops runaway growth / repeats / ceilings
    FR-080      telemetry attributes every call
    FR-081      failover is bounded and never resets the objective
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

from friday import model_gateway as mg

FAKE = str(Path(__file__).parent / "fake_model_gateway_worker.py")


@pytest.fixture
def clean_env(monkeypatch, tmp_path):
    for key in ("FAKE_GW_FAIL_PROVIDERS", "FAKE_GW_HANG", "FAKE_GW_DIE", "FAKE_GW_ECHO"):
        monkeypatch.delenv(key, raising=False)
    # Never touch the live cooldown file: a failover test marks routes.
    from friday import provider_cooldowns as PC
    monkeypatch.setattr(PC, "COOLDOWNS_FILE", tmp_path / "cooldowns.json")
    return monkeypatch


def make(tmp_path, *, tiers=None, max_failover=2) -> mg.ModelGateway:
    worker = mg.ModelGatewayWorker(command=[sys.executable, FAKE], profile="")
    telemetry = mg.GatewayTelemetry(tmp_path / "gateway.sqlite3")
    tiers = tiers or {
        mg.TIER_FAST: ("anthropic", "fake-fast"),
        mg.TIER_STANDARD: ("anthropic", "fake-standard"),
        mg.TIER_DEEP: ("openai-codex", "fake-deep"),
    }
    return mg.ModelGateway(worker=worker, telemetry=telemetry, tier_table=tiers,
                           max_failover=max_failover)


def request(objective="obj-1", task_class=mg.SIMPLE, text="ping", **kw):
    return mg.ModelGatewayRequest(
        objective_id=objective, task_class=task_class,
        context_package=mg.compile_context(system="You are Friday.", user=text),
        **kw)


# -- budgets ---------------------------------------------------------------


def test_every_task_class_has_a_budget_and_a_default_tier():
    for cls in mg.TASK_CLASSES:
        b = mg.budget_for(cls)
        assert b.max_input_tokens > 0 and b.max_output_tokens > 0
        assert b.default_tier in mg.TIERS
    with pytest.raises(ValueError):
        mg.budget_for("HUGE")


def test_budgets_grow_with_class():
    order = [mg.budget_for(c).max_input_tokens for c in
             (mg.TRIVIAL, mg.SIMPLE, mg.STANDARD, mg.COMPLEX)]
    assert order == sorted(order) and len(set(order)) == 4


def test_compile_context_is_bounded_and_ordered():
    history = [("user", f"turn {i}") for i in range(20)]
    msgs = mg.compile_context(system="persona", user="now", history=history,
                              memory="boss likes tea", max_history_turns=4)
    assert msgs[0] == {"role": "system", "content": "persona"}
    assert len(msgs) == 1 + 4 + 1
    assert msgs[-1]["role"] == "user"
    assert "boss likes tea" in msgs[-1]["content"]
    assert "turn 19" in msgs[-2]["content"]


# -- inference ------------------------------------------------------------


def test_infer_returns_envelope_and_records_telemetry(tmp_path, clean_env):
    gw = make(tmp_path)
    try:
        res = gw.infer(request())
        assert res.status == "ok"
        assert res.response == "PONG"
        assert res.provider == "anthropic" and res.model == "fake-fast"
        assert res.route_kind == "api" and res.boundary == "upstream_cloud"
        assert res.entitlement_state == "OK"
        assert res.failover_count == 0
        assert res.input_tokens > 0 and res.output_tokens == 2
        rows = gw.telemetry.for_objective("obj-1")
        assert len(rows) == 1
        row = rows[0]
        assert row["status"] == "ok" and row["worker"] == "friday"
        assert row["task_class"] == mg.SIMPLE and row["tier"] == mg.TIER_FAST
        assert row["provider"] == "anthropic" and row["model"] == "fake-fast"
        assert row["input_tokens"] == res.input_tokens
        assert res.call_id == row["id"]
        # FR-080: the prompt itself is NOT in the ledger.
        assert "ping" not in " ".join(str(v) for v in row.values())
    finally:
        gw.close()


def test_infer_sends_only_the_compiled_package(tmp_path, clean_env):
    """FR-076: what reaches the provider is exactly the context package."""
    clean_env.setenv("FAKE_GW_ECHO", "1")
    gw = make(tmp_path)
    try:
        req = request(text="hello there")
        res = gw.infer(req)
        assert res.response == req.context_package[-1]["content"][::-1]
    finally:
        gw.close()


def test_provider_inventory_distinguishes_route_kinds(tmp_path, clean_env):
    gw = make(tmp_path)
    try:
        inv = gw.providers()
        kinds = {p["id"]: p["route_kind"] for p in inv["providers"]}
        assert kinds["openai-codex"] == "subscription"
        assert kinds["anthropic"] == "api"
        assert kinds["opencode-free"] == "free_tier"
        assert "lmstudio" not in inv["usable"]       # not authenticated
        assert "anthropic" in inv["usable"]
        assert gw.health()["state"] == "READY"
    finally:
        gw.close()


def test_preferred_tier_is_honoured(tmp_path, clean_env):
    gw = make(tmp_path)
    try:
        res = gw.infer(request(preferred_quality_tier=mg.TIER_DEEP))
        assert (res.provider, res.model) == ("openai-codex", "fake-deep")
        assert res.route_kind == "subscription"
    finally:
        gw.close()


# -- FR-077 budget escalation ---------------------------------------------


def test_over_budget_context_is_refused_without_escalation(tmp_path, clean_env):
    gw = make(tmp_path)
    try:
        big = "x" * (mg.budget_for(mg.TRIVIAL).max_input_tokens * 4 + 400)
        with pytest.raises(mg.BudgetExceeded):
            gw.infer(request(task_class=mg.TRIVIAL, text=big))
        rows = gw.telemetry.for_objective("obj-1")
        assert rows[-1]["status"] == "refused" and rows[-1]["error"] == "budget"
        res = gw.infer(request(task_class=mg.TRIVIAL, text=big, escalate=True))
        assert res.status == "ok"
    finally:
        gw.close()


# -- FR-078 growth guard --------------------------------------------------


def test_growth_guard_stops_geometric_context_growth():
    guard = mg.GrowthGuard(growth_factor=1.5, growth_streak=3)
    sizes = [100, 200, 400]
    for i, size in enumerate(sizes):
        v = guard.check("o", input_tokens=size, ceiling=10**9, fingerprint=f"f{i}")
        if i < 2:
            assert v.allowed
            guard.record("o", input_tokens=size, output_tokens=10, fingerprint=f"f{i}")
        else:
            assert not v.allowed and "geometrically" in v.reason


def test_growth_guard_stops_repeated_identical_context():
    guard = mg.GrowthGuard(max_repeats=2)
    for _ in range(2):
        assert guard.check("o", input_tokens=50, ceiling=0, fingerprint="same").allowed
        guard.record("o", input_tokens=50, output_tokens=5, fingerprint="same")
    v = guard.check("o", input_tokens=50, ceiling=0, fingerprint="same")
    assert not v.allowed and "identical" in v.reason


def test_growth_guard_enforces_objective_ceiling():
    guard = mg.GrowthGuard()
    guard.record("o", input_tokens=900, output_tokens=50, fingerprint="a")
    v = guard.check("o", input_tokens=100, ceiling=1000, fingerprint="b")
    assert not v.allowed and "ceiling" in v.reason
    guard.forget("o")
    assert guard.check("o", input_tokens=100, ceiling=1000, fingerprint="b").allowed


def test_gateway_refuses_before_budget_exhaustion(tmp_path, clean_env):
    """FR-078 acceptance: the synthetic runaway trips the guard BEFORE the
    objective ceiling is spent, and nothing reaches the provider."""
    gw = make(tmp_path)
    gw.guard = mg.GrowthGuard(growth_factor=1.5, growth_streak=3)
    try:
        ceiling = mg.budget_for(mg.STANDARD).objective_ceiling
        for n in (400, 800):
            assert gw.infer(request(task_class=mg.STANDARD, text="y" * n)).status == "ok"
        with pytest.raises(mg.GrowthStopped):
            gw.infer(request(task_class=mg.STANDARD, text="y" * 1600))
        assert gw.guard.spent("obj-1") < ceiling
        rows = gw.telemetry.for_objective("obj-1")
        assert rows[-1]["status"] == "refused" and rows[-1]["error"].startswith("growth:")
        assert sum(1 for r in rows if r["status"] == "ok") == 2
    finally:
        gw.close()


# -- FR-081 failover ------------------------------------------------------


def test_failover_is_bounded_and_keeps_the_objective(tmp_path, clean_env):
    clean_env.setenv("FAKE_GW_FAIL_PROVIDERS", "anthropic:QUOTA_EXCEEDED")
    gw = make(tmp_path)
    try:
        res = gw.infer(request(objective="obj-fo"))
        assert res.status == "ok"
        assert res.provider == "openai-codex"        # deep tier took over
        assert res.failover_count >= 1
        assert res.attempts and res.attempts[0]["code"] == "QUOTA_EXCEEDED"
        rows = gw.telemetry.for_objective("obj-fo")
        statuses = [r["status"] for r in rows]
        assert statuses.count("failed") >= 1 and statuses[-1] == "ok"
        assert all(r["objective_id"] == "obj-fo" for r in rows)
        # The failed route is remembered as unhealthy: the next call skips it
        # immediately (no duplicate context sent to the dead route).
        res2 = gw.infer(request(objective="obj-fo"))
        assert res2.failover_count == 0 and res2.provider == "openai-codex"
    finally:
        gw.close()


def test_failover_disabled_reports_entitlement_truthfully(tmp_path, clean_env):
    clean_env.setenv("FAKE_GW_FAIL_PROVIDERS", "anthropic:AUTH_FAILED")
    gw = make(tmp_path)
    try:
        res = gw.infer(request(allow_failover=False))
        assert res.status == "failed"
        assert res.entitlement_state == "AUTH_FAILED"
        assert res.attempts and len(res.attempts) == 1
    finally:
        gw.close()


def test_all_routes_failing_is_a_failed_result_not_an_exception(tmp_path, clean_env):
    clean_env.setenv("FAKE_GW_FAIL_PROVIDERS",
                     "anthropic:MODEL_UNAVAILABLE,openai-codex:RATE_LIMITED")
    gw = make(tmp_path)
    try:
        res = gw.infer(request())
        assert res.status == "failed"
        assert res.entitlement_state in ("MODEL_UNAVAILABLE", "RATE_LIMITED")
        # Bounded: 1 + max_failover attempts, never more, across 3 routes.
        assert len(res.attempts) == 1 + gw.max_failover == 3
        assert res.failover_count == 2
    finally:
        gw.close()


def test_denylist_and_allowlist_filter_routes(tmp_path, clean_env):
    gw = make(tmp_path)
    try:
        res = gw.infer(request(provider_denylist=("anthropic",)))
        assert res.provider == "openai-codex"
        res = gw.infer(request(provider_allowlist=("nobody",)))
        assert res.status == "failed" and res.entitlement_state == "NO_ROUTE"
    finally:
        gw.close()


def test_local_only_privacy_policy_refuses_cloud_routes(tmp_path, clean_env):
    """FR-074: no cloud route is ever relabelled local."""
    gw = make(tmp_path)
    try:
        res = gw.infer(request(privacy_policy="local_only"))
        assert res.status == "failed" and res.entitlement_state == "NO_ROUTE"
    finally:
        gw.close()


# -- worker lifecycle -----------------------------------------------------


def test_worker_crash_is_reported_and_recovers(tmp_path, clean_env):
    clean_env.setenv("FAKE_GW_DIE", "1")
    gw = make(tmp_path, max_failover=0)
    try:
        res = gw.infer(request(allow_failover=False))
        assert res.status == "failed"
        assert res.entitlement_state == "GATEWAY_UNAVAILABLE"
        assert not gw.worker.alive()
        clean_env.delenv("FAKE_GW_DIE")
        # A fresh start recovers; the objective id is unchanged.
        res = gw.infer(request(allow_failover=False))
        assert res.status == "ok"
    finally:
        gw.close()


def test_hung_worker_trips_the_watchdog(tmp_path, clean_env):
    clean_env.setenv("FAKE_GW_HANG", "1")
    gw = make(tmp_path, max_failover=0)
    try:
        res = gw.infer(request(timeout_s=1.0, allow_failover=False))
        assert res.status == "failed"
        assert res.entitlement_state == "GATEWAY_UNAVAILABLE"
        assert "within" in res.warnings[0]
    finally:
        gw.close()


def test_telemetry_summary_and_spikes(tmp_path, clean_env):
    gw = make(tmp_path)
    try:
        gw.infer(request(objective="a", text="short"))
        gw.infer(request(objective="a", text="x" * 2000, worker="hermes"))
        summary = gw.telemetry.summary("a")
        assert summary["calls"] == 2
        assert set(summary["by_worker"]) == {"friday", "hermes"}
        top = gw.telemetry.spikes(limit=1)[0]
        assert top["worker"] == "hermes"          # the big one
    finally:
        gw.close()


# -- live -----------------------------------------------------------------


@pytest.mark.live
def test_live_hermes_gateway_answers_without_an_agent_loop():
    """Against the real Hermes install and the friday profile. Proves the
    substrate: a provider answers, usage is reported, no session exists."""
    from friday import hermes_bridge as hb
    if not hb.locate():
        pytest.skip("Hermes not installed here")
    gw = mg.ModelGateway(telemetry=mg.GatewayTelemetry(
        Path(__file__).parent.parent / "data" / "gateway_calls_live_test.sqlite3"))
    try:
        health = gw.health()
        assert health["state"] in ("READY", "AUTH_REQUIRED"), health
        if health["state"] != "READY":
            pytest.skip("no authenticated provider")
        res = gw.infer(mg.ModelGatewayRequest(
            objective_id="live-test", task_class=mg.TRIVIAL,
            context_package=[{"role": "user", "content": "Reply with exactly the word PONG"}],
            preferred_quality_tier=mg.TIER_FAST, temperature=0, timeout_s=60))
        assert res.status == "ok", res
        assert "PONG" in res.response.upper()
        assert res.input_tokens > 0
        assert res.boundary == "upstream_cloud"
    finally:
        gw.close()
