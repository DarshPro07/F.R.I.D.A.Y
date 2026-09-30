"""
FRIDAY's own state is not a file its tools may touch, and its own work areas
are inside the jail wherever the state lives (V7-D55, V7-D56).

From a source checkout the project directory is a jail root, which put the
live store, the audit trail, the confirmation nonces and the MCP pins within
reach of files_read, files_write and files_delete; the denylist covered only
secret-shaped files. Inside the state root's data directory only FRIDAY's two
work areas - its workspace and its artifacts - are files for the tools; the
rest is refused. And wherever the state root lives - a packaged build keeps it
under %LOCALAPPDATA%, the test session in a temporary directory - those two
areas are roots, so FRIDAY can still write the files it makes for the person.
"""
from __future__ import annotations

import pytest

from friday import config
from friday import fsjail


@pytest.mark.parametrize("name", ["ada.sqlite3", "audit.sqlite3", "consumed_nonces.json",
                                  "mcp_pins.json", "access_log.jsonl", "logs/friday.log"])
def test_fridays_own_state_is_refused_even_inside_a_root(name):
    jail = fsjail.FileJail(roots=(config.STATE_ROOT,))
    with pytest.raises(fsjail.JailError, match="FRIDAY's own state"):
        jail.resolve(config.DATA_DIR / name)


def test_the_work_areas_inside_the_state_are_files_for_the_tools():
    jail = fsjail.FileJail(roots=(config.STATE_ROOT,))
    assert jail.resolve(fsjail.DEFAULT_WORKSPACE / "note.txt").name == "note.txt"
    assert jail.resolve(config.ARTIFACTS_DIR / "report.md").name == "report.md"


def test_the_work_areas_are_roots_wherever_the_state_lives():
    # In this session the state root is outside the project, as in a packaged
    # build; the work areas need not exist yet to be roots.
    assert config.PROJECT_ROOT not in config.STATE_ROOT.parents
    roots = fsjail.default_roots()
    assert config.ARTIFACTS_DIR.resolve() in roots
    assert fsjail.DEFAULT_WORKSPACE.resolve() in roots
