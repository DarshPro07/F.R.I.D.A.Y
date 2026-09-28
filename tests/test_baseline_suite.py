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
