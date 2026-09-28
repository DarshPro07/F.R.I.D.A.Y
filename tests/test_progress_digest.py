from friday.progress_digest import compose, gather, outcome_line, \
    read_detail_level


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


# ---------------------------------------------------------------------------
# FR-POL-005: user-selectable progress detail. Same facts every time -
# only how much of them gets said changes.
# ---------------------------------------------------------------------------


def test_balanced_is_the_unset_default():
    """Not passing `detail` must behave exactly like explicit 'balanced' -
    the entire suite above depends on this staying true."""
    runs = [_run()]
    default = compose(runs, now=200, last_digest_at=0, cadence=180)
    explicit = compose(runs, now=200, last_digest_at=0, cadence=180,
                       detail="balanced")
    assert default.digest == explicit.digest


def test_critical_only_drops_process_chatter_but_keeps_the_facts():
    runs = [_run(current="deploying")]
    d = compose(runs, now=200, last_digest_at=0, cadence=180,
               detail="critical-only")
    assert "read policy.py" in d.digest   # last line: still there
    assert "deploying" in d.digest        # next action: still there
    assert "claude-sonnet" not in d.digest  # model chatter: gone
    assert "did 2 tools" not in d.digest    # tool count: gone


def test_detailed_is_a_superset_of_balanced_information():
    runs = [_run(elapsed_s=42)]
    balanced = compose(runs, now=200, last_digest_at=0, cadence=180,
                       detail="balanced").digest
    detailed = compose(runs, now=200, last_digest_at=0, cadence=180,
                       detail="detailed").digest
    assert "did 2 tools" in detailed and "did 2 tools" in balanced
    assert "42s elapsed" in detailed
    assert "42s elapsed" not in balanced


def test_milestone_detail_levels():
    runs = [_run(status="COMPLETE", result="fixed the bug", seq=9,
                 task="patch the rate limiter")]
    critical = compose(runs, now=5, last_digest_at=0, cadence=180,
                       detail="critical-only")
    balanced = compose(runs, now=5, last_digest_at=0, cadence=180,
                       detail="balanced")
    detailed = compose(runs, now=5, last_digest_at=0, cadence=180,
                       detail="detailed")
    assert critical.milestones[0] == "fixed the bug"
    assert "claude-sonnet" not in critical.milestones[0]
    assert "fixed the bug" in balanced.milestones[0]
    assert "claude-sonnet" in balanced.milestones[0]
    assert "patch the rate limiter" not in balanced.milestones[0]
    assert "patch the rate limiter" in detailed.milestones[0]
    assert "fixed the bug" in detailed.milestones[0]


def test_unknown_detail_level_falls_back_to_balanced_not_a_crash():
    runs = [_run()]
    d = compose(runs, now=200, last_digest_at=0, cadence=180, detail="yolo")
    balanced = compose(runs, now=200, last_digest_at=0, cadence=180,
                       detail="balanced")
    assert d.digest == balanced.digest


def test_outcome_line_detail_levels():
    record = {"status": "COMPLETE", "task": "ship the release",
              "result": "shipped", "model": "claude-sonnet",
              "route_reason": "capacity", "work_run_id": "wr-9"}
    balanced = outcome_line(record, now=0)
    critical = outcome_line(record, now=0, detail="critical-only")
    detailed = outcome_line(record, now=0, detail="detailed")
    assert "It ran on" in balanced and "shipped" in balanced
    assert "It ran on" not in critical
    assert "shipped" in critical
    assert "It ran on" in detailed
    assert "wr-9" in detailed and "wr-9" not in balanced


def test_read_detail_level_defaults_to_balanced_when_nothing_set(tmp_path):
    assert read_detail_level(tmp_path) == "balanced"


def test_read_detail_level_reads_the_contract(tmp_path):
    import json
    (tmp_path / "operating_contract.json").write_text(
        json.dumps({"progress_detail": "critical-only"}), encoding="utf-8")
    assert read_detail_level(tmp_path) == "critical-only"


def test_read_detail_level_ignores_unknown_value(tmp_path):
    import json
    (tmp_path / "operating_contract.json").write_text(
        json.dumps({"progress_detail": "yolo"}), encoding="utf-8")
    assert read_detail_level(tmp_path) == "balanced"
