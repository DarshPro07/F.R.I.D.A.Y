"""
The resource governor: how much the machine can carry, decided outside the model.

PRD v3.1 FR-056 (P0): monitor CPU, RAM, disk, browser processes, worker count
and queue pressure, applying backpressure or shedding optional work.
FR-013 (P0): default active execution concurrency stays bounded (0-2
workers) and scales only for demonstrably parallel work. NFR-P09.

Three things, deliberately separate:

  1. `Sample`     one measurement of the machine (psutil) - never guessed.
  2. `Pressure`   a level derived from the readings: NORMAL / ELEVATED /
                  HIGH / CRITICAL (Amendment A4's GREEN / YELLOW / ORANGE /
                  RED), with the reasons that put it there.
  3. `Governor`   the admission gate. Every worker dispatch asks
                  `admit(kind)` first and gets ADMIT / QUEUE / SHED with a
                  reason it can say out loud (the PRD's "resource pressure
                  banner"). Admitted work holds a lease it must release.

The level is judged against this machine, not against fixed percentages
(PRD v7 RES-003). A machine whose operating system idles at 76% of its RAM
is not under pressure at 76%, and one at 68% whose free memory is collapsing
is. Static percentages are emergency guardrails, not the primary pressure
classifier:

  - headroom: the share of the machine's idle headroom now in use - available
    RAM, the commit charge's room below its limit where the platform reports
    it, and idle CPU, sustained over a few seconds - against its `Baseline`,
    the median of readings taken while FRIDAY holds no leased work;
  - trend: how fast available RAM is falling, as the time left before it
    reaches the emergency floor;
  - guardrails: RAM or CPU at 95%, RAM or commit headroom below a floor, the
    disk nearly full - pressure whatever the baseline;
  - hysteresis (`Tracker`): a worse level is entered at once, a better one
    only after the reading has stayed past the boundary, by a margin, for a
    dwell time, so a reading wavering at a boundary does not flap.

The governor never kills a worker; it stops NEW work being started when
the machine cannot carry it. That is what keeps the control plane
responsive (FR-056 acceptance): the control plane's own reads and voice
turns are not workers and are never gated.

Nothing here is a mock. Thresholds and the baseline are configurable for
tests, but the sample is always the live machine unless a test injects one.
"""
from __future__ import annotations

import ctypes
import logging
import statistics
import sys
import threading
import time
from collections import deque
from dataclasses import dataclass, field

logger = logging.getLogger("friday.governor")

# -- pressure levels ---------------------------------------------------------

NORMAL = "NORMAL"
ELEVATED = "ELEVATED"
HIGH = "HIGH"
CRITICAL = "CRITICAL"
LEVELS = (NORMAL, ELEVATED, HIGH, CRITICAL)

#: Amendment A4's names for the same four levels.
STATES = {NORMAL: "GREEN", ELEVATED: "YELLOW", HIGH: "ORANGE", CRITICAL: "RED"}

# -- admission decisions -----------------------------------------------------

ADMIT = "ADMIT"
QUEUE = "QUEUE"
SHED = "SHED"

# -- work kinds. The distinction is what the governor sheds first. -----------

WORKER = "worker"            # a coding/specialist execution worker
BROWSER = "browser"          # a browser automation session
OPTIONAL = "optional"        # autolearn, benchmarks, background digests
MODEL = "model"              # a local model held resident in RAM/VRAM (MODEL-005)
KINDS = (WORKER, BROWSER, OPTIONAL, MODEL)

# -- priority of the objective the work serves (BG-001) ----------------------

BACKGROUND = "background"    # proactive work: waits for the person to be away and the power to be on
URGENT = "urgent"            # an objective that cannot wait for either; pressure still sheds it
PRIORITIES = (BACKGROUND, URGENT)


@dataclass(frozen=True)
class Thresholds:
    """What decides the level (RES-003), and the concurrency caps (FR-013).

    The shares are fractions of this machine's idle headroom in use, so the
    same numbers are different amounts on different machines. Memory is
    judged more conservatively than CPU: running out of memory pages the
    machine and recovers slowly, a saturated CPU slows work and recovers at
    once. The guardrails are the only fixed lines, and they are emergencies.
    """

    # share of the idle headroom in use: available RAM and commit headroom
    ram_yellow: float = 0.40
    ram_orange: float = 0.60
    ram_red: float = 0.80
    # share of the idle CPU capacity in use, sustained over `cpu_window_s`
    cpu_window_s: float = 10.0
    cpu_yellow: float = 0.60
    cpu_orange: float = 0.80
    cpu_red: float = 0.95
    # available RAM falling: seconds left before it reaches the floor
    trend_window_s: float = 60.0
    trend_min_span_s: float = 10.0
    trend_min_samples: int = 3
    trend_orange_s: float = 60.0
    trend_yellow_s: float = 300.0
    # hysteresis: how long, and by how much, a better reading must hold
    dwell_s: float = 30.0
    release_margin: float = 0.05
    # emergency guardrails
    ram_critical: float = 95.0
    cpu_critical: float = 95.0
    ram_floor_gb: float = 0.5
    ram_floor_share: float = 0.03
    commit_floor_gb: float = 1.0
    commit_floor_share: float = 0.03
    disk_free_gb_low: float = 5.0
    disk_free_gb_critical: float = 1.0
    # concurrency caps
    max_workers: int = 2                   # FR-013 / NFR-P09 default
    max_browsers: int = 2
    max_queue: int = 8
    max_resident_models: int = 1           # local models held in memory at once
    # GPU memory in use: a local model that no longer fits is pressure
    gpu_memory_high: float = 90.0
    gpu_memory_critical: float = 98.0
    gpu_busy_high: float = 95.0
    # throughput as a share of the fastest link that is up: a saturated link
    # starves every network-bound worker, so optional work yields first
    network_busy_share: float = 0.90
    # a lease with no heartbeat for this long is presumed orphaned (section 55)
    heartbeat_stale_s: float = 120.0
    # the person is away once they have not touched the keyboard or mouse for
    # this long; background work waits for that (BG-001)
    user_idle_s: float = 120.0

    def ram_floor(self, total_gb: float | None) -> float:
        """The least available RAM that is not an emergency on this machine."""
        return max(self.ram_floor_gb, self.ram_floor_share * (total_gb or 0.0))

    def commit_floor(self, limit_gb: float | None) -> float:
        return max(self.commit_floor_gb, self.commit_floor_share * (limit_gb or 0.0))


