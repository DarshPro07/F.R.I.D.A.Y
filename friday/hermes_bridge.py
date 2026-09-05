"""
The Friday ↔ Hermes bridge: supervisor, work record, bounded task bundles.

Hermes is an EXTERNAL, independently-upgradeable runtime. This module speaks
its supported TUI-gateway protocol - newline-delimited JSON-RPC over stdio,
the same dispatch the Ink TUI and the desktop dashboard use - and nothing
else. No Hermes source is imported, no private protocol is invented, and if
Hermes is upgraded underneath this module the contract holds because the
contract is Hermes's own.

    Friday
      → HermesSupervisor        process + connection + state machine
      → tui_gateway JSON-RPC    prompt.submit / session.steer / clarify.respond
      → real Hermes session
      → events (message.*, tool.*, clarify.request, session.usage)
      → Friday

Three deliberate boundaries:

1.  **Friday state is Friday's.** A `HermesWorkRun` row in Friday's own
    database is the durable truth about a delegation - Hermes chat history is
    never Friday's task state. The row records the Hermes session id so the
    work can be resumed, but resuming re-derives everything else.

2.  **Structured events only.** State transitions come from JSON-RPC frames
    (`message.complete`, `clarify.request`, `error`), never from parsing
    Hermes prose. A `done`-sounding sentence from the model is not a
    completion; `message.complete` after `prompt.submit` is.

3.  **Bounded context in, evidence out.** Work is sent as a `TaskBundle`
    that names the goal, the acceptance, the few relevant files and the
    tool/skill scope - measured, so an oversized bundle is visible at the
    call site. The entire conversation, repository or governance corpus is
    never shipped.

The supervisor's state machine:

    DISCONNECTED → STARTING → CONNECTED → SESSION_READY → WORKING
                                             ├── WAIT_FRIDAY   (clarify)
                                             ├── STEERED
                                             └── CANCELLING
                                          → COMPLETE / FAILED

State is a recorded fact (`HermesWorkRun.status`), not an inference.

## Known limitation on this dev machine, measured and narrowed

`read_file`/`terminal` tool calls inside a SPAWNED tui_gateway process hit
Hermes's 420s tool ceiling. The faulthandler stack dump shows the worker
wedged in `tools/environments/local.py _bash_starts` → `subprocess.run`
→ `_communicate`: the Git-Bash environment probe never returns inside the
gateway. Ruled out by controlled experiment (each variant still stalled):
shared HERMES_HOME (dedicated `friday` profile), inherited env (clean-built
env), job-object inheritance (CREATE_BREAKAWAY_FROM_JOB), console
inheritance (CREATE_NO_WINDOW), and process ancestry (launched via Windows
Task Scheduler with a clean service parent - still stalled). Controls that
all pass on the same machine: the identical probe from a plain piped
console-less python child (0.2s), any-drive cwd (0.3s), login shell (1.3s),
concurrent piped-bash + probe x8 (no hang), and the full `hermes -z` CLI
with the same profile (~1s). The failure therefore requires the real
gateway process itself; see scripts/hermes_upstream_repro.py for the
50-line Friday-free reproduction to file upstream. CAUSE CLASS:
gateway-process / shell-probe interaction; exact mechanism NOT YET PROVEN.
The stall watchdog exists so that whatever the cause, a wedged tool is
named TOOL_STALLED within its tool-class ceiling and contained instead of
silently eating Hermes's 420s.
"""

from __future__ import annotations

import functools
import json
import logging
import os
import shutil
import sqlite3
import subprocess
import threading
import time
import uuid
from dataclasses import dataclass, field, replace
from pathlib import Path


logger = logging.getLogger("friday-agent")

# ---------------------------------------------------------------------------
# States
# ---------------------------------------------------------------------------

DISCONNECTED = "DISCONNECTED"
STARTING = "STARTING"
CONNECTED = "CONNECTED"
SESSION_READY = "SESSION_READY"
WORKING = "WORKING"
WAIT_FRIDAY = "WAIT_FRIDAY"
WAIT_USER = "WAIT_USER"
STEERED = "STEERED"
CANCELLING = "CANCELLING"
COMPLETE = "COMPLETE"
PARTIAL = "PARTIAL"
FAILED = "FAILED"

STATES = (DISCONNECTED, STARTING, CONNECTED, SESSION_READY, WORKING,
          WAIT_FRIDAY, WAIT_USER, STEERED, CANCELLING, COMPLETE, PARTIAL,
          FAILED)

#: Statuses a WorkRun does not leave. Shared by WorkRunLog.update() (the
#: one choke point every status write already goes through) to decide
#: when to fire on_terminal().
TERMINAL = (COMPLETE, PARTIAL, FAILED)

#: The Hermes profile Friday's gateway runs under. A dedicated profile is a
#: fully independent HERMES_HOME - own sessions, state.db, memory, leases -
#: so Friday's runtime and the user's manual `hermes` CLI (default profile)
#: never contend for tool-execution state. Empty disables the override and
#: the gateway uses whatever profile the environment resolves (tests do
#: this; production must not).
DEFAULT_PROFILE = "friday"

ENV_PROFILE = "FRIDAY_HERMES_PROFILE"
ENV_PROFILE_HOME = "FRIDAY_HERMES_PROFILE_HOME"


def profile_home(profile: str) -> str:
    """
    The HERMES_HOME directory for a named profile, or "" if not found.

    Resolution mirrors Hermes's own convention (profiles live under
    `<hermes root>/profiles/<name>`) without importing Hermes code: an
    explicit env override wins, then the located install's sibling layout,
    then the user's ~/.hermes.
    """
    explicit = os.getenv(ENV_PROFILE_HOME, "").strip()
    if explicit:
        return explicit if Path(explicit).is_dir() else ""
    if not profile:
        return ""
    roots = []
    found = locate()
    if found:
        # git installs: D:\hermes\hermes-agent -> profiles at D:\hermes\profiles
        roots.append(Path(found["root"]).parent / "profiles" / profile)
    roots.append(Path.home() / ".hermes" / "profiles" / profile)
    for candidate in roots:
        if candidate.is_dir():
            return str(candidate)
    return ""


def native_tools_healthy(timeout: float = 20.0) -> bool:
    """
    Can a SPAWNED Hermes-shaped process build its local tool environment?

    NECESSARY BUT NOT SUFFICIENT - measured. This child-process probe
    passes on this host (LocalEnvironment builds in ~1s from a piped
    console-less child) while the SAME construction wedges inside a real
    tui_gateway process (upstream #73403 shape). The defect needs the
    genuine gateway context, which no cheap probe reproduces - a faithful
    check costs a model turn through a live gateway session.

    So: False here is DISQUALIFYING (something even simpler broke - keep
    the bridge). True here is merely a prerequisite: before re-enabling
    `file`/`terminal` in the friday profile, run the real gate
    (scripts/gate_hermes_bridge.py) and require an actual in-gateway
    read_file to complete. Do not flip the config on this function alone.
    """
    found = locate()
    if not found:
        return False
    child = (
        "import sys; sys.path.insert(0, sys.argv[1]);"
        "from tools.environments.local import LocalEnvironment;"
        "env = LocalEnvironment(cwd=sys.argv[2]);"
        "print('HEALTHY')"
    )
    try:
        probe = subprocess.run(
            [found["python"], "-c", child, found["root"], found["root"]],
            capture_output=True, text=True, timeout=timeout,
            creationflags=(subprocess.CREATE_NO_WINDOW
                           if os.name == "nt" else 0))
    except subprocess.TimeoutExpired:
        logger.warning("hermes.native_tools_probe timed out after %.0fs "
                       "- the #73403-shaped wedge is still present", timeout)
        return False
    except OSError as exc:
        logger.warning("hermes.native_tools_probe failed to start: %s", exc)
        return False
    healthy = probe.returncode == 0 and "HEALTHY" in (probe.stdout or "")
    if not healthy:
        logger.warning("hermes.native_tools_probe exit=%s stderr=%s",
                       probe.returncode, (probe.stderr or "")[-200:])
    return healthy


#: Hermes gateway event type → Friday event name. Anything absent is
#: forwarded under its own name rather than dropped, so a new Hermes event
#: is visible before it is understood.
EVENT_MAP = {
    "message.delta": "HERMES_PROGRESS",
    "message.start": "HERMES_PROGRESS",
    "message.interim": "HERMES_PROGRESS",
    "message.complete": "HERMES_RESULT",
    "tool.start": "HERMES_TOOL_START",
    "tool.generating": "HERMES_TOOL_PROGRESS",
    "tool.complete": "HERMES_TOOL_COMPLETE",
    "clarify.request": "WAIT_FRIDAY",
    "approval.request": "WAIT_FRIDAY",
    "session.usage": "HERMES_USAGE",
    "error": "HERMES_FAILURE",
}


