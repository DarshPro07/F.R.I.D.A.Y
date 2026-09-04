# Integration gap audit — the fabric can declare third-party code but cannot run it

**Date:** 2026-09-01 · **Scope:** `friday/fabric.py`, `friday/fabric_adapters/`,
`third_party/upstream/`, `docs/integrations/` · **Method:** static read of the
registry plus a live `fabric.registry()` census on this machine.

Every number below is measured, not estimated. Reproduce with:

```
.venv/Scripts/python.exe -c "
from friday import fabric; import pathlib, collections
reg = fabric.registry()
print(len(reg), collections.Counter(p.integration_mode for p in reg.values()))
ups = {p.upstream for p in reg.values() if p.upstream}
clones = {d.name for d in pathlib.Path('third_party/upstream').iterdir() if d.is_dir()}
print(len(clones), len(ups & clones), sorted(clones - ups))"
```

---

## 1. The finding in one line

**The fabric's dominant integration mode executes no code.** Nine of sixteen
providers are `SKILL`, and `SKILL` is defined in `friday/fabric.py` as
*"prompt/recipe only, no code executed"*. The fabric is therefore, in the
majority, a document reader wearing a capability registry's clothes.

This is the "only one approach is used" observation, stated precisely.

## 2. The census

| Measure | Value |
|---|---|
| Registered providers | **16** |
| Clones under `third_party/upstream/` | **41** |
| Clones named by any provider | **14** (34%) |
| Clones with **no** integration path | **27** (66%) |
| Providers that execute upstream code | **5** (3 ADAPTER, 1 MCP, 1 SIDECAR) |
| Providers that own an OS process | **1** (`codebase_memory`) |
| Providers that are prompt text only | **9** (56%) |
| Providers that are internal dummies | **2** |

**Integration mode distribution:** `SKILL` 9 · `ADAPTER` 3 · `BUILTIN` 2 ·
`MCP` 1 · `SIDECAR` 1.

Net: after subtracting the two `BUILTIN` dummies, **real executable third-party
capability on this machine is five providers**, three of which are ordinary
`pip`-installable Python libraries imported in-process.

### 2.1 The 27 upstreams with no integration path

```
agent-reach        agenticseek    agentmemory    anythingllm
auto-company       awesome-harness-engineering   bolt.diy
browser-use        cline          crewai         firstmate
maxun              munder-difflin nodriver       onlook
open-lovable       open-notebook  openhands      openmontage
openviking         openwork       openworker     pipecat
postiz             strix          ultron         vane
```

These are not stragglers — they are the categories the fabric was built for:
browser drivers (`browser-use`, `nodriver`), coding agents (`cline`,
`openhands`, `strix`, `firstmate`), orchestrators (`crewai`, `auto-company`,
`openworker`), and full web applications (`bolt.diy`, `onlook`,
`open-lovable`, `postiz`, `maxun`, `anythingllm`). **Not one of them is a
Python library or a folder of markdown**, and those are the only two shapes the
fabric can currently accept.

## 3. Gap register

Severity: **P0** blocks the stated requirement · **P1** correctness/security ·
**P2** maintainability.

### G1 — `SKILL` is load-bearing and executes nothing · P0

`friday/fabric_adapters/_skillpack.py` is explicit: *"nothing here touches the
disk at import; `call()` reads one file and returns its text."* Nine providers
sit on it. A `SKILL` provider cannot fetch a page, drive a browser, build a
project, or run a test — it can only hand the model more prompt.

**Consequence:** `capability_health` reports these as `READY`, the UI shows a
populated fabric, and a user asking for the *behaviour* gets prose.

**Required change:** keep `SKILL` for what it is genuinely for (role recipes,
writing checklists), and stop counting it toward executable capability.
Introduce real execution modes — G3, G4, G5.

### G2 — 66% of cloned upstreams have no descriptor · P0

