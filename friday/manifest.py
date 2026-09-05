"""
The Capability Manifest (PRD v3.1 §9.6, FR-023, FR-024, FR-026, FR-027).

One queryable view over everything Friday can execute or consult:

  * the fabric's external providers (`friday/fabric.py`), typed by their
    integration mode - NATIVE / MCP / CLI / SDK / HTTP / SIDECAR / SKILL /
    REFERENCE / SPECIALIST_RUNTIME;
  * Friday's own MCP tools (`friday/capabilities.py`), all NATIVE;
  * the coding executors (`friday/executors/`), SPECIALIST_RUNTIME.

The manifest is derived, never hand-maintained: a provider that is not in
the fabric registry is not in the manifest, and `fabric.call` refuses any
provider id it does not know, so "no unregistered capability can execute
privileged work" (FR-023 acceptance) is a property of the call path, not
of this file. FR-024's rule - a SKILL is never presented as executable -
is `executable()` below, and `fabric.call` on a SKILL provider returns
its text, never runs anything (the adapters read markdown).
"""
from __future__ import annotations

import logging

import json
from dataclasses import dataclass, field, asdict

from friday import fabric

NATIVE = "NATIVE"
MCP = "MCP"
CLI = "CLI"
SDK = "SDK"
HTTP = "HTTP"
SIDECAR = "SIDECAR"
SKILL = "SKILL"
REFERENCE = "REFERENCE"
SPECIALIST_RUNTIME = "SPECIALIST_RUNTIME"
TYPES = (NATIVE, MCP, CLI, SDK, HTTP, SIDECAR, SKILL, REFERENCE, SPECIALIST_RUNTIME)

#: Types that can execute work. SKILL and REFERENCE are read, not run.
EXECUTABLE_TYPES = frozenset({NATIVE, MCP, CLI, SDK, HTTP, SIDECAR, SPECIALIST_RUNTIME})

#: fabric.integration_mode -> manifest type. ADAPTER is refined by the
#: adapter's `TRANSPORT` (sdk | http) when it declares one; BUILTIN is
#: Friday's own code.
_MODE_TYPE = {
    fabric.BUILTIN: NATIVE,
    fabric.ADAPTER: SDK,
    fabric.MCP: MCP,
    fabric.CLI: CLI,
    fabric.SIDECAR: SIDECAR,
    fabric.SKILL: SKILL,
    fabric.REFERENCE_ONLY: REFERENCE,
}

#: Fabric providers that are coding/agent runtimes Friday delegates whole
#: tasks to. They run their own loop under Friday's contract (PRD 4.2).
_SPECIALIST_PROVIDERS = frozenset({
    "claude_subagents", "agents_team_pack", "openhands_reference",
})

#: Provider states that mean "can be called right now".
_LIVE = (fabric.READY, fabric.DEGRADED)


@dataclass
class Manifest:
    """PRD 9.6 CapabilityManifest."""

    id: str
    name: str
    version: str
    type: str
    source: str
    license: str
    trust_level: str
    review_status: str
    permissions: tuple[str, ...]
    dangerous_actions: tuple[str, ...]
    health_check: str
    dependencies: tuple[str, ...]
    cost_profile: str
    latency_profile: str
    supported_platforms: tuple[str, ...]
    default_state: str
    state: str = fabric.REGISTERED
    family: str = ""
    supported_actions: tuple[str, ...] = ()
    executable: bool = True
    detail: str = ""
    extra: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)


def manifest_type(provider: fabric.Provider) -> str:
    if provider.id in _SPECIALIST_PROVIDERS:
        return SPECIALIST_RUNTIME
    base = _MODE_TYPE.get(provider.integration_mode, NATIVE)
    if base == SDK:
        try:
            module = fabric._adapter(provider)
            transport = str(getattr(module, "TRANSPORT", "") or "").lower()
        except Exception:  # noqa: BLE001 - a broken adapter is still typed
            transport = ""
        if transport == "http":
            return HTTP
    return base


def executable(manifest_type_: str) -> bool:
    """FR-024: a SKILL or REFERENCE is guidance, never an executor."""
    return manifest_type_ in EXECUTABLE_TYPES


def _trust_level(provider: fabric.Provider) -> str:
    if provider.integration_mode == fabric.BUILTIN:
        return "core"
    if provider.commit:
        return "pinned"
    return "unpinned" if provider.upstream else "core"


def _review_status(provider: fabric.Provider) -> str:
    if provider.integration_mode == fabric.REFERENCE_ONLY:
        return "reference_only"
    if provider.upstream and not provider.commit:
        return "unreviewed"
    return "reviewed"


def _dangerous(provider: fabric.Provider) -> tuple[str, ...]:
    if provider.risk in ("high", "restricted"):
        return tuple(o for o in provider.operations
                     if o not in getattr(provider, "open_operations", ()))
    return ()


def _latency(provider: fabric.Provider) -> str:
    if provider.integration_mode in (fabric.SKILL, fabric.REFERENCE_ONLY, fabric.BUILTIN):
        return "instant"
    if provider.owns_process or provider.integration_mode in (fabric.SIDECAR, fabric.MCP):
        return "process_start"
    if provider.model_required:
        return "model_call"
    return "subsecond"


