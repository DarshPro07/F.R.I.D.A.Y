"""T1 - browser-brain turn integrity (D-13, D-15).

D-13: two overlapping /api/ask calls used to share `_CURRENT_TURN` and
interleave their direct-action chains on the same file. Now: `reply()` is
one-turn-at-a-time (the second caller gets `busy` at once, no model call),
and `direct_action.run` serializes chains per target path even when called
from two threads directly.

D-15: "My live capability families include web, clock, ..." is a STATE
claim; the surface read that produced it is recorded as an OBSERVED
snapshot so the true recital is backed and a stale one is not.

Negative controls: a second ask on a DIFFERENT file is not blocked by the
per-path lock; ordinary conversation is not classified as a state claim.
"""
from __future__ import annotations

import threading
import time

import pytest

from friday import action_evidence as AE
from friday import voice_brain as V


@pytest.fixture(autouse=True)
def _ledger(tmp_path):
    AE.reset_ledger(AE.ActionEvidenceLedger(tmp_path / "ev.sqlite3"))
    yield


# --------------------------------------------------------------------------
# D-13: one turn at a time
# --------------------------------------------------------------------------

def test_a_second_ask_during_a_turn_is_busy_not_a_second_model_call(monkeypatch):
    started = threading.Event()
    release = threading.Event()
    calls = []

    def slow_locked(text, history=None):
        calls.append(text)
        started.set()
        release.wait(5)
        return {"reply": "first done"}
    monkeypatch.setattr(V, "_reply_locked", slow_locked)

    results = {}
    t = threading.Thread(target=lambda: results.setdefault("first", V.reply("create a.txt")))
    t.start()
    assert started.wait(2)
    second = V.reply("overwrite a.txt")                 # while the first is in flight
    release.set()
    t.join(5)
    assert second["busy"] is True and second["reply"] == ""
    assert results["first"] == {"reply": "first done"}
    assert calls == ["create a.txt"]                     # the second never reached the brain


def test_busy_reports_the_in_flight_turn_and_clears_after(monkeypatch):
    assert V.busy() is None
    gate = threading.Event()
    monkeypatch.setattr(V, "_reply_locked", lambda text, history=None: (gate.wait(5), {"reply": "ok"})[1])
    t = threading.Thread(target=lambda: V.reply("what time is it"))
    t.start()
    time.sleep(0.1)
    assert V.busy() is not None
    gate.set()
    t.join(5)
    assert V.busy() is None


def test_api_ask_answers_409_when_busy(monkeypatch):
    """The HTTP surface: the page gets 409 + the in-flight turn, not a reply."""
    import asyncio
    from friday import ui_server as U
    monkeypatch.setattr(V, "reply", lambda text, history=None: {"reply": "", "busy": True, "since": 1.0})

    class Req:
        async def json(self):
            return {"text": "hello"}
    resp = asyncio.run(U.api_ask(Req()))
    assert resp.status_code == 409


def test_direct_chains_on_the_same_file_serialize(monkeypatch, tmp_path):
    """Two threads, same target: the second chain's first step starts only
    after the first chain's last step finished."""
    from friday import direct_action as DA
    order = []
    lock_probe = threading.Lock()

    class Goal:
        def __init__(self, cap, path):
            self.capability, self.operation, self.target, self.goal_id = cap, "x", "t", "g"
            self.literals = [type("L", (), {"kind": "path"})()]
            self._path = path

    def fake_plan_for(text):
        return type("Plan", (), {"goals": [Goal("files_write", str(tmp_path / "same.txt"))]})()
    monkeypatch.setattr(DA, "plan_for", fake_plan_for)
    monkeypatch.setattr(DA.P, "arguments_for", lambda g: {"path": g._path, "content": "x"})

    def fake_execute(direct, goals, rt, text, **kw):
        order.append(("start", text))
        time.sleep(0.15)
        order.append(("end", text))
    monkeypatch.setattr(DA, "_execute", fake_execute)

    ts = [threading.Thread(target=DA.run, args=(f"chain{i}",), kwargs={"runtime": object()}) for i in range(2)]
    for t in ts:
        t.start()
    for t in ts:
        t.join(5)
    # no interleaving: start,end,start,end
    kinds = [k for k, _ in order]
    assert kinds == ["start", "end", "start", "end"], order


def test_direct_chains_on_different_files_run_side_by_side(monkeypatch, tmp_path):
    """Negative control for the per-path lock: different targets do not wait."""
    from friday import direct_action as DA
    order = []

    class Goal:
        def __init__(self, path):
            self.capability, self.operation, self.target, self.goal_id = "files_write", "x", "t", "g"
            self._path = path

    def fake_plan_for(text):
        return type("Plan", (), {"goals": [Goal(str(tmp_path / f"{text}.txt"))]})()
    monkeypatch.setattr(DA, "plan_for", fake_plan_for)
    monkeypatch.setattr(DA.P, "arguments_for", lambda g: {"path": g._path})
    both_started = threading.Barrier(2, timeout=2)

    def fake_execute(direct, goals, rt, text, **kw):
        order.append(("start", text))
        both_started.wait()                       # deadlocks (-> BrokenBarrier) if serialized
        order.append(("end", text))
    monkeypatch.setattr(DA, "_execute", fake_execute)
    ts = [threading.Thread(target=DA.run, args=(f"file{i}",), kwargs={"runtime": object()}) for i in range(2)]
    for t in ts:
        t.start()
    for t in ts:
        t.join(5)
    assert [k for k, _ in order][:2] == ["start", "start"]


# --------------------------------------------------------------------------
# D-15: the surface recital is a backed state claim
# --------------------------------------------------------------------------

def test_family_recital_is_a_state_claim():
    kinds = [c.kind for c in AE.classify_claims(
        "My live capability families include web, clock, memory, files, hermes and desktop.")]
    assert AE.STATE_CLAIM in kinds
    assert not any(c.kind == AE.STATE_CLAIM for c in AE.classify_claims("It is half past four, sir."))


def test_surface_read_records_an_observed_snapshot_that_backs_the_recital():
    fams = V._surface()
    snap = AE.ledger().snapshot_for("ui_families")
    assert snap is not None and snap.authoritative and snap.scope == "ui_surface"
    assert snap.count == len(fams) and "web" in snap.evidence
    out = V._honest_about_evidence("My live capability families include web, clock and files.")
    assert out == "My live capability families include web, clock and files."


def test_a_stale_surface_snapshot_does_not_back_the_recital(monkeypatch):
    V._surface()
    snap = AE.ledger().snapshot_for("ui_families")
    AE.ledger().snapshot(AE.CapabilityStateSnapshot(
        source="ui_families", collected_at=snap.collected_at - 3600, count=snap.count, health=snap.health,
        evidence=snap.evidence, ttl_s=300.0, scope="ui_surface", authority_level=AE.AUTHORITY_OBSERVED))
    out = V._honest_about_evidence("My live capability families include web, clock and files.")
    assert "haven't checked" in out
