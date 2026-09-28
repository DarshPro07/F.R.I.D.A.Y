"""
The packaged launcher (friday/launcher.py), tested from source.

FRIDAY.exe is three roles supervised by one executable, plus a shim that
serves Friday's own `sys.executable -c/-m/script.py` re-invocations because
a frozen build has no python.exe to hand. Everything with a decision in it
is a pure function here; the process supervision itself is exercised
against the built artifact (docs/build/FRIDAY_EXE_VALIDATION.md), not
mocked.
"""
from __future__ import annotations

import os
import sys

import pytest

# Imported first so friday.config's load_dotenv() has already happened;
# the doctor test then deletes LiveKit variables and must see them gone.
import friday.config  # noqa: F401
from friday import launcher as L


# ---------------------------------------------------------------------------
# sys.executable as an interpreter
# ---------------------------------------------------------------------------

def test_bundled_paths_map_to_module_names(tmp_path):
    assert L.bundled_module_name(tmp_path / "friday" / "objective_cli.py", tmp_path) == "friday.objective_cli"
    assert L.bundled_module_name(tmp_path / "scripts" / "jarvis_overlay.py", tmp_path) == "scripts.jarvis_overlay"
    assert L.bundled_module_name(tmp_path / "friday" / "__init__.py", tmp_path) == "friday"


def test_paths_outside_the_bundle_or_not_python_are_not_modules(tmp_path):
    assert L.bundled_module_name(tmp_path.parent / "elsewhere.py", tmp_path) is None
    assert L.bundled_module_name(tmp_path / "friday" / "notes.txt", tmp_path) is None


def test_shim_runs_dash_c_and_skips_interpreter_flags(tmp_path):
    marker = tmp_path / "marker.txt"
    code = L.maybe_run_as_python(
        ["-I", "-u", "-c", f"open({str(marker)!r}, 'w').write('ran')"], tmp_path)
    assert code == 0
    assert marker.read_text() == "ran"


def test_shim_runs_a_script_on_disk_with_its_arguments(tmp_path):
    script = tmp_path / "hello.py"
    script.write_text("import sys; open(sys.argv[1], 'w').write(sys.argv[2])", encoding="utf-8")
    out = tmp_path / "out.txt"
    assert L.maybe_run_as_python([str(script), str(out), "payload"], tmp_path) == 0
    assert out.read_text() == "payload"


def test_shim_runs_a_module_by_name(tmp_path, monkeypatch):
    package = tmp_path / "shimpkg"
    package.mkdir()
    (package / "__init__.py").write_text("", encoding="utf-8")
    (package / "job.py").write_text("import sys; open(sys.argv[1], 'w').write('module')", encoding="utf-8")
    monkeypatch.syspath_prepend(str(tmp_path))
    out = tmp_path / "m.txt"
    assert L.maybe_run_as_python(["-m", "shimpkg.job", str(out)], tmp_path) == 0
    assert out.read_text() == "module"


def test_shim_serves_a_bundled_path_that_does_not_exist_on_disk(tmp_path, monkeypatch):
    """
    The frozen case: friday.overlay computed <root>/scripts/jarvis_overlay.py
    from __file__, but in a bundle only the module exists. The path is
    mapped back to the module and run.
    """
    source = tmp_path / "src" / "shimpkg2"
    source.mkdir(parents=True)
    (source / "__init__.py").write_text("", encoding="utf-8")
    (source / "job.py").write_text("import sys; open(sys.argv[1], 'w').write('bundled')", encoding="utf-8")
    monkeypatch.syspath_prepend(str(tmp_path / "src"))
    root = tmp_path / "root"                      # no shimpkg2/job.py under it
    out = tmp_path / "b.txt"
    assert L.maybe_run_as_python([str(root / "shimpkg2" / "job.py"), str(out)], root) == 0
    assert out.read_text() == "bundled"


def test_shim_leaves_launcher_arguments_alone():
    assert L.maybe_run_as_python([]) is None
    assert L.maybe_run_as_python(["--role", "server"]) is None
    assert L.maybe_run_as_python(["--doctor"]) is None


