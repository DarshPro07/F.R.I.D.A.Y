"""
CAP-FORGE-007: an integration is complete only when a representative journey
passes against a post-state somebody else observed.

An installed integration that answers its own health check has shown that it
starts, not that it does the job. A journey drives the capability through its
real call path (`call(operation, **args)`, the same entry point the router
uses) and then reads the resulting state back through an *observer* that is
not the capability: the file on disk, the row in the database, the calculator's
display. What the capability reports about itself is recorded, never trusted.

    PASSED      the observer read the expected post-state
    FAILED      the capability raised, or the observer read something else
    UNOBSERVED  nothing independent could be read; that is not a pass

Only PASSED completes an integration (`complete`). An install that succeeded
but ran no journey, or whose journey failed or went unobserved, stays
INCOMPLETE with the reason, so it is not admitted.

Each journey class carries what it needs: filesystem and state_store have a
built-in read-back (they own the thing being read); a desktop_driver has no
read-back Friday can own, so the caller must supply the observer (UI
automation, a screenshot reader) or the journey is UNOBSERVED.
"""

from __future__ import annotations

import sqlite3
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

PASSED = "PASSED"
FAILED = "FAILED"
UNOBSERVED = "UNOBSERVED"

COMPLETE = "COMPLETE"
INCOMPLETE = "INCOMPLETE"

#: What a built-in read-back returns when the state it looks for is not there.
#: That is an observation (the capability did nothing), unlike a custom
#: observer returning None, which means it could not see.
ABSENT = "<absent>"

_REPORT_CLIP = 200

Call = Callable[..., Any]
Observer = Callable[[Path], Any]


class JourneyError(ValueError):
    """The journey itself was set up wrongly (not a capability failure)."""


@dataclass(frozen=True)
class JourneyResult:
    capability_class: str
    state: str
    reason: str
    expected: str
    observed: str | None
    #: The capability's own claim, clipped. Recorded for the audit trail only.
    reported: str

    def as_dict(self) -> dict:
        return {"capability_class": self.capability_class, "state": self.state,
                "reason": self.reason, "expected": self.expected,
                "observed": self.observed, "reported": self.reported}


@dataclass(frozen=True)
class Journey:
    capability_class: str
    #: Drives the capability: (call, workdir, token) -> the last report.
    act: Callable[[Call, Path, str], Any]
    #: The post-state a correct run leaves, as a string.
    expected: Callable[[str], str]
    #: Built-in read-back, or None when the caller must bring the observer.
    observe: Observer | None


# ---------------------------------------------------------------------------
# filesystem: the capability writes a file
# ---------------------------------------------------------------------------

FILE_NAME = "journey_file.txt"


def _file_act(call: Call, workdir: Path, token: str) -> Any:
    return call("write", path=str(workdir / FILE_NAME), content=token)


def _file_observe(workdir: Path) -> str:
    path = workdir / FILE_NAME
    return path.read_text(encoding="utf-8") if path.is_file() else ABSENT


# ---------------------------------------------------------------------------
# state_store: the capability puts a row in a SQLite database
# ---------------------------------------------------------------------------

DB_NAME = "journey_store.sqlite3"
STORE_KEY = "journey"


def _store_act(call: Call, workdir: Path, token: str) -> Any:
    return call("put", db=str(workdir / DB_NAME), key=STORE_KEY, value=token)


def _store_observe(workdir: Path) -> str:
    path = workdir / DB_NAME
    if not path.is_file():
        return ABSENT
    # Read-only, through our own connection: the capability's report is not
    # consulted. A missing table is "no such state", not an error.
    con = sqlite3.connect(f"{path.as_uri()}?mode=ro", uri=True)
    try:
        row = con.execute("SELECT v FROM kv WHERE k = ?", (STORE_KEY,)).fetchone()
    except sqlite3.OperationalError as exc:
        if "no such table" in str(exc):
            return ABSENT
        raise
    finally:
        con.close()
    return ABSENT if row is None else str(row[0])


# ---------------------------------------------------------------------------
# desktop_driver: keys 4 and 2 are entered, the display must read 42
# ---------------------------------------------------------------------------

DESKTOP_KEYS = ("4", "2")
DESKTOP_DISPLAY = "42"


def _desktop_act(call: Call, workdir: Path, token: str) -> Any:
    report = None
    for key in DESKTOP_KEYS:
        report = call("key", key=key)
    return report


