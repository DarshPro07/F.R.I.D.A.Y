"""
The Capability Fabric: what Friday could reach for, and what that would cost.

`friday/capabilities.py` already answers "what can Friday do" for the 158
things Friday implements itself. It has no concept of a pinned upstream commit,
a license that forbids importing, a sidecar that has to be started before it
answers, or a health probe against code we did not write. Those are the four
facts that matter about an *external* provider and none of them fit in a
`Capability`.

So this is the second registry, and it is deliberately small. A `Provider` is a
declaration: this upstream exists, here is the exact commit, here is what it is
allowed to do, here is what it costs, here is how to tell whether it is alive.
Registering one starts nothing. Twenty-one always-on sidecars on one Windows
box is precisely the wreckage the build pack exists to avoid, so activation is
lazy and happens when the router picks a provider for real work.

Two invariants are enforced at construction rather than in review, because
review does not catch the fiftieth file:

    copyleft implies isolation   an AGPL/GPL provider cannot declare an
                                 integration mode that would import it
    fallbacks must exist         a provider cannot name a fallback that is not
                                 registered, so a failover path cannot rot
                                 silently into a LookupError at 3am

Discovery is derived, not written down. Each adapter module under
`friday/fabric_adapters/` exposes a module-level `DESCRIPTOR`; the registry
finds them with pkgutil. A hand-maintained table of 21 entries is the same
mistake `capability_runtime` documents at its head, only shorter.
"""

from __future__ import annotations

import importlib
import logging
import os
import pkgutil
import time
from dataclasses import dataclass, replace

from friday import contracts as c

logger = logging.getLogger(__name__)

# --- vocabulary ------------------------------------------------------------

#: What the user asks for. Families are the user-facing names; the provider
#: brand behind one is a diagnostic detail. "I need this scraped" is a family;
#: "use Scrapling" is an implementation the user should never have to know.
FAMILIES = (
    "code_intelligence", "browser", "scraping", "search", "research",
    "coding", "memory", "media", "voice", "social", "security",
    "writing", "presentation", "roles", "orchestration", "diagnostic",
    #: Product trading: catalogue, inventory, orders, customers - against a
    #: store the owner runs (Medusa, Smartstore). Added 2026-09-02 when the
    #: owner asked for an end-to-end product-trading system; the family is
    #: the user-facing name, the storefront behind it is a provider.
    "commerce",
)

#: How the upstream's license constrains the integration, not the license name.
#:
#: The distinction that matters at build time is "may this be imported into
#: Friday's own process" - everything else is a lawyer's problem, and this
#: field is the engineering half of it.
PERMISSIVE = "PERMISSIVE_IMPORT"      # MIT / BSD / Apache-2.0
COPYLEFT = "ISOLATED_SIDECAR"         # AGPL-3.0 / GPL-3.0
SEPARATE = "SEPARATE_LICENSE"         # e.g. OpenHands enterprise/
BUILTIN_LICENSE = "NONE"              # ours; no upstream involved
LICENSE_MODES = (PERMISSIVE, COPYLEFT, SEPARATE, BUILTIN_LICENSE)

#: Integration modes that put a process or network boundary between Friday and
#: the upstream. Copyleft providers may only use these.
BUILTIN = "BUILTIN"
ADAPTER = "ADAPTER"                   # imported into Friday's process
MCP = "MCP"                           # separate process, MCP protocol
SKILL = "SKILL"                       # prompt/recipe only, no code executed
SIDECAR = "SIDECAR"                   # separate process/service
REFERENCE_ONLY = "REFERENCE_ONLY"     # read for patterns; never executed
#: One-shot subprocess: invoke, work, exit. The seven command-line agents in
#: third_party/upstream had no mode that described them, so they were
#: unreachable by construction rather than by omission.
CLI = "CLI"
INTEGRATION_MODES = (BUILTIN, ADAPTER, MCP, SKILL, SIDECAR, REFERENCE_ONLY, CLI)

#: The modes that do not link upstream code into Friday's address space.
#: CLI belongs here, and that is the point: a subprocess is a process boundary,
#: so a copyleft agent becomes integrable without touching the licence
#: invariant. The invariant was a wall with no door; this is the door.
ISOLATED_MODES = frozenset({MCP, SIDECAR, SKILL, REFERENCE_ONLY, CLI})

