"""
OpenMontage: the media-production project board, reached over its Backlot
FastAPI `/api/*` routes (`backlot/server.py`).

AGPL-3.0, so `fabric.Provider` refuses any importing mode outright - this is
an isolated sidecar, no upstream code linked into Friday's process. This
talks to an instance the operator already runs, named by `OPENMONTAGE_URL`
(default `http://127.0.0.1:4750`, Backlot's own `DEFAULT_PORT`), and reports
UNAVAILABLE - with the reason - when nothing answers `/api/health`.

## Why this does not spawn the instance

Same reasoning as `medusa_commerce`: Backlot is the operator's long-lived
project board (`python -m backlot serve`), not something to start from a
voice turn - and OpenMontage's full render pipeline is a heavy service
Friday has no media pipeline slotted beside yet (see UPSTREAM_LOCK.json).

## What is read and what is written

Both operations are open reads: `projects` lists the board's project
summaries, `project` reads one project's board state. Backlot's own API has
no write route (no auth middleware either - it is a local-only board), so
there is nothing here that needs a permission grant.
"""
from __future__ import annotations

import os
import time

from friday import contracts as c
from friday import fabric

UPSTREAM = "openmontage"

ENV_URL = "OPENMONTAGE_URL"
DEFAULT_URL = "http://127.0.0.1:4750"
TIMEOUT = 15.0


def base_url() -> str:
    return (os.getenv(ENV_URL, "") or DEFAULT_URL).strip().rstrip("/")


def start():
    return {"base_url": base_url()}


def stop(handle=None) -> None:
    """Nothing to stop: Backlot is the operator's process, not ours."""


def health(handle=None) -> dict:
    try:
        import httpx
    except ImportError:
        return {"state": fabric.UNAVAILABLE, "detail": "httpx is not installed"}
    url = base_url() + "/api/health"
    try:
        resp = httpx.get(url, timeout=5.0)
    except Exception as exc:  # noqa: BLE001
        return {"state": fabric.UNAVAILABLE,
                "detail": f"unreachable at {base_url()} ({type(exc).__name__}); "
                          f"set {ENV_URL} or start Backlot (`python -m backlot serve`)"}
    if resp.status_code >= 500:
        return {"state": fabric.DEGRADED,
                "detail": f"/api/health returned {resp.status_code}"}
    return {"state": fabric.READY, "detail": f"answered at {base_url()}"}


def call(operation: str, handle=None, *, run_id: str = "", **arguments):
    tool_id = f"fabric.{DESCRIPTOR.id}.{operation}"
    result = c.started(run_id or c.new_run_id(), tool_id)

    arguments.pop("secrets", None)  # no secret this adapter needs

    try:
        import httpx
    except ImportError:
        return c.failed(result, "httpx is not installed")

    if operation == "projects":
        path = "/api/projects"
    elif operation == "project":
        project_id = str(arguments.get("id", "")).strip()
        if not project_id or "/" in project_id:
            return c.failed(result, "project needs a bare `id`")
        path = f"/api/project/{project_id}/state"
    else:
        return c.failed(result, f"unknown operation {operation!r}")

    url = base_url() + path
    started = time.monotonic()
    try:
        resp = httpx.get(url, timeout=TIMEOUT)
    except Exception as exc:  # noqa: BLE001
        return c.failed(result, f"{operation} did not reach {base_url()}: {exc}")
    elapsed = time.monotonic() - started

    if resp.status_code >= 400:
        return c.failed(result, f"{operation} returned {resp.status_code}: "
                                f"{resp.text[:400]}")
    try:
        payload = resp.json()
    except Exception as exc:  # noqa: BLE001
        return c.failed(result, f"{operation} did not return JSON: {exc}")

    return c.succeeded(
        result,
        verification=c.Verification(
            method="fabric.openmontage",
            evidence=(f"GET {path} -> {resp.status_code} in {elapsed:.2f}s "
                      f"against {base_url()}; the status is Backlot's claim"),
        ),
        output=payload,
    )


DESCRIPTOR = fabric.Provider(
    id="openmontage_media",
    family="media",
    upstream=UPSTREAM,
    operations=("projects", "project"),
    risk="low",
    license_mode=fabric.COPYLEFT,
    integration_mode=fabric.SIDECAR,
    permissions=(),
    open_operations=("projects", "project"),
    secrets=(),
    cost_class="free",
    model_required=False,
    commit="cd9f3c1f03368be87b140af494914b8ee4e3c7a4",
    version="cd9f3c1",
    owns_process=False,
    notes=("AGPL-3.0; isolated sidecar, no code linked. Talks to a Backlot "
           "board the operator runs (OPENMONTAGE_URL); does not spawn the "
           "render pipeline. Both operations are open reads; Backlot's own "
           "API has no auth and no write route."),
)
