"""
Turning a spoken idea into something that can actually be built.

Journey D. The requirements loop asked good questions and recorded the
answers, and stopped there - one step short of useful, because the arc ended
exactly where the build should begin. The boss still had to write the
requirements, the acceptance criteria and the Claude prompts himself.

The sentence this is measured against:

    "I want to build a small desktop game where I control a combat drone. I
     think Godot is probably the best choice, but verify that instead of just
     agreeing with me."

Agreeing is the failure mode. So is inventing a performance target nobody
gave, and so is freezing the whole project over a keybinding.
"""
from __future__ import annotations
import pytest
from friday import product as P
from friday import requirements as REQ
from friday.store import Store
from friday.toolsets import memory as M
IDEA = 'Friday, I want to build a small desktop game where I control a combat drone. I think Godot is probably the best choice, but verify that instead of just agreeing with me. I want the first version to be simple enough to build quickly.'


@pytest.fixture
def store(tmp_path) -> Store:
    db = Store(tmp_path / "product.sqlite3")
    M.reset_store(db)
    yield db
    M.reset_store(None)


def test_being_asked_to_check_is_recognised():
    assert P.wants_to_be_challenged(IDEA)
    assert not P.wants_to_be_challenged("open Paint")


def test_the_technology_claim_is_taken_as_something_to_check():
    found = P.claims_in(IDEA)
    godot = [claim for claim in found if "godot" in claim.claim.lower()]

    assert godot, f"the Godot claim was not picked up: {found}"
    assert godot[0].verdict == P.UNCERTAIN, \
        "a claim the boss asked to be checked was treated as settled"


@pytest.mark.parametrize("text", [
    "this library supports multiplayer",
    "that API is free",
    "the framework runs on Xbox",
    "Rust is faster than Go here",
])
def test_a_statement_about_the_world_is_a_claim(text):
    assert any(claim.verdict == P.UNCERTAIN for claim in P.claims_in(text)), \
        text


def test_a_preference_is_not_researched():
    """
    "Do you prefer dark mode" has no external truth, and reading four articles
    about it would be theatre.
    """
    found = P.claims_in("I think I want it to look quite minimal")
    assert all(claim.verdict == P.PREFERENCE for claim in found), found


@pytest.mark.asyncio
async def test_verifying_a_preference_changes_nothing():
    claim = P.Claim(claim="I think I want it simple", verdict=P.PREFERENCE)
    assert (await P.verify(claim)).verdict == P.PREFERENCE


@pytest.mark.asyncio
async def test_reading_sources_does_not_by_itself_settle_a_claim(monkeypatch):
    """
    The subtle failure. Four pages read is material for a judgement, not a
    judgement - and returning VERIFIED here would be the agreeing this exists
    to prevent, wearing a citation.
    """
    class Result:
        may_claim_completion = True
        error = ""
        output = {"sources": [{"url": "https://example.com"}]}

    class Runtime:
        def __init__(self, **_):
            pass

        def execute(self, *_a, **_k):
            return Result()

    monkeypatch.setattr("friday.capability_runtime.CapabilityRuntime", Runtime)
    checked = await P.verify(P.Claim(claim="Godot is the best choice"))

    assert checked.verdict == P.UNCERTAIN
    assert checked.sources == ("https://example.com",)


def test_the_challenge_instruction_refuses_to_soften():
    assert "asked" in P.CHALLENGE
    assert "before anything else" in P.CHALLENGE


def test_acceptance_describes_an_outcome_not_a_call():
    """
    "Call move_drone()" is an implementation detail wearing acceptance
    clothes: it passes while the product is broken.
    """
    criteria, _ = P.acceptance_for("The player can control the drone")

    assert criteria
    joined = " ".join(criteria).lower()
    assert "given" in joined and "then" in joined
    assert "()" not in joined


def test_an_unmeasurable_requirement_is_flagged_not_invented():
    """
    A fabricated "under 16ms" is worse than an admitted gap - it looks like a
    decision somebody made.
    """
    criteria, needs_target = P.acceptance_for("The game should feel good")

    assert needs_target
    assert any(P.NEEDS_TARGET in item for item in criteria)
    assert "16" not in " ".join(criteria), "a target was invented"


def test_a_requirement_that_carries_a_number_is_testable():
    _criteria, needs_target = P.acceptance_for(
        "The game must render at 60 fps")
    assert not needs_target


def test_nothing_asked_produces_nothing():
    assert P.acceptance_for("") == ((), False)


def test_a_contradiction_is_found():
    found = P.conflicts(["The game works offline",
                         "Players can play multiplayer online",
                         "The drone moves"])
    assert len(found) == 1
    assert "offline" in found[0][0]


def test_requirements_that_agree_produce_no_conflict():
    assert P.conflicts(["The drone moves", "The camera follows the drone"]) == []