#: Runtime state. Distinct from "is it in the registry", which is always true
#: for anything this module can name.
REGISTERED = "REGISTERED"        # known, never activated this session
READY = "READY"                  # activated and its health probe agrees
DEGRADED = "DEGRADED"            # answering, but not fully - say so, use it
AUTH_REQUIRED = "AUTH_REQUIRED"  # a person must supply a credential
UNAVAILABLE = "UNAVAILABLE"      # tried, could not reach it
DISABLED = "DISABLED"            # deliberately switched off
FAILED = "FAILED"                # reachable, but its last call raised (FR-026)
STATES = (REGISTERED, READY, DEGRADED, AUTH_REQUIRED, UNAVAILABLE, DISABLED,
          FAILED, REFERENCE_ONLY)

#: How many consecutive call-time exceptions before a READY provider is
#: reported FAILED. One raise is a bad argument as often as a broken
#: provider; three in a row is the provider.
FAILED_AFTER = 3

RISK_LEVELS = ("low", "medium", "high", "restricted")

#: Rough price of one invocation, used for ordering candidates. Deliberately
#: coarse: the router needs "is this the cheap one" and nothing finer.
COST_ORDER = {"free": 0, "cheap": 1, "moderate": 2, "expensive": 3, "paid": 4}


class FabricError(RuntimeError):
    """A provider could not be registered, activated, or reached."""


# --- the descriptor --------------------------------------------------------


@dataclass(frozen=True)
class Provider:
    """
    One upstream capability, declared. Registering this runs nothing.

    `commit` is the exact SHA the audit was performed against. It is not
    decorative: an unpinned provider is an unaudited provider, and
    NON_NEGOTIABLE 6 says untrusted until pinned. Providers we wrote ourselves
    carry the empty string and `license_mode == NONE`.
    """

    id: str
    family: str
    #: The upstream repository's short name, or "" for something we wrote.
    upstream: str
    #: Operation names this provider answers to. The router matches on these.
    operations: tuple[str, ...]
    risk: str
    license_mode: str
    integration_mode: str
    #: Friday permission ids that must be granted before this may run.
    permissions: tuple[str, ...] = ()
    #: Operations exempt from `permissions`, because knowing is not doing.
    #:
    #: A provider-wide permission list cannot say "reading the index is open,
    #: running the procedure is not", and several providers need exactly that:
    #: `security_skills` declares `security.authorized_scope` and its own notes
    #: say catalogue and search "are open". While the gate was fail-open that
    #: contradiction cost nothing; the moment `call()` started enforcing, the
    #: blanket permission closed the open half too. This is the narrowest field
    #: that expresses the real rule, and it fails safe: an operation not named
    #: here is gated.
    open_operations: tuple[str, ...] = ()
    #: Names of secrets this needs. Names only - never values, and never
    #: resolved here. The secret broker owns resolution.
    secrets: tuple[str, ...] = ()
    #: Whether an invocation costs a model call. Free deterministic providers
    #: are preferred by the router precisely because this is False.
    model_required: bool = False
    cost_class: str = "cheap"
    #: Exact pinned upstream commit, and the human-readable version if any.
    commit: str = ""
    version: str = ""
    #: Provider ids to try, in order, when this one cannot answer. Validated
    #: against the registry at import time.
    fallbacks: tuple[str, ...] = ()
    #: True when activating this starts a long-lived OS process, which makes it
    #: the singleton check's business.
    owns_process: bool = False
    #: The dotted module implementing start/stop/health/call. Defaults to the
    #: adapter module the descriptor was discovered in.
    module: str = ""
    notes: str = ""

    def __post_init__(self) -> None:
        if self.family not in FAMILIES:
            raise FabricError(f"{self.id}: unknown family {self.family!r}")
        if self.risk not in RISK_LEVELS:
            raise FabricError(f"{self.id}: bad risk {self.risk!r}")
        if self.license_mode not in LICENSE_MODES:
            raise FabricError(
                f"{self.id}: bad license_mode {self.license_mode!r}")
        if self.integration_mode not in INTEGRATION_MODES:
            raise FabricError(
                f"{self.id}: bad integration_mode {self.integration_mode!r}")
        if self.cost_class not in COST_ORDER:
            raise FabricError(f"{self.id}: bad cost_class {self.cost_class!r}")
        if not self.operations:
            raise FabricError(f"{self.id}: declares no operations")

        # NON_NEGOTIABLE 9, made structural. An AGPL provider that declared
        # ADAPTER would be imported into a proprietary process by whoever
        # writes the adapter next, and nobody would notice until distribution.
        if self.license_mode == COPYLEFT and self.integration_mode not in ISOLATED_MODES:
            raise FabricError(
                f"{self.id}: {COPYLEFT} requires an isolated integration mode "
                f"({sorted(ISOLATED_MODES)}), not {self.integration_mode!r}")

        # An upstream with no pin is an upstream with no audit.
        if self.upstream and not self.commit and self.integration_mode != REFERENCE_ONLY:
            raise FabricError(
                f"{self.id}: upstream {self.upstream!r} is not pinned to a commit")

    @property
    def imported(self) -> bool:
        """Whether using this links upstream code into Friday's process."""
        return self.integration_mode in (BUILTIN, ADAPTER)


