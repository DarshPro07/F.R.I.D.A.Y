"""
The scan stage of the skill trust pipeline (SKILL-004).

Two scans, one stored record:

    injection   in-process patterns for text that tries to steer the model
                (role override, concealment from the user, prompt disclosure),
                over every text file of the skill. Wider than lint's
                `prompt_injection` check, which only knows one wording.
    external    NVIDIA SkillSpector, reached through the capability fabric
                (friday.fabric_adapters.skillspector) - never a second adapter.
                Only for skills that did not come from the owner or ship with
                FRIDAY. An absent scanner is NOT_SCANNED, never clean.

The record (`scan_skill`) is kept with the evaluation report in the registry:
when, over which content hash, what was found, and the external report hash.

A static PASS is evidence about text, not about behaviour. Every record says
`runtime_safe: False`; nothing in this module can make it true.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Callable

from friday.skills import contracts as K
from friday.skills import files as F

REJECTED = "REJECTED"
PASSED_STATIC = "PASSED_STATIC"
NOT_FULLY_SCANNED = "NOT_FULLY_SCANNED"

CLEAN, BLOCKED, NOT_SCANNED, NOT_REQUESTED = "CLEAN", "BLOCKED", "NOT_SCANNED", "NOT_REQUESTED"

PROVIDER = "skillspector"
STAGE = "skill-scan/1"
NOTE = "static scan only: a pass says nothing about runtime behaviour"

#: Trust levels whose content is not the owner's own or FRIDAY's: these are
#: scanned externally as well.
EXTERNALLY_SCANNED = (K.TRUST_AGENT, K.TRUST_COMMUNITY)

#: (pattern, code, detail). Text aimed at the model rather than describing a task.
INJECTION_PATTERNS: tuple[tuple[re.Pattern, str, str], ...] = (
    (re.compile(r"\byou are now\b[^.\n]{0,60}\b(unrestricted|unfiltered|jailbroken|free of|no longer|dan)\b", re.I),
     "role_override", "tells the model it is now a different, unrestricted assistant"),
    (re.compile(r"\b(disregard|forget|drop)\b[^.\n]{0,30}\b(your|all|any|the)\b[^.\n]{0,20}\b(guidelines|instructions|rules|training|safeguards|restrictions)\b", re.I),
     "instruction_override", "asks the model to drop its instructions"),
    (re.compile(r"\b(do not|don't|never|without)\b[^.\n]{0,20}\b(tell|inform|notify|alert|show)\b[^.\n]{0,20}\b(the )?(user|owner|human)\b", re.I),
     "concealment", "asks the model to hide something from the user"),
    (re.compile(r"\b(reveal|print|show|repeat|output|disclose)\b[^.\n]{0,30}\b(system prompt|hidden instructions|initial instructions)\b", re.I),
     "prompt_disclosure", "asks the model to disclose its system prompt"),
    (re.compile(r"(<\|?(system|im_start)\|?>|\[/?INST\]|^\s*system\s*:\s)", re.I | re.M),
     "fake_role_marker", "embeds a chat-role marker to pass as a system message"),
    (re.compile(r"\b(new|updated|real) (system )?(instructions|task)\s*(follow|:)", re.I),
     "instruction_override", "announces replacement instructions"),
)

Scanner = Callable[[Path], dict]


def injection_findings(text: str, file: str = "SKILL.md") -> list[dict]:
    return [{"code": code, "detail": detail, "file": file}
            for pattern, code, detail in INJECTION_PATTERNS if pattern.search(text)]


def fabric_scan(directory: Path) -> dict:
    """SkillSpector through the fabric. A call that did not produce a verdict
    (adapter absent, environment missing, scan failed) is NOT_SCANNED."""
    from friday import fabric

    result = fabric.call(PROVIDER, "scan", path=str(directory))
    if result.status == "succeeded" and isinstance(result.output, dict):
        return result.output
    return {"status": NOT_SCANNED, "detail": (result.error or f"scan {result.status}")[:300]}


def _external(report: dict) -> dict:
    status = str(report.get("status") or "")
    if status not in (CLEAN, BLOCKED):
        status = NOT_SCANNED
    return {"status": status, "scanner": PROVIDER,
            "scanner_version": str(report.get("scanner_version") or ""),
            "report_sha256": str(report.get("report_sha256") or ""),
            "blocking": int(report.get("blocking") or 0),
            "findings": list(report.get("findings") or []),
            "detail": str(report.get("detail") or "")[:300]}


def scan_skill(skill: F.SkillFiles, *, scanner: Scanner | None = None, trust: str = K.TRUST_OWNER) -> dict:
    """The stored scan record for one skill as it is on disk now."""
    from friday.contracts import now_iso

    found: list[dict] = []
    for path in skill.files():
        if path.suffix.lower() in (".png", ".jpg", ".jpeg", ".gif", ".ico", ".onnx", ".bin", ".zip", ".pdf"):
            continue
        relative = path.relative_to(skill.dir).as_posix()
        found.extend(injection_findings(path.read_text(encoding="utf-8", errors="replace"), relative))

    if trust in EXTERNALLY_SCANNED:
        external = _external((scanner or fabric_scan)(skill.dir))
    else:
        external = {"status": NOT_REQUESTED, "scanner": PROVIDER}

    if found or external["status"] == BLOCKED:
        verdict = REJECTED
    elif external["status"] == NOT_SCANNED:
        verdict = NOT_FULLY_SCANNED
    else:
        verdict = PASSED_STATIC
    return {"stage": STAGE, "at": now_iso(), "skill": skill.name, "content_hash": skill.content_hash(),
            "trust": trust, "verdict": verdict,
            "injection": {"status": "FOUND" if found else CLEAN, "findings": found},
            "external": external, "runtime_safe": False, "note": NOTE}
