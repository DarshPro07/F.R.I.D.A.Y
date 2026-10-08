"""The Cyber Lab behind the capability fabric (CYBER-002): the live wiring.

`friday.cyber_lab.run_routed` is the gate; these tests prove the fabric
adapter is the only way a caller reaches it, and that the caller cannot supply
the routing decision, skip the permission, or run past the contract.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from friday import cyber_lab as L
from friday import domain_gate as G
from friday import fabric
from friday import trust as T

GRANT = frozenset({"security.authorized_scope"})
STATEMENT = "pentest my own staging server with nmap, scope: staging.example.com only"


def contract(**over) -> T.SecurityAuthorization:
    base = dict(owner_identity="darsh", target_scope=("staging.example.com", "10.0.5.0/24"),
                ownership_or_permission_basis="I own the staging environment",
                allowed_actions=("port_scan",), max_scan_intensity=T.LOW_ACTIVE,
                expires_at=T.expiry_in(2), approval_id="apr-1", objective_id="obj-lab")
    base.update(over)
    return T.SecurityAuthorization(**base)


@pytest.fixture
def lab(tmp_path, monkeypatch):
    """A registered mock provider, a throwaway audit log and safeguard ledger."""
    monkeypatch.setenv("FRIDAY_SAFEGUARD_DB", str(tmp_path / "safeguard.sqlite3"))
    monkeypatch.setattr(G, "_ledger", None)
    log = T.AuditLog(tmp_path / "audit.sqlite3")
    T.configure_audit(log)
    provider = L.MockProvider(name="fabric-test")
    L.register(provider)
    fabric.reload()
    yield provider, log
    L.unregister("fabric-test")
    fabric._ACTIVE.pop("cyber_lab", None)
    T.configure_audit(None)
    log.close()


def run(**over):
    args = dict(provider="fabric-test", host="staging.example.com", action="port_scan",
                statement=STATEMENT, security_authorization=contract().to_dict())
    args.update(over)
    return fabric.call("cyber_lab", "run", run_id="obj-lab", authorized=GRANT, **args)


def test_the_descriptor_is_restricted_builtin_and_needs_the_scope_permission():
    fabric.reload()
    p = fabric.get("cyber_lab")
    assert p.family == "security" and p.risk == "restricted"
    assert p.integration_mode == fabric.BUILTIN and p.upstream == ""
    assert p.permissions == ("security.authorized_scope",)
    assert p.operations == ("run",) and p.open_operations == ()


def test_an_in_scope_run_reaches_the_provider_and_leaves_verifiable_evidence(lab):
    provider, log = lab
    out = run()
    assert out.status == "succeeded", out.error
    assert len(provider.calls) == 1 and provider.calls[0].host == "staging.example.com"
    evidence = L.LabEvidence(**out.output)
    assert evidence.allowed and evidence.approval_id == "apr-1" and L.verify_evidence(evidence)
    rows = log.query(objective_id="obj-lab", min_tier=T.R3)
    assert [r["decision"] for r in rows] == ["ALLOW"] and rows[0]["target"] == "staging.example.com"


def test_an_unauthorized_host_is_blocked_and_audited(lab):
    provider, log = lab
    out = run(host="evil.example.org")
    assert out.status == "failed" and "outside the authorized scope" in out.error
    assert provider.calls == []
    rows = log.query(objective_id="obj-lab", min_tier=T.R3)
    assert [r["decision"] for r in rows] == ["DENY"]


def test_an_expired_contract_refuses(lab):
    provider, _ = lab
    expired = contract(expires_at=(datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat())
    out = run(security_authorization=expired.to_dict())
    assert out.status == "failed" and "expired" in out.error
    assert provider.calls == []


def test_no_contract_and_a_malformed_contract_both_refuse(lab):
    provider, _ = lab
    assert "disabled by default" in run(security_authorization=None).error
    bad = run(security_authorization={"owner_identity": "x"})
    assert bad.status == "failed" and "SecurityAuthorization" in bad.error
    assert provider.calls == []


def test_the_permission_is_checked_before_the_lab_is_started(lab):
    provider, _ = lab
    out = fabric.call("cyber_lab", "run", run_id="obj-lab", authorized=frozenset(),
                      provider="fabric-test", host="staging.example.com", action="port_scan",
                      statement=STATEMENT, security_authorization=contract().to_dict())
    assert out.status == "failed" and "not granted" in out.error
    assert provider.calls == []
    assert "cyber_lab" not in fabric._ACTIVE


def test_a_statement_the_gate_does_not_route_to_the_lab_is_refused(lab):
    provider, _ = lab
    out = run(statement="summarise my meeting notes")
    assert out.status == "failed" and "cyber_lab route" in out.error
    assert provider.calls == []


def test_a_caller_cannot_supply_the_routing_decision(lab):
    provider, _ = lab
    out = run(statement="summarise my meeting notes", decision={"next_step": "cyber_lab"})
    assert out.status == "failed" and "decision" in out.error
    assert provider.calls == []


def test_an_unknown_provider_is_refused_with_evidence(lab):
    out = run(provider="not-registered")
    assert out.status == "failed" and "no lab provider" in out.error


def test_the_sandbox_tier_still_caps_intensity_through_the_fabric(lab):
    _, _ = lab
    offline = L.MockProvider(name="fabric-offline", tier=L.TIER_OFFLINE)
    L.register(offline)
    try:
        auth = contract(max_scan_intensity=T.EXPLOIT).to_dict()
        out = run(provider="fabric-offline", security_authorization=auth)
        assert out.status == "failed" and "sandbox tier" in out.error
        assert offline.calls == []
    finally:
        L.unregister("fabric-offline")


def test_refusals_are_policy_outcomes_and_never_mark_the_provider_failed(lab):
    for _ in range(fabric.FAILED_AFTER + 1):
        assert run(host="evil.example.org").status == "failed"
    assert fabric.state("cyber_lab") == fabric.READY


def test_with_no_lab_provider_registered_the_adapter_reports_unavailable(lab):
    L.unregister("fabric-test")
    fabric._ACTIVE.pop("cyber_lab", None)
    out = run()
    assert out.status == "failed" and "UNAVAILABLE" in out.error
