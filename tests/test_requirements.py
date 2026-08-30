"""
Asking about an idea before trying to build it.

Friday can read instructions and choose capabilities for them. An idea is not
an instruction:

    "I want to make a game where you play a lighthouse keeper."

There is no plan in that sentence and there should not be. What is missing is
not capabilities, it is decisions, and they only exist in the boss's head.

Three things can go wrong with asking, and there is a gate for each:

    asking about everything   eleven thorough questions do not get answered.
                              Small reversible choices are decided and
                              recorded, not put to the boss.
    asking twice              a question already settled in some closed
                              conversation must not come back. That is the
                              entire point of the durable store.
    agreeing                  "React Native will definitely be best" is a
                              claim. Agreeing because the boss said it is the
                              failure this assistant exists to avoid.
"""
from __future__ import annotations
import pytest
from friday import requirements as R
from friday.store import Store


@pytest.fixture
def store(tmp_path) -> Store:
    return Store(tmp_path / "requirements.sqlite3")
IDEAS = ['I want to make a game where you play a lighthouse keeper during a storm', 'I have an idea for an app that tracks what I read and why I stopped', "I'm thinking about building a small tool to watch my build pipeline", 'help me design a system for keeping notes across three machines']
INSTRUCTIONS = ['open Paint', 'check my computer and open Paint', 'I want to open Paint', 'find one current technology story', 'what time is it']


@pytest.mark.parametrize("text", IDEAS)
def test_a_described_idea_is_recognised(text):
    assert R.is_an_idea(text), text


@pytest.mark.parametrize("text", INSTRUCTIONS)
def test_an_instruction_is_not_an_idea(text):
    """
    Narrow on purpose. Treating "open Paint" as an idea would replace a
    working assistant with a questionnaire.
    """
    assert not R.is_an_idea(text), text


def test_an_instruction_wearing_an_ideas_opening_words():
    """"I want to open Paint" is a request, not a design conversation."""
    assert not R.is_an_idea("I want to open Paint")


def test_a_small_reversible_choice_is_decided_not_asked():
    """
    §16. The boss should be able to overrule it without having been
    interrogated about it, which means it is recorded rather than skipped.
    """
    found = R._as_understanding({
        "subject": "a game",
        "questions": [
            {"question": "Which engine?", "why": "changes everything",
             "materiality": "MATERIAL"},
            {"question": "Should the audio be layered?",
             "why": "nobody will remember this decision",
             "materiality": "SMALL", "proposed": "layered ambient"},
        ],
    })

    assert [q.question for q in found.questions] == ["Which engine?"]
    assert [a.proposed for a in found.assumptions] == ["layered ambient"]


def test_a_small_choice_with_no_proposal_is_still_asked():
    """
    Marking something small and then not deciding it is the worst of both:
    the boss is not asked and nothing is chosen.
    """
    found = R._as_understanding({
        "subject": "a game",
        "questions": [{"question": "Which font?", "why": "minor",
                       "materiality": "SMALL", "proposed": ""}],
    })
    assert len(found.questions) == 1


def test_a_question_that_cannot_say_why_is_dropped():
    """
    A question with no `why` is one nobody can judge the importance of -
    including Friday, which is how a list of eleven gets built.
    """
    found = R._as_understanding({
        "subject": "a game",
        "questions": [
            {"question": "What colour?", "why": "", "materiality": "MATERIAL"},
            {"question": "Which platform?", "why": "changes the build",
             "materiality": "MATERIAL"},
        ],
    })
    assert [q.question for q in found.questions] == ["Which platform?"]


def test_questions_are_bundled_rather_than_asked_one_at_a_time():
    found = R.Understanding(questions=[
        R.Question(f"Question {index}?", why="because") for index in range(6)
    ])
    spoken = found.spoken(limit=3)

    assert spoken.count("?") == 3, spoken
    assert "3 more" in spoken, "the boss was not told there were more"


def test_nothing_to_ask_reads_as_ready():
    assert R.Understanding().ready
    assert not R.Understanding(
        questions=[R.Question("Which engine?", why="matters")]).ready


def test_a_question_already_decided_is_not_asked_again(store):
    """
    The boss said it once, in a conversation that has since been closed.
    Friday is not allowed to have forgotten - that is the whole reason any of
    this is durable.
    """
    store.record_decision(project="halo",
                          decision="Which engine should we use? - Godot",
                          rationale="better 2D tooling", source="the boss")

    decided = R.recorded("halo", db=store)
    assert decided, "the decision was not recorded"

    asking = R.Question("Which engine should we use?", why="changes the build")
    assert R.already_answered(asking, decided)


