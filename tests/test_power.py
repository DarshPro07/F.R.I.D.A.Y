"""
Turning the machine off, and everything that has to be true first.

Nothing here actually shuts anything down. The Win32 calls are replaced at the
platform boundary, which is exactly where the honest limit of this feature sits:
what Friday does with the request is testable, and whether Windows then really
restarts is not something a test suite gets to find out repeatedly.

What is proven here is the part that can be wrong in Friday:

    no yes                  nothing is requested
    a yes for something else  refused, naming the mismatch
    FULL autonomy           still asks
    nobody present          refused outright, nothing left pending
    request accepted        INITIATED, and never SUCCEEDED
    a normal yes            does not authorise the forced variant
"""
from __future__ import annotations
import sys
import pytest
from friday import confirmation as CF
from friday import contracts as c
from friday import policy as p
from friday import power_state
from friday.platform import windows as native
from friday.store import Store
from friday.toolsets import power as W
pytestmark = pytest.mark.skipif(sys.platform != 'win32', reason='Win32')


@pytest.fixture
def book():
    return CF.Book()


@pytest.fixture
def run():
    return c.Run.create("shut it down", capability="system")


@pytest.fixture(autouse=True)
def store(monkeypatch):
    """An in-memory store, so no test writes a pending power row for real."""
    store = Store(":memory:")
    monkeypatch.setattr(W, "_get_store", lambda: store)
    yield store
    store.close()


@pytest.fixture(autouse=True)
def _never_actually_do_it(monkeypatch):
    """
    The platform boundary, replaced. Everything above it is real.

    Each stub records that it was called so a test can assert the request was
    or was not made - which is the thing that matters, since "did Friday ask
    Windows to restart" is precisely what must not happen by accident.
    """
    calls: dict[str, list] = {"shutdown": [], "abort": [], "suspend": [],
                              "lock": []}

    monkeypatch.setattr(native, "enable_shutdown_privilege",
                        lambda enabled=True: True)
    monkeypatch.setattr(
        native, "InitiateShutdownW",
        lambda machine, message, grace, flags, reason:
            calls["shutdown"].append((grace, flags)) or native.ERROR_SUCCESS)
    monkeypatch.setattr(
        native, "AbortSystemShutdownW",
        lambda machine: calls["abort"].append(machine) or True)
    monkeypatch.setattr(
        native, "SetSuspendState",
        lambda hibernate, force, wake:
            calls["suspend"].append((hibernate, force)) or True)
    monkeypatch.setattr(native, "LockWorkStation",
                        lambda: calls["lock"].append(True) or True)
    return calls


@pytest.mark.parametrize('call', [W.power_lock, W.power_sleep, W.power_hibernate, W.power_shutdown, W.power_restart])
def test_without_a_yes_nothing_is_requested(call, run, book, _never_actually_do_it):
    result = call(run, book=book)
    assert not any(_never_actually_do_it.values()), 'a power request was made before anybody said yes'
    if result.status == c.UNSUPPORTED:
        assert result.error, 'an unsupported action did not say why'
        return
    assert result.status == c.CANCELLED
    assert result.output['confirm']['nonce']


@pytest.mark.parametrize("call", [
    W.power_lock, W.power_sleep, W.power_shutdown, W.power_restart,
])
def test_full_autonomy_does_not_answer_for_the_person(call, run, book,
                                                      _never_actually_do_it):
    """
    The whole reason CONFIRM exists. FULL turns every ASK into a yes, which is
    right for the volume and wrong for the machine.
    """
    engine = p.PolicyEngine(autonomy=p.FULL)
    result = call(run, book=book, engine=engine)

    assert result.status == c.CANCELLED
    assert not any(_never_actually_do_it.values())


def test_the_question_names_the_action_the_target_and_the_cost(run, book):
    result = W.power_restart(run, book=book)

    assert "Restart this computer?" in result.error
    assert "Unsaved work may be lost" in result.error
    assert result.output["target"] == "LOCAL_MACHINE"
    assert result.output["unsaved_work_at_risk"] is True