def test_shim_reports_a_script_it_cannot_find(tmp_path, capsys):
    assert L.maybe_run_as_python([str(tmp_path / "nope.py")], tmp_path) == 2
    assert "can't open file" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# Roles and state
# ---------------------------------------------------------------------------

def test_role_commands_target_this_same_runtime(monkeypatch):
    monkeypatch.setattr(L, "frozen", lambda: True)
    assert L.role_command("agent", ["start"]) == [sys.executable, "--role", "agent", "start"]
    monkeypatch.setattr(L, "frozen", lambda: False)
    assert L.role_command("server") == [sys.executable, "-m", "friday.launcher", "--role", "server"]


def test_state_directories_are_created_under_the_root(tmp_path):
    made = L.ensure_state_dirs(tmp_path)
    assert all(path.is_dir() for path in made)
    assert tmp_path / "data" / "logs" in made
    assert tmp_path / "vault" in made


def test_the_launcher_defers_env_and_state_decisions_to_config(monkeypatch):
    """
    One decision, made in friday.config (tests/test_state_root.py covers the
    order). The launcher only reports it, so it can never disagree with
    what the roles resolved at import.
    """
    from friday import config as C

    assert L.env_file_candidates() == C.env_file_candidates()
    assert L.load_env() == C.ENV_FILE
    assert L.state_root() == C.STATE_ROOT
    assert "_internal" not in str(L.state_root())


# ---------------------------------------------------------------------------
# Stop: only what this runtime started
# ---------------------------------------------------------------------------

def test_frozen_stop_matches_only_this_executable():
    me = r"C:\apps\FRIDAY\FRIDAY.exe"
    other = r"C:\elsewhere\FRIDAY.exe"
    assert L.is_friday_process("FRIDAY.exe", me, [me, "--role", "agent", "start"],
                               self_exe=me, frozen_build=True)
    assert L.is_friday_process("FRIDAY.exe", me, [me, "--multiprocessing-fork", "x"],
                               self_exe=me, frozen_build=True)
    assert not L.is_friday_process("FRIDAY.exe", other, [other, "--role", "agent"],
                                   self_exe=me, frozen_build=True)
    assert not L.is_friday_process("python.exe", r"C:\py\python.exe", ["python", "server.py"],
                                   self_exe=me, frozen_build=True)


def test_source_stop_matches_only_launcher_roles():
    py = sys.executable
    assert L.is_friday_process("python.exe", py, [py, "-m", "friday.launcher", "--role", "ui"],
                               self_exe=py, frozen_build=False)
    assert not L.is_friday_process("python.exe", py, [py, "server.py"], self_exe=py, frozen_build=False)
    assert not L.is_friday_process("python.exe", py, [py, "-m", "pytest", "tests/test_launcher.py"],
                                   self_exe=py, frozen_build=False)


# ---------------------------------------------------------------------------
# Doctor
# ---------------------------------------------------------------------------

def test_the_verdict_is_the_worst_check():
    assert L.verdict([("a", L.PASS, "")]) == L.PASS
    assert L.verdict([("a", L.PASS, ""), ("b", L.DEGRADED, "")]) == L.DEGRADED
    assert L.verdict([("a", L.DEGRADED, ""), ("b", L.FAIL, "")]) == L.FAIL


