"""
What must survive when an objective's state is compressed (PRD v7 CTX-003,
§19.8).

A fresh worker does not get the conversation; it gets a packet. Before any
packet replaces history, the critical facts are identified from the
objective's durable state - the goal, the exact words the person gave, what
must and must not happen, what was decided, what is still open, where the
evidence is, what already failed, what comes next, and on whose authority -
and the packet is checked to carry every one of them. A packet that dropped
one is refused, not sent: a summary that forgot "never touch the live
database" is worse than no summary.

The facts are read from what the engine recorded (the contract, the tasks,
the approvals, the envelope), never from a model's account of them. Each is
one line that a packet must contain verbatim; paraphrase is not retention.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, fields

from friday import contracts

#: How a fact line starts, per kind, so a packet names what it carries.
OBJECTIVE = "OBJECTIVE"
LITERAL = "EXACT"
CONSTRAINT = "MUST"
DECISION = "DECIDED"
QUESTION = "OPEN QUESTION"
EVIDENCE = "EVIDENCE"
AVOID = "ALREADY FAILED"
NEXT = "NEXT"
AUTHORITY = "AUTHORITY"

#: How much of a failure reason a fact keeps. The pointer is the task; the
#: full reason stays in its row.
REASON_CHARS = 160
#: How much of a step's arguments a fact keeps; the task row holds them all.
ARGUMENT_CHARS = 200

_TERMINAL_FAILED = ("FAILED", "BLOCKED")
_OPEN = ("QUEUED", "READY", "RUNNING", "WAITING", "INTERRUPTED")


class LossyCompaction(contracts.ContractError):
    """A packet that does not carry every critical fact."""

    def __init__(self, missing: list[str]):
        self.missing = list(missing)
        super().__init__("the packet drops critical facts: " + "; ".join(self.missing))


@dataclass(frozen=True)
class CriticalFactSet:
    """The nine kinds §19.8 names, each a tuple of verbatim lines."""

    objective: tuple[str, ...] = ()
    literals: tuple[str, ...] = ()
    constraints: tuple[str, ...] = ()
    decisions: tuple[str, ...] = ()
    questions: tuple[str, ...] = ()
    evidence: tuple[str, ...] = ()
    avoid: tuple[str, ...] = ()
    next_action: tuple[str, ...] = ()
    authority: tuple[str, ...] = ()

    def lines(self) -> tuple[str, ...]:
        """Every fact, in a fixed order, once."""
        return tuple(dict.fromkeys(line for f in fields(self)
                                   for line in getattr(self, f.name)))

    def binding(self) -> tuple[str, ...]:
        """What the person gave and the engine decided: the objective, their
        exact words, the constraints, the approvals, the open questions and
        the authority. These bind a worker."""
        return tuple(dict.fromkeys(self.objective + self.literals + self.constraints
                                   + self.decisions + self.questions + self.authority))

    def recorded(self) -> tuple[str, ...]:
        """What tools and the plan recorded: evidence pointers, approaches that
        failed (with what a tool said about why) and the next step. It is
        information about the run, to be presented as data, never as an
        instruction."""
        return tuple(line for line in dict.fromkeys(self.evidence + self.avoid + self.next_action)
                     if line not in self.binding())

    def render(self) -> str:
        return "\n".join(self.lines())


def _squash(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def _governance_lines(governance: dict) -> list[str]:
    lines = [f"{CONSTRAINT}: never {kind}" for kind in governance.get("forbidden") or ()]
    for kind, names in sorted((governance.get("protected") or {}).items()):
        lines += [f"{CONSTRAINT}: do not {kind} {name}" for name in names]
    ceiling = int(governance.get("max_side_effects") or 0)
    if ceiling:
        lines.append(f"{CONSTRAINT}: at most {ceiling} changes")
    return lines


def for_objective(store, run_id: str) -> CriticalFactSet:
    """The critical facts of one objective, from what the engine recorded."""
    run = store.objective_run(run_id)
    if run is None:
        raise LookupError(f"no objective run {run_id!r}")
    raw = run.get("contract")
    contract = (contracts.ObjectiveContract.from_dict(json.loads(raw))
                if raw else None)
    goal = contract.goal if contract is not None else str(run.get("request") or "")
    objective = [f"{OBJECTIVE}: {_squash(goal)}"]
    if contract is not None:
        objective += [f"{OBJECTIVE} (why): {_squash(p)}" for p in contract.purpose]
    literals = [f"{LITERAL}: {literal}" for literal in
                (contract.exact_literals if contract is not None else ())]
    constraints = []
    if contract is not None:
        constraints = [f"{CONSTRAINT}: {_squash(h)}" for h in contract.hard_constraints]
        constraints += _governance_lines(dict(contract.governance or {}))
    questions = [f"{QUESTION}: {_squash(q)}" for q in
                 (contract.open_questions if contract is not None else ())]

    decisions = []
    for record in json.loads(run.get("approvals") or "[]"):
        if record.get("decision") == "APPROVED":
            target = f" {record['target']}" if record.get("target") else ""
            decisions.append(f"{DECISION}: {record.get('decided_by') or 'the person'} "
                             f"approved {record.get('operation')}{target}")

    evidence, avoid, next_action = [], [], []
    for task in store.objective_tasks(run_id):
        status = str(task.get("status") or "")
        arguments = json.dumps(task.get("arguments") or {}, sort_keys=True, default=str)
        if len(arguments) > ARGUMENT_CHARS:
            arguments = arguments[:ARGUMENT_CHARS] + "..."
        step = f"{task.get('capability')} {arguments}"
        if status == "SUCCEEDED":
            evidence.append(f"{EVIDENCE}: task {task['task_id']} {task.get('capability')} "
                            "succeeded; its evidence is on that task")
        elif status in _TERMINAL_FAILED:
            reason = _squash(str(task.get("evidence") or ""))[:REASON_CHARS]
            avoid.append(f"{AVOID}: {step} ({reason})")
        elif status in _OPEN and not next_action:
            next_action.append(f"{NEXT}: {step}")

    authority = []
    raw_envelope = run.get("envelope")
    if raw_envelope:
        envelope = contracts.Envelope.from_dict(json.loads(raw_envelope))
        authority.append(f"{AUTHORITY}: from {envelope.provenance}, "
                         f"{'attended' if envelope.attended else 'unattended'}, "
                         f"network {envelope.network.mode}")
        if envelope.write_roots:
            authority.append(f"{AUTHORITY}: may write only under "
                             + ", ".join(envelope.write_roots))
    return CriticalFactSet(objective=tuple(objective), literals=tuple(literals),
                           constraints=tuple(constraints), decisions=tuple(decisions),
                           questions=tuple(questions), evidence=tuple(evidence),
                           avoid=tuple(avoid), next_action=tuple(next_action),
                           authority=tuple(authority))


def for_continuation(*, objective: str, task_id: str, evidence_refs=(),
                     provenance: str) -> CriticalFactSet:
    """The facts a resumed voice portion must be handed (continuity.py): the
    person's objective as they gave it, all of it; the task it resumes; the
    evidence recorded so far; and on whose authority it runs."""
    return CriticalFactSet(
        objective=(f"{OBJECTIVE}: {objective}",),
        evidence=tuple(f"{EVIDENCE}: {ref}" for ref in evidence_refs),
        next_action=(f"{NEXT}: task {task_id}",),
        authority=(f"{AUTHORITY}: from {provenance}",))


def missing(packet: str, facts: CriticalFactSet) -> list[str]:
    """The facts `packet` does not carry. Spacing may differ; words may not,
    and an exact literal must appear exactly as the person gave it."""
    squashed = _squash(packet)
    lost = [line for line in facts.lines() if _squash(line) not in squashed]
    lost += [line for line in facts.literals
             if line not in lost and line.split(": ", 1)[1] not in packet]
    return lost


def validate(packet: str, facts: CriticalFactSet) -> None:
    """Refuse a packet that drops a critical fact."""
    lost = missing(packet, facts)
    if lost:
        raise LossyCompaction(lost)
