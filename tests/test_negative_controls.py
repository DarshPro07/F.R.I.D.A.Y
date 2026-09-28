"""
VERIFY-002 (PRD §79): every critical guard has a negative control.

The registry is `tests/negative_controls.py`. These tests keep it honest:

- it names exactly §79's twelve controls, read from the PRD so the two
  cannot drift apart;
- every entry has a red-path test, or a v7 ledger row that owns building one;
- every named test is really collected by pytest, offline - the release gate
  runs without the network, so a `live` test cannot be a release red path;
- an owning row is real and still open, and VERIFY-002 itself is not
  VERIFIED while any control has no test. A VERIFIED row owning a missing
  control would be a lie in two places.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from negative_controls import CONTROLS

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import prd_ledger as PL  # noqa: E402


def _prd_controls() -> set[str]:
    text = (ROOT / "docs" / "PRD_V7.md").read_text(encoding="utf-8")
    line = next(row for row in text.splitlines()
                if row.startswith("Critical negative controls:"))
    body = line.split(":", 1)[1].strip().rstrip(".")
    return {part.strip() for part in body.split(";")}


def _v7_rows() -> dict[str, dict]:
    return {row["id"]: row
            for row in PL.apply_progress(PL._load_v7(), PL.PROGRESS_V7)}


def test_the_registry_names_exactly_the_prd_controls():
    prd = _prd_controls()
    assert len(prd) == 12, prd
    assert set(CONTROLS) == prd, {"missing": prd - set(CONTROLS),
                                  "not in the PRD": set(CONTROLS) - prd}


@pytest.mark.parametrize("name", sorted(CONTROLS))
def test_every_control_has_a_red_path_or_an_owner(name):
    control = CONTROLS[name]
    assert control.tests or control.open_row, f"{name!r}: no test and no owner"
    assert control.proves, f"{name!r} does not say what it proves"


@pytest.mark.parametrize("name", sorted(CONTROLS))
def test_a_host_skip_names_one_of_the_controls_tests(name):
    control = CONTROLS[name]
    stray = set(control.host_skips) - set(control.tests)
    assert not stray, f"{name!r} allows a skip for tests it does not list: {stray}"
    assert all(control.host_skips.values()), f"{name!r}: a host skip needs its reason"


def test_an_owning_row_is_real_and_still_open():
    rows = _v7_rows()
    for name, control in CONTROLS.items():
        if not control.open_row:
            continue
        assert control.open_row in rows, (
            f"{name!r} is owned by {control.open_row}, which is not a v7 row")
        assert rows[control.open_row]["status"] not in PL.TERMINAL, (
            f"{name!r} has no red path, yet its owner {control.open_row} is "
            f"{rows[control.open_row]['status']}")


def test_verify_002_is_not_verified_while_a_control_has_no_test():
    uncovered = sorted(name for name, c in CONTROLS.items() if not c.tests)
    if uncovered:
        assert _v7_rows()["VERIFY-002"]["status"] != PL.VERIFIED, (
            f"VERIFY-002 is VERIFIED while {uncovered} have no red path")


def test_every_named_red_path_is_collected_offline():
    ids = sorted({test for c in CONTROLS.values() for test in c.tests})
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "--collect-only", "-q",
         "-p", "no:cacheprovider", "-m", "not live and not slow", *ids],
        cwd=ROOT, capture_output=True, text=True, timeout=600)
    collected = {line.split("[", 1)[0]
                 for line in proc.stdout.splitlines() if "::" in line}
    missing = [test for test in ids if test not in collected]
    assert proc.returncode == 0 and not missing, (
        f"not collected offline: {missing}\n"
        + proc.stdout[-1500:] + proc.stderr[-1500:])