# --- registry --------------------------------------------------------------

#: Where adapter modules live. Each exposes `DESCRIPTOR: Provider`.
ADAPTER_PACKAGE = "friday.fabric_adapters"

_REGISTRY: dict[str, Provider] | None = None
#: Adapter modules that raised at IMPORT, by module name -> the error text.
#: Kept beside the registry so `health()`/the UI can name them; they are
#: never in the registry (a provider that cannot import cannot be called)
#: and they never stop the others from loading (invariant A-048 "kernel";
#: NON_NEGOTIABLE 15: an optional integration cannot take the control
#: plane down). A DESCRIPTOR that is present but WRONG (not a Provider, a
#: duplicate id) is still a hard error: that is a bug in this tree, not an
#: absent upstream.
_BROKEN: dict[str, str] = {}


def _discover() -> dict[str, Provider]:
    """Every adapter module's DESCRIPTOR, keyed by id."""
    found: dict[str, Provider] = {}
    _BROKEN.clear()
    try:
        package = importlib.import_module(ADAPTER_PACKAGE)
    except ModuleNotFoundError:
        return found

    for info in pkgutil.iter_modules(package.__path__):
        if info.name.startswith("_"):
            continue
        name = f"{ADAPTER_PACKAGE}.{info.name}"
        try:
            module = importlib.import_module(name)
        except Exception as exc:  # noqa: BLE001 - recorded, never fatal
            _BROKEN[name] = f"{type(exc).__name__}: {exc}"
            logger.error("fabric adapter %s failed to import and is excluded: %s",
                         name, _BROKEN[name])
            continue
        descriptor = getattr(module, "DESCRIPTOR", None)
        if descriptor is None:
            continue
        if not isinstance(descriptor, Provider):
            raise FabricError(f"{name}: DESCRIPTOR is not a Provider")
        if descriptor.id in found:
            raise FabricError(
                f"duplicate provider id {descriptor.id!r} in {name}")
        found[descriptor.id] = replace(
            descriptor, module=descriptor.module or name)
    return found


def reap_orphans() -> list[int]:
    """Kill sidecars left holding a port by a previous, hard-killed Friday.

    A hard kill leaves the child running; the next start then fails with
    "address already in use", which looks nothing like its cause. Called once
    from `registry()`, which every path into the fabric goes through, so there
    is no startup hook to forget to add - and forgetting was the first version
    of this: the function existed and nothing called it.
    """
    from friday import fabric_process
    markers = {}
    for provider in (_REGISTRY or {}).values():
        if not provider.owns_process:
            continue
        try:
            markers[provider.id] = getattr(
                _adapter(provider), "PROCESS_MARKER", "")
        except Exception:  # noqa: BLE001
            continue
    if not markers:
        return []
    return fabric_process.reap_orphans(markers)


