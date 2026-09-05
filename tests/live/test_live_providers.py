"""
Live provider validation - opt in (audit A-008 / A-024; PRD Requirements 9, 11).

    FRIDAY_LIVE_PROVIDER_TESTS=1 .venv-verify/Scripts/python.exe -m pytest tests/live -q

Everything else in `tests/` talks to `fake_hermes_gateway.py` and
`fake_model_gateway_worker.py`. This directory talks to the real Hermes
model-gateway worker and, through it, to every provider Hermes can
currently broker. It answers the question the deterministic suite cannot:
does Friday actually get an answer back from each configured route, with
real usage numbers, through the production `ModelGateway.infer` path?

Rules:
  * skipped, not failed, unless FRIDAY_LIVE_PROVIDER_TESTS=1 is set;
  * one tiny request per authenticated provider (a few tokens of output) -
    this costs money on API routes, which is why it is opt-in;
  * the verdict per route is `friday.provider_health`'s, read back from
    the call ledger the probe wrote - the same verdict routing uses - so
    the suite cannot say something the product does not;
  * a route is HEALTHY only when it answered with visible content. A
    structurally valid, empty reply is a failure (Requirement 11, last
    clause); "authenticated" is never treated as "healthy";
  * an account fact (no payment method, unsupported model, 404) is not a
    test failure: it is recorded as UNAVAILABLE with the provider's own
    error text, which is exactly what Requirement 9 asks for. The suite
    FAILS when the evidence is missing or contradicts a claim: a route
    with no ledger row, a HEALTHY verdict without content, no healthy
    route at all, or a credential-shaped string anywhere in the report;
  * results are written to `data/live/providers_<timestamp>.json` for the
    ledger; no credential ever appears in the request, result or file.
"""
from __future__ import annotations

import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path

import pytest

LIVE = os.getenv("FRIDAY_LIVE_PROVIDER_TESTS") == "1"
pytestmark = [pytest.mark.live,
              pytest.mark.skipif(not LIVE, reason="set FRIDAY_LIVE_PROVIDER_TESTS=1 to talk to real providers")]

ROOT = Path(__file__).resolve().parents[2]
OUT_DIR = ROOT / "data" / "live"
SECRET_SHAPES = ("sk-", "AIza", "xoxb-", "ghp_", "Bearer ")


@pytest.fixture(scope="module")
def gateway():
    from friday.model_gateway import ModelGateway
    gw = ModelGateway()
    yield gw
    gw.close()


@pytest.fixture(scope="module")
def inventory(gateway):
    from friday.model_gateway import GatewayUnavailable
    try:
        return gateway.providers(max_age_s=0)
    except GatewayUnavailable as exc:
        pytest.fail(f"Hermes model-gateway worker did not answer provider discovery: {exc}")


def _usable(inventory) -> list[dict]:
    return [p for p in inventory.get("providers", []) if p.get("id") in inventory.get("usable", [])]


def test_hermes_lists_at_least_one_usable_provider(inventory):
    usable = _usable(inventory)
    assert usable, f"no authenticated provider; configurable: {[p.get('id') for p in inventory.get('providers', [])]}"
    for p in usable:
        assert p.get("route_kind") in ("api", "subscription", "local", "free_tier"), p


def test_inventory_carries_no_credentials(inventory):
    blob = json.dumps(inventory)
    for shape in SECRET_SHAPES:
        assert shape not in blob, f"provider inventory leaks a credential-shaped value ({shape}...)"


