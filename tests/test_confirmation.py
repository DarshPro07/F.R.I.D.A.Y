"""
A yes that means one thing.

The dangerous shape is not "Friday asked" - it is asking about a restart,
hearing yes, and terminating a process. Four migration batches in a row a
request landed on a capability that merely shared vocabulary with the intended
one, so a confirmation scoped to POWER_ACTION rather than to *this restart*
would be a routing bug away from doing the wrong irreversible thing.
"""

from __future__ import annotations

from datetime import timedelta

import pytest

from friday import confirmation as CF


@pytest.fixture
def book():
    return CF.Book()


def asked(book, action="RESTART_MACHINE", target="LOCAL_MACHINE",
          run_id="RUN-1", arguments=None, seconds=60.0):
    return book.ask(run_id, action, target, f"{action} on {target}?",
                    arguments, seconds)


# ---------------------------------------------------------------------------
# One action
# ---------------------------------------------------------------------------


def test_the_ordinary_path(book):
    confirmation = asked(book)
    assert book.approve(confirmation.nonce).ok
    verdict = book.consume(confirmation.nonce, run_id="RUN-1",
                           action="RESTART_MACHINE", target="LOCAL_MACHINE")
    assert verdict.ok


def test_a_yes_to_a_restart_cannot_terminate_a_process(book):
    """The failure this module exists to make impossible."""
    confirmation = asked(book, action="RESTART_MACHINE")
    book.approve(confirmation.nonce)

    verdict = book.consume(confirmation.nonce, run_id="RUN-1",
                           action="FORCE_TERMINATE", target="pid:4216")
    assert not verdict.ok
    assert "RESTART_MACHINE" in verdict.reason
    assert "FORCE_TERMINATE" in verdict.reason


def test_a_yes_for_one_target_does_not_cover_another(book):
    confirmation = asked(book, action="FORCE_TERMINATE", target="spotify#7712")
    book.approve(confirmation.nonce)
    assert not book.consume(confirmation.nonce, run_id="RUN-1",
                            action="FORCE_TERMINATE",
                            target="chrome#4216").ok


def test_changing_the_arguments_invalidates_it(book):
    confirmation = asked(book, action="SHUTDOWN",
                         arguments={"force": False})
    book.approve(confirmation.nonce)
    assert not book.consume(confirmation.nonce, run_id="RUN-1",
                            action="SHUTDOWN", target="LOCAL_MACHINE",
                            arguments={"force": True}).ok, \
        "a yes to a graceful shutdown authorised a forced one"


def test_a_yes_belongs_to_the_run_it_was_given_in(book):
    confirmation = asked(book, run_id="RUN-1")
    book.approve(confirmation.nonce)
    assert not book.consume(confirmation.nonce, run_id="RUN-2",
                            action="RESTART_MACHINE",
                            target="LOCAL_MACHINE").ok


def test_argument_order_does_not_change_the_fingerprint(book):
    confirmation = asked(book, action="SHUTDOWN",
                         arguments={"force": False, "delay": 0})
    book.approve(confirmation.nonce)
    assert book.consume(confirmation.nonce, run_id="RUN-1", action="SHUTDOWN",
                        target="LOCAL_MACHINE",
                        arguments={"delay": 0, "force": False}).ok


def test_an_unserialisable_argument_does_not_break_the_binding(book):
    from pathlib import Path

    confirmation = asked(book, action="EXPORT",
                         arguments={"path": Path("E:/x.csv")})
    book.approve(confirmation.nonce)
    assert book.consume(confirmation.nonce, run_id="RUN-1", action="EXPORT",
                        target="LOCAL_MACHINE",
                        arguments={"path": Path("E:/x.csv")}).ok


# ---------------------------------------------------------------------------
# One use
# ---------------------------------------------------------------------------


def test_a_confirmation_cannot_be_spent_twice(book):
    """No "confirmed power actions for the next ten minutes"."""
    confirmation = asked(book)
    book.approve(confirmation.nonce)
    first = book.consume(confirmation.nonce, run_id="RUN-1",
                         action="RESTART_MACHINE", target="LOCAL_MACHINE")
    second = book.consume(confirmation.nonce, run_id="RUN-1",
                          action="RESTART_MACHINE", target="LOCAL_MACHINE")
    assert first.ok
    assert not second.ok
    assert "already been used" in second.reason


def test_an_unapproved_confirmation_cannot_be_consumed(book):
    confirmation = asked(book)
    verdict = book.consume(confirmation.nonce, run_id="RUN-1",
                           action="RESTART_MACHINE", target="LOCAL_MACHINE")
    assert not verdict.ok
    assert "not been approved" in verdict.reason


def test_a_refusal_is_permanent(book):
    """
    Found by this test: `approve` did not check for a prior refusal, so a no
    followed by a second approval moved the state back to APPROVED and the
    action went through. A no is final; asking again means asking again.
    """
    confirmation = asked(book)
    book.refuse(confirmation.nonce)

    again = book.approve(confirmation.nonce)
    assert not again.ok
    assert "already refused" in again.reason
    assert not book.consume(confirmation.nonce, run_id="RUN-1",
                            action="RESTART_MACHINE",
                            target="LOCAL_MACHINE").ok


def test_an_unknown_nonce_is_refused(book):
    assert not book.approve("invented").ok
    assert not book.consume("invented", run_id="RUN-1", action="X",
                            target="Y").ok


# ---------------------------------------------------------------------------
# One moment
# ---------------------------------------------------------------------------


def test_an_expired_confirmation_cannot_be_approved(book):
    confirmation = asked(book, seconds=0.0)
    verdict = book.approve(confirmation.nonce)
    assert not verdict.ok
    assert "expired" in verdict.reason


def test_an_expired_confirmation_cannot_be_consumed(book):
    """
    A yes from four minutes ago was about the machine as it was four minutes
    ago.
    """
    confirmation = asked(book, seconds=60.0)
    book.approve(confirmation.nonce)
    confirmation.expires_at = confirmation.created_at - timedelta(seconds=1)
    verdict = book.consume(confirmation.nonce, run_id="RUN-1",
                           action="RESTART_MACHINE", target="LOCAL_MACHINE")
    assert not verdict.ok
    assert "expired" in verdict.reason


def test_expired_and_spent_confirmations_are_forgotten(book):
    spent = asked(book)
    book.approve(spent.nonce)
    book.consume(spent.nonce, run_id="RUN-1", action="RESTART_MACHINE",
                 target="LOCAL_MACHINE")
    stale = asked(book, seconds=0.0)
    alive = asked(book, action="SHUTDOWN")

    assert book.forget_expired() == 2
    assert list(book.pending) == [alive.nonce]


# ---------------------------------------------------------------------------
# The handle itself
# ---------------------------------------------------------------------------


def test_two_requests_never_share_a_nonce(book):
    nonces = {asked(book).nonce for _ in range(50)}
    assert len(nonces) == 50


def test_what_the_model_sees_carries_the_question_and_no_fingerprint(book):
    described = asked(book).to_dict()
    assert "question" in described and "seconds_left" in described
    assert "fingerprint" not in described, \
        "the fingerprint is for checking, not for quoting back"


def test_the_shared_book_can_be_reset():
    CF.book.ask("RUN-1", "X", "Y", "?")
    CF.reset()
    assert not CF.book.pending
