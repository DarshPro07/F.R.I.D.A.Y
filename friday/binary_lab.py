"""
BIN-004: the refusal policy for dynamic analysis of an unknown binary.

Static reading of a sample is `toolsets/binary.binary_analyze`. Anything that
would run it is a different act, and this module is the one door to it:

* A sample is never executed or detonated on the host. There is no code path
  here that starts a process (no `subprocess`, `os.system` or `ctypes`), and a
  request for host execution is refused whatever asked for it.
* Dynamic analysis is possible only through a Cyber Lab provider (CYBER-002)
  that declares the isolated `offline` tier, under a `SecurityAuthorization`
  whose target scope names the sample by its sha256. With no such provider
  FRIDAY refuses and says why. A provider declaring `scoped_network` or
  `active` is refused for a sample: a sample is not something to let near a
  network.
* The sample is held as hash and metadata only. The provider is asked about the
  sha256, never handed the bytes or a path, and the result carries none. An
  operational lab that would actually receive the sample is CYBER-003.
* Canaries (SANDBOX-001, `forge.seed_canaries`) are always in play: the caller's
  or, with none given, ones seeded in a temporary directory. Each file is hashed
  before and after the run. A canary read the provider reports, its value in the
  output, or a file changed, removed or added fails the evaluation and the report
  is withheld (the evidence still records that the run happened).

Every outcome, refused or run, carries hash-bound `cyber_lab.LabEvidence`.
"""

from __future__ import annotations

import hashlib
import json
import tempfile
from datetime import datetime
from pathlib import Path

from friday import contracts as c
from friday import cyber_lab
from friday import forge
from friday import trust as T
from friday.fsjail import JailError
from friday.toolsets import binary
from friday.toolsets.files import resolve_for

ACTION = "malware_analysis"
PROVIDER = "binary_lab"
LAB = "lab"

COMPLETED = "completed"
REFUSED = "refused"
FAILED = "failed"


def _result(status: str, reason: str, sample: dict, evidence: cyber_lab.LabEvidence, *,
            report: dict | None = None, findings: list[str] | None = None) -> dict:
    return {"status": status, "reason": reason, "sample": sample,
            "evidence": evidence.to_dict(), "report": report or {},
            "findings": findings or []}


def _canary_state(canaries: forge.Canaries) -> dict[str, str]:
    """Relative path -> sha256 of every file under the canary home."""
    home = canaries.home
    return {p.relative_to(home).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(home.rglob("*")) if p.is_file()}


def _canary_changes(before: dict[str, str], after: dict[str, str]) -> list[str]:
    """What the run did to the canary files; never echoes their contents."""
    out = [f"canary file {rel!r} was missing after the run" for rel in before if rel not in after]
    out += [f"canary file {rel!r} was modified by the run"
            for rel, digest in before.items() if rel in after and after[rel] != digest]
    out += [f"canary file {rel!r} was added by the run" for rel in after if rel not in before]
    return out


def _run_in_lab(provider, auth: T.SecurityAuthorization | None, sha: str, sample: dict,
                canaries: forge.Canaries, now: datetime | None) -> dict:
    """The provider call, with the canary files hashed before and checked after."""
    before = _canary_state(canaries)
    evidence = cyber_lab.execute(
        provider, auth,
        cyber_lab.LabRequest(host=sha, action=ACTION, intensity=T.PASSIVE), now=now)
    if not evidence.allowed:
        return _result(REFUSED, evidence.reason, sample, evidence)
    reads = evidence.output.get("reads") or ()
    touched = forge.canary_findings(
        canaries, [{"case": "lab", "reads": [str(r) for r in reads]}],
        json.dumps(evidence.output, default=str))
    touched += _canary_changes(before, _canary_state(canaries))
    if touched:
        # The run happened and its record says so; only its output is withheld.
        reason = "the lab run touched a canary"
        return _result(FAILED, reason, sample,
                       cyber_lab.record_withheld(evidence, reason), findings=touched)
    return _result(COMPLETED, evidence.reason, sample, evidence, report=evidence.output)


def dynamic_analysis(run: c.Run, path: str, *, provider, auth: T.SecurityAuthorization | None,
                     where: str = LAB, canaries: forge.Canaries | None = None,
                     now: datetime | None = None) -> dict:
    """Ask a lab provider to analyse the sample at `path`, or refuse and say why."""
    try:
        target = resolve_for(run, path)
    except JailError as exc:
        reason = f"path refused: {exc}"
        return _result(REFUSED, reason, {}, cyber_lab.record_refusal(
            PROVIDER, "", str(path)[:500], ACTION, reason, now=now))
    if not target.is_file():
        reason = f"no such file: {target.name}"
        return _result(REFUSED, reason, {}, cyber_lab.record_refusal(
            PROVIDER, "", str(path)[:500], ACTION, reason, now=now))

    _, sha, size = binary._read(target)
    sample = {"sha256": sha, "size": size, "name": target.name}
    name = getattr(provider, "name", PROVIDER)
    tier = getattr(provider, "tier", "")
    approval = auth.approval_id if auth else ""

    def refuse(reason: str) -> dict:
        return _result(REFUSED, reason, sample, cyber_lab.record_refusal(
            name, tier, sha, ACTION, reason, approval_id=approval, now=now))

    if where != LAB:
        return refuse("a sample is never executed or detonated on the host; "
                      "dynamic analysis runs only in a Cyber Lab provider")
    if provider is None:
        return refuse("no Cyber Lab provider declaring an isolated offline tier is "
                      "available, and a sample is not run on the host")
    if tier != cyber_lab.TIER_OFFLINE:
        return refuse(f"a sample needs a Cyber Lab provider at the {cyber_lab.TIER_OFFLINE} "
                      f"tier; {name!r} declares {tier or 'no tier'!r}")

    if canaries is not None:
        return _run_in_lab(provider, auth, sha, sample, canaries, now)
    # SANDBOX-001 is not up to the caller: with none given, seed our own.
    with tempfile.TemporaryDirectory(prefix="binary-lab-") as workdir:
        return _run_in_lab(provider, auth, sha, sample, forge.seed_canaries(Path(workdir)), now)
