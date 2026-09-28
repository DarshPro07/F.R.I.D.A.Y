"""
SEC-005 (PRD v7 section 43), native file capabilities: a run's file access
stays inside its execution envelope's roots.

FRIDAY's own jail (`fsjail`) still decides first - the denylist, reparse
points, the configured roots - and the envelope can only narrow what it
allows: read roots for reading, write roots for anything that changes a
file. A read root is not a write root, a move needs write on both ends, and
a path that climbs out with `..` is judged by where it really lands. A run
with no envelope - a conversation - keeps the ordinary jail.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytest

from friday import contracts as c
from friday import policy as P
from friday.fsjail import FileJail
from friday.toolsets import documents as D
from friday.toolsets import files as F

ENGINE = P.PolicyEngine(autonomy=P.DANGEROUS)


@dataclass
class Area:
    readable: Path
    writable: Path
    elsewhere: Path


@pytest.fixture
def area(tmp_path):
    here = Area(tmp_path / "readable", tmp_path / "writable", tmp_path / "elsewhere")
    for folder in (here.readable, here.writable, here.elsewhere):
        folder.mkdir()
    (here.readable / "notes.txt").write_text("inside the envelope", encoding="utf-8")
    (here.elsewhere / "other.txt").write_text("outside the envelope", encoding="utf-8")
    (here.elsewhere / "other.docx").write_bytes(b"PK")   # a document by name
    F.reset_jail(FileJail(roots=(tmp_path,)))
    yield here
    F.reset_jail(None)


def _run(read=(), write=()) -> c.Run:
    envelope = c.Envelope(run_id="RUN-FILES", read_roots=tuple(map(str, read)),
                          write_roots=tuple(map(str, write)),
                          allowed_capabilities=(c.ANY_CAPABILITY,))
    return c.Run(run_id="RUN-FILES", request="files", envelope=envelope)


def test_a_read_inside_the_envelope_is_allowed(area):
    result = F.files_read(_run(read=(area.readable,)), str(area.readable / "notes.txt"),
                          engine=ENGINE)
    assert result.status == c.SUCCEEDED, result.error


def test_a_read_outside_the_envelope_is_refused(area):
    result = F.files_read(_run(read=(area.readable,)), str(area.elsewhere / "other.txt"),
                          engine=ENGINE)
    assert result.status == c.FAILED
    assert "outside this run's envelope" in result.error


def test_climbing_out_is_judged_by_where_the_path_lands(area):
    sneaky = area.readable / ".." / "elsewhere" / "other.txt"
    result = F.files_read(_run(read=(area.readable,)), str(sneaky), engine=ENGINE)
    assert result.status == c.FAILED
    assert "outside this run's envelope" in result.error


def test_a_read_root_is_not_a_write_root(area):
    target = area.readable / "new.txt"
    result = F.files_write(_run(read=(area.readable,)), str(target), "x", engine=ENGINE)
    assert result.status == c.FAILED
    assert "outside this run's envelope" in result.error
    assert not target.exists()


def test_a_write_inside_the_write_roots_is_allowed(area):
    target = area.writable / "new.txt"
    result = F.files_write(_run(write=(area.writable,)), str(target), "x", engine=ENGINE)
    assert result.status == c.SUCCEEDED, result.error
    assert target.read_text(encoding="utf-8") == "x"


def test_a_move_needs_write_on_both_ends(area):
    source = area.readable / "notes.txt"
    result = F.files_move(_run(read=(area.readable,), write=(area.writable,)),
                          str(source), str(area.writable / "moved.txt"), engine=ENGINE)
    assert result.status == c.FAILED
    assert source.exists(), "a read-only source was moved away"


def test_a_copy_needs_only_read_at_the_source(area):
    result = F.files_copy(_run(read=(area.readable,), write=(area.writable,)),
                          str(area.readable / "notes.txt"), str(area.writable / "copy.txt"),
                          engine=ENGINE)
    assert result.status == c.SUCCEEDED, result.error


def test_documents_are_held_to_the_envelope_too(area):
    result = D.documents_inspect(_run(read=(area.readable,)),
                                 str(area.elsewhere / "other.docx"), engine=ENGINE)
    assert result.status == c.FAILED
    assert "outside this run's envelope" in result.error


def test_a_run_without_an_envelope_keeps_the_ordinary_jail(area):
    result = F.files_read(c.Run.create("files"), str(area.elsewhere / "other.txt"),
                          engine=ENGINE)
    assert result.status == c.SUCCEEDED, result.error
