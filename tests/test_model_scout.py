"""
Model Scout (FAB-006, extends SCOUT-001 / MODEL-004).

A newly listed model is recorded, probed with `model_profiling`, benchmarked per
task class against the current champion on held-out cases, and either promoted
for the classes it beats the champion on or archived. The reach to a real model
is an injected seam, so these tests drive it with scripted routes: the probe
completer answers the way a model would (it reads the nonce out of the prompt),
and the case checker is a table of which route gets which case right.

The second half is the privacy line: a `local_only` request whose private route
fails is refused, never moved to a free endpoint - proven on the real gateway
against the scripted worker.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import pytest

from friday import model_gateway as mg
from friday import model_profiling as MP
from friday import model_scout as MS

_NONCE = re.compile(r"\b[0-9a-f]{8}\b")
CHAMPION = "anthropic/champ-1"
FIXED = {"commit": "abc1234", "date": "2026-10-09"}


class Route:
    """A probe completer: answers every probe like a capable model, or does not."""

    def __init__(self, *, echo=True, unreachable=False):
        self.echo, self.unreachable, self.calls = echo, unreachable, 0

    def __call__(self, request):
        self.calls += 1
        if self.unreachable:
            raise MP.RouteUnreachable("connection refused")
        nonce = _NONCE.search(request.prompt)
        if request.tools:
            return MP.ProbeReply(tool_calls=(MP.ToolCall(
                request.tools[0]["name"], {"value": nonce.group()}),))
        if request.images:
            for name, rgb in MP.PROBE_COLOURS.items():
                if request.images[0] == MP.solid_png(rgb):
                    return MP.ProbeReply(text=name)
        if request.response_schema is not None:
            return MP.ProbeReply(text=json.dumps({"answer": nonce.group()}))
        return MP.ProbeReply(text=nonce.group() if self.echo else "I cannot help with that.")


class Table:
    """The case checker: which cases each route gets right, per route id."""

    def __init__(self, right: dict[str, set]):
        self.right, self.asked = right, []

    def __call__(self, route_id, case):
        self.asked.append((route_id, case))
        return case in self.right.get(route_id, set())


def listing(**kw):
    base = dict(model_id="new-1", provider="acme", licence="apache-2.0",
                capabilities=("tools", "json"), price="free", context=128000, version="1")
    base.update(kw)
    return MS.ModelListing(**base)


@pytest.fixture(autouse=True)
def isolated(monkeypatch, tmp_path):
    monkeypatch.setenv("FRIDAY_MODEL_PROFILES", str(tmp_path / "model_profiles.json"))


@pytest.fixture
def scout(tmp_path):
    return MS.ModelScout(tmp_path / "scout.json", provenance=FIXED)


CASES = {mg.STANDARD: [f"std{i}" for i in range(6)],
         mg.COMPLEX: [f"cx{i}" for i in range(6)]}
CHAMPIONS = {mg.STANDARD: CHAMPION, mg.COMPLEX: CHAMPION}


def test_a_new_model_is_promoted_only_for_the_classes_it_beats_the_champion_on(scout):
    table = Table({CHAMPION: {"std0", "std1", "std2", "cx0", "cx1", "cx2", "cx3"},
                   "acme/new-1": {"std0", "std1", "std2", "std3", "std4", "cx0"}})
    rec = scout.scout(listing(), completer=Route(), held_out=CASES,
                      champions=CHAMPIONS, check=table)
    assert rec.outcome == MS.PROMOTED
    assert rec.promoted_for == (mg.STANDARD,)
    assert scout.promoted_classes("acme", "new-1") == (mg.STANDARD,)
    assert scout.is_promoted("acme", "new-1", mg.STANDARD)
    assert not scout.is_promoted("acme", "new-1", mg.COMPLEX)
    assert rec.probe_status == "profiled"
    assert rec.listing.licence == "apache-2.0" and rec.listing.price == "free"


def test_a_model_that_beats_the_champion_nowhere_is_archived_not_promoted(scout):
    table = Table({CHAMPION: {"std0", "std1", "cx0", "cx1"},
                   "acme/new-1": {"std0", "std1", "cx0", "cx1"}})   # a tie keeps the champion
    rec = scout.scout(listing(), completer=Route(), held_out=CASES,
                      champions=CHAMPIONS, check=table)
    assert rec.outcome == MS.ARCHIVED and rec.promoted_for == ()
    assert scout.promoted_classes("acme", "new-1") == ()
    assert "beat the champion" in rec.reason


def test_a_model_that_fails_the_compatibility_probe_is_archived_unbenchmarked(scout):
    table = Table({})
    rec = scout.scout(listing(), completer=Route(echo=False), held_out=CASES,
                      champions=CHAMPIONS, check=table)
    assert rec.outcome == MS.ARCHIVED and rec.probe_status == "unusable"
    assert table.asked == []


def test_no_probe_route_reports_unavailable_and_promotes_nothing(scout):
    table = Table({})
    rec = scout.scout(listing(), completer=None, held_out=CASES,
                      champions=CHAMPIONS, check=table)
    assert rec.outcome == MS.UNAVAILABLE and rec.promoted_for == ()
    assert table.asked == []
    unreachable = scout.scout(listing(model_id="down-1"), completer=Route(unreachable=True),
                              held_out=CASES, champions=CHAMPIONS, check=table)
    assert unreachable.outcome == MS.UNAVAILABLE


def test_the_benchmark_is_bounded_per_class(tmp_path):
    scout = MS.ModelScout(tmp_path / "s.json", max_cases=4, provenance=FIXED)
    cases = {mg.STANDARD: [f"std{i}" for i in range(10)]}
    table = Table({"acme/new-1": {f"std{i}" for i in range(10)}})
    scout.scout(listing(), completer=Route(), held_out=cases,
                champions={mg.STANDARD: CHAMPION}, check=table)
    assert sorted({c for r, c in table.asked if r == "acme/new-1"}) == [
        "std0", "std1", "std2", "std3"]
    assert len(table.asked) == 8        # candidate and champion, four cases each


def test_a_listing_already_scouted_is_not_probed_again_until_its_version_changes(scout):
    table = Table({"acme/new-1": set(CASES[mg.STANDARD])})
    route = Route()
    first = scout.scout(listing(), completer=route, held_out=CASES,
                        champions=CHAMPIONS, check=table)
    spent = route.calls
    again = scout.scout(listing(), completer=route, held_out=CASES,
                        champions=CHAMPIONS, check=table)
    assert again == first and route.calls == spent
    # A new version is a new model: it is re-probed. (The route is now profiled, so the
    # profiler's own refusal to re-declare is not involved - only declared routes refuse.)
    scout.scout(listing(version="2"), completer=route, held_out=CASES,
                champions=CHAMPIONS, check=table)
    assert route.calls > spent


def test_the_ledger_survives_a_restart(tmp_path):
    path = tmp_path / "scout.json"
    table = Table({"acme/new-1": set(CASES[mg.STANDARD])})
    MS.ModelScout(path, provenance=FIXED).scout(
        listing(), completer=Route(), held_out=CASES, champions=CHAMPIONS, check=table)
    reopened = MS.ModelScout(path, provenance=FIXED)
    assert reopened.is_promoted("acme", "new-1", mg.STANDARD)
    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(MS.ScoutStoreError):
        reopened.is_promoted("acme", "new-1", mg.STANDARD)


def test_bad_input_is_a_caller_error(scout):
    table = Table({})
    with pytest.raises(ValueError, match="task class"):
        scout.scout(listing(), completer=Route(), held_out={"HUGE": ["a"]},
                    champions={"HUGE": CHAMPION}, check=table)
    with pytest.raises(ValueError, match="champion"):
        scout.scout(listing(), completer=Route(), held_out=CASES,
                    champions={mg.STANDARD: CHAMPION}, check=table)
    with pytest.raises(ValueError):
        listing(provider="")


# -- the privacy line: a failed private route is refused, never moved to a free one ----

FAKE = str(Path(__file__).parent / "fake_model_gateway_worker.py")


class PrivateAndFree(mg.ModelGatewayWorker):
    """Inventory: one local route (the private one) and one free-tier route."""

    def __init__(self):
        super().__init__(command=[sys.executable, FAKE], profile="")

    def call(self, method, params=None, timeout=30.0):
        reply = super().call(method, params, timeout=timeout)
        if method == "providers" and reply.get("ok"):
            reply["result"]["providers"] = [
                {"id": "ollama", "label": "Ollama", "aliases": [], "authenticated": True,
                 "route_kind": "local", "default_model": "private-1"},
                {"id": "opencode-free", "label": "OpenCode Free", "aliases": [],
                 "authenticated": True, "route_kind": "free_tier", "default_model": "free-1"}]
            reply["result"]["usable"] = ["ollama", "opencode-free"]
        return reply


def _gateway(tmp_path):
    return mg.ModelGateway(
        worker=PrivateAndFree(), telemetry=mg.GatewayTelemetry(tmp_path / "g.sqlite3"),
        tier_table={mg.TIER_FAST: ("ollama", "private-1"), mg.TIER_STANDARD: ("ollama", "private-1"),
                    mg.TIER_DEEP: ("ollama", "private-1")}, max_failover=3)


def _req(objective, **kw):
    return mg.ModelGatewayRequest(
        objective_id=objective, task_class=mg.SIMPLE,
        context_package=mg.compile_context(system="You are Friday.", user="ping"),
        allow_failover=True, **kw)


def test_a_confidential_request_is_refused_when_its_private_route_fails(
        tmp_path, monkeypatch, scout):
    from friday import provider_cooldowns as PC
    monkeypatch.setattr(PC, "COOLDOWNS_FILE", tmp_path / "cooldowns.json")
    monkeypatch.setenv("FAKE_GW_FAIL_PROVIDERS", "ollama:MODEL_UNAVAILABLE")
    # The free model has even been scouted and promoted: promotion is not clearance.
    scout.scout(listing(provider="opencode-free", model_id="free-1"), completer=Route(),
                held_out=CASES, champions=CHAMPIONS,
                check=Table({"opencode-free/free-1": set(CASES[mg.STANDARD])}))
    assert scout.is_promoted("opencode-free", "free-1", mg.STANDARD)
    gw = _gateway(tmp_path)
    try:
        # Control: an ordinary request does fail over to the free endpoint.
        open_res = gw.infer(_req("open-1"))
        assert open_res.status == "ok" and open_res.provider == "opencode-free"
        res = gw.infer(_req("conf-1", privacy_policy="local_only"))
        assert res.status == "failed"
        tried = {row["provider"] for row in gw.telemetry.for_objective("conf-1")}
        assert "opencode-free" not in tried
    finally:
        gw.close()
