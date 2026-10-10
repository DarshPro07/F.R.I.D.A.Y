"""
Post-state verification for computer-use actions (PRD v7 CUA-004).

A computer-use action is not a success because the backend said so. The
toolsets return what their backend reported; this decides whether that report
may stand. After a backend says an action succeeded, the application's state
is read again - through a backend read (`Observer`), not the action's own
report - and the action is a success only if that read shows the intended
change. A report of success with no change becomes a FAILED result (so it
counts against the path's reliability, `action_path`) and a `FailureRecord`
goes to the caller's recorder.

It builds on ACT-001 (`honesty`): the evidence it attaches is the
`post_state=<state>` observation the proof shapes ask for, so a verified
action passes `honesty.proof_gap`, and a failed one cannot be claimed done.

An `Observer` is `observe(kind, target) -> str | None` (or an awaitable of
that, for the async browser reads): the current value of the thing named, or
None when it is not there. What reads it is the caller's backend (the browser
session for a page, the native layer for a window, the file system for a
file); this module holds no backend. `confirm` takes a sync observer and
`aconfirm` an async or sync one, so neither blocks nor nests an event loop.

An expectation with `changed=True` is compared with a baseline read taken
BEFORE the action (`snapshot`/`asnapshot`): a download into a path that was
already there, or text typed into a field that already held it, is not a
change and so is not proof.

Browser primitives driven through `friday.browser.Browser` (click, type,
download, ...) are verified inside that loop (`Browser.verify`, which reads
the page before and after through the driver); this module covers the
actions that loop does not run - open, navigate, close and the desktop step.
"""
from __future__ import annotations

import inspect
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from urllib.parse import urlsplit

from friday import contracts as c

#: What an expectation reads.
PAGE = "page"        # target: url | title | text | session
WINDOW = "window"    # target: foreground
ELEMENT = "element"  # target: a selector or name; its value is read
FILE = "file"        # target: a path; the read is a size/mtime signature

Observer = Callable[[str, str], "str | None | Awaitable[str | None]"]


class _Unread:
    """A baseline read that raised: the pre-action state is not known."""

    def __repr__(self) -> str:
        return "<unread>"


UNREAD = _Unread()


@dataclass(frozen=True)
class Expectation:
    """One thing that must be true after the action."""

    kind: str
    target: str
    #: What the observed value must match; "" asks only for presence. A page
    #: url is matched as host + path (see `url_matches`), anything else as a
    #: case-insensitive substring.
    contains: str = ""
    #: False asks for the thing to be gone.
    present: bool = True
    #: True asks for the value to differ from the baseline read before the
    #: action, so that a state that was already there is not proof.
    changed: bool = False

    def describe(self) -> str:
        if not self.present:
            return f"{self.kind} {self.target!r} gone"
        want = f" matching {self.contains!r}" if self.contains else ""
        new = " (changed by the action)" if self.changed else ""
        return f"{self.kind} {self.target!r}{want}{new}"


@dataclass(frozen=True)
class Verdict:
    verified: bool
    expectation: Expectation
    observed: str | None
    reason: str = ""


@dataclass(frozen=True)
class FailureRecord:
    """A backend reported success and the state did not change."""

    run_id: str
    tool_id: str
    kind: str
    target: str
    expected: str
    observed: str | None
    reason: str
    backend_reported: object = None


# -- matching -----------------------------------------------------------------

def _split(url: str):
    return urlsplit(url if "//" in url else f"//{url}")


def _host(netloc: str) -> str:
    host = netloc.lower().rsplit("@", 1)[-1]
    return host.removeprefix("www.")


def url_matches(observed: str, wanted: str) -> bool:
    """Whether the page at `observed` is the one `wanted` names.

    The host must be the same (a leading `www.` aside) and so must the path,
    ignoring a trailing slash and the query and fragment: a navigation to
    `example.com/pricing` that landed on `/login?next=.../pricing` has not
    arrived, and `/a` is not `/abc`. A wanted address with no path (a bare
    host) accepts whatever path the site lands on, as sites redirect `/` to a
    language or home page.
    """
    got, want = _split(observed), _split(wanted)
    if _host(got.netloc) != _host(want.netloc):
        return False
    wanted_path = want.path.rstrip("/")
    return not wanted_path or got.path.rstrip("/").lower() == wanted_path.lower()


