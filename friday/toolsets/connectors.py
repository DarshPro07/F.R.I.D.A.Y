"""
ConnectorControlPlane capabilities in the ActionResult contract.

Executor-facing half of friday/connectors/plane.py, so a durable
objective ("connect Claude then resume my task") can drive connector
work through the same scheduler as everything else. Domain-prefix rule
binds connector_list -> toolsets.connectors.list_ etc.; full-name
functions are provided where the suffix is a Python builtin.
"""

from __future__ import annotations

from friday import contracts as c
from friday.policy import PolicyEngine, default_engine

_plane = None


def plane():
    from friday.connectors.plane import ConnectorControlPlane

    global _plane
    if _plane is None:
        _plane = ConnectorControlPlane()
    return _plane


def reset_plane(new=None) -> None:
    global _plane
    _plane = new


def _step_result(run: c.Run, started, step) -> c.ActionResult:
    payload = {"say": step.say, **step.detail}
    if step.action == "done":
        return run.record(c.succeeded(
            started, output=payload,
            verification=c.Verification(method="connector_flow",
                                        evidence=step.say[:200])))
    if step.action == "human_step":
        # A genuine user boundary: cancelled-not-failed, so the objective
        # parks at USER_REQUIRED rather than burning retries.
        return run.record(c.cancelled(started, step.say))
    return run.record(c.failed(started, step.say))


def connector_list(run: c.Run, *,
                   engine: PolicyEngine = default_engine) -> c.ActionResult:
    started = c.started(run.run_id, "connector.list")
    try:
        found = plane().discover_connectors()
    except Exception as exc:                                 # noqa: BLE001
        return run.record(c.failed(
            started, f"registry unreachable: {type(exc).__name__}"))
    return run.record(c.observed(
        started,
        output={"connected": [f["connector"] for f in found
                              if f["authenticated"]],
                "count": len(found)}))


def connector_describe(run: c.Run, connector: str, *,
                       engine: PolicyEngine = default_engine
                       ) -> c.ActionResult:
    started = c.started(run.run_id, "connector.describe")
    try:
        described = plane().describe_connector(connector)
    except Exception as exc:                                 # noqa: BLE001
        return run.record(c.failed(
            started, f"registry unreachable: {type(exc).__name__}"))
    return run.record(c.observed(started, output=described))


def connector_connect(run: c.Run, connector: str, *, model: str = "",
                      engine: PolicyEngine = default_engine
                      ) -> c.ActionResult:
    started = c.started(run.run_id, "connector.connect")
    try:
        step = plane().begin_connection(connector, model=model)
    except Exception as exc:                                 # noqa: BLE001
        return run.record(c.failed(started, f"{type(exc).__name__}: {exc}"))
    return _step_result(run, started, step)


def connector_verify(run: c.Run, connector: str, *, model: str = "",
                     engine: PolicyEngine = default_engine
                     ) -> c.ActionResult:
    started = c.started(run.run_id, "connector.verify")
    try:
        step = plane().verify_connection(connector, expected_model=model)
    except Exception as exc:                                 # noqa: BLE001
        return run.record(c.failed(started, f"{type(exc).__name__}: {exc}"))
    return _step_result(run, started, step)


def connector_smoke(run: c.Run, connector: str, *, model: str = "",
                    engine: PolicyEngine = default_engine
                    ) -> c.ActionResult:
    started = c.started(run.run_id, "connector.smoke")
    try:
        step = plane().smoke_test(connector, model=model)
    except Exception as exc:                                 # noqa: BLE001
        return run.record(c.failed(started, f"{type(exc).__name__}: {exc}"))
    return _step_result(run, started, step)


def connector_status(run: c.Run, *,
                     engine: PolicyEngine = default_engine
                     ) -> c.ActionResult:
    started = c.started(run.run_id, "connector.status")
    try:
        dashboard = plane().status()
    except Exception as exc:                                 # noqa: BLE001
        return run.record(c.failed(
            started, f"state unreachable: {type(exc).__name__}"))
    return run.record(c.observed(started, output=dashboard))


def connector_repair(run: c.Run, connector: str, *,
                     engine: PolicyEngine = default_engine
                     ) -> c.ActionResult:
    started = c.started(run.run_id, "connector.repair")
    try:
        step = plane().repair(connector)
    except Exception as exc:                                 # noqa: BLE001
        return run.record(c.failed(started, f"{type(exc).__name__}: {exc}"))
    return _step_result(run, started, step)