# ---------------------------------------------------------------------------
# TaskBundle — the bounded context Friday sends
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TaskBundle:
    """
    Everything Hermes gets, and the ceiling on how much that is.

    The failure this prevents was measured, not imagined: a governance
    session that replayed ~157k prompt tokens per API call because context
    was accumulated instead of compiled. A bundle is compiled fresh per
    delegation from the few facts the task needs.
    """

    goal: str
    user_outcome: str = ""
    acceptance: tuple[str, ...] = ()
    constraints: tuple[str, ...] = ()
    known_facts: tuple[str, ...] = ()
    #: What the brief proceeds on without confirmation.
    assumptions: tuple[str, ...] = ()
    #: Paths the agent may touch - the worker's ALLOWED SCOPE.
    allowed_paths: tuple[str, ...] = ()
    #: Who is doing this, from the compiled team (e.g. role titles).
    role: str = ""
    #: Commands/description of how "done" gets checked afterwards.
    verification: tuple[str, ...] = ()
    #: How many attempts this task gets before it escalates. 0 = unset.
    iteration_budget: int = 0
    #: Paths or `path:reason` references. 3-8 is the intended shape; more is
    #: allowed but shows up in `oversized()`.
    code_refs: tuple[str, ...] = ()
    #: Skill names Hermes should consider loading. 0-3 normally.
    skill_hints: tuple[str, ...] = ()
    #: Toolsets/MCP servers allowed. Empty means Hermes's own defaults.
    tool_scope: tuple[str, ...] = ()
    disallowed: tuple[str, ...] = ()
    token_budget: str = "NORMAL"
    #: Whether to append the adaptive execution policy (below). On by
    #: default; set False for a task where breadth IS the point.
    adaptive_budget: bool = True
    #: What Friday already knows that bears on this task, compiled from the
    #: shared memory (preferences, rules, specs, relations, contacts) at
    #: delegation time. This is the memory-sharing seam between Friday and
    #: her sub-agents: Hermes runs under its own HERMES_HOME with its own
    #: memory, so without this a sub-agent starts every task as a stranger
    #: to the owner. It is a bounded, task-scoped slice, never the store.
    memory_context: str = ""

    def with_memory(self, budget_tokens: int = 600) -> "TaskBundle":
        """The same bundle, with the shared memory relevant to its goal.

        Selection is by the goal text through friday.memory_stack, the one
        aggregator every voice path already uses - not a second memory. The
        budget is small by design: the failure this guards against was
        ~157k replayed prompt tokens per call. A memory tier that cannot be
        read costs nothing here; the bundle simply goes without."""
        if self.memory_context:
            return self
        try:
            from friday import memory_stack
            text = memory_stack.aggregate(
                self.goal, budget_tokens=budget_tokens,
                include_episodes=False).get("prompt", "")
        except Exception:                                    # noqa: BLE001
            text = ""
        return replace(self, memory_context=(text or "").strip())

    #: Above this many characters the bundle is reported oversized. ~6k chars
    #: is roughly 1.5k tokens - a bundle, not a corpus.
    SOFT_LIMIT_CHARS = 6000

    #: An information-value stop condition, not a tool-call quota.
    #:
    #: Measured: the same inspection goal ran 35 model calls / 2.7M aggregate
    #: prompt tokens when phrased loosely, and 2 calls / 95k with the work
    #: bounded - a 28x spread on CALL COUNT while the per-call floor barely
    #: moved. So the waste was reasoning BREADTH, not context size.
    #:
    #: Deliberately not "read N files then answer": some investigations
    #: honestly need twenty reads and some need one, and a fixed quota would
    #: overfit this benchmark and cripple the harder case. What generalizes
    #: is "stop when more looking stops changing the answer", plus explicit
    #: permission to go deeper when the evidence is actually inadequate.
    #:
    #: A severity/"did I miss something bigger" clause was proposed and
    #: MEASURED TWICE on this same goal, because it guards a real failure
    #: (three medium findings satisfying "three" while a catastrophic one
    #: sits unexamined). Both phrasings were rejected on evidence:
    #:
    #:   open-ended ("no unresolved evidence has a reasonable chance of
    #:     changing the ranked answer")   -> 38 calls / 3.6M tokens
    #:   bounded ("one pass over what you have already seen")
    #:                                    -> 23 calls / 2.1M tokens
    #:   neither clause (below)           ->  5 calls / 296k tokens
    #:
    #: 38 calls is WORSE than the 35-call unguarded baseline: nothing is
    #: ever provably "not missed", so any such clause licenses unbounded
    #: search. Even the tightened form cost 4.6x for no measured quality
    #: gain - the 5-call run already produced three ranked findings with
    #: file:line evidence and caught a stale architecture doc.
    #:
    #: The severity concern is real but belongs in ACCEPTANCE (the caller
    #: says "ranked by blast radius", which is checkable) rather than in a
    #: standing policy that turns every run into a search for unknowns.
    EXECUTION_POLICY = (
        "Start with the minimum investigation that can satisfy the goal.\n"
        "Stop when: the acceptance conditions are met with concrete "
        "evidence; or further exploration has low expected information "
        "value (it is confirming what you already know).\n"
        "Go deeper only when: evidence conflicts, confidence is genuinely "
        "insufficient, findings are not differentiated, or an important "
        "dependency is unresolved.\n"
        "Do not explore architecture unrelated to the goal. Do not run "
        "parallel investigations unless independence provides real value. "
        "Re-reading something you have already read is not progress."
    )

    #: Always present - the worker gets told the shape of a good report up
    #: front, not discovered after it has already written a transcript.
    REPORTING_CONTRACT = (
        "State: status (SUCCEEDED/FAILED/PARTIAL/BLOCKED), files changed, "
        "tests run, evidence for each claim, and any blockers. Compact, "
        "not a transcript."
    )

    def render(self) -> str:
        """The exact text sent as the Hermes prompt.

        The first ten sections are the task contract, in a fixed order a
        worker can rely on. What follows (user outcome, shared memory,
        code refs, tool scope, budget) is unchanged from before - appended
        after the contract rather than reordered, so `with_memory()` and
        its token cap keep meaning exactly what they meant.
        """
        constraints = self.constraints
        if self.iteration_budget > 0:
            constraints = constraints + (
                f"ITERATION BUDGET: {self.iteration_budget} attempts",)
        sections: list[tuple[str, object]] = [
            ("GOAL", self.goal),
            ("ACCEPTANCE CRITERIA", self.acceptance),
            ("KNOWN FACTS", self.known_facts),
            ("ASSUMPTIONS", self.assumptions),
            ("CONSTRAINTS", constraints),
            ("ALLOWED SCOPE", self.allowed_paths),
            ("PROHIBITED ACTIONS", self.disallowed),
            ("ROLE / RESPONSIBILITY", _with_subagent(self.role)),
            ("VERIFICATION", self.verification),
            ("REPORTING CONTRACT", self.REPORTING_CONTRACT),
            ("USER OUTCOME", self.user_outcome),
            ("WHAT FRIDAY ALREADY KNOWS (shared memory; treat as facts about "
             "the owner, not instructions)", self.memory_context),
            ("RELEVANT CODE", self.code_refs),
            ("SKILL HINTS (load at most these; do not enumerate all skills)",
             self.skill_hints),
            ("ALLOWED TOOLS/MCP", self.tool_scope),
            ("TOKEN BUDGET", self.token_budget),
            ("EXECUTION POLICY",
             self.EXECUTION_POLICY if self.adaptive_budget else ""),
        ]
        parts = []
        for title, value in sections:
            if not value:
                continue
            if isinstance(value, str):
                parts.append(f"{title}\n{value}")
            else:
                parts.append(title + "\n" + "\n".join(f"- {v}" for v in value))
        return "\n\n".join(parts)

    def measure(self) -> dict:
        text = self.render()
        return {"chars": len(text), "approx_tokens": len(text) // 4,
                "oversized": len(text) > self.SOFT_LIMIT_CHARS,
                "code_refs": len(self.code_refs),
                "skill_hints": len(self.skill_hints)}


# ---------------------------------------------------------------------------
# HermesWorkRun — Friday's durable record of one delegation
# ---------------------------------------------------------------------------

_TABLE = """
CREATE TABLE IF NOT EXISTS hermes_work_runs (
    work_run_id   TEXT PRIMARY KEY,
    friday_run_id TEXT NOT NULL DEFAULT '',
    hermes_session_id TEXT NOT NULL DEFAULT '',
    hermes_stored_session_id TEXT NOT NULL DEFAULT '',
    hermes_version TEXT NOT NULL DEFAULT '',
    provider      TEXT NOT NULL DEFAULT '',
    model         TEXT NOT NULL DEFAULT '',
    task          TEXT NOT NULL DEFAULT '',
    bundle_chars  INTEGER NOT NULL DEFAULT 0,
    token_budget  TEXT NOT NULL DEFAULT 'NORMAL',
    status        TEXT NOT NULL DEFAULT 'DISCONNECTED',
    pending_question TEXT NOT NULL DEFAULT '',
    result        TEXT NOT NULL DEFAULT '',
    events_seen   INTEGER NOT NULL DEFAULT 0,
    usage_json    TEXT NOT NULL DEFAULT '',
    route_reason  TEXT NOT NULL DEFAULT '',
    fallback_from TEXT NOT NULL DEFAULT '',
    fallback_to   TEXT NOT NULL DEFAULT '',
    fallback_reason TEXT NOT NULL DEFAULT '',
    started_at    REAL NOT NULL,
    last_event_at REAL NOT NULL
)
"""


_H0_COLUMNS = {
    "route_reason": "TEXT NOT NULL DEFAULT ''",
    "fallback_from": "TEXT NOT NULL DEFAULT ''",
    "fallback_to": "TEXT NOT NULL DEFAULT ''",
    "fallback_reason": "TEXT NOT NULL DEFAULT ''",
}

#: H0 route observability (the foundation of the token economy - Friday
#: cannot learn "this task didn't need Opus" from runs it cannot see).
#: Existing databases predate these columns; CREATE TABLE IF NOT EXISTS
#: will not add them, so WorkRunLog migrates additively on open. Additive
#: ALTERs only - never a rewrite of live rows.
#: RC1.1 production isolation: every WorkRun records the origin of the
#: process that created it, so a gate/test run can never be spoken into a
#: production session. Additive migration - existing rows read as
#: 'production', which is what they were.
_ORIGIN_COLUMN = {"origin": "TEXT NOT NULL DEFAULT 'production'"}

#: S2 idempotency: on_terminal()'s memory write must happen exactly once
#: per run - the same guarantee a delivery gets from create_delivery's
#: UNIQUE constraint, here a claimed flag since work_run_id is already the
#: primary key. Existing rows read as 0: nothing wrote their outcome before
#: this slice existed, which is the truth.
_MEMORY_COLUMN = {"memory_written": "INTEGER NOT NULL DEFAULT 0"}

#: S2 quota routing: which failure kind ended the run, alongside the
#: existing route_reason column (H0) that already carries the human line.
_QUOTA_COLUMNS = {"failure_kind": "TEXT NOT NULL DEFAULT ''"}