def test_the_same_question_in_different_words_is_still_answered(store):
    """
    "which engine are you using" and "what engine should we use" are the same
    question, and asking the second after answering the first is how an
    assistant stops feeling like it was listening.
    """
    store.record_decision(project="halo",
                          decision="engine: Godot rather than Unity",
                          rationale="2D tooling", source="the boss")

    decided = R.recorded("halo", db=store)
    assert R.already_answered(
        R.Question("Which engine are we using?", why="matters"), decided)


def test_a_genuinely_new_question_is_not_suppressed(store):
    store.record_decision(project="halo", decision="engine: Godot",
                          rationale="2D tooling", source="the boss")

    decided = R.recorded("halo", db=store)
    assert not R.already_answered(
        R.Question("Who is the audience for this?", why="changes the scope"),
        decided)


def test_an_answer_becomes_a_decision_that_survives(store):
    asking = R.Question("Which engine should we use?",
                        why="changes everything downstream")
    R.remember_answer("halo", asking, "Godot", db=store)

    decided = R.recorded("halo", db=store)
    assert any("Godot" in row["decision"] for row in decided)
    assert R.already_answered(asking, decided), \
        "the answer did not stop the question being asked again"


def test_a_superseded_decision_stops_counting(store):
    store.record_decision(project="halo", decision="engine: Unity",
                          rationale="familiarity", source="the boss")
    rows = store.decisions("halo")
    with store._tx() as conn:
        conn.execute("UPDATE project_decisions SET superseded = 1 WHERE id = ?",
                     (rows[0]["id"],))

    assert R.recorded("halo", db=store) == [], \
        "a decision the boss took back is still being treated as settled"


def test_a_weak_claim_is_challenged_not_echoed():
    found = R._as_understanding({
        "subject": "a game",
        "questions": [],
        "concerns": [{
            "claim": "React Native will definitely be best for this",
            "concern": "It is built for app interfaces and struggles with the "
                       "rendering a storm would need.",
        }],
    })

    assert len(found.concerns) == 1
    assert "struggles" in found.concerns[0].concern


def test_a_concern_with_no_reason_is_dropped():
    """Doubt without a reason is not useful, it is just discouraging."""
    found = R._as_understanding({
        "subject": "a game", "questions": [],
        "concerns": [{"claim": "React Native is best", "concern": ""}],
    })
    assert found.concerns == []


def test_no_provider_gives_an_empty_understanding_not_a_crash(monkeypatch):
    """
    A turn is worth more than this. Friday should say it did not follow rather
    than fall over, and it must not invent questions without a model.
    """
    monkeypatch.setattr(R, "_model", lambda: None)

    found = R.understand("I want to build something ambitious and unclear")
    assert found.questions == []
    assert found.ready


def _agent():
    import agent_friday
    agent = object.__new__(agent_friday.FridayAgent)
    agent._intent = agent._objective_detail = agent._admitted_run_id = ''
    agent._turn_owned_by = ''
    agent._router = type('R', (), {'active_tools': lambda self: []})()
    agent._toolset = type('T', (), {'_tools': []})()

    class Learner:
        def observe(self, *args, **kwargs):
            pass
    agent._learner = Learner()
    return agent


def _context():
    from livekit.agents import llm as lkllm
    return lkllm.ChatContext.empty()


def test_an_idea_puts_its_questions_in_front_of_the_model(monkeypatch, store):
    import agent_friday
    from friday.toolsets import memory as M
    M.reset_store(store)
    monkeypatch.setattr(M, 'reset_store', lambda *a, **k: None)
    monkeypatch.setattr(R, 'understand', lambda text, project='', db=None: R.Understanding(subject='a lighthouse game', questions=[R.Question('Which engine?', why='decides the build')], concerns=[R.Concern('React Native is best', 'it is built for app UI')]))
    turn_ctx = _context()
    agent_friday.ask_about_the_idea(turn_ctx, 'I want to make a game where you play a lighthouse keeper')
    said = ' '.join((str(getattr(item, 'text_content', '') or '') for item in turn_ctx.items))
    assert 'Which engine?' in said
    assert 'decides the build' in said, 'the model was not told what turns on it'
    assert 'built for app UI' in said, 'the concern was not raised'
    assert 'Do not start building' in said


