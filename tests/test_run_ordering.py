"""
"The newest run" is the one started last, even when two share a timestamp.

Python 3.11 on Windows reads a clock that advances in steps of about 15 ms,
so two runs started close together can carry the same `started_at`, and an
ORDER BY on it alone leaves their order to chance. `product_status` then
answered about the older of two catalogues (seen once in the four-chunk
suite, 2026-09-27).
"""
from __future__ import annotations

from friday import store as S


def test_the_newest_product_run_wins_a_same_instant_tie(tmp_path, monkeypatch):
    store = S.Store(tmp_path / "ada.sqlite3")
    monkeypatch.setattr(S, "now_iso", lambda: "2026-09-27T08:00:00.000000+00:00")
    for run_id in ("RUN-first", "RUN-second", "RUN-third"):
        store.start_product_run(run_id, source="catalogue.csv", total=1,
                                schema_version=1, stages=[])
    assert [r["run_id"] for r in store.product_runs()] == [
        "RUN-third", "RUN-second", "RUN-first"]


def test_the_newest_continuous_run_wins_a_same_instant_tie(tmp_path):
    from friday import continuity as C

    store = S.Store(tmp_path / "ada.sqlite3")
    manager = C.ContinuityManager(store)
    first = manager.start_run("first objective", initial_task="a")
    second = manager.start_run("second objective", initial_task="b")
    store._conn.execute("UPDATE runs SET updated_at='2026-09-27T08:00:00+00:00'")
    assert [s.run_id for s in manager.list_runs()] == [second.run_id, first.run_id]
