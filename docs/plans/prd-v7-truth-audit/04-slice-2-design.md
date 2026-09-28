# Design: Slice 2, the authority kernel

**Date:** 2026-09-26
**Status:** Reviewed by `friday-tech-lead` 2026-09-26: APPROVE-WITH-CHANGES (see Review outcome). In progress.
**Rows:** SEC-005, NET-001, CAP-002, STOP-001 (all P0). Also closes V7-D04 (TRUST-002 lineage) and V7-D08
(no out-of-band approval into the MCP process) from `docs/implementation/PRD_DEFECT_REGISTER.md`.
**Services:** objective-engine (store, continuous, objectives), mcp-tools (capability_runtime, tools), fabric
(fabric_process), executors (cli, claude_code, hermes bridge).
**Owner constraint:** SANDBOX is not the default. HOST stays the default backend; the envelope is enforced by
FRIDAY's own gates (owner, 2026-09-25).

## What exists today (read, not assumed)

| Concern | Where | State |
|---|---|---|
| OS resource limits and egress for one run | `friday/execution.py:128-190` `Limits`, `Egress`; `NativeExecutionEnvironment` `:344` | Used only when the backend is SANDBOX (`executors/claude_code.py:377-403`) and by the Hermes exec bridge (`tools/execution_bridge.py:73`) |
| A second copy of the same | `friday/sandbox.py:97-316` `Limits`, `Egress`, `JobObject`, `Sandbox` | Used only by `friday/self_upgrade.py`. DUPLICATED with `execution.py` |
| Coding CLI permissions | `executors/cli.py` `Launch`: cwd, `--allowedTools`/`--disallowedTools`, `--settings`, computed before launch | Real, outside the model; no roots, turn cap or expiry |
| Fabric sidecars | `fabric_process.spawn` `:240`, env `PASSTHROUGH` `:51` | No envelope |
| Per-call authority | `capability_runtime.execute` `:333`; `policy.provenance_verdict` at `:377`; `PolicyEngine.decide` `policy.py:663` | Per call; nothing about a run's whole capability set |
| Lineage on the durable path | `store.py:825-839` `objective_runs` has no provenance/attended; both dispatch ports run tasks as PERSON/attended (`capability_runtime.py:336,478`, `objective_cli.py:76-77`) | Lost (V7-D04) |
| Network | `netguard.check` `:194` (private, loopback, metadata); `trust.SecurityAuthorization` `:123` + `target_guard` `:200` (security targets only, with objective ID and expiry) | No objective-bound scope for ordinary work; no revocation |
| Confirmations | `confirmation.Book` `:104` keeps `pending` in memory; `control.py:149` and `ui_browser.py:44` each own a process-local Book in the UI server | The UI approves what the UI does; nothing can approve what the MCP process asked (V7-D08) |
| Stop | `launcher.stop_all` `:557` (CLI only), `fabric_process.stop_all` `:352`, `toolsets/workbench.py:162` | No latch against new actions, no lease revocation, no UI or voice path |

## Design

### 1. The envelope (SEC-005)

A frozen `Envelope`, minted by FRIDAY's policy and held outside model context:

```
run_id, objective_id, issuer
provenance, attended                      # lineage (closes V7-D04)
cwd, read_roots, write_roots
network: NetworkScope                      # section 2
secrets: tuple[alias]                      # names only; values stay in the broker
allowed_capabilities, blocked_capabilities
limits: execution.Limits                   # processes, memory, seconds
token_limit, tool_call_limit
expires_at
```

- **Narrow-only.** `envelope.narrow(...)` intersects and never widens. There is no widen method: a wider scope is a
  new envelope minted by policy, never a change requested through tool arguments or a worker message.
- **Durable.** `objective_runs` gains `provenance`, `attended` and `envelope` columns (migration with defaults for
  existing rows). Both dispatch ports build each task's Run from the stored envelope, not from defaults.
- **Strength is reported, not assumed.** Each field records ENFORCED or ADVISORY for the backend it ran on, on the
  run. On HOST, CLI subprocess network traffic is ADVISORY; that is a stated limit, not a hidden one.

Enforcement per worker type, HOST default:

