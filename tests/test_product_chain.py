"""The product chain: back off after a pressure stop that ran no wave; hold the day's spend cap."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

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
    monkeypatch.setattr(PC, "wait_for_admission", lambda hours: True)
    runs = iter(["stopped: pressure", "stopped: no ready work"])
    monkeypatch.setattr(PC.subprocess, "run", lambda *a, **kw: subprocess.CompletedProcess(a, 0, next(runs) + "\n", ""))
    sleeps = []
    monkeypatch.setattr(PC.time, "sleep", sleeps.append)
    assert PC.main(["--total-usd", "45"]) == 0
    assert sleeps == [PC.PRESSURE_BACKOFF_S]


def _chain(monkeypatch, tmp_path, day_spent: float) -> list:
    state = tmp_path / "build_state.json"
    state.write_text('{"spend_usd_total": 0, "waves": []}', encoding="utf-8")
    monkeypatch.setattr(PC, "STATE", state)
    monkeypatch.setattr(PC, "wait_for_admission", lambda hours: True)
    monkeypatch.setattr(PC.K, "day_cost", lambda day, runs_dir: day_spent)
    cmds = []
    monkeypatch.setattr(PC.subprocess, "run", lambda cmd, **kw: cmds.append(cmd)
                        or subprocess.CompletedProcess(cmd, 0, "stopped: no ready work\n", ""))
    return cmds


def test_a_run_never_spends_past_the_day_cap(monkeypatch, tmp_path):
    # 2026-10-02: a chain given $30 started after the day had already spent
    # about $28; its own cap never saw that, and the day closed at $49.66 of $45.
    cmds = _chain(monkeypatch, tmp_path, day_spent=44.0)
    assert PC.main(["--day-usd", "45", "--total-usd", "30"]) == 0
    assert cmds[0][cmds[0].index("--max-usd") + 1] == "1.00"


def test_a_spent_day_starts_no_run(monkeypatch, tmp_path):
    cmds = _chain(monkeypatch, tmp_path, day_spent=49.66)
    assert PC.main(["--day-usd", "45"]) == 0
    assert cmds == []
    assert "day spend cap" in PC.json.loads(PC.STATE.read_text(encoding="utf-8"))["product_chain"]["detail"]
