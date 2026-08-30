"""
The structural map, and the ways a map lies.

A map that is wrong is worse than no map, because it is trusted. The tests
that matter here are the ones about staleness, about approximate readings
being labelled, and about an empty project not producing a graph that later
reads as "already done".
"""
import time
import pytest
from friday import codegraph as G


@pytest.fixture
def project(tmp_path):
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "core.py").write_text(
        "import os\n"
        "\n"
        "\n"
        "def helper(x):\n"
        "    return os.path.join(x, 'y')\n"
        "\n"
        "\n"
        "class Engine:\n"
        "    def start(self):\n"
        "        return helper('a')\n"
        "\n"
        "    def stop(self):\n"
        "        return None\n",
        encoding="utf-8")
    (tmp_path / "pkg" / "app.py").write_text(
        "from pkg.core import Engine\n"
        "\n"
        "\n"
        "def main():\n"
        "    engine = Engine()\n"
        "    engine.start()\n"
        "    return helper('z')\n",
        encoding="utf-8")
    return tmp_path


def test_it_finds_functions_classes_and_methods(project):
    graph = G.CodeGraph.build(project)
    names = {s.name for s in graph.symbols}
    assert {"helper", "Engine", "start", "stop", "main"} <= names


def test_a_method_knows_its_class(project):
    graph = G.CodeGraph.build(project)
    start = next(s for s in graph.symbols if s.name == "start")
    assert start.kind == "method"
    assert start.parent == "Engine"
    assert start.qualified == "Engine.start"


def test_python_readings_are_marked_exact(project):
    graph = G.CodeGraph.build(project)
    assert all(s.exact for s in graph.symbols)


def test_an_approximate_reading_says_so(tmp_path):
    """
    A regex guess and a parser result must not be indistinguishable
    downstream. Something deciding how far to trust the map needs to know
    which it is holding.
    """
    (tmp_path / "ui.ts").write_text(
        "export function render(props) { return null }\n"
        "export class Widget {}\n", encoding="utf-8")
    graph = G.CodeGraph.build(tmp_path)
    assert {s.name for s in graph.symbols} == {"render", "Widget"}
    assert not any(s.exact for s in graph.symbols)


def test_a_file_that_does_not_parse_does_not_abandon_the_graph(project):
    (project / "pkg" / "broken.py").write_text("def (((\n", encoding="utf-8")
    graph = G.CodeGraph.build(project)
    assert any(s.name == "helper" for s in graph.symbols), \
        "one unparseable file cost the whole graph"


def test_the_usual_noise_is_not_walked(project):
    for junk in ("node_modules", "__pycache__", ".venv"):
        (project / junk).mkdir()
        (project / junk / "huge.py").write_text("def noise(): pass\n",
                                                encoding="utf-8")
    graph = G.CodeGraph.build(project)
    assert not any(s.name == "noise" for s in graph.symbols)


def test_an_enormous_file_is_skipped_and_recorded(project, monkeypatch):
    monkeypatch.setattr(G, "MAX_BYTES", 10)
    graph = G.CodeGraph.build(project)
    assert graph.skipped, "a skipped file left no trace"
    assert all(reason == "too large" for reason in graph.skipped.values())


def test_a_missing_directory_is_refused(tmp_path):
    with pytest.raises(NotADirectoryError):
        G.CodeGraph.build(tmp_path / "nowhere")


def test_find_prefers_an_exact_name(project):
    (project / "pkg" / "more.py").write_text(
        "def help_me(): pass\ndef helper(): pass\n", encoding="utf-8")
    graph = G.CodeGraph.build(project)
    assert graph.find("helper")[0].name == "helper"


def test_find_falls_back_to_contains(project):
    graph = G.CodeGraph.build(project)
    assert any(s.name == "helper" for s in graph.find("help"))


def test_find_with_nothing_to_look_for_returns_nothing(project):
    graph = G.CodeGraph.build(project)
    assert graph.find("") == []
    assert graph.find("   ") == []


def test_callers_answers_the_question_grep_answers_badly(project):
    """`helper` is called from two places and mentioned in an import."""
    graph = G.CodeGraph.build(project)
    callers = {s.qualified for s in graph.callers("helper")}
    assert callers == {"Engine.start", "main"}


def test_callers_of_something_nobody_calls_is_empty(project):
    graph = G.CodeGraph.build(project)
    assert graph.callers("stop") == []


def test_api_of_lists_what_a_file_offers(project):
    graph = G.CodeGraph.build(project)
    assert [s.name for s in graph.api_of("pkg/core.py")] == \
        ["helper", "Engine", "start", "stop"]


def test_api_of_hides_private_names_by_default(tmp_path):
    (tmp_path / "m.py").write_text(
        "def public(): pass\ndef _private(): pass\n", encoding="utf-8")
    graph = G.CodeGraph.build(tmp_path)
    assert [s.name for s in graph.api_of("m.py")] == ["public"]
    assert len(graph.api_of("m.py", include_private=True)) == 2


def test_the_repo_map_is_a_summary_not_the_whole_graph(project):
    """
    The whole graph in a context window reproduces the problem the graph
    exists to solve.
    """
    graph = G.CodeGraph.build(project)
    overview = graph.repo_map(limit=1)
    assert len(overview['largest']) == 1
    assert overview['symbols'] == len(graph.named())


def test_a_fresh_graph_is_not_stale(project):
    graph = G.CodeGraph.build(project)
    assert graph.stale() == []


def test_a_changed_file_is_stale(project):
    graph = G.CodeGraph.build(project)
    time.sleep(0.01)
    (project / "pkg" / "core.py").write_text(
        "def helper(): pass\ndef added(): pass\n", encoding="utf-8")
    assert "pkg/core.py" in graph.stale()


