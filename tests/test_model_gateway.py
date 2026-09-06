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
from friday import provider_health as PH

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


# -- Requirement 10: provider-specific default model ----------------------


def test_a_pinned_provider_outside_the_tier_table_gets_its_own_default(tmp_path, clean_env):
    """The tier table names anthropic only; pinning openai-codex must route
    to it at ITS catalog default - never at "" (which Hermes would fill with
    the profile's main model: openai-api was asked for claude-opus-5 that
    way, 2026-09-05) and never at the main model by name."""
    gw = make(tmp_path, tiers={
        mg.TIER_FAST: ("anthropic", "fake-fast"),
        mg.TIER_STANDARD: ("anthropic", "fake-standard"),
        mg.TIER_DEEP: ("anthropic", "fake-deep")})
    try:
        routes = gw.candidates(request(provider_allowlist=("openai-codex",)))
        assert routes == [(mg.TIER_FAST, "openai-codex", "fake-codex-default")], routes
        res = gw.infer(request(provider_allowlist=("openai-codex",), allow_failover=False))
        assert res.status == "ok"
        assert (res.provider, res.model) == ("openai-codex", "fake-codex-default")
        assert res.model != "fake-main", "the main provider's model leaked to another provider"
        assert gw.default_model("openai-codex") == "fake-codex-default"
    finally:
        gw.close()


def test_a_provider_with_no_catalog_default_is_no_route(tmp_path, clean_env):
    """opencode-free is authenticated but Hermes knows no default model for
    it. Guessing is exactly the failure Requirement 10 forbids: NO_ROUTE."""
    gw = make(tmp_path)
    try:
        assert gw.default_model("opencode-free") == ""
        assert gw.candidates(request(provider_allowlist=("opencode-free",))) == []
        res = gw.infer(request(provider_allowlist=("opencode-free",)))
        assert res.status == "failed" and res.entitlement_state == "NO_ROUTE"
        assert res.attempts == [], "nothing was sent to a provider with no model"
    finally:
        gw.close()


# -- Requirement 9/11: empty content is never "ok" ------------------------


def test_a_reply_truncated_before_any_content_is_retried_wider_then_ok(tmp_path, clean_env):
    """gemini-3.6-flash at max_tokens=16: finish_reason=length, 0 completion
    tokens, 13 reasoning tokens, empty content - and the gateway said ok.
    Now: one widened retry, the first attempt recorded as OUTPUT_TRUNCATED."""
    clean_env.setenv("FAKE_GW_EMPTY_PROVIDERS", "anthropic:length")
    clean_env.setenv("FAKE_GW_EMPTY_BELOW", "300")      # TRIVIAL's 256 is below it
    gw = make(tmp_path)
    try:
        res = gw.infer(request(objective="obj-trunc", task_class=mg.TRIVIAL, allow_failover=False))
        assert res.status == "ok" and res.response == "PONG", res
        assert res.provider == "anthropic"
        assert [a["code"] for a in res.attempts] == ["OUTPUT_TRUNCATED"], res.attempts
        assert "reasoning" in res.attempts[0]["error"]
        rows = gw.telemetry.for_objective("obj-trunc")
        assert [r["status"] for r in rows] == ["failed", "ok"]
        assert rows[0]["entitlement_state"] == "OUTPUT_TRUNCATED"
    finally:
        gw.close()


def test_an_empty_reply_with_finish_stop_fails_the_route_and_fails_over(tmp_path, clean_env):
    clean_env.setenv("FAKE_GW_EMPTY_PROVIDERS", "anthropic:stop")
    gw = make(tmp_path)
    try:
        res = gw.infer(request(objective="obj-empty"))
        assert res.status == "ok"
        assert res.provider == "openai-codex", "the empty route must be failed over"
        assert res.attempts[0]["code"] == "EMPTY_RESPONSE", res.attempts
        assert res.failover_count >= 1
        # the route is remembered as unhealthy, like any other failure
        res2 = gw.infer(request(objective="obj-empty"))
        assert res2.provider == "openai-codex" and res2.failover_count == 0
    finally:
        gw.close()