def test_doctor_bootstraps_the_database_and_names_what_is_missing(tmp_path, monkeypatch):
    monkeypatch.setenv("ADA_DB", str(tmp_path / "doctor.sqlite3"))
    for name in L.REQUIRED_FOR_AGENT:
        monkeypatch.delenv(name, raising=False)
    root = tmp_path / "root"
    (root / "ui").mkdir(parents=True)
    (root / "ui" / "index.html").write_text("<html></html>", encoding="utf-8")

    checks = L.doctor_checks(root=root, env_path=None)
    by_name = {name: (state, detail) for name, state, detail in checks}

    assert by_name["env file"][0] == L.DEGRADED
    assert by_name["livekit credentials"][0] == L.FAIL
    assert "LIVEKIT_URL" in by_name["livekit credentials"][1]
    assert by_name["database"][0] == L.PASS
    # The schema is whatever friday.store says it is today, not a number
    # copied from a PRD; a fresh Store on another path is the same truth.
    from friday import store
    fresh = store.Store(tmp_path / "reference.sqlite3")
    expected = fresh._conn.execute("SELECT count(*) FROM sqlite_master WHERE type='table'").fetchone()[0]
    fresh._conn.close()
    assert f"({expected} tables)" in by_name["database"][1]
    assert expected >= 42                          # the PRD baseline, never fewer
    assert by_name["core imports"][0] == L.PASS
    assert by_name["ui assets"][0] == L.PASS
    assert by_name["writable data/"][0] == L.PASS
    assert (root / "data").is_dir() and (root / "logs").is_dir() and (root / "vault").is_dir()
    assert L.verdict(checks) == L.FAIL


def test_provider_credentials_are_keys_not_settings_with_defaults(tmp_path, monkeypatch):
    """STT_PROVIDER unset is normal (a default applies); GROQ_API_KEY unset is not."""
    names = L.provider_credential_names()
    assert names and all(n.endswith(("_API_KEY", "_SECRET", "_TOKEN")) for n in names)
    assert not any(n.startswith("LIVEKIT_") for n in names)
    assert "STT_PROVIDER" not in names

    monkeypatch.setenv("ADA_DB", str(tmp_path / "doctor.sqlite3"))
    root = tmp_path / "root"
    (root / "ui").mkdir(parents=True)
    (root / "ui" / "index.html").write_text("", encoding="utf-8")
    for name in names:
        monkeypatch.delenv(name, raising=False)
    by_name = {n: (s, d) for n, s, d in L.doctor_checks(root=root, env_path=None)}
    assert by_name["provider credentials"][0] == L.DEGRADED
    assert "none set" in by_name["provider credentials"][1]

    monkeypatch.setenv(names[0], "x")
    by_name = {n: (s, d) for n, s, d in L.doctor_checks(root=root, env_path=None)}
    assert by_name["provider credentials"][0] == L.PASS
    assert names[0] in by_name["provider credentials"][1]


def test_the_console_hold_is_a_decision_not_a_crash():
    """Under pytest the shell is attached to the console, so never a hold."""
    assert L.console_will_close() is False
    L.hold_if_the_window_would_close(1)            # returns at once, no prompt


def test_doctor_never_prints_a_secret_value(tmp_path, monkeypatch):
    monkeypatch.setenv("ADA_DB", str(tmp_path / "doctor.sqlite3"))
    monkeypatch.setenv("LIVEKIT_API_SECRET", "FRIDAY_TEST_SECRET_9f3a")
    root = tmp_path / "root"
    (root / "ui").mkdir(parents=True)
    (root / "ui" / "index.html").write_text("", encoding="utf-8")
    rendered = "\n".join(f"{n} {s} {d}" for n, s, d in L.doctor_checks(root=root, env_path=None))
    assert "FRIDAY_TEST_SECRET_9f3a" not in rendered


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def test_version_answers_without_starting_anything(capsys):
    assert L.main(["--version"]) == 0
    assert capsys.readouterr().out.startswith("FRIDAY")


def test_a_role_needs_a_name(capsys):
    assert L.main(["--role"]) == 2
    assert "--role needs a name" in capsys.readouterr().err


def test_an_unknown_role_is_refused(capsys, monkeypatch, tmp_path):
    monkeypatch.setattr(L, "app_root", lambda: tmp_path)
    monkeypatch.setattr(L, "exe_dir", lambda: tmp_path)
    assert L.run_role("nope", []) == 2
    assert "unknown role" in capsys.readouterr().err


def test_exclusive_modes_cannot_be_combined():
    with pytest.raises(SystemExit):
        L.build_parser().parse_args(["--stop", "--doctor"])
