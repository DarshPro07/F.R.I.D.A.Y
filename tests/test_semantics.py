"""
Routing by what a sentence asks for, not by which words it happens to share.

The bug these gates exist for: "open Paint" and "what windows are open?" share
every content word and mean opposite things, so a lexical scorer picks between
them by accident. Five capabilities had been reworded to dodge collisions like
that, and the rewording is what kept breaking - `automations_history` had gone
so far as to avoid the word "run" in its own examples, and still lost to
`product_runs` on "when did that automation run?".

Machine semantics settle it before phrasing is consulted. So these tests check
three separable things:

  the vocabulary   every capability has an operation and a target, and none
                   got there by falling through a default
  the routing      twelve adversarial cases, positive and negative
  the failure      mutating the vocabulary must turn the routing red - a
                   taxonomy that cannot break is one nothing depends on
"""
from __future__ import annotations
import ast
import asyncio
import pathlib
import pytest
from friday import capabilities as C
from friday import capability_router as R
from friday import semantics as S


class SchemaTool:
    def __init__(self, name, description="", schema=None):
        self.info = type("I", (), {
            "name": name,
            "raw_schema": {"name": name, "description": description,
                           "parameters": schema or {"properties": {}, "required": []}},
        })()


@pytest.fixture(scope="module")
def router():
    """The real registry. Building it costs a few seconds, hence module scope."""
    from mcp.server.fastmcp import FastMCP

    from friday.tools import register_all_tools

    server = FastMCP(name="semantics-test")
    register_all_tools(server)
    tools = asyncio.run(server.list_tools())
    r = R.Router()
    r.load([SchemaTool(t.name, t.description or "", t.inputSchema) for t in tools])
    # §24. A router with nothing in it answers every query with [], and an
    # assertion about an empty answer is not evidence of anything. The first
    # version of this fixture built the fakes with `.name` instead of
    # `.info.name`, loaded zero tools, and reported all twelve cases failing.
    assert len(r.all_tools) > 100, \
        f"router loaded {len(r.all_tools)} tools; these tests would measure nothing"
    return r


def top(router, query, n=3):
    return [hit["capability"] for hit in router.search(query, limit=n)]


def test_every_capability_has_an_operation_and_a_target():
    for cap in C._ALL:
        operation, target = S.for_capability(cap.id)
        assert operation in S.OPERATIONS, f"{cap.id} -> unknown operation {operation}"
        assert target in S.TARGETS, f"{cap.id} -> unknown target {target}"


def test_no_capability_gets_its_operation_by_falling_through():
    """
    Falling through is not neutral. The default is READ, READ is
    informational, and an informational capability can never satisfy an
    imperative - so a capability that lands here is unroutable by any
    instruction and nothing says so.

    `memory_forget` was exactly this: no rule matched "forget", it defaulted
    to READ, and "forget that" could not reach it from any phrasing.
    """
    defaulted = sorted(cap.id for cap in C._ALL if S.defaulted(cap.id))
    assert defaulted == [], (
        "these capabilities have no operation rule and silently became READ: "
        f"{defaulted}. Add the verb to _OPERATION_BY_SUFFIX or an entry to "
        f"_OVERRIDES - do not leave it to the default.")


def test_the_override_table_stays_small():
    """
    Overrides are corrections to the rules. A long list of them means the
    rules are wrong and are being papered over one capability at a time.
    """
    assert len(S._OVERRIDES) <= 25, (
        f"{len(S._OVERRIDES)} overrides - the derivation rules need fixing "
        f"rather than another exception")


def test_a_question_and_an_instruction_read_differently():
    """The distinction the whole thing rests on."""
    assert S.for_request("open Paint") == S.OPEN
    assert S.for_request("what windows are open?") == S.READ
    assert S.for_request("show me my files") == S.LIST


def test_target_nouns_beat_application_names():
    """
    `friday.apps.ALIASES` maps "files" to Explorer, so an app-name lookup that
    ran first would read "show me my files" as a request about an application.
    """
    assert S.target_for_request("show me my files") == "FILE"
    assert S.target_for_request("open Paint") == "APPLICATION"
ROUTES = [('open Paint', 'apps_open'), ('what windows are open?', 'windows_list'), ('move Paint to the second monitor', 'windows_arrange'), ('what is playing?', 'music_current'), ('pause it', 'music_pause'), ('stop it', 'music_stop'), ('remove the temporary file', 'files_recycle'), ('show me my files', 'files_list'), ('run that automation again', 'automations_run'), ('when did that automation run?', 'automations_history'), ('forget that', 'memory_forget'), ('delete that file', 'files_recycle')]


@pytest.mark.parametrize("query, expected", ROUTES)
def test_semantic_routing(router, query, expected):
    hits = top(router, query)
    assert hits and hits[0] == expected, f"{query!r} -> {hits}"
FORBIDDEN = [('open Paint', 'windows_list', 'an instruction to open is not a question'), ('open Paint', 'objective_start', 'opening an app is not a durable objective'), ('show me my files', 'files_recycle', 'listing is not deleting'), ('what windows are open?', 'apps_open', 'a question must not act'), ('when did that automation run?', 'automations_run', 'asking how it went must not run it again'), ('forget that', 'memory_remember', 'forget is the opposite of remember')]