def test_an_empty_reply_without_failover_is_a_failed_result(tmp_path, clean_env):
    clean_env.setenv("FAKE_GW_EMPTY_PROVIDERS", "anthropic:stop")
    gw = make(tmp_path)
    try:
        res = gw.infer(request(allow_failover=False))
        assert res.status == "failed"
        assert res.entitlement_state == "EMPTY_RESPONSE"
        assert res.response == ""
        assert "no visible content" in res.warnings[0]
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


def test_growth_guard_memory_is_bounded_per_objective_and_across_objectives():
    """A-051 finding: the guard kept every input size of every objective
    ever seen. A control plane runs for weeks; RSS crept with it."""
    guard = mg.GrowthGuard(growth_streak=3, max_objectives=10)
    for i in range(50):
        oid = f"RUN-{i}"
        for n in range(20):
            guard.record(oid, input_tokens=100 + n, output_tokens=10, fingerprint=f"fp{n}")
    assert len(guard._spent) <= 10 and len(guard._sizes) <= 10 and len(guard._fingerprints) <= 10
    assert all(len(v) <= 3 for v in guard._sizes.values())
    # The newest objective is still fully tracked and the verdicts still work.
    assert guard.spent("RUN-49") == 20 * 10 + sum(100 + n for n in range(20))
    v = guard.check("RUN-49", input_tokens=10 ** 9, ceiling=5000, fingerprint="new")
    assert not v.allowed and "ceiling" in v.reason


def test_the_gateway_does_not_start_a_thread_per_request(tmp_path):
    """A-051, found by py-spy on a live stall.

    `_request` used to create a `gateway-read` thread per call, with the
    watchdog on the line AFTER `t.start()`. Under memory pressure (253 MB
    free of 16 GB, observed 2026-09-06) Windows could not allocate the new
    thread's stack, `Thread.start()` blocked on its `_started` event, and no
    timeout applied - one soak cycle "took" 3 h 22 m and the gate reported
    INCONCLUSIVE.

    Assert the RESOURCE, not the clock: thread count must not scale with
    request count. A wall-clock assertion passes against the bad version on
    any machine with memory to spare, which is every machine until it isn't.
    """
    import threading
    from friday import model_gateway as M

    # Count thread CREATIONS, not live threads: the old per-request threads
    # were daemons that finished in microseconds, so `active_count()` recovers
    # between calls and reads flat against the very bug this guards. What
    # cannot recover is the number of times `Thread.start()` was called - and
    # that call is what blocks when the OS cannot allocate a stack.
    started: list[str] = []
    real_start = threading.Thread.start

    def counting_start(self, *a, **kw):
        started.append(self.name)
        return real_start(self, *a, **kw)

    worker = M.ModelGatewayWorker(
        command=[sys.executable, str(Path("tests") / "fake_model_gateway_worker.py")])
    threading.Thread.start = counting_start
    try:
        worker.start()
        at_start = len(started)
        for _ in range(40):
            reply = worker._request("providers", {}, timeout=10.0)
            assert reply.get("ok"), reply
        during = len(started) - at_start
        assert during == 0, (
            f"{during} threads were started across 40 requests: the request "
            f"path creates threads again, so a request can block on thread "
            f"creation with no timeout covering it")
        assert at_start <= 2, (
            f"start() created {at_start} threads; one reader per worker is "
            f"the contract")
    finally:
        threading.Thread.start = real_start
        worker.stop()


def test_a_wedged_worker_still_times_out_without_the_per_request_thread():
    """The negative case. Removing the per-request thread must not remove the
    backstop: a worker that never answers still has to raise, not hang."""
    from friday import model_gateway as M

    worker = M.ModelGatewayWorker(
        command=[sys.executable, "-c",
                 "import sys,time\n"
                 "sys.stdin.readline()\n"      # take the request, answer never
                 "time.sleep(60)\n"])
    # start() itself does a `hello`, which is the request that must time out.
    with pytest.raises(M.GatewayUnavailable) as caught:
        worker.START_TIMEOUT = 1.0
        worker.start()
    assert "did not answer" in str(caught.value)
    worker.stop()


