"""The product chain: back off after a pressure stop that ran no wave; hold the day's spend cap."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import product_chain as PC  # noqa: E402


def test_a_pressure_stop_without_a_wave_backs_off(monkeypatch, tmp_path):
    # 2026-10-02: the chain's admission probe and the program's own check
    # disagreed at the threshold, so it started and stopped a program every minute.
    state = tmp_path / "build_state.json"
    state.write_text('{"spend_usd_total": 0, "waves": [{"n": 1}]}', encoding="utf-8")
    monkeypatch.setattr(PC, "STATE", state)
    monkeypatch.setattr(PC, "RUNS", tmp_path)
    monkeypatch.setattr(PC, "wait_for_admission", lambda hours, start=None: True)
    runs = iter(["stopped: pressure", "stopped: no ready work"])
    monkeypatch.setattr(PC.subprocess, "run", lambda *a, **kw: subprocess.CompletedProcess(a, 0, next(runs) + "\n", ""))
    sleeps = []
    monkeypatch.setattr(PC.time, "sleep", sleeps.append)
    assert PC.main(["--total-usd", "45"]) == 0
    assert sleeps == [PC.PRESSURE_BACKOFF_S]


def _default_day_budget(monkeypatch) -> None:
    """These tests are about the chain's budget logic, not today's real budget: the owner's
    standing entry in data/build_runs/day_budget.json (2026-10-05: no daily cap) must not
    decide them, so the day's limits are the code's defaults here."""
    monkeypatch.setattr(PC.K, "day_limits", lambda runs_dir=None: (PC.K.DAY_SOFT_USD, PC.K.DAY_HARD_USD))


def _chain(monkeypatch, tmp_path, day_spent: float) -> list:
    state = tmp_path / "build_state.json"
    state.write_text('{"spend_usd_total": 0, "waves": []}', encoding="utf-8")
    monkeypatch.setattr(PC, "STATE", state)
    monkeypatch.setattr(PC, "wait_for_admission", lambda hours, start=None: True)
    monkeypatch.setattr(PC.K, "spend_committed", lambda runs_dir: (day_spent, 0.0))
    _default_day_budget(monkeypatch)
    cmds = []
    monkeypatch.setattr(PC.subprocess, "run", lambda cmd, **kw: cmds.append(cmd)
                        or subprocess.CompletedProcess(cmd, 0, "stopped: no ready work\n", ""))
    return cmds


def test_a_run_never_spends_past_the_day_cap(monkeypatch, tmp_path):
    # 2026-10-02: a chain given $30 started after the day had already spent
    # about $28; its own cap never saw that, and the day closed at $49.66 of $45.
    cmds = _chain(monkeypatch, tmp_path, day_spent=38.0)
    assert PC.main(["--day-usd", "45", "--total-usd", "30"]) == 0
    assert cmds[0][cmds[0].index("--max-usd") + 1] == "7.00"


def test_no_run_starts_at_the_soft_stop(monkeypatch, tmp_path):
    # 2026-10-03 owner: $40 soft stop, $45 hard ceiling, open leases counted.
    cmds = _chain(monkeypatch, tmp_path, day_spent=PC.K.DAY_SOFT_USD)
    assert PC.main([]) == 0
    assert cmds == []


@pytest.mark.parametrize("dry_run,admitted", [("CTX-002 SHIP P0\n", True),
                                              ("nothing to plan: no ready work\n", True),
                                              ("nothing to plan: pressure (CTX-002 needs 2.8 GB)\n", False)])
def test_the_admission_probe_is_the_programs_own_dry_run(monkeypatch, dry_run, admitted):
    # 2026-10-03: the probe asked about a generic worker, priced at the heaviest recent
    # peak (CTX-002's models), so no run started while lighter jobs would have fitted.
    seen = []
    monkeypatch.setattr(PC.subprocess, "run", lambda cmd, **kw: seen.append(cmd)
                        or subprocess.CompletedProcess(cmd, 0, dry_run, ""))
    ok, why = PC.admits()
    assert ok is admitted and "--dry-run" in seen[0] and why
    if not admitted:
        assert "CTX-002 needs 2.8 GB" in why


def test_a_spend_stop_that_ran_no_wave_ends_the_chain(monkeypatch, tmp_path):
    # 2026-10-03 (chain 10): with $3.28 left in its budget and only $7.50 jobs queued,
    # every run stopped on "spend" at once and the chain started another a minute later.
    cmds = _chain(monkeypatch, tmp_path, day_spent=10.0)

    def run(cmd, **kw):
        cmds.append(cmd)
        assert len(cmds) <= 3, "the chain kept starting runs that could afford nothing"
        return subprocess.CompletedProcess(cmd, 0, "stopped: spend\n", "")
    monkeypatch.setattr(PC.subprocess, "run", run)
    assert PC.main(["--total-usd", "30"]) == 0
    assert len(cmds) == 1
    assert "nothing affordable" in PC.json.loads(PC.STATE.read_text(encoding="utf-8"))["product_chain"]["detail"]


def test_a_spent_day_starts_no_run(monkeypatch, tmp_path):
    cmds = _chain(monkeypatch, tmp_path, day_spent=49.66)
    assert PC.main(["--day-usd", "45"]) == 0
    assert cmds == []
    assert "day spend cap" in PC.json.loads(PC.STATE.read_text(encoding="utf-8"))["product_chain"]["detail"]


def _until_done(monkeypatch, tmp_path, outcomes, day_spent=(0.0,)):
    """Run main(--until-done) with scripted program outcomes; returns (commands, sleeps)."""
    state = tmp_path / "build_state.json"
    state.write_text('{"spend_usd_total": 0, "waves": []}', encoding="utf-8")
    monkeypatch.setattr(PC, "STATE", state)
    monkeypatch.setattr(PC, "wait_for_admission", lambda hours, start=None: True)
    days = iter(day_spent)
    last = [0.0]

    def committed(runs_dir):
        last[0] = next(days, last[0])
        return last[0], 0.0
    monkeypatch.setattr(PC.K, "spend_committed", committed)
    _default_day_budget(monkeypatch)
    outs, cmds, sleeps = iter(outcomes), [], []

    def run(cmd, **kw):
        cmds.append(cmd)
        assert len(cmds) <= 12, "the loop did not end"
        return subprocess.CompletedProcess(cmd, 0, f"stopped: {next(outs)}\n", "")
    monkeypatch.setattr(PC.subprocess, "run", run)
    monkeypatch.setattr(PC.time, "sleep", sleeps.append)
    monkeypatch.setattr(PC, "seconds_to_next_day", lambda: 123.0)
    assert PC.main(["--until-done"]) == 0
    return cmds, sleeps


def test_until_done_works_through_every_priority_then_stops(monkeypatch, tmp_path):
    # Owner, 2026-10-04: keep building until the whole PRD is done, not just P0.
    cmds, _ = _until_done(monkeypatch, tmp_path, ["no ready work"] * 4)
    assert [c[c.index("--priority") + 1] for c in cmds] == ["P0", "P1", "P2", "P3"]
    assert "every priority" in PC.json.loads(PC.STATE.read_text(encoding="utf-8"))["product_chain"]["detail"]


def test_until_done_waits_for_the_next_day_at_the_day_budget(monkeypatch, tmp_path):
    cmds, sleeps = _until_done(monkeypatch, tmp_path, ["no ready work"] * 4,
                               day_spent=(PC.K.DAY_SOFT_USD, 0.0))
    assert sleeps[0] == 123.0 and len(cmds) == 4  # slept to the next day, then ran


def test_until_done_backs_off_and_retries_a_no_progress_or_busy_stop(monkeypatch, tmp_path):
    cmds, sleeps = _until_done(monkeypatch, tmp_path,
                               ["no progress", "another program is running"] + ["no ready work"] * 4)
    assert sleeps[:2] == [PC.RETRY_AFTER_S, PC.BUSY_RETRY_S] and len(cmds) == 6


def test_until_done_stops_for_good_on_a_crash(monkeypatch, tmp_path):
    cmds, _ = _until_done(monkeypatch, tmp_path, ["integration crash"])
    assert len(cmds) == 1


def test_until_done_waits_out_a_dirty_tree(monkeypatch, tmp_path):
    # 2026-10-04: a controller edit left the checkout dirty for a few minutes and the loop ended.
    cmds, sleeps = _until_done(monkeypatch, tmp_path, ["dirty tree"] + ["no ready work"] * 4)
    assert sleeps[0] == PC.RETRY_AFTER_S and len(cmds) == 5



def test_until_done_rechecks_the_top_priority_after_every_real_run(monkeypatch, tmp_path):
    # 2026-10-06: MODEL-012 (P0) was unparked while the chain worked through P1, and
    # waited behind the whole P1 queue because only a new day re-checked from the top.
    cmds, _ = _until_done(monkeypatch, tmp_path, [
        "no ready work", "max waves", "max waves", "no ready work", "no ready work",
        "no ready work", "no ready work"])
    assert [c[c.index("--priority") + 1] for c in cmds] == ["P0", "P1", "P0", "P0", "P1", "P2", "P3"]


def test_lighter_work_runs_while_every_ready_higher_job_waits_on_memory(monkeypatch):
    # 2026-10-09: the chain idled five hours behind MODEL-012 (4.0 GB) and CTX-002 (4.3 GB)
    # at about 3 GB free; the probe only ever asked about P0.
    answers = {"P0": (False, "nothing to plan: pressure (MODEL-012 needs 4.0 GB)"),
               "P1": (True, "BBOX-001 SHIP NORMAL"), "P2": (True, "x")}
    asked = []
    monkeypatch.setattr(PC, "admits", lambda p="P0": asked.append(p) or answers[p])
    assert PC.admitted_priority("P0") == ("P1", "BBOX-001 SHIP NORMAL") and asked == ["P0", "P1"]
    answers.update(P1=(True, "nothing to plan: no ready work"), P2=(False, "nothing to plan: pressure"),
                   P3=(True, "nothing to plan: no ready work"))
    priority, why = PC.admitted_priority("P0")
    assert priority is None and "MODEL-012" in why


def test_the_chain_runs_the_backfilled_priority_then_rechecks_from_the_top(monkeypatch, tmp_path):
    state = tmp_path / "build_state.json"
    state.write_text('{"spend_usd_total": 0, "waves": []}', encoding="utf-8")
    monkeypatch.setattr(PC, "STATE", state)
    monkeypatch.setattr(PC, "RUNS", tmp_path)
    picks = iter(["P1"])
    seen_start = []
    monkeypatch.setattr(PC, "wait_for_admission",
                        lambda hours, start="P0": seen_start.append(start) or next(picks, start))
    monkeypatch.setattr(PC.K, "spend_committed", lambda runs_dir: (0.0, 0.0))
    cmds = []
    import itertools
    outs = itertools.chain(["stopped: max waves"], itertools.repeat("stopped: no ready work"))
    monkeypatch.setattr(PC.subprocess, "run", lambda cmd, **kw: cmds.append(cmd)
                        or subprocess.CompletedProcess(cmd, 0, next(outs) + "\n", ""))
    monkeypatch.setattr(PC.time, "sleep", lambda s: None)
    PC.main(["--until-done"])
    assert cmds[0][cmds[0].index("--priority") + 1] == "P1"      # backfilled while P0 waited
    assert seen_start[1] == "P0"                                  # the next probe starts at the top again
