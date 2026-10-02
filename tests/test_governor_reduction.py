"""RES-001: reducing running work, completing the section 55 record, and
governing local-model residency at the model gateway."""
from __future__ import annotations

import pytest
from test_governor import IDLE, make, sample

from friday import governor as G


def test_attach_completes_the_record_after_admission():
    gov, _ = make()
    d = gov.admit(G.WORKER, label="job", owner="claude_code")
    assert gov.owned_processes()[0]["pid"] is None
    assert gov.attach(d.lease, pid=77, cleanup=lambda: None, deadline_s=30.0)
    rec = gov.owned_processes()[0]
    assert rec["pid"] == 77 and rec["cleanup"] and rec["deadline"] > rec["started"]
    gov.release(d.lease)
    assert gov.attach(d.lease, pid=1) is False


def test_high_pressure_stops_surplus_workers_newest_first_and_keeps_one():
    gov, dial = make(max_workers=3)
    stopped = []
    a = gov.admit(G.WORKER, label="a", cleanup=lambda: stopped.append("a"))
    b = gov.admit(G.WORKER, label="b", cleanup=lambda: stopped.append("b"))
    gov.admit(G.WORKER, label="c", cleanup=lambda: stopped.append("c"))
    gov._owned[a.lease].started -= 3
    gov._owned[b.lease].started -= 2
    dial.s = sample(cpu=90)
    assert gov.pressure(fresh=True).level in (G.HIGH, G.CRITICAL)
    assert sorted(stopped) == ["b", "c"] and gov.active(G.WORKER) == 1
    assert gov.owned_processes()[0]["lease"] == a.lease


def test_a_worker_that_never_heartbeats_is_not_reaped_as_silent():
    gov, _ = make(heartbeat_stale_s=30.0)
    gone = []
    d = gov.admit(G.WORKER, label="job", cleanup=lambda: gone.append(1))
    gov._owned[d.lease].heartbeat -= 600.0
    gov.reduce_running()
    assert gone == [] and gov.active(G.WORKER) == 1


def test_gpu_busy_and_saturated_network_shed_optional_work():
    busy = sample()
    busy.gpu_percent = 99.0
    p = G.assess(busy, G.Thresholds(), baseline=IDLE)
    assert p.level == G.ELEVATED and any("GPU" in r for r in p.reasons)
    net = sample()
    net.network_mbps, net.network_link_mbps = 950.0, 1000.0
    p = G.assess(net, G.Thresholds(), baseline=IDLE)
    assert p.level == G.ELEVATED and any("network" in r for r in p.reasons)
    net.network_mbps = 100.0
    assert G.assess(net, G.Thresholds(), baseline=IDLE).level == G.NORMAL


def test_local_model_route_holds_a_governed_lease_and_is_refused_under_pressure(monkeypatch):
    from friday import model_gateway as MG
    gov, dial = make()
    monkeypatch.setattr(G, "governor", lambda: gov)
    gw = MG.ModelGateway.__new__(MG.ModelGateway)
    seen = []

    class W:
        def call(self, op, params, timeout):
            seen.append(gov.active(G.MODEL))
            return {"ok": True}

    gw.worker = W()
    kind = {"v": "local"}
    monkeypatch.setattr(MG.ModelGateway, "route_kind", lambda self, p: kind["v"])
    assert gw._infer("ollama", {"model": "m"}, timeout=5) == {"ok": True}
    assert seen == [1] and gov.active(G.MODEL) == 0
    dial.s = sample(cpu=90)
    with pytest.raises(MG.GatewayUnavailable, match="governor"):
        gw._infer("ollama", {"model": "m"}, timeout=5)
    kind["v"] = "api"
    assert gw._infer("openai", {}, timeout=5) == {"ok": True} and seen == [1, 0]


def test_owner_release_after_the_governor_stopped_the_work_keeps_the_queue():
    """The governor releases a victim it stopped; the owner's own release then
    runs a second time and must not retire a different caller's queue entry."""
    gov, dial = make(max_workers=3)
    a = gov.admit(G.WORKER, label="a", cleanup=lambda: None)
    b = gov.admit(G.WORKER, label="b", cleanup=lambda: None)
    gov._owned[a.lease].started -= 2
    gov._queue.extend(["waiting-1", "waiting-2"])
    before = gov.queue_depth()
    dial.s = sample(cpu=90)
    gov.pressure(fresh=True)                       # stops b, retiring nothing
    assert gov.active(G.WORKER) == 1 and gov.queue_depth() == before
    gov.release(b.lease)                           # the owner's own release
    assert gov.queue_depth() == before
    gov.release(a.lease)                           # a real release still retires one
    assert gov.queue_depth() == before - 1