def test_an_instruction_is_left_alone(monkeypatch):
    """
    The question loop must not fire on "open Paint". Turning a working
    assistant into a questionnaire is the way this feature fails.
    """
    import agent_friday
    monkeypatch.setattr(R, 'understand', lambda *a, **k: (_ for _ in ()).throw(AssertionError('the idea reader was called for an instruction')))
    turn_ctx = _context()
    agent_friday.ask_about_the_idea(turn_ctx, 'check my computer and open Paint')
    assert not turn_ctx.items


def test_an_idea_with_nothing_to_ask_says_nothing(monkeypatch):
    import agent_friday
    monkeypatch.setattr(R, 'understand', lambda *a, **k: R.Understanding(subject='clear enough'))
    turn_ctx = _context()
    agent_friday.ask_about_the_idea(turn_ctx, 'I want to build a thing that is already fully specified')
    assert not turn_ctx.items


def test_a_failure_to_read_the_idea_does_not_cost_the_turn(monkeypatch):
    """A turn is worth more than this. Friday answers plainly instead."""
    import agent_friday
    monkeypatch.setattr(R, 'understand', lambda *a, **k: (_ for _ in ()).throw(RuntimeError('provider is down')))
    turn_ctx = _context()
    agent_friday.ask_about_the_idea(turn_ctx, 'I want to make a game about a lighthouse keeper')
    assert not turn_ctx.items


def test_an_admitted_objective_is_not_also_asked_about(monkeypatch):
    """
    A compound instruction is work, not an idea. Being asked design questions
    about a request that is already running would be worse than either.
    """
    import agent_friday
    monkeypatch.setattr(R, 'understand', lambda *a, **k: (_ for _ in ()).throw(AssertionError('an admitted objective was treated as an idea')))
    monkeypatch.setattr(agent_friday, 'route_input', lambda text: ('NEW_OBJECTIVE', 'RUN-x'))
    monkeypatch.setattr(agent_friday, 'say_the_objective_owns_this', lambda ctx, run_id: None)
    agent = _agent()
    agent.prepare_turn(_context(), 'check my computer, open Paint, find a story')


class _Model:
    """A model that answers with whatever it was handed."""

    def __init__(self, answers):
        self.answers = answers
        self.prompts = []

    def __call__(self):
        import json as _json
        outer = self

        class Models:
            def generate_content(self, *, model, contents, config):
                outer.prompts.append(contents)
                return type('A', (), {'text': _json.dumps({'answers': outer.answers})})()
        return (type('C', (), {'models': Models()})(), 'test-model')


def test_a_reply_closes_the_questions_it_answers(store, monkeypatch):
    asked = R.ask('halo', [R.Question('Which engine?', why='decides the build'), R.Question('Who is it for?', why='decides the scope')], db=store)
    monkeypatch.setattr(R, '_model', _Model([{'question_id': asked[0], 'answer': 'Godot', 'confident': True}]))
    captured = R.capture_answers('Godot, I think.', 'halo', db=store)
    assert captured == [(asked[0], 'Godot', True)]
    still_open = [row['question'] for row in store.open_questions('halo')]
    assert still_open == ['Who is it for?'], still_open


def test_an_unanswered_question_stays_open(store, monkeypatch):
    """
    "I have not decided yet" is not an answer. Having one invented is worse
    than being asked again, because nobody finds out.
    """
    asked = R.ask('halo', [R.Question('Which engine?', why='decides the build')], db=store)
    monkeypatch.setattr(R, '_model', _Model([]))
    assert R.capture_answers('I have not decided that yet.', 'halo', db=store) == []
    assert len(store.open_questions('halo')) == 1


def test_an_answer_becomes_a_durable_decision(store, monkeypatch):
    asked = R.ask('halo', [R.Question('Which engine?', why='decides the build')], db=store)
    monkeypatch.setattr(R, '_model', _Model([{'question_id': asked[0], 'answer': 'Godot', 'confident': True}]))
    R.capture_answers('Godot.', 'halo', db=store)
    decided = R.recorded('halo', db=store)
    assert any(('Godot' in row['decision'] for row in decided))
    assert R.already_answered(R.Question('Which engine should we use?', why='matters'), decided), 'the captured answer does not stop the question coming back'


