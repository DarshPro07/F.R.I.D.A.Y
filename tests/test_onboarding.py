"""
Progressive onboarding (FR-ONB-004), and policy compiled from it (FR-POL-002).

The PRD's acceptance criteria are the test list: a normal user operational in
roughly five to ten minutes, deep personalisation continuing afterwards
rather than blocking, and a profile that is inspectable memory and policy
rather than a hidden system prompt.

The last one is what most of these tests are about, because it is the one an
implementation quietly fails. A prompt paragraph assembled from the answers
would satisfy a demo and leave the user unable to see, correct or delete what
FRIDAY believes about them.
"""
from __future__ import annotations

import json

import pytest

from friday import onboarding as O
from friday import workspace as W


@pytest.fixture
def store(tmp_path, monkeypatch):
    from friday.store import Store
    monkeypatch.delenv(W.ENV_VAR, raising=False)
    W.reset_override()
    db = Store(tmp_path / "onboard.sqlite3")
    db.ensure_default_workspace()
    yield db
    db.close()
    W.reset_override()


@pytest.fixture
def interview(store):
    return O.Onboarding(store)


# --- the profile is memory, not a prompt ------------------------------------

def test_an_answer_becomes_an_inspectable_memory_row(interview, store):
    interview.record({"profession": "I run an online clothing brand"})
    rows = store.search_memories("profile.profession")
    assert len(rows) == 1
    row = rows[0]
    assert row["value"] == "I run an online clothing brand"
    assert row["source"] == "onboarding", \
        "the origin must be visible in provenance, not inferred later"
    assert row["memory_type"] == "user"


def test_the_profile_reads_back_out_of_memory(interview):
    interview.record({"profession": "founder", "company": "Crunchy Fashion"})
    profile = interview.profile()
    assert profile.get("profession") == "founder"
    assert profile.get("company") == "Crunchy Fashion"


def test_deleting_the_memory_changes_the_profile(interview, store):
    """The consequence of storing the profile in memory rather than in a
    cache: forgetting in the control room actually forgets."""
    interview.record({"company": "Crunchy Fashion"})
    assert interview.profile().get("company") == "Crunchy Fashion"
    store.forget("profile.company")
    assert interview.profile().get("company") is None


def test_a_list_answer_round_trips_as_a_list(interview, store):
    interview.record({"applications": ["Chrome", "Shopify admin", " Gmail "]})
    assert interview.profile().get("applications") == ["Chrome", "Shopify admin", "Gmail"]
    stored = store.search_memories("profile.applications")[0]["value"]
    assert json.loads(stored) == ["Chrome", "Shopify admin", "Gmail"], \
        "a list must be stored as data, not as a sentence a reader has to parse"


def test_a_later_answer_supersedes_without_destroying_the_earlier_one(interview, store):
    interview.record({"company": "Crunchy"})
    interview.record({"company": "Crunchy Fashion"})
    assert interview.profile().get("company") == "Crunchy Fashion"
    history = store.recall("profile.company", include_superseded=True)
    assert len(history) == 2, "the earlier answer was overwritten rather than superseded"


# --- progressive, not a questionnaire ---------------------------------------

def test_an_answered_question_is_not_asked_again(interview):
    before = {q["field"] for q in interview.pending()}
    assert "profession" in before
    interview.record({"profession": "founder"})
    assert "profession" not in {q["field"] for q in interview.pending()}


def test_a_declined_question_is_not_asked_again_either(interview):
    """Declining is an answer. Asking again every session is how a user
    learns to ignore the assistant."""
    interview.skip("sensitive_data", why="would rather not say")
    assert "sensitive_data" not in {q["field"] for q in interview.pending()}
    assert "sensitive_data" in interview.profile().skipped
    assert interview.profile().get("sensitive_data") is None


def test_pending_comes_back_in_interview_order(interview):
    stages = [q["stage"] for q in interview.pending()]
    assert stages == sorted(stages, key=lambda s: [x.id for x in O.STAGES].index(s))
    assert stages[0] == "who", "identity should come first"


def test_a_user_is_useful_once_the_essential_stages_are_done(interview):
    """The five-to-ten-minute promise, as a predicate rather than a hope."""
    assert not interview.complete_enough()
    for stage in O.STAGES:
        if stage.id not in O.ESSENTIAL:
            continue
        for question in stage.questions:
            interview.record({question.field: "something"})
    assert interview.complete_enough()
    assert interview.pending(), \
        "personalisation should continue after the user is useful, not stop"


