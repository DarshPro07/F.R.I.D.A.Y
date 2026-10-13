"""
CAPB-001: a capability the plan needs and Friday does not have.

The objective engine used to write that down as a terminal
FAILED/CAPABILITY_MISSING and stop. This is the seam that lets it do more
while staying inside forge's own lifecycle:

    search the fabric -> record what was compared -> author a spec and source
    -> static gate and sandboxed verification (forge.Registry.verify)
    -> register -> the person's recorded approval -> enable for ONE objective

and then the engine puts the original task back to READY and runs it. This
module owns the build; `continuous.ContinuousTaskExecutor` owns the run and
only asks. Nothing here can enable a skill by itself: authoring and enabling
are separate parties, and with no approver the skill stays REGISTERED.

Who authors the source in production (Hermes, the Claude executor) is not
decided, so no author is wired outside tests: an executor without a builder
behaves exactly as before.
"""
from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from friday import contracts as c
from friday import forge

logger = logging.getLogger(__name__)

#: Recorded on the objective's event log, one per attempted build.
EVENT_BUILD = "capability.build"
#: The party that submits, verifies and registers. It never enables.
BUILDER_ACTOR = "capability-builder"
#: A task is rebuilt at most this many times, so a skill the owner keeps
#: refusing (or an author that keeps failing) cannot loop the run.
MAX_BUILDS_PER_TASK = 1
#: How many alternatives the comparison keeps.
MAX_CONSIDERED = 5


@dataclass(frozen=True)
class BuildRequest:
    capability: str
    arguments: dict
    request: str
    objective_id: str
    #: What the fabric search found; handed to the author so it does not
    #: rebuild something that is nearly there.
    considered: tuple[dict, ...] = ()


@dataclass(frozen=True)
class Authored:
    spec: forge.CapabilitySpec
    source: str
    provenance: str


@dataclass(frozen=True)
class Approval:
    """The person's decision to switch a registered skill on."""
    actor: str
    scopes: tuple[str, ...]


@dataclass
class BuildOutcome:
    capability: str
    built: bool
    reason: str
    considered: list[dict] = field(default_factory=list)
    state: str = ""
    approval: dict | None = None

    def to_dict(self) -> dict:
        return {"capability": self.capability, "built": self.built,
                "reason": self.reason, "considered": self.considered,
                "state": self.state, "approval": self.approval}


Author = Callable[[BuildRequest], "Authored | None"]
Approver = Callable[[dict], "Approval | None"]
Search = Callable[[str], list]


def scope_for(objective_id: str) -> str:
    """A forged skill is enabled for the objective that asked for it."""
    return f"objective:{objective_id}"


def _tokens(text: str) -> set[str]:
    return {t for t in re.split(r"[^a-z0-9]+", text.lower()) if len(t) > 2}


def search_fabric(capability: str) -> list[dict]:
    """
    Providers the capability fabric already has that could serve this one.

    An exact operation match is "the capability exists". Anything else that
    shares terms is a related provider, kept so the comparison is on record;
    it is never substituted for what the person's plan asked for.
    """
    from friday import fabric, fabric_memory

    wanted = _tokens(capability)
    exact: list[fabric.Provider] = []
    related: list[fabric.Provider] = []
    shared: dict[str, list[str]] = {}
    for provider in fabric.registry().values():
        words = _tokens(" ".join((provider.id, provider.family,
                                  *provider.operations)))
        if capability == provider.id or capability in provider.operations:
            exact.append(provider)
        elif wanted & words:
            related.append(provider)
            shared[provider.id] = sorted(wanted & words)
    ranked = (tuple(fabric_memory.rank(tuple(exact), capability))
              + tuple(fabric_memory.rank(tuple(related), capability)))
    return [{"provider": p.id, "family": p.family, "risk": p.risk,
             "operations": list(p.operations)[:6],
             "match": "exact" if p in exact else "related",
             "shared_terms": shared.get(p.id, [])}
            for p in ranked[:MAX_CONSIDERED]]


