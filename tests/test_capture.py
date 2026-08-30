"""
Writing down what the boss said he is building.

Measured tonight: he described a combat drone game, Friday researched the
engine, delivered a verdict, and stored none of it. Asked "what am I working
on?" twenty minutes later it answered correctly - from the conversation
window. A restart would have lost it and the answer would have been "halo
and lighthouse", from two days ago.

`projects_list` fixed the read side and says so in its own docstring. The
write side was never wired: `record_decision` and `add_requirement` were
called from tests and one golden script and from nothing a conversation
touches.
"""
from pathlib import Path
import pytest
from friday import capture as C
from friday import product as P


@pytest.fixture
def store(tmp_path):
    from friday.store import Store

    return Store(str(tmp_path / "friday.db"))


@pytest.mark.parametrize("said,expected", [
    ("Friday, I want to build a small desktop game where I control a "
     "combat drone.", "combat drone game"),
    ("I want to build a todo app", "todo app"),
    ("let's build a website for my bakery", "bakery website"),
    ("I am building a CLI tool for logs", "logs cli"),
    ("I need to create a dashboard for sales", "sales dashboard"),
])
def test_it_names_the_thing_being_built(said, expected):
    assert C.project_named_in(said) == expected


@pytest.mark.parametrize("said", [
    "What is the weather today?",
    "open notepad",
    "I think Godot is the best choice",
    "I want to go to bed",
    "play some music",
    "",
])
def test_it_does_not_invent_a_project(said):
    """
    A false positive here writes a junk project the boss has to clean up,
    which is why this is narrower than the search-enrichment version in
    product.py.
    """
    assert C.project_named_in(said) == ""


def test_size_adjectives_are_not_part_of_the_name():
    """"small" and "simple" say nothing about which project this is."""
    name = C.project_named_in("I want to build a simple small basic todo app")
    assert "small" not in name
    assert "simple" not in name
    assert "todo" in name


def test_a_name_stays_short_enough_to_read_back():
    name = C.project_named_in(
        "I want to build " + " ".join(f"word{i}" for i in range(40)))
    assert len(name) <= 60


def test_it_records_the_project(store):
    name = C.remember_the_project('I want to build a small desktop game where I control a combat drone', db=store)
    assert name == 'combat drone game'
    assert any((p['name'] == name for p in store.projects()))


def test_saying_it_twice_does_not_make_two_projects(store):
    """The boss repeats himself. `ensure_project` is an upsert."""
    for _ in range(3):
        C.remember_the_project('I want to build a todo app', db=store)
    assert len([p for p in store.projects() if p['name'] == 'todo app']) == 1


def test_nothing_is_recorded_for_an_ordinary_sentence(store):
    assert C.remember_the_project("what is the weather", db=store) == ""
    assert store.projects() == []


def test_a_store_that_fails_does_not_cost_the_turn(monkeypatch):
    class _Broken:
        def ensure_project(self, name):
            raise RuntimeError("disk on fire")

    assert C.remember_the_project("I want to build a todo app",
                                  db=_Broken()) == ""


def _checked(claim="Godot is the best choice", sources=("https://a.test",)):
    return P.Claim(claim=claim, sources=sources, evidence="2 sources read")


def test_a_checked_claim_becomes_a_decision(store):
    store.ensure_project("combat drone game")
    assert C.remember_the_decision("combat drone game", _checked(), db=store)

    decisions = store.decisions("combat drone game")
    assert len(decisions) == 1
    assert "Godot" in decisions[0]["decision"]


def test_the_decision_carries_what_produced_it(store):
    """
    A decision with no record of what produced it is an opinion - the same
    rule `product.verify` follows.
    """
    store.ensure_project("p")
    C.remember_the_decision("p", _checked(sources=("https://a.test",
                                                   "https://b.test")), db=store)
    rationale = store.decisions("p")[0]["rationale"]
    assert "a.test" in rationale and "b.test" in rationale


def test_an_unchecked_claim_is_not_a_decision(store):
    """
    The model's on-the-spot opinion in durable memory would mean every future
    planning call reading invented history as fact.
    """
    store.ensure_project("p")
    assert not C.remember_the_decision("p", _checked(sources=()), db=store)
    assert store.decisions("p") == []