#: S4b: the structured Handoff (friday/handoff.py), JSON, stored alongside
#: the memory write on_terminal already does - same additive-migration
#: pattern as every column above it.
_HANDOFF_COLUMN = {"handoff": "TEXT NOT NULL DEFAULT ''"}
#: Which OS process ran the delegation ("pid:create_time"), so a run whose
#: owner died (a launcher restart, a crash) is closed as LOST instead of being
#: narrated as "reading the task - 0s in" by every digest that follows
#: (2026-09-04 16:08: twenty of them in one breath).
_OWNER_COLUMN = {"owner": "TEXT NOT NULL DEFAULT ''"}
#: The progress ledger, persisted on every event: the room and the control
#: room read runs from OTHER processes, whose in-memory ledger they cannot see.
_PROGRESS_COLUMN = {"progress_json": "TEXT NOT NULL DEFAULT ''"}
LOST_RESULT = "lost: Friday restarted before this run finished"


def process_owner() -> str:
    """This process as a work-run owner: "pid:create_time" (pid-reuse proof)."""
    try:
        import psutil
        return f"{os.getpid()}:{int(psutil.Process().create_time())}"
    except Exception:  # noqa: BLE001 - without psutil the pid alone names us
        return f"{os.getpid()}:0"


def owner_alive(owner: str) -> bool:
    """False for '' (a row from before owners were recorded) and for a pid
    that is gone or has been reused by a newer process. Unknowable counts
    as alive: a sweep must never close a run that may still be working."""
    if not owner:
        return False
    try:
        pid_s, _, ctime_s = owner.partition(":")
        pid, ctime = int(pid_s), int(ctime_s or 0)
        import psutil
        if not psutil.pid_exists(pid):
            return False
        if ctime == 0:
            return True
        return abs(int(psutil.Process(pid).create_time()) - ctime) <= 2
    except Exception:  # noqa: BLE001
        return True

# Restored from the .pyc oracle: proven by a LOAD_CONST/STORE_NAME
# pair in the running system's bytecode, present in no source candidate.
DELIVERABLE_ORIGINS = ('production',)
DELIVERY_TTL_S = 21600


def run_origin() -> str:
    """The origin for WorkRuns created by THIS process.

    An environment variable rather than an argument threaded through
    supervisor/delegate/create, because the property is truly per-process:
    a gate script is a gate script for every run it creates. Production
    never sets it and defaults to 'production'.
    """
    return os.environ.get('FRIDAY_RUN_ORIGIN', 'production').strip() or 'production'

#: One row per WorkRun terminal outcome the user has not been told about yet.
#: UNIQUE(work_run_id): however many processes notice a completion - the
#: supervisor's event handler, a startup sweep after a crash, a second sweep
#: after another crash - only the FIRST insert creates a delivery, which is
#: what makes "the broker must not speak the same completion twice" a
#: database constraint instead of a hope.
_DELIVERY_TABLE = """
CREATE TABLE IF NOT EXISTS hermes_deliveries (
    delivery_id   TEXT PRIMARY KEY,
    work_run_id   TEXT NOT NULL UNIQUE,
    goal          TEXT NOT NULL DEFAULT '',
    status        TEXT NOT NULL DEFAULT '',
    message       TEXT NOT NULL DEFAULT '',
    delivery_state TEXT NOT NULL DEFAULT 'PENDING',
    created_at    REAL NOT NULL,
    delivered_at  REAL NOT NULL DEFAULT 0,
    delivered_via TEXT NOT NULL DEFAULT ''
)
"""


#: Tool name -> the verb a person would say. Anything not here is reported
#: as the bare name, which is still truthful.
_TOOL_VERBS = {
    "read_file": "reading", "search_files": "searching", "write_file": "writing",
    "patch": "editing", "terminal": "running", "web_search": "searching the web for",
    "web_extract": "reading the page", "todo": "planning", "delegate_task": "handing off",
    # Friday's own bridge tools as Hermes sees them (mcp__friday_exec__bridge_*)
    "bridge_run_command": "running", "bridge_read_file": "reading",
    "bridge_list_files": "listing", "bridge_write_file": "writing",
    "bridge_edit_file": "editing", "bridge_search": "searching",
}


def _describe_tool(name: str, payload: dict | None) -> str:
    """One spoken line for a tool start: "editing friday/policy.py"."""
    args = (payload or {}).get("arguments") or (payload or {}).get("args") or {}
    # MCP-prefixed names (mcp__<server>__<tool>) -> the tool part.
    short = name.split("__")[-1] if "__" in name else name
    target = ""
    if isinstance(args, dict):
        for key in ("path", "file", "command", "query", "pattern", "goal"):
            if args.get(key):
                target = str(args[key])
                if key in ("path", "file"):
                    target = target.replace("\\", "/").rstrip("/").split("/")[-1]
                break
    target = target[:48]
    verb = _TOOL_VERBS.get(short, short.replace("_", " "))
    return f"{verb} {target}".strip()


@functools.lru_cache(maxsize=1)
def _profile_model_default() -> tuple[str, str]:
    """
    (provider, model) from the friday profile's config.yaml.

    The gateway's session.create info reports the model but omits the
    provider entirely (measured across every Phase-1 run: provider='' in
    all of them), and H0 requires the EFFECTIVE pair on the durable
    record. The profile config is what the gateway itself loads, so when
    the session is silent this is ground truth, not a guess. Cached: the
    config changes only on profile edits, which restart the gateway.
    """
    home = os.environ.get(ENV_PROFILE_HOME) or profile_home(
        os.environ.get(ENV_PROFILE, "friday"))
    if not home:
        return "", ""
    try:
        import yaml
        config = yaml.safe_load(
            (Path(home) / "config.yaml").read_text(encoding="utf-8"))
        section = (config or {}).get("model") or {}
        return (str(section.get("provider", "")),
                str(section.get("default", "") or section.get("name", "")))
    except Exception:                                        # noqa: BLE001
        return "", ""


def _with_subagent(role: str) -> str:
    """The worker is told which Claude project subagent fits the role, so the
    same specialist runs whichever executor takes the task (the line existed
    only on the Claude executor's prompt; the Hermes bundle lacked it)."""
    if not role:
        return ""
    try:
        from friday.roles import claude_agent_for
        agent = claude_agent_for(role)
    except Exception:  # noqa: BLE001
        return role
    return f"{role}\nUse the `{agent}` subagent for this role." if agent else role


def render_completion(record: dict) -> str:
    """
    One user-facing completion message, deterministically.

    Hermes already wrote the grounded findings; Friday's job here is
    framing, not re-reasoning - so this is string assembly, zero model
    calls, zero tokens. A validator LLM pass is reserved for results that
    genuinely need conversational synthesis, which a bounded inspect/build
    report does not.
    """
    goal = (record.get("task") or "the delegated task").strip()
    if len(goal) > 140:
        goal = goal[:137] + "..."
    result = (record.get("result") or "").strip()
    status = record.get("status", "")
    # S4b: route_reason (S2) + next_action (the Handoff's pending_question,
    # if any) ride along when present - unchanged wording otherwise, so
    # existing callers/tests see the same text they always did.
    route_reason = (record.get("route_reason") or "").strip()
    next_action = ""
    try:
        from friday.handoff import Handoff
        raw = record.get("handoff")
        if raw:
            next_action = Handoff.from_json(raw).next_action
    except Exception:                                        # noqa: BLE001
        next_action = ""
    extra = ""
    if route_reason:
        extra += f"\n\n({route_reason})"
    if next_action:
        extra += f"\n\nNext: {next_action}"
    if status == COMPLETE:
        head = f"Hermes finished: {goal}"
        body = result or (
            "(The run completed but returned no text - "
            "check hermes_status for the work run record.)")
        return f"{head}\n\n{body}{extra}"
    if status == PARTIAL:
        return (f"Hermes stopped partway through: {goal}\n\n"
                f"{result or 'Partial work is preserved in the run record.'}{extra}")
    # FAILED - say what actually failed, never stay silent.
    return (f"Hermes couldn't finish: {goal}\n\n"
            f"{result or 'No error text was returned.'} "
            f"The run is preserved as {record.get('work_run_id', '?')}.{extra}")


def _write_outcome(record: dict) -> None:
    """
    The sanitized write itself - split from WorkRunLog.on_terminal() so the
    claim (DB-shaped, needs self._connect()) and the write (memory-shaped,
    needs store/voice_brain) are each easy to reason about alone.

    render_completion() is already the deterministic goal+result summary
    Friday would speak, so it doubles as the ≤600-char memory summary
    rather than a second string-assembly function. Never the raw bundle,
    never a secret: brain._sensitive is the same admission guard the brain
    itself uses before ingestion.
    """
    summary = render_completion(record)[:600]
    from friday.brain import _sensitive
    reason = _sensitive(summary)
    if reason:
        logger.warning("hermes.on_terminal refused run=%s: %s",
                       record.get("work_run_id"), reason)
        return
    from friday.toolsets.memory import store
    from friday import voice_brain
    work_run_id = record.get("work_run_id", "")
    db = store()
    # Reader 1: the voice-brain conversation, so _recent_turns() /
    # _memory_context() answer "what did Hermes just do?" next turn.
    db.add_message(voice_brain.conversation_id(), "assistant", summary,
                   run_id=work_run_id)
    # Reader 2: a durable outcome row a Hermes-bundle memory_stack.aggregate
    # (include_episodes=False) can surface - see hermes_outcomes() tier.
    db.record_decision(
        "hermes", summary, source=f"hermes:{work_run_id}",
        rationale=(f"status={record.get('status', '')} "
                   f"model={record.get('model', '')} "
                   f"route={record.get('route_reason', '')}")[:400],
        run_id=work_run_id)


