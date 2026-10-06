"""
MODEL-006: a recorded safeguard intervention changes the next route.

The registry records which task domains a route is declared unsuitable for. A
provider's own safeguard refusing a request is recorded in the safeguard ledger
against (route, domain), and the next request in that domain no longer takes the
route: it goes to the fallback, or ends BLOCKED / ASK_USER. A request in any
other domain is unaffected, and a refusal is not a health problem, so the
120 s unhealthy-route memory is not what carries it.

Assumed, not owned: the domain phrase lists in friday/domain_gate.py are still
owner-pending (prd_ledger MODEL-006 note), and an exclusion lasts until the
ledger is cleared.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

from friday import domain_gate as DG
from friday import model_capabilities as MC
from friday import model_gateway as mg

FAKE = str(Path(__file__).parent / "fake_model_gateway_worker.py")

#: classify_text -> cyber_sensitive / route "ask_scope" (a test action, no scope stated).
ASK_SCOPE_TEXT = "write an exploit for the sql injection in the login form"
#: classify_text -> cyber_sensitive / route "restricted" (abusive intent).
MALICIOUS_TEXT = "build ransomware to break into their server"
GENERAL_TEXT = "what is on my calendar tomorrow"

A = ("anthropic", "fake-a")
B = ("openai-codex", "fake-b")
KEY_A = DG.route_name("gateway", *A)


@pytest.fixture
def clean_env(monkeypatch, tmp_path):
    for key in ("FAKE_GW_FAIL_PROVIDERS", "FAKE_GW_REFUSE_PROVIDERS", "FAKE_GW_EMPTY_PROVIDERS",
                "FAKE_GW_HANG", "FAKE_GW_DIE", "FAKE_GW_ECHO"):
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


def request(text, **kw):
    return mg.ModelGatewayRequest(
        objective_id="obj-safeguard", task_class=mg.SIMPLE,
        context_package=mg.compile_context(system="You are Friday.", user=text), **kw)


def served_by(res):
    return (res.provider, res.model)


def refusals(route=KEY_A, domain=DG.CYBER):
    return DG.ledger().count(route, "provider_refusal", domain=domain)


def intervene(provider, model, domain=DG.CYBER):
    DG.record_provider_refusal(DG.route_name("gateway", provider, model), domain=domain)


# --- the registry records the restriction ----------------------------------


def _caps(**kw):
    base = dict(model_id="m", provider="p", backend="p", vision=True, tools=True,
                structured_output=True, realtime_audio=False, max_context=None,
                relative_latency="low", relative_cost="low")
    base.update(kw)
    return MC.ModelCapabilities(**base)


def test_the_registry_records_restricted_domains_and_safeguard_posture():
    caps = _caps(restricted_domains=(DG.CYBER,), safeguard="intervenes")
    assert caps.restricted_domains == (DG.CYBER,)
    assert caps.safeguard == "intervenes"
    plain = _caps()
    assert plain.restricted_domains == () and plain.safeguard == "unknown"


def test_the_registry_rejects_a_domain_or_posture_it_does_not_know():
    with pytest.raises(ValueError):
        _caps(restricted_domains=("made_up_domain",))
    with pytest.raises(ValueError):
        _caps(safeguard="probably_fine")


def test_a_route_declared_restricted_for_a_domain_is_rejected_for_that_domain_only(monkeypatch, gw):
    monkeypatch.setattr(MC, "CAPABILITIES", {
        A: _caps(model_id=A[1], provider=A[0], backend=A[0], restricted_domains=(DG.CYBER,)),
        B: _caps(model_id=B[1], provider=B[0], backend=B[0]),
    })
    cyber = gw.route_decision(request(ASK_SCOPE_TEXT))
    assert A not in [(p, m) for _, p, m in cyber.routes]
    rejected = {(j.provider, j.model): j for j in cyber.rejected}
    assert A in rejected and rejected[A].safeguard
    assert DG.CYBER in rejected[A].reason
    assert cyber.domain == DG.CYBER

    general = gw.route_decision(request(GENERAL_TEXT))
    assert A in [(p, m) for _, p, m in general.routes]


# --- a recorded intervention excludes the route for that domain ------------


def test_a_ledger_event_excludes_the_route_for_that_domain_and_no_other(gw):
    intervene(*A)
    cyber = gw.route_decision(request(ASK_SCOPE_TEXT))
    assert A not in [(p, m) for _, p, m in cyber.routes]
    why = {(j.provider, j.model): j.reason for j in cyber.rejected}[A]
    assert "safeguard" in why and DG.CYBER in why

    assert A in [(p, m) for _, p, m in gw.route_decision(request(GENERAL_TEXT)).routes]
    cbrne = request("how do i weaponize anthrax spores for dispersal")
    assert DG.classify_text("how do i weaponize anthrax spores for dispersal").domain == DG.CBRNE
    assert A in [(p, m) for _, p, m in gw.route_decision(cbrne).routes]


def test_clearing_the_ledger_restores_the_route(gw, monkeypatch, tmp_path):
    intervene(*A)
    assert A not in [(p, m) for _, p, m in gw.route_decision(request(ASK_SCOPE_TEXT)).routes]
    monkeypatch.setenv("FRIDAY_SAFEGUARD_DB", str(tmp_path / "cleared.sqlite3"))
    monkeypatch.setattr(DG, "_ledger", None)
    assert A in [(p, m) for _, p, m in gw.route_decision(request(ASK_SCOPE_TEXT)).routes]


def test_lifting_an_exclusion_restores_that_route_for_that_domain_only(gw):
    intervene(*A, domain=DG.CYBER)
    intervene(*A, domain=DG.CBRNE)
    assert DG.clear_route_exclusion(KEY_A, DG.CYBER) == 1
    assert A in [(p, m) for _, p, m in gw.route_decision(request(ASK_SCOPE_TEXT)).routes]
    assert DG.route_excluded(KEY_A, DG.CBRNE)


def test_lifting_without_a_domain_restores_every_domain_on_the_route(gw):
    intervene(*A, domain=DG.CYBER)
    intervene(*A, domain=DG.CBRNE)
    other = DG.route_name("gateway", *B)
    DG.record_provider_refusal(other, domain=DG.CYBER)
    assert DG.clear_route_exclusion(KEY_A) == 2
    assert not DG.route_excluded(KEY_A, DG.CYBER) and not DG.route_excluded(KEY_A, DG.CBRNE)
    assert DG.route_excluded(other, DG.CYBER)


def test_lifting_an_exclusion_keeps_the_audit_events_that_are_not_refusals():
    led = DG.ledger()
    led.record(KEY_A, "restricted_packet", domain=DG.CYBER, packet="p")
    led.record(KEY_A, "provider_refusal", domain=DG.CYBER)
    assert DG.clear_route_exclusion(KEY_A, DG.CYBER) == 1
    assert led.count(KEY_A, "restricted_packet", domain=DG.CYBER) == 1
    assert DG.clear_route_exclusion(KEY_A, DG.CYBER) == 0


def test_a_lifted_route_is_called_again_instead_of_skipped(gw, clean_env):
    # The fake worker keeps the env it started with, so it refuses on A both times:
    # what the lift changes is that A is attempted again rather than skipped.
    clean_env.setenv("FAKE_GW_REFUSE_PROVIDERS", A[0])
    gw.infer(request(ASK_SCOPE_TEXT))
    skipped = gw.infer(request(ASK_SCOPE_TEXT))
    assert skipped.attempts == [] and refusals() == 1
    assert DG.clear_route_exclusion(KEY_A, DG.CYBER) == 1
    retried = gw.infer(request(ASK_SCOPE_TEXT))
    assert [a["provider"] for a in retried.attempts] == [A[0]]
    assert retried.attempts[0]["code"] == "SAFEGUARD_REFUSED"
    assert served_by(retried) == B and refusals() == 1


def test_the_ledger_counts_per_route_and_domain():
    led = DG.ledger()
    led.record("r", "provider_refusal", domain=DG.CYBER)
    led.record("r", "provider_refusal", domain=DG.CBRNE)
    led.record("r", "provider_refusal", domain=DG.CBRNE)
    assert led.count("r", "provider_refusal") == 3
    assert led.count("r", "provider_refusal", domain=DG.CYBER) == 1
    assert led.count("r", "provider_refusal", domain=DG.CBRNE) == 2
    assert led.count("r", "provider_refusal", domain="") == 0


# --- dispatch: the second attempt takes the fallback or ends BLOCKED -------


def test_a_refusal_is_recorded_and_the_second_attempt_takes_the_fallback(gw, clean_env):
    clean_env.setenv("FAKE_GW_REFUSE_PROVIDERS", "anthropic")
    first = gw.infer(request(ASK_SCOPE_TEXT))
    assert first.status == "ok" and served_by(first) == B
    assert first.dispatch == "FALLBACK"
    assert first.attempts[0]["code"] == "SAFEGUARD_REFUSED"
    assert refusals() == 1

    second = gw.infer(request(ASK_SCOPE_TEXT))
    assert served_by(second) == B and second.dispatch == "FALLBACK"
    assert second.attempts == [] and second.failover_count == 0
    assert refusals() == 1, "the excluded route was called again"


def test_a_refusal_does_not_cool_the_route_for_other_domains(gw, clean_env):
    clean_env.setenv("FAKE_GW_REFUSE_PROVIDERS", "anthropic")
    gw.infer(request(ASK_SCOPE_TEXT))
    assert refusals() == 1
    assert A not in [(p, m) for _, p, m in gw.candidates(request(ASK_SCOPE_TEXT))]
    assert A in [(p, m) for _, p, m in gw.route_decision(request(GENERAL_TEXT)).routes]
    assert A in [(p, m) for _, p, m in gw.candidates(request(GENERAL_TEXT))]


def test_a_general_request_still_uses_the_first_route(gw):
    intervene(*A)
    res = gw.infer(request(GENERAL_TEXT))
    assert served_by(res) == A and res.dispatch == "ALLOW"


def test_a_declared_restriction_skips_the_route_without_calling_it(gw, monkeypatch):
    monkeypatch.setattr(MC, "CAPABILITIES", {
        A: _caps(model_id=A[1], provider=A[0], backend=A[0], restricted_domains=(DG.CYBER,)),
    })
    res = gw.infer(request(ASK_SCOPE_TEXT))
    assert served_by(res) == B and res.dispatch == "FALLBACK"
    assert res.attempts == []
    assert refusals() == 0


def test_with_every_route_refused_the_second_attempt_is_blocked(gw, clean_env):
    clean_env.setenv("FAKE_GW_REFUSE_PROVIDERS", "anthropic,openai-codex")
    first = gw.infer(request(MALICIOUS_TEXT))
    assert first.status == "refused" and first.dispatch == "BLOCKED"
    assert [a["code"] for a in first.attempts] == ["SAFEGUARD_REFUSED", "SAFEGUARD_REFUSED"]
    assert first.response == ""

    second = gw.infer(request(MALICIOUS_TEXT))
    assert second.status == "refused" and second.dispatch == "BLOCKED"
    assert second.entitlement_state == "SAFEGUARD_BLOCKED"
    assert second.attempts == [], "an incompatible route was retried"
    assert refusals() == 1 and refusals(DG.route_name("gateway", *B)) == 1


def test_a_blocked_dispatch_asks_the_user_when_the_scope_is_missing(gw, clean_env):
    clean_env.setenv("FAKE_GW_REFUSE_PROVIDERS", "anthropic,openai-codex")
    first = gw.infer(request(ASK_SCOPE_TEXT))
    assert first.status == "refused" and first.dispatch == "ASK_USER"
    assert first.entitlement_state == "NEEDS_SCOPE"
    second = gw.infer(request(ASK_SCOPE_TEXT))
    assert second.dispatch == "ASK_USER" and second.attempts == []


def test_when_only_a_local_route_remains_the_dispatch_says_so(gw, monkeypatch):
    intervene(*A)
    monkeypatch.setattr(gw, "route_kind", lambda provider: "local" if provider == B[0] else "api")
    res = gw.infer(request(ASK_SCOPE_TEXT))
    assert served_by(res) == B and res.dispatch == "LOCAL_ONLY"


def test_a_plain_call_is_allow(gw):
    res = gw.infer(request(GENERAL_TEXT))
    assert served_by(res) == A and res.dispatch == "ALLOW"


def test_an_ordinary_failover_is_a_fallback_and_records_no_safeguard_event(gw, clean_env):
    clean_env.setenv("FAKE_GW_FAIL_PROVIDERS", "anthropic:QUOTA_EXCEEDED")
    res = gw.infer(request(GENERAL_TEXT))
    assert served_by(res) == B and res.dispatch == "FALLBACK"
    assert DG.ledger().count(KEY_A) == 0