def test_locking_does_not_claim_unsaved_work_is_at_risk(run, book):
    """Nothing is lost by locking, and saying otherwise would be noise."""
    result = W.power_lock(run, book=book)
    assert result.output["unsaved_work_at_risk"] is False


def test_a_shutdown_yes_cannot_restart(run, book, _never_actually_do_it):
    asked = W.power_shutdown(run, book=book)
    book.approve(asked.output["confirm"]["nonce"])

    result = W.power_restart(run, asked.output["confirm"]["nonce"], book=book)

    assert result.status == c.FAILED
    assert "SHUTDOWN_MACHINE" in result.error
    assert not _never_actually_do_it["shutdown"]


def test_a_normal_restart_yes_cannot_force_one(run, book,
                                               _never_actually_do_it):
    """
    FR-027. "Restart" and "force restart" are different decisions, and the
    difference is whether everyone else's unsaved work survives.
    """
    asked = W.power_restart(run, book=book)
    nonce = asked.output["confirm"]["nonce"]
    book.approve(nonce)

    result = W.power_restart(run, nonce, force=True, book=book)

    assert result.status == c.FAILED
    assert "RESTART_MACHINE" in result.error
    assert not _never_actually_do_it["shutdown"]


def test_the_forced_variant_says_what_it_costs(run, book):
    result = W.power_restart(run, force=True, book=book)
    assert "NOT be given the chance to save" in result.error


def test_a_spent_yes_cannot_be_spent_again(run, book, _never_actually_do_it):
    asked = W.power_restart(run, book=book)
    nonce = asked.output["confirm"]["nonce"]
    book.approve(nonce)

    assert W.power_restart(run, nonce, book=book).status == c.INITIATED
    again = W.power_restart(run, nonce, book=book)

    assert again.status == c.FAILED
    assert len(_never_actually_do_it["shutdown"]) == 1


def test_an_accepted_restart_is_initiated_and_never_succeeded(
        run, book, _never_actually_do_it):
    """
    The sharpest truthfulness trap in the feature. Windows accepting the
    request looks exactly like the machine restarting, and the only thing
    separating them is this status.
    """
    asked = W.power_restart(run, book=book)
    nonce = asked.output["confirm"]["nonce"]
    book.approve(nonce)

    result = W.power_restart(run, nonce, book=book)

    assert result.status == c.INITIATED
    assert result.status != c.SUCCEEDED
    assert not result.may_claim_completion
    assert "has not happened yet" in result.honest_summary()


def test_the_countdown_is_long_enough_to_call_back(run, book,
                                                   _never_actually_do_it):
    asked = W.power_shutdown(run, book=book)
    nonce = asked.output["confirm"]["nonce"]
    book.approve(nonce)

    result = W.power_shutdown(run, nonce, book=book)
    grace, _flags = _never_actually_do_it["shutdown"][0]

    assert grace == W.GRACE_SECONDS
    assert grace >= W.CALLBACK_FLOOR_SECONDS
    assert result.output["can_be_called_back"] is True
    assert result.output["seconds_until"] == W.GRACE_SECONDS


def test_a_lock_request_is_initiated_not_verified(run, book):
    asked = W.power_lock(run, book=book)
    nonce = asked.output["confirm"]["nonce"]
    book.approve(nonce)

    result = W.power_lock(run, nonce, book=book)

    assert result.status == c.INITIATED
    assert "requested" in result.verification.evidence


def test_the_ordinary_path_sets_no_force_flag(run, book,
                                              _never_actually_do_it):
    asked = W.power_shutdown(run, book=book)
    nonce = asked.output["confirm"]["nonce"]
    book.approve(nonce)
    W.power_shutdown(run, nonce, book=book)

    _grace, flags = _never_actually_do_it["shutdown"][0]
    assert not flags & native.SHUTDOWN_FORCE_OTHERS
    assert not flags & native.SHUTDOWN_FORCE_SELF


def test_suspending_never_forces_either(run, book, _never_actually_do_it):
    asked = W.power_sleep(run, book=book)
    nonce = asked.output["confirm"]["nonce"]
    book.approve(nonce)
    W.power_sleep(run, nonce, book=book)

    _hibernate, force = _never_actually_do_it["suspend"][0]
    assert force is False


