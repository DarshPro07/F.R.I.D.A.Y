"""
Proof-of-work contracts: Verification, Artifact, ActionResult, Run.

The failure mode these exist to make impossible:

    User:  "Let's build an Arc Reactor design."
    Agent: "Boss, the Arc Reactor design is ready."   <- nothing was built

This is not hypothetical. `Mark-L/actions/open_app.py` ships it today: its
launch helper returns True because `subprocess.Popen` did not raise, and its
last-resort branch types into the Start Menu and returns True unconditionally
- then the caller says "Opened Spotify." A prompt rule cannot fix that,
because the lie is produced below the prompt.

So the guarantee is structural: **an ActionResult cannot be constructed with
status "succeeded" unless it carries Verification evidence.** There is no code
path that yields an unverified success. A tool author who wants to claim
success must first say how they checked.

`friday/honesty.py` consumes these to gate what the agent is allowed to say.
"""

from __future__ import annotations

import os
import re
import uuid
from dataclasses import dataclass, field, fields, replace
from datetime import datetime, timezone
from pathlib import PurePath
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from friday.privacy_budget import PrivacyBudget

# --- statuses --------------------------------------------------------------

QUEUED = "queued"
RUNNING = "running"
SUCCEEDED = "succeeded"
FAILED = "failed"
CANCELLED = "cancelled"
PARTIAL = "partial"

#: The machine accepted the request; the thing has not happened yet.
#:
#: `ExitWindowsEx` returning non-zero means a shutdown was *initiated*, and any
#: application can still stop it. `LockWorkStation` returning true means the
#: request reached the input desktop. This is not a gentler SUCCEEDED - it is a
#: different fact, and the difference is the whole reason the state exists.
INITIATED = "initiated"

#: Evidence now says it happened. Reached by reconciliation after the fact,
#: never by the call that made the request.
OBSERVED = "observed"

#: Accepted, and then it did not happen - something stopped it, or the person
#: called it back.
NOT_CARRIED_OUT = "not_carried_out"

#: Friday lacks the privilege. Distinct from the machine refusing: an
#: unadjusted SeShutdownPrivilege makes every power call fail, and reporting
#: that as "Windows said no" blames the wrong party.
NOT_PERMITTED = "not_permitted"

#: This machine cannot do it - no hibernate file, no sleep state.
UNSUPPORTED = "unsupported"

#: The capability exists but is not wired up on this path.
#:
#: Distinct from UNSUPPORTED, which is about the machine, and from a
#: LookupError, which says the capability does not exist at all. This one means
#: Friday has it and cannot run it *here* - the case a durable objective meets
#: when a capability's only implementation is its MCP adapter and there is no
#: verifiable result underneath. Saying "no such capability" about that would
#: be false, and manufacturing a success would be worse.
NOT_CONFIGURED = "not_configured"

ACTION_STATUSES = (QUEUED, RUNNING, SUCCEEDED, FAILED, CANCELLED, PARTIAL,
                   INITIATED, OBSERVED, NOT_CARRIED_OUT, NOT_PERMITTED,
                   UNSUPPORTED, NOT_CONFIGURED)

#: Statuses that permit a completion claim ("opened", "created", ...).
#:
#: PARTIAL is deliberately excluded - partial work is described, not claimed.
#: INITIATED is excluded for the same reason and a sharper one: a request that
#: was accepted looks exactly like one that succeeded, and only this line stops
#: Friday saying "restarted" about a machine that is still running.
CLAIMABLE = (SUCCEEDED, OBSERVED)

# Run states (§7). Broader than action statuses: a Run spans many actions.
RUN_STATES = (
    "received",
    "planning",
    "working",
    "waiting_permission",
    "completed",
    "partial",
    "failed",
    "cancelled",
)


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def new_run_id() -> str:
    return f"RUN-{uuid.uuid4().hex[:12]}"


class ContractError(ValueError):
    """A result violates the proof-of-work contract."""


# --- verification ----------------------------------------------------------


@dataclass(frozen=True)
class Verification:
    """
    How we know the action actually happened.

    ``method`` names the check, ``evidence`` records what was concretely
    observed. "I called Popen and it did not raise" is not evidence that an
    application opened; "process 'Spotify.exe' pid=1234 found after launch"
    is. Anything vague enough to be untestable belongs in ``output``, not
    here.
    """

    method: str
    evidence: str
    checked_at: str = field(default_factory=now_iso)

    def __post_init__(self) -> None:
        if not self.method.strip():
            raise ContractError("Verification.method must not be empty")
        if not self.evidence.strip():
            raise ContractError("Verification.evidence must not be empty")


# --- artifacts -------------------------------------------------------------


ARTIFACT_TYPES = (
    "file", "document", "image", "screenshot", "research", "code",
    "cad", "stl", "gcode", "task_plan", "requirements", "download",
)


@dataclass(frozen=True)
class Artifact:
    """Something that now exists because ADA did the work."""

    artifact_id: str
    run_id: str
    type: str
    title: str
    path_or_uri: str
    producer: str
    verification: Verification
    created_at: str = field(default_factory=now_iso)
    metadata: dict = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.type not in ARTIFACT_TYPES:
            raise ContractError(
                f"unknown artifact type {self.type!r}; known: {list(ARTIFACT_TYPES)}"
            )
        if not self.path_or_uri.strip():
            raise ContractError("Artifact.path_or_uri must not be empty")


