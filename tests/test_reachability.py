"""
The gate that replaces "tests pass" as the completion signal.

Three times in one session a feature was implemented, tested, green, and
unreachable from the way Friday is actually used. This file is the standing
check against a fourth.

The important tests here are not the ones about the machinery. They are the
named-feature gates at the bottom: each one asserts that a specific thing a
person does reaches the code that does it. Those are the tests that would
have failed on the typed-input defect, and no unit test in the suite did.
"""
import pytest
from friday import codegraph as G
from friday import reachability as R


@pytest.fixture(scope="module")
def graph():
    """The real repository. This audit is only meaningful on real code."""
    return G.CodeGraph.build(".")


@pytest.fixture(scope="module")
def audited(graph):
    return R.audit(graph)


def test_the_depths_are_ordered():
    assert R.DEPTHS[0] == R.DEFINED
    assert R.DEPTHS[-1] == R.REAL_JOURNEY_VERIFIED
    assert R.DEPTHS.index(R.PRODUCTION_REACHABLE) > R.DEPTHS.index(R.REGISTERED)


def test_at_least_compares_depth_not_string():
    assert R.at_least(R.EXECUTED, R.PRODUCTION_REACHABLE)
    assert not R.at_least(R.REGISTERED, R.PRODUCTION_REACHABLE)
    assert R.at_least(R.DEFINED, R.DEFINED)


def test_an_unknown_depth_never_satisfies_a_gate():
    """A typo in a depth name must fail closed, not pass silently."""
    assert not R.at_least("PROBABLY_FINE", R.DEFINED)


def test_tests_cannot_prove_the_depths_that_matter():
    """
    The whole premise. A unit test can show a thing is callable; it cannot
    show a person can reach it.
    """
    assert R.DEPTHS.index(R.PROVABLE_BY_TESTS) < \
        R.DEPTHS.index(R.PRODUCTION_REACHABLE)


def test_the_minimum_for_a_user_facing_feature_is_reachability():
    assert R.MINIMUM == R.PRODUCTION_REACHABLE


def test_every_declared_entry_point_exists(audited):
    """
    A stale entry list silently shrinks what counts as reachable, which makes
    the audit quietly stop auditing.
    """
    for path, found in audited.entry_points_found.items():
        if path == R.AUTOMATION:
            continue          # not yet built; declared for when it is
        assert found, f"no entry point found for {path}"


def test_both_input_modalities_have_an_entry_point(audited):
    """
    Listed separately *because* they were not the same pipeline. One combined
    entry would have hidden exactly the defect this module exists for.
    """
    assert audited.entry_points_found[R.VOICE_USER]
    assert audited.entry_points_found[R.TEXT_USER]


def test_a_test_import_is_not_production_reachability(graph):
    """
    The entire point. Every defect this file guards had tests calling the
    code directly - if a test import counted, all three would have been
    reported reachable while the product was broken.
    """
    assert R._is_test("tests/test_thing.py")
    assert R._is_test("scripts/golden_executor.py")
    assert not R._is_test("friday/product.py")
    assert not R._is_test("agent_friday.py")


def test_module_scope_counts_as_a_root(tmp_path):
    """
    Importing a module runs its top level. A walk that only follows
    function-to-function edges called a dozen live functions dead purely
    because they were invoked at import time.
    """
    (tmp_path / "m.py").write_text(
        "def on_user_turn_completed():\n"
        "    pass\n"
        "\n"
        "def _at_import():\n"
        "    return 1\n"
        "\n"
        "VALUE = _at_import()\n",
        encoding="utf-8")
    result = R.audit(G.CodeGraph.build(tmp_path))
    assert "_at_import" in result.reachable


def test_an_approximate_reading_is_never_called_dead(tmp_path):
    """
    A regex reader produces no reference edges, so calling its symbols
    unreachable would be an artefact of how the file was parsed.
    """
    (tmp_path / "ui.ts").write_text("export function render() {}\n",
                                    encoding="utf-8")
    result = R.audit(G.CodeGraph.build(tmp_path))
    assert not any(f.name == "render" for f in result.dead)


