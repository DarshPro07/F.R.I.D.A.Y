"""
A-051 soak: a workload-driven endurance run against the REAL control plane.

Not "sleep for eight hours". Every cycle drives the production code paths a
day of use would - objectives created and completed, provider success and
failure through the real ModelGateway, budget pause/resume, the Hermes
supervisor started/stopped/crashed/restarted, DB writes, the scheduler's
at-most-once claim, remote-nonce traffic, context handoffs through the
promotion gate, and a worker process killed mid-task and recovered by the
watchdog - while sampling the process every few seconds:

    rss_mb, cpu_pct, handles (Windows) / fds, threads, children,
    sqlite_conns (open .sqlite3 handles), locks (store._lock waits),
    tokens_per_hour, provider_calls_per_hour, log_lines_per_hour,
    queue_depth (non-terminal objective runs)

Providers are the scripted fake worker (`tests/fake_model_gateway_worker.py`)
and the scripted Hermes gateway (`tests/fake_hermes_gateway.py`): the REAL
transports, subprocess lifecycles, JSON-lines protocols, retry and failover
logic run; only the model behind them is faked. No network, no spend.

Verdict: hour-1 vs final-hour comparison on every sampled series. A series
that grows monotonically across the run (rss, handles, threads, children,
sqlite_conns) fails the soak; rates are reported. The report is JSON +
a one-page markdown, reproducible from the run id.

    python scripts/soak.py --hours 8            # the PRD gate
    python scripts/soak.py --minutes 3          # a smoke pass (CI uses this)
    python scripts/soak.py --hours 8 --out data/soak/<name>
    python scripts/soak.py --hours 8 --governor relaxed   # loaded host; named in the report

Verdicts: PASS (>= 30 min, no monotonic growth on any hard series, every
cycle's invariants held, every workload move ran) / SMOKE (shorter than
30 min: workload and invariants judged, growth only reported) / INCOMPLETE
(a promised move never ran - e.g. the governor shed every worker on a
loaded host) / FAIL. Exit 0 only for PASS and SMOKE. Read the report, not
the exit code alone.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import secrets
import statistics
import subprocess
import sys
import textwrap
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import psutil  # noqa: E402

FAKE_GW = str(ROOT / "tests" / "fake_model_gateway_worker.py")
FAKE_HERMES = str(ROOT / "tests" / "fake_hermes_gateway.py")

#: Series where ANY monotonic growth across the run is a leak.
HARD_SERIES = ("rss_mb", "handles", "threads", "children", "sqlite_conns")
#: Below this many seconds growth is reported but not judged (SMOKE).
MIN_JUDGED_S = 1800.0
#: Series reported as rates; growth is information, not failure.
RATE_SERIES = ("tokens", "provider_calls", "log_lines", "queue_depth", "cpu_pct",
               "host_ram_pct", "host_cpu_pct")


# ---------------------------------------------------------------------------
# sampling
# ---------------------------------------------------------------------------


def sample(proc: psutil.Process, store, telemetry, log_path: Path, t0: float) -> dict:
    with proc.oneshot():
        mem = proc.memory_info()
        try:
            handles = proc.num_handles() if hasattr(proc, "num_handles") else proc.num_fds()
        except (psutil.Error, AttributeError):
            handles = -1
        threads = proc.num_threads()
        try:
            children = len(proc.children(recursive=True))
        except psutil.Error:
            children = -1
        cpu = proc.cpu_percent(interval=None)
    try:
        open_files = proc.open_files()
        sqlite_conns = sum(1 for f in open_files if f.path.endswith((".sqlite3", ".sqlite3-wal", ".sqlite3-shm")))
    except psutil.Error:
        sqlite_conns = -1
    try:
        with telemetry._connect() as conn:
            row = conn.execute(
                "SELECT COUNT(*), COALESCE(SUM(input_tokens + output_tokens), 0) FROM gateway_calls").fetchone()
        provider_calls, tokens = int(row[0]), int(row[1])
    except Exception:  # noqa: BLE001
        tokens = provider_calls = -1
    try:
        # Through the store's own lock: the Store serialises every use of
        # its one shared connection, and a sampler that bypasses that is
        # the harness racing the product (it did: one "database is locked"
        # in 1,435 cycles came from here, not from any product path).
        with store._lock:
            queue_depth = store._conn.execute(
                "SELECT COUNT(*) FROM objective_runs WHERE status NOT IN ('COMPLETED','PARTIAL','FAILED','CANCELLED')"
            ).fetchone()[0]
    except Exception:  # noqa: BLE001
        queue_depth = -1
    try:
        log_lines = sum(1 for _ in log_path.open("rb")) if log_path.exists() else 0
    except OSError:
        log_lines = -1
    vm = psutil.virtual_memory()
    return {"t": round(time.time() - t0, 1), "rss_mb": round(mem.rss / 1048576, 2),
            "host_ram_pct": vm.percent, "host_cpu_pct": psutil.cpu_percent(interval=None),
            "cpu_pct": cpu, "handles": handles, "threads": threads, "children": children,
            "sqlite_conns": sqlite_conns, "tokens": tokens, "provider_calls": provider_calls,
            "log_lines": log_lines, "queue_depth": queue_depth}


# ---------------------------------------------------------------------------
# the workload
# ---------------------------------------------------------------------------


WORKER_CHILD = textwrap.dedent(r'''
    import asyncio, json, sys, time
    from pathlib import Path
    sys.path.insert(0, sys.argv[1])
    from friday.store import Store
    from friday.continuous import ContinuousTaskExecutor
    from friday.objectives import compile_objective
    hold = Path(sys.argv[3])
    async def capability(name, arguments):
        while name == "slow" and hold.exists():
            await asyncio.sleep(0.05)
        return {"ok": True, "verification": {"method": "return_value", "evidence": "ran"}}
    async def main():
        store = Store(sys.argv[2])
        run = compile_objective(store, request="soak crash probe",
                                tasks=[{"capability": "fast", "arguments": {}},
                                       {"capability": "slow", "arguments": {}, "dependencies": ["t1"]}],
                                manifest=[{"id": "fast", "description": "f"}, {"id": "slow", "description": "s"}],
                                objective_summary="soak crash probe")
        print(json.dumps({"run_id": run["run_id"]}), flush=True)
        ex = ContinuousTaskExecutor(store, capability, executor_id="soak-worker")
        ex.lease_timeout = 1.0
        ex.stop()
        await ex.start(run["run_id"])
    asyncio.run(main())
''')


class Workload:
    """One instance owns every long-lived handle so the sampler measures
    THIS process and its children, nothing else."""

    def __init__(self, out: Path) -> None:
        from friday import model_gateway as mg
        from friday import hermes_bridge as hb
        from friday.store import Store
        from friday import provider_cooldowns as PC
        self.out = out
        self.db = out / "soak.sqlite3"
        self.store = Store(self.db)
        PC.COOLDOWNS_FILE = out / "cooldowns.json"
        self.telemetry = mg.GatewayTelemetry(out / "gateway.sqlite3")
        self.mg = mg
        self.hb = hb
        self.gateway = mg.ModelGateway(
            worker=mg.ModelGatewayWorker(command=[sys.executable, FAKE_GW], profile=""),
            telemetry=self.telemetry, objective_store=self.store,
            tier_table={mg.TIER_FAST: ("anthropic", "fake-fast"),
                        mg.TIER_STANDARD: ("anthropic", "fake-standard"),
                        mg.TIER_DEEP: ("openai-codex", "fake-deep")})
        self.worklog = hb.WorkRunLog(out / "bridge.sqlite3")
        self.supervisor = hb.HermesSupervisor(log=self.worklog, command=[sys.executable, FAKE_HERMES], profile="")
        self.supervisor.READY_TIMEOUT = 30
        self.log_path = out / "soak.log"
        self.counts: dict[str, int] = {}
        self.violations: list[str] = []
        self.manifest = [{"id": c, "description": c} for c in ("a", "b", "c")]

    def count(self, key: str, n: int = 1) -> None:
        self.counts[key] = self.counts.get(key, 0) + n

    def log(self, line: str) -> None:
        with self.log_path.open("a", encoding="utf-8") as fh:
            fh.write(f"{datetime.now(timezone.utc).isoformat(timespec='seconds')} {line}\n")

    def violate(self, what: str) -> None:
        self.violations.append(what)
        self.log(f"VIOLATION {what}")

    # -- the individual moves ------------------------------------------------

    async def objective_cycle(self) -> None:
        """Create, drive to a terminal state, and hold the completion invariant."""
        from friday import objectives as O
        from friday.continuous import ContinuousTaskExecutor

        async def capability(name, arguments):
            if name == "c":
                raise ValueError("planted failure")
            return {"ok": True, "verification": {"method": "return_value", "evidence": "ran"}}
        tasks = [{"capability": "a", "arguments": {}}, {"capability": "b", "arguments": {}, "dependencies": ["t1"]}]
        if self.counts.get("objective", 0) % 5 == 4:
            tasks.append({"capability": "c", "arguments": {}})        # one in five has a failing task
        run_id = O.compile_objective(self.store, request="soak objective", tasks=tasks,
                                     manifest=self.manifest, objective_summary="soak")["run_id"]
        ex = ContinuousTaskExecutor(self.store, capability)
        ex.stop()
        try:
            await ex.start(run_id)
            deadline = time.time() + 20
            while time.time() < deadline:
                row = self.store.objective_run(run_id)
                if row["status"] in O.RUN_TERMINAL:
                    break
                await asyncio.sleep(0.05)
            row = self.store.objective_run(run_id)
            if row["status"] not in O.RUN_TERMINAL:
                self.violate(f"objective {run_id} did not reach a terminal state in 20s ({row['status']})")
            if row["status"] == O.RUN_COMPLETED and self.store.completion_evidence_gap(run_id):
                self.violate(f"objective {run_id} COMPLETED with an evidence gap")
            self.count("objective")
            self.count(f"objective_{row['status'].lower()}")
        finally:
            ex.stop()

    def provider_cycle(self) -> None:
        """Real gateway calls: success, then a provider that fails, then failover."""
        mg = self.mg
        run_id = self._fresh_run("provider")
        res = self.gateway.infer(mg.ModelGatewayRequest(
            objective_id=run_id, task_class=mg.SIMPLE,
            context_package=mg.compile_context(system="soak", user=f"ping {secrets.token_hex(3)}")))
        self.count("provider_ok" if res.status == "ok" else "provider_fail")
        if res.status == "ok" and not res.response:
            self.violate("gateway returned ok with empty content")
        # failover: deny the default provider, expect the other route
        res2 = self.gateway.infer(mg.ModelGatewayRequest(
            objective_id=run_id, task_class=mg.SIMPLE, allow_failover=True,
            provider_denylist=("anthropic",),
            context_package=mg.compile_context(system="soak", user="ping")))
        self.count("provider_failover_ok" if res2.status == "ok" else "provider_failover_fail")

    def budget_cycle(self) -> None:
        """Exhaust an objective's budget, see the gateway refuse, raise it, see it pass."""
        mg = self.mg
        run_id = self._fresh_run("budget")
        self.store.touch_objective_run(run_id, cost_budget_tokens=5)
        self.telemetry.record(objective_id=run_id, worker="soak", task_class="SIMPLE",
                              provider="anthropic", model="fake-fast", route_kind="api",
                              status="ok", input_tokens=10, output_tokens=10, latency_ms=1)
        try:
            self.gateway.infer(mg.ModelGatewayRequest(
                objective_id=run_id, task_class=mg.SIMPLE,
                context_package=mg.compile_context(system="soak", user="ping")))
            self.violate(f"budget: exhausted objective {run_id} was allowed a provider call")
        except mg.BudgetExceeded:
            self.count("budget_refused")
        self.store.touch_objective_run(run_id, cost_budget_tokens=100000)
        res = self.gateway.infer(mg.ModelGatewayRequest(
            objective_id=run_id, task_class=mg.SIMPLE,
            context_package=mg.compile_context(system="soak", user="ping")))
        if res.status != "ok":
            self.violate(f"budget: raised budget still refused ({res.status})")
        self.count("budget_resumed")

    def hermes_cycle(self, crash: bool) -> None:
        """Delegate through the real supervisor; every Nth cycle the gateway
        dies mid-task and the supervisor must restart it. When the governor
        refuses a worker for HOST pressure (FR-056) that is the product
        doing its job on a loaded machine: counted as `hermes_shed`, and
        the report says so beside the host numbers."""
        from friday import governor as G
        hb = self.hb
        try:
            self._hermes_cycle(crash)
        except G.Refused as refused:
            self.count("hermes_shed")
            self.log(f"hermes shed: {refused}")

    def _hermes_cycle(self, crash: bool) -> None:
        hb = self.hb
        if crash:
            # The fake reads FAKE_HERMES_DIE at ITS startup: restart the
            # gateway under the flag, then the next task kills it mid-turn.
            os.environ["FAKE_HERMES_DIE"] = "1"
            try:
                self.supervisor.restart()
                self.supervisor.delegate(hb.TaskBundle(goal="doomed"))
                deadline = time.time() + 10
                while time.time() < deadline and self.supervisor.alive():
                    time.sleep(0.1)
                if self.supervisor.alive():
                    self.violate("hermes: gateway did not die when told to")
            finally:
                os.environ.pop("FAKE_HERMES_DIE", None)
            self.supervisor.restart()
            if not self.supervisor.alive():
                self.violate("hermes: restart after crash did not come back")
            self.count("hermes_crash_recovered")
        out = self.supervisor.delegate(hb.TaskBundle(goal="inspect, do not modify"), wait=True, turn_timeout=30)
        if out["result"]["status"] != hb.COMPLETE:
            self.violate(f"hermes: delegate ended {out['result']['status']}")
        self.count("hermes_delegate")

    def hermes_stop_start(self) -> None:
        self.supervisor.stop()
        if self.supervisor.alive():
            self.violate("hermes: alive after stop()")
        self.supervisor.start()
        if not self.supervisor.alive():
            self.violate("hermes: not alive after start()")
        self.count("hermes_stop_start")

    def db_cycle(self) -> None:
        for i in range(50):
            self.store.remember(f"soak subject {i}", f"value {secrets.token_hex(4)}", kind="FACT",
                                source="soak", scope="user")
        self.count("db_writes", 50)

    def scheduler_cycle(self) -> None:
        key = f"soak@{secrets.token_hex(4)}"
        if self.store.claim_automation_execution(key, "RUN-a") is not None:
            self.violate("scheduler: a fresh key was already claimed")
        self.store.start_automation_run("RUN-a-" + key, "soak", "schedule", {})
        if self.store.claim_automation_execution(key, "RUN-b") is None:
            self.violate("scheduler: the same key was claimed twice")
        self.count("scheduler_claims")

    def nonce_cycle(self) -> None:
        from friday import access
        now = time.time()
        n = "soak-" + secrets.token_urlsafe(12)
        ok, _ = access.check_replay(n, now, now=now)
        again, _ = access.check_replay(n, now, now=now)
        if not ok or again:
            self.violate(f"replay: nonce accepted {ok}, replayed {again}")
        self.count("nonces")

    def handoff_cycle(self) -> None:
        from friday import memory_promotion as MP
        c = MP.Candidate(statement=f"soak fact {secrets.token_hex(3)}: is true", kind="project_fact",
                         source="handoff:soak", owner="friday", scope="project",
                         confidence=0.8, evidence=["soak"])
        MP.promote(c, store=self.store)
        bare = MP.Candidate(statement="the deploy target is prod", kind="project_fact",
                            source="page: https://x.example", owner="friday", scope="project",
                            confidence=0.9, evidence=[])
        if MP.promote(bare, store=self.store).accepted:
            self.violate("memory: a candidate with no evidence was promoted")
        self.count("handoffs")

    def cancel_cycle(self) -> None:
        from friday import objectives as O
        from friday.continuous import ContinuousTaskExecutor
        run_id = O.compile_objective(self.store, request="soak cancel",
                                     tasks=[{"capability": "a", "arguments": {}}],
                                     manifest=self.manifest, objective_summary="soak")["run_id"]
        calls = []

        async def capability(name, arguments):
            calls.append(name); return {"ok": True}
        ex = ContinuousTaskExecutor(self.store, capability)
        ex.stop()
        try:
            ex.cancel(run_id, reason="soak")
            n = asyncio.run(ex._drive_one_round(run_id))
            if n or calls:
                self.violate(f"cancel: {n} tasks executed after cancel ({calls})")
            self.count("cancels")
        finally:
            ex.stop()

    def worker_crash_cycle(self) -> None:
        """A real child control plane killed mid-task; the watchdog in THIS
        process recovers the run (lease expiry, orphan sweep)."""
        from friday.continuous import ContinuousTaskExecutor, RunWatchdog
        from friday import objectives as O
        hold = self.out / f"hold-{secrets.token_hex(3)}"
        hold.write_text("x")
        proc = subprocess.Popen([sys.executable, "-c", WORKER_CHILD, str(ROOT), str(self.db), str(hold)],
                                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True,
                                env={**os.environ, "PYTHONIOENCODING": "utf-8"})
        try:
            line = proc.stdout.readline()
            run_id = json.loads(line)["run_id"]
            time.sleep(0.4)                                  # inside task "slow"
            proc.kill(); proc.wait(timeout=10)
        finally:
            if proc.poll() is None:
                proc.kill()
            hold.unlink(missing_ok=True)

        async def recover():
            async def capability(name, arguments):
                return {"ok": True, "verification": {"method": "return_value", "evidence": "ran"}}
            ex = ContinuousTaskExecutor(self.store, capability, executor_id="soak-watchdog")
            ex.lease_timeout = 1.0
            ex.stop()
            wd = RunWatchdog(ex, lease_timeout=1.0)
            deadline = time.time() + 15
            while time.time() < deadline:
                await wd.sweep_once()
                row = self.store.objective_run(run_id)
                if row["status"] in O.RUN_TERMINAL:
                    return row["status"]
                await asyncio.sleep(0.3)
            return self.store.objective_run(run_id)["status"]
        status = asyncio.run(recover())
        if status not in O.RUN_TERMINAL:
            self.violate(f"worker crash: run {run_id} not recovered ({status})")
        self.count("worker_crashes_recovered")

    # -- helpers ---------------------------------------------------------------

    def _fresh_run(self, tag: str) -> str:
        from friday import objectives as O
        return O.compile_objective(self.store, request=f"soak {tag}",
                                   tasks=[{"capability": "a", "arguments": {}}],
                                   manifest=self.manifest, objective_summary=f"soak {tag}")["run_id"]

    def close(self) -> None:
        try:
            self.supervisor.stop()
        finally:
            self.gateway.close()
            self.store.close()