def new_artifact(
    *, run_id: str, type: str, title: str, path_or_uri: str,
    producer: str, verification: Verification, metadata: dict | None = None,
) -> Artifact:
    return Artifact(
        artifact_id=f"ART-{uuid.uuid4().hex[:12]}",
        run_id=run_id, type=type, title=title, path_or_uri=path_or_uri,
        producer=producer, verification=verification, metadata=metadata or {},
    )


# --- action results --------------------------------------------------------


@dataclass(frozen=True)
class Correlation:
    """
    The key that ties a piece of evidence to the one action it is about.

    An ActionResult records the key it was produced under; a Run declares the
    key its work must carry. `honesty.audit` holds every result to the run's
    declared key without being asked, refuses evidence recorded for anything
    else, and evidence that recorded nothing cannot match a declared key at
    all. A recorded `target` is the one thing the action was about, so a
    filename that merely appears in its evidence text cannot be claimed.
    `timestamp` is when the evidence was produced; it is compared as a time,
    and the result's own started/completed stamps are checked beside it.
    """

    workspace: str = ""
    objective: str = ""
    version: str = ""
    action: str = ""
    target: str = ""
    executor: str = ""
    operation: str = ""
    timestamp: str = ""

    KEYS = ("workspace", "objective", "version", "action", "target",
            "executor", "operation")


@dataclass(frozen=True)
class ActionResult:
    """
    The envelope every externally meaningful tool operation returns.

    Invariant enforced in __post_init__:
        status == "succeeded"  =>  verification is not None

    That single rule is what makes fabricated completion a construction-time
    error rather than a plausible sentence.
    """

    run_id: str
    tool_id: str
    status: str
    started_at: str
    completed_at: str | None = None
    output: object = None
    error: str | None = None
    artifacts: tuple[Artifact, ...] = ()
    side_effects: tuple[str, ...] = ()
    verification: Verification | None = None
    correlation: Correlation | None = None

    def __post_init__(self) -> None:
        if self.status not in ACTION_STATUSES:
            raise ContractError(
                f"unknown status {self.status!r}; known: {list(ACTION_STATUSES)}"
            )
        if self.status == SUCCEEDED and self.verification is None:
            raise ContractError(
                f"{self.tool_id}: cannot report 'succeeded' without Verification. "
                "State how success was checked, or use PARTIAL/FAILED."
            )
        if self.status == FAILED and not (self.error or "").strip():
            raise ContractError(f"{self.tool_id}: 'failed' requires an error message")
        for artifact in self.artifacts:
            if artifact.run_id != self.run_id:
                raise ContractError(
                    f"artifact {artifact.artifact_id} belongs to run "
                    f"{artifact.run_id}, not {self.run_id}"
                )

    # -- claims -------------------------------------------------------------

    @property
    def may_claim_completion(self) -> bool:
        """Whether the agent is allowed to say 'opened' / 'created' / 'done'."""
        return self.status in CLAIMABLE

    @property
    def is_terminal(self) -> bool:
        # INITIATED is deliberately absent: Friday's part is over, but the
        # thing it asked for has not resolved, and reconciliation will settle
        # it later. Calling it terminal would close the book on an open fact.
        return self.status in (SUCCEEDED, FAILED, CANCELLED, PARTIAL, OBSERVED,
                               NOT_CARRIED_OUT, NOT_PERMITTED, UNSUPPORTED)

    def honest_summary(self) -> str:
        """A sentence the agent can safely say, derived from status alone."""
        if self.status == SUCCEEDED:
            return f"{self.tool_id} succeeded ({self.verification.evidence})"
        if self.status == FAILED:
            return f"{self.tool_id} failed: {self.error}"
        if self.status == PARTIAL:
            return f"{self.tool_id} partially completed: {self.error or 'incomplete'}"
        if self.status == CANCELLED:
            return f"{self.tool_id} was cancelled"
        # The wording here is the point. "requested" and "did" are different
        # sentences, and this is the only place that guarantees the first one
        # cannot be spoken as the second.
        if self.status == INITIATED:
            return (f"{self.tool_id} was requested and accepted - it has not "
                    f"happened yet")
        if self.status == OBSERVED:
            return f"{self.tool_id} happened ({self.verification.evidence})" \
                if self.verification else f"{self.tool_id} happened"
        if self.status == NOT_CARRIED_OUT:
            return (f"{self.tool_id} was requested and did not happen: "
                    f"{self.error or 'something stopped it'}")
        if self.status == NOT_PERMITTED:
            return f"{self.tool_id} was not permitted: {self.error}"
        if self.status == UNSUPPORTED:
            return f"{self.tool_id} is not available on this machine"
        return f"{self.tool_id} is still working"

    def finish(self, **changes) -> "ActionResult":
        """Return a terminal copy, stamping completed_at if absent."""
        changes.setdefault("completed_at", now_iso())
        return replace(self, **changes)

    def to_dict(self) -> dict:
        """
        Wire format. `may_claim_completion` is included deliberately: the
        model reads this, and the permission to say "done" should be an
        explicit field rather than something it infers from `status`.
        """
        return {
            "run_id": self.run_id,
            "tool_id": self.tool_id,
            "status": self.status,
            "may_claim_completion": self.may_claim_completion,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "output": self.output,
            "error": self.error,
            "side_effects": list(self.side_effects),
            "verification": (
                {"method": self.verification.method,
                 "evidence": self.verification.evidence,
                 "checked_at": self.verification.checked_at}
                if self.verification else None
            ),
            "artifacts": [
                {"artifact_id": a.artifact_id, "type": a.type, "title": a.title,
                 "path_or_uri": a.path_or_uri, "producer": a.producer}
                for a in self.artifacts
            ],
        }


