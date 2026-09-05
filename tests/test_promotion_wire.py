"""S7's gate is only worth anything if the bridge actually calls it: a terminal
work run must route its handoff through memory_promotion exactly once."""
import pytest

from friday import hermes_bridge as hb


@pytest.fixture
def db(tmp_path, monkeypatch):
    path = tmp_path / "wire.sqlite3"
    monkeypatch.setenv("ADA_DB", str(path))
    from friday.toolsets import memory as M
    M.reset_store(None)
    yield path
    M.reset_store(None)


def test_terminal_run_routes_its_handoff_through_the_promotion_gate(db, monkeypatch):
    import friday.memory_promotion as MP
    seen = []
    monkeypatch.setattr(MP, "promote_handoff", lambda handoff, **kw: seen.append(handoff) or [])
    log = hb.WorkRunLog(db)
    run_id = log.create(task="wire check")
    log.update(run_id, status=hb.COMPLETE, result="wired the gate",
               model="fake-model", route_reason="trivial")
    assert len(seen) == 1 and "wired the gate" in seen[0].summary
    # A second terminal write is the same run: the claim is already taken,
    # so the gate is not asked twice.
    log.update(run_id, status=hb.COMPLETE, result="wired the gate")
    assert len(seen) == 1
