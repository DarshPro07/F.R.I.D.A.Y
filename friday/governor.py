"""
The resource governor: how much the machine can carry, decided outside the model.

PRD v3.1 FR-056 (P0): monitor CPU, RAM, disk, browser processes, worker count
and queue pressure, applying backpressure or shedding optional work.
FR-013 (P0): default active execution concurrency stays bounded (0-2
workers) and scales only for demonstrably parallel work. NFR-P09.

Three things, deliberately separate:

  1. `Sample`     one measurement of the machine (psutil) - never guessed.
  2. `Pressure`   a level derived from the sample: NORMAL / ELEVATED / HIGH /
                  CRITICAL, with the reasons that put it there.
  3. `Governor`   the admission gate. Every worker dispatch asks
                  `admit(kind)` first and gets ADMIT / QUEUE / SHED with a
                  reason it can say out loud (the PRD's "resource pressure
                  banner"). Admitted work holds a lease it must release.

The governor never kills a worker; it stops NEW work being started when
the machine cannot carry it. That is what keeps the control plane
responsive (FR-056 acceptance): the control plane's own reads and voice
turns are not workers and are never gated.

Nothing here is a mock. Thresholds are configurable for tests, but the
sample is always the live machine unless a test injects one.
"""
from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field

logger = logging.getLogger("friday.governor")

# -- pressure levels ---------------------------------------------------------

NORMAL = "NORMAL"
ELEVATED = "ELEVATED"
HIGH = "HIGH"
CRITICAL = "CRITICAL"
LEVELS = (NORMAL, ELEVATED, HIGH, CRITICAL)

# -- admission decisions -----------------------------------------------------

ADMIT = "ADMIT"
QUEUE = "QUEUE"
SHED = "SHED"

# -- work kinds. The distinction is what the governor sheds first. -----------

WORKER = "worker"            # a coding/specialist execution worker
BROWSER = "browser"          # a browser automation session
OPTIONAL = "optional"        # autolearn, benchmarks, background digests
KINDS = (WORKER, BROWSER, OPTIONAL)


@dataclass(frozen=True)
class Thresholds:
    """Percent-of-machine limits. ELEVATED starts backpressure on optional
    work; HIGH queues new workers; CRITICAL sheds everything but the one
    worker already running."""

    cpu_elevated: float = 70.0
    cpu_high: float = 85.0
    cpu_critical: float = 95.0
    ram_elevated: float = 75.0
    ram_high: float = 88.0
    ram_critical: float = 95.0
    disk_free_gb_low: float = 5.0
    disk_free_gb_critical: float = 1.0
    max_workers: int = 2                   # FR-013 / NFR-P09 default
    max_browsers: int = 2
    max_queue: int = 8


@dataclass
class Sample:
    """One reading of the machine. `browser_processes` counts Chromium-family
    processes, which is what a runaway browser session looks like."""

    at: float
    cpu_percent: float
    ram_percent: float
    ram_available_gb: float
    disk_free_gb: float
    browser_processes: int
    friday_rss_mb: float

    def to_dict(self) -> dict:
        return {"at": self.at, "cpu_percent": self.cpu_percent,
                "ram_percent": self.ram_percent,
                "ram_available_gb": self.ram_available_gb,
                "disk_free_gb": self.disk_free_gb,
                "browser_processes": self.browser_processes,
                "friday_rss_mb": self.friday_rss_mb}


_BROWSER_NAMES = ("chrome", "chromium", "msedge", "brave", "firefox", "playwright")


def sample_machine(*, cpu_interval: float = 0.0) -> Sample:
    """Measure. `cpu_interval=0` returns the delta since the last call,
    which is what a periodic sampler wants; a first call reports 0.0."""
    import psutil
    from pathlib import Path

    cpu = psutil.cpu_percent(interval=cpu_interval or None) if cpu_interval else psutil.cpu_percent(interval=None)
    mem = psutil.virtual_memory()
    try:
        from friday.config import DATA_DIR
        anchor = str(Path(DATA_DIR).resolve().anchor or Path.cwd().anchor)
    except Exception:  # noqa: BLE001 - config trouble must not break sampling
        anchor = str(Path.cwd().anchor)
    try:
        disk_free = psutil.disk_usage(anchor).free / 1024**3
    except OSError:
        disk_free = float("inf")
    browsers = 0
    for proc in psutil.process_iter(["name"]):
        name = (proc.info.get("name") or "").lower()
        if any(b in name for b in _BROWSER_NAMES):
            browsers += 1
    try:
        rss = psutil.Process().memory_info().rss / 1024**2
    except psutil.Error:
        rss = 0.0
    return Sample(at=time.time(), cpu_percent=float(cpu),
                  ram_percent=float(mem.percent),
                  ram_available_gb=mem.available / 1024**3,
                  disk_free_gb=disk_free, browser_processes=browsers,
                  friday_rss_mb=rss)