def registry() -> dict[str, Provider]:
    """The provider registry, discovered once."""
    global _REGISTRY
    if _REGISTRY is None:
        found = _discover()
        # Validated after the whole set is known, because a fallback may point
        # at a provider whose module sorts later.
        for provider in found.values():
            for target in provider.fallbacks:
                if target not in found:
                    raise FabricError(
                        f"{provider.id}: fallback {target!r} is not registered")
                if target == provider.id:
                    raise FabricError(f"{provider.id}: names itself as fallback")
        _REGISTRY = found
        # Once per process, after the registry exists so markers can be read.
        # Opt-out rather than opt-in: leaving a stale sidecar holding a port is
        # the failure, and an env var is for the rare debugging session where
        # you want the leftover kept.
        if os.getenv("FRIDAY_KEEP_ORPHANS", "").lower() not in ("1", "true"):
            try:
                killed = reap_orphans()
                if killed:
                    logger.info("fabric reaped %d orphaned sidecar(s): %s",
                                len(killed), killed)
            except Exception:  # noqa: BLE001 - never block the registry
                logger.exception("orphan reap failed; continuing")
    return _REGISTRY


def reload() -> dict[str, Provider]:
    """Drop the cache. For tests that add an adapter module on the fly."""
    global _REGISTRY
    _REGISTRY = None
    _ACTIVE.clear()
    return registry()


def get(provider_id: str) -> Provider:
    try:
        return registry()[provider_id]
    except KeyError:
        raise FabricError(f"no such provider: {provider_id!r}") from None


def by_family(family: str) -> tuple[Provider, ...]:
    if family not in FAMILIES:
        raise FabricError(f"unknown family {family!r}")
    return tuple(p for p in registry().values() if p.family == family)


def families() -> tuple[str, ...]:
    """Families that actually have a provider, in declaration order."""
    present = {p.family for p in registry().values()}
    return tuple(f for f in FAMILIES if f in present)


# --- lifecycle -------------------------------------------------------------


@dataclass
class Activation:
    """What happened when we last tried to bring a provider up."""

    provider_id: str
    state: str = REGISTERED
    detail: str = ""
    activated_at: float = 0.0
    #: Whatever the adapter's `start()` handed back - a process, a client, a
    #: session. The fabric never inspects it.
    handle: object = None
    #: Consecutive call-time exceptions since the last success (FR-026).
    consecutive_failures: int = 0


_ACTIVE: dict[str, Activation] = {}


def _adapter(provider: Provider):
    return importlib.import_module(provider.module)


def state(provider_id: str) -> str:
    """Current runtime state without touching the provider."""
    provider = get(provider_id)
    if provider.integration_mode == REFERENCE_ONLY:
        return REFERENCE_ONLY
    activation = _ACTIVE.get(provider_id)
    return activation.state if activation else REGISTERED


def activate(provider_id: str) -> Activation:
    """
    Bring a provider up, once. Idempotent: an already-READY provider is
    returned as-is rather than started twice.

    A provider that cannot start is recorded as UNAVAILABLE with the reason
    rather than raising, because an optional provider failing is not the parent
    objective failing (NON_NEGOTIABLE 15). Callers that need it to work should
    check the returned state.
    """
    provider = get(provider_id)
    if provider.integration_mode == REFERENCE_ONLY:
        return Activation(provider_id, REFERENCE_ONLY,
                          "read for patterns; never executed")

    existing = _ACTIVE.get(provider_id)
    if existing and existing.state in (READY, DEGRADED):
        return existing

    activation = Activation(provider_id, activated_at=time.time())
    try:
        module = _adapter(provider)
    except Exception as exc:                       # noqa: BLE001 - reported
        activation.state = UNAVAILABLE
        activation.detail = f"adapter import failed: {exc}"
        _ACTIVE[provider_id] = activation
        return activation

    # A PROCESS_SPEC means the fabric owns the child, not the adapter: port,
    # readiness, logs, restart and a stop that actually stops. Adapters that
    # already implement start() keep working untouched, which is what lets
    # this land without editing codebase_memory or graft.
    spec = getattr(module, "PROCESS_SPEC", None)
    if spec is not None:
        from friday import fabric_process
        try:
            activation.handle = fabric_process.spawn(provider_id, spec)
        except Exception as exc:                   # noqa: BLE001 - reported
            activation.state = UNAVAILABLE
            activation.detail = str(exc)
            _ACTIVE[provider_id] = activation
            return activation
        _ACTIVE[provider_id] = activation
        probe = health(provider_id, _activation=activation)
        activation.state = probe["state"]
        activation.detail = probe.get("detail", "")
        return activation

    start = getattr(module, "start", None)
    if start is not None:
        try:
            activation.handle = start()
        except Exception as exc:                   # noqa: BLE001 - reported
            activation.state = UNAVAILABLE
            activation.detail = f"start failed: {exc}"
            _ACTIVE[provider_id] = activation
            return activation

    _ACTIVE[provider_id] = activation
    probe = health(provider_id, _activation=activation)
    activation.state = probe["state"]
    activation.detail = probe.get("detail", "")
    return activation


