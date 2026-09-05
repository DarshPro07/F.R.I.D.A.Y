"""
Shared loading for the markdown-only upstreams. Underscored, so not a provider.

Three of the twenty-one ship no code at all - `no-ai-slop`, `i-have-adhd` and
`agency-agents` are folders of markdown that tell a model how to write, how to
present, and how to play a role. There is nothing to install, nothing to start
and nothing to sandbox; the entire integration is "read this file when, and
only when, the work actually calls for it".

That last clause is the whole engineering content. `agency-agents` alone is 348
files. Loading them eagerly would put a hundred thousand tokens of role
recipes in front of a model that was asked to open Spotify, which is the
token-economy failure the fabric exists to avoid. So nothing here touches the
disk at import; `call()` reads one file and returns its text.

Providers built on this declare `integration_mode=SKILL`: no upstream code is
executed, which is also why the licence question is simply attribution.
"""

from __future__ import annotations

import pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent.parent
UPSTREAM = ROOT / "third_party" / "upstream"

#: Nothing above this is worth putting in a prompt in one go. A recipe longer
#: than this is a document to consult, not context to carry.
MAX_CHARS = 40_000

#: Below this an entry file has a name and nothing else. Not zero: a file
#: holding only a title line or a stray newline is just as useless to a model
#: as an empty one, and reporting it READY is the presence-vs-function bug.
MIN_USEFUL_BYTES = 32


def pack_root(upstream: str) -> pathlib.Path:
    return UPSTREAM / upstream


def cloned(upstream: str) -> bool:
    """Whether the pack's source is actually here, not merely its directory.

    A checkout of this repository creates every `third_party/upstream/<name>`
    as an EMPTY directory - they are gitlinks (mode 160000) and no submodule
    machinery fills them - so `pack_root(x).is_dir()` answered "cloned" on a
    fresh CI runner and every pack test then ran against nothing (2026-09-05,
    first green-attempt run: 20 failures of the shape "catalogue is empty").
    A clone has files in it; a placeholder does not.
    """
    root = pack_root(upstream)
    if not root.is_dir():
        return False
    try:
        return any(root.iterdir())
    except OSError:
        return False


def health(upstream: str, *entries: str) -> dict:
    """READY only when an entry file exists AND has content worth reading.

    This used to return READY on `path.exists()`, which is a filesystem check
    being reported as a capability check: a pack whose entry file had been
    truncated to nothing was indistinguishable from a working one, and the UI
    showed a healthy fabric either way.

    An empty or near-empty entry is DEGRADED, not READY - DEGRADED already
    means "answering, but not fully; say so, use it", which is the honest
    answer for a pack that is present but has nothing to give.
    """
    from friday import fabric

    root = pack_root(upstream)
    if not cloned(upstream):
        return {"state": fabric.UNAVAILABLE,
                "detail": f"not cloned: {root}"}
    missing = [entry for entry in entries if not (root / entry).exists()]
    if missing:
        return {"state": fabric.DEGRADED,
                "detail": f"cloned, but missing {missing}"}
    # The smallest real operation this family has is reading an entry, so that
    # is what the probe does. Cheap: one stat per entry, no file is parsed.
    empty = []
    for entry in entries:
        try:
            if (root / entry).stat().st_size < MIN_USEFUL_BYTES:
                empty.append(entry)
        except OSError as exc:
            return {"state": fabric.DEGRADED,
                    "detail": f"{entry} is present but unreadable: {exc}"}
    if empty:
        return {"state": fabric.DEGRADED,
                "detail": f"present but effectively empty: {empty}"}
    return {"state": fabric.READY,
            "detail": f"{len(entries)} entry file(s) present and non-empty"}


def read(upstream: str, relative: str) -> str:
    """One file's text, size-capped. The only disk access in this family."""
    path = pack_root(upstream) / relative
    if not path.is_file():
        raise FileNotFoundError(f"{upstream}: no such skill file {relative}")
    text = path.read_text(encoding="utf-8", errors="replace")
    if len(text) > MAX_CHARS:
        return (text[:MAX_CHARS]
                + f"\n\n[truncated at {MAX_CHARS} chars of {len(text)}]")
    return text


def catalogue(upstream: str, subdir: str = "") -> list[str]:
    """
    Every recipe the pack offers, by relative path. Names only.

    This is what a router needs to choose one, and it is two orders of
    magnitude cheaper than the recipes themselves.
    """
    root = pack_root(upstream) / subdir if subdir else pack_root(upstream)
    if not root.is_dir():
        return []
    return sorted(
        str(p.relative_to(pack_root(upstream))).replace("\\", "/")
        for p in root.rglob("*.md")
        if ".git" not in p.parts and ".github" not in p.parts)
