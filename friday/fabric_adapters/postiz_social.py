"""
Postiz: the social-scheduling backend, reached over its public API.

AGPL-3.0, so this is COPYLEFT/ISOLATED_SIDECAR (NON_NEGOTIABLE 9): nothing
here imports Postiz code, it only speaks HTTP to a process the operator runs
and named by `POSTIZ_API_URL` (default `http://127.0.0.1:5000`), under
`/public/v1`. Auth is a raw API key sent in `Authorization` - the upstream's
own middleware reads `req.headers.authorization` verbatim, no `Bearer `
prefix.

## Why this does not spawn Postiz

Postiz is a full scheduling app with its own database and worker, not a
single-process web app `fabric_service` could supervise from a voice turn.
So, exactly like `medusa_commerce`, this talks to a store already running and
reports UNAVAILABLE - with the reason - when nothing answers.

## What is read and what is written

`integrations`, `queue` (scheduled posts) and `status` are open reads.
`schedule` creates a real post on a real channel, so it needs
`social.publish`, which NON_NEGOTIABLE 13 already requires human confirmation
for before anything reaches the outside world.

The API key is the secret alias `postiz_api_key`, resolved by the broker at
call time and never stored on the descriptor.
"""
from __future__ import annotations

import os
import time

from friday import contracts as c
from friday import fabric

UPSTREAM = "postiz"

ENV_URL = "POSTIZ_API_URL"
DEFAULT_URL = "http://127.0.0.1:5000"
TIMEOUT = 30.0

#: operation -> (method, path, write?)  Data, not branches.
ENDPOINTS = {
    "health":       ("GET",  "/public/v1/integrations", False),
    "integrations": ("GET",  "/public/v1/integrations", False),
    "queue":        ("GET",  "/public/v1/posts", False),
    "status":       ("GET",  "/public/v1/integrations", False),
    "schedule":     ("POST", "/public/v1/posts", True),
}

READ_OPS = tuple(op for op, (_, _, w) in ENDPOINTS.items() if not w)
WRITE_OPS = tuple(op for op, (_, _, w) in ENDPOINTS.items() if w)


def base_url() -> str:
    return (os.getenv(ENV_URL, "") or DEFAULT_URL).strip().rstrip("/")


def _auth(secret: str) -> dict:
    # Raw key, no "Bearer " prefix: Postiz reads req.headers.authorization
    # verbatim (see public.auth.middleware.ts).
    return {"Authorization": secret}


def _schedule_body(arguments: dict) -> dict:
    text = str(arguments.get("text", "")).strip()
    when = str(arguments.get("when", "")).strip()
    integrations = arguments.get("integrations") or []
    if not text:
        raise fabric.FabricError("schedule needs `text`")
    if not when:
        raise fabric.FabricError("schedule needs `when`")
    if not isinstance(integrations, list) or not integrations:
        raise fabric.FabricError("schedule needs a non-empty `integrations` list")
    return {
        "type": "schedule",
        "date": when,
        "posts": [
            {"integration": {"id": str(iid)}, "value": [{"content": text}]}
            for iid in integrations
        ],
    }


def start():
    return {"base_url": base_url()}


def stop(handle=None) -> None:
    """Nothing to stop: Postiz is the operator's process, not ours."""


def health(handle=None) -> dict:
    try:
        import httpx
    except ImportError:
        return {"state": fabric.UNAVAILABLE, "detail": "httpx is not installed"}
    url = base_url() + "/public/v1/integrations"
    try:
        resp = httpx.get(url, timeout=5.0)
    except Exception as exc:  # noqa: BLE001
        return {"state": fabric.UNAVAILABLE,
                "detail": f"unreachable at {base_url()} ({type(exc).__name__}); "
                          f"set {ENV_URL} or start Postiz"}
    if resp.status_code >= 500:
        return {"state": fabric.DEGRADED,
                "detail": f"/public/v1/integrations returned {resp.status_code}"}
    return {"state": fabric.READY, "detail": f"answered at {base_url()}"}


def call(operation: str, handle=None, *, run_id: str = "", **arguments):
    tool_id = f"fabric.{DESCRIPTOR.id}.{operation}"
    result = c.started(run_id or c.new_run_id(), tool_id)

    secrets = arguments.pop("secrets", None) or {}
    method, path, is_write = ENDPOINTS[operation]

    try:
        import httpx
    except ImportError:
        return c.failed(result, "httpx is not installed")

    key = secrets.get("postiz_api_key", "")
    if not key:
        return c.failed(result, "postiz_api_key is not set in the secret broker")
    headers = _auth(key)

    url = base_url() + path
    started = time.monotonic()
    try:
        if method == "GET":
            resp = httpx.get(url, headers=headers, timeout=TIMEOUT)
        else:
            try:
                body = _schedule_body(arguments)
            except fabric.FabricError as exc:
                return c.failed(result, str(exc))
            # A write is never retried: a retried POST is two posts.
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
            method="fabric.postiz",
            evidence=(f"{method} {path} -> {resp.status_code} in {elapsed:.2f}s "
                      f"against {base_url()}; the status is the store's claim"),
        ),
        output=payload,
    )


DESCRIPTOR = fabric.Provider(
    id="postiz_social",
    family="social",
    upstream=UPSTREAM,
    operations=tuple(ENDPOINTS),
    risk="medium",
    license_mode=fabric.COPYLEFT,
    integration_mode=fabric.SIDECAR,
    permissions=("social.publish",),
    open_operations=READ_OPS,
    secrets=("postiz_api_key",),
    cost_class="free",
    model_required=False,
    commit="0f1647f7491a217d43eb5ae7a480484bdf0aff3e",
    version="0f1647f",
    owns_process=False,
    notes=("AGPL-3.0; ISOLATED_SIDECAR, nothing imported. Talks to a Postiz "
           "instance the operator runs (POSTIZ_API_URL); does not spawn it. "
           "integrations/queue/status are open reads; schedule needs "
           "social.publish and human confirmation before it posts."),
)