def deactivate(provider_id: str) -> None:
    """Stop a provider if it owns anything. Safe to call when it is not up."""
    activation = _ACTIVE.pop(provider_id, None)
    if activation is None:
        return
    provider = get(provider_id)
    from friday import fabric_process
    if fabric_process.child(provider_id) is not None:
        # The supervisor escalates terminate -> kill and verifies. Swallowing
        # the failure here is what used to leave a port held after a restart.
        fabric_process.stop(provider_id)
        return
    stop = getattr(_adapter(provider), "stop", None)
    if stop is not None:
        try:
            stop(activation.handle)
        except Exception:                          # noqa: BLE001
            # An adapter-owned child we cannot stop is a process-table problem,
            # which `processes()` reports. Not worth failing a shutdown over.
            pass


def deactivate_all() -> None:
    for provider_id in list(_ACTIVE):
        deactivate(provider_id)


def health(provider_id: str, *, _activation: Activation | None = None) -> dict:
    """
    Ask a provider whether it is actually working.

    An adapter with no `health` is reported READY on the strength of having
    imported, and says so in `detail` - an honest weak claim rather than a
    confident one. The fabric never upgrades "it imported" into "it works".
    """
    provider = get(provider_id)
    if provider.integration_mode == REFERENCE_ONLY:
        return {"provider": provider_id, "state": REFERENCE_ONLY,
                "detail": "reference only; nothing to probe"}

    activation = _activation or _ACTIVE.get(provider_id)
    if activation is None:
        return {"provider": provider_id, "state": REGISTERED,
                "detail": "not activated"}

    probe = getattr(_adapter(provider), "health", None)
    if probe is None:
        return {"provider": provider_id, "state": READY,
                "detail": "adapter imported; no health probe declared"}
    try:
        result = probe(activation.handle)
    except Exception as exc:                       # noqa: BLE001 - reported
        return {"provider": provider_id, "state": UNAVAILABLE,
                "detail": f"health probe raised: {exc}"}

    if isinstance(result, dict):
        reported = result.get("state", READY)
        if reported not in STATES:
            raise FabricError(
                f"{provider_id}: health returned unknown state {reported!r}")
        extra = {k: v for k, v in result.items()
                 if k not in ("state", "detail", "provider")}
        return {"provider": provider_id, "state": reported,
                "detail": result.get("detail", ""), **extra}
    return {"provider": provider_id,
            "state": READY if result else UNAVAILABLE,
            "detail": "health probe returned a bare boolean"}


def report() -> list[dict]:
    """Every provider's family, state and pin. The diagnostic surface.
    Adapter modules that failed to import are listed too, as UNAVAILABLE
    with the import error - excluded from the registry, not from the
    truth."""
    rows = [{
        "provider": provider.id,
        "family": provider.family,
        "upstream": provider.upstream,
        "state": state(provider.id),
        "integration_mode": provider.integration_mode,
        "license_mode": provider.license_mode,
        "commit": provider.commit,
        "owns_process": provider.owns_process,
    } for provider in sorted(registry().values(),
                             key=lambda p: (p.family, p.id))]
    for module, error in sorted(_BROKEN.items()):
        rows.append({"provider": module.rsplit(".", 1)[-1], "family": "", "upstream": "",
                     "state": UNAVAILABLE, "integration_mode": "", "license_mode": "",
                     "commit": "", "owns_process": False, "import_error": error})
    return rows


