"""CAP-MKT-006: resolve a missing capability without installing blindly.

The flow is deliberately narrow: ask FRIDAY's current catalogue first, then
look at marketplace metadata, and only materialise matching marketplace
entries into the existing CAP-MKT-002 scan gate. A candidate can proceed only
through explicit approval, sandbox/test, activation, and retry gates supplied
by the caller.
"""
from __future__ import annotations

import inspect
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable

from friday import capability_sources as sources
from friday import forge

LOCAL_MATCH = "LOCAL_MATCH"
CANDIDATE_READY = "CANDIDATE_READY"
NO_CANDIDATE = "NO_CANDIDATE"
POLICY_APPROVAL_REQUIRED = "POLICY_APPROVAL_REQUIRED"
SANDBOX_NOT_CONFIGURED = "SANDBOX_NOT_CONFIGURED"
SANDBOX_TEST_FAILED = "SANDBOX_TEST_FAILED"
ACTIVATION_NOT_CONFIGURED = "ACTIVATION_NOT_CONFIGURED"
ACTIVATION_FAILED = "ACTIVATION_FAILED"
CANDIDATE_ACTIVATED = "CANDIDATE_ACTIVATED"
ORIGINAL_TASK_FAILED = "ORIGINAL_TASK_FAILED"
ORIGINAL_TASK_RETRIED = "ORIGINAL_TASK_RETRIED"

LocalCatalogue = Callable[..., list[dict]]
Scanner = Callable[[Path], forge.ScanReport]
ApprovalGate = Callable[[sources.Candidate], "GateDecision"]
SandboxGate = Callable[[sources.Candidate, str], "GateDecision"]
Activator = Callable[[sources.Candidate], Any]
OriginalRetry = Callable[[str, Any], Any]


@dataclass(frozen=True)
class SourceSearch:
    source: str
    health: sources.SourceHealth
    listed: int = 0
    matched: int = 0
    refused: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True)
class GateDecision:
    allowed: bool
    reason: str = ""


@dataclass
class CapabilitySearchResult:
    query: str
    decision: str
    message: str
    local_matches: list[dict] = field(default_factory=list)
    marketplace_candidates: list[sources.Candidate] = field(default_factory=list)
    sources: list[SourceSearch] = field(default_factory=list)
    activation: Any = None
    retry_result: Any = None
    # True only when the caller supplied both a sandbox/test gate and an
    # activator, so an approval could actually lead to the candidate being used.
    activation_wired: bool = False
    # Class name of an activation/retry exception. The exception text stays in
    # `message` (structured output) and is never spoken.
    error_type: str = ""

    def passing_candidates(self) -> list[sources.Candidate]:
        return [candidate for candidate in self.marketplace_candidates
                if candidate.admitted]


class _EntrySubsetSource(sources.CapabilitySource):
    """A source view that harvests only entries already matched as metadata."""

    def __init__(self, parent: sources.CapabilitySource,
                 entries: list[sources.CatalogEntry]):
        self.parent = parent
        self.entries = entries
        self.kind = parent.kind

    def health(self) -> sources.SourceHealth:
        return self.parent.health()

    def catalog(self) -> list[sources.CatalogEntry]:
        return list(self.entries)

    def fetch(self, entry: sources.CatalogEntry, destination: Path) -> None:
        self.parent.fetch(entry, destination)


def default_marketplace_sources() -> list[sources.CapabilitySource]:
    """Marketplace-like sources. Network-backed sources may be unavailable."""
    return [
        sources.SkillsRestSource(),
        sources.GitHubSource(),
        sources.NvidiaSkillsSource(),
        sources.McpRegistrySource(),
        sources.PluginSource(),
    ]


