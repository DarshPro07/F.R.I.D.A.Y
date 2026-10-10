"""
Rollout of the staged tool router behind capability search (PRD v7 19.3, CTX-002).

`tool_router_mode` (env FRIDAY_TOOL_ROUTER_MODE) decides who answers a search:

  legacy  the shipped keyword router. Nothing else runs. The default.
  shadow  legacy decides and its answer is returned untouched. The staged router
          also runs, on a background worker, and one record is logged per
          request: the legacy choice, the staged top-k, confidence, latency,
          whether it would have asked to clarify, and (a later event) the tool
          that then ran successfully. Its stage 2 calls a chooser only for a
          bounded, deterministic sample (`sample_rate`, `sample_budget`); every
          other request stops at stage 1, so shadow costs no model call by default.
  staged  the staged router owns the answer. A build failure, an exception, a
          missed deadline or an ABSTAIN decision falls back to the legacy answer for
          that request; a CLARIFY decision keeps the shortlist and tells the caller to ask.

Promotion from shadow to staged is an owner decision backed by evidence
(docs/plans/ctx-002/PLAN.md); `Metrics` carries the numbers it needs. The
evidence is an arena report (friday/arena.py, kind capability_path, candidate
`staged_router`) named by FRIDAY_TOOL_ROUTER_REPORT: `staged` without a passing
one runs as `shadow`. A record holds a hash of the request, never its text.
"""
from __future__ import annotations

import hashlib
import json
import logging
import math
import os
import threading
import time
from collections import deque
from concurrent.futures import Future, ThreadPoolExecutor, wait
from dataclasses import dataclass
from typing import Callable, Mapping

from friday import arena, tool_router

log =logging.getLogger("friday.tool_router.rollout")

LEGACY, SHADOW, STAGED = "legacy", "shadow", "staged"
MODES = (LEGACY, SHADOW, STAGED)

SHIPPED_DEPTH = 20      # how deep the shipped ranking feeds the fusion (the benchmark's depth)
MAX_PENDING = 8         # shadow evaluations queued before more are dropped, never waited for
BUILD_RETRY_S = 300.0   # after a failed build, serve legacy this long before building again
RECORDS_KEPT = 500
LATENCIES_KEPT = 2000


def _number(env: Mapping[str, str], key: str, default, kind, low, high):
    raw = env.get(key)
    if raw is None or not str(raw).strip():
        return default
    try:
        value = kind(str(raw).strip())
    except ValueError:
        log.warning("%s=%r is not a number; using %s", key, raw, default)
        return default
    if isinstance(value, float) and not math.isfinite(value):
        log.warning("%s=%r is not finite; using %s", key, raw, default)
        return default
    return max(low, min(high, value))


@dataclass(frozen=True)
class Config:
    mode: str = LEGACY
    timeout_s: float = 2.0       # staged: the whole route, build included, before the legacy fallback
    sample_rate: float = 0.0     # shadow: share of requests whose stage 2 may call the chooser
    sample_budget: int = 0       # shadow: hard cap on chooser calls per process
    log_path: str = ""           # optional JSON-lines copy of every record

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None,
                 store: "arena.ArenaStore | None" = None) -> "Config":
        """A bad value never stops boot: it is logged and the safe default is used.

        `staged` is a promotion, so it needs a passing capability_path arena report
        for `staged_router`, named by FRIDAY_TOOL_ROUTER_REPORT; without one the
        router runs as `shadow`, which still gathers the evidence a report is made of."""
        env = os.environ if env is None else env
        mode = (env.get("FRIDAY_TOOL_ROUTER_MODE") or LEGACY).strip().lower()
        if mode not in MODES:
            log.warning("FRIDAY_TOOL_ROUTER_MODE=%r is not one of %s; using legacy", mode, MODES)
            mode = LEGACY
        if mode == STAGED:
            try:
                arena.promote(arena.ArenaKind.CAPABILITY_PATH, "staged_router",
                              report_id=(env.get("FRIDAY_TOOL_ROUTER_REPORT") or "").strip(),
                              store=store)
            except arena.PromotionRefused as exc:
                log.warning("FRIDAY_TOOL_ROUTER_MODE=staged is not promoted (%s); using shadow", exc)
                mode = SHADOW
        return cls(
            mode=mode,
            timeout_s=_number(env, "FRIDAY_TOOL_ROUTER_TIMEOUT_S", 2.0, float, 0.05, 30.0),
            sample_rate=_number(env, "FRIDAY_TOOL_ROUTER_SAMPLE_RATE", 0.0, float, 0.0, 1.0),
            sample_budget=_number(env, "FRIDAY_TOOL_ROUTER_SAMPLE_BUDGET", 0, int, 0, 10_000),
            log_path=(env.get("FRIDAY_TOOL_ROUTER_LOG") or "").strip())


