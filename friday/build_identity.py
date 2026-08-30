"""
What code is actually running, so a test can refuse to test the wrong build.

This exists because of an hour lost to a lie. `agent_friday.py dev` printed

    registered worker  {"id": "AW_m4i9JfMEo5iT", ...}

four times while the process id never changed, because LiveKit's dev watcher
re-registers the worker without re-importing modules already in `sys.modules`.
And separately, `server.py` had been running since 13:23 while the MCP tools it
was meant to serve were written at 23:57.

So a live test asked "what am I working on?", got "I don't have a record of
that", and it looked like a routing failure. The routing was fine. The
capability was registered, policy-gated, in `CORE_TOOLS` and correct. It simply
did not exist in the process being talked to.

    the runtime said        worker registered
    the runtime meant       worker registered, serving code from ten hours ago

An autonomous QA loop that cannot tell those apart will keep diagnosing code it
is not running.

## What identity means here

Not a version number - a build is what is *loaded in this process*:

    commit          what git says the tree is at
    dirty           whether the tree has uncommitted changes, because a
                    developer's working copy is the usual case and a commit
                    alone would claim more than it knows
    registry_hash   a hash over the capability ids actually importable now.
                    This is the one that catches the stale-server case: two
                    processes at the same commit disagree about it the moment
                    one of them has been running across an edit.
    started_at      when this process began, which is how "ten hours ago"
                    becomes visible

`matches()` is the point of the module. A test asks whether the thing it is
about to drive is the thing it just built, and gets a straight answer instead
of a plausible-looking log line.
"""

from __future__ import annotations

import hashlib
import os
import subprocess
import time
from dataclasses import dataclass

#: When this process started. Module import time is close enough and costs
#: nothing - the question is "hours or seconds", never milliseconds.
_STARTED = time.time()

_CACHED: "Build | None" = None


@dataclass(frozen=True)
class Build:
    """What one running process is made of."""

    commit: str = ""
    dirty: bool = False
    registry_hash: str = ""
    capabilities: int = 0
    started_at: float = 0.0
    pid: int = 0

    @property
    def age_seconds(self) -> float:
        return max(0.0, time.time() - self.started_at)

    def to_dict(self) -> dict:
        return {
            "commit": self.commit,
            "dirty": self.dirty,
            "registry_hash": self.registry_hash,
            "capabilities": self.capabilities,
            "pid": self.pid,
            "age_seconds": round(self.age_seconds, 1),
        }

    def matches(self, other: "Build") -> tuple[bool, str]:
        """
        (same build, why not). The question a test should ask before testing.

        `registry_hash` is checked before `commit` deliberately: a process
        that has been running across an edit reports the *new* commit - git
        reads the tree, not memory - while still serving the old capability
        set. The hash is computed from what is importable right now, so it is
        the half that actually catches a stale server.
        """
        if self.registry_hash != other.registry_hash:
            return False, (
                f"capability registry differs "
                f"({self.registry_hash} vs {other.registry_hash}) - one of "
                f"these processes has been running across an edit")
        if self.commit != other.commit:
            return False, f"different commit ({self.commit} vs {other.commit})"
        if self.dirty or other.dirty:
            return True, "same registry, but the tree is dirty"
        return True, "same build"


def _git(*arguments: str) -> str:
    try:
        return subprocess.run(
            ("git", *arguments), capture_output=True, text=True, timeout=5,
            cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        ).stdout.strip()
    except Exception:                                       # noqa: BLE001
        return ""


def registry_hash() -> str:
    """
    A hash over the capability ids importable in *this* process.

    Deliberately over ids rather than file contents: it answers "does this
    process know about the same abilities", which is the question that was
    silently wrong. A comment change should not invalidate a running server;
    a new capability must.
    """
    try:
        from friday import capabilities as C

        ids = ",".join(sorted(capability.id for capability in C._ALL))
    except Exception:                                       # noqa: BLE001
        return "unknown"
    return hashlib.sha256(ids.encode()).hexdigest()[:12]


def current(*, refresh: bool = False) -> Build:
    """What this process is running. Cached, because git is not free."""
    global _CACHED

    if _CACHED is not None and not refresh:
        return _CACHED

    try:
        from friday import capabilities as C

        count = len(C._ALL)
    except Exception:                                       # noqa: BLE001
        count = 0

    _CACHED = Build(
        commit=_git("rev-parse", "--short", "HEAD") or "unknown",
        dirty=bool(_git("status", "--porcelain")),
        registry_hash=registry_hash(),
        capabilities=count,
        started_at=_STARTED,
        pid=os.getpid(),
    )
    return _CACHED


def expected() -> Build:
    """
    What a freshly started process *would* be, computed now.

    The other half of the comparison: a test runs this in its own process,
    which by definition has just imported everything, and compares it against
    what the running server reports.
    """
    return current(refresh=True)


def describe() -> str:
    """One line for a log or a diagnostics tool."""
    build = current()
    age = build.age_seconds
    when = (f"{age:.0f}s" if age < 120 else
            f"{age / 60:.0f}m" if age < 7200 else f"{age / 3600:.1f}h")
    return (f"commit {build.commit}{'+dirty' if build.dirty else ''} "
            f"registry {build.registry_hash} "
            f"({build.capabilities} capabilities) "
            f"pid {build.pid} up {when}")
