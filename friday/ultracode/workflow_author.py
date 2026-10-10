"""UCW-008: FRIDAY drafts a workflow definition from a plain-language process.

`author_workflow` asks an injected drafting model (`draft`, async) for a UCW-002
definition, then holds the draft to four checks before it is offered:

1. it parses (`definition.parse_definition`: no model name, no cycle, typed
   handoffs) and compiles (`compile_definition`);
2. every step role is registered and every deterministic executor is an
   available capability - a step that needs something else is a typed GAP
   refusal naming it, never an invented step;
3. a dry run with stub agents and stub executors finishes every step (no real
   model and no real executor is called);
4. only then is the result OFFERED. Nothing is registered or run for real here:
   `offer` asks the person through `friday/confirmation.py` (a random, single-use,
   expiring nonce bound to this exact definition) and `activate` spends that
   nonce, so only a yes the person gave can register it, and a changed or refused
   draft can never ride an old consent.

The description is data: it goes to the drafting model in the call, never into
a prompt this module builds, and nothing in it is executed.

Hook, stated so nobody trusts it more than it is: the AGENT-002 role registry
(`friday/agent_registry.py`) has not landed, so `roles` is a plain collection of
registered role names the caller supplies; when AGENT-002 lands its role list
is passed here. Likewise `capabilities` is the set of available executors.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Awaitable, Callable, Collection, Mapping

from friday import confirmation as C
from friday.ultracode import definition as D
from friday.ultracode.spec import DEEP_TEMPLATES, TIERS

OFFERED = "OFFERED"
REFUSED = "REFUSED"

#: Why a draft was refused.
GAP = "GAP"            # a role, capability or need FRIDAY does not have
INVALID = "INVALID"    # the draft fails the UCW-002 schema or compile
DRY_RUN = "DRY_RUN"    # the draft validates but a stub step did not finish

_GAP_NAME_MAX = 200

#: The action a confirmation is bound to; the target is `<workflow_id>@<version>`
#: and the arguments are the canonical definition, so a yes covers one definition.
ACTIVATE_ACTION = "ultracode.workflow.activate"

Draft = Callable[[str, dict], Awaitable["str | dict"]]


class ActivationRefused(PermissionError):
    """Activation without the person's confirmation of this exact definition."""


@dataclass(frozen=True)
class Gap:
    """One thing the process needs that FRIDAY does not have.

    kind is `role` (no registered role), `capability` (executor not available)
    or `requested` (the drafting model reported a need it could not meet)."""
    kind: str
    name: str
    step: str = ""
    reason: str = ""


@dataclass(frozen=True)
class Authored:
    status: str
    kind: str = ""
    definition: D.Definition | None = None
    gaps: tuple[Gap, ...] = ()
    field: str = ""
    reason: str = ""
    dry_run: Mapping[str, str] | None = None

    @property
    def offerable(self) -> bool:
        return self.status == OFFERED and self.definition is not None


def _target(definition: D.Definition) -> str:
    return f"{definition.workflow_id}@{definition.version}"


def _refuse(kind: str, reason: str, *, field: str = "", gaps: tuple[Gap, ...] = (),
            dry_run: Mapping[str, str] | None = None) -> Authored:
    return Authored(REFUSED, kind=kind, field=field, reason=reason, gaps=gaps, dry_run=dry_run)


def _requested_gaps(raw: dict) -> tuple[tuple[Gap, ...], tuple[int, ...]] | None:
    """The needs a drafting model reported in place of a definition, with the
    positions of any malformed entries, or None when `raw` is not such a report.
    The valid needs are kept even when a sibling entry is malformed."""
    if set(raw) != {"gaps"} or not isinstance(raw["gaps"], list) or not raw["gaps"]:
        return None
    gaps, malformed = [], []
    for index, item in enumerate(raw["gaps"]):
        need = item.get("need") if isinstance(item, dict) else item
        if not isinstance(need, str) or not need.strip():
            malformed.append(index)
            continue
        gaps.append(Gap("requested", need.strip()[:_GAP_NAME_MAX],
                        reason="the drafting model reported a need with no registered role "
                               "or available capability"))
    return tuple(gaps), tuple(malformed)


def _find_gaps(definition: D.Definition, roles: Collection[str],
               capabilities: Collection[str]) -> tuple[Gap, ...]:
    seen: set[tuple[str, str]] = set()
    gaps = []
    for step in definition.steps:
        if step.role not in roles and ("role", step.role) not in seen:
            seen.add(("role", step.role))
            gaps.append(Gap("role", step.role, step.id,
                            f"step {step.id!r} needs role {step.role!r}, which is not registered"))
        if (step.kind == "deterministic" and step.executor not in capabilities
                and ("capability", step.executor) not in seen):
            seen.add(("capability", step.executor))
            gaps.append(Gap("capability", step.executor, step.id,
                            f"step {step.id!r} needs capability {step.executor!r}, "
                            "which is not available"))
    return tuple(gaps)


