# FABRIC-PROC-01 — Supervised child processes for the capability fabric

**Closes:** G3, G6 (`docs/audit/2026-09-01-INTEGRATION-GAP-AUDIT.md`)
**Status:** specified, not built · **Depends on:** nothing · **Blocks:**
`FABRIC-CLI-01`, `FABRIC-SVC-01`

## Why

`fabric.activate()` calls an adapter's `start()` and then immediately polls
`health()`. There is no readiness wait, no log capture, no crash detection, no
restart, and no orphan cleanup. `SIDECAR` is a word in `INTEGRATION_MODES`
with no runtime behind it — which is why exactly one of sixteen providers owns
a process, and why every copyleft upstream is stuck: the isolation the licence
invariant demands has nowhere to run.

## Non-goals

- Not a container runtime. No Docker, no cgroups. Windows-first, one machine.
- Not a scheduler. Objectives own scheduling; this owns one child's lifetime.
- Not a second registry. `Provider` stays the declaration; this is the runtime.

## Interface

New module `friday/fabric_process.py`.

```python
@dataclass
class Spec:
    argv: tuple[str, ...]          # already resolved; no shell
    cwd: pathlib.Path              # the clone root, normally
    env: dict[str, str]            # ADDED to a scrubbed base, never replaces os.environ
    ready: Ready                   # how to know it is up (below)
    stop_timeout: float = 10.0     # graceful, then kill
    max_restarts: int = 3          # inside restart_window
    restart_window: float = 300.0

class Ready(Protocol):
    def check(self, child: "Child") -> bool: ...

class LogLine(Ready):      # a regex appears on stdout/stderr
class TcpPort(Ready):      # a port accepts a connection
class HttpOk(Ready):       # a URL returns < 500
class Immediate(Ready):    # process is up == ready (CLI one-shots)

def spawn(provider_id: str, spec: Spec, *, timeout: float = 60.0) -> Child
def stop(provider_id: str) -> None          # graceful, then kill, then verify
def child(provider_id: str) -> Child | None
def logs(provider_id: str, tail: int = 200) -> list[str]
def reap_orphans() -> list[int]             # PIDs killed
```

`Child` exposes `pid`, `state` (`STARTING`/`READY`/`CRASHED`/`STOPPED`),
`started_at`, `restarts`, `last_error`, and `port` when one was allocated.

## Behaviour

**Ports.** `spawn` allocates by binding port 0, reading the assignment, closing,
and passing it in `env`. Racy in principle; the alternative is a hand-maintained
port table, which is racy in practice and drifts besides. The allocated port is
recorded on `Child` so `FABRIC-SVC-01` can find it without a second registry.

**Readiness.** `spawn` blocks until `ready.check()` passes or `timeout` elapses.
On timeout the child is stopped and `spawn` raises `FabricError` naming the last
20 log lines. This is the specific failure that is currently silent.

**Logs.** `stdout`/`stderr` are drained on a daemon thread into a bounded
`deque` (2,000 lines) and mirrored to `logs/fabric/<provider_id>.log`. The
in-memory tail exists so a health probe can quote a reason without disk I/O in
a voice turn.

**Crash and restart.** The drain thread notices EOF and marks `CRASHED`.
Restart is attempted up to `max_restarts` within `restart_window`, with backoff
`2**n` seconds capped at 30. Beyond that the child stays `CRASHED` and
`fabric.health()` reports `UNAVAILABLE` with the exit code and log tail.
Deliberately not infinite: a crash loop that silently retries forever is how one
bad clone eats a laptop.

**Environment scrubbing.** The child gets `PATH`, `SYSTEMROOT`, `TEMP`, plus
exactly what `Spec.env` declares. It does **not** inherit Friday's environment,
because that is where `GOOGLE_API_KEY` and every other secret lives, and
handing all of them to a third-party clone is the leak G7 is about.

**Orphans.** `reap_orphans()` matches `PROCESS_MARKER` the way
`fabric.processes()` already does, and kills PIDs this process did not spawn.
Called at fabric startup, so a hard kill of Friday does not leave a port held.

## Wiring into `fabric.py`

`activate()` gains: if the adapter exposes `PROCESS_SPEC`, the fabric calls
`fabric_process.spawn()` itself instead of the adapter's `start()`. Adapters
that already implement `start()` keep working unchanged — `codebase_memory` and
`graft` must not need edits for this to land.

`deactivate()` routes to `fabric_process.stop()` when a `Child` exists, and
stops swallowing the failure: an un-stoppable child is escalated to a kill and
then reported, not shrugged at.

`processes()` gains a `supervised` key listing children this module owns, so
duplicate detection can distinguish "our child" from "someone else's leftover".

## Acceptance

Tests in `tests/test_fabric_process.py`, each failing before the change:

1. A child that never prints its ready line is stopped and `spawn` raises,
   quoting the log tail; no orphan PID survives.
2. A child that exits 1 at startup is `CRASHED` with the exit code available,
   not `READY`.
3. Killing a `READY` child externally flips it to `CRASHED` within 2 s.
4. Restart backoff stops at `max_restarts`; the 4th failure does not respawn.
5. `stop()` on a process ignoring SIGTERM kills it, and the PID is gone.
6. The child's environment does not contain `GOOGLE_API_KEY` even though
   Friday's does.
7. Two `spawn` calls for the same provider get different ports.
8. `reap_orphans()` kills a marker-matching process it did not spawn.

## Risks

- **Windows signal semantics.** `terminate()` is not `SIGTERM`; graceful stop
  must go through the child's own protocol where one exists (MCP shutdown,
  HTTP `/shutdown`), with `terminate()` as fallback and `kill()` as backstop.
- **Port-0 race.** Accepted, documented above. Revisit only if a collision is
  actually observed.
- **Thread per child.** Fine at this scale (single digits). If provider count
  grows past ~20, move to a single selector loop.
