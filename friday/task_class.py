"""
Task complexity classification (PRD v3.1 FR-002, P0).

Every request is placed in exactly one of six classes before anything is
routed, so a trivial request never reaches a coding executor and a critical
one never runs on the economy path:

    TRIVIAL       one deterministic action, no model reasoning needed
    SIMPLE        one capability call or a direct answer; fast model at most
    STANDARD      bounded multi-step work inside one domain
    COMPLEX       cross-file / cross-tool implementation with verification
    LONG_RUNNING  many steps, checkpoints, may span restarts
    CRITICAL      touches security, money, credentials, deletion, deploys

The class decides three things downstream: the route level
(`execution_economics.choose_route` vocabulary), the token budget profile
(`model_gateway.BUDGETS`) and whether the objective is admitted with a
plan + verification criteria before consequential execution (FR-003).

Deterministic, no model calls: the class is a property of the words and
the measured shape of the request (clause count, code references,
acceptance criteria), which is what makes the >=95% agreement benchmark in
tests/test_task_class.py meaningful rather than a coin toss.

Matching is by WORD, not substring: the first version matched "release"
inside "release date", "payment" inside "payment module", "schedule"
inside "scheduler" and "production" inside "production health", and
classified four benign requests as CRITICAL. A destructive word has to be
the verb or object of the request, not a fragment of another word.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from friday import execution_economics as ee
from friday.model_gateway import (COMPLEX, CRITICAL, LONG_RUNNING, SIMPLE,
                                  STANDARD, TASK_CLASSES, TRIVIAL)

__all__ = ["TRIVIAL", "SIMPLE", "STANDARD", "COMPLEX", "LONG_RUNNING",
           "CRITICAL", "TASK_CLASSES", "Classification", "classify",
           "route_for", "risk_tier_for"]


def _words(phrases: tuple[str, ...]) -> re.Pattern:
    """A pattern that matches any phrase as whole words, in order."""
    alts = "|".join(r"\b" + re.escape(p).replace(r"\ ", r"\s+") + r"\b" for p in phrases)
    return re.compile(alts, re.I)


#: Verbs/shapes that are one deterministic device or media action.
_TRIVIAL_VERBS = re.compile(
    r"^(?:please\s+)?(?:open|launch|start|play|pause|resume|stop|mute|unmute|"
    r"lock|close|minimi[sz]e|maximi[sz]e|focus|show|what time|what's the time|"
    r"set (?:the )?(?:volume|brightness)|turn (?:up|down)|next (?:song|track)|"
    r"skip|take a screenshot|screenshot)\b", re.I)

#: A second action verb after the first: "open X and play Y" is two
#: actions, which is STANDARD, not TRIVIAL.
_SECOND_ACTION = re.compile(
    r"\b(?:and|then)\s+(?:open|launch|start|play|pause|stop|close|find|search|"
    r"read|write|create|delete|send|show|tell|check|run)\b", re.I)

#: Words that mean "keep going for a long time / on a schedule".
_LONG_RUNNING = _words((
    "every morning", "every day", "every hour", "every week", "each morning",
    "daily", "weekly", "hourly", "overnight", "keep monitoring",
    "keep watching", "monitor", "until it", "long-running", "long running",
    "for the next", "continuously", "recurring", "schedule", "whenever",
    "watch for",
))

#: Explicitly destructive, financial, publishing or privileged phrasing.
#: Whole words only (see module docstring).
_CRITICAL = _words((
    "delete", "permanently", "wipe", "format the", "drop table", "rm -rf",
    "purchase", "buy", "pay", "transfer money", "send money", "checkout",
    "publish", "post to", "post it", "send the email", "send email",
    "send it to", "deploy", "to production", "password", "passwords",
    "credential", "credentials", "api key", "api keys", "secret", "secrets",
    "token", "tokens", "security setting", "security settings", "firewall",
    "sudo", "admin", "scan", "pentest", "nmap", "exploit", "uninstall",
    "reinstall", "rotate", "revoke", "grant access", "chmod", "registry",
))

#: Multi-file / verification-heavy implementation phrasing.
_COMPLEX = _words((
    "fix the bug", "find why", "figure out why", "root cause", "crash",
    "crashes", "refactor", "migrate", "implement", "build a", "build the",
    "add a feature", "end to end", "end-to-end", "prove it works",
    "with tests", "add tests", "add a regression test", "regression test",
    "integration", "across", "the whole", "entire", "multi-file", "pipeline",
    "storefront", "launch plan", "business plan", "challenge my",
    "storyboard", "go-to-market", "go to market", "reel", "video",
))

#: Bounded, single-domain work that still needs a plan.
_STANDARD = _words((
    "research", "compare", "summarise", "summarize", "summary", "write a",
    "draft", "write me", "look up", "find me", "search for", "read this",
    "extract", "convert", "organise", "organize", "rename", "sort",
    "translate", "explain", "review", "audit", "check whether", "check if",
    "update the", "edit the", "change the", "create a note", "make a note",
    "remind me", "create a file", "suggest",
))


@dataclass(frozen=True)
class Classification:
    task_class: str
    reason: str
    signals: tuple[str, ...]
    clauses: int
    economics: ee.TaskEconomics

    def to_dict(self) -> dict:
        return {"task_class": self.task_class, "reason": self.reason,
                "signals": list(self.signals), "clauses": self.clauses,
                "route": route_for(self.task_class),
                "risk_tier": risk_tier_for(self.task_class)}


def _clauses(text: str) -> int:
    parts = [p for p in re.split(r",|\bthen\b|\band then\b|;|\. ", text) if p.strip()]
    return max(1, len(parts))


def _hits(pattern: re.Pattern, text: str) -> tuple[str, ...]:
    return tuple(dict.fromkeys(m.group(0).lower() for m in pattern.finditer(text)))


def classify(text: str, *, code_refs: int = 0, acceptance: int = 0) -> Classification:
    """One deterministic pass. Precedence, highest first: CRITICAL (a
    destructive word anywhere wins, whatever else the request is),
    LONG_RUNNING, COMPLEX, STANDARD, TRIVIAL, SIMPLE."""
    raw = (text or "").strip()
    lowered = " ".join(raw.lower().split())
    econ = ee.classify_task(raw, code_refs=code_refs, acceptance=acceptance)
    clauses = _clauses(lowered)

    critical = _hits(_CRITICAL, lowered)
    if critical:
        return Classification(CRITICAL, "destructive, financial, publishing or "
                              "security wording: exact-action approval path",
                              critical, clauses, econ)
    long_running = _hits(_LONG_RUNNING, lowered)
    if long_running:
        return Classification(LONG_RUNNING, "recurring or open-ended work: "
                              "checkpoints and a schedule/budget",
                              long_running, clauses, econ)
    complex_ = _hits(_COMPLEX, lowered)
    if complex_ or econ.kind == "multi_stream" or (
            econ.kind == "code_change" and (clauses >= 3 or code_refs >= 3)):
        return Classification(COMPLEX, "cross-file or verification-heavy "
                              "implementation: executor with independent "
                              "verification", complex_ or (econ.kind,), clauses, econ)
    if (_TRIVIAL_VERBS.match(lowered) and clauses == 1 and len(lowered) <= 80
            and not _SECOND_ACTION.search(lowered)):
        return Classification(TRIVIAL, "one deterministic device/media action",
                              (lowered.split()[0],), clauses, econ)
    standard = _hits(_STANDARD, lowered)
    if (standard or clauses >= 2 or _SECOND_ACTION.search(lowered)
            or econ.kind in ("research", "inspection", "code_change")):
        return Classification(STANDARD, "bounded multi-step work in one domain",
                              standard or (econ.kind,), clauses, econ)
    return Classification(SIMPLE, "one capability call or a direct answer",
                          (econ.kind,), clauses, econ)


#: Class -> the cheapest sufficient route level (FR-075 / PRD 1.4 "least
#: sufficient mechanism"). CRITICAL is not a route: it is STANDARD work
#: that additionally requires exact-action approval, so it maps to the
#: single-executor route with the approval flag carried by risk_tier.
_ROUTE = {
    TRIVIAL: ee.DETERMINISTIC,
    SIMPLE: ee.FRIDAY_DIRECT,
    STANDARD: ee.FRIDAY_DIRECT,
    COMPLEX: ee.HERMES_SINGLE,
    LONG_RUNNING: ee.HERMES_SINGLE,
    CRITICAL: ee.HERMES_SINGLE,
}

#: Class -> default PRD 4.11 risk tier before the policy engine looks at
#: the concrete action. The policy engine is authoritative per action; this
#: is the objective-level floor.
_RISK = {
    TRIVIAL: "R1", SIMPLE: "R0", STANDARD: "R1", COMPLEX: "R1",
    LONG_RUNNING: "R1", CRITICAL: "R3",
}


def route_for(task_class: str) -> str:
    return _ROUTE[task_class]


def risk_tier_for(task_class: str) -> str:
    return _RISK[task_class]
