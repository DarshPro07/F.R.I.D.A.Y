"""
FED-001: a remote agent's card is a claim, never a grant.

A peer arriving over A2A (or anything else) is refused local data unless a
local policy grant names that peer and that scope; whatever it claims about
itself changes nothing. The wire protocol is an injectable transport that
reports UNAVAILABLE while absent.
"""

from __future__ import annotations

import pytest

from friday import fabric
from friday import federation as fed


def _card(peer="peer-a", claims=("memory.read",)):
    return fed.PeerCard(agent_id=peer, name="Remote", capabilities=tuple(claims))


def _read_calls():
    calls = []

    def read(scope):
        calls.append(scope)
        return {"scope": scope, "data": "local-secret-note"}

    return read, calls


def test_card_claim_without_local_grant_is_refused_and_data_untouched():
    read, calls = _read_calls()
    gw = fed.FederationGateway(grants=fed.Grants())

    reply = gw.handle(fed.InboundMessage(card=_card(), scope="memory.read"), read)

    assert reply.allowed is False
    assert reply.data is None
    assert "no local grant" in reply.reason
    assert calls == []


def test_local_grant_serves_exactly_the_granted_scope():
    read, calls = _read_calls()
    grants = fed.Grants()
    grants.allow("peer-a", "memory.read")
    gw = fed.FederationGateway(grants=grants)

    ok = gw.handle(fed.InboundMessage(card=_card(), scope="memory.read"), read)
    other = gw.handle(fed.InboundMessage(card=_card(claims=("files.read",)),
                                         scope="files.read"), read)

    assert ok.allowed and ok.data["data"] == "local-secret-note"
    assert other.allowed is False
    assert calls == ["memory.read"]


def test_grant_belongs_to_the_peer_not_to_the_claim():
    read, calls = _read_calls()
    grants = fed.Grants()
    grants.allow("peer-a", "memory.read")
    gw = fed.FederationGateway(grants=grants)

    reply = gw.handle(fed.InboundMessage(card=_card(peer="peer-b"),
                                         scope="memory.read"), read)

    assert reply.allowed is False
    assert calls == []


def test_a_revoked_grant_stops_serving():
    read, calls = _read_calls()
    grants = fed.Grants()
    grants.allow("peer-a", "memory.read")
    grants.revoke("peer-a", "memory.read")
    gw = fed.FederationGateway(grants=grants)

    assert gw.handle(fed.InboundMessage(card=_card(), scope="memory.read"),
                     read).allowed is False
    assert calls == []


def test_every_refusal_is_logged(caplog):
    read, _ = _read_calls()
    gw = fed.FederationGateway(grants=fed.Grants())
    with caplog.at_level("WARNING", logger="friday.federation"):
        gw.handle(fed.InboundMessage(card=_card(), scope="memory.read"), read)
    assert any("peer-a" in r.getMessage() and "memory.read" in r.getMessage()
               for r in caplog.records)


def test_absent_transport_reports_unavailable_and_stub_reports_its_health():
    assert fed.FederationGateway(grants=fed.Grants()).transport_state() == fabric.UNAVAILABLE

    class Stub:
        def health(self):
            return {"state": fabric.READY}

    gw = fed.FederationGateway(grants=fed.Grants(), transport=Stub())
    assert gw.transport_state() == fabric.READY


def test_a_transport_that_raises_is_unavailable_not_fatal():
    class Broken:
        def health(self):
            raise RuntimeError("boom")

    gw = fed.FederationGateway(grants=fed.Grants(), transport=Broken())
    assert gw.transport_state() == fabric.UNAVAILABLE