def _stub(schema: dict):
    """A value that satisfies the JSON-Schema subset a step hands on."""
    if "enum" in schema:
        return schema["enum"][0]
    kind = schema["type"]
    if kind == "object":
        return {name: _stub(sub) for name, sub in schema.get("properties", {}).items()}
    if kind == "array":
        return [_stub(schema["items"])] if "items" in schema else []
    return {"string": "", "integer": 0, "number": 0, "boolean": False, "null": None}[kind]


async def _dry_run(compiled: D.Compiled) -> dict[str, D.StepOutcome]:
    async def agent(step, handed, model):
        return _stub(step.outputs)

    async def executor(step, handed):
        return _stub(step.outputs)

    executors = {s.executor: executor for s in compiled.definition.steps
                 if s.kind == "deterministic"}
    return await D.run_definition(compiled, agent=agent, executors=executors)


def _context(roles: Collection[str], capabilities: Collection[str]) -> dict:
    return {
        "roles": sorted(roles), "capabilities": sorted(capabilities), "tiers": list(TIERS),
        "rules": [
            "name a step role only from `roles` and a deterministic executor only from "
            "`capabilities`",
            "never name a model anywhere in the definition; a model is chosen at run time "
            "from the step's tier",
            f"the deep tier is only for the templates {list(DEEP_TEMPLATES)}",
            'if the process needs a role or capability that is not listed, answer '
            '{"gaps": ["<what is missing>"]} instead of inventing a step',
        ],
    }


async def author_workflow(description: str, *, draft: Draft, roles: Collection[str],
                          capabilities: Collection[str]) -> Authored:
    """Draft, validate and dry-run a workflow for `description`. A drafting-model
    error propagates; every other failure is a typed refusal."""
    if not isinstance(description, str) or not description.strip():
        raise ValueError("a process description is required")
    produced = await draft(description, _context(roles, capabilities))
    raw = produced
    if isinstance(produced, (str, bytes)):
        try:
            raw = json.loads(produced)
        except ValueError:
            raw = produced          # parse_definition reports it as not valid JSON
    if isinstance(raw, dict) and (report := _requested_gaps(raw)) is not None:
        needs, malformed = report
        if not needs:
            return _refuse(INVALID, "the reported gaps are all malformed: entries "
                           + ", ".join(map(str, malformed)) + " are not a need", field="gaps")
        note = ("; gaps entries " + ", ".join(map(str, malformed)) + " were malformed and "
                "ignored" if malformed else "")
        return _refuse(GAP, "the process needs " + "; ".join(g.name for g in needs)
                       + ", which FRIDAY has no registered role or available capability for"
                       + note, gaps=needs)
    try:
        definition = D.parse_definition(raw)
        found = _find_gaps(definition, roles, capabilities)
        if found:
            return _refuse(GAP, "; ".join(g.reason for g in found), gaps=found)
        compiled = D.compile_definition(definition)
    except D.DefinitionError as exc:
        return _refuse(INVALID, str(exc), field=exc.field)
    outcomes = await _dry_run(compiled)
    states = {step_id: out.state for step_id, out in outcomes.items()}
    failed = next((out for out in outcomes.values() if out.state != "DONE"), None)
    if failed is not None:
        return _refuse(DRY_RUN, f"stub dry run: step {failed.step!r} is {failed.state}: "
                                f"{failed.reason}", field=failed.field, dry_run=states)
    return Authored(OFFERED, definition=definition, dry_run=states)


def offer(authored: Authored, book: "C.Book | C.StoredBook", *, run_id: str,
          seconds: float = C.DEFAULT_SECONDS) -> C.Confirmation:
    """Ask the person to activate this exact definition. The returned
    confirmation's nonce is random and single use; only the person's approval
    (`book.approve`, reached from a real answer) lets `activate` spend it."""
    if not authored.offerable:
        raise ActivationRefused("only a validated, dry-run workflow can be offered")
    definition = authored.definition
    return book.ask(
        run_id, ACTIVATE_ACTION, _target(definition),
        f"Activate workflow {_target(definition)} ({len(definition.steps)} steps)?",
        arguments=definition.to_dict(), seconds=seconds)


def activate(authored: Authored, registry: D.DefinitionRegistry, *,
             book: "C.Book | C.StoredBook", nonce: str, run_id: str) -> D.Definition:
    """Register an offered definition, only by spending the person's approved,
    unexpired, unused confirmation for exactly this definition."""
    if not authored.offerable:
        raise ActivationRefused("only a validated, dry-run workflow can be activated")
    if not isinstance(nonce, str):
        raise ActivationRefused("a confirmation nonce is required")
    definition = authored.definition
    verdict = book.consume(nonce, run_id=run_id, action=ACTIVATE_ACTION,
                           target=_target(definition), arguments=definition.to_dict())
    if not verdict.ok:
        raise ActivationRefused(verdict.reason)
    registry.register(definition)
    return definition
