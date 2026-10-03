"""The requirement ledger's own honesty rules.

`scripts/prd_ledger.py` is what says how much of a PRD is done, so the ways
it could read green without being green matter more than the ways it could
crash. PRD v7 section 0 and CORE-002: the ledger maps every requirement to
files, tests and state that exist, and a claim of VERIFIED has to carry its
proof.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import prd_ledger as PL  # noqa: E402

THIS_TEST = "tests/test_prd_ledger.py::test_a_verified_v7_row_needs_evidence_and_a_test"


def _v7_row(**fields):
    base = dict(acceptance="a thing is shown", status=PL.PARTIAL,
                next_action="do the next thing")
    base.update(fields)
    return PL.R7("TEST-001", "0", "a requirement", PL.P0, **base)


def _matrix() -> dict[str, str]:
    """id -> priority, read from the PRD's own requirement tables: section 91,
    and the addenda of Amendments A1 to A7 in sections 95.3 to 101.2 (ids such
    as S1-001 carry a digit in their prefix; TEST-ISOLATION-001 a hyphen)."""
    text = (ROOT / "docs" / "PRD_V7.md").read_text(encoding="utf-8")
    rows = re.findall(r"^\| ([A-Z][A-Z0-9]*(?:-[A-Z][A-Z0-9]*)*-\d{3}) \| (P\d) \|", text, flags=re.M)
    return dict(rows)


def test_the_v7_set_covers_exactly_the_prd_matrix():
    """Every section-91 and 95.3 to 103.2 id has one row, in the PRD's
    order, with the PRD's priority, and the ledger holds nothing the PRD
    does not name."""
    matrix = _matrix()
    rows = PL._load_v7()
    assert len(matrix) == 69 + 37 + 14 + 4 + 6 + 2 + 9 + 14 + 12 + 10 + 1  # A8 Capability Forge, A9 Workflow Fabric, A10 scoped commands
    assert [r["id"] for r in rows] == list(matrix)
    assert {r["id"]: r["priority"] for r in rows} == matrix


def test_a_crosswalk_link_to_a_missing_v1_row_is_a_problem():
    """A v7 row may reuse v1 evidence only by naming a v1 row that exists;
    a typo would otherwise borrow evidence from nowhere."""
    v1_ids = {r["id"] for r in PL.REQUIREMENTS}
    real = next(iter(sorted(v1_ids)))
    assert not [p for p in PL.check([_v7_row(maps_to_v1=[real])], v1_ids)
                if "maps_to_v1" in p]
    problems = PL.check([_v7_row(maps_to_v1=["FR-NOPE-999"])], v1_ids)
    assert any("FR-NOPE-999" in p for p in problems)


def test_a_verified_v7_row_needs_evidence_and_a_test():
    """Section 0.4: code existing is not evidence. Remove either half and the
    row is refused; with both it passes."""
    ok = _v7_row(status=PL.VERIFIED, evidence="ran green", tests=[THIS_TEST],
                 next_action="")
    assert PL.check([ok], set()) == []
    no_test = _v7_row(status=PL.VERIFIED, evidence="ran green", next_action="")
    assert any("names no test" in p for p in PL.check([no_test], set()))
    only_a_file = _v7_row(status=PL.VERIFIED, evidence="ran green",
                          tests=["tests/test_prd_ledger.py"], next_action="")
    assert any("names no test" in p for p in PL.check([only_a_file], set()))
    no_evidence = _v7_row(status=PL.VERIFIED, tests=[THIS_TEST], next_action="")
    assert any("without evidence" in p for p in PL.check([no_evidence], set()))


def test_a_citation_that_does_not_resolve_on_the_tree_is_a_problem(tmp_path):
    """A ledger that cites a missing file, a line past the end, a prose note
    or an undefined test does not map the tree, whatever its status says.
    Each broken citation is caught; the sound ones next to it are not."""
    (tmp_path / "friday").mkdir()
    (tmp_path / "friday" / "a.py").write_text("x = 1\ny = 2\nz = 3\n", encoding="utf-8")
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_a.py").write_text(
        '"""Quoted, not defined:\n\ndef test_in_docstring():\n    pass\n"""\n\n'
        "def helper():\n    pass\n\n"
        "def test_real():\n    pass\n\n"
        "async def test_async_real():\n    pass\n\n"
        "class TestThing:\n    def test_method(self):\n        pass\n",
        encoding="utf-8")
    sound = _v7_row(files=["friday/a.py:2", "friday/a.py:1-3", "friday", "friday/a.py"],
                    tests=["tests/test_a.py::test_real",
                           "tests/test_a.py::test_async_real[param]",
                           "tests/test_a.py::TestThing::test_method", "tests/test_a.py"])
    assert PL.check([sound], set(), root=tmp_path) == []

    broken = {
        "friday/a.py:9": "outside a 3-line file",
        "friday/a.py:0": "outside a 3-line file",
        "friday/a.py:-": "is not a path",
        "friday/missing.py:1": "does not exist",
        "friday/gone.py": "does not exist",
        "../outside.py": "leaves the tree",
        "friday/a.py:2 (read in full)": "is not a path",
    }
    for entry, expected in broken.items():
        problems = PL.check([_v7_row(files=[entry])], set(), root=tmp_path)
        assert any(expected in p for p in problems), (entry, problems)
    for entry, expected in {
            "tests/test_a.py::test_ghost": "not a collectable test",
            "tests/test_a.py::helper": "not a collectable test",
            "tests/test_a.py::test_in_docstring": "not a collectable test",
            "tests/test_a.py::TestThing::test_gone": "not a collectable test",
            "tests/test_nope.py::test_real": "does not exist",
            "../tests/test_a.py::test_real": "does not exist in the tree"}.items():
        problems = PL.check([_v7_row(tests=[entry])], set(), root=tmp_path)
        assert any(expected in p for p in problems), (entry, problems)


def test_the_v7_states_that_mean_work_remains_cannot_end_the_program():
    """BROKEN, DUPLICATED and SUPERSEDED all still need work: none may be a
    terminal state, each needs a next action, and a set holding one is not
    a PASS."""
    for state in (PL.BROKEN, PL.DUPLICATED, PL.SUPERSEDED):
        assert state in PL.WORKING and state not in PL.TERMINAL
        problems = PL.check([_v7_row(status=state, next_action="")], set())
        assert any("without a next action" in p for p in problems), state
        rows = [_v7_row(status=state),
                _v7_row(status=PL.VERIFIED, evidence="e", tests=[THIS_TEST])]
        rows[1]["id"] = "TEST-002"
        assert PL.verdict(rows, {"head": "h", "prd": {}, "generated": "g"}
                          )["prd_status"] != "PASS"


def test_main_does_not_write_a_set_whose_table_has_a_problem(monkeypatch):
    """The generated ledger says it refuses to exist while its table has a
    problem, so a bad table must leave the previous artifacts alone and exit
    non-zero, and a sound table must be written."""
    written = []
    monkeypatch.setattr(PL, "write_set", lambda name, *a, **k: written.append(name))
    # PROGRESS_V7 names rows of the real table; this synthetic table has none.
    monkeypatch.setattr(PL, "PROGRESS_V7", [])

    bad = _v7_row(status=PL.VERIFIED, next_action="")   # no evidence, no test
    monkeypatch.setattr(PL, "_load_v7", lambda: [bad])
    assert PL.main(["--set", "v7"]) == 1
    assert written == []

    sound = _v7_row(files=["scripts/prd_ledger.py"], tests=[THIS_TEST])
    monkeypatch.setattr(PL, "_load_v7", lambda: [sound])
    assert PL.main(["--set", "v7"]) == 0
    assert written == ["v7"]


def test_the_real_ledgers_are_clean():
    """The authored tables themselves pass every rule above against this
    tree, including each v7-to-v1 crosswalk link and each citation."""
    v1 = PL.apply_progress(PL.REQUIREMENTS)
    assert PL.check(v1) == []
    v7 = PL.apply_progress(PL._load_v7(), PL.PROGRESS_V7)
    assert PL.check(v7, {r["id"] for r in v1}) == []
