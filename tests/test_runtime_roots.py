"""
The invariant, enforced by launching Friday from somewhere else.

    NO SUBSYSTEM MAY DERIVE A PERSISTENT, PRIVILEGED OR SECURITY-SENSITIVE
    LOCATION FROM THE PROCESS WORKING DIRECTORY. CWD IS EXECUTION CONTEXT,
    NOT TRUSTED CONFIGURATION.

Asserting `Path(X).is_absolute()` is not enough to hold this. An absolute path
can still be *computed* from the working directory - `Path("data").resolve()`
is absolute and wrong - so this starts real interpreters in four different
directories, including the one Task Scheduler uses, and requires every runtime
root to come back identical.

This is the class that produced a scheduled automation which fired on time,
ran its whole graph, and wrote its result to
`C:\\Windows\\System32\\data\\ada.sqlite3`. Nothing raised. It cost a live gate
run to find, and six months from now a new relative default would cost another.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent

#: Every location that must not move. Two are security boundaries rather than
#: conveniences: the jail root decides what may be touched, and the companion
#: token is the secret the browser extension authenticates with.
PROBE = r"""
import json
from pathlib import Path
from friday.config import ARTIFACTS_DIR, DATA_DIR, LOGS_DIR, PROJECT_ROOT
from friday.companion.bridge import TOKEN_PATH
from friday.companion.pairing import ID_PATH, KEY_PATH
from friday.fsjail import DEFAULT_WORKSPACE
from friday.store import DEFAULT_DB
from friday.toolsets.vision import captures_dir

print(json.dumps({
    "project_root": str(PROJECT_ROOT),
    "data_dir": str(DATA_DIR),
    "logs_dir": str(LOGS_DIR),
    "artifacts_dir": str(ARTIFACTS_DIR),
    "database": str(DEFAULT_DB),
    "fs_jail_root": str(DEFAULT_WORKSPACE),
    "companion_token": str(TOKEN_PATH),
    "companion_key": str(KEY_PATH),
    "companion_id": str(ID_PATH),
    "captures": str(captures_dir()),
}))
"""


def probe_from(cwd: Path) -> dict:
    completed = subprocess.run(
        [sys.executable, "-c", PROBE], cwd=str(cwd),
        capture_output=True, text=True, timeout=120)
    assert completed.returncode == 0, (
        f"Friday could not even start from {cwd}:\n{completed.stderr[-1500:]}")
    return json.loads(completed.stdout.strip().splitlines()[-1])


def launch_directories() -> list[Path]:
    places = [ROOT, Path(tempfile.gettempdir())]
    system32 = Path(os.environ.get("SYSTEMROOT", "C:\\Windows")) / "System32"
    if system32.is_dir():
        places.append(system32)          # exactly where Task Scheduler starts
    home = Path.home()
    if home.is_dir():
        places.append(home)
    return [p for p in places if p.is_dir()]


@pytest.mark.slow
def test_every_runtime_root_is_identical_wherever_friday_is_launched_from():
    places = launch_directories()
    assert len(places) >= 3, f"not enough launch directories to prove anything: {places}"

    baseline = probe_from(places[0])
    for place in places[1:]:
        got = probe_from(place)
        differing = {
            key: (baseline[key], got[key])
            for key in baseline if baseline[key] != got[key]
        }
        assert not differing, (
            f"launched from {place}, these moved: "
            + "; ".join(f"{k}: {a!r} -> {b!r}" for k, (a, b) in differing.items()))


@pytest.mark.slow
def test_the_probe_would_actually_catch_a_relative_path():
    """
    A test that cannot fail proves nothing. This asserts the method works: a
    deliberately relative path resolves differently in two directories, so the
    comparison above is capable of detecting one.
    """
    script = ("import json;from pathlib import Path;"
              "print(json.dumps({'naive': str(Path('data').resolve())}))")
    places = launch_directories()
    first = json.loads(subprocess.run(
        [sys.executable, "-c", script], cwd=str(places[0]),
        capture_output=True, text=True, timeout=60).stdout)
    second = json.loads(subprocess.run(
        [sys.executable, "-c", script], cwd=str(places[1]),
        capture_output=True, text=True, timeout=60).stdout)

    assert first["naive"] != second["naive"], (
        "a relative path resolved identically from two directories, so this "
        "harness could not detect the bug it exists for")
    assert Path(first["naive"]).is_absolute(), (
        "and note it is absolute in both - which is why asserting "
        "is_absolute() alone does not hold the invariant")
