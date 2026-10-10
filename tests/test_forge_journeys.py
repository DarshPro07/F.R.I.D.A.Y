"""
CAP-FORGE-007: an integration is complete only when a representative
capability journey passes against an independently observed post-state.

The fixture capabilities below have real side effects inside a tmp dir. The
"liars" report success and change nothing: only a read-back that does not go
through the capability can tell them apart from the honest ones.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from friday import forge_journeys as J


# ---------------------------------------------------------------------------
# Fixture capabilities (each one is the `call(operation, **args)` entry point)
# ---------------------------------------------------------------------------

def honest_files(operation, **args):
    assert operation == "write"
    Path(args["path"]).write_text(args["content"], encoding="utf-8")
    return {"ok": True}


def lying_files(operation, **args):
    return {"ok": True, "written": args["content"]}


def honest_store(operation, **args):
    assert operation == "put"
    with sqlite3.connect(args["db"]) as con:
        con.execute("CREATE TABLE IF NOT EXISTS kv (k TEXT PRIMARY KEY, v TEXT)")
        con.execute("INSERT OR REPLACE INTO kv VALUES (?, ?)",
                    (args["key"], args["value"]))
    return {"ok": True}


def lying_store(operation, **args):
    return {"ok": True, "stored": args["value"]}


class Calculator:
    """A fixture 'desktop app': its display is state outside the driver."""

    def __init__(self, workdir: Path, *, honest: bool = True) -> None:
        self.display = Path(workdir) / "calculator_display.txt"
        self.display.write_text("", encoding="utf-8")
        self.honest = honest

    def driver(self, operation, **args):
        assert operation == "key"
        if self.honest or args["key"] != "2":
            self.display.write_text(
                self.display.read_text(encoding="utf-8") + args["key"],
                encoding="utf-8")
        return {"ok": True, "display": "42"}

    def observer(self, workdir):
        return self.display.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# Journeys: pass, and fail when the post-state is not there
# ---------------------------------------------------------------------------

def test_file_journey_passes_when_the_file_is_really_written(tmp_path):
    result = J.run_journey("filesystem", honest_files, workdir=tmp_path)
    assert result.state == J.PASSED, result.reason


def test_file_journey_fails_when_the_capability_only_reports_success(tmp_path):
    result = J.run_journey("filesystem", lying_files, workdir=tmp_path)
    assert result.state == J.FAILED
    assert "post-state" in result.reason


def test_store_journey_passes_on_a_real_row(tmp_path):
    assert J.run_journey("state_store", honest_store,
                         workdir=tmp_path).state == J.PASSED


def test_store_journey_fails_when_no_row_exists(tmp_path):
    result = J.run_journey("state_store", lying_store, workdir=tmp_path)
    assert result.state == J.FAILED


def test_desktop_journey_reads_42_off_the_calculator_display(tmp_path):
    calc = Calculator(tmp_path)
    result = J.run_journey("desktop_driver", calc.driver, workdir=tmp_path,
                           observer=calc.observer)
    assert result.state == J.PASSED, result.reason
    assert result.observed == "42"


def test_desktop_journey_fails_when_the_display_disagrees_with_the_report(tmp_path):
    calc = Calculator(tmp_path, honest=False)
    result = J.run_journey("desktop_driver", calc.driver, workdir=tmp_path,
                           observer=calc.observer)
    assert result.state == J.FAILED
    assert result.observed == "4"


def test_desktop_journey_without_an_observer_is_unobserved(tmp_path):
    calc = Calculator(tmp_path)
    result = J.run_journey("desktop_driver", calc.driver, workdir=tmp_path)
    assert result.state == J.UNOBSERVED
    assert "observer" in result.reason


def test_an_observer_that_sees_nothing_is_unobserved_not_passed(tmp_path):
    calc = Calculator(tmp_path)
    result = J.run_journey("desktop_driver", calc.driver, workdir=tmp_path,
                           observer=lambda workdir: None)
    assert result.state == J.UNOBSERVED


def test_the_capability_cannot_observe_itself(tmp_path):
    calc = Calculator(tmp_path)
    with pytest.raises(J.JourneyError, match="independent"):
        J.run_journey("desktop_driver", calc.driver, workdir=tmp_path,
                      observer=calc.driver)


def test_a_raising_capability_fails_with_the_reason_recorded(tmp_path):
    def broken(operation, **args):
        raise RuntimeError("driver crashed")

    result = J.run_journey("filesystem", broken, workdir=tmp_path)
    assert result.state == J.FAILED
    assert "driver crashed" in result.reason


def test_an_unknown_capability_class_is_refused(tmp_path):
    with pytest.raises(J.JourneyError, match="unknown capability class"):
        J.run_journey("telepathy", honest_files, workdir=tmp_path)


def test_each_run_uses_a_fresh_token_so_stale_state_cannot_pass(tmp_path):
    first = J.run_journey("filesystem", honest_files, workdir=tmp_path)
    stale = {}

    def replay(operation, **args):
        stale.setdefault("calls", 0)
        stale["calls"] += 1
        return {"ok": True}  # writes nothing; the first run's file is still there

    second = J.run_journey("filesystem", replay, workdir=tmp_path)
    assert first.state == J.PASSED
    assert second.state == J.FAILED


# ---------------------------------------------------------------------------
# Completion: only a passing journey completes an integration
# ---------------------------------------------------------------------------

def test_a_passing_journey_completes_the_integration(tmp_path):
    status = J.complete("files-upstream", "filesystem", honest_files,
                        workdir=tmp_path)
    assert status["status"] == J.COMPLETE
    assert status["journey"]["state"] == J.PASSED


def test_a_successful_install_without_a_journey_stays_unadmitted():
    status = J.integration_status("files-upstream", install_ok=True, result=None)
    assert status["status"] == J.INCOMPLETE
    assert "no journey" in status["reason"]


def test_a_failing_journey_leaves_the_integration_incomplete_with_the_reason(tmp_path):
    status = J.complete("files-upstream", "filesystem", lying_files,
                        workdir=tmp_path)
    assert status["status"] == J.INCOMPLETE
    assert status["reason"] == status["journey"]["reason"]


def test_an_unobserved_journey_leaves_the_integration_incomplete(tmp_path):
    calc = Calculator(tmp_path)
    status = J.complete("calc-driver", "desktop_driver", calc.driver,
                        workdir=tmp_path)
    assert status["status"] == J.INCOMPLETE
    assert status["journey"]["state"] == J.UNOBSERVED


def test_a_failed_install_never_runs_the_journey(tmp_path):
    calls = []

    def capability(operation, **args):
        calls.append(operation)
        return {"ok": True}

    status = J.complete("files-upstream", "filesystem", capability,
                        workdir=tmp_path, install_ok=False)
    assert status["status"] == J.INCOMPLETE
    assert "install" in status["reason"]
    assert calls == []


# ---------------------------------------------------------------------------
# Review fixes: pre-state, bounded untrusted text, observer independence
# ---------------------------------------------------------------------------

def test_a_noop_driver_on_a_display_already_at_42_is_not_passed(tmp_path):
    calc = Calculator(tmp_path)
    calc.display.write_text("42", encoding="utf-8")  # stale state

    def noop(operation, **args):
        return {"ok": True}

    result = J.run_journey("desktop_driver", noop, workdir=tmp_path,
                           observer=calc.observer)
    assert result.state != J.PASSED
    assert "before the journey" in result.reason


def test_a_stale_42_display_leaves_the_integration_incomplete(tmp_path):
    calc = Calculator(tmp_path)
    calc.display.write_text("42", encoding="utf-8")
    status = J.complete("calc-driver", "desktop_driver",
                        lambda operation, **args: {"ok": True},
                        workdir=tmp_path, observer=calc.observer)
    assert status["status"] == J.INCOMPLETE
    assert "before the journey" in status["reason"]


def test_an_observer_that_cannot_read_the_pre_state_is_unobserved(tmp_path):
    def blind(workdir):
        raise OSError("no screen")

    calc = Calculator(tmp_path)
    result = J.run_journey("desktop_driver", calc.driver, workdir=tmp_path,
                           observer=blind)
    assert result.state == J.UNOBSERVED


def test_untrusted_exception_and_observed_text_is_bounded(tmp_path):
    def noisy(operation, **args):
        raise RuntimeError("x" * 5000)

    failed = J.run_journey("filesystem", noisy, workdir=tmp_path)
    assert len(failed.reason) < 400

    calc = Calculator(tmp_path)
    huge = J.run_journey("desktop_driver", calc.driver, workdir=tmp_path,
                         observer=lambda w: "y" * 5000)
    assert huge.state == J.FAILED
    assert len(huge.reason) < 600
    assert len(huge.observed) <= 210

    def bad_observer(workdir):
        raise RuntimeError("z" * 5000)

    unobs = J.run_journey("desktop_driver", calc.driver, workdir=tmp_path,
                          observer=bad_observer)
    assert len(unobs.reason) < 400


def test_the_observer_independence_caveat_is_documented():
    assert "caller's responsibility" in J.run_journey.__doc__
