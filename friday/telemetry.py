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

    @contextmanager
    def span(self, name: str, *, component: str, attributes: dict | None = None):
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
        token = _current.set(sp)
        try:
            yield sp
        except BaseException as exc:
            sp.status = ERROR
            sp.set("error.type", type(exc).__name__)
            sp.set("error.message", str(exc))
            raise
        finally:
            _current.reset(token)
            sp.end_ns = self._clock()
            self._finish(sp)

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
        """Wall time of the run: the roots' durations."""
        return sum(s.duration_ms for s in self.trace(trace_id) if s.parent_id is None)

    def component_time_ms(self, trace_id: str) -> dict[str, float]:
        """Wall time per component: each span's own time (its duration minus
        its children's), summed by component, so the parts add up to the run."""
        spans = self.trace(trace_id)
        child_ms: dict[str, float] = {}
        for s in spans:
            if s.parent_id:
                child_ms[s.parent_id] = child_ms.get(s.parent_id, 0.0) + s.duration_ms
        out: dict[str, float] = {}
        for s in spans:
            own = max(s.duration_ms - child_ms.get(s.span_id, 0.0), 0.0)
            out[s.component] = out.get(s.component, 0.0) + own
        return out


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