def family_report() -> list[dict]:
    """
    What the user is allowed to hear: families and their best current state.

    A family is READY when any provider in it is READY. This is the answer to
    "what can you do", and it deliberately does not name brands - the user asks
    for outcomes, Friday picks the backend.
    """
    rank = {READY: 0, DEGRADED: 1, AUTH_REQUIRED: 2, REGISTERED: 3,
            REFERENCE_ONLY: 4, DISABLED: 5, UNAVAILABLE: 6}
    out = []
    for family in families():
        states = [state(p.id) for p in by_family(family)]
        best = min(states, key=lambda s: rank.get(s, 9))
        # REGISTERED is a fabric-internal fact ("nobody has needed it yet"),
        # not a user-facing one. Dormant-but-installed is the design, so it
        # reads as READY to the person asking what Friday can do.
        out.append({"family": family,
                    "state": READY if best == REGISTERED else best,
                    "providers": len(states)})
    return out


# --- routing ---------------------------------------------------------------


def candidates(family: str, operation: str = "", *,
               authorized: frozenset[str] | None = None,
               allow_model: bool = True) -> tuple[Provider, ...]:
    """
    Providers that could serve this, cheapest and least risky first.

    The ordering is the selection rule from the fabric architecture, reduced to
    what is knowable before the call: a deterministic free provider outranks a
    model-backed one, and a low-risk provider outranks a restricted one at the
    same price. Quality and historical rework live in `execution_economics`'
    route outcomes and are applied by the caller that has the task text.
    """
    pool = by_family(family)
    if operation:
        pool = tuple(p for p in pool if operation in p.operations)
    if not allow_model:
        pool = tuple(p for p in pool if not p.model_required)
    if authorized is not None:
        # Same exemption `call()` applies, so ranking and enforcement agree.
        # They must: a provider filtered out here that call() would have
        # allowed is a capability that silently disappears from the menu.
        pool = tuple(
            p for p in pool
            if operation in p.open_operations
            or all(perm in authorized for perm in p.permissions))
    pool = tuple(p for p in pool if p.integration_mode != REFERENCE_ONLY)
    ordered = tuple(sorted(pool, key=lambda p: (
        COST_ORDER[p.cost_class],
        RISK_LEVELS.index(p.risk),
        p.model_required,
        p.id,
    )))
    # Then, and only then, what use has taught us. Cost and risk are the
    # primary rule and stay so; this is a stable re-sort, so it moves nothing
    # between providers it has learned nothing about. Without it a provider
    # failing nine calls in ten was picked exactly as readily as one that
    # always worked, and the fallback chain rediscovered that every request.
    from friday import fabric_memory
    return fabric_memory.rank(ordered, operation)


def select(family: str, operation: str = "", *,
           authorized: frozenset[str] | None = None,
           allow_model: bool = True) -> Provider | None:
    """
    The minimum sufficient provider that is actually up, or None.

    Activation happens here and only here: this is the moment a provider is
    genuinely needed, which is what "lazy" means. Candidates that will not come
    up are skipped and the next is tried, so a dead optional provider costs one
    failed probe rather than the objective.
    """
    for provider in candidates(family, operation, authorized=authorized,
                               allow_model=allow_model):
        if activate(provider.id).state in (READY, DEGRADED):
            return provider
    return None


def route(family: str, operation: str = "", **kwargs) -> tuple[Provider, ...]:
    """The chosen provider followed by its declared fallbacks, in order."""
    chosen = select(family, operation, **kwargs)
    if chosen is None:
        return ()
    chain = [chosen]
    for target in chosen.fallbacks:
        provider = get(target)
        if operation and operation not in provider.operations:
            continue
        chain.append(provider)
    return tuple(chain)


# --- invocation ------------------------------------------------------------


def _resolve_secrets(provider: Provider) -> tuple[dict, str]:
    """(values by alias, missing alias) for a provider's declared secrets.

    Adapters used to reach into `os.environ` themselves. That stops here: the
    supervisor scrubs a child's environment, so an adapter that keeps doing it
    would silently get nothing, and "silently gets nothing" is how a credential
    bug becomes a mystery. Values are returned to `call()` and never stored,
    never logged, never put in an ActionResult.
    """
    if not provider.secrets:
        return {}, ""
    try:
        from friday.secret_broker import SecretBroker
        broker = SecretBroker()
    except Exception as exc:  # noqa: BLE001
        return {}, f"secret broker unavailable: {exc}"
    values = {}
    for alias in provider.secrets:
        try:
            value = broker.resolve_for_process(alias)
        except Exception:  # noqa: BLE001
            value = ""
        if not value:
            return {}, alias
        values[alias] = value
    return values, ""