def of_provider(provider: fabric.Provider) -> Manifest:
    kind = manifest_type(provider)
    module = provider.module or ""
    has_probe = False
    try:
        has_probe = hasattr(fabric._adapter(provider), "health")
    except Exception as exc:  # noqa: BLE001 - an adapter that cannot import has no probe
        logging.getLogger("friday.manifest").debug("%s: adapter not importable: %s", provider.id, exc)
    return Manifest(
        id=provider.id,
        name=provider.id.replace("_", " "),
        version=provider.version or (provider.commit[:12] if provider.commit else "builtin"),
        type=kind,
        source=provider.upstream or module,
        license=provider.license_mode,
        trust_level=_trust_level(provider),
        review_status=_review_status(provider),
        permissions=tuple(provider.permissions),
        dangerous_actions=_dangerous(provider),
        health_check=("adapter.health" if has_probe else "import_only"),
        dependencies=tuple(provider.secrets) + tuple(provider.fallbacks),
        cost_profile=provider.cost_class,
        latency_profile=_latency(provider),
        supported_platforms=("windows",),
        default_state=(fabric.REFERENCE_ONLY if kind == REFERENCE else fabric.REGISTERED),
        state=fabric.state(provider.id),
        family=provider.family,
        supported_actions=tuple(provider.operations),
        executable=executable(kind),
        detail=(fabric._ACTIVE[provider.id].detail
                if provider.id in fabric._ACTIVE else ""),
        extra={"risk": provider.risk, "integration_mode": provider.integration_mode,
               "owns_process": provider.owns_process,
               "model_required": provider.model_required,
               "commit": provider.commit},
    )


def of_native_tool(capability) -> Manifest:
    """One of Friday's own MCP tools, from `friday/capabilities.py`."""
    from friday import policy as P
    from friday import trust as T
    # The same id resolution `Capability.requires_approval` uses, so the
    # manifest and the approval gate cannot disagree about a tool.
    category = None
    for tool_id in capability.policy_tool_ids():
        category = P.TOOL_CATEGORIES.get(tool_id)
        if category is not None:
            break
    tier = T.tier_of_category(category) if category else T.R2
    return Manifest(
        id=capability.id,
        name=capability.id.replace("_", " "),
        version="builtin",
        type=NATIVE,
        source="friday.tools",
        license=fabric.BUILTIN_LICENSE,
        trust_level="core",
        review_status="reviewed",
        permissions=(category,) if category else (),
        dangerous_actions=(capability.id,) if tier in (T.R3, T.R4) else (),
        health_check="registered",
        dependencies=(),
        cost_profile="free",
        latency_profile="subsecond",
        supported_platforms=("windows",),
        default_state=fabric.READY,
        state=fabric.READY,
        family=getattr(capability, "execution_scope", ""),
        supported_actions=(capability.id,),
        executable=True,
        extra={"risk_tier": tier, "side_effect": getattr(capability, "side_effect", ""),
               "requires_edge": bool(getattr(capability, "requires_edge", False))},
    )


def of_executor(executor_id: str, *, available: bool, detail: str = "") -> Manifest:
    return Manifest(
        id=f"executor:{executor_id}",
        name=f"{executor_id} coding executor",
        version="external",
        type=SPECIALIST_RUNTIME,
        source=f"friday.executors.{executor_id}",
        license="SEPARATE_LICENSE",
        trust_level="pinned" if executor_id == "hermes" else "external",
        review_status="reviewed",
        permissions=("COMMAND_EXECUTION",),
        dangerous_actions=("execute",),
        health_check="executor.available",
        dependencies=(),
        cost_profile="expensive",
        latency_profile="minutes",
        supported_platforms=("windows",),
        default_state=fabric.REGISTERED,
        state=fabric.READY if available else fabric.UNAVAILABLE,
        family="coding",
        supported_actions=("execute",),
        executable=True,
        detail=detail,
    )


def build(*, include_native: bool = True, include_executors: bool = True) -> list[Manifest]:
    """The whole manifest. Cheap: nothing is activated to build it."""
    out = [of_provider(p) for p in sorted(fabric.registry().values(),
                                         key=lambda p: (p.family, p.id))]
    if include_native:
        from friday import capabilities as C
        out.extend(of_native_tool(cap) for cap in C.CAPABILITIES.values())
    if include_executors:
        try:
            from friday import executor_router as XR
            present = set(XR.installed())
            for executor in XR.KNOWN:
                out.append(of_executor(
                    executor.id, available=executor.id in present,
                    detail="" if executor.id in present else "not installed"))
        except Exception as exc:  # noqa: BLE001 - the manifest still answers
            out.append(of_executor("unknown", available=False, detail=str(exc)))
    return out


def summary() -> dict:
    """FR-025 progressive discovery: the summary face - counts per type and
    state, names only. Full schemas come from `describe(id)`, one at a time."""
    rows = build()
    by_type: dict[str, int] = {}
    by_state: dict[str, int] = {}
    for m in rows:
        by_type[m.type] = by_type.get(m.type, 0) + 1
        by_state[m.state] = by_state.get(m.state, 0) + 1
    return {
        "total": len(rows),
        "by_type": dict(sorted(by_type.items())),
        "by_state": dict(sorted(by_state.items())),
        "executable": sum(1 for m in rows if m.executable),
        "guidance_only": sorted(m.id for m in rows if not m.executable),
        "ids": sorted(m.id for m in rows),
    }


def describe(capability_id: str) -> dict | None:
    for m in build():
        if m.id == capability_id:
            return m.to_dict()
    return None


def export(path=None) -> str:
    """FR-027: the pinned set as JSON, for upgrade review and rollback diffs."""
    rows = [m.to_dict() for m in build()]
    text = json.dumps(rows, indent=2, sort_keys=True, default=str)
    if path is not None:
        from pathlib import Path
        Path(path).write_text(text, encoding="utf-8")
    return text