def search_for_missing_capability(
    query: str,
    *,
    local_catalogue: LocalCatalogue,
    marketplace_sources: Iterable[sources.CapabilitySource] | None = None,
    quarantine_root: Path,
    limit: int = 6,
    scanner: Scanner = forge.scan_import,
) -> CapabilitySearchResult:
    """Search local capabilities, then gated marketplace candidates.

    `local_catalogue` is usually `capability_router.Router.search`. Marketplace
    entries are metadata until they match the query; then the matching entries
    are harvested into quarantine and scanned. Source failures are isolated:
    one broken source is reported as unavailable while later sources continue.
    """
    text = (query or "").strip()
    local_matches = _search_local(local_catalogue, text, limit=limit) if text else []
    if local_matches:
        return CapabilitySearchResult(
            query=text,
            decision=LOCAL_MATCH,
            message="FRIDAY already has a matching local capability",
            local_matches=local_matches,
        )

    observations: list[SourceSearch] = []
    candidates: list[sources.Candidate] = []
    for source in marketplace_sources or default_marketplace_sources():
        try:
            health = source.health()
        except Exception as exc:  # noqa: BLE001 - one bad source is not global failure
            observations.append(SourceSearch(
                source=getattr(source, "kind", ""),
                health=sources.SourceHealth(sources.UNAVAILABLE, str(exc)),
            ))
            continue
        if health.state != sources.AVAILABLE:
            observations.append(SourceSearch(source=source.kind, health=health))
            continue
        try:
            entries = source.catalog()
        except Exception as exc:  # noqa: BLE001 - catalogue bugs are isolated per source
            observations.append(SourceSearch(
                source=source.kind,
                health=sources.SourceHealth(sources.UNAVAILABLE, str(exc)),
            ))
            continue

        matched = _rank_entries(text, entries, limit=limit)
        if not matched:
            observations.append(SourceSearch(
                source=source.kind,
                health=health,
                listed=len(entries),
            ))
            continue

        try:
            harvested = sources.harvest(
                _EntrySubsetSource(source, matched),
                quarantine_root,
                scanner=scanner,
            )
        except Exception as exc:  # noqa: BLE001 - scanner/source crashes fail closed
            observations.append(SourceSearch(
                source=source.kind,
                health=sources.SourceHealth(sources.UNAVAILABLE, str(exc)),
                listed=len(entries),
                matched=len(matched),
                refused=tuple((entry.name, str(exc)) for entry in matched),
            ))
            continue
        observations.append(SourceSearch(
            source=source.kind,
            health=harvested.health,
            listed=len(entries),
            matched=len(matched),
            refused=tuple(harvested.refused),
        ))
        candidates.extend(harvested.candidates)

    passing = [candidate for candidate in candidates if candidate.admitted]
    if passing:
        noun = "candidate is" if len(passing) == 1 else "candidates are"
        return CapabilitySearchResult(
            query=text,
            decision=CANDIDATE_READY,
            message=f"{len(passing)} gated {noun} ready for policy approval",
            marketplace_candidates=candidates,
            sources=observations,
        )
    return CapabilitySearchResult(
        query=text,
        decision=NO_CANDIDATE,
        message="no local capability or passing marketplace candidate matched",
        marketplace_candidates=candidates,
        sources=observations,
    )


