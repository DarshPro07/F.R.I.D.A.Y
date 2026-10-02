"""Cyber Lab boundaries (CYBER-002): deterministic policy tests only."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from friday import cyber_lab as L
from friday import trust as T


def contract(**over) -> T.SecurityAuthorization:
    base = dict(owner_identity="darsh", target_scope=("staging.example.com", "10.0.5.0/24"),
                ownership_or_permission_basis="I own the staging environment",
                allowed_actions=("port_scan",), max_scan_intensity=T.LOW_ACTIVE,
                expires_at=T.expiry_in(2), approval_id="apr-1")
    base.update(over)
    return T.SecurityAuthorization(**base)


def test_mock_provider_on_unauthorized_host_is_blocked():
    p = L.MockProvider()
    ev = L.execute(p, contract(), L.LabRequest("evil.example.org", "port_scan"))
    assert not ev.allowed and "outside the authorized scope" in ev.reason
    assert p.calls == []
    assert L.verify_evidence(ev)


def test_expired_contract_refuses():
    p = L.MockProvider()
    later = datetime.now(timezone.utc) + timedelta(days=30)
    ev = L.execute(p, contract(), L.LabRequest("staging.example.com", "port_scan"), now=later)
    assert not ev.allowed and "expired" in ev.reason
    assert p.calls == []


def test_no_contract_refuses():
    p = L.MockProvider()
    assert not L.execute(p, None, L.LabRequest("staging.example.com", "port_scan")).allowed
    assert p.calls == []


def test_in_scope_request_runs_and_leaves_verifiable_evidence():
    p = L.MockProvider()
    ev = L.execute(p, contract(), L.LabRequest("api.staging.example.com", "port_scan"))
    assert ev.allowed and len(p.calls) == 1 and ev.approval_id == "apr-1"
    assert L.verify_evidence(ev)
    tampered = L.LabEvidence(**{**ev.to_dict(), "allowed": False})
    assert not L.verify_evidence(tampered)


def test_sandbox_tier_caps_intensity_even_when_contract_allows_it():
    p = L.MockProvider(tier=L.TIER_OFFLINE)
    auth = contract(max_scan_intensity=T.EXPLOIT, allowed_actions=("port_scan",))
    ev = L.execute(p, auth, L.LabRequest("staging.example.com", "port_scan", T.LOW_ACTIVE))
    assert not ev.allowed and "sandbox tier" in ev.reason
    assert p.calls == []


def test_registry_rejects_unknown_tier_and_duplicates():
    with pytest.raises(ValueError):
        L.register(L.MockProvider(name="bad", tier="root"))
    p = L.MockProvider(name="reg-test")
    L.register(p)
    try:
        assert L.get("reg-test") is p
        with pytest.raises(ValueError):
            L.register(L.MockProvider(name="reg-test"))
    finally:
        L.unregister("reg-test")
