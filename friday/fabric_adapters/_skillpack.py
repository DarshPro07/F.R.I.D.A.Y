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


def pack_root(upstream: str) -> pathlib.Path:
    return UPSTREAM / upstream


def health(upstream: str, *entries: str) -> dict:
    """READY when the pack is on disk and the files it promises exist."""
    from friday import fabric

    root = pack_root(upstream)
    if not root.is_dir():
        return {"state": fabric.UNAVAILABLE,
                "detail": f"not cloned: {root}"}
    missing = [entry for entry in entries if not (root / entry).exists()]
    if missing:
        return {"state": fabric.DEGRADED,
                "detail": f"cloned, but missing {missing}"}
    return {"state": fabric.READY, "detail": f"{len(entries)} entry file(s) present"}


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