def test_a_new_file_is_stale(project):
    graph = G.CodeGraph.build(project)
    (project / "pkg" / "extra.py").write_text("def fresh(): pass\n",
                                              encoding="utf-8")
    assert "pkg/extra.py" in graph.stale()


def test_a_deleted_file_is_stale(project):
    graph = G.CodeGraph.build(project)
    (project / "pkg" / "app.py").unlink()
    assert "pkg/app.py" in graph.stale()


def test_refresh_re_reads_only_what_changed(project):
    graph = G.CodeGraph.build(project)
    time.sleep(0.01)
    (project / "pkg" / "core.py").write_text(
        "def renamed(): pass\n", encoding="utf-8")

    assert graph.refresh() == 1
    names = {s.name for s in graph.symbols}
    assert "renamed" in names
    assert "Engine" not in names, "the old symbols from that file survived"
    assert "main" in names, "an untouched file lost its symbols"
    assert graph.stale() == []


def test_refresh_drops_a_deleted_files_symbols(project):
    graph = G.CodeGraph.build(project)
    (project / "pkg" / "app.py").unlink()
    graph.refresh()
    assert not any(s.path == "pkg/app.py" for s in graph.symbols)


def test_refreshing_an_unchanged_graph_does_nothing(project):
    graph = G.CodeGraph.build(project)
    assert graph.refresh() == 0


def test_a_saved_graph_loads_back_the_same(project, tmp_path):
    graph = G.CodeGraph.build(project)
    out = graph.save(tmp_path / "cache" / "g.json")
    again = G.CodeGraph.load(out)

    assert again is not None
    assert len(again.symbols) == len(graph.symbols)
    assert {s.qualified for s in again.symbols} == \
        {s.qualified for s in graph.symbols}
    assert again.callers("helper"), "call edges did not survive the round trip"


def test_a_corrupt_cache_is_rebuilt_not_raised(tmp_path):
    """It is a cache. A cache that can halt the program is a liability."""
    bad = tmp_path / "g.json"
    bad.write_text("{not json", encoding="utf-8")
    assert G.CodeGraph.load(bad) is None


def test_loading_a_graph_that_was_never_saved_is_none(tmp_path):
    assert G.CodeGraph.load(tmp_path / "absent.json") is None


def test_an_empty_project_is_not_worth_mapping(tmp_path):
    """
    A new project has nothing to understand. Building anyway writes an empty
    graph that every later check reads as "already done".
    """
    (tmp_path / "README.md").write_text("# new\n", encoding="utf-8")
    graph = G.CodeGraph.build(tmp_path)
    assert not graph.worth_building()


def test_a_real_project_is_worth_mapping(project):
    assert G.CodeGraph.build(project).worth_building()


def test_ensure_returns_nothing_for_a_project_with_no_code(tmp_path, monkeypatch):
    monkeypatch.setattr(G, "graph_path", lambda root: tmp_path / "c" / "g.json")
    (tmp_path / "README.md").write_text("# new\n", encoding="utf-8")
    assert G.ensure(tmp_path) is None


def test_ensure_builds_caches_and_then_refreshes(project, tmp_path, monkeypatch):
    cache = tmp_path / "cache" / "g.json"
    monkeypatch.setattr(G, "graph_path", lambda root: cache)

    first = G.ensure(project)
    assert first is not None and cache.is_file()

    time.sleep(0.01)
    (project / "pkg" / "later.py").write_text("def later(): pass\n",
                                              encoding="utf-8")
    second = G.ensure(project)
    assert any(s.name == "later" for s in second.symbols), \
        "ensure returned a stale graph"


def test_a_property_read_makes_no_call_edge(tmp_path):
    """
    The limitation that matters, written down as a test because it has
    already caused one wrong deletion.

    `result.acts` is an attribute read, not a call, so it creates no edge.
    `callers("acts")` came back empty for a property that seven tests used,
    it was deleted on that evidence, and the tests caught it.

    This asserts the *limitation*, not a bug. Fixing it means resolving
    attribute reads, which needs type inference. The honest answer is that
    `callers()` tells you who calls something and never whether it is used.
    """
    (tmp_path / 'm.py').write_text("class Result:\n    @property\n    def acts(self):\n        return True\n\n\ndef decide(result):\n    if result.acts:\n        return 'yes'\n    return 'no'\n", encoding='utf-8')
    graph = G.CodeGraph.build(tmp_path)
    assert graph.find('acts'), 'the property was not even found as a symbol'
    assert graph.callers('acts') == [], 'if this now finds it, the docstring warning can be relaxed'


def test_a_name_passed_as_a_callback_makes_no_call_edge(tmp_path):
    """The same shape: `run(handler)` does not call `handler` here."""
    (tmp_path / 'm.py').write_text('def handler():\n    return 1\n\n\ndef wire(run):\n    return run(handler)\n', encoding='utf-8')
    graph = G.CodeGraph.build(tmp_path)
    assert graph.callers('handler') == []


def test_the_synthetic_module_symbol_stays_out_of_readable_queries(project):
    """
    `MODULE_SCOPE` carries a file's import-time references so reachability
    can see them. It is not a definition, and it turned up first as part of a
    module's API - a thing an agent would then have tried to call.
    """
    graph = G.CodeGraph.build(project)
    assert any((s.name == G.MODULE_SCOPE for s in graph.symbols)), 'the reachability edges are missing entirely'
    assert G.MODULE_SCOPE not in [s.name for s in graph.api_of('pkg/core.py')]
    assert G.MODULE_SCOPE not in [s.name for s in graph.find('module')]
    assert graph.repo_map()['symbols'] == len(graph.named())