| Worker | Enforced by |
|---|---|
| FRIDAY-native capability | `capability_runtime.execute`, before `provenance_verdict`: capability allowed and not blocked, envelope not expired, tool-call count under limit, file paths inside roots (via `fsjail`), hosts inside scope (via `netguard`) |
| Coding CLI (Claude Code) | `Launch` compiled from the envelope: cwd, tool allow/deny lists, read roots, turn cap; env without unlisted secrets (scrubbed on HOST too); expiry watchdog; refuses to launch on an expired envelope |
| Fabric sidecar | `fabric_process.spawn`: env limited to envelope secrets, cwd, expiry; refuses on expired |
| Hermes | TaskBundle constraints plus the exec bridge environment built from the envelope (cwd, egress, limits) |
| Browser | navigation hosts checked against the envelope's scope |

**Acceptance test (per worker type):** an out-of-envelope write, host, capability and an expired envelope are each
denied; a widening attempt through arguments changes nothing.

### 2. Network scope tokens (NET-001)

`NetworkScope(mode, hosts, objective_id, expires_at, token_id)` with PRD §44's modes: NONE, PUBLIC_WEB_READ,
SELECTED_HOSTS, PROVIDER_ENDPOINTS, AUTHENTICATED_SERVICES, SECURITY_TARGETS. SECURITY_TARGETS wraps the existing
`SecurityAuthorization`, so `target_guard` stays the judge of action and intensity.

Leases are persisted in the store (`issue`, `revoke`, `revoke_all`), so a revocation in any process holds in all.
`netguard.check(url, scope=...)` refuses an expired or revoked scope and a host outside it, on top of today's
private/metadata rules.

**Acceptance test:** an expired host is denied, an unknown host is denied, a revoked token is denied, and an
in-scope host passes.

### 3. Composition risk (CAP-002)

Before a worker starts (objective start, `executor_router.build`, `fabric_process.spawn`, `hermes_delegate`), the
`PolicyEngine` assesses the envelope's whole capability set. PRIVATE_READ (files, memory, mail/calendar read,
screen, clipboard) combined with ARBITRARY_SEND (outbound post, mail/message send, browser publish, upload)
escalates: CONFIRM when attended, DENY when not, with the reason "this run could send what it reads".

**Acceptance test:** each class alone is allowed, and both together escalate.

### 4. Out-of-band stop (STOP-001)

`emergency_stop(reason, actor)`, deterministic and model-free:

1. Latch STOP in the store. `capability_runtime` and the continuous executor refuse new actions while the latch is set.
2. Revoke every envelope and network lease.
3. Stop owned workers: CLI runs, fabric children, Hermes runs, workbench processes, and the browser/desktop queue.
4. Checkpoint recoverable state. Resuming needs the person's explicit resume.

Triggers are the `friday stop` CLI, a control-room button behind the access gate, and a voice stop word matched
before the LLM. None of them depends on a model tool call.

**Acceptance test:** a fake worker that ignores its stop request and a fake model that refuses are still
terminated; their leases are revoked; and a new action is refused while the latch is set.

### 5. Out-of-band approval (closes V7-D08)

Pending confirmations move into the store. The acting process asks and spends; person-facing surfaces (the UI
click, a voice yes matched deterministically to one pending ask) approve, with the actor recorded. No model-facing
tool exposes approve. `access.py` already persists consumed nonces across restarts (replay tests), and its pattern
is reused.

**Acceptance test:** a nonce asked in the MCP process is approved from another Book instance on the same store and
spent exactly once; a model tool call cannot approve it.

## Decisions for the review

1. **Where `Envelope`/`NetworkScope` live.** Recommend `friday/contracts.py`, the shared contract module that
   already holds `Run` (rule 2); enforcement stays in each owning service.
2. **Job Object on HOST.** Recommend yes, for kill-on-close and generous process/memory limits only (no filesystem
   or network isolation). It makes STOP reliable without restricting reach. *Assumption, revisit if the owner
   objects: the owner declined a sandbox for reach, not lifetime control.*
3. **`sandbox.py` vs `execution.py`.** Recommend moving `self_upgrade` onto `execution.py` and retiring `sandbox.py`
   in this slice, so the envelope has one enforcement substrate.
4. **Approval transport.** Recommend a store-backed Book over a local HTTP endpoint on the MCP server: it survives
   restarts, has no new listener, and reuses `access.py`'s replay rule.