def test_progress_tells_the_user_where_they_are(interview):
    interview.record({"call_me": "Darsh"})
    progress = interview.progress()
    assert progress["answered"] == 1
    assert progress["total"] > 10
    assert progress["essential_remaining"] > 0
    assert progress["useful_now"] is False
    assert {s["id"] for s in progress["stages"]} == {s.id for s in O.STAGES}


def test_an_unknown_field_is_refused_rather_than_silently_stored(interview):
    """A typo that wrote an unreachable memory row would be invisible: the
    profile would not show it and nothing would complain."""
    with pytest.raises(KeyError):
        interview.record({"favourite_colour": "blue"})
    with pytest.raises(KeyError):
        interview.skip("favourite_colour")


# --- the workspace ----------------------------------------------------------

def test_the_workspace_is_named_after_the_company(interview, store):
    interview.record({"company": "Crunchy Fashion"})
    workspace_id = interview.open_workspace(kind="brand")
    assert workspace_id == "crunchy-fashion"
    registry = {w["id"]: w for w in store.workspaces()}
    assert registry["crunchy-fashion"]["label"] == "Crunchy Fashion"
    assert registry["crunchy-fashion"]["kind"] == "brand"
    assert registry["crunchy-fashion"]["active"] == 1


def test_a_user_with_no_company_stays_in_the_default_workspace(interview, store):
    assert interview.open_workspace() == W.DEFAULT_ID
    assert store.active_workspace() == W.DEFAULT_ID


def test_two_workspaces_do_not_see_each_others_profiles(store):
    """The profile is private state, so the tenant boundary applies to it
    like any other memory."""
    store.register_workspace("brand-a", label="A", kind="brand")
    store.register_workspace("brand-b", label="B", kind="brand")
    O.Onboarding(store, workspace_id="brand-a").record({"company": "A Ltd"})
    O.Onboarding(store, workspace_id="brand-b").record({"company": "B Ltd"})
    assert O.Onboarding(store, workspace_id="brand-a").profile().get("company") == "A Ltd"
    assert O.Onboarding(store, workspace_id="brand-b").profile().get("company") == "B Ltd"


# --- policy from the profile (FR-POL-002) -----------------------------------

def test_policy_is_compiled_from_the_profile_not_from_a_template(interview, tmp_path):
    from friday.user_policy import UserPolicy
    policy = UserPolicy(tmp_path / "policy.sqlite3")
    interview._policy = policy

    out = interview.compile_policy(level="Autonomous Routine Work")
    assert out["level"] == "Autonomous Routine Work"
    assert policy.level() == "Autonomous Routine Work"
    assert policy.state_of("workspace_access") == "AUTO"


def test_a_user_who_says_nothing_gets_the_behaviour_the_product_always_had(interview, tmp_path):
    """The safe default. Guessing a higher level on someone's behalf is the
    one mistake this function must not make."""
    from friday.user_policy import UserPolicy
    interview._policy = UserPolicy(tmp_path / "policy.sqlite3")
    assert interview.compile_policy()["level"] == "Ask Before Acting"


def test_an_unknown_level_is_refused(interview, tmp_path):
    from friday.user_policy import UserPolicy
    interview._policy = UserPolicy(tmp_path / "policy.sqlite3")
    with pytest.raises(ValueError):
        interview.compile_policy(level="Do Whatever You Like")


def test_compiled_policy_still_cannot_reach_the_constitutional_set(interview, tmp_path):
    """NEGATIVE CONTROL. Onboarding is a new door into policy, so it needs
    its own proof that the door does not open onto the kernel - a permission
    that cannot be granted by conversation must not become grantable by
    answering an interview question."""
    from friday.user_policy import CONSTITUTIONAL_DENY, UserPolicy
    policy = UserPolicy(tmp_path / "policy.sqlite3")
    interview._policy = policy
    interview.compile_policy(level="High Autonomy")
    for domain in CONSTITUTIONAL_DENY:
        assert policy.state_of(domain) != "AUTO", \
            f"{domain} became AUTO through onboarding"


