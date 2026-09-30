"""scripts/test_impact.py: static import graph and affected-test closure."""

from __future__ import annotations

import importlib.util
from pathlib import Path

_SPEC = importlib.util.spec_from_file_location(
    "test_impact_under_test", Path(__file__).resolve().parent.parent / "scripts" / "test_impact.py"
)
ti = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(ti)


def _tree(tmp_path: Path) -> dict[str, str]:
    (tmp_path / "tests").mkdir()
    (tmp_path / "a.py").write_text("X = 1\n")
    (tmp_path / "b.py").write_text("import a\n")
    (tmp_path / "tests" / "test_b.py").write_text("import b\n")
    (tmp_path / "tests" / "test_c.py").write_text("def test_c():\n    pass\n")
    return ti.collect_sources(tmp_path)


def test_transitive_closure_reaches_test_through_intermediate(tmp_path):
    graph = ti.build_graph(_tree(tmp_path))
    assert ti.affected_tests({"a.py"}, graph) == ["tests/test_b.py"]


def test_direct_closure_stops_at_first_hop(tmp_path):
    graph = ti.build_graph(_tree(tmp_path))
    assert ti.affected_tests({"a.py"}, graph, direct=True) == []
    assert ti.affected_tests({"b.py"}, graph, direct=True) == ["tests/test_b.py"]


def test_changed_test_is_selected_and_unrelated_is_not(tmp_path):
    graph = ti.build_graph(_tree(tmp_path))
    assert ti.affected_tests({"tests/test_c.py"}, graph) == ["tests/test_c.py"]


def test_package_and_relative_imports_and_script_string_mentions():
    sources = {
        "friday/__init__.py": "",
        "friday/x.py": "from . import y\n",
        "friday/y.py": "",
        "scripts/tool.py": "",
        "tests/test_x.py": "from friday.x import thing\n",
        "tests/test_s.py": "P = 'scripts/tool.py'\n",
    }
    graph = ti.build_graph(sources)
    assert ti.affected_tests({"friday/y.py"}, graph) == ["tests/test_x.py"]
    assert ti.affected_tests({"scripts/tool.py"}, graph, direct=True) == ["tests/test_s.py"]


def test_conftest_change_selects_every_test(tmp_path):
    graph = ti.build_graph(_tree(tmp_path))
    (tmp_path / "tests" / "conftest.py").write_text("")
    graph = ti.build_graph(ti.collect_sources(tmp_path))
    assert ti.affected_tests({"tests/conftest.py"}, graph) == ["tests/test_b.py", "tests/test_c.py"]