def test_an_inferred_answer_is_recorded_as_inferred(store, monkeypatch):
    """
    Something the boss implied is not something the boss said. Both are kept;
    only one of them is quotable back at them.
    """
    asked = R.ask('halo', [R.Question('Which engine?', why='decides the build')], db=store)
    monkeypatch.setattr(R, '_model', _Model([{'question_id': asked[0], 'answer': 'Godot', 'confident': False}]))
    R.capture_answers('I have been playing with Godot lately.', 'halo', db=store)
    row = R.recorded('halo', db=store)[0]
    assert 'inferred' in row['source'], row['source']


def test_an_id_for_a_question_nobody_asked_is_ignored(store, monkeypatch):
    asked = R.ask('halo', [R.Question('Which engine?', why='decides')], db=store)
    monkeypatch.setattr(R, '_model', _Model([{'question_id': 9999, 'answer': 'invented', 'confident': True}]))
    assert R.capture_answers('something', 'halo', db=store) == []
    assert len(store.open_questions('halo')) == 1
    assert R.recorded('halo', db=store) == []


def test_an_empty_answer_settles_nothing(store, monkeypatch):
    asked = R.ask('halo', [R.Question('Which engine?', why='decides')], db=store)
    monkeypatch.setattr(R, '_model', _Model([{'question_id': asked[0], 'answer': '   ', 'confident': True}]))
    assert R.capture_answers('mmm', 'halo', db=store) == []
    assert len(store.open_questions('halo')) == 1


def test_the_same_question_is_not_answered_twice(store, monkeypatch):
    """
    Two decisions about one question and no way to tell which the boss meant
    is worse than one.
    """
    asked = R.ask('halo', [R.Question('Which engine?', why='decides')], db=store)
    monkeypatch.setattr(R, '_model', _Model([{'question_id': asked[0], 'answer': 'Godot', 'confident': True}]))
    assert R.capture_answers('Godot', 'halo', db=store)
    assert R.capture_answers('Godot', 'halo', db=store) == []
    assert len(R.recorded('halo', db=store)) == 1


def test_nothing_asked_means_nothing_to_capture(store, monkeypatch):
    monkeypatch.setattr(R, '_model', _Model([{'question_id': 1, 'answer': 'x', 'confident': True}]))
    assert R.capture_answers('anything at all', 'halo', db=store) == []


def test_two_ideas_in_flight_do_not_get_each_others_answers(store):
    """
    A decision filed under the wrong project is worse than one not filed at
    all - it will be found later and believed.
    """
    R.ask('halo', [R.Question('Which engine?', why='decides')], db=store)
    R.ask('pipeline', [R.Question('Which CI?', why='decides')], db=store)
    assert R.current_project(db=store) == '', 'an answer would have been filed against a guess'


def test_one_idea_in_flight_is_unambiguous(store):
    R.ask('halo', [R.Question('Which engine?', why='decides')], db=store)
    assert R.current_project(db=store) == 'halo'


def test_a_project_name_is_stable_and_readable():
    assert R.project_name('Lighthouse Keeper Game') == 'lighthouse-keeper'
    assert R.project_name('a new app') == 'unnamed'
    assert R.project_name('Lighthouse Keeper Game') == R.project_name('The Lighthouse Keeper game')


def test_the_questions_are_recorded_when_they_are_asked(monkeypatch, store):
    """
    Written down before they are spoken. An answer can arrive in any later
    turn and has to be able to find the question it belongs to.
    """
    import agent_friday
    from friday.toolsets import memory as M
    M.reset_store(store)
    try:
        monkeypatch.setattr(R, 'understand', lambda text, project='', db=None: R.Understanding(subject='Lighthouse Keeper Game', questions=[R.Question('Which engine?', why='decides the build')]))
        agent_friday.ask_about_the_idea(_context(), 'I want to make a game about a lighthouse keeper')
        assert [row['question'] for row in store.open_questions()] == ['Which engine?']
    finally:
        M.reset_store(None)


def test_an_unanswered_question_comes_back_when_the_build_starts(store):
    R.ask('lighthouse-keeper-storm', [R.Question('Which engine?', why='decides the build')], db=store)
    project, blocking = R.still_blocking('right, build the lighthouse keeper game', db=store)
    assert project == 'lighthouse-keeper-storm'
    assert [row['question'] for row in blocking] == ['Which engine?']


