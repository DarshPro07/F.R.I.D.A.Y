"""The canonical suite runner's own contract: a verdict line names its
host state, and `--resume` never inherits green chunks from a different
tree. Runs the runner in-process against a stub pytest (`--python` pointed at
a script that records what it was asked to run), so nothing here takes
minutes or touches the real suite."""
from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _load():
    spec = importlib.util.spec_from_file_location("baseline_suite", ROOT / "scripts" / "baseline_suite.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _fake_python(tmp_path, exit_code=0):
    """A 'python' whose `-m pytest ...` writes the files it was given and exits."""
    script = tmp_path / "fakepy.py"
    script.write_text(
        "import sys\n"
        "files=[a for a in sys.argv[1:] if a.startswith('tests/')]\n"
        "print('RAN', len(files), 'files')\n"
        "print('%d passed in 0.01s' % len(files))\n"
        f"sys.exit({exit_code})\n", encoding="utf-8")
    # a trampoline so the runner can exec it as `python`: .cmd on Windows,
    # a shell script elsewhere - never the real interpreter, which would run
    # the real suite
    if os.name == "nt":
        cmd = tmp_path / "fakepy.cmd"
        cmd.write_text(f'@"{sys.executable}" "{script}" %*\n', encoding="utf-8")
    else:
        cmd = tmp_path / "fakepy.sh"
        cmd.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{script}" "$@"\n', encoding="utf-8")
        cmd.chmod(0o755)
    return str(cmd)


def test_verdict_lines_carry_host_state(tmp_path, monkeypatch):
    mod = _load()
    out = tmp_path / "out"
    rc = mod.main(["--out", str(out), "--chunks", "2", "--python", _fake_python(tmp_path)])
    assert rc == 0
    text = (out / "summary.txt").read_text(encoding="utf-8")
    assert "host[" in text.splitlines()[0]
    for line in text.splitlines()[1:]:
        assert line.startswith("chunk") and " host[" in line and "free_mb=" in line, line


def test_resume_keeps_green_chunks_on_the_same_tree_only(tmp_path, monkeypatch):
    mod = _load()
    out = tmp_path / "out"
    fake = _fake_python(tmp_path)
    monkeypatch.setattr(mod, "_tree_id", lambda: "abc1234")
    assert mod.main(["--out", str(out), "--chunks", "2", "--python", fake]) == 0
    # simulate the second chunk having died (not exit=0)
    summary = out / "summary.txt"
    lines = summary.read_text(encoding="utf-8").splitlines()
    lines[-1] = lines[-1].replace(" exit=0 ", " exit=1073807364 ")
    summary.write_text("\n".join(lines) + "\n", encoding="utf-8")

    ran = []
    real_run_chunk = mod.run_chunk

    def spy(python, files, log, **kw):
        ran.append(len(files))
        return real_run_chunk(python, files, log, **kw)
    monkeypatch.setattr(mod, "run_chunk", spy)

    # same tree -> chunk0 kept, only chunk1 runs
    assert mod.main(["--out", str(out), "--chunks", "2", "--python", fake, "--resume"]) == 0
    assert len(ran) == 1
    assert "resume=[0]" in summary.read_text(encoding="utf-8")

    # different tree (same commit, more edits) -> nothing kept, both run  (negative control)
    ran.clear()
    monkeypatch.setattr(mod, "_tree_id", lambda: "abc1234+deadbeef00")
    assert mod.main(["--out", str(out), "--chunks", "2", "--python", fake, "--resume"]) == 0
    assert len(ran) == 2
