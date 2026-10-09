"""
A device's persistent identity and its pairing and trust lifecycle (DEVICE-003).

    UNPAIRED -> pairing code -> owner approval -> PAIRED_UNVERIFIED
             -> capability inventory -> policy assignment -> ACTIVE

Every step is persisted through the store. Announcing itself on the LAN gets a
device an identity and a code, never trust: `is_trusted` is true only for an
ACTIVE device, and ACTIVE is reachable only through the owner's approval,
which needs the code the device showed. `approve` is the owner's act - it has
no MCP tool, and nothing the model calls reaches it.

An endpoint must be wss://; plain ws:// is accepted only for loopback or a
private LAN address, the trusted local network.
"""

from __future__ import annotations

import hashlib
import hmac
import ipaddress
import json
import logging
import secrets
from datetime import datetime, timedelta, timezone
from typing import Callable
from urllib.parse import urlsplit

from friday import netguard

logger = logging.getLogger(__name__)

UNPAIRED = "UNPAIRED"
PAIRED_UNVERIFIED = "PAIRED_UNVERIFIED"
ACTIVE = "ACTIVE"

CODE_SECONDS = 300


class PairingRefused(RuntimeError):
    """A lifecycle step was refused; the device stays where it was."""


def _trusted_local(host: str) -> bool:
    if host == "localhost":
        return True
    try:
        real = netguard.normalise(ipaddress.ip_address(host))
    except ValueError:
        return False  # a name is not known to be local; it must use wss://
    if str(real) in netguard.METADATA_ADDRESSES:
        return False
    return real.is_loopback or real.is_private


def _check_endpoint(endpoint: str) -> None:
    parts = urlsplit(endpoint)
    if parts.scheme == "wss" and parts.hostname:
        return
    if parts.scheme == "ws" and parts.hostname and _trusted_local(parts.hostname):
        return
    raise PairingRefused(
        "device endpoint must be wss://; ws:// is allowed only on the trusted "
        "local network")


def _hash(device_id: str, code: str) -> str:
    return hashlib.sha256(f"{device_id}:{code}".encode()).hexdigest()


class Registry:
    def __init__(self, store, *, now: Callable[[], datetime] | None = None) -> None:
        self.store = store
        self._now = now or (lambda: datetime.now(timezone.utc))

    def request_pairing(self, name: str, endpoint: str) -> dict:
        """A device asks to pair: it gets an identity and a one-time code to
        show the owner. It is not trusted."""
        _check_endpoint(endpoint)
        now = self._now()
        device_id = f"dev_{secrets.token_hex(8)}"
        code = f"{secrets.randbelow(10**6):06d}"
        self.store.device_create(
            device_id=device_id, name=name, endpoint=endpoint, state=UNPAIRED,
            code_hash=_hash(device_id, code),
            code_expires_at=(now + timedelta(seconds=CODE_SECONDS)).isoformat(),
            created_at=now.isoformat())
        return {"device_id": device_id, "code": code, "state": UNPAIRED}

    def approve(self, device_id: str, code: str, *, approved_by: str) -> str:
        """The owner approves, with the code the device showed. Spent once."""
        row = self._row(device_id)
        now = self._now()
        if row["state"] != UNPAIRED:
            raise PairingRefused(f"{device_id} is {row['state']}, not awaiting approval")
        if now.isoformat() >= row["code_expires_at"]:
            logger.warning("pairing refused for %s: code expired", device_id)
            raise PairingRefused("pairing code expired")
        if not hmac.compare_digest(row["code_hash"], _hash(device_id, code)):
            logger.warning("pairing refused for %s: wrong code", device_id)
            raise PairingRefused("wrong pairing code")
        if not self.store.device_advance(
                device_id, expect=UNPAIRED, state=PAIRED_UNVERIFIED,
                approved_by=approved_by, approved_at=now.isoformat()):
            raise PairingRefused(f"{device_id} was already approved")
        return PAIRED_UNVERIFIED

    def record_inventory(self, device_id: str, capabilities: list[str]) -> None:
        """What the paired device says it can do. State stays PAIRED_UNVERIFIED."""
        row = self._row(device_id)
        if row["state"] != PAIRED_UNVERIFIED:
            raise PairingRefused(f"{device_id} is {row['state']}, not awaiting inventory")
        self.store.device_advance(
            device_id, expect=PAIRED_UNVERIFIED, state=PAIRED_UNVERIFIED,
            inventory=json.dumps(list(capabilities)))

    def assign_policy(self, device_id: str, policy: str) -> str:
        """Give the inventoried device a policy and activate it."""
        row = self._row(device_id)
        if row["state"] != PAIRED_UNVERIFIED:
            raise PairingRefused(f"{device_id} is {row['state']}, not awaiting a policy")
        if row["inventory"] is None:
            raise PairingRefused(f"{device_id} has no capability inventory yet")
        if not policy:
            raise PairingRefused("a policy name is required")
        if not self.store.device_advance(
                device_id, expect=PAIRED_UNVERIFIED, state=ACTIVE, policy=policy,
                activated_at=self._now().isoformat()):
            raise PairingRefused(f"{device_id} changed state underneath this call")
        return ACTIVE

    def is_trusted(self, device_id: str) -> bool:
        row = self.store.device(device_id)
        return bool(row) and row["state"] == ACTIVE

    def get(self, device_id: str) -> dict:
        return self._decode(self._row(device_id))

    def devices(self) -> list[dict]:
        return [self._decode(row) for row in self.store.devices()]

    def _row(self, device_id: str) -> dict:
        row = self.store.device(device_id)
        if row is None:
            raise PairingRefused(f"unknown device {device_id}")
        return row

    @staticmethod
    def _decode(row: dict) -> dict:
        out = {k: v for k, v in row.items() if k not in ("code_hash", "code_expires_at")}
        out["inventory"] = None if row["inventory"] is None else json.loads(row["inventory"])
        return out
