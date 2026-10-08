"""RES-001: reducing running work, completing the section 55 record, and
governing local-model residency at the model gateway."""
from __future__ import annotations

import dataclasses
import io
import json
import subprocess
import sys

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


# -- an evicted local model is really unloaded --------------------------------


def _local_gateway(monkeypatch, gov, worker):
    from friday import model_gateway as MG
    monkeypatch.setattr(G, "governor", lambda: gov)
    monkeypatch.setattr(MG.ModelGateway, "route_kind", lambda self, p: "local")
    gw = MG.ModelGateway.__new__(MG.ModelGateway)
    gw.worker = worker
    gw._cli = {}
    return gw


def test_a_local_model_evicted_under_high_pressure_is_unloaded_by_the_worker(monkeypatch):
    gov, dial = make()
    calls = []

    class W:
        def call(self, op, params, timeout):
            calls.append((op, dict(params)))
            if op == "infer":
                dial.s = sample(cpu=90)
                assert gov.pressure(fresh=True).level in (G.HIGH, G.CRITICAL)   # mid-call
            return {"ok": True, "result": {"supported": True, "unloaded": ["m"]}}

    _local_gateway(monkeypatch, gov, W())._infer("ollama", {"model": "m"}, timeout=5)
    assert calls == [("infer", {"model": "m"}),
                     ("unload", {"provider": "ollama", "model": "m"})]
    record = gov.reductions[-1]
    assert record["kind"] == G.MODEL and "evicted" in record["reason"]
    assert record["released"] is True and gov.active(G.MODEL) == 0


def test_a_model_call_under_no_pressure_is_not_unloaded(monkeypatch):
    gov, _ = make()
    calls = []

    class W:
        def call(self, op, params, timeout):
            calls.append(op)
            return {"ok": True}

    _local_gateway(monkeypatch, gov, W())._infer("ollama", {"model": "m"}, timeout=5)
    assert calls == ["infer"] and gov.reductions == []


def test_a_failed_unload_never_replaces_the_calls_own_result(monkeypatch, caplog):
    from friday import model_gateway as MG
    gov, dial = make()

    class W:
        def call(self, op, params, timeout):
            if op == "infer":
                dial.s = sample(cpu=90)
                gov.pressure(fresh=True)
                return {"ok": True, "result": {"response": "hi"}}
            raise MG.GatewayUnavailable("worker gone")

    gw = _local_gateway(monkeypatch, gov, W())
    with caplog.at_level("WARNING", logger="friday.model_gateway"):
        assert gw._infer("ollama", {"model": "m"}, timeout=5)["result"]["response"] == "hi"
    assert "unloading it failed" in caplog.text


def _ollama(base_url="http://localhost:11434/v1", provider="ollama"):
    return lambda: {"providers": [{"id": provider, "base_url": base_url}]}


def test_worker_unload_sends_keep_alive_zero_to_the_local_ollama_root():
    from friday import hermes_model_gateway_worker as W
    sent = []

    def http(root, path, body=None):
        sent.append((root, path, body))
        return {}

    out = W._unload({"provider": "ollama", "model": "qwen:7b"},
                    providers=_ollama(), http=http)
    assert out == {"supported": True, "unloaded": ["qwen:7b"], "reason": ""}
    assert sent == [("http://localhost:11434", "/api/generate",
                     {"model": "qwen:7b", "keep_alive": 0})]


def test_worker_unload_without_a_model_drops_whatever_the_server_holds():
    from friday import hermes_model_gateway_worker as W
    sent = []

    def http(root, path, body=None):
        sent.append((path, body))
        return {"models": [{"name": "a"}, {"name": "b"}]} if path == "/api/ps" else {}

    out = W._unload({"provider": "ollama"}, providers=_ollama(), http=http)
    assert out["unloaded"] == ["a", "b"]
    assert sent[1:] == [("/api/generate", {"model": "a", "keep_alive": 0}),
                        ("/api/generate", {"model": "b", "keep_alive": 0})]