Twenty-seven clones occupy disk, are pinned in `third_party/UPSTREAM_LOCK.json`,
appear in `docs/integrations/NEW_UPSTREAM_SET.json`, and are unreachable from
`fabric.registry()`. The build pack was staged; the integration was not written.

**Required change:** each of the 27 either gains a descriptor against a mode
that can actually run it, or is explicitly demoted to `REFERENCE_ONLY` with a
stated reason. An unclassified clone is the ambiguous state that lets this rot.

### G3 — There is no process control plane · P0

`INTEGRATION_MODES` names `SIDECAR` and `MCP`, but `fabric.activate()` does
exactly one thing for them: call an adapter-supplied `start()`. The fabric
itself provides **none** of:

- port allocation and collision avoidance;
- readiness waiting (a started process is assumed up — `health()` is polled
  immediately after `start()` returns);
- log capture (`stdout`/`stderr` go nowhere; a failing sidecar is silent);
- restart with backoff, or crash detection after activation;
- resource limits or timeouts on a long-running child;
- orphan cleanup — `deactivate()` calls the adapter's `stop()` and swallows
  failure, with the comment *"a provider that will not stop cleanly is a
  process-table problem"*;
- dependency bootstrap (`npm install`, `uv sync`, `playwright install`) —
  the assumption is that a human prepared the clone by hand.

`fabric.processes()` **detects** duplicate PIDs by matching a `PROCESS_MARKER`
against command lines. It cannot start, stop, or restart anything. Exactly one
provider sets `owns_process=True`.

**Consequence:** `SIDECAR` is a label, not a runtime. `graft` declares
`SIDECAR` while `owns_process` is False, so nothing supervises it.

**Required change:** a supervisor that owns the child lifecycle. See
`specs/FABRIC-PROC-01.md`.

### G4 — No CLI / subprocess integration mode exists · P1

Seven of the unintegrated upstreams (`cline`, `crewai`, `strix`, `agenticseek`,
`openhands`, `firstmate`, `openworker`) are command-line agents: you invoke
them, they work, they exit. The mode vocabulary has no `CLI`. There is no
descriptor a contributor could write for them even in principle.

**Required change:** add a `CLI` mode with a declared argv template, timeout,
working directory, exit-code contract and captured output. See
`specs/FABRIC-CLI-01.md`.

### G5 — No HTTP/service client contract · P1

`bolt.diy`, `onlook`, `open-lovable`, `postiz`, `maxun`, `anythingllm` and
`open-notebook` are web services. `SIDECAR` exists as a name with no base-URL
discovery, no auth handling, no request timeout, and no retry policy. An
adapter author must write all of it by hand, differently each time.

**Required change:** an HTTP service contract layered on the supervisor. See
`specs/FABRIC-SVC-01.md`.

### G6 — The copyleft invariant is a wall with no door · P1

`Provider.__post_init__` correctly refuses an AGPL/GPL provider that declares
`ADAPTER` or `BUILTIN`. This is good and must stay. But the only *working*
execution modes today are `ADAPTER` (in-process, forbidden to copyleft) and
`MCP` (one provider, hand-built). A copyleft upstream therefore has **no
compliant path in at all**, which is why `maxun`, `openmontage`, `postiz` and
`agenticseek` remain unintegrated.

**Required change:** G3/G4/G5 give copyleft code the isolated boundary the
invariant already demands. The invariant becomes enforceable rather than
merely prohibitive.

### G7 — `fabric.call()` enforces neither permissions nor secrets · P1 (security)

`Provider.permissions` and `Provider.secrets` are declared fields. Reading
`fabric.call()` end to end: it resolves the provider, checks the operation is
declared, activates, and invokes. **It never consults `provider.permissions`
and never resolves `provider.secrets` through the secret broker.**

Permission filtering lives in `candidates()` via the `authorized` frozenset
that `call_with_fallback()` threads down — so the gate is on one path and
absent on the other. Any caller reaching `fabric.call(provider_id, operation)`
directly bypasses authorisation entirely.

