"""
No test runs anything in the live `.venv` (AGENTS.md; V7-D31, V7-D41).

The live `.venv` belongs to the running agent. A test that spawns its
interpreter tests the live environment's packages instead of the one the
suite runs in, and could leave state inside it. Subprocesses the suite
starts use `sys.executable`, the suite's own interpreter.
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

#: `ROOT / ".venv" / "Scripts" / "python.exe"` and its spellings.
_LIVE_INTERPRETER = re.compile(
    r"""['"]\.venv['"]\s*/\s*['"](?:Scripts|bin)['"]\s*/\s*['"]python""")


def test_no_test_spawns_the_live_venv_interpreter():
    offenders = []
    for source in sorted((ROOT / "tests").glob("*.py")):
        if source.name == Path(__file__).name:
            continue
        for number, line in enumerate(source.read_text(encoding="utf-8").splitlines(), 1):
            if _LIVE_INTERPRETER.search(line):
                offenders.append(f"{source.name}:{number}")
    assert offenders == [], f"these tests reach the live .venv interpreter: {offenders}"