def test_a_blocking_question_means_not_ready(store):
    REQ.ask("drone", [REQ.Question("Single-player or multiplayer?",
                                   why="decides the networking design",
                                   materiality=REQ.MATERIAL)], db=store)

    found = P.readiness("drone", db=store)
    assert found.state == P.NOT_READY
    assert not found.can_build
    assert "Single-player or multiplayer?" in found.blockers


def test_an_assumption_alone_still_allows_building(store):
    """
    Waiting for READY would mean never starting. The middle state is the
    normal one.
    """
    store.record_decision(project="drone", decision="desktop, not mobile",
                          source="the boss answered")
    REQ.ask("drone", [REQ.Question("What should the pause key be?",
                                   why="cosmetic", materiality=REQ.SMALL,
                                   proposed="Escape")], db=store)

    found = P.readiness("drone", db=store)
    assert found.state == P.READY_WITH_ASSUMPTIONS
    assert found.can_build
    assert any("Escape" in item for item in found.assumptions)


def test_everything_answered_is_ready(store):
    store.record_decision(project="drone", decision="desktop, not mobile",
                          source="the boss answered")
    assert P.readiness("drone", db=store).state == P.READY


def test_a_project_with_nothing_decided_is_not_ready(store):
    store.ensure_project("drone")
    found = P.readiness("drone", db=store)
    assert found.state == P.NOT_READY
    assert "nothing has been decided" in found.because


def test_a_requirement_keeps_its_acceptance(store):
    criteria, needs_target = P.acceptance_for("The player can control the drone")
    store.add_requirement("drone", "The player can control the drone",
                          acceptance=criteria, needs_target=needs_target,
                          source="the idea")

    found = store.requirements("drone")[0]
    assert found["acceptance"] == list(criteria)
    assert found["needs_target"] is False


def test_superseding_keeps_the_old_one_and_the_reason(store):
    """
    "Why did we remove multiplayer?" is a question the boss will ask weeks
    later, and a DELETE answers it with silence.
    """
    old = store.add_requirement("drone", "The game supports multiplayer")
    new = store.add_requirement("drone", "The game is single-player only")
    store.supersede_requirement(old, why="the boss cut it from version one",
                                replaced_by=new)

    live = store.requirements("drone")
    assert [item["statement"] for item in live] == \
        ["The game is single-player only"]

    everything = store.requirements("drone", include_superseded=True)
    retired = [item for item in everything if item["status"] == "SUPERSEDED"][0]
    assert retired["statement"] == "The game supports multiplayer"
    assert "cut it from version one" in retired["why_changed"]
    assert retired["superseded_by"] == new


def test_changing_one_requirement_leaves_the_others(store):
    keep = store.add_requirement("drone", "The player can control the drone")
    drop = store.add_requirement("drone", "The game supports multiplayer")
    store.supersede_requirement(drop, why="cut from version one")

    assert [item["id"] for item in store.requirements("drone")] == [keep]


def test_the_brief_is_assembled_from_the_store(store):
    store.record_decision(project="drone", decision="engine - Godot",
                          source="the boss answered")
    criteria, needs = P.acceptance_for("The game should feel responsive")
    store.add_requirement("drone", "The game should feel responsive",
                          category=P.PERFORMANCE, acceptance=criteria,
                          needs_target=needs)

    found = P.brief("drone", db=store)
    assert found["project"] == "drone"
    assert found["decisions"] == ["engine - Godot"]
    assert found["counts"]["requirements"] == 1
    assert found["counts"]["needs_target"] == 1
    assert found["readiness"] == P.READY


def test_the_brief_surfaces_conflicts_rather_than_choosing(store):
    store.record_decision(project="drone", decision="desktop",
                          source="the boss")
    store.add_requirement("drone", "The game works offline")
    store.add_requirement("drone", "Players can play multiplayer online")

    assert P.brief("drone", db=store)["conflicts"]


def test_an_unknown_project_has_no_brief(store):
    assert P.brief("atlantis", db=store) == {}
DRONE = 'Friday, I want to build a small desktop game where I control a combat drone. I think Godot is probably the best choice, but verify that instead of just agreeing with me. I want the first version to be simple enough to build quickly.'


def test_a_superlative_is_checked_against_alternatives_not_the_subject():
    """
    The measured failure. "Godot is the best choice" read godotengine.org -
    the vendor's own homepage - plus two Reddit fragments of 553 and 125
    characters, 2,496 characters in total. Friday hedged, which was the
    honest reading of sources that could not settle anything.

    Rewritten to ask the field, the same claim read 12,000 characters across
    four engine comparisons.
    """
    question = P.as_question('I think Godot is probably the best choice,', DRONE)
    assert question.startswith('Godot vs alternatives'), question
    assert 'best' not in question, 'the superlative is the thing being tested'