# ---------------------------------------------------------------------------
# the run
# ---------------------------------------------------------------------------


def run(duration_s: float, out: Path, sample_every_s: float = 5.0,
        governor_mode: str = "product") -> dict:
    """`governor_mode`: "product" leaves the resource governor at its shipped
    thresholds - on a host already at 95%+ RAM it sheds every worker, the
    Hermes lifecycle never runs, and the verdict is INCOMPLETE (correct: the
    product refused, the soak did not measure). "relaxed" raises ONLY the
    RAM/CPU critical lines for this process so the lifecycle runs on a
    loaded machine; the report names the mode, and shedding itself is not
    under test here (tests/test_governor.py is)."""
    out.mkdir(parents=True, exist_ok=True)
    from friday import governor as G
    if governor_mode == "relaxed":
        G.configure(G.Governor(thresholds=G.Thresholds(cpu_critical=101.0, ram_critical=101.0,
                                                        cpu_high=101.0, ram_high=101.0)))
    else:
        G.configure(None)
    os.environ["ADA_DB"] = str(out / "soak.sqlite3")
    os.environ["FRIDAY_REPLAY_NONCES"] = str(out / "nonces.json")
    for key in ("FAKE_GW_FAIL_PROVIDERS", "FAKE_GW_HANG", "FAKE_GW_DIE", "FAKE_GW_ECHO",
                "FAKE_GW_EMPTY_PROVIDERS", "FAKE_HERMES_CLARIFY", "FAKE_HERMES_HANG", "FAKE_HERMES_DIE"):
        os.environ.pop(key, None)
    from friday import access
    access.NONCES_PATH = Path(os.environ["FRIDAY_REPLAY_NONCES"])
    access._seen_nonces.clear()

    wl = Workload(out)
    # Cadence for the every-Nth moves. On a long run these are rare by
    # design; on a short one (the CI smoke) a fixed N=25 means a slow
    # runner never reaches the Hermes stop/start at all and the report is
    # INCOMPLETE for a reason that has nothing to do with the product.
    # So the divisors shrink with the planned duration: every promised
    # move fires at least twice in the shortest run we allow.
    tight = duration_s < 600
    cadence = {"budget": 3, "cancel": 4,
               "hermes_crash": 4 if tight else 10,
               "worker_crash": 5 if tight else 15,
               "hermes_stop_start": 6 if tight else 25}
    proc = psutil.Process()
    proc.cpu_percent(interval=None)
    samples: list[dict] = []
    t0 = time.time()
    stop = threading.Event()

    def sampler():
        while not stop.is_set():
            try:
                samples.append(sample(proc, wl.store, wl.telemetry, wl.log_path, t0))
            except Exception as exc:  # noqa: BLE001
                wl.log(f"sampler error {type(exc).__name__}: {exc}")
            stop.wait(sample_every_s)
    th = threading.Thread(target=sampler, name="soak-sampler", daemon=True)
    th.start()

    cycle = 0
    errors: list[str] = []
    try:
        wl.supervisor.start()
        while time.time() - t0 < duration_s:
            cycle += 1
            try:
                asyncio.run(wl.objective_cycle())
                wl.provider_cycle()
                wl.db_cycle()
                wl.nonce_cycle()
                wl.scheduler_cycle()
                wl.handoff_cycle()
                if cycle % cadence["budget"] == 0:
                    wl.budget_cycle()
                if cycle % cadence["cancel"] == 0:
                    wl.cancel_cycle()
                wl.hermes_cycle(crash=(cycle % cadence["hermes_crash"] == 0))
                if cycle % cadence["hermes_stop_start"] == 0:
                    wl.hermes_stop_start()
                if cycle % cadence["worker_crash"] == 0:
                    wl.worker_crash_cycle()
            except Exception as exc:  # noqa: BLE001 - recorded, the soak continues
                msg = f"cycle {cycle}: {type(exc).__name__}: {exc}"
                errors.append(msg)
                wl.log(f"ERROR {msg}")
            wl.log(f"cycle {cycle} done counts={json.dumps(wl.counts, sort_keys=True)}")
    finally:
        stop.set(); th.join(timeout=10)
        try:
            samples.append(sample(proc, wl.store, wl.telemetry, wl.log_path, t0))
        except Exception:  # noqa: BLE001
            pass
        wl.close()

    report = analyse(samples, duration_s, wl.counts, wl.violations, errors, cycle)
    report["governor_mode"] = governor_mode
    report["cadence"] = cadence
    (out / "samples.json").write_text(json.dumps(samples), encoding="utf-8")
    (out / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    (out / "report.md").write_text(render(report), encoding="utf-8")
    return report


def _window(samples: list[dict], key: str, lo: float, hi: float) -> list[float]:
    return [float(s[key]) for s in samples if lo <= s["t"] < hi and s.get(key, -1) >= 0]


def analyse(samples: list[dict], duration_s: float, counts: dict, violations: list[str],
            errors: list[str], cycles: int) -> dict:
    """Hour-1 vs final-hour (or first vs last quarter for short runs)."""
    if len(samples) < 4:
        return {"verdict": "INCONCLUSIVE", "reason": f"only {len(samples)} samples", "counts": counts,
                "violations": violations, "errors": errors, "cycles": cycles}
    span = samples[-1]["t"]
    win = min(3600.0, span / 4)
    # Warm-up is not growth: lazy imports on the first terminal event
    # (~25 MB once), SQLite page caches filling to their 2 MB cap per
    # ledger, allocator arenas. The first window starts after it.
    warmup = min(max(60.0, 0.05 * span), span / 4)
    first = (warmup, warmup + win)
    last = (span - win, span + 1)
    series: dict[str, dict] = {}
    growing: list[str] = []
    for key in HARD_SERIES + RATE_SERIES:
        a, b = _window(samples, key, *first), _window(samples, key, *last)
        if not a or not b:
            continue
        ma, mb = statistics.median(a), statistics.median(b)
        # Monotonic growth: the medians rise AND the series never returns
        # to its first-window median after the midpoint. A sawtooth (GC,
        # a child that comes and goes) is not a leak.
        mid = span / 2
        tail = [float(s[key]) for s in samples if s["t"] >= mid and s.get(key, -1) >= 0]
        never_returns = bool(tail) and min(tail) > ma
        grows = mb > ma and never_returns
        rel = (mb - ma) / ma if ma else (float("inf") if mb > ma else 0.0)
        series[key] = {"first_median": ma, "last_median": mb, "delta": round(mb - ma, 3),
                       "rel": round(rel, 4) if rel != float("inf") else None,
                       "max": max(float(s[key]) for s in samples if s.get(key, -1) >= 0),
                       "monotonic_growth": grows}
        # For hard series a small absolute drift is noise: rss under 10 MB
        # or a couple of handles across the run is not a leak signal.
        floor = {"rss_mb": 10.0, "handles": 8, "threads": 2, "children": 0, "sqlite_conns": 0}.get(key)
        if key in HARD_SERIES and grows and (floor is None or (mb - ma) > floor):
            growing.append(key)
    hours = span / 3600 or 1e-9
    rates = {k: round(counts.get(k, 0) / hours, 1) for k in sorted(counts)}
    # A cycle that raised is a workload move that did not complete: the
    # soak did not exercise what it claims it did. Counted against the
    # verdict, never footnoted.
    verdict = "PASS" if not growing and not violations and not errors else "FAIL"
    # Coverage: every move the workload PROMISES must actually have run.
    # A host under pressure makes the governor shed every worker (correct),
    # and then the Hermes lifecycle was never exercised - a PASS with that
    # column empty would be a claim about code that did not run. That is
    # INCOMPLETE, with the missing moves named, never PASS.
    required = ("objective", "provider_ok", "budget_refused", "hermes_delegate",
                "hermes_stop_start", "hermes_crash_recovered", "worker_crashes_recovered",
                "cancels", "nonces", "scheduler_claims", "handoffs", "db_writes")
    missing = [k for k in required if not counts.get(k)]
    if verdict == "PASS" and missing:
        verdict = "INCOMPLETE"
    # A short run cannot separate warm-up from a leak: its growth numbers
    # are reported, not judged. The verdict says SMOKE (workload ran,
    # invariants held) - never PASS, which is the 8-hour gate's word.
    if span < MIN_JUDGED_S and verdict == "PASS":
        verdict = "SMOKE"
    return {"verdict": verdict, "duration_s": round(span, 1), "planned_s": duration_s,
            "window_s": win, "warmup_s": warmup, "judged": span >= MIN_JUDGED_S,
            "samples": len(samples), "cycles": cycles,
            "growing": growing, "series": series, "counts": counts, "per_hour": rates,
            "missing_moves": missing,
            "violations": violations, "errors": errors[:50], "error_count": len(errors),
            "host": {"platform": sys.platform, "python": sys.version.split()[0],
                     "cpu_count": os.cpu_count(), "ran_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}}


def render(r: dict) -> str:
    lines = [f"# A-051 soak report - {r['verdict']}", "",
             f"ran {r.get('duration_s', 0)} s of {r.get('planned_s', 0)} planned; {r.get('cycles', 0)} cycles; "
             f"{r.get('samples', 0)} samples; window {r.get('window_s', 0):.0f} s after a "
             f"{r.get('warmup_s', 0):.0f} s warm-up", ""]
    if r.get("reason"):
        lines.append(f"INCONCLUSIVE: {r['reason']}")
    if r.get("judged") is False:
        lines.append(f"SMOKE: {r.get('duration_s', 0)} s is under {MIN_JUDGED_S:.0f} s - growth is reported, not judged. "
                     "The PRD gate is `--hours 8`.")
    lines += ["## series (first window median -> last window median)", "",
              "| series | first | last | delta | max | monotonic growth |", "|---|---|---|---|---|---|"]
    for k, v in r.get("series", {}).items():
        flag = "**YES**" if v["monotonic_growth"] else "no"
        lines.append(f"| {k} | {v['first_median']} | {v['last_median']} | {v['delta']} | {v['max']} | {flag} |")
    host = r.get("series", {})
    if "host_ram_pct" in host:
        lines += ["", f"host during the run: RAM {host['host_ram_pct']['first_median']}% -> "
                      f"{host['host_ram_pct']['last_median']}% (max {host['host_ram_pct']['max']}%), "
                      f"CPU max {host.get('host_cpu_pct', {}).get('max', '?')}%; "
                      f"workers shed by the governor: {r.get('counts', {}).get('hermes_shed', 0)}; "
                      f"governor thresholds: {r.get('governor_mode', 'product')}"
                      + (" (RAM/CPU critical lines raised for this run - shedding NOT under test)"
                         if r.get('governor_mode') == 'relaxed' else "")]
    lines += ["", "## workload per hour", ""]
    for k, v in r.get("per_hour", {}).items():
        lines.append(f"- {k}: {v}")
    lines += ["", f"## violations ({len(r.get('violations', []))})", ""]
    lines += [f"- {v}" for v in r.get("violations", [])] or ["- none"]
    lines += ["", f"## errors ({r.get('error_count', 0)})", ""]
    lines += [f"- {e}" for e in r.get("errors", [])] or ["- none"]
    if r.get("growing"):
        lines += ["", f"FAIL: monotonic growth on {', '.join(r['growing'])}"]
    if r.get("missing_moves"):
        lines += ["", f"INCOMPLETE: these workload moves never ran - {', '.join(r['missing_moves'])}. "
                      "If the governor shed workers (see host pressure above), rerun on a quieter machine; "
                      "a verdict over code that did not execute is not a verdict."]
    return "\n".join(lines) + "\n"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--hours", type=float, default=0.0)
    ap.add_argument("--minutes", type=float, default=0.0)
    ap.add_argument("--out", type=Path, default=None)
    ap.add_argument("--sample-every", type=float, default=5.0)
    ap.add_argument("--evidence", type=Path, default=None,
                    help="also copy report.md/report.json/samples.json into this directory (release evidence, e.g. docs/evidence/soak/<name>)")
    ap.add_argument("--governor", choices=("product", "relaxed"), default="product",
                    help="relaxed: raise the RAM/CPU critical lines so the Hermes lifecycle runs on a loaded host (named in the report)")
    args = ap.parse_args(argv)
    duration = args.hours * 3600 + args.minutes * 60
    if duration <= 0:
        ap.error("give --hours or --minutes")
    out = args.out or (ROOT / "data" / "soak" / datetime.now().strftime("%Y%m%d_%H%M%S"))
    report = run(duration, out, sample_every_s=args.sample_every, governor_mode=args.governor)
    print(render(report))
    print(f"report: {out / 'report.md'}")
    if args.evidence:
        import shutil
        args.evidence.mkdir(parents=True, exist_ok=True)
        for name in ("report.md", "report.json", "samples.json"):
            shutil.copy2(out / name, args.evidence / name)
        print(f"evidence: {args.evidence}")
    return 0 if report["verdict"] in ("PASS", "SMOKE") else 1


if __name__ == "__main__":
    sys.exit(main())
