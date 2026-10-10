"""
Telemetry plane (PRD OTEL-001): local spans in the OpenTelemetry GenAI
semantic conventions, with no OpenTelemetry SDK behind them.

  * Spans nest into a tree per run (a contextvar carries the parent), so the
    wall time of a run can be attributed per component: a span's self time is
    its duration minus its children's, summed by `friday.component`.
  * Attribute names follow the GenAI conventions (`gen_ai.operation.name`,
    `gen_ai.request.model`, `gen_ai.usage.input_tokens`, ...).
  * Metadata is on and local by default. Content (prompts, completions, tool
    arguments and results) is OFF by default and is dropped, not masked, until
    the recorder is configured with `capture_content=True`
    (`FRIDAY_TELEMETRY_CONTENT=1`).
  * Every attribute value, and every error message, passes
    `friday.observability.redact` first, so a secret never reaches a span,
    with content capture on or off.
  * Export is a local JSONL file the caller names (`path=`, or
    `FRIDAY_TELEMETRY_FILE`). Nothing leaves the machine.

Telemetry observes a run and never changes it: a failed export is logged and
the run carries on.
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
from collections import deque
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from pathlib import Path

from friday.observability import redact

logger = logging.getLogger(__name__)

COMPONENT = "friday.component"
OK, ERROR = "OK", "ERROR"

# GenAI-convention attributes that carry what was said or done, not metadata.
CONTENT_ATTRIBUTES = frozenset({
    "gen_ai.prompt", "gen_ai.completion",
    "gen_ai.system_instructions",
    "gen_ai.input.messages", "gen_ai.output.messages",
    "gen_ai.tool.call.arguments", "gen_ai.tool.call.result",
})
_MAX_VALUE_CHARS = 2000
_KEEP_SPANS = 2000


@dataclass
class Span:
    name: str
    trace_id: str
    span_id: str
    parent_id: str | None
    component: str
    start_unix_ns: int
    start_ns: int
    end_ns: int | None = None
    status: str = OK
    attributes: dict = field(default_factory=dict)
    _recorder: "Recorder | None" = field(default=None, repr=False, compare=False)

    def set(self, key: str, value) -> None:
        """Set one attribute. Content is dropped unless capture is on; every
        value is redacted. Keyed redaction catches `api_key`-style names."""
        rec = self._recorder
        if key in CONTENT_ATTRIBUTES and not (rec and rec.capture_content):
            return
        self.attributes[key] = _clean(key, value)

    def fail(self, exc: BaseException) -> None:
        self.status = ERROR
        self.set("error.type", type(exc).__name__)
        self.set("error.message", str(exc))

    def end(self, error: BaseException | None = None) -> None:
        """Close the span and hand it to the recorder. Ending twice is a no-op."""
        rec = self._recorder
        if self.end_ns is not None or rec is None:
            return
        if error is not None:
            self.fail(error)
        self.end_ns = rec._clock()
        rec._finish(self)

    @property
    def duration_ms(self) -> float:
        return ((self.end_ns if self.end_ns is not None else self.start_ns)
                - self.start_ns) / 1e6

    def to_record(self) -> dict:
        return {
            "name": self.name,
            "trace_id": self.trace_id,
            "span_id": self.span_id,
            "parent_span_id": self.parent_id,
            "start_time_unix_nano": self.start_unix_ns,
            "duration_ms": self.duration_ms,
            "status": self.status,
            "attributes": dict(self.attributes),
        }


def _clean(key: str, value):
    value = redact({key: value})[key]
    if isinstance(value, str) and len(value) > _MAX_VALUE_CHARS:
        value = value[:_MAX_VALUE_CHARS] + "..."
    return value


_current: ContextVar[Span | None] = ContextVar("friday_telemetry_span", default=None)


class Recorder:
    def __init__(self, *, path=None, capture_content: bool = False,
                 clock=time.perf_counter_ns, keep: int = _KEEP_SPANS) -> None:
        self.path = Path(path) if path else None
        self.capture_content = capture_content
        self._clock = clock
        self._finished: deque[Span] = deque(maxlen=keep)
        self._lock = threading.Lock()

    def _new_id(self, width: int) -> str:
        return os.urandom(width).hex()

    def start(self, name: str, *, component: str, attributes: dict | None = None) -> Span:
        """Open a span under the current one WITHOUT making it current, for an
        interval that opens and closes in different places (a tool call that
        starts on one event and ends on another). Close it with `Span.end`."""
        parent = _current.get()
        sp = Span(name=name,
                  trace_id=parent.trace_id if parent else self._new_id(16),
                  span_id=self._new_id(8),
                  parent_id=parent.span_id if parent else None,
                  component=component,
                  start_unix_ns=time.time_ns(), start_ns=self._clock(),
                  _recorder=self)
        sp.set(COMPONENT, component)
        for key, value in (attributes or {}).items():
            sp.set(key, value)
        return sp

    @contextmanager
    def span(self, name: str, *, component: str, attributes: dict | None = None):
        sp = self.start(name, component=component, attributes=attributes)
        token = _current.set(sp)
        try:
            yield sp
        except BaseException as exc:
            sp.fail(exc)
            raise
        finally:
            _current.reset(token)
            sp.end()

    def _finish(self, sp: Span) -> None:
        with self._lock:
            self._finished.append(sp)
            if self.path is None:
                return
            try:
                with self.path.open("a", encoding="utf-8") as fh:
                    fh.write(json.dumps(sp.to_record(), default=str) + "\n")
            except OSError:
                logger.exception("telemetry: could not export a span to %s", self.path)

    # -- reading ------------------------------------------------------------

    def spans(self) -> list[Span]:
        with self._lock:
            return list(self._finished)

    def trace(self, trace_id: str) -> list[Span]:
        return [s for s in self.spans() if s.trace_id == trace_id]

    def duration_ms(self, trace_id: str) -> float:
        """Wall time of the run: the time covered by at least one of its ended
        spans. A span that outlives its parent (a background run) extends it,
        so this equals the sum of `component_time_ms`."""
        spans = sorted((s for s in self.trace(trace_id) if s.end_ns is not None),
                       key=lambda s: s.start_ns)
        total = 0
        reach = None
        for s in spans:
            lo = s.start_ns if reach is None else max(s.start_ns, reach)
            if s.end_ns > lo:
                total += s.end_ns - lo
            reach = s.end_ns if reach is None else max(reach, s.end_ns)
        return total / 1e6

    def component_time_ms(self, trace_id: str) -> dict[str, float]:
        """Wall time per component. Every instant of the run is counted once:
        it goes to the innermost spans open at that instant (those with no open
        descendant), split evenly when several are open together, such as
        parallel tool calls. The parts therefore add up to the run, however the
        spans overlap."""
        spans = [s for s in self.trace(trace_id) if s.end_ns is not None]
        by_id = {s.span_id: s for s in spans}
        cuts = sorted({t for s in spans for t in (s.start_ns, s.end_ns)})
        ns: dict[str, float] = {}
        for lo, hi in zip(cuts, cuts[1:]):
            active = [s for s in spans if s.start_ns <= lo and s.end_ns >= hi]
            if not active:
                continue
            covered = set()
            for s in active:
                seen = {s.span_id}
                parent = by_id.get(s.parent_id)
                while parent is not None and parent.span_id not in seen:
                    covered.add(parent.span_id)
                    seen.add(parent.span_id)
                    parent = by_id.get(parent.parent_id)
            inner = [s for s in active if s.span_id not in covered]
            share = (hi - lo) / len(inner)
            for s in inner:
                ns[s.component] = ns.get(s.component, 0.0) + share
        return {k: v / 1e6 for k, v in ns.items()}


_recorder: Recorder | None = None


def configure(*, path=None, capture_content: bool = False) -> Recorder:
    """Replace the process recorder. Content capture needs this explicit ask."""
    global _recorder
    _recorder = Recorder(path=path, capture_content=capture_content)
    return _recorder


def reset() -> None:
    global _recorder
    _recorder = None


def recorder() -> Recorder:
    """The process recorder: in memory, content off, unless the environment
    names an export file or switches content on."""
    global _recorder
    if _recorder is None:
        _recorder = Recorder(
            path=os.environ.get("FRIDAY_TELEMETRY_FILE") or None,
            capture_content=os.environ.get("FRIDAY_TELEMETRY_CONTENT", "") == "1")
    return _recorder


def span(name: str, *, component: str, attributes: dict | None = None):
    return recorder().span(name, component=component, attributes=attributes)


def start(name: str, *, component: str, attributes: dict | None = None) -> Span:
    return recorder().start(name, component=component, attributes=attributes)