def resolve_missing_capability(
    original_task: str,
    *,
    local_catalogue: LocalCatalogue,
    marketplace_sources: Iterable[sources.CapabilitySource] | None = None,
    quarantine_root: Path,
    limit: int = 6,
    scanner: Scanner = forge.scan_import,
    approval_gate: ApprovalGate | None = None,
    sandbox_gate: SandboxGate | None = None,
    activator: Activator | None = None,
    retry_original: OriginalRetry | None = None,
) -> CapabilitySearchResult:
    """Search, scan, approve, sandbox/test, activate, then retry the task.

    The default approval gate asks the policy engine and fails closed. A
    missing sandbox gate or activator is reported as SANDBOX_NOT_CONFIGURED or
    ACTIVATION_NOT_CONFIGURED, never as a failed test or activation. Tests and
    explicit runtime approval paths inject the concrete approval, sandbox,
    activation and retry functions. This keeps marketplace discovery from silently installing or
    activating anything.
    """
    result = search_for_missing_capability(
        original_task,
        local_catalogue=local_catalogue,
        marketplace_sources=marketplace_sources,
        quarantine_root=quarantine_root,
        limit=limit,
        scanner=scanner,
    )
    if result.decision != CANDIDATE_READY:
        return result
    result.activation_wired = sandbox_gate is not None and activator is not None

    candidate = result.passing_candidates()[0]
    approval = (approval_gate or _default_approval_gate)(candidate)
    if not approval.allowed:
        result.decision = POLICY_APPROVAL_REQUIRED
        result.message = approval.reason or "policy approval required before activation"
        return result

    # A missing gate is not a failed gate: no sandbox test ran, so none failed.
    if sandbox_gate is None:
        result.decision = SANDBOX_NOT_CONFIGURED
        result.message = "no sandbox/test gate is configured"
        return result
    sandbox = sandbox_gate(candidate, result.query)
    if not sandbox.allowed:
        result.decision = SANDBOX_TEST_FAILED
        result.message = sandbox.reason or "sandbox/test gate failed"
        return result

    if activator is None:
        result.decision = ACTIVATION_NOT_CONFIGURED
        result.message = "no activator is configured"
        return result
    try:
        result.activation = activator(candidate)
    except Exception as exc:  # noqa: BLE001 - activation refuses, it does not crash the turn
        result.decision = ACTIVATION_FAILED
        result.message = f"activation failed: {exc}"
        result.error_type = type(exc).__name__
        return result

    if retry_original is None:
        result.decision = CANDIDATE_ACTIVATED
        result.message = "candidate activated; no retry of the original task is configured"
        return result
    try:
        result.retry_result = retry_original(original_task, result.activation)
    except Exception as exc:  # noqa: BLE001 - a failed retry is reported, not claimed as done
        result.decision = ORIGINAL_TASK_FAILED
        result.message = f"candidate activated but the original task failed: {exc}"
        result.error_type = type(exc).__name__
        return result
    result.decision = ORIGINAL_TASK_RETRIED
    result.message = "candidate activated and original task retried"
    return result


def spoken_summary(result: CapabilitySearchResult) -> str:
    """What FRIDAY may truthfully say, built from what the search really did.

    Every decision `resolve_missing_capability` can return has its own
    sentence; an unknown decision raises rather than falling back to a claim
    that may be false. Gate reasons and exception text stay in the structured
    result; speech names only the gate and, for a crash, the exception class.
    """
    if result.decision == LOCAL_MATCH:
        return "I already have a matching capability in my own catalogue."
    if result.decision == NO_CANDIDATE:
        return _no_candidate_summary(result)
    passing = result.passing_candidates()
    if result.decision == CANDIDATE_READY:
        return _approval_summary(", ".join(c.entry.name for c in passing),
                                 result.activation_wired)
    # Past the scan, the gates ran on the first passing candidate only.
    name = passing[0].entry.name if passing else "the candidate"
    if result.decision == POLICY_APPROVAL_REQUIRED:
        return _approval_summary(name, result.activation_wired)
    if result.decision == SANDBOX_NOT_CONFIGURED:
        return (f"I found {name} on a marketplace source and it passed the safety "
                "scan, but I have no sandbox to test it in yet, so I have not "
                "installed or used it and I have not done the task.")
    if result.decision == SANDBOX_TEST_FAILED:
        return (f"I found {name} on a marketplace source and it passed the safety "
                "scan, but it did not pass the sandbox test. Nothing is installed "
                "or active and I have not done the task.")
    if result.decision == ACTIVATION_NOT_CONFIGURED:
        return (f"{name} passed the safety scan, approval and sandbox test, but "
                "activating marketplace capabilities is not wired in this build, "
                "so it is not active and I have not done the task.")
    if result.decision == ACTIVATION_FAILED:
        return (f"{name} passed the safety scan, approval and sandbox test, but "
                f"activating it failed with {_error_label(result)}. It is not "
                "active and I have not done the task.")
    if result.decision == CANDIDATE_ACTIVATED:
        return (f"I activated {name} after the safety scan, approval and sandbox "
                "test, but I have not run your original task with it yet.")
    if result.decision == ORIGINAL_TASK_FAILED:
        return (f"I activated {name}, but running your original task with it "
                f"failed with {_error_label(result)}.")
    if result.decision == ORIGINAL_TASK_RETRIED:
        return (f"I activated {name} after the safety scan, approval and sandbox "
                "test, and ran your original task with it.")
    raise ValueError(f"no spoken summary for capability search decision {result.decision!r}")