def test_a_declared_module_is_not_reported_dead(audited):
    """
    Capabilities are reached by id through the MCP surface, never by a static
    call. Without the declaration every toolset would read as dead.
    """
    assert not any(f.path.startswith("friday/toolsets/") for f in audited.dead)


def test_every_declaration_gives_a_reason():
    """An undocumented exemption is how an audit quietly stops auditing."""
    for prefix, (verdict, why) in R.DECLARED.items():
        assert verdict in (R.PRODUCTION, R.INTENTIONALLY_INTERNAL, R.FUTURE,
                           R.TEST_ONLY), prefix
        assert why, f"{prefix} is exempted for no stated reason"


def test_the_audit_reports_who_mentions_an_unreachable_thing(audited):
    """
    So a person can judge rather than trust. UNREACHABLE means "no path was
    found", never "there is no path".
    """
    for finding in audited.dead[:5]:
        assert finding.why
        assert finding.path


def test_the_typed_path_prepares_a_turn_like_the_spoken_one(graph):
    """
    The defect, as a standing test.

    `text_input_callback` called `prepare_turn` and nothing else, so
    research, claim verification and project capture existed and were
    unreachable for the way Friday is actually used. Nothing in a 2,400-test
    suite noticed.
    """
    result = R.parity(graph)
    missing = [name for name in result["spoken_only"]
               if name in ("research_first", "check_what_he_asserted",
                           "remember_the_project", "prepare_turn",
                           "read_before_answering", "stop_re_reading")]
    assert not missing, \
        f"the typed path does not reach {missing} - the spoken path does"


def test_both_paths_share_the_preparation(graph):
    result = R.parity(graph)
    assert result["shared"] > 50, \
        "the two input paths barely overlap, which means they are not the " \
        "same pipeline"
JOURNEYS = {'research reaches the sources': ('read_before_answering', 'research_first'), 'a claim gets checked before it is agreed with': ('read_before_answering', 'check_what_he_asserted'), 'the reading tools go away once the reading is done': ('read_before_answering', 'stop_re_reading'), 'a project he describes is written down': ('read_before_answering', 'remember_the_project'), 'a checked decision is written down': ('check_what_he_asserted', 'remember_the_decision'), 'an objective can be admitted': ('prepare_turn', 'route_input'), 'a development run understands the repo': ('for_goal', 'understand'), 'a development run is staffed': ('for_goal', 'staff'), 'a development run is contained': ('verify', 'backend_named'), 'work is verified before it may land': ('gate', 'decide')}


@pytest.mark.parametrize("journey", sorted(JOURNEYS))
def test_a_real_journey_reaches_its_implementation(graph, journey):
    """
    `from` must reference `to`, directly or through the graph.

    Asserted on the reference graph rather than by importing and calling,
    because the failure being guarded is precisely that the call is missing
    from the production path while the implementation is perfectly callable.
    """
    origin, target = JOURNEYS[journey]

    by_name: dict[str, list] = {}
    for symbol in graph.symbols:
        if not R._is_test(symbol.path):
            by_name.setdefault(symbol.name, []).append(symbol)

    assert origin in by_name, f"{origin} does not exist"
    assert target in by_name, f"{target} does not exist"

    seen: set[str] = set()
    frontier = [origin]
    while frontier:
        current = frontier.pop()
        if current in seen:
            continue
        seen.add(current)
        for symbol in by_name.get(current, ()):
            for referenced in symbol.references:
                if referenced not in seen and referenced in by_name:
                    frontier.append(referenced)

    assert target in seen, \
        f"{journey!r}: nothing on the path from {origin} reaches {target}"


def test_the_number_of_unreachable_things_does_not_grow(audited):
    """
    A ratchet, not a target. The count is allowed to fall freely and this
    fails when it climbs, which is the moment somebody has added a feature
    nobody can reach.

    Raise the ceiling only with a reason, in the same commit that adds the
    thing, having decided it is genuinely internal or genuinely future.
    """
    own = [f for f in audited.dead if f.path.startswith(('friday/', 'agent_friday', 'server.py'))]
    assert not own, f"{len(own)} unreachable and unclassified symbols in Friday's own code. Either wire them to a production path, or add them to `reachability.KNOWN` with a verdict and a reason:\n" + '\n'.join((f"  {f.path}:{f.line} {f.name}" for f in own))
