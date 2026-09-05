"""
Hermes MODEL_GATEWAY - Friday's inference-only door into Hermes's providers.

PRD v3.1 §4.9 / §9.4. Friday is the brain; Hermes brokers model access. Two
things are true of every call through here, by construction:

  1. No Hermes agent loop starts. The other side of the pipe is
     `friday/hermes_model_gateway_worker.py`, which calls Hermes's
     stateless `agent.auxiliary_client.call_llm` once per request - no
     tools, skills, subagents, session history or repository access
     (FR-069, FR-076, FR-079). Promotion to EXECUTION_ENGINE is a different
     door entirely: `friday.hermes_bridge.HermesSupervisor.delegate`.
  2. Friday never sees a credential. The worker runs in the Hermes venv
     under Friday's Hermes profile; it returns text, usage and route
     metadata (FR-073).

What Friday adds on this side:

  - `ModelGatewayRequest` / `ModelGatewayResult` - the PRD envelope.
  - `TokenBudget` profiles per task class (FR-077) with an explicit
    escalation gate: a request whose compiled context exceeds its class
    budget is refused unless `escalate=True` is passed deliberately.
  - `GrowthGuard` (FR-078): detects abnormal token growth across retries /
    handoffs within an objective and stops before the objective budget is
    exhausted.
  - Bounded failover (FR-081): a provider failure is classified, the
    request is checkpointed (it is stateless, so the checkpoint IS the
    request), and at most `max_failover` compatible candidates are tried.
    The objective is never reset and context is never re-sent to the same
    failed route.
  - Telemetry (FR-080): every call - success or failure - is appended to
    the `gateway_calls` table with route, latency, usage, failover count,
    entitlement state and objective attribution. Never the prompt, never
    a secret.
  - Provider discovery (FR-071/072): `providers()` asks Hermes what is
    configured and authenticated NOW; each route carries `route_kind`
    (api / subscription / free_tier / local) so a consumer subscription is
    never advertised as generic API credit.
  - Privacy truthfulness (FR-074): `ModelGatewayResult.boundary` says
    `upstream_cloud` or `local` from the route kind; nothing here claims a
    cloud call became private because Hermes brokered it.
"""
from __future__ import annotations

import json
import logging
import os
import subprocess
import threading
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

from friday import provider_health

logger = logging.getLogger("friday.model_gateway")

# ---------------------------------------------------------------------------
# Task classes and token budget profiles (FR-002 vocabulary, FR-077 budgets)
# ---------------------------------------------------------------------------

TRIVIAL = "TRIVIAL"
SIMPLE = "SIMPLE"
STANDARD = "STANDARD"
COMPLEX = "COMPLEX"
LONG_RUNNING = "LONG_RUNNING"
CRITICAL = "CRITICAL"
TASK_CLASSES = (TRIVIAL, SIMPLE, STANDARD, COMPLEX, LONG_RUNNING, CRITICAL)

#: Quality tiers a caller may ask for. `fast` is the profile's economy
#: model, `deep` the strongest configured one.
TIER_FAST = "fast"
TIER_STANDARD = "standard"
TIER_DEEP = "deep"
TIERS = (TIER_FAST, TIER_STANDARD, TIER_DEEP)


@dataclass(frozen=True)
class TokenBudget:
    """Per-request ceilings for one task class.

    `max_input_tokens` bounds the compiled context package; `max_output`
    the completion; `reasoning` is the reasoning-effort label handed to the
    provider where supported. `objective_ceiling` is the running total an
    objective may spend through the gateway before the growth guard stops
    it regardless of per-call health.
    """

    task_class: str
    max_input_tokens: int
    max_output_tokens: int
    reasoning: str
    objective_ceiling: int
    default_tier: str


BUDGETS: dict[str, TokenBudget] = {
    TRIVIAL: TokenBudget(TRIVIAL, 1_500, 256, "none", 8_000, TIER_FAST),
    SIMPLE: TokenBudget(SIMPLE, 4_000, 800, "low", 30_000, TIER_FAST),
    STANDARD: TokenBudget(STANDARD, 12_000, 2_000, "medium", 120_000, TIER_STANDARD),
    COMPLEX: TokenBudget(COMPLEX, 32_000, 4_000, "high", 400_000, TIER_DEEP),
    LONG_RUNNING: TokenBudget(LONG_RUNNING, 32_000, 4_000, "high", 1_200_000, TIER_STANDARD),
    CRITICAL: TokenBudget(CRITICAL, 48_000, 6_000, "high", 600_000, TIER_DEEP),
}


def budget_for(task_class: str) -> TokenBudget:
    if task_class not in BUDGETS:
        raise ValueError(f"unknown task class {task_class!r}; "
                         f"known: {list(BUDGETS)}")
    return BUDGETS[task_class]


def approx_tokens(text: str) -> int:
    """~4 chars/token. Deliberately simple and provider-agnostic; the point
    is a stable bound for budget decisions, not a billing estimate."""
    return (len(text or "") + 3) // 4


class BudgetExceeded(ValueError):
    """The compiled context is over the class budget and escalation was
    not requested. A simple request cannot silently become a large one."""


class GrowthStopped(RuntimeError):
    """The growth guard refused the call. Replan; do not retry."""


# ---------------------------------------------------------------------------
# Envelope
# ---------------------------------------------------------------------------


@dataclass
class ModelGatewayRequest:
    """PRD §9.4 ModelGatewayRequest. `context_package` is the compiled,
    bounded message list - never a memory dump."""

    objective_id: str
    task_class: str
    context_package: list[dict]
    preferred_quality_tier: str = ""
    required_capabilities: tuple[str, ...] = ()
    privacy_policy: str = "default"          # default | local_only
    max_input_tokens: int = 0                # 0 = class budget
    max_output_tokens: int = 0               # 0 = class budget
    reasoning_budget: str = ""               # "" = class default
    latency_budget_ms: int = 0               # 0 = no latency preference
    provider_allowlist: tuple[str, ...] = ()
    provider_denylist: tuple[str, ...] = ()
    allow_failover: bool = True
    escalate: bool = False                   # explicit over-budget consent
    temperature: float | None = None
    worker: str = "friday"                   # attribution (FR-055/080)
    timeout_s: float = 60.0

    def input_tokens(self) -> int:
        return sum(approx_tokens(str(m.get("content", "")))
                   for m in self.context_package)