def _matches(expectation: Expectation, observed: str) -> bool:
    if not expectation.contains:
        return True
    if expectation.kind == PAGE and expectation.target == "url":
        return url_matches(observed, expectation.contains)
    return expectation.contains.lower() in observed.lower()


# -- reading ------------------------------------------------------------------

#: What one read produced: the value, or the error that stopped it.
Read = tuple["str | None", str]


def _failed_read(exc: BaseException) -> Read:
    return None, f"the post-state read failed: {type(exc).__name__}: {exc}"


def _read(observe: Observer, kind: str, target: str) -> Read:
    try:
        value = observe(kind, target)
        if inspect.isawaitable(value):
            if inspect.iscoroutine(value):
                value.close()
            return None, ("the observer is async; use aconfirm / asnapshot "
                          "for an async backend read")
        return value, ""
    except Exception as exc:  # the read failing is a result, not a crash
        return _failed_read(exc)


async def _aread(observe: Observer, kind: str, target: str) -> Read:
    try:
        value = observe(kind, target)
        if inspect.isawaitable(value):
            value = await value
        return value, ""
    except Exception as exc:
        return _failed_read(exc)


def snapshot(expectation: Expectation | None, observe: Observer) -> "str | None | _Unread":
    """The baseline read, taken before the action. Only an expectation that
    asks for a change needs one; a read that raises is `UNREAD`."""
    if expectation is None or not expectation.changed:
        return None
    value, error = _read(observe, expectation.kind, expectation.target)
    return UNREAD if error else value


async def asnapshot(expectation: Expectation | None,
                    observe: Observer) -> "str | None | _Unread":
    if expectation is None or not expectation.changed:
        return None
    value, error = await _aread(observe, expectation.kind, expectation.target)
    return UNREAD if error else value


# -- judging ------------------------------------------------------------------

def _judge(expectation: Expectation, read: Read, before) -> Verdict:
    observed, error = read
    if error:
        return Verdict(False, expectation, None, error)
    if not expectation.present:
        if observed is None:
            return Verdict(True, expectation, None)
        return Verdict(False, expectation, observed,
                       f"{expectation.describe()}, but it is still there")
    if observed is None:
        return Verdict(False, expectation, None,
                       f"{expectation.describe()}, but nothing was found")
    if not _matches(expectation, observed):
        return Verdict(False, expectation, observed,
                       f"{expectation.describe()}, but it reads {observed[:200]!r}")
    if expectation.changed:
        if before is UNREAD:
            return Verdict(False, expectation, observed,
                           f"{expectation.describe()}, but the state before the "
                           "action could not be read, so no change is shown")
        if before == observed:
            return Verdict(False, expectation, observed,
                           f"{expectation.describe()}, but it reads exactly as it "
                           "did before the action")
    return Verdict(True, expectation, observed)


def verify(expectation: Expectation, observe: Observer, *, before=None) -> Verdict:
    """Read the state once and compare it with `expectation` (and, for a
    `changed` one, with the `before` baseline). A read that raises leaves the
    action unverified, with the error as the reason: an action whose effect
    cannot be seen is not one that can be called done."""
    return _judge(expectation, _read(observe, expectation.kind, expectation.target),
                  before)


async def averify(expectation: Expectation, observe: Observer, *,
                  before=None) -> Verdict:
    return _judge(expectation,
                  await _aread(observe, expectation.kind, expectation.target), before)


