"""
The output router for FRIDAY's workers (PRD v7 ORCH-007, section 101).

Test, git and search output is the bulk of what a worker prints and the part a
model needs least of verbatim: it wants the counts and the failures. So a long
output of one of those kinds reaches a model as a brief inside a size cap, and
the full text stays in the artifact vault behind a `vault:sha256:` pointer the
model can ask for (`full_output`).

What is deliberately not done:
  * No second compressor. The test brief is `scripts/pytest_brief.digest` (the
    rules the build harness already uses), and the lossy step runs through the
    one `compression.CompressionCoordinator`, as a single SUMMARIZE transform.
    Text that already carries a pointer is never routed again, so two
    compressors do not stack here; stacking one needs a benchmark first.
  * Exact text is not touched. A patch, text nobody can classify, text a caller
    tags exact, and anything under the cap pass raw.
  * A vault that cannot write returns the raw output with the refusal recorded
    on `Routed.record` (the coordinator's fail-closed rule).
"""
from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass

from friday import compaction, compression
from scripts import pytest_brief

#: A brief is cut to this many characters; the pointer footer rides on top.
MAX_CHARS = 2000
#: Files or commits listed under a count.
MAX_LISTED = 10

_MARK = "full output: " + compression.POINTER
_STATUS = re.compile(r"^[ MADRCUT?!]{2} \S")
_LOG = re.compile(r"^[0-9a-f]{7,40} \S")
_STAT_LINE = re.compile(r"^\s*(\S.*?)\s+\|\s+(\d+)\b")
_STAT_SUMMARY = re.compile(r"^\s*\d+ files? changed")
_HIT = re.compile(r"^((?:[A-Za-z]:)?[^:]+):\d+:")
_PATCH = re.compile(r"^diff --git ", re.M)
_STATUS_NAMES = {"M": "modified", "A": "added", "D": "deleted", "R": "renamed",
                 "C": "copied", "U": "unmerged", "?": "untracked", "!": "ignored"}


@dataclass(frozen=True)
class Routed:
    """What reaches the model. `compressed` is False when `text` is the raw
    output; `recovery` is the pointer to the full output ("" when none was
    stored); `record` is the coordinator's record when a transform was tried."""

    text: str
    kind: str
    compressed: bool
    recovery: str
    source_chars: int
    record: compression.Transformation | None = None


def _lines(text: str) -> list[str]:
    return [ln for ln in text.splitlines() if ln.strip()]


def detect(text: str) -> str:
    """"pytest", "git", "search", or "" when the text is none of them."""
    lines = _lines(text)
    if not lines or _PATCH.search(text):
        return ""
    if any(pytest_brief._COUNTS.search(ln) for ln in lines[-3:]):
        return "pytest"
    if _STAT_SUMMARY.match(lines[-1]) or all(_STATUS.match(ln) for ln in lines) \
            or all(_LOG.match(ln) for ln in lines):
        return "git"
    hits = sum(1 for ln in lines if _HIT.match(ln))
    return "search" if len(lines) >= 3 and hits >= 0.9 * len(lines) else ""


def _git(text: str) -> list[str]:
    lines = _lines(text)
    if _STAT_SUMMARY.match(lines[-1]):
        files = [(int(m.group(2)), m.group(1)) for m in map(_STAT_LINE.match, lines[:-1]) if m]
        top = sorted(files, key=lambda f: -f[0])[:MAX_LISTED]
        out = [lines[-1].strip(), *("%s (%d)" % (p, n) for n, p in top)]
        if len(files) > len(top):
            out.append("... and %d more files" % (len(files) - len(top)))
        return out
    if all(_STATUS.match(ln) for ln in lines):
        counts = Counter(_STATUS_NAMES.get((ln[0] if ln[0] != " " else ln[1]), "other") for ln in lines)
        out = [", ".join("%d %s" % (n, name) for name, n in counts.most_common())]
        out += [ln.strip() for ln in lines[:MAX_LISTED]]
        if len(lines) > MAX_LISTED:
            out.append("... and %d more paths" % (len(lines) - MAX_LISTED))
        return out
    out = ["%d commits" % len(lines), *lines[:MAX_LISTED]]
    if len(lines) > MAX_LISTED:
        out.append("... and %d more commits" % (len(lines) - MAX_LISTED))
    return out


def _search(text: str) -> list[str]:
    per_file = Counter(m.group(1) for m in map(_HIT.match, _lines(text)) if m)
    top = per_file.most_common(MAX_LISTED)
    out = ["%d matches in %d files" % (sum(per_file.values()), len(per_file)),
           *("%s (%d)" % (path, n) for path, n in top)]
    if len(per_file) > len(top):
        out.append("... and %d more files" % (len(per_file) - len(top)))
    return out


_BRIEFS = {"pytest": pytest_brief.digest, "git": _git, "search": _search}


def _fit(lines: list[str], cap: int) -> str:
    """The lines that fit in `cap` characters, whole, with a marker if any were cut."""
    kept, used = [], 0
    for ln in lines:
        if used + len(ln) + 1 > cap and kept:
            kept.append("... (brief cut at %d chars)" % cap)
            break
        kept.append(ln)
        used += len(ln) + 1
    return "\n".join(kept)


def _backend(kind: str, cap: int):
    def brief(text: str) -> str:
        pointer = compression.POINTER + compression.sha256(text)
        return "%s\n%s%s (%d chars)" % (_fit(_BRIEFS[kind](text), cap), "full output: ", pointer, len(text))
    return brief


def route(text: str, *, kind: str = "", cap: int = MAX_CHARS, tags=(),
          coordinator: compression.CompressionCoordinator | None = None) -> Routed:
    """`text` as it should reach a model. `kind` overrides detection."""
    raw = Routed(text, kind, False, "", len(text))
    if len(text) <= cap or _MARK in text:
        return raw
    kind = kind or detect(text)
    if kind not in _BRIEFS:
        return raw
    coordinator = coordinator or compression.default()
    name = "output_router." + kind
    coordinator.register(name, _backend(kind, cap))
    try:
        out, record = coordinator.transform(text, compression.SUMMARIZE, backend=name,
                                            tags=tags, hint=compression.SUMMARIZABLE)
    except compaction.LossyCompaction:
        return Routed(text, kind, False, "", len(text))
    if record.action != compression.SUMMARIZE:
        return Routed(text, kind, False, "", len(text), record)
    return Routed(out, kind, True, record.recovery, len(text), record)


def full_output(pointer: str, *,
                coordinator: compression.CompressionCoordinator | None = None) -> str:
    """The exact original behind a pointer `route` returned."""
    coordinator = coordinator or compression.default()
    return coordinator.recover(pointer).decode("utf-8", "surrogatepass")
