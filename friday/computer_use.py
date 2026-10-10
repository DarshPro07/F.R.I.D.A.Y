"""
The computer-use backend interface and its router (PRD v7 CUA-001).

`action_path` decides which path a step acts through (DOM, coordinates, ...).
This decides which *backend* serves that path: Playwright for the DOM, the
desktop takeover's native input layer for coordinates, and later an isolated
computer or Cua Driver, each one a fabric provider in the `computer_use`
family that satisfies `Backend`.

A backend is a fabric adapter module, so the contract is the fabric's own
(`start / stop / health / call`) plus `PATHS`, the action paths it serves. It
declares one `<path>.probe` operation per path; the router asks the fabric for
the minimum sufficient provider that offers the probe for the path, and a
backend that is not installed or not up is reported with its reason and
skipped. One backend being down never takes another down.

The probe is read-only by design. Acting on a page or a screen stays behind
the policy-gated `browser_*` and `desktop_*` tools; a backend exposes no new
way to act.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

from friday import action_path, computer_use_sessions, fabric

FAMILY = "computer_use"

#: The paths a computer-use backend can serve. VISION is not one yet: the
#: `vision_*` capabilities read a frame through their own toolset, and routing
#: them here would mark them unavailable for want of a backend they never use.
PATHS = (action_path.DOM, action_path.COORDINATES)


@runtime_checkable
class Backend(Protocol):
    """What a computer-use adapter module provides."""

    #: The action paths this backend serves; each must be in `PATHS`.
    PATHS: tuple[str, ...]

    def start(self) -> Any: ...

    def stop(self, handle: Any) -> None: ...

    def health(self, handle: Any) -> dict: ...

    def call(self, operation: str, handle: Any, **arguments: Any) -> Any: ...


def probe_operation(path: str) -> str:
    """The read-only operation every backend of `path` answers."""
    return f"{path}.probe"


def conforms(module: Any) -> list[str]:
    """What stops `module` being a computer-use backend; empty when it is one."""
    if not isinstance(module, Backend):
        return ["lacks one of PATHS, start, stop, health, call"]
    problems = [f"serves {path!r}, which is not a computer-use path"
                for path in module.PATHS if path not in PATHS]
    descriptor = getattr(module, "DESCRIPTOR", None)
    if descriptor is None:
        return problems + ["has no DESCRIPTOR"]
    if descriptor.family != FAMILY:
        problems.append(f"is in family {descriptor.family!r}, not {FAMILY!r}")
    problems += [f"declares no {probe_operation(path)!r} operation"
                 for path in module.PATHS
                 if probe_operation(path) not in descriptor.operations]
    return problems


@dataclass(frozen=True)
class Route:
    """The backend chosen for one capability, or why none could be."""

    capability_id: str
    path: str
    #: Provider id of the chosen backend; empty when none is up.
    backend: str
    #: The chosen backend's state (READY or DEGRADED), else UNAVAILABLE.
    state: str
    detail: str
    #: (provider id, state, detail) of each backend tried and found down.
    down: tuple[tuple[str, str, str], ...] = ()

    @property
    def usable(self) -> bool:
        return self.state in (fabric.READY, fabric.DEGRADED)


def unavailable_paths(capability_ids, *,
                      authorized: frozenset[str] | None = None) -> frozenset[str]:
    """The computer-use paths that `capability_ids` act through and that have
    no backend up, for `action_path.order_key`: a step whose backend is down
    is tried after the ones whose backend answers. A path no capability here
    acts through is not asked about, so a request that needs no computer use
    activates no backend."""
    wanted = {action_path.path_of(cid) for cid in capability_ids} & set(PATHS)
    return frozenset(path for path in sorted(wanted)
                     if not _serve(path, "", authorized).usable)


def route(capability_id: str, *,
          authorized: frozenset[str] | None = None,
          session: str | None = None, window: str = "", app: str = "",
          sessions: computer_use_sessions.SessionRegistry | None = None,
          ) -> Route | None:
    """The backend that serves `capability_id`, or None when the capability
    does not act through a computer-use path (a connector, a structured tool).

    An action made for a computer-use `session` (CUA-003) names the `window`
    and `app` it acts on. The session's registry (the process one unless
    `sessions` is given) authorizes it first: a window or application outside
    the session's set raises `SessionRefused`, recorded in the registry's
    `refusals()`, and a stopped session raises `SessionStopped`, both before
    any backend is activated. Without a `session` the call only asks which
    backend would serve the capability, as the planner does.
    """
    path = action_path.path_of(capability_id)
    if path not in PATHS:
        return None
    if session is not None:
        (computer_use_sessions.registry() if sessions is None else sessions).authorize(session, window, app)
    return _serve(path, capability_id, authorized)


def _serve(path: str, capability_id: str,
           authorized: frozenset[str] | None) -> Route:
    """The route for one computer-use `path`.

    Candidates come in the fabric's order (cheapest, least risky first); each
    is activated in turn, so a backend that is missing costs one failed
    activation and the next one answers. An already-active backend is asked
    for its health again, because a probe since activation can have shown it
    is not usable. When none is up the route is UNAVAILABLE and says why for
    every backend that was tried.
    """
    usable = (fabric.READY, fabric.DEGRADED)
    down: list[tuple[str, str, str]] = []
    for provider in fabric.candidates(FAMILY, probe_operation(path),
                                      authorized=authorized):
        activation = fabric.activate(provider.id)
        state, detail = activation.state, activation.detail
        if state in usable:
            current = fabric.health(provider.id)
            state, detail = current["state"], current.get("detail", "")
        if state in usable:
            return Route(capability_id, path, provider.id, state, detail,
                         tuple(down))
        down.append((provider.id, state, detail))
    detail = ("; ".join(f"{pid} is {state} ({why})" for pid, state, why in down)
              or f"no backend is registered for the {path} path")
    return Route(capability_id, path, "", fabric.UNAVAILABLE, detail, tuple(down))