def test_failover_skips_a_dead_route_not_the_whole_provider(tmp_path, clean_env):
    """A-018/A-019 wired into ROUTING, not merely available as a helper.

    The failover branch offers each authenticated provider at its own catalog
    default. It used the PROVIDER verdict to decide whether to try, so one
    unsupported model condemned every model on that provider - the
    `opencode-free` shape from the live suite.

    Reaching that branch takes care: it is skipped for providers the tier
    table named (`tabled`), and an empty tier table still emits a blank route
    that fills `out`. So the table names a provider that is then denylisted -
    every table route is filtered, `out` is empty, and anthropic/openai-codex
    arrive from the failover branch where health is consulted.
    """
    tiers = {mg.TIER_FAST: ("lmstudio", "fake-local"),
             mg.TIER_STANDARD: ("lmstudio", "fake-local"),
             mg.TIER_DEEP: ("lmstudio", "fake-local")}

    def build(name):
        return mg.ModelGateway(
            worker=mg.ModelGatewayWorker(command=[sys.executable, FAKE], profile=""),
            telemetry=mg.GatewayTelemetry(tmp_path / name),
            tier_table=tiers, max_failover=3)

    req = request(allow_failover=True, provider_denylist=("lmstudio",))

    clean = build("clean.sqlite3")
    before = {(p, m) for _, p, m in clean.candidates(req)}
    assert ("anthropic", "fake-haiku") in before, before
    assert ("openai-codex", "fake-codex-default") in before, before

    dead = build("dead.sqlite3")
    cred = dead.credential_label("")
    # The distinguishing shape. A dead route on openai-codex's DEFAULT model,
    # followed by a healthy row for a DIFFERENT model on the same provider.
    # Keyed per route, the default is skipped. Keyed per provider, the newest
    # row (the healthy one) is the whole provider's verdict and the dead
    # default is offered anyway - which is the defect. One failure alone
    # cannot tell the two keyings apart, because then both call the provider
    # unavailable.
    dead.telemetry.record(objective_id="x", worker="w", task_class="TRIVIAL",
                          provider="openai-codex", model="fake-codex-default",
                          credential=cred, status="failed",
                          entitlement_state="MODEL_UNAVAILABLE",
                          error="MODEL_UNAVAILABLE: no such model",
                          output_tokens=0)
    dead.telemetry.record(objective_id="x", worker="w", task_class="TRIVIAL",
                          provider="openai-codex", model="fake-codex-other",
                          credential=cred, status="ok", output_tokens=9)
    dead.telemetry.record(objective_id="x", worker="w", task_class="TRIVIAL",
                          provider="anthropic", model="fake-haiku",
                          credential=cred, status="failed",
                          entitlement_state="MODEL_UNAVAILABLE",
                          error="MODEL_UNAVAILABLE: no such model",
                          output_tokens=0)
    verdict = PH.assess_routes(dead.telemetry)[
        PH.route_key("anthropic", "fake-haiku", cred)]
    assert verdict.state == PH.UNAVAILABLE, verdict

    after = {(p, m) for _, p, m in dead.candidates(req)}
    assert ("anthropic", "fake-haiku") not in after, (
        f"a route whose last evidence is MODEL_UNAVAILABLE was still offered: {after}")
    # The load-bearing assertion: openai-codex's newest row is a SUCCESS on a
    # different model, so the provider reads healthy - but its default model
    # is dead and must not be offered.
    assert ("openai-codex", "fake-codex-default") not in after, (
        "a dead route was offered because a sibling model on the same provider "
        f"succeeded - routing is keyed by provider, not by route: {after}")