Worse, the one gate that exists is **fail-open by default**:

```python
if authorized is not None:
    pool = tuple(p for p in pool
                 if all(perm in authorized for perm in p.permissions))
```

`authorized=None` is the default and skips filtering altogether, so a caller
that simply omits the argument gets every provider regardless of what it
declares. The safe reading of "no grants supplied" is *no* providers requiring
grants, not *all* of them.

**Required change:** move the permission check into `call()`, the single choke
point, so no path can miss it. Resolve declared secrets there too, so an
adapter never reads `os.environ` itself.

### G8 — Health measures presence, not function · P2

`_skillpack.health()` returns `READY` when a file exists on disk. That is a
filesystem check being reported as a capability check. A provider can be
`READY` and non-functional.

**Required change:** health must exercise the smallest real operation the
provider offers, or report `DEGRADED` with "presence only" as the detail.

### G9 — Provider selection never learns · P2

`call_with_fallback()` orders candidates by declared cost and current health.
Observed success rate is not an input, so a provider that fails 90% of the time
is chosen exactly as often as one that always works. This is the same open loop
that `friday/routing_memory.py` closed for capability routing on 2026-09-01;
the fabric side is still open.

**Required change:** record per-provider/operation outcomes and feed them into
`select()` as a prior, reusing the weighting approach in `routing_memory.py`.

### G10 — The integration matrix has drifted · P2

`docs/integrations/THIRD_PARTY_INTEGRATION_MATRIX.md` documents 21 upstreams.
There are 41 clones. The newer batch (`auto-company`, `bolt.diy`, `firstmate`,
`nodriver`, `onlook`, `open-lovable`, `openworker`) is untracked in git and
absent from the matrix.

**Required change:** generate the matrix from `fabric.registry()` and the clone
directory, as `scripts/upstream_lock.py` already does for pins. A hand-typed
table of 41 rows will drift again.

## 4. What is already right, and must not be rebuilt

Recording these so a later pass does not "fix" them:

- **Discovery is derived.** `pkgutil` over `fabric_adapters/`; an adapter that
  fails to import becomes an `UNAVAILABLE` provider, not a broken Friday.
- **Pins are generated and checkable.** `scripts/upstream_lock.py --check`.
- **Fallbacks are validated at import**, so a failover path cannot rot into a
  3am `LookupError`.
- **Copyleft isolation is enforced in `__post_init__`**, not in review.
- **`ActionResult` envelopes are honest** — a bare return value is labelled as
  weak evidence rather than dressed up as verification.

The correct work is to add execution modes *into this contract*. Building a
second fabric would be the worst available outcome, and the existing matrix
already says so.

## 5. Ordered plan

| # | Spec | Closes | Why this order |
|---|------|--------|----------------|
| 1 | `FABRIC-PROC-01` supervisor | G3, G6 | Everything below needs a supervised child |
| 2 | `FABRIC-CLI-01` CLI mode | G4 | Cheapest real execution; 7 upstreams unblocked |
| 3 | `FABRIC-SVC-01` HTTP service mode | G5 | 7 more upstreams; needs the supervisor |
| 4 | `FABRIC-GATE-01` permission/secret choke point | G7 | Must land before more code executes |
| 5 | `FABRIC-LEARN-01` provider outcome prior | G9 | Needs traffic from 1–3 to learn from |
| 6 | `FABRIC-CENSUS-01` generated matrix | G2, G10 | Makes G2 visible and self-maintaining |
| 7 | `FABRIC-HEALTH-01` functional probes | G8 | Cheap once each mode exists |

**Sequencing constraint:** `FABRIC-GATE-01` must land before or with the first
mode that executes untrusted third-party code in anger. Shipping CLI or service
execution while `call()` has no authorisation check widens G7 from a latent
bypass into a live one.