def test_no_project_means_no_decision(store):
    assert not C.remember_the_decision("", _checked(), db=store)


def test_nothing_at_all_is_handled(store):
    assert not C.remember_the_decision("p", None, db=store)


def test_the_work_survives_being_asked_about_later(store):
    """
    The journey, end to end and without a conversation window: he says what
    he is building, a claim gets checked, and both are still there when
    something else asks.
    """
    name = C.remember_the_project('I want to build a small desktop game where I control a combat drone', db=store)
    C.remember_the_decision(name, _checked(), db=store)
    projects = {p['name'] for p in store.projects()}
    assert name in projects
    assert store.decisions(name), 'the decision did not survive'


def test_the_turn_hook_reaches_it():
    """
    The wiring. `record_decision` was already complete, already tested, and
    called by nothing a conversation touches - which is exactly why this
    assertion exists rather than trusting the module to be used.
    """
    import inspect

    import agent_friday as A

    assert "remember_the_project" in inspect.getsource(
        A.FridayAgent.read_before_answering)
    assert "remember_the_decision" in inspect.getsource(
        A.check_what_he_asserted)


def test_it_writes_to_the_database_everything_else_reads():
    """
    The bug that made the whole feature invisible.

    A first version opened `Store(DATA_DIR / "friday.db")` - a reasonable
    guess and the wrong file. The real database is `DEFAULT_DB`,
    `data/ada.sqlite3`, and `ADA_DB` may move it. So the writes landed, the
    log said `capture.project name='combat drone game'`, and `projects_list`
    went on reporting two-day-old projects. Two databases, one invented, and
    nothing failing loudly enough to notice.
    """
    from friday.toolsets.memory import store
    assert C._store().path == store().path


def test_the_store_honours_ADA_DB(monkeypatch, tmp_path):
    """
    Deferring to the canonical accessor is what makes this true. A path built
    here would ignore the override and write somewhere nobody reads.
    """
    import friday.toolsets.memory as M
    moved = tmp_path / 'elsewhere.sqlite3'
    monkeypatch.setenv('ADA_DB', str(moved))
    monkeypatch.setattr(M, '_store', None, raising=False)
    assert Path(C._store().path) == moved


@pytest.mark.parametrize('said,expected', [('It should run at 60fps on my laptop.', True), ('I want it to work offline.', True), ('The first version should support multiplayer.', True), ('It must handle a lost connection.', True), ('What is the weather?', False), ('I think Godot is best.', False), ('open notepad', False)])
def test_it_notices_a_requirement(said, expected):
    """
    Conservative on purpose. A requirement shapes what gets built and is
    checked at the end, so a false positive is a spec entry nobody agreed to.
    """
    assert bool(C.requirements_in(said)) is expected


def test_requirements_are_recorded_with_acceptance(store):
    store.ensure_project('game')
    ids = C.remember_the_requirements('game', 'It should run at 60fps on my laptop.', db=store)
    assert ids
    rows = store.requirements('game')
    assert rows
    assert rows[0]['acceptance'], 'a requirement without acceptance criteria'


def test_saying_the_same_requirement_twice_records_it_once(store):
    store.ensure_project('game')
    said = 'It should work offline.'
    C.remember_the_requirements('game', said, db=store)
    C.remember_the_requirements('game', said, db=store)
    assert len(store.requirements('game')) == 1


def test_no_project_means_no_requirement(store):
    """Attaching a spec to a guess puts another project's spec in the way."""
    assert C.remember_the_requirements('', 'It should work offline.', db=store) == []


@pytest.mark.parametrize('said,kind,subject', [('Actually, remove multiplayer from the first version.', C.REMOVE, 'multiplayer'), ('drop the leaderboard', C.REMOVE, 'leaderboard'), ('change the target from 60fps to 30fps', C.REPLACE, 'target'), ('make it 30fps instead of 60fps', C.REPLACE, '60fps'), ('change the engine to Godot', C.REPLACE, 'engine')])
def test_it_reads_a_change(said, kind, subject):
    change = C.change_in(said)
    assert change is not None, said
    assert change.kind == kind
    assert change.subject == subject