@dataclass
class ModelGatewayResult:
    """PRD §9.4 ModelGatewayResult, plus the boundary label FR-074 needs."""

    status: str                              # ok | failed | refused
    provider: str = ""
    model: str = ""
    response: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    cached_tokens: int = 0
    reasoning_tokens: int = 0
    latency_ms: int = 0
    failover_count: int = 0
    entitlement_state: str = ""
    warnings: list[str] = field(default_factory=list)
    route_kind: str = ""
    boundary: str = ""                       # upstream_cloud | local
    attempts: list[dict] = field(default_factory=list)
    call_id: int = 0

    def to_dict(self) -> dict:
        return asdict(self)


# ---------------------------------------------------------------------------
# Growth guard (FR-078)
# ---------------------------------------------------------------------------


@dataclass
class GrowthVerdict:
    allowed: bool
    reason: str = ""
    spent: int = 0
    ceiling: int = 0


class GrowthGuard:
    """Detects runaway token growth per objective.

    Three independent signals, each measured, none guessed:

      - ceiling:   total input+output tokens spent on this objective through
                   the gateway would exceed the class ceiling.
      - growth:    the input size of consecutive calls keeps growing
                   geometrically (each >= `growth_factor` x the previous)
                   for `growth_streak` calls - the signature of context
                   accumulated instead of compiled.
      - repeat:    the same context fingerprint is submitted more than
                   `max_repeats` times - a retry loop replaying itself.
    """

    def __init__(self, *, growth_factor: float = 1.5, growth_streak: int = 3,
                 max_repeats: int = 3, max_objectives: int = 512) -> None:
        self.growth_factor = growth_factor
        self.growth_streak = growth_streak
        self.max_repeats = max_repeats
        #: Objectives tracked at once. The guard's memory is per objective
        #: and a control plane creates objectives for as long as it runs;
        #: unbounded, this grew with every objective ever seen (A-051
        #: soak: RSS creep). Oldest objective forgotten first - its budget
        #: truth lives in the durable ledger, not here.
        self.max_objectives = max_objectives
        self._lock = threading.Lock()
        self._spent: dict[str, int] = {}
        self._sizes: dict[str, list[int]] = {}
        self._fingerprints: dict[str, dict[str, int]] = {}

    @staticmethod
    def fingerprint(messages: list[dict]) -> str:
        import hashlib
        blob = json.dumps(messages, sort_keys=True, default=str).encode("utf-8")
        return hashlib.blake2b(blob, digest_size=12).hexdigest()

    def check(self, objective_id: str, *, input_tokens: int, ceiling: int,
              fingerprint: str) -> GrowthVerdict:
        with self._lock:
            spent = self._spent.get(objective_id, 0)
            if ceiling and spent + input_tokens > ceiling:
                return GrowthVerdict(False, "objective token ceiling reached",
                                     spent, ceiling)
            sizes = self._sizes.get(objective_id, [])
            if len(sizes) >= self.growth_streak - 1:
                recent = sizes[-(self.growth_streak - 1):] + [input_tokens]
                geometric = all(
                    recent[i + 1] >= recent[i] * self.growth_factor
                    for i in range(len(recent) - 1))
                if geometric and recent[0] > 0:
                    return GrowthVerdict(
                        False, "context growing geometrically across calls "
                               f"({' -> '.join(str(s) for s in recent)})",
                        spent, ceiling)
            seen = self._fingerprints.get(objective_id, {}).get(fingerprint, 0)
            if seen >= self.max_repeats:
                return GrowthVerdict(
                    False, f"identical context submitted {seen} times",
                    spent, ceiling)
            return GrowthVerdict(True, "", spent, ceiling)

    def record(self, objective_id: str, *, input_tokens: int,
               output_tokens: int, fingerprint: str) -> None:
        with self._lock:
            self._spent[objective_id] = (self._spent.get(objective_id, 0)
                                         + input_tokens + output_tokens)
            sizes = self._sizes.setdefault(objective_id, [])
            sizes.append(input_tokens)
            # Only the last `growth_streak` sizes take part in the verdict.
            del sizes[:-max(self.growth_streak, 1)]
            fps = self._fingerprints.setdefault(objective_id, {})
            fps[fingerprint] = fps.get(fingerprint, 0) + 1
            while len(self._spent) > self.max_objectives:
                oldest = next(iter(self._spent))
                self._spent.pop(oldest, None)
                self._sizes.pop(oldest, None)
                self._fingerprints.pop(oldest, None)

    def spent(self, objective_id: str) -> int:
        with self._lock:
            return self._spent.get(objective_id, 0)

    def forget(self, objective_id: str) -> None:
        with self._lock:
            self._spent.pop(objective_id, None)
            self._sizes.pop(objective_id, None)
            self._fingerprints.pop(objective_id, None)


# ---------------------------------------------------------------------------
# Telemetry (FR-080) - a table in Friday's store, written by the gateway
# ---------------------------------------------------------------------------

