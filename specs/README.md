# Specs

Numbered specifications. A spec is written before the code and states what
would have to be true for the change to be finished — including the tests that
must fail beforehand. It is not a design essay.

## Capability fabric — third-party integration

All seven arise from `docs/audit/2026-09-01-INTEGRATION-GAP-AUDIT.md`, which
measured that 27 of 41 cloned upstreams have no integration path and that 9 of
16 registered providers execute no code at all.

| Spec | Closes | Status | Implementation |
|---|---|---|---|
| [FABRIC-PROC-01](FABRIC-PROC-01.md) — supervised child processes | G3, G6 | **built** | `friday/fabric_process.py` |
| [FABRIC-CLI-01](FABRIC-CLI-01.md) — `CLI` integration mode | G4, G2 | **built** | `friday/fabric_cli.py` |
| [FABRIC-SVC-01](FABRIC-SVC-01.md) — HTTP service contract | G5, G6, G2 | **built** | `friday/fabric_service.py` |
| [FABRIC-GATE-01](FABRIC-GATE-01.md) — permissions/secrets at `call()` | G7 | **built** | `fabric.call()` + `Provider.open_operations` |
| FABRIC-LEARN-01 — provider outcome prior | G9 | **built** | `friday/fabric_memory.py` |
| FABRIC-CENSUS-01 — generated integration matrix | G2, G10 | **built** | `scripts/integration_matrix.py` |
| FABRIC-HEALTH-01 — functional health probes | G8 | **built** | `_skillpack.health()` |

Tests: `tests/test_fabric_execution.py` (PROC/CLI/GATE) and
`tests/test_fabric_completion.py` (SVC/LEARN/CENSUS/HEALTH + per-operation
permissions).

> **Verification status.** `test_fabric_execution.py` was run and passed 26/26.
> `test_fabric_completion.py` and the full suite have **not been run** — the
> session that wrote them had no shell. Run these before trusting the table
> above:
>
> ```
> .venv/Scripts/python.exe -m pytest tests/test_fabric_completion.py tests/test_fabric_execution.py -q
> .venv/Scripts/python.exe -m pytest tests/ -q
> .venv/Scripts/python.exe scripts/integration_matrix.py --check
> ```

**G2 is closed as a mechanism, not as data.** `integration_matrix.py --check`
now makes an unclassified clone a build failure; the 27 clones it names still
need descriptors written one at a time. That is deliberate — writing 27
descriptors without running each upstream would be inventing pins and licences.

### The per-operation permission field

GATE-01's fail-closed gate met `security_skills`, which declares one permission
across three operations while its own notes call two of them open. A
provider-wide list cannot express "reading the index is open, running the
procedure is not", so `Provider.open_operations` does. It fails safe: an
operation not named there is gated. `candidates()` applies the same exemption,
because a provider that ranking hides and `call()` would allow is a capability
that silently vanishes from the menu.

Build order is `PROC-01` → `GATE-01` → `CLI-01` → `SVC-01` → the rest.
**`GATE-01` must not trail `CLI-01`**: shipping subprocess execution while
`fabric.call()` has no authorisation check turns a latent bypass into a live
one.

---

## FABRIC-LEARN-01 — provider selection learns from outcomes

**Closes G9.** `call_with_fallback()` orders candidates by declared cost and
current health. Observed success rate is not an input, so a provider failing
90% of the time is chosen as readily as one that always works.

This is the same open loop `friday/routing_memory.py` closed for capability
routing on 2026-09-01 — outcomes were recorded for months and never read back.
Reuse that module's shape rather than inventing a second one:

- key on `(provider_id, operation)` rather than a request fingerprint;
- source the tally from `ActionResult` status already flowing through `call()`;
- require a minimum observation count before it moves anything (one success is
  noise, three is a habit);
- net evidence, so five successes and five failures is worth zero, not five;
- cap the adjustment so a well-trodden provider cannot outrank a health check
  that says the better one is up right now.

Feed the result into `select()` as a tie-breaker, after cost and health.
Not before: a cheap healthy provider should still win on the merits.

**Acceptance:** a provider that has failed the last five calls is ranked below
its fallback for the same family/operation, and returns to parity after three
successes.

---

## FABRIC-CENSUS-01 — the integration matrix is generated

**Closes G2, G10.** `docs/integrations/THIRD_PARTY_INTEGRATION_MATRIX.md`
documents 21 upstreams; there are 41 clones. A hand-typed table of 41 rows will
drift again — it already has.

`scripts/upstream_lock.py` already proves the pattern for pins. Extend it (or
add `scripts/integration_matrix.py`) to join three sources it can read:

1. clone directories under `third_party/upstream/`;
2. `third_party/UPSTREAM_LOCK.json` for pin and licence;
3. `fabric.registry()` for mode, operations and status.

Emit the matrix, and support `--check` to fail when a clone appears with no
descriptor and no explicit `REFERENCE_ONLY` demotion. **The unclassified state
is the one to make impossible** — that is what let 27 clones accumulate
unnoticed. Wire `--check` into the test gate.

**Acceptance:** adding an empty directory under `third_party/upstream/` fails
`--check` with that directory named; demoting it to `REFERENCE_ONLY` with a
reason passes.

---

## FABRIC-HEALTH-01 — health measures function, not presence

**Closes G8.** `_skillpack.health()` returns `READY` when a file exists on
disk. That is a filesystem check reported as a capability check, so a provider
can be `READY` and non-functional.

Every provider's `health()` must either exercise the smallest real operation it
offers, or report `DEGRADED` with detail `"presence only"`. `DEGRADED` is
already in `STATES` and already means "answering, but not fully — say so, use
it", which is exactly the honest answer for a skill pack that has been read but
never invoked.

**Acceptance:** a skill pack whose files exist but whose entry file is empty
reports `DEGRADED`, not `READY`; a provider with a real probe that fails
reports `UNAVAILABLE` with the failure in the detail.
