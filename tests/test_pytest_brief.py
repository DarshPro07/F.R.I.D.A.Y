"""scripts/pytest_brief.py: short digest on stdout, full output in the log."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

BRIEF = Path(__file__).resolve().parent.parent / "scripts" / "pytest_brief.py"


def test_brief_prints_counts_and_failing_id_and_logs_full_traceback(tmp_path):
    t = tmp_path / "test_sample.py"
    t.write_text("def test_ok():\n    assert True\n\n\ndef test_bad():\n    assert 1 == 2, 'boom-marker'\n")
    log = tmp_path / "run.log"
    res = subprocess.run(
        [sys.executable, str(BRIEF), "--log", str(log), "--", t.name],
        capture_output=True, text=True, timeout=120, cwd=tmp_path,
    )
    assert res.returncode == 1, res.stdout + res.stderr + log.read_text()[:1500]
    lines = res.stdout.splitlines()
    assert len(lines) <= 15
    assert "1 failed" in lines[0] and "1 passed" in lines[0]
    assert any("test_sample.py::test_bad" in ln and "boom-marker" in ln for ln in lines)
    assert lines[-1] == f"full log: {log}"
    full = log.read_text(encoding="utf-8")
    assert "assert 1 == 2" in full and "assert 1 == 2" not in res.stdout
    assert "test_ok" not in "\n".join(lines)
    assert len(full.splitlines()) > len(lines)


def test_default_log_is_outside_repo():
    spec_path = BRIEF.parent.parent
    import importlib.util

    spec = importlib.util.spec_from_file_location("pytest_brief_under_test", BRIEF)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    log = mod.default_log()
    assert spec_path not in log.parents