def test_the_consequential_domains_survive_the_highest_level(interview, tmp_path):
    from friday.user_policy import UserPolicy
    policy = UserPolicy(tmp_path / "policy.sqlite3")
    interview._policy = policy
    interview.compile_policy(level="High Autonomy")
    for domain in ("spend_money", "buy_domain", "budget_envelope_change",
                   "delete_data"):
        assert policy.state_of(domain) == "CONFIRM", \
            f"{domain} was delegated by onboarding; FR-POL-003 outranks the interview"


# --- what the profile implies (FR-AGT-001) ----------------------------------

def test_workers_are_suggested_from_the_answers_with_the_reason(interview):
    interview.record({"profession": "I run a Shopify store",
                      "typical_work": "customer support and Instagram content"})
    suggestions = interview.suggested_workers()
    roles = {s["role"] for s in suggestions}
    assert "Storefront operator" in roles
    assert "Customer correspondent" in roles
    assert "Content producer" in roles
    assert all(s["because"] for s in suggestions), \
        "a suggested worker must carry the answer that justified it"


def test_an_empty_profile_suggests_nothing(interview):
    """The PRD forbids instantiating a fixed swarm at install. An install
    that knows nothing about the user has no basis for a single worker."""
    assert interview.suggested_workers() == []


def test_suggesting_a_worker_creates_nothing(interview, store):
    interview.record({"profession": "I run a Shopify store"})
    interview.suggested_workers()
    assert not store.search_memories("worker"), \
        "suggesting a worker wrote something; it must only propose"


# --- the owner's console ----------------------------------------------------
#
# The console is a production entry point (it is why `friday/onboarding`
# carries a reachability declaration), so it is tested like one. These run
# the real argparse dispatch against a temporary state root.

@pytest.fixture
def console(tmp_path):
    """The console, pointed at a throwaway state root and database.

    The environment is restored before the modules are reloaded back: reloaded
    while FRIDAY_HOME still named this test's home, the config kept a DATA_DIR
    in a deleted temp directory for every later test in the process."""
    import importlib

    from friday import config
    import friday.store
    with pytest.MonkeyPatch.context() as env:
        env.setenv("ADA_DB", str(tmp_path / "console.sqlite3"))
        env.setenv("FRIDAY_HOME", str(tmp_path / "home"))
        env.delenv(W.ENV_VAR, raising=False)
        W.reset_override()
        importlib.reload(config)
        importlib.reload(friday.store)
        module = importlib.reload(O)
        yield module.console
    importlib.reload(config)
    importlib.reload(friday.store)
    importlib.reload(O)
    W.reset_override()


def test_the_console_reports_status_before_anything_is_answered(console, capsys):
    assert console(["status"]) == 0
    printed = capsys.readouterr().out
    assert "essential question(s) left" in printed
    assert "who" in printed and "Who you are" in printed


def test_the_console_records_an_answer_and_reads_it_back(console, capsys):
    assert console(["answer", "profession", "I", "run", "a", "clothing", "brand"]) == 0
    capsys.readouterr()
    assert console(["profile"]) == 0
    assert "I run a clothing brand" in capsys.readouterr().out


def test_the_console_splits_a_comma_list_into_items(console, capsys):
    assert console(["answer", "applications", "Chrome, Shopify admin, Gmail"]) == 0
    capsys.readouterr()
    console(["profile"])
    printed = capsys.readouterr().out
    assert "Chrome" in printed and "Shopify admin" in printed


def test_the_console_asks_the_next_question_and_names_the_field(console, capsys):
    assert console(["next", "--essential"]) == 0
    printed = capsys.readouterr().out
    assert "answer call_me" in printed, \
        "the console should tell the user how to answer, not just what was asked"


def test_the_console_records_a_decline(console, capsys):
    assert console(["skip", "sensitive_data", "--why", "rather not say"]) == 0
    assert "will not be asked again" in capsys.readouterr().out
    console(["next"])
    assert "sensitive" not in capsys.readouterr().out.lower()


def test_the_console_suggests_nothing_from_an_empty_profile(console, capsys):
    assert console(["workers"]) == 0
    assert "does not say enough" in capsys.readouterr().out


def test_the_console_refuses_an_unknown_field(console):
    with pytest.raises(KeyError):
        console(["answer", "favourite_colour", "blue"])


def test_the_console_registers_a_workspace_from_the_company(console, capsys):
    console(["answer", "company", "Crunchy Fashion"])
    capsys.readouterr()
    assert console(["workspace", "--kind", "brand"]) == 0
    assert "crunchy-fashion registered and active" in capsys.readouterr().out