# -- constructors: the only ergonomic way to build each status --------------


def started(run_id: str, tool_id: str, *,
            correlation: Correlation | None = None) -> ActionResult:
    return ActionResult(
        run_id=run_id, tool_id=tool_id, status=RUNNING, started_at=now_iso(),
        correlation=correlation,
    )


def succeeded(
    prior: ActionResult, *, verification: Verification, output: object = None,
    artifacts: tuple[Artifact, ...] = (), side_effects: tuple[str, ...] = (),
    correlation: Correlation | None = None,
) -> ActionResult:
    return prior.finish(
        status=SUCCEEDED, verification=verification, output=output,
        artifacts=artifacts, side_effects=side_effects,
        correlation=correlation or prior.correlation,
    )


def failed(prior: ActionResult, error: str, *, side_effects: tuple[str, ...] = (),
           correlation: Correlation | None = None) -> ActionResult:
    return prior.finish(status=FAILED, error=error, side_effects=side_effects,
                        correlation=correlation or prior.correlation)


def partial(
    prior: ActionResult, reason: str, *, output: object = None,
    artifacts: tuple[Artifact, ...] = (),
) -> ActionResult:
    return prior.finish(
        status=PARTIAL, error=reason, output=output, artifacts=artifacts
    )


def cancelled(prior: ActionResult) -> ActionResult:
    return prior.finish(status=CANCELLED)


# --- runs ------------------------------------------------------------------


#: The objective came from the person Friday is talking to.
PERSON = "PERSON"

#: The objective was derived from something Friday read - a web page, a
#: document, an email, another tool's output. Untrusted observation, however
#: reasonable it sounds.
READ_MATERIAL = "READ_MATERIAL"

PROVENANCES = (PERSON, READ_MATERIAL)


@dataclass
class Run:
    """A user request and everything done in service of it."""

    run_id: str
    request: str
    state: str = "received"
    capability: str | None = None
    created_at: str = field(default_factory=now_iso)
    updated_at: str = field(default_factory=now_iso)
    results: list[ActionResult] = field(default_factory=list)
    error: str | None = None
    #: Whether there is a person who could answer a question about this run.
    #:
    #: False for a scheduled automation at three in the morning. A capability
    #: that needs a yes then refuses outright rather than leaving a
    #: confirmation open - a live authorisation for something destructive,
    #: waiting all night for whoever speaks next to say something that sounds
    #: like agreement, is worse than a refusal.
    attended: bool = True
    #: Where the objective came from, fixed when the run is created.
    #:
    #: Deliberately established here rather than judged at the point of
    #: action. Asking "does this instruction look like it came from the
    #: person?" at the moment of a shutdown means asking the model to assess
    #: whether the model has been manipulated, which is not a question it is
    #: positioned to answer. Recorded once, at the only moment anybody
    #: actually knows, and carried unchanged.
    provenance: str = PERSON
    #: The execution envelope this action runs inside (PRD v7 section 43),
    #: when it belongs to a durable objective. The capability runtime refuses
    #: a capability outside it, or after it expires.
    envelope: "Envelope | None" = None
    #: The correlation key this run's work must carry (workspace, objective,
    #: version, executor, ...). Declared once, when the run is created, so a
    #: claim is audited against it by default rather than only when a caller
    #: remembers to ask.
    correlation: Correlation | None = None
    #: The store holding the durable state this run belongs to (an object
    #: with `record_side_effect`), when an objective executor built the run.
    #: The side-effect ledger row must land where the completion verifier
    #: will read it - the objective's own store - not in whatever store a
    #: capability happens to resolve by itself.
    ledger: object | None = field(default=None, repr=False, compare=False)

    def __post_init__(self) -> None:
        if self.state not in RUN_STATES:
            raise ContractError(f"unknown run state {self.state!r}")
        if self.provenance not in PROVENANCES:
            raise ContractError(f"unknown provenance {self.provenance!r}")
        if self.envelope is not None and (
                self.envelope.provenance, self.envelope.attended) != (
                self.provenance, self.attended):
            raise ContractError(
                f"{self.run_id}: the run and its envelope disagree about "
                "where it came from or who is there")

    @classmethod
    def create(cls, request: str, capability: str | None = None, *,
               provenance: str = PERSON,
               correlation: Correlation | None = None) -> "Run":
        return cls(run_id=new_run_id(), request=request, capability=capability,
                   provenance=provenance, correlation=correlation)

    @classmethod
    def from_read_material(cls, request: str, capability: str | None = None
                           ) -> "Run":
        """
        A run whose objective came out of something Friday read.

        Named rather than a flag, so that the call site says what it is doing.
        Nothing destructive is reachable from one of these.
        """
        return cls.create(request, capability, provenance=READ_MATERIAL)

    def record(self, result: ActionResult) -> ActionResult:
        if result.run_id != self.run_id:
            raise ContractError(
                f"result belongs to {result.run_id}, not {self.run_id}"
            )
        self.results.append(result)
        self.updated_at = now_iso()
        return result

    def transition(self, state: str, error: str | None = None) -> None:
        if state not in RUN_STATES:
            raise ContractError(f"unknown run state {state!r}")
        if state == "completed" and not self.all_succeeded:
            raise ContractError(
                f"{self.run_id}: cannot complete - "
                f"{len(self.unverified)} action(s) did not succeed"
            )
        self.state = state
        self.error = error
        self.updated_at = now_iso()

    @property
    def artifacts(self) -> list[Artifact]:
        return [a for r in self.results for a in r.artifacts]

    @property
    def unverified(self) -> list[ActionResult]:
        return [r for r in self.results if not r.may_claim_completion]

    @property
    def all_succeeded(self) -> bool:
        return bool(self.results) and not self.unverified