JOURNEYS: dict[str, Journey] = {
    "filesystem": Journey("filesystem", _file_act, lambda token: token,
                          _file_observe),
    "state_store": Journey("state_store", _store_act, lambda token: token,
                           _store_observe),
    "desktop_driver": Journey("desktop_driver", _desktop_act,
                              lambda token: DESKTOP_DISPLAY, None),
}


# ---------------------------------------------------------------------------
# Running a journey
# ---------------------------------------------------------------------------

def _clip_text(text: str) -> str:
    """Bound untrusted text before it enters a status dict."""
    return text if len(text) <= _REPORT_CLIP else text[:_REPORT_CLIP] + "..."


def _clip(value: Any) -> str:
    return _clip_text(repr(value))


def run_journey(capability_class: str, call: Call, *, workdir: Path,
                observer: Observer | None = None) -> JourneyResult:
    """Run the class's journey through `call`, then read the post-state back.

    The state is read once before the capability acts and once after. A
    post-state that was already there beforehand proves nothing, so it is not
    a pass.

    Observer independence is the caller's responsibility. The only guard here
    is refusing `observer == call`; a wrapper around the capability, or a
    constant observer, gets past it and would make the journey meaningless.
    """
    journey = JOURNEYS.get(capability_class)
    if journey is None:
        raise JourneyError(f"unknown capability class {capability_class!r}; "
                           f"known: {sorted(JOURNEYS)}")
    workdir = Path(workdir)
    if not workdir.is_dir():
        raise JourneyError(f"journey workdir {str(workdir)!r} is not a directory")
    if observer is not None and observer == call:
        raise JourneyError("the observer must be independent of the "
                           "capability's own call path")
    reader = observer or journey.observe
    token = uuid.uuid4().hex
    expected = journey.expected(token)

    def result(state: str, reason: str, observed: str | None = None,
               reported: Any = None) -> JourneyResult:
        return JourneyResult(capability_class, state, reason, expected,
                             observed, _clip(reported))

    if reader is None:
        return result(UNOBSERVED, f"no observer supplied for a "
                                  f"{capability_class} journey")
    try:
        before = reader(workdir)
    except Exception as exc:
        return result(UNOBSERVED, "observer raised before the journey: "
                      + _clip_text(f"{type(exc).__name__}: {exc}"))
    if before is not None and str(before) == expected:
        return result(UNOBSERVED, "the expected post-state was already there "
                                  "before the journey ran, so no change by the "
                                  "capability can be observed; reset the state "
                                  "and run again", _clip_text(str(before)))
    try:
        reported = journey.act(call, workdir, token)
    except Exception as exc:
        return result(FAILED, "capability raised "
                      + _clip_text(f"{type(exc).__name__}: {exc}"))
    try:
        seen = reader(workdir)
    except Exception as exc:
        return result(UNOBSERVED, "observer raised "
                      + _clip_text(f"{type(exc).__name__}: {exc}"),
                      reported=reported)
    if seen is None:
        return result(UNOBSERVED, "the observer could not read the post-state",
                      reported=reported)
    observed = _clip_text(str(seen))
    if str(seen) != expected:
        return result(FAILED, f"post-state is {observed!r}, expected "
                              f"{expected!r}; the capability's own report was "
                              f"not accepted in its place",
                      observed, reported)
    return result(PASSED, "post-state observed as expected", observed, reported)


# ---------------------------------------------------------------------------
# Completion
# ---------------------------------------------------------------------------

def integration_status(integration_id: str, *, install_ok: bool,
                       result: JourneyResult | None) -> dict:
    """COMPLETE only for a successful install with a PASSED journey."""
    status = {"integration": integration_id, "status": INCOMPLETE,
              "reason": "", "journey": result.as_dict() if result else None}
    if not install_ok:
        status["reason"] = "install did not succeed"
    elif result is None:
        status["reason"] = "no journey was run for this integration"
    elif result.state != PASSED:
        status["reason"] = result.reason
    else:
        status["status"] = COMPLETE
        status["reason"] = result.reason
    return status


def complete(integration_id: str, capability_class: str, call: Call, *,
             workdir: Path, observer: Observer | None = None,
             install_ok: bool = True) -> dict:
    """Run the journey (only for an install that succeeded) and judge it."""
    result = None
    if install_ok:
        result = run_journey(capability_class, call, workdir=workdir,
                             observer=observer)
    return integration_status(integration_id, install_ok=install_ok,
                              result=result)