class Metrics:
    """What promotion needs: how often the chooser is asked, how slow the router
    is, how often it failed back to legacy."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._counts = {"requests": 0, "chooser_calls": 0, "fallbacks": 0, "errors": 0,
                        "timeouts": 0, "clarify": 0, "abstain": 0, "dropped": 0, "log_errors": 0}
        self._latency: deque[float] = deque(maxlen=LATENCIES_KEPT)

    def add(self, key: str, n: int = 1) -> None:
        with self._lock:
            self._counts[key] += n

    def observe(self, latency_ms: float) -> None:
        with self._lock:
            self._latency.append(latency_ms)

    def snapshot(self) -> dict:
        with self._lock:
            counts, latency = dict(self._counts), sorted(self._latency)
        requests = counts["requests"]
        p95 = latency[max(0, math.ceil(0.95 * len(latency)) - 1)] if latency else 0.0
        return {**counts,
                "chooser_invocation_rate": round(counts["chooser_calls"] / requests, 4) if requests else 0.0,
                "p95_latency_ms": round(p95, 2)}


def query_id(query: str) -> str:
    return hashlib.sha256((query or "").encode("utf-8")).hexdigest()[:16]


def _fraction(sha: str) -> float:
    """A stable position in [0, 1) for a request, so a sample is reproducible."""
    return int(sha[:8], 16) / 0x1_0000_0000


def _build_staged(ids, texts, descriptions, *, shipped, chooser):
    from friday import tool_models

    embedder = tool_models.embedder()          # pinned files on disk or None: never a download
    revision = tool_models.PINS[tool_models.EMBEDDER.id].revision if embedder else ""
    return tool_router.StagedRouter(
        ids, texts, shipped=shipped, embedder=embedder,
        cache_dir=tool_models.models_dir() / "vectors", revision=revision,
        chooser=chooser, descriptions=descriptions)


class Rollout:
    """One per capability Router. `legacy(query, limit)` and `describe(name)` are
    the Router's own; `inputs()` is its (ids, texts, descriptions) for indexing."""

    def __init__(self, *, legacy: Callable[[str, int], list[dict]], describe: Callable[[str], dict],
                 inputs: Callable[[], tuple], config: Config | None = None,
                 chooser: tool_router.Chooser | None = None, build: Callable | None = None):
        self.config = config or Config()
        self.chooser = chooser
        self.metrics = Metrics()
        self.records: deque[dict] = deque(maxlen=RECORDS_KEPT)
        #: staged mode: the latest search's decision, None after a fallback
        self.last_decision: dict | None = None
        self._legacy, self._describe, self._inputs = legacy, describe, inputs
        self._build = build or _build_staged
        self._staged = None
        self._failed: tuple[float, str] | None = None
        self._generation = 0             # bumped by reset(); a build started under an older one is not kept
        self._state_lock = threading.Lock()    # guards _staged/_failed/_generation; never held across a build
        self._build_lock = threading.Lock()    # serialises builds
        self._pending: set[Future] = set()
        self._pending_lock = threading.Lock()
        self._log_lock = threading.Lock()
        # Executors start no thread until the first submit, so legacy mode pays nothing.
        self._shadow_pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="tool-shadow")
        self._route_pool = ThreadPoolExecutor(max_workers=4, thread_name_prefix="tool-router")
        self._chooser_used = 0           # shadow worker only: chooser calls spent from the sample budget
        self._turn = 0
        #: the latest search of the current turn: (query sha, turn, names the caller was given)
        self._last: tuple[str, int, frozenset[str]] | None = None

    # -- entry points -------------------------------------------------------

    def search(self, query: str, limit: int) -> list[dict]:
        mode = self.config.mode
        if mode == LEGACY:
            return self._legacy(query, limit)
        self.last_decision = None
        sha, turn = query_id(query), self._turn
        if mode == SHADOW:
            result = self._legacy(query, limit)
            self._shadow(query, sha, [m["capability"] for m in result], turn)
        else:
            result = self._staged_search(query, sha, limit, turn)
        self._last = (sha, turn, frozenset(m["capability"] for m in result))
        return result

    def new_turn(self) -> None:
        """The owner spoke again: a tool that runs from here on belongs to no earlier search."""
        self._turn += 1
        self._last = None

    def clarifying(self) -> bool:
        decision = self.last_decision
        return bool(decision and decision["decision"] == tool_router.CLARIFY)

    def observe(self, tool: str) -> None:
        """A tool ran and succeeded after this turn's search: the label shadow evidence needs.
        A tool that ran with no search this turn (a core tool, an earlier turn's grant) has no
        query to be paired with, so it records nothing rather than a wrong pairing."""
        last = self._last
        if self.config.mode == LEGACY or last is None or last[1] != self._turn:
            return
        sha, turn, shortlisted = last
        self._emit({"event": "observed", "ts": round(time.time(), 3), "query_sha": sha,
                    "turn": turn, "tool": tool, "shortlisted": tool in shortlisted})

    def reset(self) -> None:
        """The tool set changed: index it again on next use. Never waits for a build in
        flight (that one's result is dropped), so the caller is not held up by a slow index."""
        with self._state_lock:
            self._staged, self._failed = None, None
            self._generation += 1

    def drain(self, timeout: float = 10.0) -> None:
        """Wait for queued shadow evaluations (tests, shutdown)."""
        with self._pending_lock:
            pending = list(self._pending)
        wait(pending, timeout=timeout)

    # -- staged -------------------------------------------------------------

    def _route_job(self, query: str) -> tool_router.Route:
        """What the route pool runs: the (possibly first, slow) index build and the route,
        so the caller's deadline covers both."""
        return self._router().route(query)

    def _staged_search(self, query: str, sha: str, limit: int, turn: int) -> list[dict]:
        started = time.perf_counter()
        route, reason = None, None
        try:
            future = self._route_pool.submit(self._route_job, query)
            # wait() rather than future.result(timeout=): on Python 3.11 the futures TimeoutError
            # is the builtin one, so a worker that raises it would be miscounted as a missed
            # deadline. Only an unfinished future is a timeout; a finished one that raised is an error.
            done, _ = wait([future], timeout=self.config.timeout_s)
            if done:
                route = future.result()
            else:
                future.cancel()               # a queued job never runs; a running build finishes in the background
                self.metrics.add("timeouts")
                reason = "timeout"
        except Exception as exc:                              # noqa: BLE001
            self.metrics.add("errors")
            reason = f"error: {type(exc).__name__}"
            log.warning("staged tool routing failed, serving legacy: %s", exc)
        latency_ms = (time.perf_counter() - started) * 1000
        self.metrics.add("requests")
        self.metrics.observe(latency_ms)
        if route is None:
            self.metrics.add("fallbacks")
            result = self._legacy(query, limit)
            self._record(sha, turn, STAGED, None, latency_ms,
                         legacy=[m["capability"] for m in result], fallback=reason)
            return result
        self._count(route)
        if route.decision == tool_router.ABSTAIN:
            # No candidate does what was asked: the staged shortlist would only mislead,
            # so this request is answered as in legacy mode, and the record says why.
            self.metrics.add("abstain")
            self.metrics.add("fallbacks")
            result = self._legacy(query, limit)
            self._record(sha, turn, STAGED, route, latency_ms,
                         legacy=[m["capability"] for m in result], fallback="abstain")
            return result
        self.last_decision = {"decision": route.decision, "stage": route.stage,
                              "confidence": route.confidence, "uncertain": route.uncertain}
        self._record(sha, turn, STAGED, route, latency_ms)
        names = list(route.candidates)
        if route.decision == tool_router.TOOL and route.tool_id:
            names = [route.tool_id, *(n for n in names if n != route.tool_id)]
        return [self._describe(name) for name in names[:limit]]

    # -- shadow -------------------------------------------------------------

    def _shadow(self, query: str, sha: str, legacy: list[str], turn: int) -> None:
        """Queue the staged evaluation. Never raises, never waits: shadow must not
        change or slow what the caller gets."""
        try:
            with self._pending_lock:
                if len(self._pending) >= MAX_PENDING:
                    self.metrics.add("dropped")
                    return
                future = self._shadow_pool.submit(self._shadow_run, query, sha, legacy, turn)
                self._pending.add(future)
            future.add_done_callback(self._forget)
        except Exception as exc:                              # noqa: BLE001
            self.metrics.add("errors")
            log.warning("shadow tool routing could not be queued: %s", exc)

    def _forget(self, future: Future) -> None:
        with self._pending_lock:
            self._pending.discard(future)

    def _shadow_run(self, query: str, sha: str, legacy: list[str], turn: int) -> None:
        started = time.perf_counter()
        try:
            cfg = self.config
            allowed = (self.chooser is not None and self._chooser_used < cfg.sample_budget
                       and _fraction(sha) < cfg.sample_rate)
            route = self._router().route(query, use_chooser=allowed)
        except Exception as exc:                              # noqa: BLE001
            self.metrics.add("requests")
            self.metrics.add("errors")
            log.warning("shadow tool routing failed: %s", exc)
            self._record(sha, turn, SHADOW, None, (time.perf_counter() - started) * 1000,
                         legacy=legacy, fallback=f"error: {type(exc).__name__}")
            return
        latency_ms = (time.perf_counter() - started) * 1000
        self.metrics.add("requests")
        self.metrics.observe(latency_ms)
        if route.chooser_called:
            self._chooser_used += 1
        self._count(route)
        self._record(sha, turn, SHADOW, route, latency_ms, legacy=legacy)

    # -- shared -------------------------------------------------------------

    def _count(self, route: tool_router.Route) -> None:
        if route.chooser_called:
            self.metrics.add("chooser_calls")
        if route.decision == tool_router.CLARIFY:
            self.metrics.add("clarify")

    def _shipped(self, query: str) -> list[str]:
        return [m["capability"] for m in self._legacy(query, SHIPPED_DEPTH)]

    def _ready(self) -> tuple[tool_router.StagedRouter | None, int]:
        """The built router, or None; raises while a failed build is being waited out.
        Also the tool-set generation this answer belongs to."""
        with self._state_lock:
            if self._staged is not None:
                return self._staged, self._generation
            if self._failed and time.monotonic() - self._failed[0] < BUILD_RETRY_S:
                raise RuntimeError(f"staged router unavailable: {self._failed[1]}")
            return None, self._generation

    def _router(self) -> tool_router.StagedRouter:
        staged, _ = self._ready()
        if staged is not None:
            return staged
        with self._build_lock:                    # one build at a time; a waiter reuses its result
            staged, generation = self._ready()
            if staged is not None:
                return staged
            try:
                ids, texts, descriptions = self._inputs()
                staged = self._build(ids, texts, descriptions, shipped=self._shipped,
                                     chooser=self.chooser)
            except Exception as exc:                          # noqa: BLE001
                with self._state_lock:
                    if generation == self._generation:
                        self._failed = (time.monotonic(), f"{type(exc).__name__}: {exc}")
                raise
            with self._state_lock:
                if generation == self._generation:            # not if the tool set changed mid-build
                    self._staged = staged
            return staged

    def _record(self, sha: str, turn: int, mode: str, route: tool_router.Route | None,
                latency_ms: float, *, legacy: list[str] | None = None,
                fallback: str | None = None) -> None:
        self._emit({
            "event": "route", "ts": round(time.time(), 3), "mode": mode, "query_sha": sha,
            "turn": turn,
            "legacy_choice": legacy[0] if legacy else None, "legacy_top": legacy or [],
            "staged_choice": route.tool_id if route else None,
            "staged_top": list(route.candidates) if route else [],
            "decision": route.decision if route else None,
            "stage": route.stage if route else None,
            "confidence": route.confidence if route else None,
            "would_clarify": bool(route and route.decision == tool_router.CLARIFY),
            "uncertain": bool(route and route.uncertain),
            "chooser_called": bool(route and route.chooser_called),
            "latency_ms": round(latency_ms, 2), "fallback": fallback})

    def _emit(self, record: dict) -> None:
        self.records.append(record)
        line = json.dumps(record, sort_keys=True)
        log.info("tool_router %s", line)
        if not self.config.log_path:
            return
        try:
            with self._log_lock, open(self.config.log_path, "a", encoding="utf-8") as sink:
                sink.write(line + "\n")
        except OSError as exc:
            self.metrics.add("log_errors")
            log.warning("tool router log %s not writable: %s", self.config.log_path, exc)