class WorkRunLog:
    """
    The durable half. Plain sqlite against Friday's own database file.

    Deliberately its own table rather than Hermes transcripts: the
    governance ADR-003 rule is that Friday's project truth lives in Friday's
    store, and a delegation's status is project truth.
    """

    def __init__(self, db_path: str | Path | None = None, *,
                 path: str | Path | None = None) -> None:
        db_path = db_path or path
        if db_path is None:
            from friday.config import DATA_DIR
            db_path = Path(DATA_DIR) / "ada.sqlite3"
        self._path = str(db_path)
        #: Called with the work_run_id after every terminal status write.
        #: The supervisor uses it to release the governor lease.
        self.on_terminal_hook = None
        with self._connect() as db:
            db.execute(_TABLE)
            db.execute(_DELIVERY_TABLE)
            # H0 additive migration for databases created before the
            # route-observability columns existed.
            have = {r[1] for r in db.execute(
                "PRAGMA table_info(hermes_work_runs)")}
            for column, decl in {**_H0_COLUMNS,
                                 **_ORIGIN_COLUMN, **_MEMORY_COLUMN,
                                 **_QUOTA_COLUMNS, **_HANDOFF_COLUMN,
                                 **_OWNER_COLUMN, **_PROGRESS_COLUMN}.items():
                if column not in have:
                    db.execute(f"ALTER TABLE hermes_work_runs"
                               f" ADD COLUMN {column} {decl}")

    def _connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self._path, timeout=10)
        db.row_factory = sqlite3.Row
        return db

    def create(self, *, task: str, friday_run_id: str = "",
               bundle_chars: int = 0, token_budget: str = "NORMAL") -> str:
        work_run_id = f"hermes-{uuid.uuid4().hex[:10]}"
        now = time.time()
        with self._connect() as db:
            db.execute(
                "INSERT INTO hermes_work_runs (work_run_id, friday_run_id,"
                " task, bundle_chars, token_budget, status, origin, owner,"
                " started_at, last_event_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (work_run_id, friday_run_id, task, bundle_chars, token_budget,
                 STARTING, run_origin(), process_owner(), now, now))
        return work_run_id

    def update(self, work_run_id: str, *, progress: dict | None = None,
               **fields) -> None:
        allowed = {"hermes_session_id", "hermes_stored_session_id",
                   "hermes_version", "provider", "model", "status",
                   "pending_question", "result", "events_seen", "usage_json",
                   "route_reason", "fallback_from", "fallback_to",
                   "fallback_reason", "failure_kind", "progress_json"}
        unknown = set(fields) - allowed
        if unknown:
            raise ValueError(f"unknown work-run fields: {sorted(unknown)}")
        if not fields:
            return
        sets = ", ".join(f"{k} = ?" for k in fields)
        with self._connect() as db:
            db.execute(
                f"UPDATE hermes_work_runs SET {sets}, last_event_at = ?"
                f" WHERE work_run_id = ?",
                (*fields.values(), time.time(), work_run_id))
        if fields.get("status") in TERMINAL:
            record = self.get(work_run_id)
            if record:
                self.on_terminal(record, progress=progress)
            hook = getattr(self, "on_terminal_hook", None)
            if hook is not None:
                try:
                    hook(work_run_id)
                except Exception:  # noqa: BLE001 - a lease release never fails a run
                    logger.exception("terminal hook failed for %s", work_run_id)

    def on_terminal(self, record: dict, progress: dict | None = None) -> None:
        """
        Sanitized Hermes outcome -> Friday's shared memory, once per run.

        Called by update() the instant a run's status lands on COMPLETE/
        PARTIAL/FAILED - the single choke point every status write already
        goes through (delegate/event-handler/interrupt/cancel all call
        update()), so no call site can miss it. Also safe to call directly
        (tests do): idempotency lives here, not at the call site.

        NEVER raises: an optional memory write must not cost the run whose
        result it is trying to save. Idempotent the same way a delivery is
        claimed (claim_delivery below) - the atomic UPDATE ... WHERE
        memory_written = 0 is the guarantee, not a hope, so calling this
        any number of times for one work_run_id writes at most once.
        """
        work_run_id = record.get("work_run_id", "")
        if not work_run_id:
            return
        from friday.handoff import Handoff
        try:
            handoff_json = Handoff.from_work_run(record, progress).to_json()
        except Exception:  # noqa: BLE001 - a bad handoff must not cost the run's memory
            logger.exception("hermes.on_terminal handoff failed run=%s", work_run_id)
            handoff_json = ""
        try:
            with self._connect() as db:
                cursor = db.execute(
                    "UPDATE hermes_work_runs SET memory_written = 1,"
                    " handoff = ? WHERE work_run_id = ? AND memory_written = 0",
                    (handoff_json, work_run_id))
                if cursor.rowcount != 1:
                    return              # already written, or unknown run
        except Exception:                                    # noqa: BLE001
            logger.exception("hermes.on_terminal claim failed run=%s", work_run_id)
            return
        try:
            _write_outcome(record)
            # What the worker learned crosses into canonical memory only
            # through the promotion gate (evidence, contradiction, dedupe,
            # secret guard) - never straight from a handoff (ADR-001).
            try:
                from friday.handoff import Handoff
                from friday.memory_promotion import promote_handoff
                promote_handoff(Handoff.from_json(handoff_json))
            except Exception:                                # noqa: BLE001
                logger.exception("hermes.on_terminal promotion failed run=%s", work_run_id)
        except Exception:                                    # noqa: BLE001
            logger.exception("hermes.on_terminal write failed run=%s", work_run_id)
            # The claim was taken before the write; a failed write must hand it
            # back or the outcome is lost for good (review, 2026-09-03). A
            # secret-shaped outcome is refused inside _write_outcome without
            # raising and stays claimed on purpose: it must not be retried.
            try:
                with self._connect() as db:
                    db.execute("UPDATE hermes_work_runs SET memory_written = 0"
                               " WHERE work_run_id = ?", (work_run_id,))
            except Exception:                                # noqa: BLE001
                logger.exception("hermes.on_terminal could not release the claim run=%s",
                                 work_run_id)

    def get(self, work_run_id: str) -> dict | None:
        with self._connect() as db:
            row = db.execute(
                "SELECT * FROM hermes_work_runs WHERE work_run_id = ?",
                (work_run_id,)).fetchone()
        return dict(row) if row else None

    def active(self) -> list[dict]:
        terminal = (COMPLETE, PARTIAL, FAILED)
        with self._connect() as db:
            rows = db.execute(
                "SELECT * FROM hermes_work_runs WHERE status NOT IN (?,?,?)"
                " ORDER BY started_at DESC", terminal).fetchall()
        return [dict(r) for r in rows]

    def recent(self, limit: int = 10) -> list[dict]:
        """The newest runs regardless of status - the gate's evidence view."""
        with self._connect() as db:
            rows = db.execute(
                "SELECT * FROM hermes_work_runs ORDER BY started_at DESC"
                " LIMIT ?", (int(limit),)).fetchall()
        return [dict(r) for r in rows]

    def sweep_orphans(self) -> list[str]:
        """Close every non-terminal run whose owning process is gone.

        A run is a promise made by one process; when that process dies the
        promise cannot be kept, and until 2026-09-04 nothing said so: the
        row stayed WORKING forever and the first digest after a restart
        recited twenty of them. Direct SQL, not update(): last_event_at is
        left alone on purpose, so a run that died an hour ago does not
        become a fresh milestone, and on_terminal is not invoked - there is
        no outcome to hand off or remember. Safe from any process (owner
        liveness is a global fact) and idempotent.
        """
        with self._connect() as db:
            rows = db.execute(
                "SELECT work_run_id, owner FROM hermes_work_runs"
                " WHERE status NOT IN (?,?,?)", TERMINAL).fetchall()
        lost = [r["work_run_id"] for r in rows if not owner_alive(r["owner"])]
        if not lost:
            return []
        with self._connect() as db:
            for wid in lost:
                db.execute(
                    "UPDATE hermes_work_runs SET status = ?, failure_kind = ?,"
                    " result = ? WHERE work_run_id = ? AND status NOT IN (?,?,?)",
                    (FAILED, "LOST", LOST_RESULT, wid, *TERMINAL))
        for wid in lost:
            logger.info("hermes.run lost run=%s (owner process gone)", wid)
        return lost

    # -- deliveries ---------------------------------------------------------

    def create_delivery(self, work_run_id: str, *, goal: str,
                        status: str, message: str) -> str | None:
        """
        Record "the user must be told about this run", exactly once.

        Returns the delivery id, or None when a delivery for this run
        already exists - the UNIQUE constraint is the idempotency, so a
        crash-and-resweep can call this as often as it likes.

        Also None for a run whose origin is not user-facing: a gate/test
        WorkRun must never be spoken into a production session, however it
        ended. Enforced HERE - the single choke point every delivery writer
        (completion event, startup sweep) already goes through - rather
        than at each caller.
        """
        record = self.get(work_run_id)
        origin = (record or {}).get("origin", "production") or "production"
        if origin not in DELIVERABLE_ORIGINS:
            logger.info("hermes.delivery suppressed run=%s origin=%s",
                        work_run_id, origin)
            return None
        delivery_id = f"dlv-{uuid.uuid4().hex[:10]}"
        try:
            with self._connect() as db:
                db.execute(
                    "INSERT INTO hermes_deliveries (delivery_id, work_run_id,"
                    " goal, status, message, delivery_state, created_at)"
                    " VALUES (?,?,?,?,?,?,?)",
                    (delivery_id, work_run_id, goal[:500], status,
                     message, "PENDING", time.time()))
        except sqlite3.IntegrityError:
            return None
        return delivery_id

    def pending_deliveries(self, *, max_age_s: float = DELIVERY_TTL_S) -> list[dict]:
        """
        Deliveries still worth speaking, oldest first.

        A delivery row carries no conversation affinity - there is no room or
        session column - so whatever is PENDING is spoken into whichever
        session happens to be live. That is right for "you reconnected, here is
        what finished while you were away" and wrong for everything older.

        Measured, on this machine: four smoke-test completions recorded at
        18:13-18:22 sat PENDING because no session was connected, and were
        then recited, cold and without context, into a brand-new room at 22:43
        - four and a half hours later, to a conversation that had asked for
        none of them.

        So age is the guard, and it is the same six hours `sweep_undelivered`
        already applies for the same stated reason: Friday must not stand up
        and recite every delegation it has ever completed. Over-age rows become
        EXPIRED rather than being skipped, because a row that says why it was
        never spoken is evidence and a row that is quietly filtered is not.
        """
        cutoff = time.time() - max_age_s
        with self._connect() as db:
            stale = db.execute(
                "SELECT delivery_id, work_run_id FROM hermes_deliveries"
                " WHERE delivery_state = 'PENDING' AND created_at < ?",
                (cutoff,)).fetchall()
            if stale:
                db.execute(
                    "UPDATE hermes_deliveries SET delivery_state = 'EXPIRED'"
                    " WHERE delivery_state = 'PENDING' AND created_at < ?",
                    (cutoff,))
            rows = db.execute(
                "SELECT * FROM hermes_deliveries WHERE delivery_state ="
                " 'PENDING' ORDER BY created_at").fetchall()
        for row in stale:
            logger.info("hermes.delivery expired id=%s run=%s age>%ss",
                        row["delivery_id"], row["work_run_id"], int(max_age_s))
        return [dict(r) for r in rows]

    def claim_delivery(self, delivery_id: str) -> bool:
        """
        PENDING -> DELIVERING, atomically. False when another process (or
        an earlier attempt) already holds it - the second half of the
        no-duplicate guarantee, covering the crash-during-delivery window.
        """
        with self._connect() as db:
            cursor = db.execute(
                "UPDATE hermes_deliveries SET delivery_state = 'DELIVERING'"
                " WHERE delivery_id = ? AND delivery_state = 'PENDING'",
                (delivery_id,))
            return cursor.rowcount == 1

    def mark_delivered(self, delivery_id: str, *, via: str) -> None:
        with self._connect() as db:
            db.execute(
                "UPDATE hermes_deliveries SET delivery_state = 'DELIVERED',"
                " delivered_at = ?, delivered_via = ? WHERE delivery_id = ?",
                (time.time(), via, delivery_id))

    def release_delivery(self, delivery_id: str) -> None:
        """DELIVERING -> PENDING: the claimed attempt failed before the
        message reached the user; put it back rather than losing it."""
        with self._connect() as db:
            db.execute(
                "UPDATE hermes_deliveries SET delivery_state = 'PENDING'"
                " WHERE delivery_id = ? AND delivery_state = 'DELIVERING'",
                (delivery_id,))

    def sweep_undelivered(self, *, max_age_s: float = DELIVERY_TTL_S) -> int:
        """
        Startup recovery: every terminal run with no delivery row gets one.

        This is what makes delivery survive a crash between "run finished"
        and "delivery recorded", and what turns a Friday restart into
        "while you were away..." instead of a lost result. Idempotent by
        the UNIQUE constraint. Returns how many PENDING deliveries were
        created.

        Runs older than `max_age_s` are backfilled as already DELIVERED:
        the first process to boot after this feature ships must not stand
        up and recite every delegation Friday has ever completed.
        """
        created = 0
        now = time.time()
        with self._connect() as db:
            rows = db.execute(
                "SELECT r.* FROM hermes_work_runs r LEFT JOIN"
                " hermes_deliveries d ON d.work_run_id = r.work_run_id"
                " WHERE r.status IN (?,?,?) AND d.work_run_id IS NULL",
                (COMPLETE, PARTIAL, FAILED)).fetchall()
        for row in rows:
            record = dict(row)
            delivery_id = self.create_delivery(
                record["work_run_id"], goal=record.get("task", ""),
                status=record["status"],
                message=render_completion(record))
            if not delivery_id:
                continue
            if now - (record.get("last_event_at") or 0) > max_age_s:
                self.claim_delivery(delivery_id)
                self.mark_delivered(delivery_id, via="backfill")
            else:
                created += 1
        return created

    # -- deliveries ---------------------------------------------------------


