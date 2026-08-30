"""
Picking the run a sentence means, and knowing how sure that makes us.

The failure: a fresh session was asked "how did that product catalogue job
finish?" and answered "do you have the run id?". He cannot. Friday invented
it.

The failure this exists to prevent next: "retry those" landing on whichever
run happens to be newest, and reprocessing a catalogue nobody was talking
about. A reader may act on a likely answer if it says which it picked. A
mutator may not act on a guess at all.
"""

from __future__ import annotations

from friday import runcontext as RC

RUNS = [
    {"run_id": "RUN-c", "source": "autumn.csv"},
    {"run_id": "RUN-b", "source": "spring.csv"},
    {"run_id": "RUN-a", "source": "spring.csv"},
]


# ---------------------------------------------------------------------------
# What it resolves to
# ---------------------------------------------------------------------------


def test_nothing_recorded_says_so_rather_than_guessing():
    found = RC.resolve([], noun="catalogue run")
    assert not found
    assert found.basis == RC.NOTHING_RECORDED
    assert "no catalogue run" in found.reason
    assert not found.safe_to_mutate


def test_one_run_needs_no_disambiguation():
    found = RC.resolve(RUNS[:1])
    assert (found.run_id, found.basis) == ("RUN-c", RC.ONLY_RUN)
    assert found.confidence == RC.CERTAIN
    assert found.safe_to_mutate


def test_the_run_this_session_started_wins_over_the_newest():
    """
    "Retry those" means the catalogue we were just talking about - not the
    newest row in a database that also holds every gate run from last night.
    """
    found = RC.resolve(RUNS, active_run_id="RUN-b")
    assert (found.run_id, found.basis) == ("RUN-b", RC.CURRENT_ACTIVE_RUN)
    assert found.safe_to_mutate


def test_an_active_run_that_is_not_in_the_list_is_ignored():
    """A stale id must not resurrect a run that no longer exists."""
    found = RC.resolve(RUNS, active_run_id="RUN-deleted")
    assert found.run_id == "RUN-c"
    assert found.basis == RC.LAST_DOMAIN_RUN


def test_a_hint_that_fits_one_run_resolves_to_it():
    found = RC.resolve(RUNS, hint="autumn")
    assert (found.run_id, found.basis) == ("RUN-c", RC.UNIQUE_RECENT_MATCH)
    assert found.safe_to_mutate


def test_a_hint_that_fits_several_refuses_and_offers_them():
    found = RC.resolve(RUNS, hint="spring.csv")
    assert found.basis == RC.AMBIGUOUS
    assert not found.run_id, "it picked one anyway"
    assert not found.safe_to_mutate
    assert found.candidate_count == 2
    assert [c["run_id"] for c in found.candidates] == ["RUN-b", "RUN-a"]


def test_a_hint_that_fits_nothing_falls_back_rather_than_failing():
    """The hint may be his words for the job, not anything we recorded."""
    found = RC.resolve(RUNS, hint="the one from Tuesday")
    assert found.run_id == "RUN-c"


def test_a_partial_run_id_is_a_hint_like_any_other():
    assert RC.resolve(RUNS, hint="RUN-a").run_id == "RUN-a"


# ---------------------------------------------------------------------------
# And what that permits
# ---------------------------------------------------------------------------


def test_the_newest_of_several_is_readable_but_not_mutable():
    """
    The whole point of the type. Naming the newest is helpful; re-running the
    newest because it is newest is a coin toss with side effects.
    """
    found = RC.resolve(RUNS)
    assert found.run_id == "RUN-c", "a reader still gets an answer"
    assert found.basis == RC.LAST_DOMAIN_RUN
    assert found.confidence == RC.LIKELY
    assert not found.safe_to_mutate
    assert "3" in found.reason and "autumn.csv" in found.reason


def test_every_basis_that_permits_mutation_is_a_single_candidate():
    for basis in RC.SAFE_TO_MUTATE:
        assert basis in RC.BASES
    assert RC.LAST_DOMAIN_RUN not in RC.SAFE_TO_MUTATE
    assert RC.AMBIGUOUS not in RC.SAFE_TO_MUTATE
    assert RC.NOTHING_RECORDED not in RC.SAFE_TO_MUTATE


def test_an_empty_run_id_is_never_safe_to_mutate():
    assert not RC.Resolution("", RC.EXPLICIT_RUN_ID, "", 1,
                             RC.CERTAIN).safe_to_mutate


def test_the_reason_is_never_empty():
    """It is what makes a wrong pick visible instead of silent."""
    for hint in ("", "spring", "autumn", "nonsense"):
        assert RC.resolve(RUNS, hint=hint).reason.strip()