def test_the_question_carries_what_he_is_building():
    """
    Sentence splitting cuts the claim off at the comma before "but", so the
    drone game it is a choice *for* is in a different sentence entirely.
    Without it the search found people's existing drone projects on itch.io
    rather than an opinion about engines.
    """
    question = P.as_question('I think Godot is probably the best choice,', DRONE)
    assert 'combat drone' in question, question


def test_a_plain_fact_is_searched_as_written():
    """
    Bolting the project onto an unrelated assertion would search for
    something nobody claimed.
    """
    question = P.as_question('the API returns 429 under load', DRONE)
    assert question == 'the API returns 429 under load'


def test_a_comparison_that_names_both_sides_is_left_alone():
    """Already a checkable question. Nothing to add."""
    assert P.as_question('Rust is faster than Go', DRONE) == 'Rust is faster than Go'


def test_the_rewrite_carries_no_domain_knowledge():
    """
    "vs alternatives" has to work for a claim about anything, or this is a
    game-engine special case wearing a general name.
    """
    assert P.as_question('Postgres is the right choice for this') == 'Postgres vs alternatives'
    assert P.as_question('SQLite is the simplest option') == 'SQLite vs alternatives'


def test_the_hedge_is_still_stripped():
    assert not P.as_question('I think Godot is probably the best choice,', DRONE).startswith('I think')


def test_nothing_to_ask_stays_nothing():
    assert P.as_question('') == ''
    assert P.as_question('   ') == ''


def test_no_regex_escape_was_collapsed_into_a_control_byte():
    """
    Three times now: a heredoc turns `` into a literal backspace (0x08) and
    the regex silently stops matching word boundaries. Cheaper to assert than
    to find again.
    """
    import inspect
    source = inspect.getsource(P)
    assert '\x08' not in source


def test_an_unrelated_question_does_not_block_the_work_asked_about(store):
    """
    The flaw. One unanswered question used to make the whole project
    NOT_READY, so "is this single or multiplayer?" stopped the menu, the
    controls and the build pipeline as effectively as it stopped the netcode.
    """
    store.record_decision(project='drone', decision='engine - Godot', source='the boss')
    store.ask_question('drone', 'Is this single or multiplayer?', why='decides the netcode', impact='multiplayer netcode', blocking=True)
    scoped = P.readiness('drone', scope='the main menu', db=store)
    assert scoped.can_build, scoped.because
    assert not scoped.blockers


def test_the_dependent_work_is_still_blocked(store):
    store.record_decision(project='drone', decision='engine - Godot', source='the boss')
    store.ask_question('drone', 'Is this single or multiplayer?', why='decides the netcode', impact='multiplayer netcode', blocking=True)
    scoped = P.readiness('drone', scope='the multiplayer netcode', db=store)
    assert not scoped.can_build
    assert scoped.blockers


def test_the_whole_project_is_still_blocked_when_nothing_is_scoped(store):
    """
    Asking about the project as a whole gets the conservative answer. The
    scoping is a refinement for a specific piece of work, not a way to make
    blockers disappear.
    """
    store.record_decision(project='drone', decision='engine - Godot', source='the boss')
    store.ask_question('drone', 'Is this single or multiplayer?', why='decides the netcode', impact='multiplayer netcode', blocking=True)
    assert not P.readiness('drone', db=store).can_build


def test_what_is_blocked_is_reported_even_when_the_scope_can_proceed(store):
    """
    "You can start, but the netcode is waiting on a question" is the useful
    sentence, and a bare READY hides it.
    """
    store.record_decision(project='drone', decision='engine - Godot', source='the boss')
    store.ask_question('drone', 'Is this single or multiplayer?', why='decides the netcode', impact='multiplayer netcode', blocking=True)
    scoped = P.readiness('drone', scope='the main menu', db=store)
    assert scoped.blocked, 'nothing said which areas are still waiting'
    assert any(('multiplayer' in area.lower() for area in scoped.blocked))


def test_a_question_with_no_stated_impact_blocks_everything(store):
    """
    The safe reading. Somebody marked it blocking, and no stated impact is
    not evidence of no impact.
    """
    store.record_decision(project='drone', decision='engine - Godot', source='the boss')
    store.ask_question('drone', '?', why='', blocking=True)
    assert not P.readiness('drone', scope='the main menu', db=store).can_build


def test_common_words_do_not_link_a_question_to_everything(store):
    """
    Matching on "should" or "version" would make every question block every
    piece of work, which is the flaw wearing a different hat.
    """
    store.record_decision(project='drone', decision='engine - Godot', source='the boss')
    store.ask_question('drone', 'What should the first version do?', why='', impact='scope of the first version', blocking=True)
    scoped = P.readiness('drone', scope='the audio mixer', db=store)
    assert scoped.can_build, f"a generic question blocked unrelated work: {scoped.blockers}"


def test_readiness_still_needs_something_decided(store):
    store.ensure_project('empty')
    assert P.readiness('empty', db=store).state == P.NOT_READY