@dataclass
class Pressure:
    level: str
    reasons: list[str] = field(default_factory=list)
    sample: Sample | None = None

    def to_dict(self) -> dict:
        return {"level": self.level, "reasons": list(self.reasons),
                "sample": self.sample.to_dict() if self.sample else None}


def assess(sample: Sample, thresholds: Thresholds = Thresholds()) -> Pressure:
    """Derive the pressure level from a sample. Pure; unit-testable."""
    t = thresholds
    reasons: list[str] = []
    level = NORMAL

    def bump(to: str, why: str) -> None:
        nonlocal level
        reasons.append(why)
        if LEVELS.index(to) > LEVELS.index(level):
            level = to

    if sample.cpu_percent >= t.cpu_critical:
        bump(CRITICAL, f"CPU {sample.cpu_percent:.0f}%")
    elif sample.cpu_percent >= t.cpu_high:
        bump(HIGH, f"CPU {sample.cpu_percent:.0f}%")
    elif sample.cpu_percent >= t.cpu_elevated:
        bump(ELEVATED, f"CPU {sample.cpu_percent:.0f}%")
    if sample.ram_percent >= t.ram_critical:
        bump(CRITICAL, f"RAM {sample.ram_percent:.0f}% used")
    elif sample.ram_percent >= t.ram_high:
        bump(HIGH, f"RAM {sample.ram_percent:.0f}% used")
    elif sample.ram_percent >= t.ram_elevated:
        bump(ELEVATED, f"RAM {sample.ram_percent:.0f}% used")
    if sample.disk_free_gb <= t.disk_free_gb_critical:
        bump(CRITICAL, f"disk {sample.disk_free_gb:.1f} GB free")
    elif sample.disk_free_gb <= t.disk_free_gb_low:
        bump(HIGH, f"disk {sample.disk_free_gb:.1f} GB free")
    return Pressure(level=level, reasons=reasons, sample=sample)


@dataclass
class Decision:
    decision: str                # ADMIT | QUEUE | SHED
    kind: str
    reason: str
    pressure: str
    lease: str = ""              # non-empty only when ADMIT
    active_workers: int = 0
    queued: int = 0

    @property
    def admitted(self) -> bool:
        return self.decision == ADMIT

    def to_dict(self) -> dict:
        return {"decision": self.decision, "kind": self.kind, "reason": self.reason,
                "pressure": self.pressure, "lease": self.lease,
                "active_workers": self.active_workers, "queued": self.queued}