# --- execution envelopes (PRD v7 sections 43-44) ----------------------------
#
# The scope one run works inside, fixed by FRIDAY's policy before it starts
# and held outside model context. A worker cannot widen its own envelope:
# there is no widen, only `narrow`, and a wider scope is a new envelope that
# policy mints. Enforcement lives with each service that acts - the
# capability runtime, the coding CLI's launch, the fabric, netguard - and
# these are the types they share.

@dataclass(frozen=True)
class Limits:
    """
    What one run may consume.

    Defaults are sized for a coding agent on a developer machine, not for a
    build farm. They are deliberately low enough that a runaway is stopped
    while the machine is still usable - the whole point is that Friday's
    voice loop keeps answering while a development run misbehaves.
    """

    #: Kernel-enforced ceiling for the whole tree, not per process.
    memory_mb: int = 4096
    #: Active processes in the job at once; a fork bomb hits this first.
    processes: int = 64
    #: Wall clock for one command. The executor has its own for the run; this
    #: one is what stops `npm test` hanging on a prompt for an hour.
    seconds: float = 1800.0
    #: The largest single artifact `export` will copy out.
    artifact_bytes: int = 64 * 1024 * 1024


NET_NONE = "NONE"
NET_PUBLIC_WEB_READ = "PUBLIC_WEB_READ"
NET_SELECTED_HOSTS = "SELECTED_HOSTS"
NET_PROVIDER_ENDPOINTS = "PROVIDER_ENDPOINTS"
NET_AUTHENTICATED_SERVICES = "AUTHENTICATED_SERVICES"
NET_SECURITY_TARGETS = "SECURITY_TARGETS"
NETWORK_MODES = (NET_NONE, NET_PUBLIC_WEB_READ, NET_SELECTED_HOSTS,
                 NET_PROVIDER_ENDPOINTS, NET_AUTHENTICATED_SERVICES,
                 NET_SECURITY_TARGETS)
#: Modes that name their hosts; each needs at least one.
HOSTED_MODES = (NET_SELECTED_HOSTS, NET_PROVIDER_ENDPOINTS,
                NET_AUTHENTICATED_SERVICES, NET_SECURITY_TARGETS)

#: An `allowed_capabilities` entry meaning "anything not blocked". Explicit
#: on purpose: an empty allowlist allows nothing.
ANY_CAPABILITY = "*"

#: How strongly a part of an envelope holds for one worker (PRD v7 section
#: 43: reported, not assumed). ENFORCED: the worker, its launcher or FRIDAY
#: refuses what is outside it. ADVISORY: the worker is told, and something it
#: may legitimately do (a test suite, a gateway's own tools) is not held to it.
#: DISABLED: the worker is not given the capability at all, so there is nothing
#: left to hold it to (a delegation's commands on a host with no OS sandbox:
#: a program is not confined by file roots, so there they do not run).
ENFORCED = "ENFORCED"
ADVISORY = "ADVISORY"
DISABLED = "DISABLED"


class OutsideEnvelope(Exception):
    """A run's envelope does not allow this launch or delegation at all."""


def _parse_utc(stamp: str) -> datetime | None:
    if not stamp:
        return None
    parsed = datetime.fromisoformat(stamp)
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _host_matches(host: str, pattern: str) -> bool:
    """`.example.com` names the domain and everything under it."""
    host = (host or "").strip().lower().rstrip(".")
    pattern = (pattern or "").strip().lower().rstrip(".")
    if pattern.startswith("."):
        return host == pattern[1:] or host.endswith(pattern)
    return bool(host) and host == pattern


def _pattern_within(child: str, parent: str) -> bool:
    """A child host pattern is no wider than its parent's."""
    child = (child or "").strip().lower().rstrip(".")
    parent = (parent or "").strip().lower().rstrip(".")
    if child.startswith("."):
        # A whole domain fits only under a domain that covers it.
        return parent.startswith(".") and _host_matches(child[1:], parent)
    return _host_matches(child, parent)


