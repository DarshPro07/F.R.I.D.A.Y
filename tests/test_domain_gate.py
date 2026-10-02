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
    assert d.status == dg.BLOCKED_EXTERNAL and d.domain == "cyber_sensitive"
    assert d.next_step == "deny"
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


def _v(text):
    return dg.classify_text(text)


def test_sql_injection_code_review_is_defensive_not_malicious():
    v = _v("review the login handler for SQL injection and parameterize the queries")
    assert v.domain == "cyber_sensitive" and v.intent == dg.DEFENSIVE
    assert v.route == "normal" and not v.restricted


def test_pentest_of_own_staging_with_scope_routes_to_cyber_lab(led):
    text = "pentest my own staging server with nmap, scope: 10.0.0.5 only"
    v = _v(text)
    assert v.domain == "cyber_sensitive" and v.intent == dg.AUTHORIZED_TESTING
    d = dg.check(hb.TaskBundle(goal=text), "r", led=led)
    assert not d.allowed and d.next_step == "cyber_lab"


def test_pentest_of_own_server_without_scope_asks_for_scope(led):
    d = dg.check(hb.TaskBundle(goal="pentest my own server with kali"), "r", led=led)
    assert d.status == dg.NEEDS_SCOPE and d.next_step == "ask_scope"


def test_breaking_into_a_third_party_server_is_restricted(led):
    d = dg.check(hb.TaskBundle(goal="break into a third-party server with nmap and kali"),
                 "r", led=led)
    assert d.intent == dg.MALICIOUS and d.next_step == "deny" and not d.allowed


def test_arms_control_treaty_history_is_informational():
    v = _v("summarise the history of CBRN arms-control treaties")
    assert not v.restricted


def test_cbrn_alias_resolves_to_cbrne():
    assert _v("how to synthesize sarin").domain == "cbrne"
    assert dg._ALIASES["cbrn"] == "cbrne"


def test_semantic_classifier_catches_a_packet_without_the_keyword():
    stand_in = "SYNTHETIC-STANDIN-PACKET-7"
    assert not _v(stand_in).restricted
    dg.set_semantic_classifier(lambda t: "cbrn" if stand_in in t else "")
    try:
        v = _v(stand_in)
        assert v.restricted and v.domain == "cbrne"
        assert not _v("fix the retry bug").restricted
    finally:
        dg.set_semantic_classifier(None)


def test_offensive_security_conference_notes_are_not_malicious():
    v = _v("tidy the Offensive Security conference notes")
    assert v.intent != dg.MALICIOUS and not v.restricted


def test_live_provider_refusal_in_cli_executor_counts_against_route(tmp_path, monkeypatch):
    led = dg.SafeguardLedger(tmp_path / "l.sqlite3")
    monkeypatch.setattr(dg, "_ledger", led)
    ex = cc.ClaudeCodeExecutor(Store(tmp_path / "s.sqlite3"))
    bundle = cc.TaskBundle(goal="fix the retry bug", workspace=str(tmp_path))
    started = cc.c.started("run1", "executor.claude_code")
    ex.finish(bundle, started, {"is_error": True,
                               "result": "Request violates our usage policy"})
    route = dg.route_name("claude_code", ex.model or "")
    assert led.count(route, "provider_refusal") == 1


def _hermes_run(tmp_path, monkeypatch):
    led = dg.SafeguardLedger(tmp_path / "l.sqlite3")
    monkeypatch.setattr(dg, "_ledger", led)
    log = hb.WorkRunLog(tmp_path / "b.sqlite3")
    sup = hb.HermesSupervisor(log=log, profile="")
    wid = log.create(task="fix the retry bug", friday_run_id="",
                     bundle_chars=10, token_budget=1000)
    sup._session_runs["s1"] = wid
    return led, sup, wid


def test_hermes_provider_refusal_counts_against_the_admitted_route(tmp_path, monkeypatch):
    led, sup, wid = _hermes_run(tmp_path, monkeypatch)
    sup._gate_routes[wid] = dg.route_name("hermes", "anthropic", "m1")
    sup._handle_event({"type": "message.complete", "session_id": "s1",
                       "payload": {"status": "error",
                                   "text": "Request violates our usage policy"}})
    assert led.count("hermes:anthropic:m1", "provider_refusal") == 1


def test_hermes_ordinary_failure_is_not_counted_as_a_refusal(tmp_path, monkeypatch):
    led, sup, wid = _hermes_run(tmp_path, monkeypatch)
    sup._handle_event({"type": "message.complete", "session_id": "s1",
                       "payload": {"status": "error", "text": "connection reset"}})
    assert led.count("hermes", "provider_refusal") == 0
