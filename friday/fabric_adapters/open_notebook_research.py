"""
Open Notebook: the NotebookLM-style research notebook, reached over its
FastAPI `/api/*` routes.

MIT (root; no nested subtree carve-out needed). This talks to an instance the
operator already runs, named by `OPEN_NOTEBOOK_URL` (default
`http://127.0.0.1:5055`, its own default port), and reports UNAVAILABLE -
with the reason - when nothing answers `/api/notebooks`. Auth is optional
upstream (`/api/auth/status` reports `auth_enabled`): when set, the password
goes in `Authorization: Bearer <password>`, per `api/auth.py`'s own check.

## Why this does not spawn the instance

Same reasoning as `medusa_commerce`: Open Notebook needs SurrealDB and its
own container, not something to start from a voice turn.

## What is read and what is written

`notebooks` and `notebook` are open reads. `ask` runs a chat turn against a
notebook and returns the answer; upstream models this as create-a-session
then execute-a-turn, so this adapter does both in one call and treats the
whole thing as a read - it queries the notebook's content, it does not add
anything to it. `add_source` (attaching a new URL to a notebook) is a write:
it grows the operator's corpus, so it needs `research.write`.

The password is the secret alias `open_notebook_password`, resolved by the
broker at call time and never stored on the descriptor.
"""
from __future__ import annotations

import os
import time

from friday import contracts as c
from friday import fabric

UPSTREAM = "open-notebook"

ENV_URL = "OPEN_NOTEBOOK_URL"
DEFAULT_URL = "http://127.0.0.1:5055"
TIMEOUT = 60.0  # ask runs an LLM call on the far side


def base_url() -> str:
    return (os.getenv(ENV_URL, "") or DEFAULT_URL).strip().rstrip("/")


def _auth(secret: str) -> dict:
    return {"Authorization": f"Bearer {secret}"} if secret else {}


def start():
    return {"base_url": base_url()}


def stop(handle=None) -> None:
    """Nothing to stop: Open Notebook is the operator's process, not ours."""


def health(handle=None) -> dict:
    try:
        import httpx
    except ImportError:
        return {"state": fabric.UNAVAILABLE, "detail": "httpx is not installed"}
    url = base_url() + "/api/notebooks"
    try:
        resp = httpx.get(url, timeout=5.0)
    except Exception as exc:  # noqa: BLE001
        return {"state": fabric.UNAVAILABLE,
                "detail": f"unreachable at {base_url()} ({type(exc).__name__}); "
                          f"set {ENV_URL} or start Open Notebook"}
    if resp.status_code >= 500:
        return {"state": fabric.DEGRADED,
                "detail": f"/api/notebooks returned {resp.status_code}"}
    return {"state": fabric.READY, "detail": f"answered at {base_url()}"}


def call(operation: str, handle=None, *, run_id: str = "", **arguments):
    tool_id = f"fabric.{DESCRIPTOR.id}.{operation}"
    result = c.started(run_id or c.new_run_id(), tool_id)

    secrets = arguments.pop("secrets", None) or {}

    try:
        import httpx
    except ImportError:
        return c.failed(result, "httpx is not installed")

    headers = _auth(secrets.get("open_notebook_password", ""))
    started = time.monotonic()

    try:
        if operation == "notebooks":
            resp = httpx.get(base_url() + "/api/notebooks", headers=headers,
                              timeout=TIMEOUT)
        elif operation == "notebook":
            notebook_id = str(arguments.get("id", "")).strip()
            if not notebook_id:
                return c.failed(result, "notebook needs `id`")
            resp = httpx.get(base_url() + f"/api/notebooks/{notebook_id}",
                              headers=headers, timeout=TIMEOUT)
        elif operation == "add_source":
            notebook_id = str(arguments.get("notebook", "")).strip()
            url = str(arguments.get("url", "")).strip()
            if not notebook_id:
                return c.failed(result, "add_source needs `notebook`")
            if not url:
                return c.failed(result, "add_source needs `url`")
            body = {"notebooks": [notebook_id], "type": "link", "url": url}
            resp = httpx.post(base_url() + "/api/sources", headers=headers,
                               json=body, timeout=TIMEOUT)
        elif operation == "ask":
            notebook_id = str(arguments.get("notebook", "")).strip()
            question = str(arguments.get("question", "")).strip()
            if not notebook_id:
                return c.failed(result, "ask needs `notebook`")
            if not question:
                return c.failed(result, "ask needs `question`")
            session = httpx.post(base_url() + "/api/chat/sessions", headers=headers,
                                  json={"notebook_id": notebook_id}, timeout=TIMEOUT)
            if session.status_code >= 400:
                return c.failed(result, f"ask could not open a session: "
                                        f"{session.status_code}: {session.text[:400]}")
            session_id = session.json().get("id", "")
            resp = httpx.post(base_url() + "/api/chat/execute", headers=headers,
                               json={"session_id": session_id, "message": question,
                                     "context": {}}, timeout=TIMEOUT)
        else:
            return c.failed(result, f"unknown operation {operation!r}")
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
            method="fabric.open_notebook",
            evidence=(f"{operation} -> {resp.status_code} in {elapsed:.2f}s "
                      f"against {base_url()}; the status is the notebook's claim"),
        ),
        output=payload,
    )


DESCRIPTOR = fabric.Provider(
    id="open_notebook_research",
    family="research",
    upstream=UPSTREAM,
    operations=("notebooks", "notebook", "ask", "add_source"),
    risk="low",
    license_mode=fabric.PERMISSIVE,
    integration_mode=fabric.SIDECAR,
    permissions=("research.write",),
    open_operations=("notebooks", "notebook", "ask"),
    secrets=("open_notebook_password",),
    cost_class="free",
    model_required=False,
    commit="a7de90d38aaf18ee85fd661854d35c11e44613e2",
    version="v1.14.0 (root MIT)",
    owns_process=False,
    notes=("MIT root. Talks to an Open Notebook instance the operator runs "
           "(OPEN_NOTEBOOK_URL); does not spawn SurrealDB or the app. "
           "`notebooks`/`notebook`/`ask` are open reads; `ask` opens a chat "
           "session and executes one turn against upstream's two-call API. "
           "`add_source` grows the corpus and needs research.write."),
)
