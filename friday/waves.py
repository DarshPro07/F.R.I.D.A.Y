"""
ORCH-001: which independent work may start together.

The pure scheduling rule, shared by the objective executor
(`continuous.py`, tasks) and the build kernel (`scripts/build_kernel.py`,
ledger rows) so there is one implementation of it. It knows nothing about
either: an item is whatever the caller passes, and its surface is the set of
paths it will touch, taken from `surface_of`.

The rule:
- an item with a declared surface may start beside others only when that
  surface is disjoint from everything running and everything chosen with it;
- an item with no declared surface is unknown, and unknown runs alone - it
  starts only when nothing runs, and nothing starts beside it;
- a directory (a path ending in "/") overlaps every path under it.
"""

from __future__ import annotations

import posixpath
import re
from collections.abc import Callable, Iterable, Sequence
from typing import TypeVar

T = TypeVar("T")

_LINE_RANGE = re.compile(r":\d+(?:[-,:]\d+)*$")


def normalize_path(path: str) -> str:
    """`friday\\a.py:10-20` -> `friday/a.py`; a trailing "/" (a directory) is kept."""
    text = str(path).strip().replace(chr(92), "/")
    text = _LINE_RANGE.sub("", text)
    if not text:
        return ""
    is_dir = text.endswith("/")
    text = posixpath.normpath(text)
    return text + "/" if is_dir and text != "/" else text


def surface_of(paths: Iterable[str] | None) -> frozenset[str]:
    """The normalised, non-empty paths; an empty result means unknown."""
    if not paths or isinstance(paths, (str, bytes)):
        return frozenset()
    return frozenset(p for p in (normalize_path(one) for one in paths) if p)


def overlaps(a: Iterable[str], b: Iterable[str]) -> bool:
    """True when any path in `a` is, or lies under or over, a path in `b`.

    Compared case-insensitively: a predicted conflict only serialises work,
    while a missed one lets two writers share a file."""
    left = [p.casefold() for p in a]
    right = [p.casefold() for p in b]
    for x in left:
        for y in right:
            # A path may name a directory with or without its trailing "/":
            # a conflict missed here lets two writers share a tree.
            x_dir, y_dir = x.rstrip("/") + "/", y.rstrip("/") + "/"
            if x == y or x.startswith(y_dir) or y.startswith(x_dir):
                return True
    return False


def pick_starts(candidates: Sequence[T], surface: Callable[[T], frozenset[str]],
                *, running: Iterable[frozenset[str]] = (),
                room: int = 1) -> list[T]:
    """The candidates that may start now, in order, at most `room` of them.

    `running` holds the surface of everything already running; an empty
    surface in it is an unknown task and blocks every start, as an unknown
    candidate does anything beside it."""
    running = list(running)
    if room <= 0 or any(not s for s in running):
        return []
    used: list[str] = [p for s in running for p in s]
    chosen: list[T] = []
    for item in candidates:
        if len(chosen) >= room:
            break
        s = surface(item)
        if not s:
            if not running and not chosen:
                return [item]
            continue
        if overlaps(s, used):
            continue
        chosen.append(item)
        used.extend(s)
    return chosen


def pack_waves(items: Sequence[T], surface: Callable[[T], frozenset[str]],
               cap: int = 2) -> list[list[T]]:
    """Greedy, in the given order: the head of what is left starts a wave and
    later items join it while their surfaces stay disjoint. Unknown runs alone."""
    cap = max(1, cap)
    pending = list(items)
    out: list[list[T]] = []
    while pending:
        wave = pick_starts(pending, surface, room=cap)
        out.append(wave)
        # By identity: two rows can compare equal and still be two rows.
        pending = [p for p in pending if not any(p is w for w in wave)]
    return out
