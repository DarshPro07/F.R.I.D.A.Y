# FR-100..106 / GB-35 — Proactive runtime: what exists, what the gap is

Written before building (PRD §0.1). Spec vocabulary on the left, the tree on
the right, and the honest gap. The gap is ONE thing: a durable, verified
*external-event* wait. Everything else the section names is present under
other names.

| Spec (FR-100..106, GB-35) | In the tree | Classification |
|---|---|---|
| FR-101 heartbeat: sleep idle → trigger → bounded work → persist → sleep | `continuous.ContinuousTaskExecutor._driver_tick` drives runs whose `next_wake` is due; `_settle_without_work` schedules the next wake; leases + watchdog `_is_orphan` | EXISTING |
| FR-102 scheduled objectives (one-time / recurring) | `toolsets/schedules.py` + `toolsets/automations.py` (Windows Task Scheduler owns the clock; `source_channel=schedule:` runs stay quiet — FR-042 no-noise) | EXISTING |
| FR-104 `WAITING_APPROVAL` | `RunStatus.WAITING_PERMISSION` + `_park_for_approval` / `resume_after_approval` bound to the exact action (FR-060) | EXISTING (different name) |
| FR-104 `WAITING_PROVIDER` (auth) | `_park_for_auth` / `resume_after_auth` (connector_verify hook) | EXISTING |
| FR-104 `WAITING_TIME` | task `next_wake` + `RUN_WAITING_STATUSES`; a run with a future wake is nobody's orphan | EXISTING |
| FR-104 worker in flight | `TaskStatus.WAITING` + `EVENT_WORKER_WAITING`, reconciled by WorkRun id, never re-delegated | EXISTING |
| FR-106 notification intelligence | `objective_deliveries` (exactly-once, TTL-expired, schedule-fired runs withhold the announcement) | EXISTING |
| FR-105 morning brief | `deck.py` briefs; not an objective-engine concern | EXISTING / NOT_AUDITED here |
| FR-100 event bus: objective / provider-health / system events | `objective_events` ledger (every transition, in order); `provider_health` ledger; `GatewayTelemetry` | EXISTING as ledgers — no *subscription*, but nothing in FR-103 needs one for these sources |
| **FR-100/FR-103/FR-104 external events**: `WAITING_EMAIL`, `WAITING_CI`, `WAITING_FILE`, `WAITING_BOOKING`; "continue when email arrives / notify when CI is green / resume when file exists" | **nothing.** A task cannot park on a condition outside the process and be resumed by that condition arriving. `resume_after_auth` is the only external resume and it is hard-wired to one signal | **MISSING** |
| GB-35 push broker (Gawkbot: external signal → objective) | same gap: no door for a signal to reach a waiting run | MISSING |

## Decision

One mechanism, built on the existing wait machinery, not beside it:

- A task result may say **`status: "waiting", wait: {kind, key, ...}`**. The
  executor parks the task WAITING with `failure_kind=EVENT_REQUIRED`, records
  the condition in `detail.wait`, and parks the run at `WAITING_EVENT` (a
  new member of `RUN_WAITING_STATUSES`, so the driver loop, the watchdog and
  the invariant already treat it as a legitimate wait, and `speak()` says
  what it is waiting for). A `deadline` may be given; past it the wait is
  failed as `TRANSIENT` with the reason named, never left forever.
- **Delivery** is `continuous.deliver_event(store, kind, key, payload)`:
  deterministic, executor-free (like `resume_after_auth`), flips every
  matching wait back to READY with the payload attached to the task result,
  appends `event.delivered`, and the driver picks it up. It returns the run
  ids it resumed — an event that matched nothing is recorded (`event.
  unmatched`) so the ledger shows it arrived.
- **Sources** are adapters over the same door, not a second bus:
  `files_wait` (a capability that parks on a path and is satisfied by a
  file-watch sweep in the driver tick) proves the loop end-to-end inside
  this tree; `POST /api/event` (session-gated, A-042 replay-protected, like
  `/api/objective`) is the door for email / CI / webhook connectors, which
  land in Phase 4/6 with their own adapters.
- **Not** built: a general pub/sub with subscriptions, topics, a separate
  event table, or a second scheduler. The ledger is `objective_events`;
  the clock is `next_wake`; the identity is the session gate.

## Evidence required (before VERIFIED)

Tests on a real `Store`: a task parks WAITING_EVENT and the invariant
holds; the driver loop skips it; a matching event resumes it and the
payload reaches the task; a non-matching event does not; a deadline
expires it honestly; `speak()` names the wait; the remote door refuses
unauthenticated and replayed deliveries and never resumes a run for them;
plants red/restored for each guard.
