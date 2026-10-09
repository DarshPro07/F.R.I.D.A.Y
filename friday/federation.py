"""FED-001: the gate an inbound message from another agent must pass.

A remote agent's card (A2A "agent card") is its own claim about what it can
do. It is never evidence of what it may be given: the only thing that lets a
peer read local data is a grant FRIDAY's owner put in `Grants`, naming that
peer and that scope. Without one the data reader is never called.

A2A is the wire protocol and lives behind an injectable transport; with none
attached the gateway reports UNAVAILABLE and nothing else changes.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Callable

from friday import fabric

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class PeerCard:
    """What a remote agent says about itself. Untrusted."""
    agent_id: str
    name: str
    capabilities: tuple[str, ...] = ()


@dataclass(frozen=True)
class InboundMessage:
    card: PeerCard
    scope: str  # the local data the peer is asking for, e.g. "memory.read"


@dataclass(frozen=True)
class Reply:
    allowed: bool
    reason: str
    data: Any = None


class Grants:
    """Local policy grants: (peer id, scope) pairs the owner allowed."""

    def __init__(self) -> None:
        self._granted: set[tuple[str, str]] = set()

    def allow(self, peer_id: str, scope: str) -> None:
        self._granted.add((peer_id, scope))

    def revoke(self, peer_id: str, scope: str) -> None:
        self._granted.discard((peer_id, scope))

    def allows(self, peer_id: str, scope: str) -> bool:
        return (peer_id, scope) in self._granted


class FederationGateway:
    def __init__(self, grants: Grants, transport: Any | None = None) -> None:
        self._grants = grants
        self._transport = transport

    def handle(self, message: InboundMessage,
               read: Callable[[str], Any]) -> Reply:
        """Serve `message.scope` through `read` only under a local grant."""
        peer = message.card.agent_id
        if not self._grants.allows(peer, message.scope):
            logger.warning("federation refused peer=%s scope=%s: no local grant",
                           peer, message.scope)
            return Reply(False, f"no local grant for {message.scope!r}")
        return Reply(True, "local grant", read(message.scope))

    def transport_state(self) -> str:
        if self._transport is None:
            return fabric.UNAVAILABLE
        try:
            return self._transport.health()["state"]
        except Exception:
            logger.warning("federation transport health failed", exc_info=True)
            return fabric.UNAVAILABLE
