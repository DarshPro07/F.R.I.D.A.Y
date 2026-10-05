"""
Open-set model onboarding (MODEL-004, PRD section 27 "open-set profiling").

`test_route_fitness` showed the declaration half: a route is a registry entry
the gateway consumes unmodified. Until `friday/model_profiling.py` a human still
wrote that entry. These tests onboard a mock route that nobody declared - the
profiler probes it, the registry falls back to the stored profile, and the
gateway's own `capability_verdict` routes on it with no gateway change.

The mock routes behave like models, not like flags: they read the nonce out of
the prompt and answer (or fail to) the way a real route would, so a probe that
could be passed by a canned answer, or by the model's name, would show here.
"""
from __future__ import annotations

import json
import re
import struct
import zlib

import pytest

from friday import model_capabilities as MC
from friday import model_gateway as MG
from friday import model_profiling as MP
from friday import route_benchmarks as RB

BACKEND, MODEL = "mock", "mock-1"
_NONCE = re.compile(r"\b[0-9a-f]{8}\b")


class Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


class MockRoute:
    """A route that answers each probe the way its flags say a model would."""

    def __init__(self, clock, *, latency=0.5, tools="call", structured="json",
                 vision="sees", unreachable=False, echo=True):
        self.clock, self.latency = clock, latency
        self.tools, self.structured, self.vision = tools, structured, vision
        self.unreachable, self.echo = unreachable, echo
        self.requests = []

    def __call__(self, request):
        self.requests.append(request)
        self.clock.now += self.latency
        if self.unreachable:
            raise MP.RouteUnreachable("connection refused")
        nonce = _NONCE.search(request.prompt)
        if request.tools:
            if self.tools == "refuse":
                raise MP.ProbeRefused("this model does not accept tools")
            if self.tools == "call":
                return MP.ProbeReply(tool_calls=(MP.ToolCall(
                    request.tools[0]["name"], {"value": nonce.group()}),))
            return MP.ProbeReply(text="I would call the tool but I will just say so.")
        if request.images:
            if self.vision == "sees":
                for name, rgb in MP.PROBE_COLOURS.items():
                    if request.images[0] == MP.solid_png(rgb):
                        return MP.ProbeReply(text=name)
            return MP.ProbeReply(text="red")           # blind: a constant guess
        if request.response_schema is not None:
            if self.structured == "json":
                return MP.ProbeReply(text=json.dumps({"answer": nonce.group()}))
            return MP.ProbeReply(text=f"Sure! The answer is {nonce.group()}.")
        return MP.ProbeReply(text=nonce.group() if self.echo else "I cannot help with that.")


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture(autouse=True)
def store(monkeypatch, tmp_path):
    path = tmp_path / "model_profiles.json"
    monkeypatch.setenv("FRIDAY_MODEL_PROFILES", str(path))
    monkeypatch.setattr(RB, "_STORE", RB.BenchmarkStore())
    return path


def _profile(route, clock, **kw):
    return MP.profile_route(BACKEND, MODEL, route, clock=clock, **kw)


# --- the acceptance: a new mock route is profiled and routed, no core rewrite --

def test_an_undeclared_route_is_profiled_and_then_routed_by_the_unmodified_gateway(clock):
    with pytest.raises(MC.CapabilityError):
        MC.get(BACKEND, MODEL)                          # no hand-written declaration exists
    profile = _profile(MockRoute(clock), clock, provider="acme")

    assert profile.status == "profiled"
    caps = MC.get(BACKEND, MODEL)
    assert (caps.tools, caps.vision, caps.structured_output) == (True, True, True)
    assert (caps.provider, caps.health) == ("acme", "ok")
    fit, _ = MG.capability_verdict(BACKEND, MODEL, required=("tools", "images"))
    assert fit
    assert (BACKEND, MODEL) not in MC.CAPABILITIES      # the registry's source was never edited


def test_the_gateway_says_a_profiled_route_was_measured_not_declared(clock):
    """Developer Mode reads the verdict reason to tell what a routing choice
    rests on; "declared capable" for a route nobody declared would hide that."""
    _profile(MockRoute(clock), clock)
    fit, reason = MG.capability_verdict(BACKEND, MODEL, required=("tools",))
    assert fit
    assert reason == "profiled capable (measured, not declared)"


def test_a_profiled_route_that_failed_a_probe_is_refused_as_profiled_not_declared(clock):
    _profile(MockRoute(clock, tools="refuse"), clock)
    fit, reason = MG.capability_verdict(BACKEND, MODEL, required=("tools",))
    assert not fit
    assert reason == "profiled without tools"


def test_capabilities_come_from_what_the_route_did_not_from_its_name(clock):
    route = MockRoute(clock, tools="refuse", vision="blind", structured="prose")
    profile = MP.profile_route(BACKEND, "mock-vision-tools-pro", route, clock=clock)

    caps = profile.capabilities
    assert (caps.tools, caps.vision, caps.structured_output) == (False, False, False)
    fit, reason = MG.capability_verdict(BACKEND, "mock-vision-tools-pro", required=("tools",))
    assert not fit and "without tools" in reason
    by_name = {o.name: o for o in profile.outcomes}
    assert "refused" in by_name["tools"].detail
    assert "JSON" in by_name["structured_output"].detail


def test_a_route_that_ignores_the_offered_tool_is_recorded_without_tools(clock):
    profile = _profile(MockRoute(clock, tools="ignore"), clock)
    assert profile.capabilities.tools is False


