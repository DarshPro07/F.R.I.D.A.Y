# FABRIC-SVC-01 — An HTTP service contract for sidecar upstreams

**Closes:** G5, G6, and G2 for seven upstreams
**Status:** specified, not built · **Depends on:** `FABRIC-PROC-01`,
`FABRIC-GATE-01`

## Why

`bolt.diy`, `onlook`, `open-lovable`, `postiz`, `maxun`, `anythingllm` and
`open-notebook` are web applications. `SIDECAR` names the shape but supplies
no client: no base-URL discovery, no auth, no timeout, no retry. Today an
adapter author would hand-write all of it, differently each time, and the one
existing `SIDECAR` provider (`graft`) does not even set `owns_process`.

Four of these are copyleft. A supervised HTTP boundary is the compliant path
the licence invariant already assumes exists (G6).

## Interface

New module `friday/fabric_service.py`. `SIDECAR` keeps its name; this gives it
a runtime.

```python
@dataclass(frozen=True)
class Service:
    spec: fabric_process.Spec        # how to start it (FABRIC-PROC-01)
    base_url: str = "http://127.0.0.1:{port}"
    health_path: str = "/health"
    timeout: float = 30.0
    retries: int = 1                 # idempotent GETs only
    auth: str = "NONE"               # NONE | BEARER | HEADER
    auth_secret: str = ""            # secret NAME, resolved by the broker

@dataclass(frozen=True)
class Endpoint:
    method: str                      # GET | POST
    path: str                        # "/api/generate", {} placeholders allowed
    body: str = "JSON"               # JSON | FORM | NONE
    expect: str = "JSON"             # JSON | TEXT | STATUS

def request(provider_id: str, operation: str, **arguments) -> c.ActionResult
```

An adapter declaring `SIDECAR` exposes `SERVICE: Service` and
`ENDPOINTS: dict[str, Endpoint]`. It writes no HTTP code.

## Behaviour

**Binding is loopback-only, always.** `127.0.0.1`, never `0.0.0.0`. A
third-party web app started by Friday must not be reachable from the network.
Not configurable; a provider needing otherwise is a conversation, not a flag.

**The port comes from the supervisor.** `base_url` is formatted with the port
`fabric_process.spawn()` allocated. There is no port field in the descriptor
and no port table — G3's allocation is the single source, so the two cannot
disagree.

**Auth secrets are resolved by name at call time**, through the secret broker,
and injected as a header. The secret value never appears in `Service`, never
in a log line, and never in `ActionResult` evidence. The child's environment
still gets only what `Spec.env` declares (FABRIC-PROC-01), so a service needing
a key gets that key and no other.

**Retries are for idempotent GETs only.** A `POST` is never retried
automatically: "generate a site" retried on a timeout is two sites. On timeout
a `POST` returns `failed` with the elapsed time, and the caller decides.

**netguard applies.** These are localhost calls to a supervised child, not
egress, but the *child* may make egress calls of its own. The descriptor must
declare `permissions` covering that (e.g. `network.egress`), and
`FABRIC-GATE-01` enforces it at `call()`. A provider that reaches the internet
without declaring it is the failure this clause exists to prevent.

**Lifecycle is lazy and shared.** The service starts on first `request()` and
stays up for `IDLE_TTL` (default 600 s) after the last one, then is stopped by
the supervisor. Twenty-one always-on sidecars on one Windows box is the exact
wreckage `fabric.py`'s own docstring names; idle shutdown is how this mode
avoids recreating it.

**Health is a real request.** `health_path` is fetched, not assumed. A service
whose process is alive but whose HTTP surface returns 500 is `DEGRADED`, and
that distinction is what G8 is about.

## Acceptance

Tests in `tests/test_fabric_service.py`, against a local stub server:

1. The client uses the supervisor's allocated port; no port is hard-coded.
2. A bearer secret is sent as a header and appears in **no** log line and in
   **no** `ActionResult` evidence field.
3. A timed-out `POST` is not retried; a timed-out `GET` is retried once.
4. A 500 from `health_path` yields `DEGRADED`, not `UNAVAILABLE`.
5. The service binds `127.0.0.1` and is not reachable on the LAN address.
6. After `IDLE_TTL` with no traffic the child is stopped and the port released.
7. A request to a service whose child crashed returns `failed` naming the exit
   code, and triggers at most `max_restarts` respawns.
8. An AGPL provider declaring `SIDECAR` constructs successfully; the same
   provider declaring `ADAPTER` still raises `FabricError`.

## First adapter

`open-lovable` — MIT, single HTTP surface, no credentials for the local path.
`maxun` (AGPL) follows as the deliberate proof that copyleft now has a
compliant route, which is the whole of G6.