def _approval_summary(names: str, activation_wired: bool) -> str:
    found = (f"I found {names} on a marketplace source and it passed the safety "
             "scan. Nothing is installed or active")
    if activation_wired:
        return (found + ": it needs your approval, then a sandbox test, before I "
                "would use it for this task.")
    return (found + ", and sandboxing and activating marketplace capabilities is "
            "not wired in this build, so even with your approval I could not use "
            "it for this task yet.")


def _error_label(result: CapabilitySearchResult) -> str:
    return f"an error ({result.error_type})" if result.error_type else "an error"


def _no_candidate_summary(result: CapabilitySearchResult) -> str:
    reached = [s for s in result.sources if s.health.state == sources.AVAILABLE]
    if not reached:
        return ("I do not have that capability active, and I could not reach any "
                "marketplace source to look for it.")
    spoken = (f"I do not have that capability active. I searched {len(reached)} "
              f"marketplace source{'s' if len(reached) != 1 else ''} and none had "
              "a safe match.")
    if len(reached) < len(result.sources):
        spoken += " Some sources could not be reached."
    return spoken


def _default_approval_gate(candidate: sources.Candidate) -> GateDecision:
    try:
        from friday import policy

        verdict = policy.default_engine.decide("capability_marketplace_activate")
        if verdict.allowed:
            return GateDecision(True, verdict.reason)
        return GateDecision(False, verdict.reason)
    except Exception as exc:  # noqa: BLE001 - a broken policy read is not approval
        return GateDecision(False, f"policy approval unavailable: {exc}")


def _search_local(local_catalogue: LocalCatalogue, query: str, *,
                  limit: int) -> list[dict]:
    """Call the catalogue with the limit it accepts, decided from its signature.

    A TypeError raised inside the catalogue is a real error and propagates; the
    catalogue is never run twice to find out how to call it.
    """
    try:
        params = list(inspect.signature(local_catalogue).parameters.values())
    except (TypeError, ValueError):
        return list(local_catalogue(query, limit=limit))
    kinds = inspect.Parameter
    named = next((p for p in params if p.name == "limit"), None)
    if (named is not None and named.kind != kinds.POSITIONAL_ONLY) or any(
            p.kind == kinds.VAR_KEYWORD for p in params):
        return list(local_catalogue(query, limit=limit))
    positional = [p for p in params
                  if p.kind in (kinds.POSITIONAL_ONLY, kinds.POSITIONAL_OR_KEYWORD)]
    if len(positional) >= 2 or any(p.kind == kinds.VAR_POSITIONAL for p in params):
        return list(local_catalogue(query, limit))
    return list(local_catalogue(query))


def _rank_entries(query: str, entries: list[sources.CatalogEntry], *,
                  limit: int) -> list[sources.CatalogEntry]:
    terms = _terms(query)
    if not terms:
        return []
    scored: list[tuple[int, sources.CatalogEntry]] = []
    for entry in entries:
        haystack = _terms(" ".join((entry.name, entry.category, entry.ref)))
        score = len(terms & haystack) * 10 + int(entry.popularity)
        if score > 0 and terms & haystack:
            scored.append((score, entry))
    scored.sort(key=lambda pair: (-pair[0], pair[1].source, pair[1].name))
    return [entry for _, entry in scored[:limit]]


def _terms(text: str) -> set[str]:
    return {part for part in "".join(
        ch.lower() if ch.isalnum() else " " for ch in text
    ).split() if len(part) > 2}