# ---------------------------------------------------------------------------
# Locating Hermes
# ---------------------------------------------------------------------------


def locate() -> dict | None:
    """
    Where Hermes is on this machine, or None.

    Two env overrides, then the install convention, then PATH. The gateway
    is launched as `<hermes python> -m tui_gateway.entry` from the install
    directory - the same way Hermes's own desktop app launches it - because
    the `hermes` console script does not expose a gateway-only mode.
    """
    python = os.getenv("HERMES_PYTHON", "").strip()
    root = os.getenv("HERMES_DIR", "").strip()
    if python and root and Path(python).exists() and Path(root).is_dir():
        return {"python": python, "root": root, "how": "env"}

    binary = shutil.which("hermes")
    if binary:
        # git installs put the venv beside the package: <root>/venv/Scripts.
        candidate = Path(binary).resolve().parent.parent.parent
        python_exe = candidate / "venv" / "Scripts" / "python.exe"
        if (candidate / "tui_gateway").is_dir() and python_exe.exists():
            return {"python": str(python_exe), "root": str(candidate),
                    "how": "hermes-on-path"}
        # venv-relative: Scripts/python.exe next to the hermes script.
        sibling = Path(binary).resolve().parent / "python.exe"
        for parent in Path(binary).resolve().parents:
            if (parent / "tui_gateway").is_dir() and sibling.exists():
                return {"python": str(sibling), "root": str(parent),
                        "how": "venv-sibling"}
    return None


# ---------------------------------------------------------------------------
# The supervisor
# ---------------------------------------------------------------------------


class HermesUnavailable(RuntimeError):
    """Hermes could not be located, started or reached. Says which."""


