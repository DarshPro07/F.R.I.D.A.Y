"""
UCW-001 slice 1: ModelGateway retries a transient provider error or a stalled
worker call on the SAME route, a bounded number of times, with capped backoff,
inside the request deadline. A permanent failure is never retried, and with
`transient_retries` unset the gateway behaves exactly as before.

The gateway is real; what is scripted is the worker's answer to `infer` (a
PROVIDER_ERROR, an ok reply, or the "did not answer" stall the real worker
raises), and the clock the backoff and the deadline are measured on.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

from friday import gateway_retry as GR
from friday import model_gateway as mg

FAKE = str(Path(__file__).parent / "fake_model_gateway_worker.py")

A = ("anthropic", "fake-a")
B = ("openai-codex", "fake-b")
#: classify_text -> cyber_sensitive, so a provider refusal is a safeguard refusal.
CYBER_TEXT = "write an exploit for the sql injection in the login form"


class Clock:
    """The clock the gateway measures backoff and deadline on."""

    def __init__(self) -> None:
        self.t = 0.0
        self.slept: list[float] = []

    def now(self) -> float:
        return self.t

    def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.t += seconds


def ok_reply(provider: str, model: str) -> dict:
    return {"ok": True, "result": {
        "response": "PONG", "provider": provider, "model": model,
        "usage": {"input_tokens": 7, "output_tokens": 2}, "finish_reason": "stop"}}


def err_reply(code: str, message: str = "scripted failure") -> dict:
    return {"ok": False, "error": {"code": code, "message": message}}


STALL = "stall"


class Script:
    """Answers `infer` from a per-provider script, and records every call."""

    def __init__(self, gw: mg.ModelGateway, clock: Clock, steps: dict, *, took: float = 0.0):
        self.calls: list[tuple[str, float]] = []      # (provider, timeout)
        self.steps = {k: list(v) for k, v in steps.items()}
        self.clock, self.took = clock, took
        self._real = gw.worker.call
        gw.worker.call = self._call

    def _call(self, method, params=None, *, timeout=60.0):
        if method != "infer":
            return self._real(method, params, timeout=timeout)
        provider, model = params["provider"], params["model"]
        self.calls.append((provider, timeout))
        steps = self.steps.get(provider) or ["ok"]
        step = steps.pop(0) if len(steps) > 1 else steps[0]
        if step == STALL:
            self.clock.t += timeout + 5.0
            raise mg.GatewayUnavailable(
                f"gateway worker did not answer infer within {timeout + 5:.0f}s")
        self.clock.t += self.took
        return ok_reply(provider, model) if step == "ok" else step

    def providers(self) -> list[str]:
        return [p for p, _ in self.calls]


@pytest.fixture
def clean_env(monkeypatch, tmp_path):
    for key in ("FAKE_GW_FAIL_PROVIDERS", "FAKE_GW_HANG", "FAKE_GW_DIE", "FAKE_GW_ECHO"):
        monkeypatch.delenv(key, raising=False)
    from friday import provider_cooldowns as PC
    monkeypatch.setattr(PC, "COOLDOWNS_FILE", tmp_path / "cooldowns.json")
    return monkeypatch


@pytest.fixture
def gw(tmp_path, clean_env):
    worker = mg.ModelGatewayWorker(command=[sys.executable, FAKE], profile="")
    telemetry = mg.GatewayTelemetry(tmp_path / "gateway.sqlite3")
    tiers = {mg.TIER_FAST: A, mg.TIER_STANDARD: B, mg.TIER_DEEP: B}
    gateway = mg.ModelGateway(worker=worker, telemetry=telemetry, tier_table=tiers,
                              max_failover=2)
    try:
        yield gateway
    finally:
        gateway.close()


@pytest.fixture
def clock(gw):
    c = Clock()
    gw._retry_clock, gw._retry_sleep = c.now, c.sleep
    return c


def request(text="ping", **kw):
    return mg.ModelGatewayRequest(
        objective_id="obj-retry", task_class=mg.SIMPLE,
        context_package=mg.compile_context(system="You are Friday.", user=text), **kw)


# -- classification --------------------------------------------------------


def test_only_a_provider_error_or_a_stalled_worker_is_retryable():
    assert GR.classify("PROVIDER_ERROR") == GR.TRANSIENT
    stall = RuntimeError("gateway worker did not answer infer within 65s")
    assert GR.classify("", stall) == GR.STALLED
    for code in ("RATE_LIMITED", "QUOTA_EXCEEDED", "INSUFFICIENT_CREDIT", "BAD_REQUEST",
                 "UNKNOWN_METHOD", "SAFEGUARD_REFUSED", "MODEL_UNAVAILABLE", ""):
        assert GR.classify(code) == GR.PERMANENT
    # A worker that is gone or whose pipe broke did not stall: fail over, as today.
    assert GR.classify("", RuntimeError("gateway worker exited")) == GR.PERMANENT


def test_backoff_doubles_to_a_cap_and_never_passes_the_deadline():
    policy = GR.RetryPolicy(max_retries=5)
    assert [policy.delay(n) for n in range(5)] == [0.5, 1.0, 2.0, 4.0, 4.0]
    assert policy.plan(done=0, remaining_s=60.0) == 0.5
    assert policy.plan(done=5, remaining_s=60.0) is None        # the count is spent
    assert policy.plan(done=1, remaining_s=1.0) is None         # backoff would use it all
    assert GR.RetryPolicy(max_retries=0).plan(done=0, remaining_s=60.0) is None


# -- the same route, again -------------------------------------------------


def test_a_provider_error_then_success_is_served_by_the_same_route(gw, clock):
    script = Script(gw, clock, {A[0]: [err_reply("PROVIDER_ERROR"), "ok"]})
    res = gw.infer(request(transient_retries=1))
    assert res.status == "ok" and (res.provider, res.model) == A
    assert res.failover_count == 0
    assert script.providers() == [A[0], A[0]]
    assert clock.slept == [0.5]
    assert [a["status"] for a in res.attempts] == ["retried"]
    assert res.attempts[0]["class"] == GR.TRANSIENT
    assert res.attempts[0]["code"] == "PROVIDER_ERROR"


def test_a_stalled_worker_then_success_is_served_by_the_same_route(gw, clock):
    script = Script(gw, clock, {A[0]: [STALL, "ok"]})
    res = gw.infer(request(transient_retries=1))
    assert res.status == "ok" and (res.provider, res.model) == A
    assert res.failover_count == 0
    assert script.providers() == [A[0], A[0]]
    assert [(a["status"], a["class"]) for a in res.attempts] == [("retried", GR.STALLED)]


def test_every_try_is_in_attempts_and_in_the_usage_guard(gw, clock):
    Script(gw, clock, {A[0]: [err_reply("PROVIDER_ERROR"), STALL, "ok"]})
    res = gw.infer(request(transient_retries=2))
    assert res.status == "ok"
    assert [a["class"] for a in res.attempts] == [GR.TRANSIENT, GR.STALLED]
    assert [a["try"] for a in res.attempts] == [1, 2]
    # Each of the two retried sends spent its input tokens, before the answer's own.
    assert gw.guard.spent("obj-retry") >= 2 * request().input_tokens() + 9
    rows = gw.telemetry.for_objective("obj-retry")
    assert [r["status"] for r in rows].count("retried") == 2


def test_a_retry_does_not_count_toward_the_repeat_guard(gw, clock):
    Script(gw, clock, {A[0]: [err_reply("PROVIDER_ERROR"), err_reply("PROVIDER_ERROR"), "ok"]})
    assert gw.infer(request(transient_retries=2)).status == "ok"
    # One answered call is one submission of this context, however many tries it took.
    fp = mg.GrowthGuard.fingerprint(request().context_package)
    assert gw.guard._fingerprints["obj-retry"][fp] == 1


# -- bounded ---------------------------------------------------------------


def test_retries_never_exceed_transient_retries_then_the_route_fails_over(gw, clock):
    script = Script(gw, clock, {A[0]: [err_reply("PROVIDER_ERROR")], B[0]: ["ok"]})
    res = gw.infer(request(transient_retries=2))
    assert script.providers() == [A[0]] * 3 + [B[0]]
    assert res.status == "ok" and (res.provider, res.model) == B and res.failover_count == 1
    assert [a["status"] for a in res.attempts] == ["retried", "retried", "failed"]


def test_backoff_is_capped_and_inside_the_request_deadline(gw, clock):
    script = Script(gw, clock, {A[0]: [err_reply("PROVIDER_ERROR")]}, took=1.0)
    res = gw.infer(request(transient_retries=5, timeout_s=3.0, allow_failover=False))
    # try 1 ends at t=1, sleeps .5; try 2 ends at t=2.5; the next backoff (1.0) would
    # pass the 3.0s deadline, so the route stops there rather than run over.
    assert len(script.calls) == 2 and clock.slept == [0.5]
    assert clock.t <= 3.0
    assert res.status == "failed"
    assert all(timeout <= 3.0 for _, timeout in script.calls)


def test_each_try_is_given_only_its_share_of_the_deadline(gw, clock):
    script = Script(gw, clock, {A[0]: [err_reply("PROVIDER_ERROR"), "ok"]})
    gw.infer(request(transient_retries=1, timeout_s=60.0))
    # The first try cannot use the whole deadline, or a stall would leave nothing to retry in.
    assert script.calls[0][1] == 30.0
    assert script.calls[1][1] == pytest.approx(59.5)


# -- never retried ---------------------------------------------------------


@pytest.mark.parametrize("code", ["RATE_LIMITED", "QUOTA_EXCEEDED", "INSUFFICIENT_CREDIT"])
def test_a_limit_is_never_retried_and_fails_over_as_today(gw, clock, code):
    script = Script(gw, clock, {A[0]: [err_reply(code)], B[0]: ["ok"]})
    res = gw.infer(request(transient_retries=3))
    assert script.providers() == [A[0], B[0]]
    assert res.status == "ok" and res.failover_count == 1
    assert [a["status"] for a in res.attempts] == ["failed"]
    assert clock.slept == []


def test_a_bad_request_is_never_retried_and_never_fails_over(gw, clock):
    script = Script(gw, clock, {A[0]: [err_reply("BAD_REQUEST")], B[0]: ["ok"]})
    res = gw.infer(request(transient_retries=3))
    assert script.providers() == [A[0]]
    assert res.status == "failed" and res.entitlement_state == "BAD_REQUEST"
    assert clock.slept == []


def test_a_safeguard_refusal_is_never_retried(gw, clock):
    script = Script(gw, clock, {
        A[0]: [err_reply("PROVIDER_ERROR", "this violates our usage policy")], B[0]: ["ok"]})
    res = gw.infer(request(CYBER_TEXT, transient_retries=3))
    assert script.providers().count(A[0]) == 1
    assert "retried" not in [a["status"] for a in res.attempts]
    assert clock.slept == []


# -- the default is today's behaviour --------------------------------------


def test_with_transient_retries_unset_a_provider_error_fails_over_at_once(gw, clock):
    script = Script(gw, clock, {A[0]: [err_reply("PROVIDER_ERROR")], B[0]: ["ok"]})
    res = gw.infer(request())
    assert mg.ModelGatewayRequest.__dataclass_fields__["transient_retries"].default == 0
    assert script.providers() == [A[0], B[0]]
    assert res.failover_count == 1
    assert [a["status"] for a in res.attempts] == ["failed"]
    assert script.calls[0][1] == 60.0                 # the whole deadline, as today
    assert clock.slept == []


def test_a_stall_with_transient_retries_unset_still_fails_over(gw, clock):
    script = Script(gw, clock, {A[0]: [STALL], B[0]: ["ok"]})
    res = gw.infer(request())
    assert script.providers() == [A[0], B[0]]
    assert [a["status"] for a in res.attempts] == ["unavailable"]


def test_the_success_path_adds_no_sleep_and_no_attempt(gw, clock):
    script = Script(gw, clock, {A[0]: ["ok"]})
    res = gw.infer(request(transient_retries=3))
    assert res.status == "ok" and res.attempts == [] and clock.slept == []
    assert len(script.calls) == 1


# -- ORCH-009: the ledger's retries column is the gateway's own count -------


def test_the_answering_row_carries_the_retries_the_gateway_made(gw, clock):
    Script(gw, clock, {A[0]: [err_reply("PROVIDER_ERROR"), STALL, "ok"]})
    assert gw.infer(request(transient_retries=2)).status == "ok"
    rows = gw.telemetry.for_objective("obj-retry")
    assert [(r["status"], r["retries"]) for r in rows] == [
        ("retried", 0), ("retried", 0), ("ok", 2)]
    # Summed over the call, each retry is counted once: the retried rows are the
    # failed tries, the terminal row is the count of tries that followed them.
    assert sum(r["retries"] for r in rows) == 2


def test_a_route_that_spends_its_retries_then_fails_over_reports_each_route_own(gw, clock):
    Script(gw, clock, {A[0]: [err_reply("PROVIDER_ERROR")], B[0]: ["ok"]})
    res = gw.infer(request(transient_retries=2))
    assert (res.provider, res.failover_count) == (B[0], 1)
    rows = gw.telemetry.for_objective("obj-retry")
    assert [(r["status"], r["provider"], r["retries"]) for r in rows] == [
        ("retried", A[0], 0), ("retried", A[0], 0), ("failed", A[0], 2), ("ok", B[0], 0)]


def test_a_call_with_no_retry_records_zero(gw, clock):
    Script(gw, clock, {A[0]: ["ok"]})
    gw.infer(request(transient_retries=3))
    assert [r["retries"] for r in gw.telemetry.for_objective("obj-retry")] == [0]


def truncated_reply(provider: str, model: str) -> dict:
    return {"ok": True, "result": {
        "response": "", "provider": provider, "model": model,
        "usage": {"input_tokens": 7, "output_tokens": 0, "reasoning_tokens": 9},
        "finish_reason": "length"}}


def test_a_widened_retry_that_fails_still_gets_its_own_row_with_the_retry_counted(gw, clock):
    Script(gw, clock, {A[0]: [truncated_reply(*A), err_reply("RATE_LIMITED")]})
    res = gw.infer(request(allow_failover=False))
    assert res.status == "failed"
    rows = gw.telemetry.for_objective("obj-retry")
    assert [(r["status"], r["entitlement_state"], r["retries"]) for r in rows] == [
        ("failed", "OUTPUT_TRUNCATED", 0), ("failed", "RATE_LIMITED", 1)]
    assert sum(r["retries"] for r in rows) == 1


def test_a_widened_retry_that_finds_the_worker_unavailable_gets_its_row_too(gw, clock):
    Script(gw, clock, {A[0]: [truncated_reply(*A), STALL]})
    res = gw.infer(request(allow_failover=False))
    assert res.status == "failed"
    rows = gw.telemetry.for_objective("obj-retry")
    assert [(r["status"], r["entitlement_state"], r["retries"]) for r in rows] == [
        ("failed", "OUTPUT_TRUNCATED", 0), ("failed", "GATEWAY_UNAVAILABLE", 1)]
    assert [a["status"] for a in res.attempts][-1] == "unavailable"