def _under(path: str, root: str) -> bool:
    """Lexical containment. The real path is resolved again where the path
    is used (fsjail); this only decides what an envelope may name."""
    if not path or not root:
        return False
    child = PurePath(os.path.normpath(path))
    parent = PurePath(os.path.normpath(root))
    return child == parent or child.is_relative_to(parent)


def _inside_any(path: str, roots: tuple[str, ...]) -> bool:
    """`_under` on resolved paths, for a place about to be used rather than
    named: a link or `..` is judged by where it really lands."""
    if not path:
        return False
    real = os.path.realpath(path)
    return any(_under(real, os.path.realpath(root)) for root in roots if root)


def _later(child: str, parent: str) -> bool:
    """Whether a child expiry outlives its parent's. No expiry is forever."""
    ends = _parse_utc(parent)
    if ends is None:
        return False
    child_ends = _parse_utc(child)
    return child_ends is None or child_ends > ends


@dataclass(frozen=True)
class NetworkScope:
    """
    Where a run may connect (PRD section 44). Objective-bound and expiring;
    revocable through its grant in the store, which netguard consults.
    """

    mode: str = NET_NONE
    hosts: tuple[str, ...] = ()
    objective_id: str = ""
    expires_at: str = ""
    #: The store grant that can revoke this scope; "" until one is issued.
    grant_id: str = ""

    def __post_init__(self) -> None:
        if self.mode not in NETWORK_MODES:
            raise ContractError(f"unknown network mode {self.mode!r}")
        if self.mode in HOSTED_MODES and not self.hosts:
            raise ContractError(
                f"{self.mode} names no hosts; say {NET_NONE} if that is meant")

    def expired(self, now: datetime | None = None) -> bool:
        ends = _parse_utc(self.expires_at)
        return ends is not None and (now or datetime.now(timezone.utc)) >= ends

    def allows(self, host: str, now: datetime | None = None) -> bool:
        """This scope's own answer. Private, loopback and metadata addresses
        stay netguard's to refuse whatever this says; revocation is the
        store's."""
        if self.mode == NET_NONE or not host or self.expired(now):
            return False
        if self.mode == NET_PUBLIC_WEB_READ:
            return True
        return any(_host_matches(host, pattern) for pattern in self.hosts)

    def wider_than(self, parent: "NetworkScope") -> list[str]:
        """What this scope allows that `parent` does not."""
        if self.mode == NET_NONE:
            return []
        if self.mode != parent.mode:
            return [f"network mode {parent.mode} -> {self.mode}"]
        problems = [f"network host {host}" for host in self.hosts
                    if not any(_pattern_within(host, p) for p in parent.hosts)]
        if _later(self.expires_at, parent.expires_at):
            problems.append("network expiry")
        if parent.objective_id and self.objective_id != parent.objective_id:
            problems.append("network objective")
        return problems