5. **HOST network for CLI subprocesses.** Recommend ADVISORY plus honest strength reporting. The alternative is a
   local allowlisting proxy, which only binds proxy-respecting clients.

## Review outcome (friday-tech-lead, 2026-09-26): APPROVE-WITH-CHANGES

The five decisions:

1. **Envelope location.** `Envelope`, `NetworkScope` and `Limits` go in `friday/contracts.py`. `Limits` moves
   there because `contracts.py` imports only the standard library and must not depend on `execution.py`.
   `execution.py` uses `contracts.Limits`.
2. **Job Object on HOST.** Yes, but only around the child processes a run owns. Anything meant to outlive a FRIDAY
   restart stays outside it.
3. **Retire `sandbox.py`.** Yes. `self_upgrade` moves onto `execution.py`, leaving one enforcement substrate.
4. **Store-backed Book.** Yes. Spending a confirmation is one atomic conditional UPDATE in the store.
   `access.py`'s nonce file (`access.py:291`) is not cross-process safe and is **not** the pattern to reuse.
5. **Network on HOST.** ADVISORY, and the network scope is also written into the CLI's own WebFetch permission
   rules through `--settings`, so only raw subprocess (Bash) egress stays ADVISORY.

Changes adopted:

- **Ownership.**
  - The STOP latch belongs to objective-engine (the store), and each service registers its own stop hook.
  - Fabric and netguard read grants through a Store method, never raw SQL.
- **Naming and duplicated paths.**
  - "Lease" already means executor ownership (`objective_runs.lease_*`, `store.py:830-832`), so network and
    capability leases are called **grants**.
  - `ADA_PREAPPROVED_TOOLS` -> `approve_for_session` is a second approval path (`file_control.py:33-36`, and now
    `hermes_control.py`). It must never override `blocked_capabilities`, and a test pins that.
- **Tests that cannot pass vacuously.**
  - STOP's ignoring worker is a real OS process, checked dead by PID.
  - The refusal test asserts the LLM is never called.
  - Revocation is tested with two Store connections on one file.
  - CAP-002 classifies every real `CAPABILITIES` entry.
  - The widening test attacks a real argument path.
- **Fail-closed legacy.**
  - Existing `objective_runs` rows migrate as unattended with no envelope, and a NULL envelope refuses to run
    until the person re-issues it. *Assumed because the review requires fail-closed; the cost is that in-flight
    objectives at migration wait for a re-issue, which the migration reports.*
- **States.**
  - Stopped runs must stay inside the `NON_TERMINAL_RUN_HAS_FUTURE` invariant (`store.py:818-823`). Refinement
    while implementing: `objectives.RUN_PAUSED` already is that state. It is an explicit-resume wait in
    `RUN_WAITING_STATUSES` (`objectives.py:62-77`), so STOP moves runs to it with a recorded stop reason rather than
    adding a parallel `WAITING_STOPPED` (extend the seam).
  - Resume is exempt from the latch and works after a reboot.
- **PRD gaps closed.**
  - §53 "stop model/TTS where requested".
  - §44 "auditable": every grant's issue, use, deny and revoke goes to `trust.AuditLog`.
  - `token_limit` is enforced through the model gateway's per-objective budget.
  - Sub-workers get child envelopes derived with `narrow()`.

## Sequencing (revised after review)

1. `contracts.py`: `Envelope`, `NetworkScope`, `Limits` (moved); `execution.py` uses it; `self_upgrade` onto
   `execution.py`, retire `sandbox.py`.
2. Store migration: `objective_runs` gains `provenance`, `attended`, `envelope`; legacy rows are fail-closed;
   `WAITING_STOPPED` joins the WAITING states. V7-D04's failing test comes first.
3. Native enforcement in `capability_runtime` (roots, capabilities, expiry, tool-call count), with
   `approve_for_session` unable to override a block.
4. Store-backed Book with atomic spend (V7-D08). This comes before CAP-002, whose CONFIRM needs something that can
   approve it.
5. NET-001 grants: store-backed, audited, revocable; the netguard scope; CLI WebFetch rules.
6. CAP-002 composition at the four start points, over every real capability.
7. STOP-001: latch, service stop hooks, grant revocation, model/TTS stop on request; resume survives a reboot.
8. Worker compilation: CLI `Launch` (tool lists, `--settings`, env scrub, Job Object for owned children, expiry
   watchdog), fabric spawn, the Hermes bridge, and child envelopes.

