"""
The canonical suite runner (`scripts/baseline_suite.py`) never reaches the
live `.venv`.

AGENTS.md: tests run in `.venv-verify`, never the live `.venv`, which the
running agent uses. With no `.venv-verify` - the case on this machine - the
runner fell back to `.venv`, and on 2026-09-26 it started all four chunks
there. That was harmless only because the live venv has no pytest (V7-D31).
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _runner():
    spec = importlib.util.spec_from_file_location(
        "baseline_suite_under_test", ROOT / "scripts" / "baseline_suite.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _python(root: Path, venv: str) -> Path:
    exe = root / venv / "Scripts" / "python.exe"
    exe.parent.mkdir(parents=True)
    exe.write_bytes(b"")
    return exe


def test_with_no_verify_venv_the_default_is_not_the_live_venv(tmp_path, monkeypatch):
    runner = _runner()
    live = _python(tmp_path, ".venv")
    monkeypatch.setattr(runner, "ROOT", tmp_path)
    assert Path(runner._default_python()) != live
    assert runner._default_python() == sys.executable


def test_the_verify_venv_is_still_preferred(tmp_path, monkeypatch):
    runner = _runner()
    _python(tmp_path, ".venv")
    verify = _python(tmp_path, ".venv-verify")
    monkeypatch.setattr(runner, "ROOT", tmp_path)
    assert Path(runner._default_python()) == verify


def test_the_live_venv_is_refused_even_when_asked_for(tmp_path, monkeypatch, capsys):
    runner = _runner()
    live = _python(tmp_path, ".venv")
    monkeypatch.setattr(runner, "ROOT", tmp_path)
    assert runner.main(["--python", str(live), "--out", str(tmp_path / "out")]) == 2
    assert "live .venv" in capsys.readouterr().err
    assert not (tmp_path / "out").exists(), "the refusal came after the run began"


def test_the_run_names_every_live_file_it_changed(tmp_path):
    """TEST-ISOLATION-001: the runner compares the repository's data/ and logs/
    before and after, leaving out only its own output directory."""
    R = _runner()
    (tmp_path / "data" / "run").mkdir(parents=True)
    (tmp_path / "logs").mkdir()
    (tmp_path / "data" / "store.sqlite3").write_bytes(b"a")
    (tmp_path / "logs" / "friday.log").write_text("x", encoding="utf-8")
    before = R._live_state(tmp_path, exclude=tmp_path / "data" / "run")
    (tmp_path / "data" / "store.sqlite3").write_bytes(b"ab")
    (tmp_path / "data" / "nonces.json").write_text("{}", encoding="utf-8")
    (tmp_path / "data" / "run" / "summary.txt").write_text("own output", encoding="utf-8")
    after = R._live_state(tmp_path, exclude=tmp_path / "data" / "run")
    assert R._changed(before, after) == ["data/nonces.json", "data/store.sqlite3"]


def test_a_change_is_blamed_on_the_suite_only_when_friday_was_not_running():
    R = _runner()
    ok, line = R._verdict([], running=[], watched=12)
    assert ok and "unchanged" in line
    ok, line = R._verdict(["data/audit.sqlite3"], running=[], watched=12)
    assert not ok and "TEST-ISOLATION-001" in line and "data/audit.sqlite3" in line
    ok, line = R._verdict(["data/audit.sqlite3"], running=["4242"], watched=12)
    assert ok and "FRIDAY was running" in line


def test_a_run_whose_chunks_pass_still_fails_when_live_state_changed(tmp_path, monkeypatch):
    """The acceptance is the exit code: a green suite that changed the live
    state is a failed run. The chunks are stood in for; the verdict is not."""
    R = _runner()
    monkeypatch.setattr(R, "run_chunk", lambda *args, **kwargs: (0, "1 passed", 0.0))
    monkeypatch.setattr(R, "_friday_running", lambda root: [])
    for after, code in (({"data/ada.sqlite3": (1, 1)}, 0), ({"data/ada.sqlite3": (2, 2)}, 1)):
        readings = iter([{"data/ada.sqlite3": (1, 1)}, after])
        monkeypatch.setattr(R, "_live_state", lambda root, *, exclude: next(readings))
        out = tmp_path / f"run{code}"
        assert R.main(["--out", str(out), "--python", sys.executable, "--chunks", "1"]) == code
        summary = (out / "summary.txt").read_text(encoding="utf-8")
        assert ("live_state CHANGED" in summary) == bool(code), summary
