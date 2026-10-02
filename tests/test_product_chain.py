"""The product chain: back off after a pressure stop that ran no wave."""
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
    monkeypatch.setattr(PC, "wait_for_admission", lambda hours: True)
    runs = iter(["stopped: pressure", "stopped: no ready work"])
    monkeypatch.setattr(PC.subprocess, "run", lambda *a, **kw: subprocess.CompletedProcess(a, 0, next(runs) + "\n", ""))
    sleeps = []
    monkeypatch.setattr(PC.time, "sleep", sleeps.append)
    assert PC.main(["--total-usd", "45"]) == 0
    assert sleeps == [PC.PRESSURE_BACKOFF_S]
