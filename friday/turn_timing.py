"""
friday/turn_timing.py -- where a slow turn's time went, and what to say about it.

The owner's rule: "if latency occurs, the program should report the cause
while preserving output quality". A number alone does not do that - "the turn
took 14s" is a complaint, "the screen read took 11 of 14s" is a cause. So a
turn is timed in named stages and the slowest stage is the attribution.

This is deliberately tiny and dependency-free so both conversational paths
(the LiveKit agent and the browser brain) can use the same one. It never
changes what is said; it adds a `latency` block to the response meta, and a
one-line `latency_note` the model may voice when the turn was slow enough for
the owner to have noticed.

Machine-side causes are separated from provider-side ones because the fix is
different: 98% CPU from a Java process is not something the model can reason
its way out of, and the note should say so rather than blame the model.
"""
from __future__ import annotations

import os
import time
from dataclasses import dataclass, field

#: A turn slower than this gets a spoken cause. Voice tolerates ~2s; beyond
#: this the boss has started wondering.
SLOW_TURN_SECONDS = float(os.getenv("FRIDAY_SLOW_TURN_SECONDS", "4.0"))

#: Host load above which the machine, not the provider, is named as the cause.
HOST_LOAD_CPU = float(os.getenv("FRIDAY_HOST_LOAD_CPU", "90"))
HOST_LOAD_RAM = float(os.getenv("FRIDAY_HOST_LOAD_RAM", "92"))

#: Stage name -> how to say it aloud.
SPOKEN = {
    "history": "reading our recent conversation",
    "memory": "recalling what I know",
    "model": "thinking",
    "tool": "running a capability",
    "stt": "hearing you",
    "tts": "finding my voice",
    "screen": "reading the screen",
    "web": "waiting on the web",
    "hermes": "waiting on Hermes",
}


@dataclass
class TurnTimer:
    """Accumulates named stages for one turn."""

    started: float = field(default_factory=time.monotonic)
    stages: dict = field(default_factory=dict)
    _open: dict = field(default_factory=dict)

    def start(self, stage: str) -> None:
        self._open[stage] = time.monotonic()

    def stop(self, stage: str) -> None:
        t0 = self._open.pop(stage, None)
        if t0 is not None:
            self.stages[stage] = self.stages.get(stage, 0.0) + (time.monotonic() - t0)

    def add(self, stage: str, seconds: float) -> None:
        self.stages[stage] = self.stages.get(stage, 0.0) + max(0.0, seconds)

    def total(self) -> float:
        return time.monotonic() - self.started

    def report(self, *, host: dict | None = None) -> dict:
        """The attribution. `host` is {"cpu": %, "ram": %} when known."""
        total = self.total()
        stages = {k: round(v, 2) for k, v in self.stages.items()}
        slowest = max(stages, key=stages.get) if stages else ""
        host = host if host is not None else host_load()
        loaded = bool(host) and (host.get("cpu", 0) >= HOST_LOAD_CPU
                                 or host.get("ram", 0) >= HOST_LOAD_RAM)
        out = {"total_s": round(total, 2), "stages_s": stages,
               "slowest": slowest, "slow": total >= SLOW_TURN_SECONDS,
               "host": host, "host_loaded": loaded, "note": ""}
        if out["slow"]:
            out["note"] = _note(total, slowest, stages.get(slowest, 0.0), host, loaded)
        return out


def _note(total: float, slowest: str, seconds: float, host: dict, loaded: bool) -> str:
    """One spoken line naming the cause. Never blames without a number."""
    if loaded:
        return (f"That took {total:.0f} seconds - this machine is at "
                f"{host.get('cpu', 0):.0f}% CPU and {host.get('ram', 0):.0f}% "
                f"memory, so everything is slower than I am.")
    if slowest and seconds >= total * 0.5:
        return (f"That took {total:.0f} seconds; most of it was "
                f"{SPOKEN.get(slowest, slowest)}.")
    return f"That took {total:.0f} seconds across several steps."


def host_load() -> dict:
    """CPU and RAM percent, cheaply. Empty when psutil is unavailable."""
    try:
        import psutil
        return {"cpu": float(psutil.cpu_percent(interval=None)),
                "ram": float(psutil.virtual_memory().percent)}
    except Exception:  # noqa: BLE001
        return {}
