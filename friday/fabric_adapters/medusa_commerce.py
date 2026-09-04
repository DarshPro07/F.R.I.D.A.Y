"""
Medusa: the product-trading backend, reached over its admin REST API.

MIT core (the `ENTERPRISE-LICENSE.md` subtree is carved out and never
touched). Medusa v2 is a headless commerce engine: products, variants,
inventory, orders, customers, promotions, all behind `/admin/*` with a secret
API key sent as HTTP Basic auth - the upstream's own middleware refuses a
secret key sent as Bearer and says so in its error text.

## Why this does not spawn the store

`fabric_service` can supervise a sidecar, and for a single-process web app
that is right. Medusa is not one: it needs PostgreSQL, a built backend and
(optionally) an admin dashboard and Redis. Starting that from a voice turn is
the "twenty-one sidecars on one Windows box" failure `fabric.py` warns about.
So this provider talks to a store the operator already runs, named by
`MEDUSA_BACKEND_URL` (default `http://127.0.0.1:9000`), and reports
UNAVAILABLE - with the reason - when nothing answers `/health`. An absent
optional upstream never breaks boot (NON_NEGOTIABLE 15).

## What is read and what is written

Reads are open: list/search products, orders, customers, inventory levels.
Writes - creating a product, updating a price, changing an order - carry
real consequence for a live shop, so they need `commerce.write`, which the
policy engine only grants through the confirmation seam. Nothing here
completes a payment or issues a refund: those stay behind
`sensitive_domains` (NON_NEGOTIABLE 5) and are deliberately not operations.

The API key is the secret alias `medusa_admin_key`, resolved by the broker
at call time into the Basic header and never stored on the descriptor.
"""
from __future__ import annotations

import base64
import os
import time

from friday import contracts as c
from friday import fabric

UPSTREAM = "medusa"

ENV_URL = "MEDUSA_BACKEND_URL"
DEFAULT_URL = "http://127.0.0.1:9000"
TIMEOUT = 30.0
MAX_ROWS = 100

#: operation -> (method, path template, write?)  Data, not branches.
ENDPOINTS = {
    "health":           ("GET",  "/health", False),
    "products":         ("GET",  "/admin/products", False),
    "product":          ("GET",  "/admin/products/{id}", False),
    "product_create":   ("POST", "/admin/products", True),
    "product_update":   ("POST", "/admin/products/{id}", True),
    "variants":         ("GET",  "/admin/product-variants", False),
    "inventory":        ("GET",  "/admin/inventory-items", False),
    "inventory_adjust": ("POST", "/admin/inventory-items/{id}/location-levels/{location_id}", True),
    "orders":           ("GET",  "/admin/orders", False),
    "order":            ("GET",  "/admin/orders/{id}", False),
    "customers":        ("GET",  "/admin/customers", False),
    "customer":         ("GET",  "/admin/customers/{id}", False),
    "promotions":       ("GET",  "/admin/promotions", False),
    "regions":          ("GET",  "/admin/regions", False),
}

READ_OPS = tuple(op for op, (_, _, w) in ENDPOINTS.items() if not w)
WRITE_OPS = tuple(op for op, (_, _, w) in ENDPOINTS.items() if w)

#: Query keys a read operation forwards; everything else is dropped so a
#: model cannot smuggle an unknown filter into the store's query parser.
READ_QUERY_KEYS = ("q", "limit", "offset", "order", "status", "fields",
                   "id", "sku", "email", "region_id", "collection_id",
                   "sales_channel_id", "location_id")


def base_url() -> str:
    url = (os.getenv(ENV_URL, "") or DEFAULT_URL).strip().rstrip("/")
    return url


def _basic(secret: str) -> dict:
    token = base64.b64encode(f"{secret}:".encode()).decode()
    return {"Authorization": f"Basic {token}"}


def _fill(path: str, arguments: dict) -> str:
    out = path
    for key in ("id", "location_id"):
        marker = "{" + key + "}"
        if marker in out:
            value = str(arguments.get(key, "")).strip()
            if not value or "/" in value or ".." in value:
                raise fabric.FabricError(f"{key} is required and must be a bare id")
            out = out.replace(marker, value)
    return out


def start():
    return {"base_url": base_url()}


def stop(handle=None) -> None:
    """Nothing to stop: the store is the operator's process, not ours."""


def health(handle=None) -> dict:
    try:
        import httpx
    except ImportError:
        return {"state": fabric.UNAVAILABLE, "detail": "httpx is not installed"}
    url = base_url() + "/health"
    try:
        resp = httpx.get(url, timeout=5.0)
    except Exception as exc:  # noqa: BLE001
        return {"state": fabric.UNAVAILABLE,
                "detail": f"no Medusa at {base_url()} ({type(exc).__name__}); "
                          f"set {ENV_URL} or start the store"}
    if resp.status_code >= 500:
        return {"state": fabric.DEGRADED,
                "detail": f"/health returned {resp.status_code}"}
    return {"state": fabric.READY, "detail": f"/health OK at {base_url()}"}


def call(operation: str, handle=None, *, run_id: str = "", **arguments):
    tool_id = f"fabric.{DESCRIPTOR.id}.{operation}"
    result = c.started(run_id or c.new_run_id(), tool_id)

    secrets = arguments.pop("secrets", None) or {}
    method, path, is_write = ENDPOINTS[operation]

    try:
        import httpx
    except ImportError:
        return c.failed(result, "httpx is not installed")

    try:
        url = base_url() + _fill(path, arguments)
    except fabric.FabricError as exc:
        return c.failed(result, str(exc))

    headers = {}
    if operation != "health":
        key = secrets.get("medusa_admin_key", "")
        if not key:
            return c.failed(result, "medusa_admin_key is not set in the secret broker")
        headers = _basic(key)

    started = time.monotonic()
    try:
        if method == "GET":
            params = {k: v for k, v in arguments.items()
                      if k in READ_QUERY_KEYS and v not in (None, "")}
            params.setdefault("limit", min(int(params.get("limit", 20)), MAX_ROWS))
            resp = httpx.get(url, headers=headers, params=params, timeout=TIMEOUT)
        else:
            body = arguments.get("body")
            if not isinstance(body, dict):
                return c.failed(result, f"{operation} needs a JSON object in `body`")
            # A write is never retried: a retried POST is two products.
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
            method="fabric.medusa",
            evidence=(f"{method} {path} -> {resp.status_code} in {elapsed:.2f}s "
                      f"against {base_url()}; the status is the store's claim"),
        ),
        output=payload,
    )


DESCRIPTOR = fabric.Provider(
    id="medusa_commerce",
    family="commerce",
    upstream=UPSTREAM,
    operations=tuple(ENDPOINTS),
    risk="medium",
    license_mode=fabric.PERMISSIVE,
    integration_mode=fabric.SIDECAR,
    permissions=("commerce.write",),
    open_operations=READ_OPS,
    secrets=("medusa_admin_key",),
    cost_class="free",
    model_required=False,
    commit="6a2fce501f3bcd459c21a67f586c7a15b905ff0f",
    version="v2 (clone pinned; www/ docs excluded by sparse checkout)",
    owns_process=False,
    notes=("MIT core; ENTERPRISE-LICENSE.md subtree untouched. Talks to a "
           "store the operator runs (MEDUSA_BACKEND_URL); does not spawn "
           "postgres+backend. Reads are open; product/inventory writes need "
           "commerce.write. Payments and refunds are not operations."),
)