def _remember(provider_id: str, operation: str, ok: bool) -> None:
    """Feed one outcome to the selection prior. Never costs a call its result.

    Only invocation outcomes are recorded - not refusals. A permission gate
    firing says nothing about whether the provider works, and counting it as a
    failure would teach the fabric to avoid providers whose only sin is being
    correctly gated.
    """
    try:
        from friday import fabric_memory
        fabric_memory.record(provider_id, operation, ok)
    except Exception:  # noqa: BLE001
        pass


def call(provider_id: str, operation: str, *, run_id: str = "",
         authorized: frozenset[str] | None = None,
         **arguments) -> c.ActionResult:
    """
    Invoke one operation and return an honest envelope.

    `run_id` is the caller's WorkRun or ObjectiveRun id and is threaded into
    the ActionResult so a fabric call is attributable to the objective that
    wanted it. An adapter that returns a bare value gets a verification naming
    exactly what was observed - that the provider returned - which is a weak
    claim, honestly labelled, rather than a fabricated strong one.
    """
    provider = get(provider_id)
    if operation not in provider.operations:
        raise FabricError(
            f"{provider_id} does not declare operation {operation!r}")

    tool_id = f"fabric.{provider_id}.{operation}"
    result = c.started(run_id or c.new_run_id(), tool_id)

    # The authorisation gate, ahead of activation on purpose: a call that is
    # not allowed must not be able to start a process as a side effect of being
    # refused. `authorized=None` means no grants, not all grants - the same
    # fail-closed rule `capabilities.requires_approval` applies to an unknown
    # tool id, because unaudited must not mean allowed. `candidates()` filters
    # too, but that is a convenience for ranking; this is the enforcement, and
    # it sits at the one function every path funnels through.
    granted = authorized if authorized is not None else frozenset()
    required = (() if operation in getattr(provider, "open_operations", ())
                else provider.permissions)
    missing = [perm for perm in required if perm not in granted]
    if missing:
        return c.failed(
            result,
            f"{provider_id}.{operation} needs {sorted(missing)}; not granted")

    # FR-061/062: the AUTHORIZED_SECURITY namespace needs more than a
    # permission grant - a SecurityAuthorization contract naming the
    # target scope, actions, intensity and expiry, checked deterministically
    # against what THIS call is about to touch. An out-of-scope host or
    # action is refused here whatever the caller or the tool asked for.
    from friday import trust as T
    if T.is_security_capability(provider_id) and operation not in getattr(
            provider, "open_operations", ()):
        auth = arguments.pop("security_authorization", None)
        if isinstance(auth, dict):
            try:
                auth = T.SecurityAuthorization(**auth)
            except (TypeError, ValueError) as exc:
                return c.failed(result, f"invalid SecurityAuthorization: {exc}")
        host = str(arguments.get("target") or arguments.get("host")
                   or arguments.get("domain") or arguments.get("url") or "")
        guard = T.target_guard(auth, host=host, action=operation,
                               intensity=str(arguments.get("intensity") or T.LOW_ACTIVE))
        T.audit().record(actor=run_id or "friday", action=tool_id, target=host,
                         tier=T.R3, decision="ALLOW" if guard["allowed"] else "DENY",
                         result=guard["reason"], objective_id=run_id or "",
                         detail={"approval_id": getattr(auth, "approval_id", "")})
        if not guard["allowed"]:
            return c.failed(result, f"security scope: {guard['reason']}")

    secrets, missing_secret = _resolve_secrets(provider)
    if missing_secret:
        # The alias, never the value, and never a partial value.
        return c.failed(
            result, f"{provider_id} needs the secret {missing_secret!r}")

    activation = activate(provider_id)
    if activation.state not in (READY, DEGRADED):
        return c.failed(
            result, f"{provider_id} is {activation.state}: {activation.detail}")

    invoke = getattr(_adapter(provider), "call", None)
    if invoke is None:
        return c.failed(result, f"{provider_id}'s adapter declares no call()")

    try:
        # `secrets=` is additive: an adapter that ignores it keeps working
        # unchanged, which is what makes this safe to land on the three
        # existing ADAPTER providers without touching them.
        if secrets:
            arguments = {**arguments, "secrets": secrets}
        value = invoke(operation, activation.handle, **arguments)
    except Exception as exc:                       # noqa: BLE001 - reported
        _remember(provider_id, operation, False)
        # FR-026: a provider that keeps raising is FAILED, not READY. The
        # state is visible in report()/family_report() and the next call
        # re-activates (health probe decides whether it is back).
        activation.consecutive_failures += 1
        if activation.consecutive_failures >= FAILED_AFTER:
            activation.state = FAILED
            activation.detail = (f"{activation.consecutive_failures} consecutive "
                                 f"call failures; last: {exc}")
        return c.failed(result, f"{provider_id}.{operation} raised: {exc}")
    activation.consecutive_failures = 0

    # An adapter may return a finished ActionResult when it has real evidence.
    # That is the preferred shape and is passed through untouched.
    if isinstance(value, c.ActionResult):
        _remember(provider_id, operation, value.status == "succeeded")
        return value
    _remember(provider_id, operation, True)
    return c.succeeded(
        result,
        verification=c.Verification(
            method="fabric.call",
            evidence=(f"{provider_id}.{operation} returned "
                      f"{type(value).__name__} without raising; "
                      f"provider state {activation.state}"),
        ),
        output=value,
    )