def _settle(result: c.ActionResult, expectation: Expectation | None,
            verdict: Verdict | None, record, required: bool) -> c.ActionResult:
    if verdict is None:
        if not required:
            return result
        return c.partial(
            result, "the backend reported success but there is no independent "
                    "post-state check for this action",
            output={"backend_reported": result.output})
    if verdict.verified:
        seen = ("absent" if not expectation.present
                else (verdict.observed or "present"))
        return c.succeeded(
            result, output=result.output, artifacts=result.artifacts,
            side_effects=result.side_effects,
            verification=c.Verification(
                "post-state read through a backend, independent of the action's report",
                f"post_state=verified {expectation.kind}={expectation.target} "
                f"readback=ok observed={seen[:120]!r}"))
    if record is not None:
        record(FailureRecord(
            run_id=result.run_id, tool_id=result.tool_id, kind=expectation.kind,
            target=expectation.target, expected=expectation.describe(),
            observed=verdict.observed, reason=verdict.reason,
            backend_reported=result.output))
    return c.failed(
        result, f"backend reported success but {verdict.reason}",
        side_effects=result.side_effects).finish(
            output={"backend_reported": result.output,
                    "post_state": {"expected": expectation.describe(),
                                   "observed": verdict.observed}})


def confirm(result: c.ActionResult, expectation: Expectation | None,
            observe: Observer, *, before=None,
            record: Callable[[FailureRecord], None] | None = None,
            required: bool = False) -> c.ActionResult:
    """`result` as the independent post-state read supports it.

    Only a reported success is checked: any other status already says the
    action did not (fully) happen. With no `expectation`, an action that
    `required` one is downgraded to PARTIAL - nothing independent shows it
    changed anything - and any other passes through untouched. `before` is the
    `snapshot` taken before the action.
    """
    if result.status != c.SUCCEEDED:
        return result
    verdict = None if expectation is None else verify(expectation, observe, before=before)
    return _settle(result, expectation, verdict, record, required)


async def aconfirm(result: c.ActionResult, expectation: Expectation | None,
                   observe: Observer, *, before=None,
                   record: Callable[[FailureRecord], None] | None = None,
                   required: bool = False) -> c.ActionResult:
    """`confirm` for an async (or sync) observer."""
    if result.status != c.SUCCEEDED:
        return result
    verdict = (None if expectation is None
               else await averify(expectation, observe, before=before))
    return _settle(result, expectation, verdict, record, required)


# -- what each action is expected to change -----------------------------------

_OWN = frozenset({"browser.open", "browser.navigate", "browser.close", "desktop.step"})


def changes_state(tool_id: str, arguments: dict | None = None) -> bool:
    """Whether the action changes the application, so success needs proof.

    A `browser.<primitive>` id follows the owning table,
    `friday.browser.PRIMITIVES`; open, navigate and close are named here
    because they move or end the session."""
    if tool_id in _OWN:
        return True
    from friday import browser as B
    primitive = tool_id.removeprefix("browser.") if tool_id.startswith("browser.") else ""
    return bool(B.PRIMITIVES.get(primitive, ("", False))[1])


def expectation_for(tool_id: str, arguments: dict) -> Expectation | None:
    """What `tool_id` called with `arguments` must leave behind, or None when
    the call states no outcome that can be read back."""
    if tool_id in ("browser.open", "browser.navigate"):
        url = str(arguments.get("url", ""))
        return Expectation(PAGE, "url", url) if url else None
    if tool_id == "browser.close":
        return Expectation(PAGE, "session", present=False)
    if tool_id == "desktop.step":
        window = str(arguments.get("expect_window", ""))
        return Expectation(WINDOW, "foreground", window) if window else None
    return None


def verify_action(result: c.ActionResult, arguments: dict, observe: Observer, *,
                  before=None,
                  record: Callable[[FailureRecord], None] | None = None
                  ) -> c.ActionResult:
    """`confirm` with the expectation derived from the action's arguments."""
    return confirm(result, expectation_for(result.tool_id, arguments), observe,
                   before=before, record=record,
                   required=changes_state(result.tool_id, arguments))


async def averify_action(result: c.ActionResult, arguments: dict, observe: Observer,
                         *, before=None,
                         record: Callable[[FailureRecord], None] | None = None
                         ) -> c.ActionResult:
    return await aconfirm(result, expectation_for(result.tool_id, arguments), observe,
                          before=before, record=record,
                          required=changes_state(result.tool_id, arguments))