GATEWAY_CALLS_SCHEMA = """
CREATE TABLE IF NOT EXISTS gateway_calls (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    objective_id      TEXT NOT NULL,
    worker            TEXT NOT NULL DEFAULT 'friday',
    task_class        TEXT NOT NULL,
    tier              TEXT NOT NULL DEFAULT '',
    provider          TEXT NOT NULL DEFAULT '',
    model             TEXT NOT NULL DEFAULT '',
    route_kind        TEXT NOT NULL DEFAULT '',
    status            TEXT NOT NULL,
    entitlement_state TEXT NOT NULL DEFAULT '',
    input_tokens      INTEGER NOT NULL DEFAULT 0,
    output_tokens     INTEGER NOT NULL DEFAULT 0,
    cached_tokens     INTEGER NOT NULL DEFAULT 0,
    reasoning_tokens  INTEGER NOT NULL DEFAULT 0,
    latency_ms        INTEGER NOT NULL DEFAULT 0,
    retries           INTEGER NOT NULL DEFAULT 0,
    failover_count    INTEGER NOT NULL DEFAULT 0,
    context_fingerprint TEXT NOT NULL DEFAULT '',
    error             TEXT NOT NULL DEFAULT '',
    created_at        TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_gateway_calls_obj ON gateway_calls(objective_id, created_at);
"""


class GatewayTelemetry:
    """Append-only usage ledger. Its own SQLite file by default so the
    gateway has no dependency on the objective store's connection."""

    def __init__(self, path: str | Path | None = None) -> None:
        if path is None:
            from friday.config import DATA_DIR
            path = Path(DATA_DIR) / "gateway_calls.sqlite3"
        self.path = Path(path)
        self._lock = threading.Lock()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.executescript(GATEWAY_CALLS_SCHEMA)

    def _connect(self):
        from friday.dbconn import ledger_connection
        return ledger_connection(self.path)

    def record(self, **row) -> int:
        from datetime import datetime, timezone
        # UTC-aware like every other ledger (contracts.now_iso), so a merged
        # trace (FR-054) orders gateway calls against objective events.
        row.setdefault("created_at", datetime.now(timezone.utc).isoformat(timespec="seconds"))
        cols = ", ".join(row)
        marks = ", ".join("?" for _ in row)
        with self._lock, self._connect() as conn:
            cur = conn.execute(
                f"INSERT INTO gateway_calls ({cols}) VALUES ({marks})",
                tuple(row.values()))
            return int(cur.lastrowid)

    def for_objective(self, objective_id: str) -> list[dict]:
        with self._connect() as conn:
            return [dict(r) for r in conn.execute(
                "SELECT * FROM gateway_calls WHERE objective_id=? ORDER BY id",
                (objective_id,))]

    def recent(self, limit: int = 50) -> list[dict]:
        with self._connect() as conn:
            return [dict(r) for r in conn.execute(
                "SELECT * FROM gateway_calls ORDER BY id DESC LIMIT ?",
                (limit,))]

    def summary(self, objective_id: str) -> dict:
        """FR-055: calls/tokens per objective and per worker."""
        rows = self.for_objective(objective_id)
        by_worker: dict[str, dict] = {}
        for r in rows:
            w = by_worker.setdefault(r["worker"], {"calls": 0, "input_tokens": 0,
                                                    "output_tokens": 0,
                                                    "failures": 0})
            w["calls"] += 1
            w["input_tokens"] += r["input_tokens"]
            w["output_tokens"] += r["output_tokens"]
            if r["status"] != "ok":
                w["failures"] += 1
        return {"objective_id": objective_id, "calls": len(rows),
                "input_tokens": sum(r["input_tokens"] for r in rows),
                "output_tokens": sum(r["output_tokens"] for r in rows),
                "by_worker": by_worker}

    def spikes(self, *, limit: int = 5) -> list[dict]:
        """The calls responsible for the most tokens - FR-080 acceptance:
        diagnostics identify the exact calls behind a spike."""
        with self._connect() as conn:
            return [dict(r) for r in conn.execute(
                "SELECT id, objective_id, worker, provider, model, "
                "input_tokens + output_tokens AS total_tokens, latency_ms, "
                "created_at FROM gateway_calls ORDER BY total_tokens DESC "
                "LIMIT ?", (limit,))]


# ---------------------------------------------------------------------------
# The worker process
# ---------------------------------------------------------------------------

WORKER_PATH = Path(__file__).resolve().parent / "hermes_model_gateway_worker.py"

#: Session-state variables scrubbed from the child (same list the execution
#: bridge uses, for the same measured reason: inherited session state
#: binds the child's tool leases to the parent).
_SCRUB_ENV = ("HERMES_SESSION_ID", "HERMES_AGENT", "HERMES_TURN_LEASE_TIMEOUT",
              "HERMES_KANBAN_BOARD", "HERMES_SESSION_SOURCE",
              "HERMES_TUI_SIDECAR_URL", "HERMES_INTERACTIVE",
              "HERMES_MAX_ITERATIONS", "MSYSTEM", "MSYS", "MSYS2_PATH_TYPE",
              "ORIGINAL_PATH", "SHELL", "SHLVL", "OLDPWD", "PS1")


class GatewayUnavailable(RuntimeError):
    """Hermes could not be located or the worker could not start."""