# ---------------------------------------------------------------------------
# A-010: the transport model is a property of the ROUTE, and a `local` claim
# has to be true.
#
# `route_kind` came from a hardcoded provider-id list, and `custom`
# ("Custom endpoint") was on the LOCAL list. Its base_url is whatever the user
# configured - on this machine `hermes_cli` reports it pointing at
# https://opencode.ai/zen/v1. `ModelGateway.candidates()` uses that label to
# honour `privacy_policy="local_only"`, so a request that promised to stay on
# the machine was routed to a public endpoint. Probed before the fix:
#
#   route_kind('custom')       : local
#   candidates under local_only: [('fast', 'custom', 'some-remote-model')]
#   LEAK: a 'local_only' request is routed to https://opencode.ai/zen/v1
# ---------------------------------------------------------------------------


class TestTheTransportModel:

    def test_a_custom_provider_pointed_at_the_internet_is_not_local(self):
        from friday import hermes_model_gateway_worker as W
        kind = W._route_kind_for({"id": "custom",
                                  "base_url": "https://opencode.ai/zen/v1"})
        assert kind == "api", (
            "a user-configured endpoint on a public host was classified "
            f"{kind!r}; local_only would route private context to it")

    def test_a_custom_provider_on_loopback_is_local(self):
        """The negative case: a gate that refuses everything is not a fix."""
        from friday import hermes_model_gateway_worker as W
        for url in ("http://127.0.0.1:1234/v1", "http://localhost:1234/v1",
                    "http://[::1]:1234/v1", "http://box.local:11434"):
            assert W._route_kind_for({"id": "custom", "base_url": url}) == "local", url

    def test_an_unknown_endpoint_is_not_assumed_local(self):
        """A provider that never says where it points has not PROVEN locality.
        For a privacy promise the burden of proof runs the other way."""
        from friday import hermes_model_gateway_worker as W
        assert W._route_kind_for({"id": "custom"}) == "api"
        assert W._route_kind_for({"id": "custom", "base_url": ""}) == "api"

    def test_a_lan_address_is_not_this_machine(self):
        """`local_only` means this machine, not this network: another host on
        the LAN is still somewhere the data left for."""
        from friday import hermes_model_gateway_worker as W
        assert W._route_kind_for(
            {"id": "ollama", "base_url": "http://192.168.1.50:11434"}) == "api"

    def test_the_other_transports_still_classify(self):
        from friday import hermes_model_gateway_worker as W
        assert W._route_kind_for({"id": "openai-codex"}) == "subscription"
        assert W._route_kind_for({"id": "copilot"}) == "subscription"
        assert W._route_kind_for({"id": "opencode-free"}) == "free_tier"
        assert W._route_kind_for({"id": "anthropic"}) == "api"
        assert W._route_kind_for({"id": "some-new-provider"}) == "api"

    def test_local_only_refuses_a_remote_custom_route(self, tmp_path, clean_env):
        """The end-to-end shape: routing must not offer the remote endpoint."""
        from friday import hermes_model_gateway_worker as W

        class RemoteCustom(mg.ModelGatewayWorker):
            def __init__(self):
                super().__init__(command=[sys.executable, FAKE], profile="")

            def call(self, method, params=None, timeout=30.0):
                reply = super().call(method, params, timeout=timeout)
                if method == "providers" and reply.get("ok"):
                    prov = {"id": "custom", "label": "Custom endpoint",
                            "aliases": [], "authenticated": True,
                            "base_url": "https://opencode.ai/zen/v1",
                            "default_model": "some-remote-model"}
                    prov["route_kind"] = W._route_kind_for(prov)
                    reply["result"]["providers"] = [prov]
                return reply

        gw = mg.ModelGateway(worker=RemoteCustom(),
                             telemetry=mg.GatewayTelemetry(tmp_path / "g.sqlite3"),
                             tier_table={}, max_failover=3)
        picks = gw.candidates(request(privacy_policy="local_only", allow_failover=True))
        assert not [p for _, p, _ in picks if p == "custom"], (
            f"local_only routed to a public endpoint: {picks}")