def test_it_does_not_come_back_on_an_unrelated_request(store):
    """
    Raised when the answer is needed, not on a timer. A question brought back
    on any other schedule is nagging, and nobody answers nagging.
    """
    R.ask('lighthouse-keeper-storm', [R.Question('Which engine?', why='decides the build')], db=store)
    assert R.still_blocking('what is the weather in London', db=store) == ('', [])
    assert R.still_blocking('open Paint', db=store) == ('', [])


def test_an_answered_question_does_not_come_back(store):
    asked = R.ask('lighthouse-keeper-storm', [R.Question('Which engine?', why='decides the build')], db=store)
    store.answer_question(asked[0], 'Godot')
    assert R.still_blocking('build the lighthouse keeper game', db=store) == ('', [])


def test_the_head_word_is_what_the_project_is_called(store):
    """
    "the lighthouse game" is the same project as "lighthouse keeper storm
    game" - requiring the full name means it is only recognised when named in
    full, which is not how anybody talks.

    And any-word-matches is the other error: "will there be a storm tomorrow"
    would reopen the game, and answering a weather question with an assumption
    about a game engine is worse than staying quiet.
    """
    R.ask('lighthouse-keeper-storm', [R.Question('Which engine?', why='')], db=store)
    assert R.about_this_project('start on the lighthouse game', 'lighthouse-keeper-storm')
    assert not R.about_this_project('will there be a storm tomorrow', 'lighthouse-keeper-storm')
    assert not R.about_this_project('start on the keeper survey', 'lighthouse-keeper-storm')


def test_two_projects_in_flight_raise_nothing(store):
    """Same rule as `current_project`: a wrong project is worse than none."""
    R.ask('lighthouse-keeper-storm', [R.Question('Which engine?', why='')], db=store)
    R.ask('pipeline-rewrite', [R.Question('Which CI?', why='')], db=store)
    assert R.still_blocking('build the lighthouse keeper game', db=store) == ('', [])


def test_the_work_is_not_held_up_by_it(monkeypatch, store):
    """
    The boss said not to stop between tasks. An assumption said out loud is
    not an invented answer - it is a declared default they can overrule, and
    refusing to start would be the interrogation this loop is written against.
    """
    import agent_friday
    from friday.toolsets import memory as M
    M.reset_store(store)
    try:
        R.ask('lighthouse-keeper-storm', [R.Question('Which engine?', why='decides the build')], db=store)
        context = _context()
        agent_friday.raise_what_is_still_open(context, 'build the lighthouse keeper game')
        said = '\n'.join((str(getattr(item, 'content', '')) for item in context.items))
        assert 'Which engine?' in said
        assert 'do not hold it up' in said.lower()
    finally:
        M.reset_store(None)


def test_asking_about_a_project_says_what_is_still_open(store):
    """
    Friday could say what had been settled about the lighthouse game and not
    what it was still waiting on - and the open questions are the more useful
    half, because they are what is blocking the work rather than what is
    already behind it.
    """
    from friday import contracts as c
    from friday.toolsets import memory as M
    M.reset_store(store)
    try:
        store.record_decision(project='lighthouse-keeper', decision='engine - Godot', rationale='', source='the boss answered')
        R.ask('lighthouse-keeper', [R.Question('How does it end?', why='decides the scope')], db=store)
        run = c.Run.create('probe', capability='memory_project_context')
        result = M.project_context(run, 'lighthouse-keeper')
        assert result.status == c.SUCCEEDED, result.error
        assert result.output['counts']['open_questions'] == 1
        assert result.output['open_questions'][0]['question'] == 'How does it end?'
        assert 'still open' in result.verification.evidence
    finally:
        M.reset_store(None)


def test_a_project_that_is_only_questions_is_not_nothing(store):
    """
    "nothing recorded about it" was returned whenever there were no memories
    and no decisions - which is exactly the state a project is in right after
    Friday finishes asking about it.
    """
    from friday import contracts as c
    from friday.toolsets import memory as M
    M.reset_store(store)
    try:
        R.ask('lighthouse-keeper', [R.Question('Which engine?', why='')], db=store)
        run = c.Run.create('probe', capability='memory_project_context')
        assert M.project_context(run, 'lighthouse-keeper').status == c.SUCCEEDED
    finally:
        M.reset_store(None)
