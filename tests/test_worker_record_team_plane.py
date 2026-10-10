"""
ORCH-004 (slice 4): the two remaining paths that carried a worker's full text
- per-profile team gateways (their deferred deliveries) and the connector
smoke test (what it hands on) - carry the bounded record instead.
"""
from __future__ import annotations

import json

from friday import hermes_bridge as hb
from friday import hermes_team as ht
from friday.connectors import plane as P
from friday.handoff import RECORD_TOKEN_CAP
from friday.record_delivery import RecordDeliveryLog

NOISE = "TRANSCRIPT-NOISE-LINE. "
TAIL = "END-OF-TRANSCRIPT-MARKER"


def test_team_gateways_deliver_the_record_not_the_transcript(monkeypatch):
    built = []

    class FakeSup:
        def __init__(self, *, profile, cwd, log=None):
            built.append(log)

        def start(self):
            pass

        def stop(self):
            pass

    monkeypatch.setattr(ht.hb, "HermesSupervisor", FakeSup)
    monkeypatch.setattr(ht.hb, "profile_home", lambda p: "")
    monkeypatch.setattr(ht, "_gateways", {})
    ht.gateway_for("friday-research")
    assert len(built) == 1
    assert isinstance(built[0], RecordDeliveryLog)


def _smoke(monkeypatch, tmp_path, row):
    from friday.tools import hermes_control

    class FakeSup:
        def start(self):
            pass

        def delegate(self, bundle, **kw):
            return {"work_run_id": "wr-1", "result": row}

    class FakeConnector:
        id = "anthropic"
        name = "Anthropic"
        models = ["claude-opus-5"]

    monkeypatch.setattr(hermes_control, "supervisor", lambda: FakeSup())
    monkeypatch.setattr(P, "find", lambda cid: FakeConnector())
    return P.ConnectorControlPlane(P.ConnectorState(tmp_path / "c.db")).smoke_test(
        "anthropic", model="claude-opus-5")


def _row(**over):
    return {"work_run_id": "wr-1", "status": hb.COMPLETE,
            "provider": "anthropic", "model": "claude-opus-5",
            "result": "CONNECTOR SMOKE OK\n" + NOISE * 170 + TAIL,
            "hermes_stored_session_id": "stored-9", **over}


def test_smoke_test_hands_on_the_bounded_record(monkeypatch, tmp_path):
    step = _smoke(monkeypatch, tmp_path, _row())
    assert step.action == "done"
    assert step.detail["record"]["status"] == hb.COMPLETE
    assert step.detail["record"]["transcript_ref"] == "stored-9"
    blob = json.dumps(step.detail)
    assert TAIL not in blob
    assert len(blob) // 4 <= RECORD_TOKEN_CAP + 100


def test_smoke_test_failure_also_carries_only_the_record(monkeypatch, tmp_path):
    step = _smoke(monkeypatch, tmp_path, _row(status=hb.FAILED))
    assert step.action == "error"
    assert step.detail["record"]["status"] == hb.FAILED
    assert TAIL not in json.dumps(step.detail)
