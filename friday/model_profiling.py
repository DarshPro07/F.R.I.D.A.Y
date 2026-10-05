"""
Open-set model onboarding: profile a route nobody has declared (MODEL-004,
PRD section 27 "open-set profiling").

`friday/model_capabilities.py` is the registry the gateway reads, and until
now a new (backend, model) entered it only when a human wrote a declaration.
This module is the other half: it PROBES a route, derives what it can do from
what it actually did, and stores that as a profile the registry falls back to
(`model_capabilities.record_profile`). The gateway is untouched - it asks
`model_capabilities.get` and gets the profile.

Nothing is inferred from the model id. Each capability is a probe whose answer
carries a fresh random nonce, so a canned or lucky reply cannot pass:

    tools              the route must emit a call to the offered tool with the nonce
    structured_output  asked for a JSON schema, it must return that JSON with the nonce
    vision             shown two solid-colour images, it must name both colours

Not probed, and recorded as such rather than guessed: `max_context` (None - a
wrong number is worse than an absent one), `realtime_audio` (False, a text
probe cannot show it), `relative_cost` ("unknown" - a price is not measurable).
Latency is the median round trip of the probe calls, bucketed.

A route is reached through a `Completer`: any callable taking a
`ProbeRequest` and returning a `ProbeReply`. It signals `RouteUnreachable` for
a transport/auth failure (nothing is recorded: that says nothing about what
the model can do) and `ProbeRefused` when the route rejects the request
shape (the capability is recorded as absent). Any other exception is a bug in
the adapter and propagates.

A profile is a measurement of capability, not a benchmark: it never promotes a
route (`route_benchmarks` and the Golden Objective gates still own that), and
it never shadows a hand-written declaration.

Assumption (revisit if wrong): the latency bucket thresholds below are
absolute seconds for one short probe call, tuned to put the declared Gemini
routes in the buckets they carry today. A route that cannot echo a nonce at
all is "unusable" and is not recorded.
"""
from __future__ import annotations

import json
import secrets
import statistics
import struct
import time
import zlib
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable, Mapping

from friday import model_capabilities as MC

PROBE_TOOL = "probe_echo"
#: name -> RGB of a solid 8x8 PNG shown to the vision probe
PROBE_COLOURS: Mapping[str, tuple[int, int, int]] = {
    "red": (255, 0, 0), "green": (0, 160, 0), "blue": (0, 0, 255), "yellow": (255, 255, 0)}
_LATENCY_BUCKETS = ((1.0, "very_low"), (3.0, "low"), (8.0, "medium"))   # else "high"
_DETAIL_CHARS = 200


@dataclass(frozen=True)
class ToolCall:
    name: str
    arguments: Mapping[str, object]


@dataclass(frozen=True)
class ProbeRequest:
    prompt: str
    tools: tuple[dict, ...] = ()          # {"name", "description", "parameters"} specs
    images: tuple[bytes, ...] = ()        # PNG
    response_schema: dict | None = None   # ask the route's structured-output mode


@dataclass(frozen=True)
class ProbeReply:
    text: str = ""
    tool_calls: tuple[ToolCall, ...] = ()


Completer = Callable[[ProbeRequest], ProbeReply]


class RouteUnreachable(RuntimeError):
    """The route could not be reached (transport, auth, quota); say nothing about its model."""


class ProbeRefused(RuntimeError):
    """The route rejected this kind of request, e.g. it does not accept tools."""


@dataclass(frozen=True)
class ProbeOutcome:
    name: str
    passed: bool
    detail: str
    latency_s: float

    def to_dict(self) -> dict:
        return {"name": self.name, "passed": self.passed, "detail": self.detail,
                "latency_s": round(self.latency_s, 4)}


@dataclass(frozen=True)
class RouteProfile:
    """`status` is "profiled" (recorded), "unreachable" or "unusable" (neither
    recorded; `capabilities` is None)."""

    backend: str
    model_id: str
    status: str
    capabilities: MC.ModelCapabilities | None
    outcomes: tuple[ProbeOutcome, ...]


def solid_png(rgb: tuple[int, int, int], size: int = 8) -> bytes:
    """A solid-colour PNG, built here so the vision probe needs no image library."""
    def chunk(tag: bytes, data: bytes) -> bytes:
        body = tag + data
        return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body))
    rows = (b"\x00" + bytes(rgb) * size) * size
    return (b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(rows)) + chunk(b"IEND", b""))


def _mentions(text: str) -> set[str]:
    lowered = text.lower()
    return {name for name in PROBE_COLOURS if name in lowered}


def _check_text(nonce: str):
    def check(reply: ProbeReply) -> tuple[bool, str]:
        if nonce in reply.text:
            return True, "echoed the nonce"
        return False, "reply did not contain the nonce"
    return check


def _check_tool(nonce: str):
    def check(reply: ProbeReply) -> tuple[bool, str]:
        for call in reply.tool_calls:
            if call.name == PROBE_TOOL and str(call.arguments.get("value")) == nonce:
                return True, "called the offered tool with the nonce"
        return False, "no call to the offered tool carrying the nonce"
    return check