Each step writes its failing test first and runs targeted tests. After the slice: the four-chunk suite,
`friday-final-reviewer`, and `PROGRESS_V7` entries.

## Implementation review (friday-final-reviewer, 2026-09-26): SHIP-WITH-FIXES

Steps 1-7 and the worker environment, reviewed by reading code only (no tests run). Every finding is now
dispositioned, each with a test that failed first and a mutation that turns it red again (register entries in
`docs/implementation/PRD_DEFECT_REGISTER.md`).

| # | Review said | Disposition |
|---|---|---|
| 1 | Medium: an envelope or scope with no expiry never expires (`_parse_utc` returns None) | Fixed at both doors instead of changing `expired()`: compile refuses a supplied envelope without one (V7-D23) and the stored form must carry one (V7-D24). The only product builders are `envelope_for` (always sets one) and `from_dict`. |
| 2 | Medium: compile and `Run` default to the person, attended; golden and soak rely on it | Compile's lineage is now required (V7-D25), and so is the stored form's (V7-D24). The evidence was stale: `friday/golden.py:316-318` and all four `scripts/soak.py` calls were already explicit. `Run`/`Run.create` stay open as V7-D28, because 69 product sites need a lineage decided, not defaulted. |
| 3 | Low: a supplied envelope's capabilities are not checked against the plan | Fixed with the whole narrow-only rule, plus a borrowed grant id (V7-D23). |
| 4 | Low: the Hermes poll runs before the STOP latch, read-only | Raised to High and fixed (V7-D26): a poll that found the killed gateway unreachable drove a RECONCILE_THEN_RESTART repair. |
| 5 | Low: `classify_url` swallows an unexpected netguard error | Fixed (V7-D22); three malformed loopback URLs had been ALLOWED. |
| 6 | Info: composition ignores uncategorised tools | Fixed in the runtime as well (V7-D27): an uncategorised capability ran on no decision where its toolset does not self-gate. |

Process note: the conversation summary that carried this review forward kept only findings 1, 3 and 5, and
turned finding 2 into a dispute. The full report was recovered from the session transcript before anything was
recorded here.

## Risks

- **Touches the live store's schema.** The migration must be additive, with defaults, and tested on a copy; never
  on `data/ada.sqlite3` directly.
- **Latch false positives.** A stuck STOP latch would freeze FRIDAY. Resume must be explicit, visible and tested.
- **The CLI's own enforcement is external evidence.** Tests assert FRIDAY passes the right flags; whether the
  installed CLI honours them is probed once and recorded, like `cli.py`'s existing probe notes.

## Step 8 implementation review (friday-final-reviewer, 2026-09-27): SHIP-WITH-FIXES

Nothing found widens authority. The five findings, each fixed with a failing-first test and a mutation that turns it
red again:

| # | Review said | Disposition |
|---|---|---|
| 1 | Medium: Hermes expiry lived in a timer alone, yet was reported ENFORCED | The expiry is kept on the work run (`expires_at`), and `expire_overdue` (run by the digest loop beside `sweep_orphans`) stops an overdue task whatever became of its timer. A run whose owner died is swept LOST, so it cannot outlive its supervisor as FRIDAY's task. |
| 2 | Medium: a conversation-supplied `objective_run_id` attributes spend to that objective | Accepted only while that objective is itself running a `hermes_delegate` task. Residual, stated: a call racing the objective's own delegation in that window could still be charged to it. |
| 3 | Low: `_expire` could mark a task that finished in the same instant; timers were never cancelled | Timers are kept per run and cancelled on any terminal transition and on interrupt. The same-instant case follows `interrupt`'s own stop-race rule (a stopped job is never announced as completed), now stated in its docstring. |
| 4 | Low: `rule_path` for a drive root, a UNC path and a linked root | A drive root is `//c`; a network path is refused before anything resolves it (resolving one can contact its host); a linked write root is allowed under both its spellings, since the CLI checks a link and its target. |
| 5 | Low: `isolate=True` untested; `--worktree` sits under the git top level | Under an envelope, an isolated run's worktree (`<git top level>/.claude/worktrees`) must lie inside a write root, or the launch is refused. |
