"""
V7-D08 (PRD v7): one confirmation book for every FRIDAY process.

A confirmation asked in the MCP process - a purchase in the browser, a
permanent delete - could not be answered: the person answers where the
person is (voice, the control room), and each process kept its own book in
memory. The shared book lives in the store, so a yes given in one process is
seen by the process that asked.

What the in-memory book guaranteed still holds. A yes is spent by the
process instance that asked and by nobody else, so a restart voids anything
asked before it: a yes to a machine that no longer exists in that state
authorises nothing. Spending is one conditional UPDATE, so two spenders
cannot both win. Only the fingerprint is stored, never the arguments, which
can carry what was typed into a form.
"""
from __future__ import annotations

import threading

import pytest

from friday import approval
from friday import confirmation as CF
from friday.store import Store

ARGS = {"permanent": True}


@pytest.fixture
def stores(tmp_path):
    """Two connections on one file: the process that asks and the one the
    person answers in."""
    path = tmp_path / "shared.sqlite3"
    asking, answering = Store(path), Store(path)
    yield asking, answering
    asking.close()
    answering.close()


@pytest.fixture
def books(stores):
    asking, answering = stores
    return (CF.StoredBook(lambda: asking, instance="MCP-1"),
            CF.StoredBook(lambda: answering, instance="VOICE-1"))


def _ask(book, **overrides):
    fields = dict(run_id="RUN-1", action="files.delete", target="x.txt",
                  question="Delete x.txt for good?", arguments=ARGS)
    fields.update(overrides)
    return book.ask(**fields)


def _spend(book, nonce, **overrides):
    fields = dict(run_id="RUN-1", action="files.delete", target="x.txt", arguments=ARGS)
    fields.update(overrides)
    return book.consume(nonce, **fields)


def test_a_yes_given_in_another_process_is_spent_by_the_one_that_asked(books):
    asker, answerer = books
    asked = _ask(asker)
    assert answerer.approve(asked.nonce).ok
    assert _spend(asker, asked.nonce).ok


def test_a_yes_is_spent_once(books):
    asker, answerer = books
    asked = _ask(asker)
    answerer.approve(asked.nonce)
    assert _spend(asker, asked.nonce).ok
    again = _spend(asker, asked.nonce)
    assert not again.ok and "already been used" in again.reason


def test_only_the_process_that_asked_can_spend_the_yes(books):
    asker, answerer = books
    asked = _ask(asker)
    answerer.approve(asked.nonce)
    stolen = _spend(answerer, asked.nonce)
    assert not stolen.ok and "another process" in stolen.reason
    assert _spend(asker, asked.nonce).ok, "the refusal must not have spent it"


def test_a_restart_voids_what_was_asked_before_it(stores):
    asking, answering = stores
    before = CF.StoredBook(lambda: asking, instance="MCP-before-restart")
    asked = _ask(before)
    CF.StoredBook(lambda: answering, instance="VOICE-1").approve(asked.nonce)
    after = CF.StoredBook(lambda: asking, instance="MCP-after-restart")
    assert not _spend(after, asked.nonce).ok


def test_a_yes_for_one_action_cannot_be_spent_on_another(books):
    asker, answerer = books
    asked = _ask(asker)
    answerer.approve(asked.nonce)
    other = _spend(asker, asked.nonce, target="y.txt")
    assert not other.ok and "that yes was for" in other.reason
    assert _spend(asker, asked.nonce).ok


def test_a_yes_to_an_expired_question_is_no_yes(books):
    asker, answerer = books
    asked = _ask(asker, seconds=-1)
    verdict = answerer.approve(asked.nonce)
    assert not verdict.ok and "expired" in verdict.reason
    assert not _spend(asker, asked.nonce).ok


def test_a_no_is_final(books):
    asker, answerer = books
    asked = _ask(asker)
    answerer.refuse(asked.nonce)
    assert not answerer.approve(asked.nonce).ok
    assert not _spend(asker, asked.nonce).ok


def test_two_spenders_racing_for_one_yes_cannot_both_win(stores):
    """Same instance, two threads, one approved yes: the conditional UPDATE
    lets exactly one through."""
    asking, answering = stores
    third = Store(asking.path)
    try:
        first = CF.StoredBook(lambda: asking, instance="MCP-1")
        second = CF.StoredBook(lambda: third, instance="MCP-1")
        asked = _ask(first)
        CF.StoredBook(lambda: answering, instance="VOICE-1").approve(asked.nonce)
        barrier, results = threading.Barrier(2), []

        def spend(book):
            barrier.wait()
            results.append(_spend(book, asked.nonce).ok)

        threads = [threading.Thread(target=spend, args=(b,)) for b in (first, second)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
    finally:
        third.close()
    assert sorted(results) == [False, True]


def test_the_arguments_themselves_are_not_stored(stores, books):
    asker, _ = books
    _ask(asker, arguments={"password": "hunter2-not-for-the-disk"})
    rows = stores[0]._conn.execute("SELECT * FROM confirmations").fetchall()
    assert rows and "hunter2" not in repr([dict(r) for r in rows])


def test_what_is_waiting_is_listed_for_the_person(books):
    asker, answerer = books
    asked = _ask(asker)
    waiting = approval.awaiting(answerer)
    assert [c.nonce for c in waiting] == [asked.nonce]


def test_the_persons_spoken_yes_reaches_the_process_that_asked(books):
    asker, answerer = books
    asked = _ask(asker)
    assert approval.answer(answerer, "yes").ok
    assert _spend(asker, asked.nonce).ok


def test_the_process_book_is_the_shared_one():
    assert isinstance(CF.book, CF.StoredBook)