@dataclass(frozen=True)
class Envelope:
    """The deterministic scope of one run (PRD section 43)."""

    run_id: str
    objective_id: str = ""
    issuer: str = "friday-policy"
    provenance: str = PERSON
    attended: bool = True
    cwd: str = ""
    read_roots: tuple[str, ...] = ()
    write_roots: tuple[str, ...] = ()
    network: NetworkScope = field(default_factory=NetworkScope)
    #: Secret aliases the run may have resolved for it - never the values.
    secrets: tuple[str, ...] = ()
    allowed_capabilities: tuple[str, ...] = ()
    blocked_capabilities: tuple[str, ...] = ()
    limits: Limits = field(default_factory=Limits)
    #: 0 means no ceiling of its own; the objective's budget still applies.
    token_limit: int = 0
    tool_call_limit: int = 0
    expires_at: str = ""
    #: capability id -> what it was when the plan was made (PRD v7 JIT-001,
    #: `capability_runtime.fingerprint`). A capability that has changed since
    #: is refused at execution. Empty on envelopes from before it.
    capability_hashes: dict = field(default_factory=dict, hash=False)
    #: At most this many changes (write or external-action calls) for the
    #: whole objective (BCON-001); 0 is no ceiling of its own.
    max_side_effects: int = 0
    #: What the objective may let leave the machine (PRIV-001): the most
    #: sensitive data class, the providers and the external services. None is
    #: no budget of its own; the global data-class policy still applies.
    privacy_budget: "PrivacyBudget | None" = None

    def __post_init__(self) -> None:
        if not self.run_id:
            raise ContractError("an envelope belongs to one run")
        if self.provenance not in PROVENANCES:
            raise ContractError(f"unknown provenance {self.provenance!r}")

    def expired(self, now: datetime | None = None) -> bool:
        ends = _parse_utc(self.expires_at)
        return ends is not None and (now or datetime.now(timezone.utc)) >= ends

    def seconds_left(self, now: datetime | None = None) -> float | None:
        """Until the envelope expires; None when it names no expiry."""
        ends = _parse_utc(self.expires_at)
        if ends is None:
            return None
        return (ends - (now or datetime.now(timezone.utc))).total_seconds()

    def reads(self, path: str) -> bool:
        """Whether `path` lies inside a read or a write root, on real paths."""
        return _inside_any(path, self.read_roots + self.write_roots)

    def writes(self, path: str) -> bool:
        """Whether `path` lies inside a write root, on real paths."""
        return _inside_any(path, self.write_roots)

    def permits(self, capability_id: str) -> str:
        """Empty when the capability is inside the envelope, otherwise why not."""
        if capability_id in self.blocked_capabilities:
            return f"{capability_id} is blocked for this run"
        if (ANY_CAPABILITY not in self.allowed_capabilities
                and capability_id not in self.allowed_capabilities):
            return f"{capability_id} is outside this run's envelope"
        return ""

    # -- narrow, never widen ---------------------------------------------

    def narrow(self, **changes) -> "Envelope":
        """
        A tighter envelope, for a sub-worker or a later step. Every change
        must fit inside what this envelope already allows; anything wider
        raises rather than being quietly clipped, because a clipped request
        is a request somebody thought they had.
        """
        known = {f.name for f in fields(Envelope)}
        unknown = sorted(set(changes) - known)
        if unknown:
            raise ContractError(f"an envelope has no field {unknown}")
        child = replace(self, **changes)
        problems = child.wider_than(self)
        if problems:
            raise ContractError(
                f"{child.run_id} would be wider than {self.run_id}: "
                + "; ".join(problems))
        return child

    def wider_than(self, parent: "Envelope") -> list[str]:
        """What this envelope allows that `parent` does not; empty when it fits."""
        problems = []
        if self.objective_id != parent.objective_id:
            problems.append("objective")
        if self.issuer != parent.issuer:
            problems.append("issuer")
        if self.provenance != parent.provenance and self.provenance != READ_MATERIAL:
            problems.append(f"provenance {parent.provenance} -> {self.provenance}")
        if self.attended and not parent.attended:
            problems.append("attended")
        readable = parent.read_roots + parent.write_roots
        if self.cwd != parent.cwd and not any(_under(self.cwd, r) for r in readable):
            problems.append(f"cwd {self.cwd}")
        problems += [f"read root {r}" for r in self.read_roots
                     if not any(_under(r, p) for p in readable)]
        problems += [f"write root {r}" for r in self.write_roots
                     if not any(_under(r, p) for p in parent.write_roots)]
        problems += self.network.wider_than(parent.network)
        problems += [f"secret {s}" for s in self.secrets
                     if s not in parent.secrets]
        if ANY_CAPABILITY not in parent.allowed_capabilities:
            problems += [f"capability {cap}" for cap in self.allowed_capabilities
                         if cap not in parent.allowed_capabilities]
        problems += [f"unblocked {cap}" for cap in parent.blocked_capabilities
                     if cap not in self.blocked_capabilities]
        for name in ("memory_mb", "processes", "seconds", "artifact_bytes"):
            if getattr(self.limits, name) > getattr(parent.limits, name):
                problems.append(f"limit {name}")
        for name in ("token_limit", "tool_call_limit", "max_side_effects"):
            mine, theirs = getattr(self, name), getattr(parent, name)
            if theirs and (mine == 0 or mine > theirs):
                problems.append(name)
        if _later(self.expires_at, parent.expires_at):
            problems.append("expiry")
        # A planned version may be kept, never dropped or swapped: without it
        # the check at the moment of action (JIT-001) is off for that
        # capability (V7-D44). A child may pin more than its parent did.
        problems += [f"capability version {cap}"
                     for cap, fingerprint in parent.capability_hashes.items()
                     if self.capability_hashes.get(cap) != fingerprint]
        # A privacy budget may be tightened, never dropped or loosened
        # (PRIV-001): a child with none would be free of its parent's.
        if parent.privacy_budget is not None:
            if self.privacy_budget is None:
                problems.append("privacy budget")
            else:
                problems += self.privacy_budget.wider_than(parent.privacy_budget)
        return problems

    # -- the stored form -------------------------------------------------

    def to_dict(self) -> dict:
        return {
            "run_id": self.run_id, "objective_id": self.objective_id,
            "issuer": self.issuer, "provenance": self.provenance,
            "attended": self.attended, "cwd": self.cwd,
            "read_roots": list(self.read_roots),
            "write_roots": list(self.write_roots),
            "network": {"mode": self.network.mode,
                        "hosts": list(self.network.hosts),
                        "objective_id": self.network.objective_id,
                        "expires_at": self.network.expires_at,
                        "grant_id": self.network.grant_id},
            "secrets": list(self.secrets),
            "allowed_capabilities": list(self.allowed_capabilities),
            "blocked_capabilities": list(self.blocked_capabilities),
            "limits": {"memory_mb": self.limits.memory_mb,
                       "processes": self.limits.processes,
                       "seconds": self.limits.seconds,
                       "artifact_bytes": self.limits.artifact_bytes},
            "token_limit": self.token_limit,
            "tool_call_limit": self.tool_call_limit,
            "expires_at": self.expires_at,
            "capability_hashes": dict(self.capability_hashes),
            "max_side_effects": self.max_side_effects,
            "privacy_budget": (None if self.privacy_budget is None
                               else self.privacy_budget.to_dict()),
        }

    @classmethod
    def from_dict(cls, raw: dict) -> "Envelope":
        """
        The stored form, read strictly. `to_dict` writes the lineage and the
        expiry, so a row without them is not an envelope FRIDAY issued, and
        reading it with the most trusting defaults - the person, attended,
        forever - is how it would be trusted anyway (Slice 2 review). Raises
        ContractError, which every reader treats as no envelope: fail closed.
        """
        missing = [key for key in ("run_id", "provenance", "attended", "expires_at")
                   if key not in raw]
        if missing:
            raise ContractError(f"a stored envelope must say {', '.join(missing)}")
        if not isinstance(raw["attended"], bool):
            raise ContractError("a stored envelope's attended is true or false")
        if not raw["expires_at"]:
            raise ContractError("a stored envelope must expire")
        network = dict(raw.get("network") or {})
        network["hosts"] = tuple(network.get("hosts") or ())
        scope = NetworkScope(**network)
        if scope.mode != NET_NONE and not scope.expires_at:
            raise ContractError(f"a stored {scope.mode} network scope must expire")
        return cls(
            run_id=raw["run_id"], objective_id=raw.get("objective_id", ""),
            issuer=raw.get("issuer", "friday-policy"),
            provenance=raw["provenance"],
            attended=raw["attended"], cwd=raw.get("cwd", ""),
            read_roots=tuple(raw.get("read_roots") or ()),
            write_roots=tuple(raw.get("write_roots") or ()),
            network=scope,
            secrets=tuple(raw.get("secrets") or ()),
            allowed_capabilities=tuple(raw.get("allowed_capabilities") or ()),
            blocked_capabilities=tuple(raw.get("blocked_capabilities") or ()),
            limits=Limits(**(raw.get("limits") or {})),
            token_limit=int(raw.get("token_limit", 0)),
            tool_call_limit=int(raw.get("tool_call_limit", 0)),
            expires_at=raw["expires_at"],
            capability_hashes=dict(raw.get("capability_hashes") or {}),
            max_side_effects=int(raw.get("max_side_effects", 0)),
            privacy_budget=_stored_budget(raw.get("privacy_budget")),
        )


