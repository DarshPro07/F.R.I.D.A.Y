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

import uuid
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone

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


def started(run_id: str, tool_id: str) -> ActionResult:
    return ActionResult(
        run_id=run_id, tool_id=tool_id, status=RUNNING, started_at=now_iso()
    )


def succeeded(
    prior: ActionResult, *, verification: Verification, output: object = None,
    artifacts: tuple[Artifact, ...] = (), side_effects: tuple[str, ...] = (),
) -> ActionResult:
    return prior.finish(
        status=SUCCEEDED, verification=verification, output=output,
        artifacts=artifacts, side_effects=side_effects,
    )


def failed(prior: ActionResult, error: str, *, side_effects: tuple[str, ...] = ()) -> ActionResult:
    return prior.finish(status=FAILED, error=error, side_effects=side_effects)


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

    def __post_init__(self) -> None:
        if self.state not in RUN_STATES:
            raise ContractError(f"unknown run state {self.state!r}")
        if self.provenance not in PROVENANCES:
            raise ContractError(f"unknown provenance {self.provenance!r}")

    @classmethod
    def create(cls, request: str, capability: str | None = None, *,
               provenance: str = PERSON) -> "Run":
        return cls(run_id=new_run_id(), request=request, capability=capability,
                   provenance=provenance)

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