def test_a_constant_answer_cannot_pass_the_vision_probe(clock):
    """The blind route says "red" to everything; the two probe images differ."""
    route = MockRoute(clock, vision="blind")
    assert _profile(route, clock).capabilities.vision is False
    shown = [r.images[0] for r in route.requests if r.images]
    assert len(shown) >= 1 and len(set(shown)) == len(shown)


def test_probe_images_are_valid_pngs():
    data = MP.solid_png((255, 0, 0), size=8)
    assert data[:8] == b"\x89PNG\r\n\x1a\n"
    pos, chunks = 8, {}
    while pos < len(data):
        (length,) = struct.unpack(">I", data[pos:pos + 4])
        tag, body = data[pos + 4:pos + 8], data[pos + 8:pos + 8 + length]
        (crc,) = struct.unpack(">I", data[pos + 8 + length:pos + 12 + length])
        assert crc == zlib.crc32(tag + body)
        chunks[tag] = body
        pos += 12 + length
    assert struct.unpack(">II", chunks[b"IHDR"][:8]) == (8, 8)
    assert len(zlib.decompress(chunks[b"IDAT"])) == 8 * (1 + 3 * 8)


def test_each_run_draws_a_fresh_nonce(clock):
    first, second = MockRoute(clock), MockRoute(clock)
    _profile(first, clock)
    MP.profile_route(BACKEND, "mock-2", second, clock=clock)
    assert first.requests[0].prompt != second.requests[0].prompt


# --- latency is measured, cost and context are not guessed -------------------

@pytest.mark.parametrize("seconds, bucket", [(0.2, "very_low"), (2.0, "low"),
                                             (5.0, "medium"), (20.0, "high")])
def test_latency_is_bucketed_from_the_measured_round_trip(clock, seconds, bucket):
    profile = _profile(MockRoute(clock, latency=seconds), clock)
    assert profile.capabilities.relative_latency == bucket


def test_what_a_probe_cannot_show_is_recorded_as_unknown(clock):
    caps = _profile(MockRoute(clock), clock).capabilities
    assert caps.max_context is None
    assert caps.relative_cost == "unknown"
    assert caps.realtime_audio is False
    assert "not probed" in caps.notes


# --- a route that cannot be measured is not recorded --------------------------

def test_an_unreachable_route_records_nothing(clock, store):
    profile = _profile(MockRoute(clock, unreachable=True), clock)
    assert profile.status == "unreachable" and profile.capabilities is None
    assert "unreachable" in profile.outcomes[-1].detail
    assert not store.exists()
    with pytest.raises(MC.CapabilityError):
        MC.get(BACKEND, MODEL)


def test_a_route_that_cannot_echo_a_token_is_unusable_and_not_recorded(clock, store):
    route = MockRoute(clock, echo=False)
    profile = _profile(route, clock)
    assert profile.status == "unusable" and profile.capabilities is None
    assert len(route.requests) == 1                     # nothing after a failed echo means anything
    assert not store.exists()


# --- the profile is a record, and it does not outrank the owner ---------------

def test_the_profile_is_stored_with_its_evidence(clock, store):
    _profile(MockRoute(clock), clock)
    row = json.loads(store.read_text(encoding="utf-8"))["profiles"][0]
    assert row["capabilities"]["model_id"] == MODEL
    assert [e["name"] for e in row["evidence"]] == ["text", "tools", "structured_output", "vision"]
    assert all(e["passed"] for e in row["evidence"])
    assert MC.profiled(BACKEND, MODEL).profiled_at == row["profiled_at"]


def test_the_latest_profile_replaces_the_earlier_one(clock):
    _profile(MockRoute(clock), clock)
    _profile(MockRoute(clock, tools="refuse"), clock)
    assert MC.get(BACKEND, MODEL).tools is False


def test_a_declared_route_is_not_profiled(clock):
    declared_backend, declared_model = next(iter(MC.CAPABILITIES))
    with pytest.raises(ValueError, match="declared"):
        MP.profile_route(declared_backend, declared_model, MockRoute(clock), clock=clock)
    with pytest.raises(ValueError, match="shadows"):
        MC.record_profile(MC.CAPABILITIES[(declared_backend, declared_model)], [],
                          profiled_at="2026-10-06T00:00:00+00:00")


def test_a_profile_never_promotes_a_route(clock):
    """Promotion is the Golden Objective gates' (EVAL-003); a probe is not one."""
    _profile(MockRoute(clock), clock)
    fit, promoted, reason = MG.benchmark_verdict(BACKEND, MODEL)
    assert fit and not promoted
    assert "not benchmarked" in reason
    assert not MG.benchmark_verdict(BACKEND, MODEL, require_measured=True)[0]


def test_a_route_reachable_through_a_role_is_undeclared_until_it_is_profiled(clock, monkeypatch):
    monkeypatch.setattr(MC, "LLM_ROLE_MODELS", {BACKEND: {"NORMAL": MODEL}})
    assert MC.undeclared_models() == [(BACKEND, MODEL)]
    _profile(MockRoute(clock), clock)
    assert MC.undeclared_models() == []


def test_an_unreadable_profile_store_is_an_error_not_a_silent_miss(store):
    store.write_text("{not json", encoding="utf-8")
    with pytest.raises(MC.ProfileStoreError, match="not a model profile store"):
        MC.get(BACKEND, MODEL)
    store.write_text(json.dumps({"profiles": [{"capabilities": {"model_id": "x"},
                                               "evidence": [], "profiled_at": "t"}]}),
                     encoding="utf-8")
    with pytest.raises(MC.ProfileStoreError):
        MC.get(BACKEND, MODEL)