def _stored_budget(raw) -> "PrivacyBudget | None":
    """The privacy budget of a stored envelope; None for one from before budgets."""
    if raw is None:
        return None
    from friday.privacy_budget import PrivacyBudget

    return PrivacyBudget.from_dict(raw)


#: Kinds of action a person forbids or limits (PRD v7 BCON-001), by the words
#: that name them - in a request ("do not delete anything") and in a
#: capability's id (`files_delete`, `email_send`).
ACTION_CLASSES = {
    "delete": ("delete", "erase", "remove", "recycle", "purge", "empty", "wipe"),
    "send": ("send", "share", "publish", "post", "email", "message", "reply"),
    "spend": ("buy", "purchase", "pay", "spend", "order"),
    "power": ("shutdown", "restart", "reboot", "shut"),
}


def action_class(words: str) -> str:
    """The action class a verb or a capability id names, or ""."""
    tokens = [w for w in re.split(r"[^a-z]+", (words or "").lower()) if w]
    for name, vocabulary in ACTION_CLASSES.items():
        if any(token in vocabulary for token in tokens):
            return name
    return ""


def _find_field(value, name: str):
    """Every value `name` takes anywhere in a nested result."""
    found = []
    if isinstance(value, dict):
        for key, inner in value.items():
            if key == name:
                found.append(inner)
            found += _find_field(inner, name)
    elif isinstance(value, (list, tuple)):
        for inner in value:
            found += _find_field(inner, name)
    return found


def broken_invariants(governance: dict | None, data) -> list[str]:
    """
    Which data invariants in `governance["invariants"]` the data breaks
    (BCON-001). An invariant is {"field": "currency", "equals": "USD"}: wherever
    the field appears in the arguments (a precondition) or the result (a
    postcondition) it must hold that value. A field that is absent is not
    broken - the invariant constrains what is said, not what is omitted.
    """
    broken = []
    for rule in (governance or {}).get("invariants") or ():
        name, wanted = str(rule.get("field") or ""), rule.get("equals")
        if not name:
            continue
        for seen in _find_field(data, name):
            if seen != wanted:
                broken.append(f"{name} is {seen!r}, the contract holds it at {wanted!r}")
                break
    return broken


RESUME_FAILED_ITEMS_ONLY = "resume_failed_items_only"


def _gated_by(rule: dict, capability: str | None) -> bool:
    """Whether a state precondition holds back `capability` (None: any)."""
    if capability is None:
        return True
    before = str(rule.get("before") or "")
    if before:
        return (action_class(capability) == before if before in ACTION_CLASSES
                else capability == before)
    from friday import trajectory

    return trajectory._side_effecting(capability)