def test_from_to_keeps_the_new_value_not_both(said='change the target from 60fps to 30fps'):
    """
    A single pattern accepting either preposition matched at `from` and
    captured "60fps to 30fps" as the replacement.
    """
    assert C.change_in(said).replacement == '30fps'


@pytest.mark.parametrize('said', ['no worries', 'no idea', 'It should support multiplayer.', 'what is the weather', 'I want to build a todo app'])
def test_it_does_not_invent_a_change(said):
    assert C.change_in(said) is None


def test_a_new_requirement_is_not_read_as_a_change():
    """
    Conflating them is how a spec grows a requirement that says to delete
    something.
    """
    said = 'It should support multiplayer.'
    assert C.change_in(said) is None
    assert C.requirements_in(said)


@pytest.fixture
def spec(store):
    store.ensure_project('game')
    store.add_requirement('game', 'The game should support multiplayer', source='t')
    store.add_requirement('game', 'It should work offline', source='t')
    store.record_decision('game', 'engine - Godot', source='t')
    store.record_decision('game', 'multiplayer - peer to peer', source='t')
    return store


def test_a_change_supersedes_the_matching_requirement(spec):
    report = C.apply_change('game', C.change_in('remove multiplayer'), db=spec)
    assert any(('multiplayer' in s.lower() for s in report['superseded']))


def test_a_change_leaves_the_other_requirements_alone(spec):
    C.apply_change('game', C.change_in('remove multiplayer'), db=spec)
    active = [r['statement'] for r in spec.requirements('game')]
    assert any(('offline' in s.lower() for s in active)), 'an unrelated requirement was retired too'


def test_the_old_requirement_is_kept_with_the_reason(spec):
    """
    "Why did we remove multiplayer?" needs the old row and the reason, and a
    DELETE answers it with silence.
    """
    C.apply_change('game', C.change_in('remove multiplayer from v1'), db=spec)
    retired = [r for r in spec.requirements('game', include_superseded=True) if r['status'] == 'SUPERSEDED']
    assert retired
    assert retired[0]['why_changed'], 'retired with no reason recorded'


def test_it_names_the_decisions_that_rested_on_the_change(spec):
    report = C.apply_change('game', C.change_in('remove multiplayer'), db=spec)
    assert any(('multiplayer' in d.lower() for d in report['dependent_decisions']))


def test_it_names_the_decisions_that_still_stand(spec):
    """
    The difference between a change and a reset. He removed one thing; the
    engine choice still stands, and saying so stops him wondering.
    """
    report = C.apply_change('game', C.change_in('remove multiplayer'), db=spec)
    assert any(('godot' in d.lower() for d in report['untouched_decisions']))


def test_a_dependent_decision_is_named_not_silently_undone(spec):
    """
    Invalidating a decision without being asked is how Friday would quietly
    undo something he settled on purpose.
    """
    C.apply_change('game', C.change_in('remove multiplayer'), db=spec)
    assert len(spec.decisions('game')) == 2, 'a decision was removed'


def test_a_replacement_adds_the_new_requirement(spec):
    spec.add_requirement('game', 'It should run at 60fps', source='t')
    report = C.apply_change('game', C.change_in('change the fps from 60fps to 30fps'), db=spec)
    assert report['added']
    active = ' '.join((r['statement'] for r in spec.requirements('game')))
    assert '30fps' in active


def test_a_change_matching_nothing_reports_nothing(spec):
    report = C.apply_change('game', C.change_in('remove telemetry'), db=spec)
    assert report['superseded'] == []


def test_the_turn_hook_reaches_the_requirement_path():
    """The invariant: a feature nothing calls is decoration."""
    import inspect
    import agent_friday as A
    source = inspect.getsource(A.FridayAgent.read_before_answering)
    assert 'note_the_requirements' in source
    hook = inspect.getsource(A.note_the_requirements)
    assert 'change_in' in hook
    assert 'remember_the_requirements' in hook