@pytest.mark.parametrize("query, forbidden, why", FORBIDDEN)
def test_semantic_routing_excludes(router, query, forbidden, why):
    hits = top(router, query)
    assert forbidden not in hits, f"{query!r} -> {hits}: {why}"


def test_a_wrong_guess_narrows_but_never_empties(router):
    """
    The filter drops structurally implausible candidates only while something
    plausible survives. An unrecognised verb must not leave the model with
    nothing to call.
    """
    assert top(router, 'read a file'), 'a plain request returned nothing'
    assert S.for_request('frobnicate my files') is None
    assert top(router, 'frobnicate my files'), 'an unknown verb emptied the results'


def test_corrupting_open_breaks_routing(router, monkeypatch):
    """If OPEN meant LIST, "open Paint" would be a question again."""
    monkeypatch.setitem(S._OPERATION_BY_SUFFIX, "open", S.LIST)
    monkeypatch.setitem(S._OVERRIDES, "apps_open", (S.LIST, "APPLICATION"))
    assert top(router, "open Paint")[:1] != ["apps_open"], \
        "the routing gate survived OPEN being redefined as LIST"


def test_collapsing_delete_into_mutate_breaks_routing(router, monkeypatch):
    """
    The specific regression the taxonomy replaced: recycling a file and
    pausing music were both MUTATE, and MUTATE cannot separate them.
    """
    monkeypatch.setitem(S._OVERRIDES, "files_recycle", (S.MUTATE, "FILE"))
    assert top(router, "remove the temporary file")[:1] != ["files_recycle"], \
        "the routing gate survived DELETE being collapsed into MUTATE"


def test_mislabelling_the_target_breaks_routing(router, monkeypatch):
    """APPLICATION and WINDOW are different things to open."""
    monkeypatch.setitem(S._OVERRIDES, "apps_open", (S.OPEN, "WINDOW"))
    assert top(router, "open Paint")[:1] != ["apps_open"], \
        "the routing gate survived apps_open being retargeted to WINDOW"


def test_removing_the_semantic_signal_breaks_routing(router, monkeypatch):
    """Without the question/instruction split there is no signal at all."""
    monkeypatch.setattr(S, "for_request", lambda text: None)
    monkeypatch.setattr(S, "target_for_request", lambda text: None)
    broken = [q for q, expected in ROUTES if top(router, q)[:1] != [expected]]
    assert broken, "routing was unchanged with the semantic signal removed"
PURE = {'format_json', 'word_count'}
OBSERVES = {'shutil', 'platform', 'ctypes', 'os', 'psutil', 'socket', 'time', 'subprocess', 'datetime', 'winreg', 'pathlib'}


def _adapter_source(capability_id: str) -> str:
    for path in pathlib.Path("friday/tools").glob("*.py"):
        text = path.read_text(encoding="utf-8")
        if f"def {capability_id}(" in text:
            return text
    raise AssertionError(f"no adapter found for {capability_id}")


def test_a_pure_capability_observes_nothing():
    """
    The test that would have caught the misclassification. `get_current_time`
    and `get_system_info` were filed as pure because they are read-only, but
    they read the clock and the platform - that is the world, and an
    observation of the world has evidence to carry.
    """
    for capability_id in sorted(PURE):
        tree = ast.parse(_adapter_source(capability_id))
        imported = {
            node.names[0].name.split(".")[0]
            for node in ast.walk(tree)
            if isinstance(node, (ast.Import, ast.ImportFrom)) and node.names
        }
        leaked = imported & OBSERVES
        assert not leaked, (
            f"{capability_id} is declared pure but its module imports "
            f"{sorted(leaked)} - if it observes the machine it is not a "
            f"transform and belongs in the evidence model")


def test_reading_the_world_is_not_purity():
    """The converse, stated so the category cannot quietly widen."""
    for capability_id in ("get_current_time", "get_system_info"):
        assert capability_id not in PURE, (
            f"{capability_id} reads the machine it runs on. Read-only is not "
            f"the same as pure: it has an observation to report and a scope "
            f"to report it under.")
OBJECT_DEPENDENT = ['restart the song', 'put the computer to sleep', 'turn off the pc', 'start the music again', 'make that window full screen', 'bring that window to the front']


@pytest.mark.parametrize('phrase', OBJECT_DEPENDENT)
def test_an_object_dependent_verb_infers_no_operation(phrase):
    assert S.for_request(phrase) is None, f"{phrase!r} inferred an operation from a verb that means different things to different objects; that reading filtered out the right capability every time it was tried"


def test_restarting_a_song_never_reaches_the_power_capabilities(router):
    """The safety case, kept separate because it is the one that matters."""
    hits = top(router, 'restart the song', n=6)
    assert not {'power_restart', 'power_shutdown'} & set(hits[:1]), f"restart the song -> {hits}"


def test_a_question_gets_no_exact_operation_bonus(router):
    """
    Every question infers READ, so READ carries no information beyond "not an
    instruction". Rewarding an exact match on it handed three objective_*
    phrasings to `ada_ask`, which is a READ and answers none of them.
    """
    for phrase, wanted in (('what jobs have you done', 'objective_history'), ('what are you working on', 'objective_list'), ('are we done yet', 'objective_status')):
        assert wanted in top(router, phrase, n=3), f"{phrase!r} -> {top(router, phrase, n=3)}"
