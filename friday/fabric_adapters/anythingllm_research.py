"""
AnythingLLM: the document/RAG workspace backend, reached over its REST API.

MIT root (`open-computer/` is a nested AGPL subtree - this adapter never
touches it, only the plain `/api/v1` workspace/document routes). This talks
to an instance the operator already runs, named by `ANYTHINGLLM_URL`
(default `http://127.0.0.1:3001`), and reports UNAVAILABLE - with the reason
- when nothing answers. Auth is a bearer key: the upstream's own
`validApiKey` middleware reads `Authorization: Bearer <key>` and nothing
else.

## Why this does not spawn the instance

Same reasoning as `medusa_commerce`: AnythingLLM needs its own storage and
vector DB, not something to start from a voice turn.

## What is read and what is written

`workspaces`, `ask` and `documents` are all open reads: `ask` runs a
workspace chat and returns its answer, it does not create or delete
anything server-side that a human need approve first.

The API key is the secret alias `anythingllm_api_key`, resolved by the
broker at call time and never stored on the descriptor.
"""
from __future__ import annotations

import os
import time

from friday import contracts as c
from friday import fabric

UPSTREAM = "anythingllm"

ENV_URL = "ANYTHINGLLM_URL"
DEFAULT_URL = "http://127.0.0.1:3001"
TIMEOUT = 60.0  # chat can be slow: it's an LLM call on the far side


def base_url() -> str:
    return (os.getenv(ENV_URL, "") or DEFAULT_URL).strip().rstrip("/")


def _auth(secret: str) -> dict:
    return {"Authorization": f"Bearer {secret}"}


def start():
    return {"base_url": base_url()}


def stop(handle=None) -> None:
    """Nothing to stop: AnythingLLM is the operator's process, not ours."""


def health(handle=None) -> dict:
    try:
        import httpx
    except ImportError:
        return {"state": fabric.UNAVAILABLE, "detail": "httpx is not installed"}
    url = base_url() + "/api/v1/workspaces"
    try:
        resp = httpx.get(url, timeout=5.0)
    except Exception as exc:  # noqa: BLE001
        return {"state": fabric.UNAVAILABLE,
                "detail": f"unreachable at {base_url()} ({type(exc).__name__}); "
                          f"set {ENV_URL} or start AnythingLLM"}
    if resp.status_code >= 500:
        return {"state": fabric.DEGRADED,
                "detail": f"/api/v1/workspaces returned {resp.status_code}"}
    return {"state": fabric.READY, "detail": f"answered at {base_url()}"}


def call(operation: str, handle=None, *, run_id: str = "", **arguments):
    tool_id = f"fabric.{DESCRIPTOR.id}.{operation}"
    result = c.started(run_id or c.new_run_id(), tool_id)

    secrets = arguments.pop("secrets", None) or {}

    try:
        import httpx
    except ImportError:
        return c.failed(result, "httpx is not installed")

    key = secrets.get("anythingllm_api_key", "")
    if not key:
        return c.failed(result, "anythingllm_api_key is not set in the secret broker")
    headers = _auth(key)

    if operation == "workspaces":
        method, path, body = "GET", "/api/v1/workspaces", None
    elif operation == "documents":
        method, path, body = "GET", "/api/v1/documents", None
    elif operation == "ask":
        workspace = str(arguments.get("workspace", "")).strip()
        question = str(arguments.get("question", "")).strip()
        if not workspace:
            return c.failed(result, "ask needs `workspace`")
        if not question:
            return c.failed(result, "ask needs `question`")
        method = "POST"
        path = f"/api/v1/workspace/{workspace}/chat"
        body = {"message": question, "mode": "chat"}
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
            method="fabric.anythingllm",
            evidence=(f"{method} {path} -> {resp.status_code} in {elapsed:.2f}s "
                      f"against {base_url()}; the status is the store's claim"),
        ),
        output=payload,
    )


DESCRIPTOR = fabric.Provider(
    id="anythingllm_research",
    family="research",
    upstream=UPSTREAM,
    operations=("workspaces", "ask", "documents"),
    risk="low",
    license_mode=fabric.PERMISSIVE,
    integration_mode=fabric.SIDECAR,
    permissions=(),
    open_operations=("workspaces", "ask", "documents"),
    secrets=("anythingllm_api_key",),
    cost_class="free",
    model_required=False,
    commit="35c58d89907e675a8c4fb10544c19be0f050f611",
    version="v1.8.1 (root MIT; open-computer/ AGPL subtree untouched)",
    owns_process=False,
    notes=("MIT root; nested open-computer/ subtree is AGPL-3.0 and this "
           "adapter never imports or touches it. Talks to an AnythingLLM "
           "instance the operator runs (ANYTHINGLLM_URL); does not spawn "
           "it. All three operations are open reads."),
)