@dataclass
class Sample:
    """One reading of the machine. `browser_processes` counts Chromium-family
    processes, which is what a runaway browser session looks like. A value
    the platform does not report is None, never guessed."""

    at: float
    cpu_percent: float
    ram_percent: float
    ram_available_gb: float
    disk_free_gb: float
    browser_processes: int
    friday_rss_mb: float
    ram_total_gb: float | None = None
    #: The commit charge's room below the commit limit (Windows): when it
    #: runs out, allocations fail whatever physical memory is free.
    commit_available_gb: float | None = None
    commit_limit_gb: float | None = None
    #: GPU memory in use (percent) on the busiest NVIDIA device, None when the
    #: machine has none or the tool is absent - never guessed.
    gpu_memory_percent: float | None = None
    gpu_percent: float | None = None
    #: Network throughput since the previous reading, megabits per second;
    #: None on the first reading.
    network_mbps: float | None = None
    #: Fastest link among the interfaces that are up, megabits per second.
    network_link_mbps: float | None = None

    def total_gb(self) -> float | None:
        if self.ram_total_gb:
            return self.ram_total_gb
        used = self.ram_percent / 100.0
        return self.ram_available_gb / (1.0 - used) if used < 1.0 else None

    def to_dict(self) -> dict:
        return {"at": self.at, "cpu_percent": self.cpu_percent,
                "ram_percent": self.ram_percent,
                "ram_available_gb": self.ram_available_gb,
                "ram_total_gb": self.ram_total_gb,
                "commit_available_gb": self.commit_available_gb,
                "commit_limit_gb": self.commit_limit_gb,
                "gpu_memory_percent": self.gpu_memory_percent,
                "gpu_percent": self.gpu_percent,
                "network_mbps": self.network_mbps,
                "network_link_mbps": self.network_link_mbps,
                "disk_free_gb": self.disk_free_gb,
                "browser_processes": self.browser_processes,
                "friday_rss_mb": self.friday_rss_mb}


_BROWSER_NAMES = ("chrome", "chromium", "msedge", "brave", "firefox", "playwright")

#: psutil measures CPU since its previous call, and a window of a few
#: milliseconds is noise: a reading taken 20 ms after the last one said 100%
#: on a quiet machine (V7-D57). A sooner call reuses the last reading, so the
#: next one covers the whole time since.
CPU_MIN_WINDOW_S = 1.0
_cpu_last: tuple[float, float] | None = None       # (monotonic time, percent)
_cpu_lock = threading.Lock()


def _cpu_percent(interval: float = 0.0) -> float:
    global _cpu_last
    import psutil

    with _cpu_lock:
        now = time.monotonic()
        if not interval and _cpu_last is not None and now - _cpu_last[0] < CPU_MIN_WINDOW_S:
            return _cpu_last[1]
        value = float(psutil.cpu_percent(interval=interval or None))
        _cpu_last = (time.monotonic(), value)
        return value


def _commit() -> tuple[float | None, float | None]:
    """(room below the commit limit, the limit) in GB where the platform
    reports them - Windows, through GetPerformanceInfo - else (None, None)."""
    if sys.platform != "win32":
        return None, None
    import ctypes

    size_t, dword = ctypes.c_size_t, ctypes.c_uint32

    class PerformanceInformation(ctypes.Structure):
        _fields_ = [("cb", dword), ("CommitTotal", size_t), ("CommitLimit", size_t),
                    ("CommitPeak", size_t), ("PhysicalTotal", size_t),
                    ("PhysicalAvailable", size_t), ("SystemCache", size_t),
                    ("KernelTotal", size_t), ("KernelPaged", size_t),
                    ("KernelNonpaged", size_t), ("PageSize", size_t),
                    ("HandleCount", dword), ("ProcessCount", dword), ("ThreadCount", dword)]

    info = PerformanceInformation()
    info.cb = ctypes.sizeof(info)
    if not ctypes.windll.psapi.GetPerformanceInfo(ctypes.byref(info), info.cb):
        return None, None
    page_gb = info.PageSize / 1024**3
    return (info.CommitLimit - info.CommitTotal) * page_gb, info.CommitLimit * page_gb