@pytest.mark.parametrize("asked,configured,base_url,why", [
    ("ollama", "ollama", "https://models.example.com", "not a local endpoint"),
    ("lmstudio", "lmstudio", "http://localhost:1234/v1", "no unload call is implemented"),
    ("ollama", "lmstudio", "http://localhost:1234/v1", "is not configured"),
])
def test_worker_unload_reports_what_it_cannot_do_and_sends_nothing(
        asked, configured, base_url, why):
    from friday import hermes_model_gateway_worker as W

    def http(*args, **kwargs):
        raise AssertionError("nothing may be sent")

    out = W._unload({"provider": asked, "model": "m"},
                    providers=_ollama(base_url, provider=configured), http=http)
    assert out["supported"] is False and out["unloaded"] == [] and why in out["reason"]


def test_worker_serves_the_unload_method(monkeypatch):
    from friday import hermes_model_gateway_worker as W
    replies = io.StringIO()
    monkeypatch.setattr(W, "_boot_hermes", lambda: None)
    monkeypatch.setattr(W, "_PROTOCOL", replies)    # replies bypass sys.stdout by design
    monkeypatch.setattr(W, "_unload", lambda params: {
        "supported": True, "unloaded": [params["model"]], "reason": ""})
    monkeypatch.setattr(sys, "stdin", io.StringIO(
        '{"id": 9, "method": "unload", "params": {"provider": "ollama", "model": "m"}}\n'))
    W.serve()
    assert json.loads(replies.getvalue()) == {
        "id": 9, "ok": True, "result": {"supported": True, "unloaded": ["m"], "reason": ""}}


# -- HIGH/CRITICAL on a real reading, not a synthetic one ----------------------

#: Thresholds no real machine can reach, so admission is never refused by the
#: host's own load; the test then lowers exactly one line.
_CALM = G.Thresholds(
    ram_yellow=2.0, ram_orange=2.0, ram_red=2.0, cpu_yellow=2.0, cpu_orange=2.0,
    cpu_red=2.0, trend_orange_s=-1.0, trend_yellow_s=-1.0, ram_critical=101.0,
    cpu_critical=101.0, ram_floor_gb=0.0, ram_floor_share=0.0, commit_floor_gb=0.0,
    commit_floor_share=0.0, disk_free_gb_critical=-1.0, disk_free_gb_low=-1.0,
    gpu_memory_high=101.0, gpu_memory_critical=101.0, gpu_busy_high=101.0,
    network_busy_share=2.0, max_workers=3)


def test_critical_pressure_on_a_real_reading_stops_real_workers_and_unloads_the_model(
        monkeypatch):
    """The real sampler reads this machine; the only thing moved is the RAM
    floor, set above anything the machine has, so the reading is really
    CRITICAL. Real helper processes are the workers: the governor must stop
    all but the oldest, evict the resident model, and the worker must be told
    to unload it."""
    import psutil

    gov = G.Governor(thresholds=_CALM, sample_ttl_s=0.0)           # sample_machine()
    helpers = [subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)"])
               for _ in range(3)]
    seen: dict = {}
    calls = []

    class W:
        def call(self, op, params, timeout):
            calls.append(op)
            if op == "infer":
                gov.rebaseline(G.Baseline.of([G.sample_machine()], provisional=True),
                               thresholds=dataclasses.replace(_CALM, ram_floor_gb=1e6))
                seen["pressure"] = gov.pressure(fresh=True)
            return {"ok": True, "result": {"supported": True, "unloaded": ["m"]}}

    try:
        for i, proc in enumerate(helpers):
            d = gov.admit(G.WORKER, label=f"helper{i}", pid=proc.pid, cleanup=proc.terminate)
            assert d.admitted, d.to_dict()
        gw = _local_gateway(monkeypatch, gov, W())
        gw._infer("ollama", {"model": "m"}, timeout=5)

        p = seen["pressure"]
        assert p.level == G.CRITICAL and any("GB RAM available" in r for r in p.reasons)
        assert p.sample.ram_total_gb == pytest.approx(
            psutil.virtual_memory().total / 1024**3, abs=0.01)
        for proc in helpers[1:]:
            assert proc.wait(timeout=15) is not None               # really gone
        assert helpers[0].poll() is None and gov.active(G.WORKER) == 1
        stopped = sorted((r["kind"], r["released"]) for r in gov.reductions)
        assert stopped == [(G.MODEL, True), (G.WORKER, True), (G.WORKER, True)]
        assert calls == ["infer", "unload"]
    finally:
        for proc in helpers:
            proc.kill()
            proc.wait(timeout=15)