def test_every_authenticated_provider_is_probed_and_gets_an_evidence_verdict(gateway, inventory):
    """The one that matters. Each authenticated route gets one small probe
    through ModelGateway.infer (pinned, no failover); its verdict is read
    back from the ledger by `provider_health`. The report is the product's
    health view, not the suite's opinion."""
    from friday import provider_health as PH
    usable = _usable(inventory)
    results = []
    for p in usable:
        pid = p["id"]
        t0 = time.time()
        try:
            verdict = gateway.probe(pid, worker="live-suite", objective_id=f"live-probe-{pid}")
            ev = verdict.evidence or {}
            row = {"provider": pid, "route_kind": p.get("route_kind"),
                   "default_model": p.get("default_model", ""),
                   "state": verdict.state, "reason": verdict.reason, "code": verdict.code,
                   "model": verdict.model, "latency_ms": int((time.time() - t0) * 1000),
                   "input_tokens": int(ev.get("input_tokens") or 0),
                   "output_tokens": int(ev.get("output_tokens") or 0),
                   "observed_at": verdict.observed_at}
        except Exception as exc:  # noqa: BLE001 - the provider's failure is the evidence
            row = {"provider": pid, "route_kind": p.get("route_kind"), "state": "EXCEPTION",
                   "reason": f"{type(exc).__name__}: {exc}"[:300],
                   "latency_ms": int((time.time() - t0) * 1000)}
        results.append(row)

    by_state: dict[str, list[str]] = {}
    for r in results:
        by_state.setdefault(r["state"], []).append(r["provider"])
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    report = {"ran_at": stamp, "prompt": PH.PROBE_PROMPT,
              "authenticated": [p["id"] for p in usable],
              "healthy": by_state.get(PH.HEALTHY, []),
              "degraded": by_state.get(PH.DEGRADED, []),
              "unavailable": by_state.get(PH.UNAVAILABLE, []),
              "results": results}
    (OUT_DIR / f"providers_{stamp}.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    (OUT_DIR / "providers_latest.json").write_text(json.dumps(report, indent=2), encoding="utf-8")

    blob = json.dumps(report)
    for shape in SECRET_SHAPES:
        assert shape not in blob, "live report leaks a credential-shaped value"

    # Evidence discipline, not account luck:
    #  1. every probed route has a verdict from evidence, never UNPROBED/STALE
    #     right after its own probe, and never an exception in Friday;
    #  2. HEALTHY means visible content came back (an empty reply is
    #     EMPTY_RESPONSE/OUTPUT_TRUNCATED, never healthy);
    #  3. every non-healthy route carries the provider's own reason;
    #  4. at least one route is healthy, or Friday has no model at all.
    lines = "\n".join(f"  {r['provider']:14s} {r['state']:12s} {r.get('model', '')!s:32s} {r['reason'][:120]}"
                      for r in results)
    bad_state = [r for r in results if r["state"] in (PH.UNPROBED, PH.STALE, "EXCEPTION")]
    assert not bad_state, "routes without fresh evidence after their own probe:\n" + lines
    for r in results:
        if r["state"] == PH.HEALTHY:
            assert r["output_tokens"] > 0, f"{r['provider']} is HEALTHY without visible output:\n{lines}"
        else:
            assert r["reason"].strip(), f"{r['provider']} is {r['state']} with no reason attached:\n{lines}"
    assert report["healthy"], "no authenticated route answered with content:\n" + lines


def test_the_gateway_health_view_matches_the_probe_evidence(gateway, inventory):
    """`ModelGateway.health()` - what the model_providers tool and the
    executor router read - must now say what the ledger says."""
    from friday import provider_health as PH
    health = gateway.health()
    latest = json.loads((OUT_DIR / "providers_latest.json").read_text(encoding="utf-8"))
    assert health["state"] == "READY"
    assert set(health["usable"]) == set(latest["authenticated"])
    assert sorted(health["healthy"]) == sorted(latest["healthy"]), (health["healthy"], latest["healthy"])
    assert sorted(health["unavailable"]) == sorted(latest["unavailable"])
    for r in latest["results"]:
        assert health["providers"][r["provider"]]["state"] == r["state"]
    # "usable" (a key exists) is a superset of "healthy" (answered), never equal by construction
    assert set(health["healthy"]) <= set(health["usable"])
    assert all(v["state"] != PH.UNPROBED for v in health["providers"].values())


def test_usage_is_recorded_per_provider(gateway, inventory):
    """FR-080: every live probe above must have left an attributed telemetry row."""
    from friday.model_gateway import GatewayTelemetry
    tel = GatewayTelemetry()
    for p in _usable(inventory):
        rows = tel.for_objective(f"live-probe-{p['id']}")
        assert rows, f"no telemetry row for {p['id']}"
        assert all(r.get("worker") == "live-suite" for r in rows)