def _check_json(nonce: str):
    def check(reply: ProbeReply) -> tuple[bool, str]:
        try:
            body = json.loads(reply.text)
        except ValueError:
            return False, "reply was not valid JSON"
        if isinstance(body, dict) and body.get("answer") == nonce:
            return True, "returned the schema's JSON with the nonce"
        return False, "JSON did not match the schema or carry the nonce"
    return check


def _check_colour(expected: str):
    def check(reply: ProbeReply) -> tuple[bool, str]:
        said = _mentions(reply.text)
        if said == {expected}:
            return True, f"named {expected}"
        return False, f"named {sorted(said) or 'no colour'}, image was {expected}"
    return check


def _probe_set() -> list[tuple[str, list]]:
    """(capability, [(request, check), ...]) in run order; the first is the
    "text" baseline. Nonces and colours are drawn fresh on every run."""
    nonce = secrets.token_hex(4)
    tool = {"name": PROBE_TOOL, "description": "Echo a value back.",
            "parameters": {"type": "object", "properties": {"value": {"type": "string"}},
                           "required": ["value"]}}
    schema = {"type": "object", "properties": {"answer": {"type": "string"}},
              "required": ["answer"]}
    colours = secrets.SystemRandom().sample(sorted(PROBE_COLOURS), 2)
    options = ", ".join(sorted(PROBE_COLOURS))
    return [
        ("text", [(ProbeRequest(f"Reply with exactly this token and nothing else: {nonce}"),
                   _check_text(nonce))]),
        ("tools", [(ProbeRequest(f"Call the {PROBE_TOOL} tool with value {nonce}. "
                                 "Do not answer in text.", tools=(tool,)),
                    _check_tool(nonce))]),
        ("structured_output", [(ProbeRequest(
            f'Reply with only a JSON object whose "answer" is "{nonce}".',
            response_schema=schema), _check_json(nonce))]),
        ("vision", [(ProbeRequest(f"Which colour fills this image? Answer with one word from: "
                                  f"{options}.", images=(solid_png(PROBE_COLOURS[c]),)),
                     _check_colour(c)) for c in colours]),
    ]


def _latency_bucket(seconds: float) -> str:
    for limit, bucket in _LATENCY_BUCKETS:
        if seconds < limit:
            return bucket
    return "high"


def profile_route(backend: str, model_id: str, complete: Completer, *,
                  provider: str | None = None, clock: Callable[[], float] = time.monotonic,
                  ) -> RouteProfile:
    """Probe a route with no declaration and record what it did.

    `provider` is who serves the model and defaults to the backend; it cannot
    be measured. Raises ValueError for a route that already has a declaration:
    a declaration is the owner's word and profiling would only contradict it.
    """
    if (backend, model_id) in MC.CAPABILITIES:
        raise ValueError(f"{model_id!r} on backend {backend!r} is declared; "
                         "profiling is for routes with no declaration")
    outcomes: list[ProbeOutcome] = []
    latencies: list[float] = []
    passed_by_name: dict[str, bool] = {}

    def result(status: str) -> RouteProfile:
        return RouteProfile(backend, model_id, status, None, tuple(outcomes))

    for name, steps in _probe_set():
        step_latencies: list[float] = []
        passed, detail = True, ""
        for request, check in steps:
            started = clock()
            try:
                reply = complete(request)
            except ProbeRefused as exc:
                passed, detail = False, f"route refused the probe: {str(exc)[:_DETAIL_CHARS]}"
                break
            except RouteUnreachable as exc:
                outcomes.append(ProbeOutcome(name, False, f"route unreachable: "
                                             f"{str(exc)[:_DETAIL_CHARS]}", 0.0))
                return result("unreachable")
            step_latencies.append(clock() - started)
            passed, detail = check(reply)
            if not passed:
                break
        latencies.extend(step_latencies)
        outcomes.append(ProbeOutcome(
            name, passed, detail, statistics.median(step_latencies) if step_latencies else 0.0))
        if name == "text" and not passed:
            return result("unusable")      # nothing after a failed echo means anything
        passed_by_name[name] = passed

    verdict = ", ".join(f"{o.name}={'pass' if o.passed else 'fail'}" for o in outcomes[1:])
    caps = MC.ModelCapabilities(
        model_id=model_id, provider=provider or backend, backend=backend,
        vision=passed_by_name["vision"], tools=passed_by_name["tools"],
        structured_output=passed_by_name["structured_output"], realtime_audio=False,
        max_context=None, relative_latency=_latency_bucket(statistics.median(latencies)),
        relative_cost="unknown", health="ok",
        notes=f"profiled ({verdict}); max_context, realtime_audio and cost not probed")
    MC.record_profile(caps, [o.to_dict() for o in outcomes],
                      profiled_at=datetime.now(timezone.utc).isoformat(timespec="seconds"))
    return RouteProfile(backend, model_id, "profiled", caps, tuple(outcomes))