class ModelGatewayWorker:
    """One long-lived worker subprocess, JSON lines over stdio, one request
    at a time (a lock serialises callers; the worker is single-threaded by
    design so a call's usage is attributable without ambiguity)."""

    START_TIMEOUT = 30.0

    def __init__(self, *, command: list[str] | None = None,
                 cwd: str | None = None, profile: str = "") -> None:
        self._command = command
        self._cwd = cwd
        self.profile = profile
        self._proc: subprocess.Popen | None = None
        self._lock = threading.Lock()
        self._next_id = 0
        self.started_at: float | None = None
        self.hermes_home: str = ""

    # -- lifecycle ----------------------------------------------------------

    def _launch_plan(self) -> tuple[list[str], str | None, dict]:
        from friday import hermes_bridge as hb
        env = {k: v for k, v in os.environ.items() if k not in _SCRUB_ENV}
        env["PYTHONUNBUFFERED"] = "1"
        if self._command:
            return list(self._command), self._cwd, env
        found = hb.locate()
        if not found:
            raise GatewayUnavailable(
                "Hermes is not installed here (checked HERMES_PYTHON/HERMES_DIR, then PATH)")
        profile = self.profile or os.getenv(hb.ENV_PROFILE, hb.DEFAULT_PROFILE)
        home = hb.profile_home(profile) if profile else ""
        if profile and not home:
            raise GatewayUnavailable(
                f"Hermes profile {profile!r} does not exist; create it with: "
                f"hermes profile create {profile} --clone")
        if home:
            env["HERMES_HOME"] = home
            self.hermes_home = home
        return [found["python"], str(WORKER_PATH)], found["root"], env

    def start(self) -> None:
        if self.alive():
            return
        command, cwd, env = self._launch_plan()
        try:
            self._proc = subprocess.Popen(
                command, cwd=cwd, env=env, stdin=subprocess.PIPE,
                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True,
                encoding="utf-8", errors="replace", bufsize=1)
        except OSError as exc:
            raise GatewayUnavailable(f"could not start gateway worker: {exc}") from exc
        self.started_at = time.time()
        hello = self._request("hello", {}, timeout=self.START_TIMEOUT)
        if not hello.get("ok"):
            self.stop()
            raise GatewayUnavailable(f"gateway worker hello failed: {hello}")
        self.hermes_home = hello["result"].get("hermes_home", self.hermes_home)

    def alive(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    def stop(self) -> None:
        proc, self._proc = self._proc, None
        if proc is None:
            return
        try:
            if proc.poll() is None:
                try:
                    proc.stdin.write(json.dumps({"id": 0, "method": "shutdown"}) + "\n")
                    proc.stdin.flush()
                except (OSError, ValueError):
                    pass
                try:
                    proc.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    proc.kill()
        finally:
            for stream in (proc.stdin, proc.stdout):
                try:
                    stream.close()
                except (OSError, ValueError, AttributeError):
                    pass

    # -- wire ---------------------------------------------------------------

    def _request(self, method: str, params: dict, *, timeout: float) -> dict:
        with self._lock:
            if not self.alive():
                raise GatewayUnavailable("gateway worker is not running")
            self._next_id += 1
            rid = self._next_id
            frame = json.dumps({"id": rid, "method": method, "params": params},
                               default=str)
            try:
                self._proc.stdin.write(frame + "\n")
                self._proc.stdin.flush()
            except (OSError, ValueError) as exc:
                self.stop()
                raise GatewayUnavailable(f"gateway worker pipe broke: {exc}") from exc
            # One outstanding request at a time, so a blocking readline with
            # a watchdog is enough; the worker itself enforces the provider
            # timeout, and this is the backstop for a wedged worker.
            result: dict = {}
            done = threading.Event()

            def _read() -> None:
                try:
                    line = self._proc.stdout.readline()
                    result["line"] = line
                finally:
                    done.set()

            t = threading.Thread(target=_read, daemon=True, name="gateway-read")
            t.start()
            if not done.wait(timeout + 5.0):
                self.stop()
                raise GatewayUnavailable(
                    f"gateway worker did not answer {method} within {timeout + 5:.0f}s")
            line = result.get("line") or ""
            if not line:
                self.stop()
                raise GatewayUnavailable("gateway worker exited")
            try:
                reply = json.loads(line)
            except ValueError as exc:
                raise GatewayUnavailable(f"gateway worker sent non-JSON: {line[:200]}") from exc
            if reply.get("id") not in (rid, None):
                raise GatewayUnavailable("gateway worker answered out of order")
            return reply

    def call(self, method: str, params: dict | None = None, *,
             timeout: float = 60.0) -> dict:
        self.start()
        return self._request(method, params or {}, timeout=timeout)


# ---------------------------------------------------------------------------
# The gateway itself
# ---------------------------------------------------------------------------

#: How long a failing provider route is remembered as unhealthy, in
#: seconds. Short by design: an entitlement failure is durable (it is also
#: written to provider_cooldowns), a transient one is not.
ROUTE_UNHEALTHY_S = 120.0
#: Floor for the one widened retry after a thinking model spends the whole
#: output budget on reasoning. gemini-3.6-flash needed >16 and answered at
#: 256; four times a TRIVIAL budget (256) is 1024, well under any class cap.
EMPTY_RETRY_MIN_OUTPUT_TOKENS = 1024

#: Failure codes after which trying ANOTHER route is pointless.
_NO_FAILOVER = frozenset({"BAD_REQUEST", "UNKNOWN_METHOD"})


class ModelGateway:
    """FRIDAY -> Hermes MODEL_GATEWAY. One instance per process."""

    def __init__(self, *, worker: ModelGatewayWorker | None = None,
                 telemetry: GatewayTelemetry | None = None,
                 guard: GrowthGuard | None = None,
                 tier_table: dict[str, tuple[str, str]] | None = None,
                 max_failover: int = 2,
                 objective_store=None) -> None:
        self.worker = worker or ModelGatewayWorker()
        self.telemetry = telemetry or GatewayTelemetry()
        self.guard = guard or GrowthGuard()
        self.max_failover = max_failover
        self._tier_table = tier_table
        self._unhealthy: dict[tuple[str, str], float] = {}
        self._providers_cache: tuple[float, dict] | None = None
        self._lock = threading.Lock()
        #: Where an objective's DURABLE token ceiling is read from before any
        #: provider is touched (invariant A-048 "budget"). The GrowthGuard
        #: above is per-process memory against the CLASS ceiling; it forgets
        #: on restart and knows nothing of the objective's own
        #: `cost_budget_tokens`. This does: the same `objective_budget.check`
        #: the driver uses, over the same ledger rows, so a call that does
        #: not go through the driver - a tool, the deliberation panel, a
        #: probe with the objective's id - cannot spend past a budget the
        #: driver would have parked the run on. None = no objective store
        #: (unit tests of the gateway alone); the ledger rows still count.
        self._objective_store = objective_store

    # -- discovery (FR-071 / FR-072) ---------------------------------------

    def providers(self, *, max_age_s: float = 60.0) -> dict:
        """What Hermes can broker right now. Cached briefly; a change in
        Hermes config or auth is visible on the next refresh without a
        Friday code change."""
        now = time.time()
        with self._lock:
            cached = self._providers_cache
            if cached and now - cached[0] < max_age_s:
                return cached[1]
        reply = self.worker.call("providers", timeout=30.0)
        if not reply.get("ok"):
            raise GatewayUnavailable(f"provider discovery failed: {reply.get('error')}")
        inventory = reply["result"]
        inventory["queried_at"] = now
        # Only authenticated routes are advertised as usable; the rest are
        # listed as configurable so the UI can be truthful about both.
        inventory["usable"] = [p["id"] for p in inventory.get("providers", [])
                               if p.get("authenticated")]
        with self._lock:
            self._providers_cache = (now, inventory)
        return inventory

    def route_kind(self, provider: str) -> str:
        try:
            inventory = self.providers()
        except GatewayUnavailable:
            return ""
        for p in inventory.get("providers", []):
            if p.get("id") == provider:
                return p.get("route_kind", "")
        return ""

    # -- routing (FR-075) ---------------------------------------------------

    def tier_table(self) -> dict[str, tuple[str, str]]:
        """tier -> (provider, model). The friday profile's `routing.tiers`
        wins where it names a tier; otherwise the profile main model is
        `deep` and the economy defaults from `execution_economics` fill
        `fast`/`standard`. "" model means the profile default."""
        if self._tier_table is not None:
            return self._tier_table
        from friday import execution_economics as ee
        table = ee._tier_table()   # noqa: SLF001 - one table, one owner
        main_provider = ""
        try:
            main = self.providers().get("main") or {}
            main_provider = str(main.get("provider") or "")
        except GatewayUnavailable:
            pass
        out = {
            TIER_FAST: (main_provider, table.get(ee.TIER_ECONOMY, "")),
            TIER_STANDARD: (main_provider, table.get(ee.TIER_STANDARD, "")),
            TIER_DEEP: (main_provider, table.get(ee.TIER_DEEP, "")),
        }
        self._tier_table = out
        return out

    def candidates(self, request: ModelGatewayRequest) -> list[tuple[str, str, str]]:
        """Ordered (tier, provider, model) routes to try. The preferred tier
        first, then the stronger tiers as failover, filtered by allow/deny
        lists, cooldowns and recent health."""
        budget = budget_for(request.task_class)
        tier = request.preferred_quality_tier or budget.default_tier
        if tier not in TIERS:
            raise ValueError(f"unknown quality tier {tier!r}; known: {TIERS}")
        order = [tier] + [t for t in (TIER_FAST, TIER_STANDARD, TIER_DEEP) if t != tier]
        table = self.tier_table()
        try:
            from friday import provider_cooldowns
            cooling = provider_cooldowns.active()
        except Exception:  # noqa: BLE001 - cooldown file trouble never blocks routing
            cooling = {}
        now = time.time()
        seen: set[tuple[str, str]] = set()
        out: list[tuple[str, str, str]] = []
        #: Providers the tier table named, usable or not. A provider whose
        #: table route just failed is not re-offered as a "beyond the table"
        #: fallback of itself at a different model name - unhealthiness is
        #: keyed (provider, model), and that loophole retried the same dead
        #: provider under its catalog default (test_provider_health found it).
        tabled: set[str] = set()
        for t in order:
            provider, model = table.get(t, ("", ""))
            key = (provider, model)
            if provider:
                tabled.add(provider)
            if key in seen:
                continue
            seen.add(key)
            if request.provider_allowlist and provider not in request.provider_allowlist:
                continue
            if provider in request.provider_denylist:
                continue
            if request.privacy_policy == "local_only" and self.route_kind(provider) != "local":
                continue
            if (provider, model) in cooling:
                continue
            until = self._unhealthy.get(key, 0.0)
            if until > now:
                continue
            out.append((t, provider, model))

        # Routes beyond the tier table: every provider Hermes reports as
        # authenticated, at ITS OWN default model. They are reached when the
        # caller pins one (`provider_allowlist`) or when failover is allowed
        # and the tier table gave nothing usable - a second authenticated
        # provider is a real fallback, an unauthenticated one is not (the
        # live suite found the allowlist path returned NO_ROUTE for every
        # provider but the main one, 2026-09-05).
        #
        # Requirement 10: the model is resolved HERE, from the provider's
        # catalog, never left blank. A blank model reaches Hermes's
        # `resolve_provider_client`, which pre-fills the profile's MAIN model
        # - and that is how openai-api was asked for `claude-opus-5`. A
        # provider with no default of its own is not a route.
        wants_more = bool(request.provider_allowlist) or (request.allow_failover and not out)
        if wants_more:
            verdicts = self.provider_health(self._authenticated_providers())
            for provider in self._authenticated_providers():
                if provider in tabled and not request.provider_allowlist:
                    continue
                model = self.default_model(provider)
                if not model:
                    logger.info("no catalog default model for %s; not a route", provider)
                    continue
                # Requirement 9: a route whose last evidence is a durable
                # failure (auth, credits, unsupported model) is not tried
                # again until a probe says otherwise. Degraded and stale
                # routes ARE tried - that is how they get fresh evidence.
                verdict = verdicts.get(provider)
                if verdict is not None and verdict.state == provider_health.UNAVAILABLE \
                        and not request.provider_allowlist:
                    logger.info("skipping %s: %s", provider, verdict.reason)
                    continue
                key = (provider, model)
                if key in seen or any(p == provider for _, p, _ in out):
                    continue
                seen.add(key)
                if request.provider_allowlist and provider not in request.provider_allowlist:
                    continue
                if provider in request.provider_denylist:
                    continue
                if request.privacy_policy == "local_only" and self.route_kind(provider) != "local":
                    continue
                if key in cooling or self._unhealthy.get(key, 0.0) > now:
                    continue
                out.append((tier, provider, model))
        return out

    def _authenticated_providers(self) -> list[str]:
        try:
            return list(self.providers().get("usable") or [])
        except GatewayUnavailable:
            return []

    def _durable_budget_verdict(self, objective_id: str):
        """`objective_budget.check` for this objective over the durable
        ledgers, or None when there is no objective store to read the
        budget from / the id names no run. The store is opened lazily and
        an unreadable one is a debug line, never a refusal: a budget that
        cannot be read is not exhausted, it is unknown, and the class
        ceiling above still applies."""
        if not objective_id:
            return None
        store = self._objective_store
        if store is None:
            try:
                # The process's one store handle - the same one the driver
                # and every tool use, so the budget read here is the budget
                # they see (ADA_DB honoured; never a second connection).
                from friday.toolsets.memory import store as _store
                store = self._objective_store = _store()
            except Exception:  # noqa: BLE001 - see docstring
                logger.debug("gateway: objective store unavailable for budget", exc_info=True)
                return None
        try:
            if store.objective_run(objective_id) is None:
                return None
            from friday import objective_budget
            return objective_budget.check(store, objective_id, telemetry=self.telemetry)
        except Exception:  # noqa: BLE001
            logger.debug("gateway: objective budget check failed", exc_info=True)
            return None

    def default_model(self, provider: str) -> str:
        """The provider's own catalog default as Hermes reports it, or "".

        Never the profile main model: `main.default` belongs to
        `main.provider` alone. The main provider's own tier-table entry uses
        "" deliberately (Hermes resolves that to the configured main model,
        which IS that provider's).
        """
        try:
            inventory = self.providers()
        except GatewayUnavailable:
            return ""
        for p in inventory.get("providers", []):
            if p.get("id") == provider:
                return str(p.get("default_model") or "").strip()
        return ""

    # -- the call (FR-070 / FR-076 / FR-077 / FR-078 / FR-080 / FR-081) ----

    def infer(self, request: ModelGatewayRequest) -> ModelGatewayResult:
        budget = budget_for(request.task_class)
        max_in = request.max_input_tokens or budget.max_input_tokens
        max_out = request.max_output_tokens or budget.max_output_tokens
        input_tokens = request.input_tokens()
        fingerprint = GrowthGuard.fingerprint(request.context_package)

        # FR-077: over-budget context needs explicit escalation.
        if input_tokens > max_in and not request.escalate:
            self._record(request, status="refused", error="budget",
                         input_tokens=input_tokens, fingerprint=fingerprint)
            raise BudgetExceeded(
                f"compiled context is ~{input_tokens} tokens; the {request.task_class} "
                f"budget is {max_in}. Pass escalate=True to spend it deliberately.")

        # FR-078: the growth guard runs before any provider is touched.
        verdict = self.guard.check(request.objective_id, input_tokens=input_tokens,
                                   ceiling=budget.objective_ceiling,
                                   fingerprint=fingerprint)
        if not verdict.allowed:
            self._record(request, status="refused", error=f"growth:{verdict.reason}",
                         input_tokens=input_tokens, fingerprint=fingerprint)
            raise GrowthStopped(
                f"token growth guard stopped objective {request.objective_id}: "
                f"{verdict.reason} (spent {verdict.spent} of {verdict.ceiling})")

        # A-048 budget invariant: the objective's OWN durable ceiling, from
        # recorded spend, before any provider is touched - on every path,
        # not only the driver's. Exhausted means refused here, with the
        # dimension and the numbers, until the budget itself changes.
        durable = self._durable_budget_verdict(request.objective_id)
        if durable is not None and not durable.allowed:
            self._record(request, status="refused",
                         error=f"budget:{durable.dimension}:{durable.reason}",
                         input_tokens=input_tokens, fingerprint=fingerprint,
                         entitlement_state="BUDGET_EXHAUSTED")
            raise BudgetExceeded(
                f"objective {request.objective_id} budget exhausted "
                f"({durable.dimension}): {durable.reason}. Raise the budget "
                "or resume the run deliberately; no provider was called.")

        routes = self.candidates(request)
        if not routes:
            self._record(request, status="failed", error="no eligible route",
                         input_tokens=input_tokens, fingerprint=fingerprint,
                         entitlement_state="NO_ROUTE")
            return ModelGatewayResult(status="failed", entitlement_state="NO_ROUTE",
                                      warnings=["no eligible provider route"],
                                      input_tokens=input_tokens)
        if not request.allow_failover:
            routes = routes[:1]
        else:
            routes = routes[: 1 + self.max_failover]

        attempts: list[dict] = []
        last_error = ""
        last_state = ""
        for index, (tier, provider, model) in enumerate(routes):
            params = {
                "messages": request.context_package,
                "provider": provider, "model": model,
                "max_output_tokens": max_out,
                "temperature": request.temperature,
                "timeout_s": request.timeout_s,
            }
            reasoning = request.reasoning_budget or budget.reasoning
            if reasoning and reasoning != "none":
                params["reasoning"] = {"effort": reasoning}
            started = time.monotonic()
            try:
                reply = self.worker.call("infer", params, timeout=request.timeout_s)
            except GatewayUnavailable as exc:
                elapsed = int((time.monotonic() - started) * 1000)
                attempts.append({"tier": tier, "provider": provider, "model": model,
                                 "status": "unavailable", "error": str(exc),
                                 "latency_ms": elapsed})
                last_error, last_state = str(exc), "GATEWAY_UNAVAILABLE"
                self._record(request, status="failed", provider=provider, model=model,
                             tier=tier, error=last_error, latency_ms=elapsed,
                             input_tokens=input_tokens, failover_count=index,
                             fingerprint=fingerprint, entitlement_state=last_state)
                continue
            elapsed = int((time.monotonic() - started) * 1000)
            if reply.get("ok"):
                res = reply["result"]
                usage = res.get("usage") or {}
                kind = self.route_kind(res.get("provider") or provider)
                text = res.get("response", "") or ""
                finish = str(res.get("finish_reason") or "")
                if not text.strip():
                    # Transport success is not an answer. A structurally
                    # valid reply with no visible content is a FAILED
                    # attempt, classified by why (Requirement 9/11):
                    #   OUTPUT_TRUNCATED  finish_reason=length - a thinking
                    #                     model spent max_output_tokens on
                    #                     reasoning (gemini-3.6-flash: 9 in,
                    #                     0 out, 22 total at max_tokens=16,
                    #                     2026-09-05); retry once, larger.
                    #   EMPTY_RESPONSE    anything else - the route is not
                    #                     healthy, say so, fail over.
                    hidden = int(usage.get("reasoning_tokens") or 0)
                    if finish in ("length", "max_tokens") and not params.get("_retried"):
                        widened = max(int(max_out) * 4, EMPTY_RETRY_MIN_OUTPUT_TOKENS)
                        code = "OUTPUT_TRUNCATED"
                        message = (f"{finish}: no visible content within {max_out} output tokens"
                                   f" ({hidden} spent on reasoning); retrying once at {widened}")
                        attempts.append({"tier": tier, "provider": provider, "model": model,
                                         "status": "failed", "code": code, "error": message,
                                         "latency_ms": elapsed})
                        self._record(request, status="failed", provider=provider, model=model,
                                     tier=tier, error=f"{code}: {message}", latency_ms=elapsed,
                                     input_tokens=input_tokens, failover_count=index,
                                     fingerprint=fingerprint, entitlement_state=code)
                        params = {**params, "max_output_tokens": widened, "_retried": True}
                        started = time.monotonic()
                        try:
                            reply = self.worker.call("infer", params, timeout=request.timeout_s)
                        except GatewayUnavailable as exc:
                            last_error, last_state = str(exc), "GATEWAY_UNAVAILABLE"
                            continue
                        elapsed = int((time.monotonic() - started) * 1000)
                        if not reply.get("ok"):
                            err = reply.get("error") or {}
                            last_error = str(err.get("message") or "")[:500]
                            last_state = str(err.get("code") or "PROVIDER_ERROR")
                            attempts.append({"tier": tier, "provider": provider, "model": model,
                                             "status": "failed", "code": last_state,
                                             "error": last_error, "latency_ms": elapsed})
                            self._mark_unhealthy(provider, model, last_state)
                            continue
                        res = reply["result"]
                        usage = res.get("usage") or {}
                        text = res.get("response", "") or ""
                        finish = str(res.get("finish_reason") or "")
                        hidden = int(usage.get("reasoning_tokens") or 0)
                    if not text.strip():
                        code = "OUTPUT_TRUNCATED" if finish in ("length", "max_tokens") else "EMPTY_RESPONSE"
                        message = (f"{provider} {res.get('model') or model} returned no visible content"
                                   f" (finish_reason={finish or 'unknown'},"
                                   f" output_tokens={int(usage.get('output_tokens') or 0)},"
                                   f" reasoning_tokens={hidden})")
                        attempts.append({"tier": tier, "provider": provider, "model": model,
                                         "status": "failed", "code": code, "error": message,
                                         "latency_ms": elapsed})
                        last_error, last_state = message, code
                        self._record(request, status="failed", provider=provider, model=model,
                                     tier=tier, error=f"{code}: {message}", latency_ms=elapsed,
                                     input_tokens=input_tokens, failover_count=index,
                                     fingerprint=fingerprint, entitlement_state=code)
                        self._mark_unhealthy(provider, model, code)
                        continue
                result = ModelGatewayResult(
                    status="ok", provider=res.get("provider") or provider,
                    model=res.get("model") or model, response=text,
                    input_tokens=int(usage.get("input_tokens") or 0),
                    output_tokens=int(usage.get("output_tokens") or 0),
                    cached_tokens=int(usage.get("cached_tokens") or 0),
                    reasoning_tokens=int(usage.get("reasoning_tokens") or 0),
                    latency_ms=int(res.get("latency_ms") or elapsed),
                    failover_count=index, entitlement_state="OK",
                    route_kind=kind, boundary="local" if kind == "local" else "upstream_cloud",
                    attempts=attempts)
                if res.get("finish_reason") in ("length", "max_tokens"):
                    result.warnings.append("output truncated at max_output_tokens")
                if model and result.model and result.model != model:
                    result.warnings.append(f"requested {model}, served {result.model}")
                self.guard.record(request.objective_id,
                                  input_tokens=result.input_tokens or input_tokens,
                                  output_tokens=result.output_tokens,
                                  fingerprint=fingerprint)
                result.call_id = self._record(
                    request, status="ok", provider=result.provider, model=result.model,
                    tier=tier, latency_ms=result.latency_ms,
                    input_tokens=result.input_tokens, output_tokens=result.output_tokens,
                    cached_tokens=result.cached_tokens,
                    reasoning_tokens=result.reasoning_tokens, failover_count=index,
                    fingerprint=fingerprint, entitlement_state="OK", route_kind=kind)
                return result
            err = reply.get("error") or {}
            code = str(err.get("code") or "PROVIDER_ERROR")
            message = str(err.get("message") or "")[:500]
            attempts.append({"tier": tier, "provider": provider, "model": model,
                             "status": "failed", "code": code, "error": message,
                             "latency_ms": elapsed})
            last_error, last_state = message, code
            self._record(request, status="failed", provider=provider, model=model,
                         tier=tier, error=f"{code}: {message}", latency_ms=elapsed,
                         input_tokens=input_tokens, failover_count=index,
                         fingerprint=fingerprint, entitlement_state=code)
            self._mark_unhealthy(provider, model, code)
            if code in _NO_FAILOVER:
                break
        return ModelGatewayResult(
            status="failed", entitlement_state=last_state or "PROVIDER_ERROR",
            warnings=[last_error] if last_error else [], attempts=attempts,
            failover_count=max(0, len(attempts) - 1), input_tokens=input_tokens)

    # -- helpers ------------------------------------------------------------

    def _mark_unhealthy(self, provider: str, model: str, code: str) -> None:
        self._unhealthy[(provider, model)] = time.time() + ROUTE_UNHEALTHY_S
        if code in ("QUOTA_EXCEEDED", "RATE_LIMITED", "INSUFFICIENT_CREDIT"):
            try:
                from datetime import datetime, timedelta
                from friday import provider_cooldowns
                provider_cooldowns.mark(
                    provider, model,
                    (datetime.now() + timedelta(minutes=10)).isoformat(timespec="seconds"),
                    reason=f"gateway:{code}")
            except Exception:  # noqa: BLE001 - a cooldown write failing is not a routing failure
                logger.debug("cooldown mark failed", exc_info=True)

    def _record(self, request: ModelGatewayRequest, *, status: str,
                provider: str = "", model: str = "", tier: str = "",
                error: str = "", latency_ms: int = 0, input_tokens: int = 0,
                output_tokens: int = 0, cached_tokens: int = 0,
                reasoning_tokens: int = 0, failover_count: int = 0,
                fingerprint: str = "", entitlement_state: str = "",
                route_kind: str = "") -> int:
        try:
            return self.telemetry.record(
                objective_id=request.objective_id, worker=request.worker,
                task_class=request.task_class,
                tier=tier or request.preferred_quality_tier or "",
                provider=provider, model=model, route_kind=route_kind,
                status=status, entitlement_state=entitlement_state,
                input_tokens=input_tokens, output_tokens=output_tokens,
                cached_tokens=cached_tokens, reasoning_tokens=reasoning_tokens,
                latency_ms=latency_ms, retries=0, failover_count=failover_count,
                context_fingerprint=fingerprint, error=error[:500])
        except Exception:  # noqa: BLE001 - telemetry must never break inference
            logger.warning("gateway telemetry write failed", exc_info=True)
            return 0

    def health(self) -> dict:
        """FR-026-style state for the gateway as a capability.

        Two lists, deliberately distinct (Requirement 9): `usable` is what
        Hermes reports as authenticated - a credential exists - and
        `providers` carries each one's EVIDENCE-based state from the call
        ledger (`friday.provider_health`). `healthy` is the subset that has
        answered with content recently; `unavailable` the subset whose last
        evidence is a durable failure with the reason attached. Nothing is
        called healthy for having a key.
        """
        try:
            inventory = self.providers()
        except GatewayUnavailable as exc:
            return {"state": "UNAVAILABLE", "detail": str(exc), "usable": [],
                    "healthy": [], "unavailable": [], "providers": {}}
        usable = list(inventory.get("usable", []))
        state = "READY" if usable else "AUTH_REQUIRED"
        verdicts = self.provider_health(usable)
        return {"state": state, "usable": usable, "main": inventory.get("main"),
                "healthy": [p for p, v in verdicts.items() if v.state == provider_health.HEALTHY],
                "unavailable": [p for p, v in verdicts.items() if v.state == provider_health.UNAVAILABLE],
                "providers": {p: v.to_dict() for p, v in verdicts.items()},
                "hermes_home": self.worker.hermes_home,
                "worker_alive": self.worker.alive(),
                "boundary_note": "upstream providers see the compiled context; "
                                 "Hermes brokers credentials, it does not make "
                                 "cloud inference local."}

    def provider_health(self, providers: list[str] | None = None,
                        *, max_age_s: float = provider_health.DEFAULT_MAX_AGE_S) -> dict:
        """provider -> `provider_health.Verdict`, from the ledger only."""
        if providers is None:
            providers = self._authenticated_providers()
        return provider_health.assess(self.telemetry, list(providers), max_age_s=max_age_s)

    def probe(self, provider: str, **kw) -> "provider_health.Verdict":
        """Refresh one provider's evidence with one tiny paid call. Explicit
        by design; see `provider_health.probe`."""
        return provider_health.probe(self, provider, **kw)

    def close(self) -> None:
        self.worker.stop()


# ---------------------------------------------------------------------------
# Process-wide instance
# ---------------------------------------------------------------------------

_GATEWAY: ModelGateway | None = None
_GATEWAY_LOCK = threading.Lock()


def gateway() -> ModelGateway:
    global _GATEWAY
    with _GATEWAY_LOCK:
        if _GATEWAY is None:
            _GATEWAY = ModelGateway()
        return _GATEWAY


def compile_context(*, system: str = "", user: str, history: list[tuple[str, str]] = (),
                    memory: str = "", max_history_turns: int = 6) -> list[dict]:
    """The one place a bounded context package is assembled for the gateway.

    Order: system persona, at most `max_history_turns` prior turns, the
    task-scoped memory slice (as part of the user message, so it cannot be
    mistaken for an instruction), then the request. No caller passes a
    whole store; `memory` is expected to come from
    `friday.memory_stack.aggregate(task, budget_tokens=...)`.
    """
    messages: list[dict] = []
    if system.strip():
        messages.append({"role": "system", "content": system.strip()})
    for role, body in list(history)[-max_history_turns:]:
        messages.append({"role": "assistant" if role in ("assistant", "model") else "user",
                         "content": str(body)})
    body = user if not memory.strip() else f"{user}\n\n(from your memory: {memory.strip()})"
    messages.append({"role": "user", "content": body})
    return messages
