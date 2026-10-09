"""
Device firmware/runtime updates (DEVICE-007): verified, canaried, reversible.

    verify hash/signature -> install on ONE test device -> health probe ->
    roll out to the rest (probing each) -> any failed probe rolls back

Every integrity claim a release carries must pass, and it must carry at least
one (a sha256, or a signature checked by the injected `verify_signature`).
Nothing is sent to a device before the new release and the rollback target
both verify. The first device in `device_ids` is the canary; if its probe
fails the rest are never touched. A failed install or a probe that raises
counts as a failed probe. On a failure every device already updated is put
back on the prior release; a device that cannot be put back is listed in
`stuck`, never hidden.

    ROLLED_OUT       every device updated and probed healthy
    ROLLED_BACK      a probe/install failed; all updated devices are on `prior`
    ROLLBACK_FAILED  a probe/install failed and some device could not go back
    REJECTED         integrity check failed or the request is malformed
    UNAVAILABLE      the device provider is absent; nothing was sent

Seams: `provider` is a `devices.DeviceProvider` (the hermes-gadget-sdk backend
reports UNAVAILABLE while the SDK is absent); `probe(provider, device_id) ->
bool` is the caller's health check; `verify_signature(payload, signature) ->
bool` is the caller's key policy.

Assumptions (revisit if wrong): the install verb is `act(device,
"firmware.install", {"version", "payload"})` - the gadget SDK's real surface is
unverified (DEVICE-001); the canary is the first listed device; the prior
release is supplied by the caller rather than read from the device.
Not built: durable rollout history in the store, and an MCP wrapper.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
from dataclasses import dataclass
from typing import Callable

from friday import fabric
from friday.toolsets.devices import DeviceProvider

logger = logging.getLogger(__name__)

ROLLED_OUT = "ROLLED_OUT"
ROLLED_BACK = "ROLLED_BACK"
ROLLBACK_FAILED = "ROLLBACK_FAILED"
REJECTED = "REJECTED"
UNAVAILABLE = fabric.UNAVAILABLE


@dataclass(frozen=True)
class Release:
    version: str
    payload: bytes
    sha256: str | None = None
    signature: bytes | None = None


def _integrity_problem(release: Release,
                       verify_signature: Callable[[bytes, bytes], bool] | None
                       ) -> str | None:
    """Why this release cannot be trusted, or None when it can."""
    if release.sha256 is None and release.signature is None:
        return f"{release.version}: no hash or signature to verify"
    if release.sha256 is not None:
        actual = hashlib.sha256(release.payload).hexdigest()
        if not hmac.compare_digest(actual, release.sha256.lower()):
            return f"{release.version}: sha256 mismatch"
    if release.signature is not None:
        if verify_signature is None:
            return f"{release.version}: signed but no verifier is configured"
        if not verify_signature(release.payload, release.signature):
            return f"{release.version}: signature rejected"
    return None


def _install(provider: DeviceProvider, device_id: str, release: Release) -> None:
    provider.act(device_id, "firmware.install",
                 {"version": release.version, "payload": release.payload})


def _healthy(provider: DeviceProvider, device_id: str, release: Release,
             probe: Callable[[DeviceProvider, str], bool]) -> bool:
    try:
        _install(provider, device_id, release)
        return bool(probe(provider, device_id))
    except Exception as exc:                                 # noqa: BLE001
        logger.warning("device %s failed update %s: %s",
                       device_id, release.version, exc)
        return False


def rollout(provider: DeviceProvider, release: Release, prior: Release,
            device_ids: list[str], *,
            probe: Callable[[DeviceProvider, str], bool],
            verify_signature: Callable[[bytes, bytes], bool] | None = None
            ) -> dict:
    """Canary-then-fleet update with rollback to `prior`. Returns a result dict."""
    if not device_ids:
        return {"status": REJECTED, "reason": "no devices to update"}
    for candidate in (release, prior):
        problem = _integrity_problem(candidate, verify_signature)
        if problem:
            return {"status": REJECTED, "reason": problem}
    state = provider.health().get("state")
    if state not in (fabric.READY, fabric.DEGRADED):
        return {"status": UNAVAILABLE, "reason": f"{provider.id} is {state}"}

    touched: list[str] = []
    for device_id in device_ids:
        touched.append(device_id)        # a failed install may be half-applied
        if _healthy(provider, device_id, release, probe):
            continue
        stuck = []
        for done in touched:
            try:
                _install(provider, done, prior)
            except Exception as exc:                         # noqa: BLE001
                logger.error("device %s could not roll back to %s: %s",
                             done, prior.version, exc)
                stuck.append(done)
        return {"status": ROLLBACK_FAILED if stuck else ROLLED_BACK,
                "failed_device": device_id, "rolled_back": touched,
                "stuck": stuck}
    return {"status": ROLLED_OUT, "updated": touched}