def unmet_requirements(governance: dict | None, finished: list,
                       capability: str | None = None) -> list[str]:
    """
    State preconditions (BCON-001): `governance["requires"]` lists
    {"field", "equals"} facts that must already be true of the run before the
    next action, read from the results of the steps finished so far. A fact no
    finished step has established, or one established as something else, is
    unmet - unlike a data invariant, silence here is not enough.

    A precondition gates changes, not the reads that establish it: for a
    `capability` it applies only to a side-effecting one, or, when the rule
    names a "before" (an action class in ACTION_CLASSES or a capability id),
    only to the actions that match it. Without a capability every rule is
    checked.
    """
    unmet = []
    for rule in (governance or {}).get("requires") or ():
        name, wanted = str(rule.get("field") or ""), rule.get("equals")
        if not name or not _gated_by(rule, capability):
            continue
        seen = [v for result in finished for v in _find_field(result, name)]
        if wanted not in seen:
            now = f"is {seen[-1]!r}" if seen else "is not established"
            unmet.append(f"{name} {now}, the contract requires {wanted!r}")
    return unmet


def item_outcomes(result) -> dict[str, bool]:
    """
    Per-item state of a batch result (OBJ-004): {item: succeeded}, read from
    the "items" of the result's "output" envelope (or of the result itself)
    shaped {"item": ..., "ok": bool} or {"item": ..., "status":
    "succeeded"|...}. Empty when the result reports no per-item outcomes.
    """
    outcomes: dict[str, bool] = {}
    rows = None
    if isinstance(result, dict):
        output = result.get("output")
        rows = output.get("items") if isinstance(output, dict) else None
        if rows is None:
            rows = result.get("items")
    for row in rows if isinstance(rows, list) else ():
        if not isinstance(row, dict) or "item" not in row:
            continue
        ok = row["ok"] if "ok" in row else str(row.get("status")).lower() in (
            "succeeded", "success", "ok", "done")
        outcomes[str(row["item"])] = bool(ok)
    return outcomes


def write_ceiling(governance: dict | None) -> int:
    """
    The most changes the contract allows (BCON-001), 0 when it sets no limit.
    The PRD's spelling `max_external_writes` and the internal
    `max_side_effects` are the same limit; when both are given the stricter
    one holds.
    """
    limits = []
    for key in ("max_external_writes", "max_side_effects"):
        try:
            limit = int((governance or {}).get(key) or 0)
        except (TypeError, ValueError):
            continue
        if limit > 0:
            limits.append(limit)
    return min(limits, default=0)


def partial_failure_recovery(governance: dict | None) -> str:
    """
    The declared recovery after a partial failure (BCON-001), "" when none.
    The internal spelling is `recovery: "<strategy>"`; the PRD's is
    `recovery: {"on_partial_failure": "<strategy>"}`.
    """
    recovery = (governance or {}).get("recovery")
    if isinstance(recovery, str):
        return recovery
    if isinstance(recovery, dict):
        declared = recovery.get("on_partial_failure")
        return declared if isinstance(declared, str) else ""
    return ""


def resume_arguments(governance: dict | None, arguments: dict,
                     outcomes: dict[str, bool] | None) -> dict:
    """
    Declared recovery `resume_failed_items_only` (BCON-001): a retry of a
    batch step carries only the items the last attempt failed, never the ones
    it already did. Any other recovery, or no recorded per-item state, leaves
    the arguments as they were.
    """
    if partial_failure_recovery(governance) != RESUME_FAILED_ITEMS_ONLY or not outcomes:
        return arguments
    items = arguments.get("items")
    if not isinstance(items, list):
        return arguments
    return {**arguments, "items": [i for i in items if not outcomes.get(str(i), False)]}


@dataclass(frozen=True)
class ObjectiveContract:
    """
    What the person asked for, as the intent compiler read it (PRD v7 §10.3,
    §11): the outcome, why they want it, what must and must not happen, how
    to speak while working, and the exact words and names they gave. Kept
    beside the task graph so the run is held to the request, not to a
    paraphrase of it.
    """

    goal: str
    purpose: tuple[str, ...] = ()
    hard_constraints: tuple[str, ...] = ()
    preferences: tuple[str, ...] = ()
    #: Names, paths and words the person gave, which the plan uses verbatim.
    exact_literals: tuple[str, ...] = ()
    #: What done means, as the compiler derived it from the goals.
    success_criteria: tuple[str, ...] = ()
    assumptions: tuple[str, ...] = ()
    open_questions: tuple[str, ...] = ()
    #: What is enforced, not only recorded (BCON-001): "forbidden" action
    #: classes, "protected" names per class, "max_side_effects".
    governance: dict = field(default_factory=dict, hash=False)
    version: int = 1

    def __post_init__(self) -> None:
        if not (self.goal or "").strip():
            raise ContractError("an objective contract states its goal")

    def to_dict(self) -> dict:
        return {f.name: (list(getattr(self, f.name))
                         if isinstance(getattr(self, f.name), tuple)
                         else getattr(self, f.name)) for f in fields(self)}

    @classmethod
    def from_dict(cls, raw: dict) -> "ObjectiveContract":
        known = {f.name for f in fields(cls)}
        unknown = sorted(set(raw) - known)
        if unknown:
            raise ContractError(f"an objective contract has no field {unknown}")
        return cls(**{name: (tuple(value) if isinstance(value, list) else value)
                      for name, value in raw.items()})
