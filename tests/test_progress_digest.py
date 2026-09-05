from friday.progress_digest import compose, gather


def _run(**kw):
    base = {"work_run_id": "wr-1", "status": "WORKING", "seq": 1,
            "tools": 2, "line": "read policy.py", "current": "editing x.py",
            "model": "claude-sonnet", "route_reason": "capacity"}
    base.update(kw)
    return base


def test_digest_composed_only_from_events_and_dedupes():
    runs = [_run(), _run()]  # same work_run_id/seq twice
    d = compose(runs, now=1000, last_digest_at=0, cadence=180)
    assert d.digest is not None
    assert d.digest.count("did 2 tools") == 1


def test_cadence_holds_digest_until_due():
    runs = [_run()]
    d = compose(runs, now=100, last_digest_at=0, cadence=180)
    assert d.digest is None
    assert d.milestones == []
    d2 = compose(runs, now=200, last_digest_at=0, cadence=180)
    assert d2.digest is not None
    assert "capacity" in d2.digest


def test_completion_is_a_milestone_not_a_digest_line():
    runs = [_run(status="COMPLETE", result="fixed the bug", seq=9)]
    d = compose(runs, now=5, last_digest_at=0, cadence=180)
    assert d.milestones and "fixed the bug" in d.milestones[0]
    assert d.digest is None  # nothing left to build a cadence digest from


def test_no_raise_on_garbage():
    garbage = [None, {}, {"work_run_id": None}, "not a dict",
               {"work_run_id": "wr-2"}]
    d = compose(garbage, now=1, last_digest_at=0, cadence=180)
    assert isinstance(d.milestones, list)

    d2 = compose(None, now=1, last_digest_at=0, cadence=180)
    assert d2.milestones == [] and d2.digest is None


def test_gather_adds_recently_terminal_run_once():
    import time as _time

    class FakeLog:
        def active(self):
            return []

        def recent(self, limit=12):
            return [{"work_run_id": "wr-done", "status": "COMPLETE",
                     "last_event_at": _time.time()}]

    class FakeSup:
        log = FakeLog()

        def progress(self, wid):
            return {"work_run_id": wid, "seq": 3, "status": "COMPLETE"}

    out = gather(FakeSup())
    assert len(out) == 1 and out[0]["work_run_id"] == "wr-done"


def test_gather_merges_row_and_progress():
    class FakeLog:
        def active(self):
            return [{"work_run_id": "wr-3", "model": "m", "route_reason": "r",
                      "status": "WORKING"}]

    class FakeSup:
        log = FakeLog()

        def progress(self, wid):
            return {"work_run_id": wid, "seq": 4, "tools": 1, "line": "x",
                     "status": "WORKING", "current": "y"}

    out = gather(FakeSup())
    assert out == [{"work_run_id": "wr-3", "model": "m", "route_reason": "r",
                     "status": "WORKING", "seq": 4, "tools": 1, "line": "x",
                     "current": "y"}]


def test_caller_must_not_store_next_at_when_no_digest_fired():
    """
    `Digest.next_at` on a non-firing poll is a future DUE time, not a past
    firing time. `agent_friday.speak_progress_digests` used to store it into
    `last_digest_at` unconditionally, which pushed the cadence clock forward
    on every poll and starved the digest after the first held cadence.
    This reproduces the caller's poll loop with the buggy rule (always
    store next_at) and the fixed rule (store only when digest fired).
    """
    runs = [_run()]
    cadence = 180

    # Buggy caller rule: never fires because last_digest_at keeps chasing
    # next_at, so `now - last_digest_at >= cadence` is never true.
    last_digest_at = 0
    for now in range(0, 1000, 50):
        d = compose(runs, now=now, last_digest_at=last_digest_at,
                    cadence=cadence)
        last_digest_at = d.next_at  # the bug
    assert d.digest is None

    # Fixed caller rule: only advance on a real fire.
    last_digest_at = 0
    fired = False
    for now in range(0, 1000, 50):
        d = compose(runs, now=now, last_digest_at=last_digest_at,
                    cadence=cadence)
        if d.digest:
            last_digest_at = d.next_at
            fired = True
    assert fired