class Governor:
    """The admission gate. One per process (`governor()` below).

    `sampler` is injectable so tests drive pressure deterministically; the
    default samples the real machine, cached for `sample_ttl_s` so a burst
    of admissions does not spend a CPU probe each.
    """

    def __init__(self, *, thresholds: Thresholds | None = None,
                 sampler=None, sample_ttl_s: float = 2.0) -> None:
        self.thresholds = thresholds or Thresholds()
        self._sampler = sampler or sample_machine
        self.sample_ttl_s = sample_ttl_s
        self._lock = threading.Lock()
        self._leases: dict[str, tuple[str, float, str]] = {}   # lease -> (kind, at, label)
        self._queue: list[tuple[str, str, float]] = []           # (kind, label, at)
        self._last: Pressure | None = None
        self._last_at = 0.0
        self._next = 0
        self.decisions: list[Decision] = []                      # bounded history
        self._parallel_justified: set[str] = set()

    # -- measurement --------------------------------------------------------

    def pressure(self, *, fresh: bool = False) -> Pressure:
        now = time.time()
        with self._lock:
            if not fresh and self._last and now - self._last_at < self.sample_ttl_s:
                return self._last
        try:
            sample = self._sampler()
            p = assess(sample, self.thresholds)
        except Exception as exc:  # noqa: BLE001 - a failed probe is ELEVATED, never a crash
            logger.warning("governor sample failed: %s", exc)
            p = Pressure(level=ELEVATED, reasons=[f"sample failed: {exc}"])
        with self._lock:
            self._last, self._last_at = p, now
        return p

    # -- accounting ---------------------------------------------------------

    def active(self, kind: str | None = None) -> int:
        with self._lock:
            return sum(1 for k, _, _ in self._leases.values()
                       if kind is None or k == kind)

    def active_leases(self) -> list[dict]:
        with self._lock:
            return [{"lease": lease, "kind": k, "since": at, "label": label}
                    for lease, (k, at, label) in self._leases.items()]

    def queue_depth(self) -> int:
        with self._lock:
            return len(self._queue)

    def justify_parallel(self, objective_id: str) -> None:
        """FR-013: concurrency scales only for demonstrably parallel work.
        A caller that has a plan with independent streams says so; this
        lifts the worker cap by one for that objective's admissions."""
        with self._lock:
            self._parallel_justified.add(objective_id)

    # -- admission ----------------------------------------------------------

    def admit(self, kind: str, *, label: str = "", objective_id: str = "") -> Decision:
        if kind not in KINDS:
            raise ValueError(f"unknown work kind {kind!r}; known: {KINDS}")
        p = self.pressure()
        t = self.thresholds
        with self._lock:
            workers = sum(1 for k, _, _ in self._leases.values() if k == WORKER)
            browsers = sum(1 for k, _, _ in self._leases.values() if k == BROWSER)
            queued = len(self._queue)
            cap = t.max_workers + (1 if objective_id in self._parallel_justified else 0)

            def out(decision: str, reason: str) -> Decision:
                lease = ""
                if decision == ADMIT:
                    self._next += 1
                    lease = f"lease-{self._next}"
                    self._leases[lease] = (kind, time.time(), label)
                elif decision == QUEUE:
                    self._queue.append((kind, label, time.time()))
                d = Decision(decision, kind, reason, p.level, lease,
                             active_workers=workers + (1 if decision == ADMIT and kind == WORKER else 0),
                             queued=len(self._queue))
                self.decisions.append(d)
                del self.decisions[:-200]
                return d

            why = ", ".join(p.reasons) or "machine healthy"
            # Optional work is the first thing to go.
            if kind == OPTIONAL:
                if p.level != NORMAL or workers >= cap:
                    return out(SHED, f"optional work shed under {p.level} pressure ({why})")
                return out(ADMIT, "optional work admitted")
            if p.level == CRITICAL:
                return out(SHED, f"CRITICAL pressure: {why}; no new {kind} started")
            if kind == WORKER:
                if workers >= cap:
                    if queued >= t.max_queue:
                        return out(SHED, f"worker cap {cap} reached and queue full ({queued})")
                    return out(QUEUE, f"worker cap {cap} reached ({workers} active)")
                if p.level == HIGH:
                    if workers == 0:
                        return out(ADMIT, f"HIGH pressure ({why}) but no worker running; one admitted")
                    if queued >= t.max_queue:
                        return out(SHED, f"HIGH pressure ({why}) and queue full")
                    return out(QUEUE, f"HIGH pressure ({why}): concurrency reduced to 1")
                return out(ADMIT, "worker admitted")
            # BROWSER
            if browsers >= t.max_browsers:
                return out(QUEUE, f"browser cap {t.max_browsers} reached")
            if p.level == HIGH and browsers >= 1:
                return out(QUEUE, f"HIGH pressure ({why}): one browser at a time")
            return out(ADMIT, "browser admitted")

    def release(self, lease: str) -> None:
        with self._lock:
            self._leases.pop(lease, None)
            # Capacity returned: the queue is a record of what waited, not a
            # scheduler - callers re-run admit() themselves - so the oldest
            # waiting entry is retired here to keep the depth truthful.
            if self._queue:
                self._queue.pop(0)

    # -- reporting ----------------------------------------------------------

    def status(self) -> dict:
        p = self.pressure()
        return {
            "pressure": p.to_dict(),
            "active": self.active_leases(),
            "workers": self.active(WORKER),
            "browsers": self.active(BROWSER),
            "queued": self.queue_depth(),
            "caps": {"workers": self.thresholds.max_workers,
                     "browsers": self.thresholds.max_browsers,
                     "queue": self.thresholds.max_queue},
            "banner": self.banner(p),
        }

    @staticmethod
    def banner(p: Pressure) -> str:
        """The PRD's resource-pressure banner: explain when concurrency was
        reduced, in one sentence, or nothing when there is nothing to say."""
        if p.level == NORMAL:
            return ""
        why = ", ".join(p.reasons)
        if p.level == ELEVATED:
            return f"Resource pressure elevated ({why}); optional background work is paused."
        if p.level == HIGH:
            return f"Resource pressure high ({why}); running one worker at a time."
        return f"Resource pressure critical ({why}); no new workers or browsers until it eases."


# -- process-wide instance -------------------------------------------------

_GOVERNOR: Governor | None = None
_LOCK = threading.Lock()


def governor() -> Governor:
    global _GOVERNOR
    with _LOCK:
        if _GOVERNOR is None:
            _GOVERNOR = Governor()
        return _GOVERNOR


def configure(new: Governor | None) -> None:
    """Test seam."""
    global _GOVERNOR
    with _LOCK:
        _GOVERNOR = new


class Refused(RuntimeError):
    """A dispatch the governor did not admit. Carries the decision."""

    def __init__(self, decision: Decision) -> None:
        super().__init__(decision.reason)
        self.decision = decision
