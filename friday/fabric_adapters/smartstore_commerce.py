"""
Smartstore: the second product-trading backend, over its OData Web API.

AGPL-3.0, so the fabric refuses any importing mode at import; SIDECAR is the
only shape it may take, and this provider never links a byte of it - it is an
HTTP client to a store the operator runs (ASP.NET Core, needs SQL Server or
MySQL and a build), named by `SMARTSTORE_URL`. Same reasoning as
`medusa_commerce`: spawning a multi-service commerce stack from a voice turn
is the failure `fabric.py` exists to prevent.

The Web API module is OData v4 under `/odata/v1/`. Auth is HTTP Basic with
`PublicKey:SecretKey` - the upstream's own handler compares the decoded
credential string to exactly that - so the broker alias `smartstore_api_key`
holds the pair in that form and is turned into the header at call time.

Reads are open; writes need `commerce.write`. Orders are read-only here on
purpose: Smartstore's order mutations are OData actions with checkout
side-effects, and NON_NEGOTIABLE 5 keeps payment-adjacent state behind the
confirmation seam rather than a generic write verb.

`medusa_commerce` is the preferred provider for the family (cost order and
declaration order both put it first); this one is the fallback and the route
for a shop that is already Smartstore.
"""
from __future__ import annotations

import base64
import os
import time

from friday import contracts as c
from friday import fabric

UPSTREAM = "smartstore"

ENV_URL = "SMARTSTORE_URL"
DEFAULT_URL = "http://127.0.0.1:5000"
TIMEOUT = 30.0
MAX_ROWS = 100
ODATA = "/odata/v1"

ENDPOINTS = {
    "health":         ("GET",   "/odata/v1/$metadata", False),
    "products":       ("GET",   "/odata/v1/Products", False),
    "product":        ("GET",   "/odata/v1/Products({id})", False),
    "product_create": ("POST",  "/odata/v1/Products", True),
    "product_update": ("PATCH", "/odata/v1/Products({id})", True),
    "categories":     ("GET",   "/odata/v1/Categories", False),
    "manufacturers":  ("GET",   "/odata/v1/Manufacturers", False),
    "orders":         ("GET",   "/odata/v1/Orders", False),
    "order":          ("GET",   "/odata/v1/Orders({id})", False),
    "customers":      ("GET",   "/odata/v1/Customers", False),
    "discounts":      ("GET",   "/odata/v1/Discounts", False),
}

READ_OPS = tuple(op for op, (_, _, w) in ENDPOINTS.items() if not w)

#: OData system query options a read forwards. `$filter` is passed through
#: because it IS the query language; the store parses it, not us.
READ_QUERY_KEYS = ("$filter", "$top", "$skip", "$orderby", "$select",
                   "$expand", "$search", "$count")


def base_url() -> str:
    return (os.getenv(ENV_URL, "") or DEFAULT_URL).strip().rstrip("/")


def _basic(pair: str) -> dict:
    return {"Authorization": "Basic " + base64.b64encode(pair.encode()).decode()}


def _fill(path: str, arguments: dict) -> str:
    if "{id}" in path:
        value = str(arguments.get("id", "")).strip()
        if not value.isdigit():
            raise fabric.FabricError("id is required and must be a numeric key")
        return path.replace("{id}", value)
    return path


def start():
    return {"base_url": base_url()}


def stop(handle=None) -> None:
    """The store is the operator's process; nothing to stop."""


def health(handle=None) -> dict:
    try:
        import httpx
    except ImportError:
        return {"state": fabric.UNAVAILABLE, "detail": "httpx is not installed"}
    try:
        resp = httpx.get(base_url() + ODATA + "/$metadata", timeout=5.0)
    except Exception as exc:  # noqa: BLE001
        return {"state": fabric.UNAVAILABLE,
                "detail": f"no Smartstore at {base_url()} ({type(exc).__name__}); "
                          f"set {ENV_URL} or start the store"}
    if resp.status_code >= 500:
        return {"state": fabric.DEGRADED, "detail": f"$metadata returned {resp.status_code}"}
    return {"state": fabric.READY, "detail": f"OData metadata OK at {base_url()}"}


def call(operation: str, handle=None, *, run_id: str = "", **arguments):
    tool_id = f"fabric.{DESCRIPTOR.id}.{operation}"
    result = c.started(run_id or c.new_run_id(), tool_id)
    secrets = arguments.pop("secrets", None) or {}
    method, path, _ = ENDPOINTS[operation]

    try:
        import httpx
    except ImportError:
        return c.failed(result, "httpx is not installed")
    try:
        url = base_url() + _fill(path, arguments)
    except fabric.FabricError as exc:
        return c.failed(result, str(exc))

    pair = secrets.get("smartstore_api_key", "")
    if not pair or ":" not in pair:
        return c.failed(result, "smartstore_api_key (PublicKey:SecretKey) is not "
                                "set in the secret broker")
    headers = _basic(pair)

    started = time.monotonic()
    try:
        if method == "GET":
            params = {k: v for k, v in arguments.items()
                      if k in READ_QUERY_KEYS and v not in (None, "")}
            params["$top"] = min(int(params.get("$top", 20)), MAX_ROWS)
            resp = httpx.get(url, headers=headers, params=params, timeout=TIMEOUT)
        else:
            body = arguments.get("body")
            if not isinstance(body, dict):
                return c.failed(result, f"{operation} needs a JSON object in `body`")
            resp = httpx.request(method, url, headers=headers, json=body,
                                 timeout=TIMEOUT)
    except Exception as exc:  # noqa: BLE001
        return c.failed(result, f"{operation} did not reach {base_url()}: {exc}")
    elapsed = time.monotonic() - started

    if resp.status_code >= 400:
        return c.failed(result, f"{operation} returned {resp.status_code}: {resp.text[:400]}")
    if operation == "health":
        payload = {"status": resp.status_code, "bytes": len(resp.content)}
    else:
        try:
            payload = resp.json()
        except Exception as exc:  # noqa: BLE001
            return c.failed(result, f"{operation} did not return JSON: {exc}")

    return c.succeeded(
        result,
        verification=c.Verification(
            method="fabric.smartstore",
            evidence=(f"{method} {path} -> {resp.status_code} in {elapsed:.2f}s "
                      f"against {base_url()}; the status is the store's claim"),
        ),
        output=payload,
    )


DESCRIPTOR = fabric.Provider(
    id="smartstore_commerce",
    family="commerce",
    upstream=UPSTREAM,
    operations=tuple(ENDPOINTS),
    risk="medium",
    license_mode=fabric.COPYLEFT,
    integration_mode=fabric.SIDECAR,
    permissions=("commerce.write",),
    open_operations=READ_OPS,
    secrets=("smartstore_api_key",),
    cost_class="free",
    model_required=False,
    commit="3b7d986ecb6c8525b63f5243b686531511c285f9",
    owns_process=False,
    notes=("AGPL-3.0: HTTP client only, never imported. Talks to a store the "
           "operator runs (SMARTSTORE_URL) over OData v4 with "
           "PublicKey:SecretKey Basic auth. Orders are read-only here."),
)