def call_with_fallback(family: str, operation: str, *, run_id: str = "",
                       authorized: frozenset[str] | None = None,
                       allow_model: bool = True,
                       **arguments) -> c.ActionResult:
    """
    Try the routed chain until one answers. Records which ones did not.

    NON_NEGOTIABLE 15: an optional provider failing must not fail the parent
    objective. So the chain is walked, each failure is kept for the record, and
    only an exhausted chain is a failure.
    """
    chain = route(family, operation, authorized=authorized,
                  allow_model=allow_model)
    if not chain:
        envelope = c.started(run_id or c.new_run_id(),
                             f"fabric.{family}.{operation}")
        return c.failed(envelope,
                        f"no provider available for {family}.{operation}")

    tried: list[str] = []
    for provider in chain:
        result = call(provider.id, operation, run_id=run_id,
                      authorized=authorized, **arguments)
        if result.status == c.SUCCEEDED:
            if tried:
                return replace(
                    result,
                    side_effects=result.side_effects
                    + tuple(f"fell back past {t}" for t in tried))
            return result
        tried.append(f"{provider.id}: {result.error}")

    envelope = c.started(run_id or c.new_run_id(),
                         f"fabric.{family}.{operation}")
    return c.failed(envelope,
                    f"every provider for {family}.{operation} failed: "
                    + "; ".join(tried))


# --- process singleton -----------------------------------------------------


def processes() -> dict:
    """
    Which fabric providers own OS processes, and whether any is duplicated.

    A provider that owns a process and appears twice is the stale-duplicate
    case `restart_friday.py` exists for, one layer out. Reported as data so a
    check can fail loudly rather than a human noticing the second port.
    """
    try:
        import psutil
    except ModuleNotFoundError:
        return {"supported": False, "reason": "psutil not installed",
                "providers": {}, "duplicates": []}

    owners = {p.id: p for p in registry().values() if p.owns_process}
    if not owners:
        return {"supported": True, "providers": {}, "duplicates": []}

    markers = {}
    for provider_id, provider in owners.items():
        try:
            markers[provider_id] = getattr(
                _adapter(provider), "PROCESS_MARKER", "")
        except Exception:                          # noqa: BLE001
            markers[provider_id] = ""

    seen: dict[str, list[int]] = {pid: [] for pid in owners}
    for proc in psutil.process_iter(["pid", "cmdline"]):
        try:
            line = " ".join(proc.info.get("cmdline") or [])
        except Exception:                          # noqa: BLE001
            continue
        if not line:
            continue
        for provider_id, marker in markers.items():
            if marker and marker in line:
                seen[provider_id].append(proc.info["pid"])

    duplicates = [{"provider": pid, "pids": pids}
                  for pid, pids in seen.items() if len(pids) > 1]
    from friday import fabric_process
    return {"supported": True, "providers": seen, "duplicates": duplicates,
            # Distinguishes "our child" from "someone else's leftover", which
            # is what duplicate detection could not do before.
            "supervised": fabric_process.running()}
