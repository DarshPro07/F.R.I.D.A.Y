"""
EVO-005: promote a signed candidate FRIDAY.exe, health-check it, roll it back.

    verify -> stop -> keep the prior exe -> swap -> start -> health probe
           -> healthy: advance the update floor   |   not healthy: restore, restart

A candidate is promoted only when ALL of these hold; every failing check is
listed, nothing is stopped or replaced until the list is empty:

  * the manifest is signed (Ed25519) by a key in `trusted_keys`, over the
    canonical manifest bytes, so editing any field after signing breaks it;
  * the exe's sha256 equals the one the manifest signs (tamper check), and the
    bytes that were hashed are the bytes that get installed (no re-read);
  * its sequence is strictly above the installed one and its version is not
    lower (rollback and replay resistance); the floor lives in `state_path`
    and moves only after a healthy start, so a rolled-back try can be retried;
  * release-gate evidence (scripts/release_gate.py report.json shape) says
    ready on a clean tree at the manifest's commit;
  * the owner approved this exact sha256 (EVOLAB-003: authorization names the
    hash); an approval for another build, or none, refuses.

An unreadable update state fails closed. Stopping and starting the process and
the health probe are injected (`stop`, `start`, `healthy`); this module owns the
decision and the file swap, not the process model. A start or probe that raises
counts as unhealthy and the exception text is kept in `reasons`.

    PROMOTED         swapped, started, healthy; the floor advanced
    ROLLED_BACK      the candidate failed; the prior exe is back and healthy
    ROLLBACK_FAILED  the candidate failed and the prior exe is not healthy either
    REFUSED          a check failed; the live exe was not touched

Assumptions (revisit if wrong): signing keys are Ed25519 and the trusted public
keys are supplied by the caller (their storage is the owner's key policy, not
decided here); a candidate is a single exe file (the onedir `_internal` tree is
not swapped); the prior exe is kept beside the live one as `<name>.previous`.
Known limits (see the report's gaps): a MISSING state file means "first
install" (floor 0), so deleting it lets an older validly-signed manifest with
sequence >= 1 through; `approved_sha256` and `gate_report` are caller-supplied
and unauthenticated here (the caller must read them from the owner's approval
record and release_gate's report.json), and the gate report is bound to the
commit, not to the exe hash.
Not built: building and signing the candidate in CI, and a live caller.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import re
import shutil
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Callable, Mapping

logger = logging.getLogger(__name__)

PROMOTED = "PROMOTED"
ROLLED_BACK = "ROLLED_BACK"
ROLLBACK_FAILED = "ROLLBACK_FAILED"
REFUSED = "REFUSED"

_DOMAIN = b"friday-exe-manifest-v1\n"
_VERSION = re.compile(r"^\d+(\.\d+)*$")
_SHA256 = re.compile(r"^[0-9a-fA-F]{64}$")


@dataclass(frozen=True)
class Manifest:
    version: str
    sequence: int
    exe_sha256: str
    commit: str
    key_id: str


@dataclass
class Outcome:
    status: str
    reasons: list[str] = field(default_factory=list)
    version: str = ""


def canonical(manifest: Manifest) -> bytes:
    body = json.dumps(asdict(manifest), sort_keys=True, separators=(",", ":"))
    return _DOMAIN + body.encode("utf-8")


def sign_manifest(manifest: Manifest, private_key: bytes) -> bytes:
    """Sign with a raw 32-byte Ed25519 private key (the build side)."""
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    return Ed25519PrivateKey.from_private_bytes(private_key).sign(canonical(manifest))


def previous_path(live_exe: Path) -> Path:
    return live_exe.with_name(live_exe.name + ".previous")


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _version_tuple(text: object) -> tuple[int, ...] | None:
    if not isinstance(text, str) or not _VERSION.match(text):
        return None
    return tuple(int(part) for part in text.split("."))


def _manifest_problems(manifest: Manifest) -> list[str]:
    """Shape of the untrusted manifest; a bad field is a reason, never a raise."""
    problems: list[str] = []
    if not isinstance(manifest.version, str):
        problems.append("manifest field version must be a string")
    if isinstance(manifest.sequence, bool) or not isinstance(manifest.sequence, int):
        problems.append("manifest field sequence must be an integer")
    if not isinstance(manifest.exe_sha256, str) or not _SHA256.match(manifest.exe_sha256):
        problems.append("manifest field exe_sha256 must be 64 hex characters")
    if not isinstance(manifest.commit, str) or not manifest.commit:
        problems.append("manifest field commit must be a non-empty string")
    if not isinstance(manifest.key_id, str) or not manifest.key_id:
        problems.append("manifest field key_id must be a non-empty string")
    return problems


def _same_digest(left: object, right: object) -> bool:
    """Constant-time hex digest comparison that is False, not an error, on junk."""
    if not (isinstance(left, str) and isinstance(right, str)
            and _SHA256.match(left) and _SHA256.match(right)):
        return False
    return hmac.compare_digest(left.lower().encode("ascii"), right.lower().encode("ascii"))


def _signature_problem(manifest: Manifest, signature: bytes | None,
                       trusted_keys: Mapping[str, bytes]) -> str | None:
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

    if not signature:
        return "unsigned candidate: no signature"
    raw = trusted_keys.get(manifest.key_id)
    if raw is None:
        return f"signature key {manifest.key_id!r} is not trusted"
    try:
        Ed25519PublicKey.from_public_bytes(raw).verify(signature, canonical(manifest))
    except InvalidSignature:
        return "signature does not verify against the manifest"
    except TypeError:
        return "signature is not bytes"
    except ValueError as exc:
        return f"trusted signature key {manifest.key_id!r} is unusable: {exc}"
    return None


def _load_floor(state_path: Path) -> tuple[dict | None, str | None]:
    if not state_path.exists():
        return {"sequence": 0, "version": "0"}, None
    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return None, f"update state unreadable, refusing to guess the floor: {exc}"
    if (not isinstance(state, dict) or isinstance(state.get("sequence"), bool)
            or not isinstance(state.get("sequence"), int)
            or _version_tuple(state.get("version")) is None):
        return None, "update state malformed, refusing to guess the floor"
    return state, None


def _gate_problem(report: object, commit: str) -> str | None:
    sha = ((report or {}).get("commit") or {}) if isinstance(report, dict) else {}
    ok = (isinstance(report, dict) and report.get("ready") is True
          and isinstance(commit, str) and bool(commit)
          and isinstance(sha, dict) and sha.get("sha") == commit
          and sha.get("dirty") is False)
    if ok:
        return None
    return (f"release gate evidence missing, not ready, dirty, or not on "
            f"commit {str(commit)[:12]!r}")


def check_candidate(candidate: Path, manifest: Manifest, signature: bytes | None, *,
                    live_exe: Path, state_path: Path,
                    trusted_keys: Mapping[str, bytes], gate_report: object,
                    approved_sha256: str | None) -> tuple[list[str], bytes | None]:
    """Every reason to refuse (empty means promotable) and the verified bytes."""
    problems = _manifest_problems(manifest)
    well_formed = not problems
    # canonical() and the trusted-key lookup need a well-formed manifest; the
    # shape reasons above already explain why the signature cannot be judged.
    problem = _signature_problem(manifest, signature, trusted_keys) if well_formed else None
    if problem:
        problems.append(problem)

    data: bytes | None = None
    try:
        data = candidate.read_bytes()
    except OSError as exc:
        problems.append(f"candidate exe unreadable: {exc}")
    if data is not None and well_formed and not _same_digest(
            _sha256(data), manifest.exe_sha256):
        problems.append("candidate exe sha256 differs from the signed manifest")

    floor, problem = _load_floor(state_path)
    if problem:
        problems.append(problem)
    else:
        wanted = _version_tuple(manifest.version)
        if (isinstance(manifest.sequence, int) and not isinstance(manifest.sequence, bool)
                and manifest.sequence <= floor["sequence"]):
            problems.append(f"downgrade or replay: sequence {manifest.sequence!r} "
                            f"is not above the installed {floor['sequence']}")
        if wanted is None and isinstance(manifest.version, str):
            problems.append(f"candidate version {manifest.version!r} is not numeric")
        elif wanted is not None and wanted < _version_tuple(floor["version"]):
            problems.append(f"candidate version {manifest.version} is lower than "
                            f"the installed {floor['version']}")

    problem = _gate_problem(gate_report, manifest.commit)
    if problem:
        problems.append(problem)
    if not _same_digest(approved_sha256, manifest.exe_sha256):
        problems.append("no owner approval naming this candidate's sha256")
    if not live_exe.is_file():
        problems.append("no installed exe to roll back to")
    return problems, data


def _write_atomic(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    staged = path.with_name(path.name + ".tmp")
    staged.write_bytes(data)
    os.replace(staged, path)


def promote(candidate: Path, manifest: Manifest, signature: bytes | None, *,
            live_exe: Path, state_path: Path, trusted_keys: Mapping[str, bytes],
            gate_report: object, approved_sha256: str | None,
            stop: Callable[[], None], start: Callable[[], None],
            healthy: Callable[[], bool]) -> Outcome:
    candidate, live_exe, state_path = Path(candidate), Path(live_exe), Path(state_path)
    problems, data = check_candidate(
        candidate, manifest, signature, live_exe=live_exe, state_path=state_path,
        trusted_keys=trusted_keys, gate_report=gate_report,
        approved_sha256=approved_sha256)
    if problems or data is None:
        logger.warning("exe promotion refused for %s: %s", manifest.version, problems)
        return Outcome(REFUSED, problems, manifest.version)

    prior = previous_path(live_exe)
    # Keep a verified copy of what is live BEFORE anything is stopped or replaced.
    # Rollback restores these bytes, never a .previous that may predate this try.
    try:
        live_bytes = live_exe.read_bytes()
        _write_atomic(prior, live_bytes)
        if prior.read_bytes() != live_bytes:
            raise OSError("the copy differs from the live exe")
    except Exception as exc:                                  # noqa: BLE001
        logger.error("exe promotion: could not keep the prior exe: %s", exc)
        return Outcome(REFUSED, [f"could not keep a verified copy of the prior exe: {exc}"],
                       manifest.version)

    reasons: list[str] = []
    try:
        stop()
    except Exception as exc:                                  # noqa: BLE001
        logger.error("exe promotion: stop failed, live exe untouched: %s", exc)
        return Outcome(REFUSED, [f"could not stop the running exe: {exc}"],
                       manifest.version)

    try:
        _write_atomic(live_exe, data)
        start()
        if not healthy():
            reasons.append("health check failed after the restart")
        else:
            try:
                _write_atomic(state_path, json.dumps({
                    "sequence": manifest.sequence, "version": manifest.version,
                    "exe_sha256": manifest.exe_sha256, "commit": manifest.commit,
                }).encode("utf-8"))
            except Exception as exc:                          # noqa: BLE001
                reasons.append(f"could not persist the update state, so the "
                               f"swap cannot be kept: {exc}")
    except Exception as exc:                                  # noqa: BLE001
        reasons.append(f"candidate failed to install or start: {exc}")
    for reason in reasons:
        logger.error("exe promotion: %s", reason)

    if not reasons:
        return Outcome(PROMOTED, [], manifest.version)

    return _roll_back(live_exe, live_bytes, reasons, manifest.version, stop, start, healthy)


def _roll_back(live_exe: Path, prior_bytes: bytes, reasons: list[str], version: str,
               stop: Callable[[], None], start: Callable[[], None],
               healthy: Callable[[], bool]) -> Outcome:
    try:
        stop()
        _write_atomic(live_exe, prior_bytes)
        start()
        ok = bool(healthy())
    except Exception as exc:                                  # noqa: BLE001
        reasons.append(f"rollback failed: {exc}")
        logger.error("exe promotion: %s", reasons[-1])
        return Outcome(ROLLBACK_FAILED, reasons, version)
    if not ok:
        reasons.append("prior exe is not healthy after the rollback")
        logger.error("exe promotion: %s", reasons[-1])
        return Outcome(ROLLBACK_FAILED, reasons, version)
    return Outcome(ROLLED_BACK, reasons, version)