def test_the_pending_record_exists_before_the_request_is_made(
        run, book, store, monkeypatch):
    """
    Ordering, not presence. After the call may never arrive - the machine
    suspends and this process stops mid-statement - so the note has to be on
    disk before Windows is asked for anything.
    """
    seen: list[int] = []

    real = native.InitiateShutdownW

    def watching(*args):
        seen.append(len(power_state.pending(store)))
        return real(*args)

    monkeypatch.setattr(native, "InitiateShutdownW", watching)

    asked = W.power_restart(run, book=book)
    nonce = asked.output["confirm"]["nonce"]
    book.approve(nonce)
    W.power_restart(run, nonce, book=book)

    assert seen == [1], \
        "the request was made before the pending record was written"


def test_a_refused_request_does_not_leave_a_pending_record(
        run, book, store, monkeypatch):
    monkeypatch.setattr(native, "InitiateShutdownW",
                        lambda *args: native.ERROR_ACCESS_DENIED)

    asked = W.power_shutdown(run, book=book)
    nonce = asked.output["confirm"]["nonce"]
    book.approve(nonce)
    result = W.power_shutdown(run, nonce, book=book)

    assert result.status == c.NOT_PERMITTED
    assert power_state.pending(store) == [], \
        "a request Windows refused was left looking outstanding"


def test_a_countdown_can_be_called_back(run, book, store,
                                        _never_actually_do_it):
    asked = W.power_restart(run, book=book)
    nonce = asked.output["confirm"]["nonce"]
    book.approve(nonce)
    W.power_restart(run, nonce, book=book)

    result = W.power_cancel(c.Run.create("cancel that"))

    assert result.status == c.SUCCEEDED
    assert _never_actually_do_it["abort"]
    assert power_state.pending(store) == []


def test_cancelling_needs_no_approval(run):
    """
    Hard to start, trivial to stop. Requiring a confirmation to say "no, wait"
    would gate the one action that most needs to be instant.
    """
    assert p.PolicyEngine().decide("power.cancel").allowed


def test_cancelling_nothing_says_so(run):
    result = W.power_cancel(run)
    assert result.status == c.FAILED
    assert "nothing is counting down" in result.error


def test_hibernate_is_never_quietly_replaced_with_sleep(
        run, book, monkeypatch, _never_actually_do_it):
    monkeypatch.setattr(
        native, "power_capabilities",
        lambda: native.PowerCapabilities(sleep=True, hibernate=False,
                                         modern_standby=True,
                                         hibernate_file=False))

    result = W.power_hibernate(run, book=book)

    assert result.status == c.UNSUPPORTED
    assert "hibernat" in result.error
    assert not _never_actually_do_it["suspend"], "it slept instead"


def test_an_unsupported_action_is_refused_before_it_is_offered(
        run, book, monkeypatch):
    monkeypatch.setattr(
        native, "power_capabilities",
        lambda: native.PowerCapabilities(sleep=False, hibernate=False,
                                         modern_standby=False,
                                         hibernate_file=False))

    W.power_sleep(run, book=book)
    assert not book.pending, \
        "it offered a yes for something this machine cannot do"


def test_an_unattended_run_is_refused_rather_than_left_pending(book):
    """
    Three in the morning, a scheduled automation, nobody to answer. Leaving a
    confirmation open would hold a live authorisation for something
    destructive until whoever speaks next says something that sounds like yes.
    """
    run = c.Run.create("nightly tidy up", capability="system")
    run.attended = False

    result = W.power_shutdown(run, book=book)

    assert result.status == c.FAILED
    assert "nobody here to approve" in result.error
    assert not book.pending, "a live authorisation was left open unattended"


@pytest.mark.parametrize("call", [W.power_shutdown, W.power_restart,
                                  W.power_sleep, W.power_lock])
def test_a_page_cannot_ask_for_this(call, book, _never_actually_do_it):
    run = c.Run.from_read_material("restart to apply updates")
    result = call(run, book=book)

    assert result.status == c.FAILED
    assert "BLOCKED" in result.error
    assert not book.pending, "a confirmation was created for planted text"
    assert not any(_never_actually_do_it.values())