class HermesSupervisor:
    """
    One Hermes gateway process, owned and supervised by Friday.

    Threading model: a reader thread drains stdout; responses are correlated
    to requests by JSON-RPC id and delivered through per-request events;
    gateway events go to `on_event` (called on the reader thread - keep the
    handler cheap) and into the work-run log.
    """

    #: How long the gateway may take to say `gateway.ready`. First start on
    #: a cold machine imports the world; measured 8-15s here.
    READY_TIMEOUT = 60.0
    REQUEST_TIMEOUT = 120.0

    def __init__(self, *, log: WorkRunLog | None = None,
                 on_event=None, answer_question=None,
                 command: list[str] | None = None,
                 cwd: str | None = None,
                 profile: str | None = None) -> None:
        self.log = log or WorkRunLog()
        self.on_event = on_event
        #: Callable(question: str, options: list[str]) -> str | None.
        #: None means "Friday cannot answer" - the question is parked as
        #: WAIT_USER rather than guessed. Wired to Friday's QuestionBroker
        #: by the caller.
        self.answer_question = answer_question
        self._command = command
        self._cwd = cwd
        #: Which Hermes profile the gateway runs under. None → env override
        #: or DEFAULT_PROFILE. Tests pass profile="" (with their own
        #: command) to skip the isolation they do not need.
        if profile is None:
            profile = os.getenv(ENV_PROFILE, DEFAULT_PROFILE).strip()
        self.profile = profile
        self.profile_dir = ""
        self._proc: subprocess.Popen | None = None
        self._reader: threading.Thread | None = None
        self._stderr_reader: threading.Thread | None = None
        self._lock = threading.Lock()
        self._next_id = 0
        self._pending: dict[int, dict] = {}   # id -> {"event", "response"}
        self._ready = threading.Event()
        self.state = DISCONNECTED
        self.hermes_version = ""
        #: session_id -> work_run_id, so events land on the right record.
        self._session_runs: dict[str, str] = {}
        #: The most recent completion text per session, set by
        #: message.complete - the structured end-of-turn signal.
        self._results: dict[str, dict] = {}
        self._turn_done: dict[str, threading.Event] = {}
        #: WorkRuns the user stopped. Consulted at the terminal transition:
        #: an interrupted run records PARTIAL (never COMPLETE) and creates
        #: no completion delivery - a cancelled job is not a triumph.
        self._interrupted: set[str] = set()
        #: Structured activity per session, for the stall classifier.
        #: Timestamps of the LAST event of each class - never wall-clock
        #: guesses about what Hermes might be doing.
        self._activity: dict[str, dict] = {}
        #: work_run_id -> live progress (tool count, current tool, last
        #: human line). Read by `progress()` for the UI's spoken updates.
        self._progress: dict[str, dict] = {}
        #: work_run_id -> governor lease, released at the terminal
        #: transition (FR-013: the worker count is the count of workers
        #: actually running, not of runs ever started).
        self._leases: dict[str, str] = {}
        self.log.on_terminal_hook = self._release_lease

    def _release_lease(self, work_run_id: str) -> None:
        lease = self._leases.pop(work_run_id, "")
        if lease:
            from friday import governor as G
            G.governor().release(lease)

    # -- lifecycle ----------------------------------------------------------

    def start(self) -> None:
        """Start the gateway and wait for `gateway.ready`."""
        if self._proc is not None and self._proc.poll() is None:
            return
        self.state = STARTING
        try:
            self.log.sweep_orphans()
        except Exception as exc:  # noqa: BLE001 - housekeeping never blocks the gateway
            logger.warning("hermes.run orphan sweep failed: %s", exc)
        command, cwd = self._launch_plan()
        env = dict(os.environ)
        # Scrub inherited Hermes SESSION state - not Hermes configuration.
        # When Friday itself runs under a Hermes agent (development,
        # diagnostics, delegated work), the parent's session variables leak
        # into the child gateway and tool execution binds to resources the
        # PARENT session owns: measured as the child's read_file hanging for
        # exactly its 420s tool timeout while the same read completed in ~1s
        # from a clean CLI. Config variables (HERMES_HOME etc.) must survive
        # or the child loses its provider/auth and fails init - also
        # measured, the other direction.
        for key in ("HERMES_SESSION_ID", "HERMES_AGENT",
                    "HERMES_TURN_LEASE_TIMEOUT", "HERMES_KANBAN_BOARD",
                    "HERMES_SESSION_SOURCE", "HERMES_TUI_SIDECAR_URL",
                    "HERMES_INTERACTIVE", "HERMES_MAX_ITERATIONS",
                    # MSYS runtime state. When Friday (or a dev harness) is
                    # itself launched from an MSYS bash, these leak into the
                    # child gateway and poison ITS Git-Bash tool sessions -
                    # captured via a faulthandler stack dump: the gateway's
                    # bash probe wedged in subprocess _communicate, which is
                    # the Windows kill-then-unbounded-drain hang, and the
                    # tool then ate Hermes's whole 420s ceiling. A gateway
                    # launched from a clean console never sees these.
                    "MSYSTEM", "MSYS", "MSYS2_PATH_TYPE", "ORIGINAL_PATH",
                    "SHELL", "SHLVL", "OLDPWD", "PS1"):
            env.pop(key, None)
        # Profile isolation: Friday's gateway runs under its own Hermes
        # profile (a fully independent HERMES_HOME with its own sessions,
        # state.db and tool-execution leases), so the user's manual
        # `hermes` CLI on the default profile and Friday's runtime never
        # contend. This is Hermes's own supported isolation boundary -
        # `hermes -p friday` sets exactly this variable.
        if self.profile:
            home = profile_home(self.profile)
            if not home:
                raise HermesUnavailable(
                    f"Hermes profile {self.profile!r} does not exist; create "
                    f"it with: hermes profile create {self.profile} --clone")
            env["HERMES_HOME"] = home
            self.profile_dir = home
        env["PYTHONUNBUFFERED"] = "1"
        # Headless gateway: there is no TTY for Hermes to raise its
        # first-seen shell-hook prompt on, and a tool call that triggers one
        # blocks forever. Hermes's own gateway modes ship an --accept-hooks
        # flag for exactly this; the env form is its documented equivalent.
        env["HERMES_ACCEPT_HOOKS"] = "1"
        logger.info("hermes.starting command=%s", command[0])
        self._ready.clear()
        try:
            self._proc = subprocess.Popen(
                command, cwd=cwd, env=env,
                stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=subprocess.PIPE, text=True, encoding="utf-8",
                errors="replace", bufsize=1)
        except OSError as exc:
            self.state = DISCONNECTED
            raise HermesUnavailable(f"could not start Hermes: {exc}") from exc
        self._reader = threading.Thread(target=self._read_loop, daemon=True,
                                        name="hermes-gateway-reader")
        self._reader.start()
        self._stderr_reader = threading.Thread(
            target=self._drain_stderr, daemon=True, name="hermes-stderr")
        self._stderr_reader.start()
        if not self._ready.wait(self.READY_TIMEOUT):
            self.stop()
            raise HermesUnavailable(
                f"Hermes gateway did not become ready within "
                f"{self.READY_TIMEOUT:.0f}s")
        self.state = CONNECTED

    def _launch_plan(self) -> tuple[list[str], str | None]:
        if self._command:
            return self._command, self._cwd
        found = locate()
        if not found:
            raise HermesUnavailable(
                "Hermes is not installed here (checked HERMES_PYTHON/"
                "HERMES_DIR, then PATH)")
        return ([found["python"], "-m", "tui_gateway.entry"], found["root"])

    def alive(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    def health(self) -> dict:
        """A cheap liveness probe: process up + a real RPC answered.

        `session.list` answers from memory in milliseconds; `commands.catalog`
        imports the whole `hermes_cli.commands` registry the first time it is
        asked (5s here, more on a loaded host) and used to make a healthy,
        freshly started gateway report `alive: False` (engine validation,
        2026-09-05). The catalog is still asked - the command count is the
        useful number - but only after the cheap probe has answered, with
        its own generous cold-start timeout."""
        if not self.alive():
            return {"alive": False, "state": self.state}
        try:
            self.request("session.list", {}, timeout=15)
        except Exception as exc:                             # noqa: BLE001
            return {"alive": False, "state": self.state, "error": str(exc)}
        try:
            catalog = self.request("commands.catalog", {}, timeout=45)
            return {"alive": True, "state": self.state,
                    "commands": len(catalog.get("pairs", []))}
        except Exception as exc:                             # noqa: BLE001
            return {"alive": True, "state": self.state, "commands": None,
                    "warning": f"commands.catalog: {exc}"}

    def stop(self) -> None:
        """Orderly shutdown. Friday owns the process it started."""
        proc = self._proc
        self._proc = None
        self.state = DISCONNECTED
        # Whatever was running died with the process: its worker slot is
        # free again. The run record is handled by restart()/the sweep;
        # the governor must not keep counting a worker that no longer
        # exists (FR-014: a worker crash cannot wedge the control plane).
        for work_run_id in list(self._leases):
            self._release_lease(work_run_id)
        if proc is None:
            return
        try:
            if proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    proc.kill()
        except OSError:
            pass

    def restart(self) -> None:
        """Crash recovery: stop, start, and mark affected runs truthfully."""
        held = dict(self._session_runs)
        self.stop()
        self._session_runs.clear()
        self.start()
        # Sessions died with the process. The durable stored ids survive and
        # a caller can resume; the in-memory map must not lie about it.
        for sid, work_run_id in held.items():
            record = self.log.get(work_run_id)
            if record and record["status"] not in (COMPLETE, PARTIAL, FAILED):
                self.log.update(work_run_id, status=STARTING)

    # -- wire ---------------------------------------------------------------

    def request(self, method: str, params: dict, *,
                timeout: float | None = None) -> dict:
        """One JSON-RPC request, correlated by id. Raises on error frames."""
        if not self.alive():
            raise HermesUnavailable("Hermes gateway is not running")
        with self._lock:
            self._next_id += 1
            rid = self._next_id
            slot = {"event": threading.Event(), "response": None}
            self._pending[rid] = slot
        frame = {"jsonrpc": "2.0", "id": rid, "method": method,
                 "params": params}
        try:
            self._proc.stdin.write(json.dumps(frame) + "\n")
            self._proc.stdin.flush()
        except (OSError, ValueError) as exc:
            raise HermesUnavailable(f"could not write to Hermes: {exc}") from exc
        if not slot["event"].wait(timeout or self.REQUEST_TIMEOUT):
            self._pending.pop(rid, None)
            raise TimeoutError(f"{method} unanswered after "
                               f"{timeout or self.REQUEST_TIMEOUT:.0f}s")
        response = slot["response"]
        if "error" in response:
            error = response["error"]
            raise RuntimeError(
                f"{method} failed: [{error.get('code')}] {error.get('message')}")
        return response.get("result") or {}

    def _read_loop(self) -> None:
        proc = self._proc
        if proc is None or proc.stdout is None:
            return
        for line in proc.stdout:
            line = line.strip()
            if not line:
                continue
            try:
                frame = json.loads(line)
            except json.JSONDecodeError:
                continue                       # gateway noise, not protocol
            if frame.get("method") == "event":
                self._handle_event(frame.get("params") or {})
            elif "id" in frame:
                slot = self._pending.pop(frame["id"], None)
                if slot is not None:
                    slot["response"] = frame
                    slot["event"].set()
        # EOF: the process ended.
        if self.state != DISCONNECTED:
            self.state = DISCONNECTED
            logger.warning("hermes.gateway_exited")

    def _drain_stderr(self) -> None:
        proc = self._proc
        if proc is None or proc.stderr is None:
            return
        for line in proc.stderr:
            logger.debug("hermes.stderr %s", line.rstrip()[:400])

    # -- events -------------------------------------------------------------

    def _handle_event(self, params: dict) -> None:
        kind = params.get("type", "")
        sid = params.get("session_id", "")
        payload = params.get("payload") or {}

        if kind == "gateway.ready":
            self._ready.set()
            return

        work_run_id = self._session_runs.get(sid)
        friday_event = EVENT_MAP.get(kind, kind)

        # Activity ledger for the stall classifier. Recorded for every
        # session event, from the structured stream itself.
        if sid:
            act = self._activity.setdefault(sid, {})
            now = time.time()
            act["last_event_at"] = now
            if kind in ("message.delta", "reasoning.delta", "thinking.delta",
                        "message.start", "message.interim"):
                act["last_model_event_at"] = now
            elif kind == "tool.start":
                act["last_tool_start_at"] = now
                act["current_tool"] = str((payload or {}).get("name", ""))
                act.pop("last_tool_complete_at", None)
            elif kind in ("tool.generating", "tool.progress",
                          "tool.output_risk"):
                act["last_tool_progress_at"] = now
            elif kind == "tool.complete":
                act["last_tool_complete_at"] = now
                act.pop("current_tool", None)
            elif kind == "message.complete":
                act.pop("current_tool", None)

        if work_run_id:
            record = self.log.get(work_run_id) or {}
            updates: dict = {"events_seen": record.get("events_seen", 0) + 1}
            # Progress ledger, in memory, for "what is Hermes doing right
            # now" - the owner's complaint (2026-09-02) was one sentence at
            # delegation and then silence until the end. Tool names are the
            # honest unit of progress: "reading policy.py", "ran the tests".
            prog = self._progress.setdefault(work_run_id, {
                "tools": 0, "current": "", "last": "", "started_at": time.time(),
                "seq": 0})
            if kind == "tool.start":
                name = str((payload or {}).get("name", "")) or "a tool"
                prog["tools"] += 1
                prog["last"] = _describe_tool(name, payload)
                prog["current"] = prog["last"]   # spoken, so described
                prog["seq"] += 1
            elif kind == "tool.complete":
                prog["current"] = ""
            elif kind == "message.complete":
                prog["current"] = ""
                prog["seq"] += 1
            updates["progress_json"] = json.dumps(prog)
            #: Set only after the durable update lands, so a waiter that
            #: wakes on it reads the completed record, not a stale WORKING.
            settle: threading.Event | None = None
            if kind == "message.complete":
                self._results[sid] = payload
                settle = self._turn_done.get(sid)
                if work_run_id in self._interrupted:
                    # The user stopped this run. Whatever Hermes managed to
                    # finish before the interrupt landed is preserved as
                    # PARTIAL - never announced as a completed triumph.
                    updates["status"] = PARTIAL
                    updates["result"] = str(payload.get("text", ""))[:4000]
                elif payload.get("status") == "error":
                    updates["status"] = FAILED
                    updates["result"] = str(payload.get("text", ""))[:4000]
                    updates.update(self._capped_update(
                        work_run_id, str(payload.get("text", ""))))
                else:
                    updates["status"] = COMPLETE
                    updates["result"] = str(payload.get("text", ""))[:4000]
            elif kind == "session.usage":
                updates["usage_json"] = json.dumps(payload)[:4000]
            elif kind == "error":
                updates["status"] = FAILED
                updates["result"] = json.dumps(payload)[:2000]
                updates.update(self._capped_update(
                    work_run_id, json.dumps(payload)))
                settle = self._turn_done.get(sid)
            elif kind == "clarify.request":
                # Never brokered on this thread: answering calls request(),
                # and this reader thread is the one that delivers responses -
                # brokering inline would deadlock until the RPC timed out.
                threading.Thread(
                    target=self._broker_clarify,
                    args=(sid, work_run_id, payload),
                    daemon=True, name="hermes-clarify-broker").start()
                record = None                       # broker updates the log
            if record is not None:
                self.log.update(work_run_id, progress=prog, **updates)
                # Terminal transition => durable delivery, created here at
                # the moment of truth (the startup sweep covers crashes
                # that land between the update above and this insert).
                # Interrupted runs are excluded: the user who said "stop"
                # was answered in that conversation; the broker announcing
                # the stopped job's output later would be the F5 ghost.
                if updates.get("status") in (COMPLETE, PARTIAL, FAILED):
                    fresh = self.log.get(work_run_id) or {}
                    # H6 learns from EVERY terminal run - a stopped job
                    # still spent tokens, and route learning that never
                    # sees cancelled work underprices risky routes.
                    self._record_route_outcome(fresh)
                    if work_run_id not in self._interrupted:
                        self.log.create_delivery(
                            work_run_id, goal=fresh.get("task", ""),
                            status=fresh.get("status", ""),
                            message=render_completion(fresh))
            if settle is not None:
                settle.set()

        if self.on_event is not None:
            try:
                self.on_event(friday_event, sid, payload)
            except Exception:                                # noqa: BLE001
                logger.exception("hermes event handler failed")

    def _capped_update(self, work_run_id: str, text: str) -> dict:
        """
        A gateway error that names a usage cap, not an outage: mark the
        cooldown for the EFFECTIVE provider/model so the next
        plan_delegation routes around it, and record why on the run.
        Anything that is not a cap diagnoses to something else and this
        returns {} untouched - only CAPPED changes the record here.
        """
        from friday import provider_diagnostics as PD
        from friday import provider_cooldowns as PC

        class _GatewayText:                    # ponytail: diagnose() reads
            def __init__(self, body):           # str(error)/.body/.status_code;
                self.body = body                # a real exception isn't
                self.status_code = None         # available for a JSON payload

            def __str__(self):
                return self.body

        found = PD.diagnose(_GatewayText(text))
        if found.kind != PD.CAPPED:
            return {}
        record = self.log.get(work_run_id) or {}
        provider, model = record.get("provider", ""), record.get("model", "")
        PC.mark(provider, model, found.reset_at, reason=found.detail)
        from friday import execution_economics as ee
        route_reason = (f"{provider or 'provider'} capped until "
                        f"{ee._fmt_hhmm(found.reset_at)}")
        # "CAPPED" mirrors friday.objectives.FAILURE_CAPPED's literal value;
        # not imported to avoid pulling objectives into the bridge module.
        return {"failure_kind": "CAPPED", "route_reason": route_reason}

    def _broker_clarify(self, sid: str, work_run_id: str,
                        payload: dict) -> None:
        """
        A Hermes question. Friday answers from evidence or parks it.

        Never guessed: `answer_question` returns None when nothing grounded
        settles it, and the run goes to WAIT_USER with the question recorded
        - visible, durable, and unanswered rather than invented.
        """
        question = str(payload.get("question")
                       or payload.get("prompt") or "")[:2000]
        options = payload.get("options") or payload.get("choices") or []
        if isinstance(options, dict):
            options = list(options.values())
        options = [str(o) for o in options]

        answer = None
        if self.answer_question is not None:
            try:
                answer = self.answer_question(question, options)
            except Exception:                                # noqa: BLE001
                logger.exception("question broker failed; parking question")

        if answer:
            self.log.update(work_run_id, status=WORKING, pending_question="")
            try:
                # The gateway's _respond reads the reply from params["answer"]
                # (clarify.respond → _respond(rid, params, "answer")).
                self.request("clarify.respond",
                             {"session_id": sid, "answer": answer,
                              "request_id": payload.get("request_id", "")},
                             timeout=30)
            except Exception:                                # noqa: BLE001
                logger.exception("clarify.respond failed")
                self.log.update(work_run_id, status=WAIT_USER,
                                pending_question=question)
        else:
            self.log.update(work_run_id, status=WAIT_USER,
                            pending_question=question)

    # -- work ---------------------------------------------------------------

    def delegate(self, bundle: TaskBundle, *, friday_run_id: str = "",
                 model: str = "", provider: str = "", workspace: str = "",
                 route_reason: str = "", wait: bool = False,
                 turn_timeout: float = 900.0, reasoning_effort: str = "",
                 share_memory: bool = True) -> dict:
        """
        Send one bounded task to a fresh Hermes session.

        Fresh session per task, deliberately: per-task model choice without
        breaking another session's prompt-cache continuity, and a bounded
        transcript instead of one ever-growing conversation.

        `workspace` matters more than it looks: a session created without a
        cwd works in the gateway's launch directory, and file tools against
        another drive can stall behind approval prompts nobody answers.
        Pass the project root the task is about.

        `reasoning_effort` is the token-aware depth: a per-session override
        the gateway honours (none|minimal|low|medium|high|xhigh|max|ultra).
        `share_memory` compiles the shared memory relevant to the goal into
        the bundle, so the sub-agent knows what Friday knows.
        """
        if self.state == DISCONNECTED:
            self.start()

        # FR-013 / FR-056: the resource governor decides whether the
        # machine can carry another worker BEFORE a session is created.
        # A refusal is a structured answer, not an exception the model
        # cannot read: the caller reports "queued under pressure" honestly.
        from friday import governor as G
        decision = G.governor().admit(G.WORKER, label=f"hermes:{bundle.goal[:40]}",
                                      objective_id=friday_run_id)
        if not decision.admitted:
            raise G.Refused(decision)

        if share_memory:
            bundle = bundle.with_memory()
        measure = bundle.measure()
        if measure["oversized"]:
            logger.warning("hermes.bundle_oversized chars=%d", measure["chars"])

        work_run_id = self.log.create(
            task=bundle.goal[:500], friday_run_id=friday_run_id,
            bundle_chars=measure["chars"], token_budget=bundle.token_budget)
        self._leases[work_run_id] = decision.lease

        create: dict = {"source": "friday", "title": f"Friday: {bundle.goal[:60]}"}
        if workspace:
            create["cwd"] = workspace
        if model:
            create["model"] = model
        if provider:
            create["provider"] = provider
        if reasoning_effort:
            create["reasoning_effort"] = reasoning_effort
        session = self.request("session.create", create, timeout=60)
        sid = session["session_id"]
        stored = session.get("stored_session_id", "")
        info = session.get("info") or {}
        self._session_runs[sid] = work_run_id
        self._turn_done[sid] = threading.Event()

        # H0 route observability. The gateway's session info omits the
        # provider (measured: provider='' on every run) - so resolve the
        # EFFECTIVE pair truthfully: what was requested, what the session
        # reports, and what the profile config supplies when the session
        # is silent. A requested-vs-effective mismatch IS the fallback,
        # recorded with its reason rather than silently normalized.
        eff_model = str(info.get("model", "")) or model
        eff_provider = str(info.get("provider", "")) or provider
        if not eff_provider:
            eff_provider, cfg_model = _profile_model_default()
            eff_model = eff_model or cfg_model
        fb: dict = {}
        if model and eff_model and model != eff_model:
            fb = {"fallback_from": model, "fallback_to": eff_model,
                  "fallback_reason": "session.create returned a different "
                                     "model than requested"}
        self.log.update(
            work_run_id, hermes_session_id=sid,
            hermes_stored_session_id=stored,
            model=eff_model, provider=eff_provider,
            route_reason=route_reason or "default: friday profile model",
            status=WORKING, **fb)
        self.state = WORKING

        self.request("prompt.submit",
                     {"session_id": sid, "text": bundle.render()},
                     timeout=60)

        out = {"work_run_id": work_run_id, "session_id": sid,
               "stored_session_id": stored, "bundle": measure}
        if wait:
            out["result"] = self.wait_for(work_run_id, timeout=turn_timeout)
        return out

    def wait_for(self, work_run_id: str, *, timeout: float = 900.0) -> dict:
        """Block until the run's turn completes, then return the record."""
        record = self.log.get(work_run_id)
        if record is None:
            raise LookupError(f"no work run {work_run_id!r}")
        sid = record["hermes_session_id"]
        done = self._turn_done.get(sid)
        if done is not None:
            done.wait(timeout)
        final = self.log.get(work_run_id) or {}
        final["result_payload"] = self._results.get(sid, {})
        return final

    def progress(self, work_run_id: str) -> dict:
        """What Hermes is doing on this run, right now, from the event stream.

        `seq` increments on every tool start and on completion, so a poller
        can say something only when it has changed - the UI speaks a new
        `line` once, not the same one every five seconds."""
        record = self.log.get(work_run_id) or {}
        prog = dict(self._progress.get(work_run_id) or {})
        if not prog and record.get("progress_json"):
            # Another process ran this delegation: read its persisted ledger.
            try:
                prog = dict(json.loads(record["progress_json"]))
            except (TypeError, ValueError):
                prog = {}
        status = record.get("status", "")
        elapsed = int(time.time() - (prog.get("started_at")
                                     or record.get("started_at")
                                     or time.time()))
        if status in (COMPLETE, PARTIAL, FAILED):
            head = {COMPLETE: "finished", PARTIAL: "stopped part way", FAILED: "failed"}[status]
            line = f"Hermes {head} after {prog.get('tools', 0)} steps."
        elif prog.get("current"):
            line = f"Hermes is {prog.get('last') or prog['current']} - step {prog['tools']}, {elapsed}s in."
        elif prog.get("tools"):
            line = f"Hermes finished {prog['last']} and is thinking - step {prog['tools']}, {elapsed}s in."
        else:
            line = f"Hermes is reading the task - {elapsed}s in."
        route_reason = record.get("route_reason", "")
        # ponytail: switched_from rides on route_reason's own wording
        # ("<provider> capped until HH:MM -> ...") rather than a new
        # column - plan_delegation already put the provider name there.
        switched_from = (route_reason.split(" capped until", 1)[0]
                         if " capped until " in route_reason else "")
        return {"work_run_id": work_run_id, "status": status, "line": line,
                "seq": prog.get("seq", 0), "tools": prog.get("tools", 0),
                "current": prog.get("current", ""), "elapsed_s": elapsed,
                "result": (record.get("result") or "")[:1200],
                "route_reason": route_reason, "switched_from": switched_from,
                "handoff": record.get("handoff", "")}

    def steer(self, work_run_id: str, text: str) -> dict:
        """
        Mid-run course correction, without killing the turn.

        `session.steer` refuses with 4010 while the agent is still being
        built (the async build window right after prompt.submit).
        `session.redirect` queues server-side in exactly that window - read
        in Hermes's own methods_session.py - so the refusal falls back to it
        rather than failing a correction the user already spoke.
        """
        record = self.log.get(work_run_id)
        if record is None:
            raise LookupError(f"no work run {work_run_id!r}")
        sid = record["hermes_session_id"]
        try:
            result = self.request("session.steer",
                                  {"session_id": sid, "text": text},
                                  timeout=30)
        except RuntimeError as exc:
            if "4010" not in str(exc):
                raise
            result = self.request("session.redirect",
                                  {"session_id": sid, "text": text},
                                  timeout=30)
        if result.get("status") in ("queued", "redirected"):
            self.log.update(work_run_id, status=STEERED)
        return result

    def _record_route_outcome(self, record: dict) -> None:
        """
        H6: one durable outcome row per terminal run, written from the
        fields the run already carries - no extra model calls, no extra
        requests. Failures here never disturb delivery.
        """
        try:
            from friday.execution_economics import RouteOutcomes, \
                classify_task
            usage = {}
            try:
                usage = json.loads(
                    record.get("usage_json") or "{}").get("usage", {})
            except (ValueError, AttributeError):
                pass
            econ = classify_task(record.get("task", ""))
            reason = record.get("route_reason", "")
            level = reason.split("/", 1)[0] if "/" in reason else ""
            tier = (reason.split("/", 1)[1].split(":", 1)[0]
                    if "/" in reason else "")
            RouteOutcomes(self.log._path).record(
                record["work_run_id"], task_class=econ.kind,
                route_level=level, tier=tier,
                model=record.get("model", ""),
                provider=record.get("provider", ""),
                calls=usage.get("calls", 0),
                prompt_tokens=usage.get("prompt", 0),
                output_tokens=usage.get("completion", 0),
                duration_s=max(0.0, record.get("last_event_at", 0)
                               - record.get("started_at", 0)),
                status=record.get("status", ""))
        except Exception:                                    # noqa: BLE001
            logger.exception("route-outcome recording failed (non-fatal)")


    def interrupt(self, work_run_id: str) -> dict:
        record = self.log.get(work_run_id)
        if record is None:
            raise LookupError(f"no work run {work_run_id!r}")
        # The stop race, both directions (measured in gate F5: a stopped
        # review completed anyway and the broker then announced "Hermes
        # finished" - a cancelled job must not be delivered as a triumph):
        #   interrupt BEFORE completion - _interrupted makes the event
        #     handler record PARTIAL and skip delivery creation;
        #   completion BEFORE interrupt - the delivery row already exists,
        #     so consume it here (via='stopped-by-user').
        self._interrupted.add(work_run_id)
        self.log.update(work_run_id, status=CANCELLING)
        result = self.request(
            "session.interrupt",
            {"session_id": record["hermes_session_id"]}, timeout=30)
        self.log.update(work_run_id, status=PARTIAL)
        # Consume any delivery for this run - the completion may have raced
        # ahead of the interrupt in either order. And because a terminal run
        # with NO delivery row would be resurrected by the startup sweep,
        # the row must exist and be consumed, not merely skipped: create it
        # if the event handler didn't, then mark it delivered.
        record = self.log.get(work_run_id) or {}
        self.log.create_delivery(
            work_run_id, goal=record.get("task", ""),
            status=record.get("status", PARTIAL),
            message=render_completion(record))
        for delivery in self.log.pending_deliveries():
            if (delivery["work_run_id"] == work_run_id
                    and self.log.claim_delivery(delivery["delivery_id"])):
                self.log.mark_delivered(delivery["delivery_id"],
                                        via="stopped-by-user")
        return result

    def usage(self, work_run_id: str) -> dict:
        """Token accounting for one run, from Hermes's own session.usage."""
        record = self.log.get(work_run_id)
        if record is None:
            raise LookupError(f"no work run {work_run_id!r}")
        try:
            live = self.request(
                "session.usage",
                {"session_id": record["hermes_session_id"]}, timeout=30)
        except Exception:                                    # noqa: BLE001
            live = {}
        stored = {}
        if record.get("usage_json"):
            try:
                stored = json.loads(record["usage_json"])
            except json.JSONDecodeError:
                pass
        return {"live": live, "last_event": stored}

    # -- stall classification ------------------------------------------------

    #: Stall ceilings are per TOOL CLASS, not one blanket number. A silent
    #: read_file is wedged after seconds; a silent build may be honest for
    #: minutes; anything emitting progress resets its clock regardless of
    #: class. Unknown tools get DEFAULT_TOOL_STALL_S.
    TOOL_STALL_CLASSES: dict[str, float] = {
        # FAST_IO - local filesystem operations answer in seconds or never.
        "read_file": 30.0, "write_file": 30.0, "list_files": 30.0,
        "search_files": 30.0, "patch": 30.0, "glob": 30.0, "grep": 30.0,
        # COMMAND - shells legitimately run installs/builds.
        "terminal": 600.0, "process": 600.0,
        # LONG_EXTERNAL - network-bound work with real long tails.
        "web_search": 120.0, "web_extract": 240.0, "browser_exec": 600.0,
        "delegate_task": 1800.0,
    }
    #: Fallback ceiling for tools not named above.
    DEFAULT_TOOL_STALL_S = 180.0
    #: A turn with no events of any kind for this long is stalled.
    TURN_STALL_S = 300.0

    def tool_stall_ceiling(self, tool: str) -> float:
        """The silence ceiling for one tool, tolerant of mcp__ prefixes."""
        name = (tool or "").rpartition("__")[2].strip().lower()
        return self.TOOL_STALL_CLASSES.get(name, self.DEFAULT_TOOL_STALL_S)

    GATEWAY_DEAD = "GATEWAY_DEAD"
    IDLE = "IDLE"
    TOOL_RUNNING = "TOOL_RUNNING"
    TOOL_STALLED = "TOOL_STALLED"
    TURN_STALLED = "TURN_STALLED"
    HEALTHY_WORKING = "WORKING"

    def stall_state(self, work_run_id: str) -> dict:
        """
        What a run is actually doing, from its structured event ledger.

        The rule that shapes this: elapsed task time proves nothing. A tool
        that started long ago but keeps emitting progress is healthy; a tool
        that started recently and went silent is not. Classification reads
        only recorded event timestamps - never Hermes prose, never a guess.
        """
        record = self.log.get(work_run_id)
        if record is None:
            raise LookupError(f"no work run {work_run_id!r}")
        if record["status"] in (COMPLETE, PARTIAL, FAILED):
            return {"state": self.IDLE, "status": record["status"]}
        if not self.alive():
            return {"state": self.GATEWAY_DEAD, "status": record["status"]}

        act = self._activity.get(record["hermes_session_id"], {})
        now = time.time()
        report = {"status": record["status"],
                  "current_tool": act.get("current_tool", ""),
                  "seconds_since_event": round(
                      now - act["last_event_at"], 1)
                  if "last_event_at" in act else None}

        tool_running = bool(act.get("current_tool"))
        if tool_running:
            # Progress counts from whichever came later: the start or the
            # most recent progress frame.
            latest = max(act.get("last_tool_start_at", 0),
                         act.get("last_tool_progress_at", 0))
            silent = now - latest if latest else None
            ceiling = self.tool_stall_ceiling(act.get("current_tool", ""))
            report["stall_ceiling_s"] = ceiling
            if silent is not None and silent > ceiling:
                report["state"] = self.TOOL_STALLED
                report["tool_silent_s"] = round(silent, 1)
            else:
                report["state"] = self.TOOL_RUNNING
            return report

        last = act.get("last_event_at")
        if last is None:
            # No structured events at all since the prompt went in. The
            # durable record's timestamps are the only honest clock left -
            # a turn that has produced zero events for the whole ceiling is
            # stalled, not quietly healthy.
            last = record.get("last_event_at") or record.get("started_at")
        if last is not None and now - last > self.TURN_STALL_S:
            report["state"] = self.TURN_STALLED
        else:
            report["state"] = self.HEALTHY_WORKING
        return report

    def recover_stalled(self, work_run_id: str) -> dict:
        """
        Contain a stall without touching Hermes internals.

        interrupt if the gateway still answers; restart the gateway if it
        does not. Either way the work run records what happened rather than
        sitting in WORKING forever.
        """
        verdict = self.stall_state(work_run_id)
        state = verdict.get("state")
        if state == self.GATEWAY_DEAD:
            self.restart()
            return {"action": "restarted_gateway", "was": verdict}
        if state in (self.TOOL_STALLED, self.TURN_STALLED):
            try:
                self.interrupt(work_run_id)
                return {"action": "interrupted", "was": verdict}
            except Exception:                                # noqa: BLE001
                logger.exception("stall interrupt failed; restarting gateway")
                self.restart()
                return {"action": "restarted_gateway", "was": verdict}
        return {"action": "none", "was": verdict}

    # -- stall classification ------------------------------------------------
