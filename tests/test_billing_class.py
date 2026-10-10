"""
FAB-001: every model route carries one billing class, spend is reported per
class, and a Claude Code run whose environment holds an API key is reported
billable before it starts.

Owner 2026-10-08 (docs/plans/build-harness/00-owner-directive-2026-10-08b.md):
"never confuse subscription usage, free API usage, prepaid credits and
billable API calls", and "An API-key environment variable can also make Claude
Code use pay-as-you-go authentication instead of your subscription, so FRIDAY
must explicitly verify which route it is using."

The gateway tests run the real gateway against the scripted worker
(tests/fake_model_gateway_worker.py), as tests/test_model_gateway.py does.
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

from friday import model_gateway as mg
from friday.executors import cli
from friday.executors.claude_code import ClaudeCodeExecutor, TaskBundle
from friday.store import Store

FAKE = str(Path(__file__).parent / "fake_model_gateway_worker.py")

# Assembled at run time so the source carries no key-shaped literal.
SECRET = "sk-" + "ant-" + "never-print-this-value"


@pytest.fixture
def gw(tmp_path, monkeypatch):
    for key in ("FAKE_GW_FAIL_PROVIDERS", "FAKE_GW_HANG", "FAKE_GW_DIE", "FAKE_GW_ECHO",
                mg.PREPAID_ENV):
        monkeypatch.delenv(key, raising=False)
    from friday import provider_cooldowns as PC
    monkeypatch.setattr(PC, "COOLDOWNS_FILE", tmp_path / "cooldowns.json")
    worker = mg.ModelGatewayWorker(command=[sys.executable, FAKE], profile="")
    telemetry = mg.GatewayTelemetry(tmp_path / "gateway.sqlite3")
    tiers = {mg.TIER_FAST: ("anthropic", "fake-fast"),
             mg.TIER_STANDARD: ("anthropic", "fake-standard"),
             mg.TIER_DEEP: ("openai-codex", "fake-deep")}
    gateway = mg.ModelGateway(worker=worker, telemetry=telemetry, tier_table=tiers,
                              max_failover=2)
    yield gateway
    gateway.close()


def ask(objective, tier):
    return mg.ModelGatewayRequest(
        objective_id=objective, task_class=mg.SIMPLE, preferred_quality_tier=tier,
        context_package=mg.compile_context(system="You are Friday.", user="ping"))


# -- the class of a route ---------------------------------------------------


def test_the_five_classes_are_the_owners_five():
    assert set(mg.BILLING_CLASSES) == {
        "subscription_cli", "prepaid_api_credit", "free_tier", "billable_api", "local"}


@pytest.mark.parametrize("kind,expected", [
    ("cli", mg.SUBSCRIPTION_CLI),
    ("subscription", mg.SUBSCRIPTION_CLI),
    ("free_tier", mg.FREE_TIER),
    ("local", mg.LOCAL),
    ("api", mg.BILLABLE_API),
])
def test_a_route_kind_maps_to_exactly_one_class(kind, expected, monkeypatch):
    monkeypatch.delenv(mg.PREPAID_ENV, raising=False)
    assert mg.billing_class_for(kind, "anthropic") == expected


def test_an_unknown_route_kind_is_billable_never_free():
    """Under-reporting spend is the failure that costs money."""
    assert mg.billing_class_for("something-new", "x") == mg.BILLABLE_API


def test_an_api_route_is_prepaid_only_when_the_owner_says_so(monkeypatch):
    monkeypatch.setenv(mg.PREPAID_ENV, "anthropic, openrouter")
    assert mg.billing_class_for("api", "anthropic") == mg.PREPAID_API_CREDIT
    assert mg.billing_class_for("api", "openai") == mg.BILLABLE_API
    # Prepaid credit is a way of paying for an API; it never turns a free or
    # local route into a paid one.
    assert mg.billing_class_for("local", "anthropic") == mg.LOCAL


# -- spend reports separate the classes ----------------------------------------


def test_every_call_is_recorded_with_its_class_and_spend_is_split(gw):
    api = gw.infer(ask("obj-api", mg.TIER_FAST))
    sub = gw.infer(ask("obj-sub", mg.TIER_DEEP))
    assert (api.status, sub.status) == ("ok", "ok")
    assert api.billing_class == mg.BILLABLE_API
    assert sub.billing_class == mg.SUBSCRIPTION_CLI

    rows = {r["objective_id"]: r for r in gw.telemetry.recent(10)}
    assert rows["obj-api"]["billing_class"] == mg.BILLABLE_API
    assert rows["obj-sub"]["billing_class"] == mg.SUBSCRIPTION_CLI

    spend = gw.telemetry.spend_by_class()
    assert set(spend) == {mg.BILLABLE_API, mg.SUBSCRIPTION_CLI}
    assert spend[mg.BILLABLE_API]["calls"] == 1
    assert spend[mg.BILLABLE_API]["input_tokens"] == api.input_tokens
    assert spend[mg.SUBSCRIPTION_CLI]["input_tokens"] == sub.input_tokens
    # Per objective, and carried in the objective summary.
    assert set(gw.telemetry.spend_by_class("obj-sub")) == {mg.SUBSCRIPTION_CLI}
    assert set(gw.telemetry.summary("obj-api")["by_class"]) == {mg.BILLABLE_API}


def test_an_old_ledger_row_without_a_class_is_reported_by_its_route_kind(tmp_path):
    """A ledger written before this column existed is migrated, not lost."""
    import sqlite3
    path = tmp_path / "old.sqlite3"
    t = mg.GatewayTelemetry(path)
    with sqlite3.connect(path) as conn:
        conn.execute(
            "INSERT INTO gateway_calls (objective_id, task_class, status, route_kind,"
            " provider, input_tokens, created_at) VALUES"
            " ('o', 'SIMPLE', 'ok', 'free_tier', 'opencode-free', 7, 'then')")
    again = mg.GatewayTelemetry(path)
    assert again.spend_by_class()[mg.FREE_TIER]["input_tokens"] == 7
    assert t.path == again.path


# -- an API key moves Claude Code onto pay-as-you-go ------------------------


class NamesOnly(dict):
    """An environment that can be asked what is SET but never what it holds."""

    def _refuse(self, *a, **k):
        raise AssertionError("the detector read a variable's value")

    __getitem__ = get = values = items = _refuse


def test_an_api_key_variable_makes_the_run_billable_by_name_only():
    env = NamesOnly({"ANTHROPIC_API_KEY": SECRET, "PATH": "x"})
    verdict = mg.claude_code_billing(env)
    assert verdict.billing_class == mg.BILLABLE_API
    assert verdict.billable
    assert verdict.variables == ("ANTHROPIC_API_KEY",)
    assert SECRET not in repr(verdict) and SECRET not in verdict.report()
    assert "ANTHROPIC_API_KEY" in verdict.report()


def test_a_clean_environment_is_the_subscription():
    verdict = mg.claude_code_billing({"PATH": "x", "HOME": "y"})
    assert verdict.billing_class == mg.SUBSCRIPTION_CLI
    assert not verdict.billable and verdict.variables == ()


def test_the_launch_environment_is_what_is_judged(tmp_path, monkeypatch):
    """The key in FRIDAY's own environment is withheld from the worker, so
    the worker stays on the subscription; a sandbox that hands one over is
    caught."""
    launch = cli.Launch(prompt="x", cwd=str(tmp_path))
    monkeypatch.setenv("ANTHROPIC_API_KEY", SECRET)
    assert not cli.launch_billing(launch).billable

    class Box:
        def environment(self):
            return {"ANTHROPIC_API_KEY": SECRET}

    assert cli.launch_billing(launch, Box()).billing_class == mg.BILLABLE_API


def test_execute_reports_billable_before_the_cli_starts(tmp_path, monkeypatch):
    from friday import contracts as c
    from friday import execution
    from friday.executors.runs import RunManager

    order: list[str] = []
    seen: list[str] = []

    class FakeRun:
        process = None

        def __init__(self, *a, **k):
            pass

        async def events(self, timeout=None):
            order.append("start")
            yield cli.Event("result", {"result": "done"})

    monkeypatch.setattr(execution, "worker_environment",
                        lambda *a, **k: {"ANTHROPIC_API_KEY": SECRET})
    monkeypatch.setattr(cli, "available", lambda: True)
    monkeypatch.setattr(cli, "Run", FakeRun)
    monkeypatch.setattr(RunManager, "saw",
                        lambda self, run_id, event, **k: (order.append("saw"),
                                                          seen.append(event)))
    monkeypatch.setattr(ClaudeCodeExecutor, "finish",
                        lambda self, b, started, final: started.finish(status=c.PARTIAL))
    store = Store(tmp_path / "exec.sqlite3")
    try:
        bundle = TaskBundle(goal="add a retry", workspace=str(tmp_path), project="halo")
        asyncio.run(ClaudeCodeExecutor(store).execute(bundle))
    finally:
        store.close()
    assert order.index("saw") < order.index("start")
    assert any("billable_api" in e and "ANTHROPIC_API_KEY" in e for e in seen)
    assert not any(SECRET in e for e in seen)
