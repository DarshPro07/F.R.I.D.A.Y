"""scripts/code_capsule.py: the ~1k-token code map rendered for a worker packet."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
_SPEC = importlib.util.spec_from_file_location(
    "code_capsule_under_test", _ROOT / "scripts" / "code_capsule.py"
)
cc = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = cc  # dataclasses resolve their module through sys.modules
_SPEC.loader.exec_module(cc)

MOD = '''"""Widgets for the demo.

Second paragraph is not shown.
"""


class Widget(Base):
    def spin(self, n: int = 1) -> int:
        return n

    def _hidden(self):
        return 0


class _Internal:
    def go(self):
        return 1


def build(a, b=2) -> str:
    return ""


def _helper():
    return None
'''


def _tree(tmp_path: Path) -> Path:
    (tmp_path / "tests").mkdir()
    (tmp_path / "mod.py").write_text(MOD)
    (tmp_path / "user.py").write_text("import mod\n")
    (tmp_path / "tests" / "test_mod.py").write_text("import mod\n")
    (tmp_path / "notes.txt").write_text("a\nb\n")
    (tmp_path / "broken.py").write_text("def (:\n")
    return tmp_path


def test_each_line_kind_is_rendered(tmp_path):
    root = _tree(tmp_path)
    out = cc.capsule(["mod.py:10-40"], root=root)
    lines = out.splitlines()
    assert lines[0] == f"## mod.py ({len(MOD.splitlines())} lines)"
    assert "  Widgets for the demo." in lines
    assert any(ln.startswith("  L") and "class Widget(Base)" in ln for ln in lines)
    assert any(ln.startswith("    L") and "def spin(self, n: int=1) -> int" in ln for ln in lines)
    assert any(ln.startswith("  L") and "def build(a, b=2) -> str" in ln for ln in lines)
    assert "tests: tests/test_mod.py" in lines
    assert "imported by: user.py" in lines


def test_citation_backslashes_missing_nonpy_and_unparseable(tmp_path):
    root = _tree(tmp_path)
    out = cc.capsule(["tests\\test_mod.py:1-2", "gone.py", "notes.txt", "broken.py"], root=root)
    assert "## tests/test_mod.py (1 lines)" in out
    assert "## gone.py (missing)" in out
    assert "## notes.txt (2 lines)" in out
    assert "## broken.py (1 lines) (unparseable)" in out


def test_importers_are_capped_at_five(tmp_path):
    root = _tree(tmp_path)
    for i in range(7):
        (root / f"imp{i}.py").write_text("import mod\n")
    out = cc.capsule(["mod.py"], root=root)
    line = next(ln for ln in out.splitlines() if ln.startswith("imported by:"))
    assert line.endswith("(+3)")
    assert line.count(".py") == 5


def test_budget_sheds_private_members_before_public_ones(tmp_path):
    root = _tree(tmp_path)
    full = cc.capsule(["mod.py"], root=root, budget_tokens=10_000)
    assert "_helper" in full and "_hidden" in full and "_Internal" in full

    no_private = cc.capsule(["mod.py"], root=root, budget_tokens=cc.estimate_tokens(full) - 1)
    assert "_helper" not in no_private and "_hidden" not in no_private and "_Internal" not in no_private
    assert "def spin" in no_private and "def build" in no_private

    no_methods = cc.capsule(["mod.py"], root=root, budget_tokens=cc.estimate_tokens(no_private) - 1)
    assert "def spin" not in no_methods and "class Widget" in no_methods and "def build" in no_methods
    assert "Widgets for the demo." in no_methods

    no_doc = cc.capsule(["mod.py"], root=root, budget_tokens=cc.estimate_tokens(no_methods) - 1)
    assert "Widgets for the demo." not in no_doc and "def build" in no_doc

    tiny = cc.capsule(["mod.py"], root=root, budget_tokens=cc.estimate_tokens(no_doc) - 1)
    assert "(+" in tiny and cc.estimate_tokens(tiny) <= cc.estimate_tokens(no_doc) - 1
    assert "## mod.py" in tiny and "tests: tests/test_mod.py" in tiny and "imported by: user.py" in tiny


def test_cli_prints_the_capsule(tmp_path, capsys):
    assert cc.main(["scripts/code_capsule.py", "--budget", "500"]) == 0
    assert "## scripts/code_capsule.py" in capsys.readouterr().out


def test_real_repo_capsule_of_build_kernel():
    out = cc.capsule(["scripts/build_kernel.py"])
    assert "ready" in out
    assert "tests/test_build_kernel.py" in out
    assert cc.estimate_tokens(out) <= 1000