class CapabilityBuilder:
    """Builds one missing capability through forge, and runs it once enabled."""

    def __init__(self, *, author: Author, approve: Approver,
                 registry: forge.Registry | None = None,
                 search: Search = search_fabric) -> None:
        self._author = author
        self._approve = approve
        self._registry = registry
        self._search = search

    @property
    def registry(self) -> forge.Registry:
        if self._registry is None:
            self._registry = forge.Registry()
        return self._registry

    # -- the build ---------------------------------------------------------

    def build(self, request: BuildRequest) -> BuildOutcome:
        name = request.capability
        scope = scope_for(request.objective_id)
        considered = list(self._search(name))

        def refused(reason: str, state: str = "") -> BuildOutcome:
            return BuildOutcome(name, False, reason, considered, state)

        if self.owns(name, request.objective_id):
            return BuildOutcome(name, True, "already enabled for this objective",
                                considered, forge.ENABLED)
        offered = [row for row in considered if row.get("match") == "exact"]
        if offered:
            return refused(f"{offered[0]['provider']} already offers {name}; "
                           "a duplicate is not built")
        request = BuildRequest(name, request.arguments, request.request,
                               request.objective_id, tuple(considered))
        try:
            authored = self._author(request)
        except Exception as exc:  # noqa: BLE001 - an author is outside code; its failure is the build's reason
            logger.exception("capability author failed for %s", name)
            return refused(f"the author failed: {type(exc).__name__}: {exc}")
        if authored is None:
            return refused("no author produced a skill for it")
        if authored.spec.name != name:
            return refused(f"the author wrote {authored.spec.name!r}, "
                           f"not {name!r}")

        try:
            self.registry.submit(authored.spec, authored.source,
                                 provenance=authored.provenance)
            record = self.registry.verify(name, actor=BUILDER_ACTOR)
            if record["state"] != forge.VERIFIED:
                findings = (record.get("verification") or {}).get("findings")
                return refused("refused by forge: "
                               + ("; ".join(findings) or "its declared cases failed"),
                               record["state"])
            record = self.registry.register(name, actor=BUILDER_ACTOR)
        except forge.ForgeError as exc:
            return refused(f"forge refused it: {exc}")

        approval = self._approve({
            "name": name, "goal": authored.spec.goal, "risk": record["risk"],
            "side_effects": list(authored.spec.side_effects),
            "filesystem": authored.spec.filesystem,
            "network": authored.spec.network,
            "cases_passed": len((record.get("verification") or {}).get("cases") or ()),
            "scope": scope, "provenance": authored.provenance})
        if approval is None:
            return refused("registered, waiting for the person's approval to "
                           "enable it", record["state"])
        recorded = {"actor": approval.actor, "scopes": list(approval.scopes)}
        if approval.actor in ("", "forge", BUILDER_ACTOR):
            return BuildOutcome(name, False, "the party that built a skill "
                                "cannot approve it", considered,
                                record["state"], recorded)
        if scope not in approval.scopes:
            return BuildOutcome(name, False, f"the approval does not cover {scope}",
                                considered, record["state"], recorded)
        try:
            record = self.registry.enable(name, tuple(approval.scopes),
                                          actor=approval.actor)
        except forge.ForgeError as exc:
            return BuildOutcome(name, False, f"forge refused to enable it: {exc}",
                                considered, record["state"], recorded)
        return BuildOutcome(name, True, f"built, verified and enabled for {scope}",
                            considered, record["state"], recorded)

    # -- running it --------------------------------------------------------

    def owns(self, capability: str, objective_id: str) -> bool:
        record = self.registry.get(capability)
        return bool(record and record["state"] == forge.ENABLED
                    and scope_for(objective_id) in (record["scopes"] or ()))

    async def call(self, capability: str, arguments: dict, *,
                   objective_id: str) -> dict:
        """Run the enabled skill on the task's arguments, in forge's sandbox."""
        record = self.registry.get(capability)
        if not self.owns(capability, objective_id):
            raise LookupError(f"{capability} is not enabled for this objective")
        source = Path(record["source_path"]).read_text(encoding="utf-8")
        if forge.digest(source) != record["source_sha256"]:
            raise RuntimeError(f"{capability}: the file on disk is not what was "
                               "verified and enabled")
        spec = forge.CapabilitySpec.from_dict(record["spec"])
        reply = await asyncio.to_thread(forge.invoke, source, spec, arguments)
        if not reply["ok"]:
            raise RuntimeError(f"{capability}: {reply['error']}")
        return {"status": c.SUCCEEDED, "output": reply["result"],
                "verification": {
                    "method": "forge_skill",
                    "evidence": f"{capability} v{record['version']} ran in "
                                "forge's sandbox on this task's arguments"}}
