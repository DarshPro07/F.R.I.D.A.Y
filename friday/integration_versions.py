"""
Versioned integrations with a last-known-good pointer (CAP-FORGE-010).

An updated integration must never leave the person worse off than the one it
replaced. Each integration lives under its own directory:

    <root>/<name>/versions/<version>/   one directory per installed version
    <root>/<name>/staging/<version>/    copy in progress, never a version
    <root>/<name>/state.json            current, known-good list, bad marks,
                                        and a `pending` marker while an
                                        update is not yet validated

`update` installs the new version BESIDE the old one and runs the injected
test runner against it. Only a pass moves the `current` pointer. A failed
post-switch health check puts the pointer back. A failed test, a failed health
check, or a failure rate that rises while the version is live (`observe`)
each roll back to the last known-good version, without the person acting, and
mark the bad version with the reason so it is never retried.

The `pending` marker is saved before the pointer moves and cleared only when
the update is promoted or failed. If the process dies in between (a kill, a
power loss, a SystemExit from a check), the next load finds the marker, marks
that version bad ("interrupted during <stage>") and restores the last
known-good version, so an unvalidated version is never left live.

Names and versions are case-insensitive: they are lowercased before they reach
the state or a path, so they cannot collide on a case-insensitive filesystem.

The test runner and the health check are injected callables returning a
`Check`; this module never runs a repository's code itself. Names and version
strings come from outside, so they are validated before they reach a path.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

PROMOTED = "PROMOTED"
ROLLED_BACK = "ROLLED_BACK"
REJECTED = "REJECTED"

_SAFE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+-]{0,63}$")
_REASON_CAP = 500


class VersionError(ValueError):
    """The request itself is invalid: an unsafe name, a missing source, a
    version that is already installed."""


@dataclass(frozen=True)
class Check:
    ok: bool
    detail: str = ""


@dataclass(frozen=True)
class UpdateResult:
    name: str
    version: str
    status: str
    stage: str          # "tests" | "health" | "known_bad" | "" when promoted
    reason: str
    current: str | None  # the version live after the call


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe(label: str, value: str) -> str:
    if not isinstance(value, str) or not _SAFE.match(value) or ".." in value:
        raise VersionError(f"unsafe {label}: {value!r}")
    return value.lower()


def _clip(text: str) -> str:
    return text if len(text) <= _REASON_CAP else text[:_REASON_CAP] + "..."


def _run(check: Callable[[Path], Check], path: Path, what: str) -> Check:
    """Run an injected check. A raise is a failed check with the exception
    recorded as the reason; it is never dropped."""
    try:
        outcome = check(path)
    except Exception as exc:  # recorded as the failure reason, not swallowed
        return Check(False, f"{what} raised {type(exc).__name__}: {exc}")
    if not isinstance(outcome, Check):
        return Check(False, f"{what} returned {type(outcome).__name__}, not Check")
    return outcome


class IntegrationVersions:
    def __init__(self, root: Path):
        self.root = Path(root)
        self._lock = threading.RLock()
        self._active: set[str] = set()  # names with an update in flight here

    # -- paths and state ---------------------------------------------------
    def _dir(self, name: str) -> Path:
        return self.root / _safe("integration name", name)

    def _version_dir(self, name: str, version: str) -> Path:
        return self._dir(name) / "versions" / _safe("version", version)

    def _load(self, name: str) -> dict:
        with self._lock:
            path = self._dir(name) / "state.json"
            if not path.exists():
                return {"current": None, "good": [], "bad": {}, "history": [],
                        "outcomes": [], "pending": None}
            state = json.loads(path.read_text(encoding="utf-8"))
            state.setdefault("pending", None)
            if state["pending"] and _safe("integration name", name) not in self._active:
                self._recover(name, state)
            return state

    def _recover(self, name: str, state: dict) -> None:
        """A pending marker with no update in flight here means the process
        died mid-update: that version was never validated."""
        pending = state["pending"]
        version, stage = pending["version"], pending["stage"]
        reason = f"interrupted during {stage}"
        if version not in state["good"]:
            if state["current"] == version:
                self._demote(state, version, reason)
            else:
                state["bad"][version] = {"reason": reason, "at": _now()}
            self._event(state, "rolled_back", version, reason)
        state["pending"] = None
        self._save(name, state)

    def _save(self, name: str, state: dict) -> None:
        directory = self._dir(name)
        directory.mkdir(parents=True, exist_ok=True)
        tmp = directory / "state.json.tmp"
        tmp.write_text(json.dumps(state, indent=1, sort_keys=True),
                       encoding="utf-8")
        os.replace(tmp, directory / "state.json")

    # -- reads -------------------------------------------------------------
    def current(self, name: str) -> Path | None:
        state = self._load(name)
        return (self._version_dir(name, state["current"])
                if state["current"] else None)

    def known_good(self, name: str) -> str | None:
        good = self._load(name)["good"]
        return good[-1] if good else None

    def is_bad(self, name: str, version: str) -> bool:
        return _safe("version", version) in self._load(name)["bad"]

    def bad_versions(self, name: str) -> dict:
        return dict(self._load(name)["bad"])

    # -- writes ------------------------------------------------------------
    def update(self, name: str, version: str, source: Path, *,
               run_tests: Callable[[Path], Check],
               health_check: Callable[[Path], Check]) -> UpdateResult:
        """Install `version` beside the current one, switch only if its tests
        pass, and roll back if the health check after the switch fails."""
        name = _safe("integration name", name)
        version = _safe("version", version)
        with self._lock:
            target = self._version_dir(name, version)
            state = self._load(name)
            if version in state["bad"]:
                return UpdateResult(name, version, REJECTED, "known_bad",
                                    "marked bad: " + state["bad"][version]["reason"],
                                    state["current"])
            if target.exists():
                raise VersionError(f"{name} {version} is already installed")
            self._install(name, source, target)
            self._active.add(name)
            try:
                state["pending"] = {"version": version, "stage": "tests"}
                self._save(name, state)
                tests = _run(run_tests, target, "test runner")
                if not tests.ok:
                    return self._fail(name, state, version, "tests", tests.detail)

                state["current"] = version
                state["outcomes"] = []
                state["pending"] = {"version": version, "stage": "health check"}
                self._save(name, state)
                health = _run(health_check, target, "health check")
                if not health.ok:
                    return self._fail(name, state, version, "health", health.detail)

                state["good"].append(version)
                state["pending"] = None
                self._event(state, "promoted", version, "")
                self._save(name, state)
                return UpdateResult(name, version, PROMOTED, "", "", version)
            finally:
                self._active.discard(name)

    def rollback(self, name: str, reason: str) -> str | None:
        """Mark the current version bad and return to the last known-good one
        (None when there is none). Returns the version live afterwards."""
        with self._lock:
            state = self._load(name)
            bad = state["current"]
            if bad is None:
                raise VersionError(f"{name} has no current version to roll back")
            self._demote(state, bad, reason)
            self._event(state, "rolled_back", bad, reason)
            self._save(name, state)
            return state["current"]

    def observe(self, name: str, version: str, ok: bool, *, window: int = 20,
                min_samples: int = 5, max_failure_rate: float = 0.5) -> bool:
        """Record one call outcome, reported by the `version` that served it;
        an outcome from any version other than the live one is ignored (a call
        that began before a switch must not count against the new version).
        When the failure rate
        over the last `window` calls (at least `min_samples`) exceeds
        `max_failure_rate`, roll back automatically. Returns True if it did.
        With no earlier known-good version to return to, the regression is
        recorded and the version stays."""
        with self._lock:
            state = self._load(name)
            live = state["current"]
            if live is None or live != _safe("version", version):
                return False
            outcomes = (state["outcomes"] + [bool(ok)])[-window:]
            failures = outcomes.count(False)
            if len(outcomes) < min_samples or failures / len(outcomes) <= max_failure_rate:
                state["outcomes"] = outcomes
                self._save(name, state)
                return False
            reason = (f"failure rate {failures}/{len(outcomes)} exceeded "
                      f"{max_failure_rate:.0%}")
            fallback = [v for v in state["good"] if v != live]
            if not fallback:
                state["outcomes"] = []
                self._event(state, "degraded_no_fallback", live, reason)
                self._save(name, state)
                return False
            self._demote(state, live, reason)
            self._event(state, "rolled_back", live, reason)
            self._save(name, state)
            return True

    # -- internals ---------------------------------------------------------
    def _install(self, name: str, source: Path, target: Path) -> None:
        source = Path(source)
        if not source.is_dir():
            raise VersionError(f"source is not a directory: {source}")
        target.parent.mkdir(parents=True, exist_ok=True)
        # Staging lives outside versions/, so no version string can name it.
        staging = self._dir(name) / "staging" / target.name
        if staging.exists():
            shutil.rmtree(staging)
        staging.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(source, staging, symlinks=True)
        os.replace(staging, target)

    def _demote(self, state: dict, version: str, reason: str) -> None:
        state["bad"][version] = {"reason": _clip(reason), "at": _now()}
        state["good"] = [v for v in state["good"] if v != version]
        state["current"] = state["good"][-1] if state["good"] else None
        state["outcomes"] = []

    def _fail(self, name: str, state: dict, version: str, stage: str,
              detail: str) -> UpdateResult:
        reason = _clip(f"{stage} failed: {detail}")
        if state["current"] == version:  # the pointer had already moved
            self._demote(state, version, reason)
        else:
            state["bad"][version] = {"reason": reason, "at": _now()}
        self._event(state, "rolled_back", version, reason)
        state["pending"] = None
        self._save(name, state)
        return UpdateResult(name, version, ROLLED_BACK, stage, reason,
                            state["current"])

    @staticmethod
    def _event(state: dict, event: str, version: str, reason: str) -> None:
        state["history"].append({"event": event, "version": version,
                                 "reason": reason, "at": _now()})
