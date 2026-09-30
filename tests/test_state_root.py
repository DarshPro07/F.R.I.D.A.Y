"""
Where state lives, and where `.env` comes from (friday/config.py).

    FRIDAY_HOME        wins everywhere
    frozen + portable  beside the executable
    frozen             %LOCALAPPDATA%\\FRIDAY
    source checkout    the repository root

and the `.env` order the owner asked for: OS environment, then
STATE_ROOT/config/.env, then beside the executable only in portable mode,
then the repository only from source. A packaged build must never read
the repository and a non-portable build must never read a file lying
beside the executable. Nothing may depend on the working directory
(tests/test_runtime_roots.py keeps that half).
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

from friday import config as C
from friday import launcher as L


@pytest.fixture
def frozen_at(tmp_path, monkeypatch):
    """Pretend to be FRIDAY.exe at <tmp>/dist/FRIDAY/FRIDAY.exe."""
    exe_dir = tmp_path / "dist" / "FRIDAY"
    exe_dir.mkdir(parents=True)
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(exe_dir / "FRIDAY.exe"))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "LocalAppData"))
    monkeypatch.delenv("FRIDAY_HOME", raising=False)
    monkeypatch.delenv("PORTABLE_MODE", raising=False)
    return exe_dir


# ---------------------------------------------------------------------------
# state root
# ---------------------------------------------------------------------------

def test_from_source_the_repository_is_the_state_root(monkeypatch):
    monkeypatch.delenv("FRIDAY_HOME", raising=False)
    monkeypatch.setattr(sys, "frozen", False, raising=False)
    assert C.default_state_root() == C.PROJECT_ROOT
    # This process's own STATE_ROOT is the test session's (FRIDAY_HOME, set by
    # tests/conftest.py for TEST-ISOLATION-001) and is checked in
    # tests/test_test_isolation.py; the rule for a source checkout is above.


def test_friday_home_wins_everywhere(tmp_path, monkeypatch, frozen_at):
    monkeypatch.setenv("FRIDAY_HOME", str(tmp_path / "home"))
    assert C.default_state_root() == tmp_path / "home"
    monkeypatch.setattr(sys, "frozen", False, raising=False)
    assert C.default_state_root() == tmp_path / "home"


def test_a_packaged_build_keeps_state_under_localappdata(tmp_path, frozen_at):
    assert C.default_state_root() == tmp_path / "LocalAppData" / "FRIDAY"
    assert C.default_state_root() != frozen_at
    assert "_internal" not in str(C.default_state_root())


def test_portable_mode_keeps_state_beside_the_executable(tmp_path, monkeypatch, frozen_at):
    monkeypatch.setenv("PORTABLE_MODE", "true")
    assert C.portable_mode() is True
    assert C.default_state_root() == frozen_at
    monkeypatch.setenv("PORTABLE_MODE", "false")
    assert C.portable_mode() is False
    monkeypatch.delenv("PORTABLE_MODE")
    (frozen_at / C.PORTABLE_MARKER).write_text("", encoding="utf-8")
    assert C.portable_mode() is True                # the marker file, for a double-click
    assert C.default_state_root() == frozen_at


def test_portable_mode_is_never_true_from_source(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "frozen", False, raising=False)
    monkeypatch.delenv("PORTABLE_MODE", raising=False)
    (Path(sys.executable).resolve().parent / C.PORTABLE_MARKER)  # may not exist; not created
    assert C.portable_mode() is False


def test_state_root_never_depends_on_the_working_directory(tmp_path, monkeypatch, frozen_at):
    before = C.default_state_root()
    monkeypatch.chdir(tmp_path)
    assert C.default_state_root() == before


# ---------------------------------------------------------------------------
# .env order
# ---------------------------------------------------------------------------

def test_env_order_for_a_packaged_build(tmp_path, monkeypatch, frozen_at):
    monkeypatch.setattr(C, "CONFIG_DIR", tmp_path / "LocalAppData" / "FRIDAY" / "config")
    found = C.env_file_candidates()
    assert found == [tmp_path / "LocalAppData" / "FRIDAY" / "config" / ".env"]
    assert frozen_at / ".env" not in found            # never beside the exe unless portable
    assert C.PROJECT_ROOT / ".env" not in found       # never the repository


def test_env_order_in_portable_mode(tmp_path, monkeypatch, frozen_at):
    monkeypatch.setenv("PORTABLE_MODE", "1")
    monkeypatch.setattr(C, "CONFIG_DIR", frozen_at / "config")
    assert C.env_file_candidates() == [frozen_at / "config" / ".env", frozen_at / ".env"]


def test_env_order_from_source(monkeypatch):
    monkeypatch.setattr(sys, "frozen", False, raising=False)
    monkeypatch.delenv("FRIDAY_HOME", raising=False)
    assert C.env_file_candidates() == [C.PROJECT_ROOT / ".env"]


def test_env_order_from_source_with_friday_home(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "frozen", False, raising=False)
    monkeypatch.setenv("FRIDAY_HOME", str(tmp_path / "home"))
    monkeypatch.setattr(C, "CONFIG_DIR", tmp_path / "home" / "config")
    assert C.env_file_candidates() == [tmp_path / "home" / "config" / ".env", C.PROJECT_ROOT / ".env"]


def test_only_the_first_existing_file_is_loaded_and_the_environment_wins(tmp_path, monkeypatch, frozen_at):
    monkeypatch.setenv("PORTABLE_MODE", "1")
    config_dir = frozen_at / "config"
    config_dir.mkdir()
    monkeypatch.setattr(C, "CONFIG_DIR", config_dir)
    monkeypatch.delenv("FRIDAY_STATE_ROOT_TEST_A", raising=False)
    monkeypatch.setenv("FRIDAY_STATE_ROOT_TEST_B", "from-shell")
    (config_dir / ".env").write_text("FRIDAY_STATE_ROOT_TEST_A=from-config\nFRIDAY_STATE_ROOT_TEST_B=from-file\n",
                                     encoding="utf-8")
    (frozen_at / ".env").write_text("FRIDAY_STATE_ROOT_TEST_A=from-exe-dir\n", encoding="utf-8")
    assert C.load_env_files() == config_dir / ".env"
    assert os.environ["FRIDAY_STATE_ROOT_TEST_A"] == "from-config"
    assert os.environ["FRIDAY_STATE_ROOT_TEST_B"] == "from-shell"
    monkeypatch.delenv("FRIDAY_STATE_ROOT_TEST_A")


def test_no_env_file_is_reported_not_raised(tmp_path, monkeypatch, frozen_at):
    monkeypatch.setattr(C, "CONFIG_DIR", tmp_path / "nowhere" / "config")
    assert C.load_env_files() is None


def test_a_legacy_env_beside_the_exe_is_adopted_into_config_once(tmp_path, monkeypatch, frozen_at):
    """The layout of builds before 2026-09-15: .env beside FRIDAY.exe."""
    config_dir = tmp_path / "LocalAppData" / "FRIDAY" / "config"
    monkeypatch.setattr(C, "CONFIG_DIR", config_dir)
    (frozen_at / ".env").write_text("FRIDAY_STATE_ROOT_TEST_C=adopted\n", encoding="utf-8")
    monkeypatch.delenv("FRIDAY_STATE_ROOT_TEST_C", raising=False)
    assert C.adopt_legacy_env_file() == config_dir / ".env"
    assert not (frozen_at / ".env").exists()
    assert (config_dir / ".env").read_text(encoding="utf-8").startswith("FRIDAY_STATE_ROOT_TEST_C")
    # a second, newer file beside the exe never overwrites the adopted one
    (frozen_at / ".env").write_text("FRIDAY_STATE_ROOT_TEST_C=newer\n", encoding="utf-8")
    assert C.adopt_legacy_env_file() is None
    assert (config_dir / ".env").read_text(encoding="utf-8").startswith("FRIDAY_STATE_ROOT_TEST_C=adopted")
    assert C.load_env_files() == config_dir / ".env"
    assert os.environ["FRIDAY_STATE_ROOT_TEST_C"] == "adopted"
    monkeypatch.delenv("FRIDAY_STATE_ROOT_TEST_C")


def test_portable_mode_does_not_adopt(tmp_path, monkeypatch, frozen_at):
    monkeypatch.setenv("PORTABLE_MODE", "1")
    monkeypatch.setattr(C, "CONFIG_DIR", frozen_at / "config")
    (frozen_at / ".env").write_text("X=1\n", encoding="utf-8")
    assert C.adopt_legacy_env_file() is None
    assert (frozen_at / ".env").exists()


def test_runtime_paths_are_all_strings():
    found = C.runtime_paths()
    assert all(isinstance(v, str) for v in found.values()), found
    assert found["state_root"] == str(C.STATE_ROOT)
    assert found["frozen"] in ("true", "false")


# ---------------------------------------------------------------------------
# migrating an earlier build's state out of _internal
# ---------------------------------------------------------------------------

def _legacy_bundle(root: Path) -> Path:
    internal = root / "_internal"
    (internal / "data" / "corpus").mkdir(parents=True)
    (internal / "data" / "gate").mkdir()
    (internal / "data" / "corpus" / "fixture.json").write_text("{}", encoding="utf-8")
    (internal / "data" / "ada.sqlite3").write_bytes(b"old database")
    (internal / "data" / "logs").mkdir()
    (internal / "data" / "logs" / "friday.log").write_text("old", encoding="utf-8")
    (internal / "logs").mkdir()
    (internal / "logs" / "server.log").write_text("old", encoding="utf-8")
    (internal / "vault" / "wiki").mkdir(parents=True)
    (internal / "vault" / "wiki" / "page.md").write_text("old", encoding="utf-8")
    return internal


def test_legacy_state_moves_out_of_internal_and_fixtures_stay(tmp_path):
    internal = _legacy_bundle(tmp_path)
    home = tmp_path / "home"
    moved = L.migrate_legacy_state(internal, home, explicit_home=False)
    assert sorted(moved) == ["data/ada.sqlite3", "data/logs", "logs/server.log", "vault/wiki"]
    assert (home / "data" / "ada.sqlite3").read_bytes() == b"old database"
    assert (home / "vault" / "wiki" / "page.md").exists()
    assert (internal / "data" / "corpus" / "fixture.json").exists()     # shipped, stays
    assert (internal / "data" / "gate").is_dir()
    assert not (internal / "data" / "ada.sqlite3").exists()


def test_migration_never_overwrites_newer_state(tmp_path):
    internal = _legacy_bundle(tmp_path)
    home = tmp_path / "home"
    (home / "data").mkdir(parents=True)
    (home / "data" / "ada.sqlite3").write_bytes(b"new database")
    moved = L.migrate_legacy_state(internal, home, explicit_home=False)
    assert "data/ada.sqlite3" not in moved
    assert (home / "data" / "ada.sqlite3").read_bytes() == b"new database"
    assert (internal / "data" / "ada.sqlite3").read_bytes() == b"old database"   # left for the owner


def test_an_explicit_home_receives_nothing(tmp_path):
    internal = _legacy_bundle(tmp_path)
    assert L.migrate_legacy_state(internal, tmp_path / "home", explicit_home=True) == []
    assert (internal / "data" / "ada.sqlite3").exists()


def test_migration_is_a_no_op_when_the_roots_coincide(tmp_path):
    internal = _legacy_bundle(tmp_path)
    assert L.migrate_legacy_state(internal, internal, explicit_home=False) == []


def test_migration_is_a_no_op_from_source(monkeypatch):
    monkeypatch.setattr(sys, "frozen", False, raising=False)
    assert L.adopt_legacy_state() == []