_net_last: tuple[float, int] | None = None         # (monotonic time, bytes sent+received)
_net_lock = threading.Lock()


def _network_mbps() -> float | None:
    """Throughput since the previous call; None on the first call or where
    the platform reports no counters."""
    global _net_last
    import psutil

    counters = psutil.net_io_counters()
    if counters is None:
        return None
    now, total = time.monotonic(), counters.bytes_sent + counters.bytes_recv
    with _net_lock:
        last, _net_last = _net_last, (now, total)
    if last is None or now - last[0] <= 0 or total < last[1]:
        return None
    return (total - last[1]) * 8 / 1e6 / (now - last[0])


def _link_mbps() -> float | None:
    """Speed of the fastest interface that is up, None where unreported."""
    import psutil

    try:
        stats = psutil.net_if_stats()
    except OSError:
        return None
    speeds = [s.speed for s in stats.values() if s.isup and s.speed > 0]
    return float(max(speeds)) if speeds else None


def _gpu() -> tuple[float | None, float | None]:
    """(memory percent, utilisation percent) of the busiest NVIDIA device via
    nvidia-smi, else (None, None). A machine without one is not an error."""
    import shutil
    import subprocess

    exe = shutil.which("nvidia-smi")
    if not exe:
        return None, None
    try:
        out = subprocess.run(
            [exe, "--query-gpu=memory.used,memory.total,utilization.gpu",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=3, check=True).stdout
    except (OSError, subprocess.SubprocessError) as exc:
        logger.warning("gpu unreadable: %s", exc)
        return None, None
    mem, util = None, None
    for line in out.strip().splitlines():
        try:
            used, total, busy = (float(x) for x in line.split(","))
        except ValueError:
            continue
        if total > 0:
            mem = max(mem or 0.0, used / total * 100.0)
        util = max(util or 0.0, busy)
    return mem, util


def sample_machine(*, cpu_interval: float = 0.0) -> Sample:
    """Measure. `cpu_interval=0` returns the CPU since the last reading,
    which is what a periodic sampler wants - at least `CPU_MIN_WINDOW_S` of
    it; a first call reports 0.0."""
    import psutil
    from pathlib import Path

    cpu = _cpu_percent(cpu_interval)
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
    try:
        commit_available, commit_limit = _commit()
    except OSError as exc:
        logger.warning("commit charge unreadable: %s", exc)
        commit_available, commit_limit = None, None
    gpu_memory, gpu_busy = _gpu()
    return Sample(gpu_memory_percent=gpu_memory, gpu_percent=gpu_busy,
                  network_mbps=_network_mbps(), network_link_mbps=_link_mbps(),
at=time.time(), cpu_percent=cpu,
                  ram_percent=float(mem.percent),
                  ram_available_gb=mem.available / 1024**3,
                  disk_free_gb=disk_free, browser_processes=browsers,
                  friday_rss_mb=rss, ram_total_gb=mem.total / 1024**3,
                  commit_available_gb=commit_available, commit_limit_gb=commit_limit)


# -- the person and the power (BG-001) ---------------------------------------


@dataclass(frozen=True)
class Context:
    """What the load cannot say: whether the person is at the machine and
    whether it runs on battery. None means the platform cannot tell, and an
    unknown never defers work - the pressure levels still apply."""

    user_idle_s: float | None = None      # seconds since the last keyboard/mouse input
    on_battery: bool | None = None        # False on mains, None with no battery to report

    def deferral(self, t: Thresholds) -> str:
        """Why background work should wait now; empty when nothing says so."""
        if self.user_idle_s is not None and self.user_idle_s < t.user_idle_s:
            return (f"the person is active (last input {self.user_idle_s:.0f}s ago, "
                    f"away after {t.user_idle_s:.0f}s)")
        if self.on_battery:
            return "the machine is on battery"
        return ""

    def to_dict(self) -> dict:
        return {"user_idle_s": self.user_idle_s, "on_battery": self.on_battery}


class _LastInputInfo(ctypes.Structure):
    _fields_ = [("cbSize", ctypes.c_uint), ("dwTime", ctypes.c_uint)]


def _idle_seconds_since(*, now: int, last: int) -> float:
    """Windows tick counts are 32-bit milliseconds that wrap every 49.7
    days; the masked difference is right across the wrap."""
    return ((now - last) & 0xFFFFFFFF) / 1000.0


def _user_idle_s() -> float | None:
    """Seconds since the person last touched the keyboard or mouse; None
    where the platform cannot say (only Windows reports it)."""
    if sys.platform != "win32":
        return None
    info = _LastInputInfo(cbSize=ctypes.sizeof(_LastInputInfo))
    try:
        if not ctypes.windll.user32.GetLastInputInfo(ctypes.byref(info)):
            return None
        ctypes.windll.kernel32.GetTickCount.restype = ctypes.c_uint
        now = ctypes.windll.kernel32.GetTickCount()
    except (OSError, AttributeError) as exc:
        logger.warning("last input time unreadable: %s", exc)
        return None
    return _idle_seconds_since(now=now, last=info.dwTime)


def _on_battery() -> bool | None:
    """True on battery, False on mains, None with no battery to report."""
    import psutil

    read = getattr(psutil, "sensors_battery", None)
    battery = read() if read else None
    if battery is None or battery.power_plugged is None:
        return None
    return not battery.power_plugged


def sample_context() -> Context:
    return Context(user_idle_s=_user_idle_s(), on_battery=_on_battery())


@dataclass(frozen=True)
class Baseline:
    """This machine at rest: what its operating system and the person's own
    programs hold while FRIDAY holds no leased work (RES-003). Medians, so
    one busy moment does not move it. Provisional until a hardware
    qualification has measured it (RES-002)."""

    ram_available_gb: float
    cpu_percent: float
    commit_available_gb: float | None = None
    readings: int = 1
    provisional: bool = True

    @classmethod
    def of(cls, samples, *, provisional: bool) -> "Baseline":
        samples = list(samples)
        commits = [s.commit_available_gb for s in samples if s.commit_available_gb is not None]
        return cls(ram_available_gb=statistics.median(s.ram_available_gb for s in samples),
                   cpu_percent=statistics.median(s.cpu_percent for s in samples),
                   commit_available_gb=statistics.median(commits) if commits else None,
                   readings=len(samples), provisional=provisional)

    def to_dict(self) -> dict:
        return {"ram_available_gb": self.ram_available_gb, "cpu_percent": self.cpu_percent,
                "commit_available_gb": self.commit_available_gb,
                "readings": self.readings, "provisional": self.provisional}


@dataclass
class Pressure:
    level: str
    reasons: list[str] = field(default_factory=list)
    sample: Sample | None = None
    #: The level the reading alone supports, before hysteresis.
    raw: str = ""
    baseline: Baseline | None = None

    def to_dict(self) -> dict:
        return {"level": self.level, "state": STATES[self.level],
                "raw": self.raw or self.level, "reasons": list(self.reasons),
                "sample": self.sample.to_dict() if self.sample else None,
                "baseline": self.baseline.to_dict() if self.baseline else None}


def _consumed(idle_free: float | None, free_now: float | None) -> float:
    """The share of the idle headroom in use; 0 when there is none to judge
    against (the guardrails decide then)."""
    if not idle_free or idle_free <= 0 or free_now is None:
        return 0.0
    return max(0.0, (idle_free - free_now) / idle_free)


def _falling_gb_per_s(recent, t: Thresholds) -> float | None:
    """How fast available RAM is falling across the recent readings (a
    least-squares slope, positive when falling), or None when there are too
    few readings, or too short a span, to say."""
    points = [(s.at, s.ram_available_gb) for s in recent]
    if len(points) < t.trend_min_samples or points[-1][0] - points[0][0] < t.trend_min_span_s:
        return None
    n = len(points)
    mean_t = sum(a for a, _ in points) / n
    mean_v = sum(v for _, v in points) / n
    spread = sum((a - mean_t) ** 2 for a, _ in points)
    if spread <= 0:
        return None
    return -sum((a - mean_t) * (v - mean_v) for a, v in points) / spread


def _sustained_cpu(sample: Sample, recent, t: Thresholds) -> float:
    """CPU over the last `cpu_window_s` of readings. Each reading covers the
    time since the one before it, so readings are weighted by that time; a
    reading with nothing before it is taken as it is. A program starting or a
    page loading is a spike, not pressure; a load that lasts is."""
    readings = list(recent) if recent and recent[-1] is sample else [sample]
    weighted = covered = 0.0
    for before, reading in zip(readings, readings[1:]):
        span = reading.at - before.at
        if span > 0 and sample.at - reading.at <= t.cpu_window_s:
            weighted += reading.cpu_percent * span
            covered += span
    return weighted / covered if covered else sample.cpu_percent


def assess(sample: Sample, thresholds: Thresholds = Thresholds(), *,
           baseline: Baseline | None = None, recent=(), margin: float = 0.0) -> Pressure:
    """The level one reading supports, judged against the machine's baseline
    and the readings just before it (RES-003). Pure; unit-testable.

    Without a baseline the reading is its own: nothing counts as consumed,
    so only the trend and the guardrails can raise the level. `margin`
    lowers the share lines, which is how a release is judged (`Tracker`)."""
    t = thresholds
    base = baseline or Baseline.of([sample], provisional=True)
    reasons: list[str] = []
    level = NORMAL

    def bump(to: str, why: str) -> None:
        nonlocal level
        reasons.append(why)
        if LEVELS.index(to) > LEVELS.index(level):
            level = to

    def by_share(share: float, yellow: float, orange: float, red: float) -> str:
        if share >= red - margin:
            return CRITICAL
        if share >= orange - margin:
            return HIGH
        if share >= yellow - margin:
            return ELEVATED
        return NORMAL

    # Headroom against this machine's baseline: the primary classifier.
    share = _consumed(base.ram_available_gb, sample.ram_available_gb)
    found = by_share(share, t.ram_yellow, t.ram_orange, t.ram_red)
    if found != NORMAL:
        bump(found, f"RAM {sample.ram_percent:.0f}% used, {share:.0%} of this machine's idle "
                    f"headroom ({sample.ram_available_gb:.1f} of {base.ram_available_gb:.1f} GB left)")
    if sample.commit_available_gb is not None:
        share = _consumed(base.commit_available_gb, sample.commit_available_gb)
        found = by_share(share, t.ram_yellow, t.ram_orange, t.ram_red)
        if found != NORMAL:
            bump(found, f"commit charge using {share:.0%} of its idle headroom "
                        f"({sample.commit_available_gb:.1f} GB below the limit)")
    cpu = _sustained_cpu(sample, recent, t)
    share = _consumed(100.0 - base.cpu_percent, 100.0 - cpu)
    found = by_share(share, t.cpu_yellow, t.cpu_orange, t.cpu_red)
    if found != NORMAL:
        bump(found, f"CPU {cpu:.0f}%, {share:.0%} of the idle capacity in use")

    # The trend: memory going fast is pressure before any line is crossed.
    floor = t.ram_floor(sample.total_gb())
    falling = _falling_gb_per_s(recent, t)
    if falling and falling > 0:
        left = max(0.0, sample.ram_available_gb - floor) / falling
        if left <= t.trend_orange_s:
            bump(HIGH, f"available RAM falling {falling * 60:.2f} GB/min: the floor in {left:.0f} s")
        elif left <= t.trend_yellow_s:
            bump(ELEVATED, f"available RAM falling {falling * 60:.2f} GB/min: the floor in {left:.0f} s")

    if sample.gpu_memory_percent is not None:
        if sample.gpu_memory_percent >= t.gpu_memory_critical:
            bump(CRITICAL, f"GPU memory {sample.gpu_memory_percent:.0f}% used")
        elif sample.gpu_memory_percent >= t.gpu_memory_high:
            bump(HIGH, f"GPU memory {sample.gpu_memory_percent:.0f}% used")

    if sample.gpu_percent is not None and sample.gpu_percent >= t.gpu_busy_high:
        bump(ELEVATED, f"GPU {sample.gpu_percent:.0f}% busy")
    if sample.network_mbps is not None and sample.network_link_mbps:
        if sample.network_mbps / sample.network_link_mbps >= t.network_busy_share:
            bump(ELEVATED, f"network {sample.network_mbps:.0f} of "
                           f"{sample.network_link_mbps:.0f} Mb/s in use")

    # Emergency guardrails: the only fixed lines.
    if sample.ram_percent >= t.ram_critical:
        bump(CRITICAL, f"RAM {sample.ram_percent:.0f}% used")
    if sample.ram_available_gb < floor:
        bump(CRITICAL, f"only {sample.ram_available_gb:.2f} GB RAM available")
    if (sample.commit_available_gb is not None
            and sample.commit_available_gb < t.commit_floor(sample.commit_limit_gb)):
        bump(CRITICAL, f"only {sample.commit_available_gb:.2f} GB left below the commit limit")
    if cpu >= t.cpu_critical:
        bump(CRITICAL, f"CPU {cpu:.0f}%")
    if sample.disk_free_gb <= t.disk_free_gb_critical:
        bump(CRITICAL, f"disk {sample.disk_free_gb:.1f} GB free")
    elif sample.disk_free_gb <= t.disk_free_gb_low:
        bump(HIGH, f"disk {sample.disk_free_gb:.1f} GB free")
    return Pressure(level=level, reasons=reasons, sample=sample, raw=level, baseline=baseline)


class Tracker:
    """What one reading cannot show (RES-003): the machine's baseline, the
    readings just before this one, and how long a better reading has held.

    A worse level is entered at once - the governor errs toward protecting
    the machine. A better one is entered only when the reading, judged with
    the share lines lowered by `release_margin`, has supported it for
    `dwell_s` of sample time; the level then falls to the worst level the
    readings supported during that wait. Without a baseline the first reading
    stands in, provisionally, and readings taken while FRIDAY holds no leased
    work refine it (their median) until a qualified one replaces it.

    Not locked on its own: the Governor holds its lock around `observe`."""

    def __init__(self, thresholds: Thresholds, baseline: Baseline | None = None, *,
                 idle_spacing_s: float = 10.0, idle_keep: int = 720) -> None:
        self.thresholds = thresholds
        self.baseline = baseline
        self.level = NORMAL
        self._recent: deque[Sample] = deque(maxlen=512)
        self._idle: deque[Sample] = deque(maxlen=idle_keep)
        self._idle_spacing_s = idle_spacing_s
        self._calm_since: float | None = None
        self._calm_level = NORMAL

    def _learn(self, sample: Sample, idle: bool) -> None:
        if self.baseline is not None and not self.baseline.provisional:
            return
        if self.baseline is None:
            self._idle.append(sample)
        elif idle and (not self._idle or sample.at - self._idle[-1].at >= self._idle_spacing_s):
            self._idle.append(sample)
        else:
            return
        self.baseline = Baseline.of(self._idle, provisional=True)

    def observe(self, sample: Sample, *, idle: bool) -> Pressure:
        t = self.thresholds
        self._learn(sample, idle)
        self._recent.append(sample)
        while sample.at - self._recent[0].at > t.trend_window_s:
            self._recent.popleft()
        raw = assess(sample, t, baseline=self.baseline, recent=self._recent)
        rank = LEVELS.index
        if rank(raw.level) >= rank(self.level):
            self.level, self._calm_since = raw.level, None
            return raw
        release = assess(sample, t, baseline=self.baseline, recent=self._recent,
                         margin=t.release_margin).level
        if rank(release) >= rank(self.level):
            self._calm_since = None
        elif self._calm_since is None:
            self._calm_since, self._calm_level = sample.at, release
        else:
            self._calm_level = max(self._calm_level, release, key=rank)
            if sample.at - self._calm_since >= t.dwell_s:
                self.level, self._calm_since = self._calm_level, None
        reasons = list(raw.reasons)
        if self.level != raw.level:
            reasons.append(f"holding {STATES[self.level]} until a better reading has lasted "
                           f"{t.dwell_s:.0f} s")
        return Pressure(level=self.level, reasons=reasons, sample=sample, raw=raw.level,
                        baseline=self.baseline)


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


@dataclass
class OwnedProcess:
    """Section 55's record for work the governor admitted: who owns it, for
    which objective, under which lease, when it last proved it is alive, by
    when it must be done, and how to clean it up. `cleanup` is what the
    governor calls to reduce or reap the work; without one the governor can
    only report it."""

    lease: str
    kind: str
    owner: str
    objective_id: str
    label: str
    pid: int | None
    started: float
    heartbeat: float
    deadline: float | None
    cleanup: object = None            # Callable[[], None] | None
    #: Set by the first `heartbeat()`: a lease whose owner never reports in
    #: has no heartbeat to go stale, only a deadline.
    beats: bool = False
    #: The priority it was admitted at: BACKGROUND optional work is also
    #: stopped when the person returns or the power goes to battery.
    priority: str = BACKGROUND

    def to_dict(self, now: float | None = None) -> dict:
        return {"lease": self.lease, "kind": self.kind, "owner": self.owner,
                "objective_id": self.objective_id, "label": self.label,
                "priority": self.priority,
                "pid": self.pid, "started": self.started,
                "heartbeat": self.heartbeat, "deadline": self.deadline,
                "cleanup": bool(self.cleanup)}


class Governor:
    """The admission gate. One per process (`governor()` below).

    `sampler` is injectable so tests drive pressure deterministically; the
    default samples the real machine, cached for `sample_ttl_s` so a burst
    of admissions does not spend a CPU probe each. `baseline` is the
    machine's measured idle state; without one the governor profiles the
    machine from its own readings (provisional). `context` reads the person's
    activity and the power state (BG-001); without one neither can defer work.
    """

    def __init__(self, *, thresholds: Thresholds | None = None,
                 sampler=None, sample_ttl_s: float = 2.0,
                 baseline: Baseline | None = None, context=None) -> None:
        self.thresholds = thresholds or Thresholds()
        self._sampler = sampler or sample_machine
        self._context = context
        self.sample_ttl_s = sample_ttl_s
        self._lock = threading.Lock()
        self._leases: dict[str, tuple[str, float, str]] = {}   # lease -> (kind, at, label)
        self._queue: list[tuple[str, str, float]] = []           # (kind, label, at)
        self._tracker = Tracker(self.thresholds, baseline)
        self._last: Pressure | None = None
        self._last_at = 0.0
        self._next = 0
        self.decisions: list[Decision] = []                      # bounded history
        self._parallel_justified: set[str] = set()
        self._owned: dict[str, OwnedProcess] = {}
        self._unreapable: set[str] = set()
        self.reductions: list[dict] = []                         # bounded history
        self._observers: list = []                               # called with each fresh Pressure

    # -- measurement --------------------------------------------------------

    @property
    def baseline(self) -> Baseline | None:
        with self._lock:
            return self._tracker.baseline

    def rebaseline(self, baseline: Baseline, *, thresholds: Thresholds | None = None) -> None:
        """Adopt a newly measured baseline (a hardware qualification), and the
        caps derived with it."""
        with self._lock:
            self._tracker.baseline = baseline
            if thresholds is not None:
                self.thresholds = self._tracker.thresholds = thresholds
            self._last = None

    def context(self) -> Context:
        """The person's activity and the power state now; unknown (nothing
        deferred) when the governor has no way to read them. A probe that
        raises is unknown too: `pressure()` reads this on every sample."""
        if not self._context:
            return Context()
        try:
            return self._context()
        except Exception as exc:  # noqa: BLE001 - an unreadable context is unknown, never a crash
            logger.warning("governor context probe failed: %s", exc)
            return Context()

    def background_wait(self, p: Pressure | None = None, ctx: Context | None = None) -> str:
        """Why background work should not start now; empty when it may run:
        the machine has no headroom to spare, the person is active, or it is
        on battery. Headroom is judged first so a pressured machine does not
        spend a context probe; a caller that already read `p` or `ctx` passes
        it so one decision rests on one reading."""
        p = self.pressure() if p is None else p
        if p.level != NORMAL:
            why = ", ".join(p.reasons) or "no headroom"
            return f"the machine has no headroom to spare ({p.level} pressure: {why})"
        return (self.context() if ctx is None else ctx).deferral(self.thresholds)

    def pressure(self, *, fresh: bool = False) -> Pressure:
        now = time.time()
        with self._lock:
            if not fresh and self._last and now - self._last_at < self.sample_ttl_s:
                return self._last
        try:
            sample = self._sampler()
            with self._lock:
                p = self._tracker.observe(sample, idle=not self._leases)
        except Exception as exc:  # noqa: BLE001 - a failed probe is ELEVATED, never a crash
            logger.warning("governor sample failed: %s", exc)
            p = Pressure(level=ELEVATED, reasons=[f"sample failed: {exc}"])
        with self._lock:
            self._last, self._last_at = p, now
        self.reduce_running(p)
        for observer in list(self._observers):       # a snapshot, called outside the lock
            try:
                observer(p)
            except Exception as exc:  # noqa: BLE001 - an observer's failure never costs the caller its reading
                logger.warning("governor observer %r failed: %s", observer, exc)
        return p

    def add_observer(self, observer) -> None:
        """Call `observer(pressure)` after every fresh reading, once the
        governor's own reductions have run. This is how a holder of resident
        work that the governor evicts only at HIGH (the local expert pool,
        MODEL-005) acts at the levels below it. Cached reads do not call it."""
        with self._lock:
            if observer not in self._observers:
                self._observers.append(observer)

    def remove_observer(self, observer) -> None:
        with self._lock:
            if observer in self._observers:
                self._observers.remove(observer)

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

    def owned_processes(self) -> list[dict]:
        """Section 55's record for every lease in flight."""
        with self._lock:
            return [o.to_dict() for o in self._owned.values()]

    def heartbeat(self, lease: str) -> bool:
        """The owner says the work is alive. False for a lease the governor
        no longer holds (reaped or released) - the owner should stop."""
        with self._lock:
            owned = self._owned.get(lease)
            if owned is None:
                return False
            owned.heartbeat = time.time()
            owned.beats = True
            return True

    def attach(self, lease: str, *, pid: int | None = None, cleanup=None,
               deadline_s: float | None = None) -> bool:
        """Complete the owned-process record once the work exists: a worker's
        pid and how to stop it are known only after it starts, after admission.
        False for a lease the governor no longer holds.

        `cleanup` runs inside `reduce_running`, which runs on every
        `pressure()` and `admit()`: it must not block. A stop that needs I/O
        starts it on its own thread and returns."""
        with self._lock:
            owned = self._owned.get(lease)
            if owned is None:
                return False
            if pid is not None:
                owned.pid = pid
            if cleanup is not None:
                owned.cleanup = cleanup
            if deadline_s is not None:
                owned.deadline = time.time() + deadline_s
            return True

    def reduce_running(self, p: Pressure | None = None) -> list[dict]:
        """Under pressure, reduce work already running (not just refuse new
        work): optional work is stopped at ELEVATED and above, a resident
        model at HIGH and above; work past its deadline or without a heartbeat
        is reaped at any level. BACKGROUND optional work is also stopped while
        the person is active or the machine is on battery (BG-001), as
        admission defers it; URGENT work keeps running through both. Each
        victim's `cleanup` runs outside the lock; a victim with no cleanup is
        reported, not released, because the governor cannot stop what it was
        not told how to stop."""
        p = p or self.pressure()
        now = time.time()
        stale = self.thresholds.heartbeat_stale_s
        victims: list[tuple[OwnedProcess, str]] = []
        with self._lock:
            watching = any(o.kind == OPTIONAL and o.priority == BACKGROUND
                           and o.lease not in self._unreapable
                           for o in self._owned.values())
        # Read outside the lock, and only when such work runs: a probe per reading
        # that nothing depends on is waste.
        wait = self.context().deferral(self.thresholds) if watching else ""
        with self._lock:
            # Workers and browsers beyond the one allowed at HIGH are stopped
            # newest first; the oldest keeps running, as admission does.
            surplus: set[str] = set()
            if p.level in (HIGH, CRITICAL):
                for kind in (WORKER, BROWSER):
                    running = sorted((o for o in self._owned.values() if o.kind == kind),
                                     key=lambda o: o.started)
                    surplus.update(o.lease for o in running[1:])
            for o in self._owned.values():
                if o.lease in self._unreapable:
                    continue
                if o.deadline is not None and now > o.deadline:
                    victims.append((o, "past its deadline"))
                elif o.beats and now - o.heartbeat > stale:
                    victims.append((o, f"no heartbeat for {now - o.heartbeat:.0f}s"))
                elif o.kind == OPTIONAL and p.level != NORMAL:
                    victims.append((o, f"optional work stopped under {p.level} pressure"))
                elif o.kind == OPTIONAL and o.priority == BACKGROUND and wait:
                    victims.append((o, f"optional work stopped: {wait}"))
                elif o.kind == MODEL and p.level in (HIGH, CRITICAL):
                    victims.append((o, f"resident model evicted under {p.level} pressure"))
                elif o.lease in surplus:
                    victims.append((o, f"{o.kind} stopped: concurrency reduced to 1 "
                                       f"under {p.level} pressure"))
        done: list[dict] = []
        for o, why in victims:
            ok = True
            if o.cleanup is not None:
                try:
                    o.cleanup()
                except Exception as exc:  # noqa: BLE001 - a failed cleanup is recorded, the lease kept
                    logger.warning("cleanup of %s failed: %s", o.lease, exc)
                    ok, why = False, f"{why}; cleanup failed: {exc}"
            else:
                ok, why = False, f"{why}; no cleanup registered"
            if ok:
                self.release(o.lease, retire_queue=False)
            else:
                with self._lock:
                    self._unreapable.add(o.lease)     # reported once, not on every reading
            record = {"lease": o.lease, "kind": o.kind, "owner": o.owner,
                      "objective_id": o.objective_id, "reason": why,
                      "released": ok, "at": now}
            done.append(record)
            with self._lock:
                self.reductions.append(record)
                del self.reductions[:-200]
        return done

    def justify_parallel(self, objective_id: str) -> None:
        """FR-013: concurrency scales only for demonstrably parallel work.
        A caller that has a plan with independent streams says so; this
        lifts the worker cap by one for that objective's admissions."""
        with self._lock:
            self._parallel_justified.add(objective_id)

    def worker_capacity(self, objective_id: str = "") -> int:
        """How many workers `admit(WORKER, objective_id=...)` would let run at
        once right now: the cap (one more once `justify_parallel` was called
        for this objective), one under HIGH pressure, none under CRITICAL.
        A scheduler bounds how many tasks it starts together by this; `admit`
        stays the gate on each worker that really starts."""
        level = self.pressure().level
        if level == CRITICAL:
            return 0
        with self._lock:
            cap = self.thresholds.max_workers + (
                1 if objective_id in self._parallel_justified else 0)
        return min(cap, 1) if level == HIGH else cap

    # -- admission ----------------------------------------------------------

    def admit(self, kind: str, *, label: str = "", objective_id: str = "",
              owner: str = "", pid: int | None = None,
              deadline_s: float | None = None, cleanup=None,
              priority: str = BACKGROUND) -> Decision:
        """`owner`, `pid`, `deadline_s` (seconds from now) and `cleanup` fill
        section 55's owned-process record for an admitted lease; a caller
        that omits `owner` is recorded under its label. `priority` is that of
        the objective the work serves: optional work at BACKGROUND (the
        default) is also deferred while the person is active or the machine
        is on battery; URGENT skips those two, never the pressure gate."""
        if kind not in KINDS:
            raise ValueError(f"unknown work kind {kind!r}; known: {KINDS}")
        if priority not in PRIORITIES:
            raise ValueError(f"unknown priority {priority!r}; known: {PRIORITIES}")
        p = self.pressure()
        t = self.thresholds
        wait = ""
        if kind == OPTIONAL and priority == BACKGROUND:
            wait = self.background_wait(p)
        with self._lock:
            workers = sum(1 for k, _, _ in self._leases.values() if k == WORKER)
            browsers = sum(1 for k, _, _ in self._leases.values() if k == BROWSER)
            models = sum(1 for k, _, _ in self._leases.values() if k == MODEL)
            queued = len(self._queue)
            cap = t.max_workers + (1 if objective_id in self._parallel_justified else 0)

            def out(decision: str, reason: str) -> Decision:
                lease = ""
                if decision == ADMIT:
                    self._next += 1
                    lease = f"lease-{self._next}"
                    now = time.time()
                    self._leases[lease] = (kind, now, label)
                    self._owned[lease] = OwnedProcess(
                        lease=lease, kind=kind, owner=owner or label,
                        objective_id=objective_id, label=label, pid=pid,
                        started=now, heartbeat=now,
                        deadline=None if deadline_s is None else now + deadline_s,
                        cleanup=cleanup, priority=priority)
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
                if wait:
                    return out(SHED, f"optional work deferred: {wait}")
                return out(ADMIT, "optional work admitted")
            if p.level == CRITICAL:
                return out(SHED, f"CRITICAL pressure: {why}; no new {kind} started")
            if kind == MODEL:
                if p.level == HIGH:
                    return out(SHED, f"HIGH pressure ({why}): no new local model loaded")
                if models >= t.max_resident_models:
                    return out(QUEUE, f"resident model cap {t.max_resident_models} reached")
                return out(ADMIT, "model admitted")
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

    def release(self, lease: str, *, retire_queue: bool = True) -> None:
        with self._lock:
            held = self._leases.pop(lease, None) is not None
            self._owned.pop(lease, None)
            self._unreapable.discard(lease)
            # Capacity returned: the queue is a record of what waited, not a
            # scheduler - callers re-run admit() themselves - so the oldest
            # waiting entry is retired here to keep the depth truthful.
            # Only when this call returned capacity: the owner's own release
            # after the governor stopped its work is a second release of a
            # lease already gone and must not retire another caller's entry.
            if held and retire_queue and self._queue:
                self._queue.pop(0)

    # -- reporting ----------------------------------------------------------

    def status(self) -> dict:
        p = self.pressure()
        ctx = self.context()
        wait = self.background_wait(p, ctx)
        return {
            "pressure": p.to_dict(),
            "context": ctx.to_dict(),
            "background": {"runs": not wait, "wait": wait},
            "active": self.active_leases(),
            "workers": self.active(WORKER),
            "browsers": self.active(BROWSER),
            "queued": self.queue_depth(),
            "models": self.active(MODEL),
            "owned": self.owned_processes(),
            "reductions": list(self.reductions[-10:]),
            "caps": {"models": self.thresholds.max_resident_models,
"workers": self.thresholds.max_workers,
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
    """The process's governor, judging against this machine's stored profile
    (RES-002) when there is one that matches it."""
    global _GOVERNOR
    with _LOCK:
        if _GOVERNOR is None:
            from friday import hardware_profile
            baseline, thresholds = hardware_profile.for_governor()
            _GOVERNOR = Governor(thresholds=thresholds, baseline=baseline,
                                 context=sample_context)
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
