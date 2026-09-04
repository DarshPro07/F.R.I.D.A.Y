"""The router must learn. It recorded outcomes for months and read none of them.

Before 2026-09-01 `capability_router.search()` scored purely from static
metadata, so a sentence mis-routed once was mis-routed identically forever,
even after the boss had corrected it out loud.
"""
import os
import tempfile

import pytest


@pytest.fixture()
def db(monkeypatch):
    path = os.path.join(tempfile.mkdtemp(), "t.sqlite3")
    monkeypatch.setenv("ADA_DB", path)
    from friday.toolsets import memory as M
    from friday import routing_memory as R
    M.reset_store(None)
    R.forget()
    yield M.store()
    M.reset_store(None)
    R.forget()


def test_nothing_learned_is_no_opinion(db):
    from friday import routing_memory as R
    assert R.prior("open spotify") == {}


def test_a_correction_moves_the_next_route(db):
    from friday import routing_memory as R
    text = "put the lights down"
    fp = R.fingerprint(text)
    db.record_routing_correction(
        fingerprint=fp, previous_capability="volume_set",
        corrected_capability="brightness_set", evidence="he said brightness")
    R.forget()
    learned = R.prior(text)
    assert learned["brightness_set"] > 0
    assert learned["volume_set"] < 0, "being told 'not that one' is evidence too"


def test_one_outcome_is_not_a_habit(db):
    from friday import routing_memory as R
    text = "check the disks"
    _row(db, R.fingerprint(text), "system_disks", ok=1)
    R.forget()
    assert R.prior(text) == {}, "a single observation is noise"


def test_repeated_outcomes_become_a_prior(db):
    from friday import routing_memory as R
    text = "check the disks"
    fp = R.fingerprint(text)
    for _ in range(3):
        _row(db, fp, "system_disks", ok=1)
    R.forget()
    assert R.prior(text)["system_disks"] > 0


def test_a_split_record_teaches_nothing(db):
    from friday import routing_memory as R
    text = "check the disks"
    fp = R.fingerprint(text)
    for _ in range(3):
        _row(db, fp, "system_disks", ok=1)
    for _ in range(3):
        _row(db, fp, "system_disks", ok=0)
    R.forget()
    assert R.prior(text).get("system_disks", 0) == 0


def test_the_prior_reaches_the_router(db):
    """The wiring, not the weights: search() must consult what was learned."""
    from friday import capability_router as cr
    from friday import routing_memory as R
    seen = {}

    class Tool:
        def __init__(self, name):
            self.info = type("I", (), {"name": name, "raw_schema": {
                "description": "does a thing", "parameters": {}}})()

    router = cr.Router()
    router.load([Tool("system_disks"), Tool("system_battery")])
    real = R.prior

    def spy(text):
        seen["text"] = text
        return {"system_battery": 500}
    R.prior = spy
    try:
        out = router.search("disks")
    finally:
        R.prior = real
    assert "text" in seen, "search() never asked what it had learned"
    assert out and out[0]["capability"] == "system_battery"


def _row(db, fp, cap, ok):
    with db._tx() as conn:
        conn.execute(
            "INSERT INTO shadow_predictions (at, fingerprint, decision, "
            "predicted_capability, intent_correct, settled_at) VALUES (?,?,?,?,?,?)",
            ("now", fp, "PREDICT", cap, ok, "now"))
