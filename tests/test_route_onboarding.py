"""
Route onboarding (MODEL-004): the trigger that profiles a newly seen gateway
route through `gateway_completer`, with a scripted gateway standing in for the
worker. A route with a declaration or a stored profile is never probed again;
an unreachable route stores nothing so a later pass retries it.
"""
from __future__ import annotations

import base64
import json
import re
from datetime import datetime, timedelta

import pytest

from friday import model_capabilities as MC
from friday import model_gateway as MG
from friday import model_profiling as MP
from friday import route_benchmarks as RB
from friday import route_onboarding as RO

_NONCE = re.compile(r"\b[0-9a-f]{8}\b")
NEW = ("newprov", "new-model-1")


def _ok(response="", tool_calls=None):
    result = {"response": response}
    if tool_calls is not None:
        result["tool_calls"] = tool_calls
    return {"ok": True, "result": result}


class CapableGateway:
    """Answers every probe the way a fully capable model would; counts its calls."""

    def __init__(self):
        self.calls = []

    def _infer(self, provider, params, *, timeout):
        self.calls.append((provider, params["model"]))
        content = params["messages"][0]["content"]
        if isinstance(content, list):
            raw = base64.b64decode(content[1]["image_url"]["url"].split(",", 1)[1])
            for colour, rgb in MP.PROBE_COLOURS.items():
                if raw == MP.solid_png(rgb):
                    return _ok(colour)
            return _ok("unknown")
        nonce = _NONCE.search(content).group()
        if "tools" in params:
            name = params["tools"][0]["function"]["name"]
            return _ok("", [{"id": "c", "name": name, "arguments": json.dumps({"value": nonce})}])
        if '"answer"' in content and "properties" in content:
            return _ok(json.dumps({"answer": nonce}))
        return _ok(nonce)


class DownGateway:
    def __init__(self):
        self.calls = 0

    def _infer(self, provider, params, *, timeout):
        self.calls += 1
        raise MG.GatewayUnavailable("worker did not start")


class MuteGateway:
    """Reachable, but never echoes the nonce."""

    def _infer(self, provider, params, *, timeout):
        return _ok("I cannot help with that.")


@pytest.fixture(autouse=True)
def store(monkeypatch, tmp_path):
    monkeypatch.setenv("FRIDAY_MODEL_PROFILES", str(tmp_path / "model_profiles.json"))
    monkeypatch.setattr(RB, "_STORE", RB.BenchmarkStore())


def test_a_newly_seen_route_is_profiled_and_then_routed_without_a_declaration():
    with pytest.raises(MC.CapabilityError):
        MC.get(*NEW)

    (result,) = RO.onboard_new_routes(CapableGateway(), [NEW])

    assert (result.provider, result.model, result.status) == (*NEW, RO.PROFILED)
    caps = MC.get(*NEW)
    assert (caps.tools, caps.vision, caps.structured_output) == (True, True, True)
    assert caps.provider == NEW[0]
    assert NEW not in MC.CAPABILITIES
    fit, reason = MG.capability_verdict(*NEW, required=("tools", "images"))
    assert fit and "measured, not declared" in reason


def test_a_declared_route_is_never_probed():
    declared = next(iter(MC.CAPABILITIES))
    gw = CapableGateway()

    (result,) = RO.onboard_new_routes(gw, [declared])

    assert result.status == RO.DECLARED
    assert gw.calls == []


def test_a_route_already_profiled_is_not_probed_again():
    RO.onboard_new_routes(CapableGateway(), [NEW])
    gw = CapableGateway()

    (result,) = RO.onboard_new_routes(gw, [NEW])

    assert result.status == RO.ALREADY_PROFILED
    assert gw.calls == []


def test_an_unreachable_route_stores_nothing_so_a_later_pass_retries_it():
    down = DownGateway()
    (first,) = RO.onboard_new_routes(down, [NEW])
    assert first.status == RO.UNREACHABLE
    assert MC.profiled(*NEW) is None

    (second,) = RO.onboard_new_routes(CapableGateway(), [NEW])
    assert second.status == RO.PROFILED


def test_a_route_that_cannot_echo_is_unusable_and_stores_nothing():
    (result,) = RO.onboard_new_routes(MuteGateway(), [NEW])
    assert result.status == RO.UNUSABLE
    assert MC.profiled(*NEW) is None


def test_duplicates_are_probed_once_and_results_keep_input_order():
    other = ("newprov", "new-model-2")
    gw = CapableGateway()

    results = RO.onboard_new_routes(gw, [NEW, other, NEW])

    assert [(r.provider, r.model) for r in results] == [NEW, other]
    assert {m for _, m in gw.calls} == {NEW[1], other[1]}


def _profiled_at(route):
    return datetime.fromisoformat(MC.profiled(*route).profiled_at)


def test_a_profile_older_than_max_age_is_measured_again():
    RO.onboard_new_routes(CapableGateway(), [NEW])
    later = _profiled_at(NEW) + timedelta(seconds=120)
    gw = CapableGateway()

    (result,) = RO.onboard_new_routes(gw, [NEW], max_age_s=60, now=lambda: later)

    assert result.status == RO.PROFILED
    assert gw.calls


def test_a_profile_within_max_age_is_not_probed_again():
    RO.onboard_new_routes(CapableGateway(), [NEW])
    later = _profiled_at(NEW) + timedelta(seconds=30)
    gw = CapableGateway()

    (result,) = RO.onboard_new_routes(gw, [NEW], max_age_s=60, now=lambda: later)

    assert result.status == RO.ALREADY_PROFILED
    assert gw.calls == []


def test_a_failed_refresh_keeps_the_earlier_profile():
    RO.onboard_new_routes(CapableGateway(), [NEW])
    before = MC.profiled(*NEW)
    later = _profiled_at(NEW) + timedelta(days=30)

    (result,) = RO.onboard_new_routes(DownGateway(), [NEW], max_age_s=60, now=lambda: later)

    assert result.status == RO.UNREACHABLE
    assert MC.profiled(*NEW) == before
