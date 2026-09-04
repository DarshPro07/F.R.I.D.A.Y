"""
Maxun: persistent scraping robots, reached over its Express `/api/*` REST API.

AGPL-3.0, so `fabric.Provider` refuses any importing mode outright - this is
an isolated sidecar, no upstream code linked into Friday's process. This
talks to an instance the operator already runs, named by `MAXUN_API_URL`
(default `http://127.0.0.1:8080`), and reports UNAVAILABLE - with the reason
- when nothing answers `/api/robots`. Auth is a bare key: upstream's own
`requireAPIKey` middleware reads `x-api-key` and nothing else (never
`Authorization`).

## Why this does not spawn the instance

Same reasoning as `medusa_commerce`, doubled: maxun's docker-compose stands
up postgres, minio, a backend, a frontend and its own browser service - a
third browser alongside Friday's Playwright. Starting that from a voice turn
is exactly the failure `fabric.py` warns about.

## What is read and what is written

`robots`, `runs` and `results` are open reads: listing robots, a robot's
past runs, and one run's scraped output changes nothing on the far side.
`run_robot` starts a real browser automation against a real site and can
take minutes, so it needs `scraping.run`, which the policy engine only
grants through the confirmation seam.

The key is the secret alias `maxun_api_key`, resolved by the broker at call
time and never stored on the descriptor.
"""
from __future__ import annotations

import os
import time

from friday import contracts as c
from friday import fabric

UPSTREAM = "maxun"

ENV_URL = "MAXUN_API_URL"
DEFAULT_URL = "http://127.0.0.1:8080"
TIMEOUT = 120.0  # run_robot drives a real browser session


def base_url() -> str:
    return (os.getenv(ENV_URL, "") or DEFAULT_URL).strip().rstrip("/")


def _auth(secret: str) -> dict:
    return {"x-api-key": secret}


def start():
    return {"base_url": base_url()}


def stop(handle=None) -> None:
    """Nothing to stop: maxun is the operator's process, not ours."""


def health(handle=None) -> dict:
    try:
        import httpx
    except ImportError:
        return {"state": fabric.UNAVAILABLE, "detail": "httpx is not installed"}
    url = base_url() + "/api/robots"
    try:
        resp = httpx.get(url, timeout=5.0)
    except Exception as exc:  # noqa: BLE001
        return {"state": fabric.UNAVAILABLE,
                "detail": f"unreachable at {base_url()} ({type(exc).__name__}); "
                          f"set {ENV_URL} or start maxun"}
    if resp.status_code >= 500:
        return {"state": fabric.DEGRADED,
                "detail": f"/api/robots returned {resp.status_code}"}
    return {"state": fabric.READY, "detail": f"answered at {base_url()}"}


def call(operation: str, handle=None, *, run_id: str = "", **arguments):
    tool_id = f"fabric.{DESCRIPTOR.id}.{operation}"
    result = c.started(run_id or c.new_run_id(), tool_id)

    secrets = arguments.pop("secrets", None) or {}
    key = secrets.get("maxun_api_key", "")
    if not key:
        return c.failed(result, "maxun_api_key is not set in the secret broker")
    headers = _auth(key)

    try:
        import httpx
    except ImportError:
        return c.failed(result, "httpx is not installed")

    if operation == "robots":
        method, path, body = "GET", "/api/robots", None
    elif operation == "runs":
        robot = str(arguments.get("robot", "")).strip()
        if not robot or "/" in robot:
            return c.failed(result, "runs needs a bare `robot` id")
        method, path, body = "GET", f"/api/robots/{robot}/runs", None
    elif operation == "results":
        robot = str(arguments.get("robot", "")).strip()
        run = str(arguments.get("run", "")).strip()
        if not robot or "/" in robot:
            return c.failed(result, "results needs a bare `robot` id")
        if not run or "/" in run:
            return c.failed(result, "results needs a bare `run` id")
        method, path, body = "GET", f"/api/robots/{robot}/runs/{run}", None
    elif operation == "run_robot":
        robot = str(arguments.get("robot", "")).strip()
        if not robot or "/" in robot:
            return c.failed(result, "run_robot needs a bare `robot` id")
        method = "POST"
        path = f"/api/robots/{robot}/runs"
        body = {}
        if arguments.get("formats"):
            body["formats"] = arguments["formats"]
        if arguments.get("promptInstructions"):
            body["promptInstructions"] = arguments["promptInstructions"]
    else:
        return c.failed(result, f"unknown operation {operation!r}")

    url = base_url() + path
    started = time.monotonic()
    try:
        if method == "GET":
            resp = httpx.get(url, headers=headers, timeout=TIMEOUT)
        else:
            resp = httpx.post(url, headers=headers, json=body, timeout=TIMEOUT)
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
            method="fabric.maxun",
            evidence=(f"{method} {path} -> {resp.status_code} in {elapsed:.2f}s "
                      f"against {base_url()}; the status is maxun's claim"),
        ),
        output=payload,
    )


DESCRIPTOR = fabric.Provider(
    id="maxun_scraping",
    family="scraping",
    upstream=UPSTREAM,
    operations=("robots", "runs", "results", "run_robot"),
    risk="medium",
    license_mode=fabric.COPYLEFT,
    integration_mode=fabric.SIDECAR,
    permissions=("scraping.run",),
    open_operations=("robots", "runs", "results"),
    secrets=("maxun_api_key",),
    cost_class="free",
    model_required=False,
    commit="4fc597d9ca7ed0960e7564e2ddfbbc810c2d6618",
    version="v0.0.46",
    owns_process=False,
    notes=("AGPL-3.0; isolated sidecar, no code linked. Talks to a maxun "
           "instance the operator runs (MAXUN_API_URL); does not spawn its "
           "postgres+minio+backend+frontend+browser stack. `robots`/`runs`/"
           "`results` are open reads; `run_robot` drives a real browser and "
           "needs scraping.run."),
)
