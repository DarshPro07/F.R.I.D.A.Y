"""
The Project Brain (BRAIN-001): structured, human-inspectable project records.

Friday's project knowledge used to sit in separate stores - decisions and
requirements in store.py, research in research_cache.py, failures in
continuous.py, hand-offs in handoff.py - with nothing that read as one
account of a project. This module is the one owner of thirteen record kinds
and of what a valid body for each looks like. The rows live in the Store's
`project_brain_records` table (friday/store.py); `export_markdown` writes them
as plain Markdown under STATE_ROOT/brain/<project>/ so a person can read the
brain without a database client.

Rules this module holds:

  * A record that restates a row elsewhere (a decision, a requirement, an
    artifact) points at it through `ref` ("project_decisions:12"); the fact is
    not copied, so there is no second version of it to drift.
  * A change supersedes. The old row stays as history; only one row per
    (project, kind, key) is active.
  * Secrets never enter the brain (NON_NEGOTIABLE 4): a secret-shaped body is
    refused on write, and the export checks again, because a row can arrive
    from a writer that skipped this module.

Not wired to any MCP tool or voice path in this slice.

Named project_brain, not brain: friday/brain.py is the GBrain gateway.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from friday import config
from friday.brain import _sensitive
from friday.store import Store

# The order is the order the index lists them. The CHECK constraint on
# project_brain_records.kind in store.py states the same list; a test keeps the
# two equal.
KINDS: tuple[str, ...] = (
    "architecture", "requirements", "decisions", "assumptions", "research",
    "failures", "experiments", "objectives", "incidents", "artifacts",
    "handoffs", "procedures", "entities",
)

_TEXT, _LIST = "text", "list"

#: Required fields per kind. A body may carry more fields; these must be there.
#: "text" is a non-blank string, "list" a non-empty list of non-blank strings.
REQUIRED: dict[str, tuple[tuple[str, str], ...]] = {
    "architecture": (("title", _TEXT), ("summary", _TEXT)),
    "requirements": (("statement", _TEXT),),
    "decisions": (("decision", _TEXT),),
    "assumptions": (("statement", _TEXT),),
    "research": (("question", _TEXT), ("finding", _TEXT)),
    "failures": (("fingerprint", _TEXT), ("summary", _TEXT)),
    "experiments": (("hypothesis", _TEXT), ("outcome", _TEXT)),
    "objectives": (("goal", _TEXT),),
    "incidents": (("summary", _TEXT),),
    "artifacts": (("title", _TEXT),),
    "handoffs": (("summary", _TEXT), ("next_steps", _LIST)),
    "procedures": (("title", _TEXT), ("steps", _LIST)),
    "entities": (("name", _TEXT), ("entity_type", _TEXT)),
}

MAX_BODY_BYTES = 16 * 1024
MAX_PROJECT_CHARS = 200

_KEY = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,79}$")
_ORIGIN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,39}$")
_REF = re.compile(r"^[a-z_]{1,40}:[A-Za-z0-9._-]{1,64}$")

# A directory name taken as-is only when it is lowercase and cannot be a
# Windows device name; anything else is slugged and suffixed with a hash of the
# original so that two different projects never share a directory (including
# on a case-insensitive filesystem).
_SAFE_DIR = re.compile(r"^[a-z0-9](?:[a-z0-9._-]{0,62}[a-z0-9])?$")
_WINDOWS_DEVICES = frozenset(
    {"con", "prn", "aux", "nul"}
    | {f"com{n}" for n in range(1, 10)} | {f"lpt{n}" for n in range(1, 10)})


class BrainRecordError(ValueError):
    """A record was refused: unknown kind, malformed body, bad key or ref,
    secret-shaped content, a duplicate, or nothing to supersede."""


@dataclass(frozen=True)
class BrainRecord:
    id: int
    project: str
    kind: str
    key: str
    body: dict
    state: str                  # "active" | "superseded"
    origin: str                 # who wrote it, e.g. "person" or "friday"
    ref: str | None             # "table:id" of the row this restates, if any
    supersedes: int | None      # id of the record this one replaced
    created_at: str
    updated_at: str


def _record(row: dict) -> BrainRecord:
    try:
        body = json.loads(row["body"])
    except ValueError as exc:
        raise BrainRecordError(
            f"brain record {row['id']} has an unreadable body") from exc
    return BrainRecord(
        id=int(row["id"]), project=row["project"], kind=row["kind"],
        key=row["key"], body=body, state=row["state"], origin=row["origin"],
        ref=row["ref"], supersedes=row["supersedes"],
        created_at=row["created_at"], updated_at=row["updated_at"])


# -- validation -----------------------------------------------------------------

def _check_project(project: str) -> None:
    if (not isinstance(project, str) or not project.strip()
            or len(project) > MAX_PROJECT_CHARS
            or any(ord(c) < 32 or ord(c) == 127 for c in project)):
        raise BrainRecordError(
            f"project must be 1-{MAX_PROJECT_CHARS} printable characters")


def _check_kind(kind: str) -> None:
    if kind not in KINDS:
        raise BrainRecordError(
            f"unknown brain record kind {str(kind)[:40]!r}; one of {', '.join(KINDS)}")


def _check_shape(name: str, value: str, pattern: re.Pattern) -> None:
    if not isinstance(value, str) or not pattern.match(value):
        raise BrainRecordError(
            f"{name} {str(value)[:40]!r} must match {pattern.pattern}")


def _validate_body(kind: str, body: dict) -> str:
    """The body as canonical JSON, or BrainRecordError."""
    if not isinstance(body, dict):
        raise BrainRecordError(f"a {kind} body must be an object, not "
                               f"{type(body).__name__}")
    if not all(isinstance(k, str) for k in body):
        raise BrainRecordError("body field names must be strings")
    for field, shape in REQUIRED[kind]:
        value = body.get(field)
        if shape == _TEXT:
            ok = isinstance(value, str) and value.strip() != ""
        else:
            ok = (isinstance(value, list) and len(value) > 0 and all(
                isinstance(i, str) and i.strip() != "" for i in value))
        if not ok:
            want = ("a non-blank string" if shape == _TEXT
                    else "a non-empty list of non-blank strings")
            raise BrainRecordError(f"a {kind} record needs {field!r} as {want}")
    try:
        text = json.dumps(body, sort_keys=True, ensure_ascii=False, allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise BrainRecordError(f"body is not plain JSON: {exc}") from exc
    if len(text.encode("utf-8")) > MAX_BODY_BYTES:
        raise BrainRecordError(f"body exceeds {MAX_BODY_BYTES} bytes")
    reason = _sensitive(text)
    if reason:
        raise BrainRecordError(
            f"refused: {reason}; a secret or sensitive text never enters the brain")
    return text


# -- rendering ------------------------------------------------------------------

def _inline(value: object) -> str:
    """One safe Markdown line: whitespace collapsed so a value cannot open a
    heading or a bullet, raw HTML and link syntax neutralised."""
    text = " ".join(str(value).split())
    return (text.replace("<", "&lt;").replace(">", "&gt;")
            .replace("[", "\\[").replace("]", "\\]"))


def _clean(text: object) -> str:
    """`_inline`, or a placeholder if the text looks like a secret."""
    return _inline(text) if _sensitive(str(text)) is None else "[withheld]"


def _field_lines(name: str, value: object) -> list[str]:
    label = f"- **{_inline(name)}:**"
    if isinstance(value, list) and value:
        return [label] + [f"  - {_inline(i if isinstance(i, str) else json.dumps(i, ensure_ascii=False))}"
                          for i in value]
    if isinstance(value, str):
        return [f"{label} {_inline(value)}"]
    return [f"{label} {_inline(json.dumps(value, ensure_ascii=False, sort_keys=True))}"]


def _render_record(row: dict, *, level: str) -> list[str]:
    lines = [f"{level} {_clean(row['key'])}", "",
             f"- **id:** {row['id']}",
             f"- **origin:** {_clean(row['origin'])}"]
    if row["ref"]:
        lines.append(f"- **ref:** {_clean(row['ref'])}")
    if row["supersedes"]:
        lines.append(f"- **replaces:** record {row['supersedes']}")
    lines.append(f"- **updated:** {_inline(row['updated_at'])}")
    reason = _sensitive(str(row["body"]))
    if reason:
        lines.append(f"- **body:** withheld ({reason})")
        return lines + [""]
    try:
        body = json.loads(row["body"])
    except ValueError:
        return lines + ["- **body:** unreadable (not valid JSON)", ""]
    if not isinstance(body, dict):
        return lines + ["- **body:** unreadable (not an object)", ""]
    required = [f for f, _ in REQUIRED[row["kind"]]]
    for name in required + sorted(set(body) - set(required)):
        if name in body:
            lines.extend(_field_lines(name, body[name]))
    return lines + [""]


def _dir_name(project: str) -> str:
    if _SAFE_DIR.match(project) and project.split(".")[0] not in _WINDOWS_DEVICES:
        return project
    slug = re.sub(r"[^a-z0-9._-]+", "-", project.lower())[:40].strip(".-_") or "project"
    return f"{slug}~{hashlib.sha256(project.encode('utf-8')).hexdigest()[:10]}"


def _write(path: Path, text: str) -> None:
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)
    os.replace(tmp, path)


# -- the brain ------------------------------------------------------------------

class ProjectBrain:
    def __init__(self, store: Store, *, state_root: Path | str | None = None) -> None:
        self._store = store
        self._state_root = Path(state_root) if state_root is not None \
            else Path(config.STATE_ROOT)

    def add(self, project: str, kind: str, key: str, body: dict, *,
            origin: str, ref: str | None = None) -> BrainRecord:
        """Write a new active record. To change one that exists, `supersede`."""
        text = self._checked(project, kind, key, body, origin, ref)
        try:
            row_id = self._store.brain_insert(project, kind, key, text,
                                              origin=origin, ref=ref)
        except sqlite3.IntegrityError as exc:
            raise BrainRecordError(
                f"{kind} record {key!r} already exists in {project!r}; "
                "supersede it to change it") from exc
        return self._by_id(project, kind, key, row_id)

    def supersede(self, project: str, kind: str, key: str, body: dict, *,
                  origin: str, ref: str | None = None) -> BrainRecord:
        """Replace the active record with a new version; the old row stays."""
        text = self._checked(project, kind, key, body, origin, ref)
        try:
            row_id = self._store.brain_supersede(project, kind, key, text,
                                                 origin=origin, ref=ref)
        except LookupError as exc:
            raise BrainRecordError(str(exc)) from exc
        return self._by_id(project, kind, key, row_id)

    def get(self, project: str, kind: str, key: str) -> BrainRecord | None:
        """The active record for this key, or None."""
        rows = self._store.brain_rows(project, kind=kind, key=key)
        return _record(rows[0]) if rows else None

    def history(self, project: str, kind: str, key: str) -> "list[BrainRecord]":
        """Every version of a key, oldest first, the active one last."""
        return [_record(r) for r in self._store.brain_rows(
            project, kind=kind, key=key, include_superseded=True)]

    def list(self, project: str, kind: str | None = None, *,
             include_superseded: bool = False) -> "list[BrainRecord]":
        if kind is not None:
            _check_kind(kind)
        return [_record(r) for r in self._store.brain_rows(
            project, kind=kind, include_superseded=include_superseded)]

    def export_markdown(self, project: str) -> "dict[str, Path]":
        """Write STATE_ROOT/brain/<project>/index.md and one file per kind.

        Every kind gets a file, empty or not, so an emptied kind never leaves a
        stale one behind, and the same records always yield the same bytes.
        Returns the paths keyed by kind, plus "index".
        """
        _check_project(project)
        brain_root = (self._state_root / "brain").resolve()
        target = (brain_root / _dir_name(project)).resolve()
        if target.parent != brain_root:
            raise BrainRecordError("export directory escaped the brain root")
        target.mkdir(parents=True, exist_ok=True)

        rows = self._store.brain_rows(project, include_superseded=True)
        written: dict[str, Path] = {}
        counts: dict[str, tuple[int, int]] = {}
        for kind in KINDS:
            mine = [r for r in rows if r["kind"] == kind]
            active = [r for r in mine if r["state"] == "active"]
            history = [r for r in mine if r["state"] == "superseded"]
            counts[kind] = (len(active), len(history))
            lines = [f"# {kind.capitalize()}: {_clean(project)}", ""]
            if not mine:
                lines += ["_No records._", ""]
            for row in active:
                lines += _render_record(row, level="##")
            if history:
                lines += ["## Superseded (history)", ""]
                for row in history:
                    lines += _render_record(row, level="###")
            written[kind] = target / f"{kind}.md"
            _write(written[kind], "\n".join(lines))

        index = [f"# Project Brain: {_clean(project)}", "",
                 "| Kind | Active | Superseded | File |",
                 "| --- | --- | --- | --- |"]
        index += [f"| {kind} | {counts[kind][0]} | {counts[kind][1]} | "
                  f"[{kind}.md]({kind}.md) |" for kind in KINDS]
        written["index"] = target / "index.md"
        _write(written["index"], "\n".join(index + [""]))
        return written

    # -- internals ---------------------------------------------------------------

    def _checked(self, project, kind, key, body, origin, ref) -> str:
        _check_project(project)
        _check_kind(kind)
        _check_shape("key", key, _KEY)
        _check_shape("origin", origin, _ORIGIN)
        text = _validate_body(kind, body)
        if ref is not None:
            _check_shape("ref", ref, _REF)
            if not self._store.brain_ref_exists(ref):
                raise BrainRecordError(f"ref {ref!r} does not name an existing row")
        return text

    def _by_id(self, project: str, kind: str, key: str, row_id: int) -> BrainRecord:
        for row in self._store.brain_rows(project, kind=kind, key=key):
            if row["id"] == row_id:
                return _record(row)
        raise BrainRecordError(f"record {row_id} vanished after writing")
