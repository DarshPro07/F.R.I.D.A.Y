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
from pathlib import Path

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

    monkeypatch.setenv(names[0], _provider_env()["GOOGLE_API_KEY"])      # key-shaped for any provider
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


# --- credentials: set is not usable ---------------------------------------

_REAL_LOOKING = {
    "LIVEKIT_URL": "wss://friday-7k2m9q.livekit.cloud",
    "LIVEKIT_API_KEY": "APIkq3Zb8Hn2WvTt",
    "LIVEKIT_API_SECRET": "Q8sD2fGh1JkL4zXcV7bN0mP3oI6uY9tR5eW2aS1d",
}


def _template_env() -> dict[str, str]:
    values = {}
    for line in (Path(__file__).resolve().parents[1] / ".env.example").read_text(
            encoding="utf-8").splitlines():
        name, sep, value = line.partition("=")
        if sep and not line.lstrip().startswith("#"):
            values[name.strip()] = value.strip()
    return values


def _doctor_rows(tmp_path, monkeypatch, env: dict[str, str], **probes):
    """doctor_checks against a scratch root with exactly `env` as credentials."""
    monkeypatch.setenv("ADA_DB", str(tmp_path / "doctor.sqlite3"))
    for name in (*L.REQUIRED_FOR_AGENT, *L.provider_credential_names()):
        monkeypatch.delenv(name, raising=False)
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    root = tmp_path / "root"
    (root / "ui").mkdir(parents=True, exist_ok=True)
    (root / "ui" / "index.html").write_text("", encoding="utf-8")
    return {n: (s, d) for n, s, d in L.doctor_checks(root=root, env_path=None, **probes)}


def _provider_env(**override) -> dict[str, str]:
    """A realistic-looking key for every provider credential, then `override`."""
    env = {name: f"k{index}Zq8Wm3Xc7Vb1Nl5Hj9Gf2Ds4" for index, name
           in enumerate(L.provider_credential_names())}
    env["GOOGLE_API_KEY"] = "AIzaSyD4kQ9mZ2pL7vB1nX8cF3hJ6tR0wE5yU"       # a Gemini key's shape
    env.update(override)
    return env


def test_a_template_livekit_credential_degrades_the_doctor_and_is_named(tmp_path, monkeypatch):
    """The 2026-10-02 defect: .env.example values reported PASS."""
    template = _template_env()
    env = {name: template[name] for name in L.REQUIRED_FOR_AGENT}
    row = _doctor_rows(tmp_path, monkeypatch, env, **_accepting())["livekit credentials"]
    assert row[0] == L.DEGRADED
    for name in L.REQUIRED_FOR_AGENT:
        assert name in row[1]
    assert "placeholder" in row[1]
    assert template["LIVEKIT_API_SECRET"] not in row[1]


def test_a_template_provider_key_degrades_the_doctor_and_is_named(tmp_path, monkeypatch):
    env = {**_REAL_LOOKING, **_provider_env(OPENAI_API_KEY=_template_env()["OPENAI_API_KEY"],
                                            GOOGLE_API_KEY=_template_env()["GOOGLE_API_KEY"])}
    row = _doctor_rows(tmp_path, monkeypatch, env, **_accepting())["provider credentials"]
    assert row[0] == L.DEGRADED
    assert "placeholder: " in row[1] and "OPENAI_API_KEY" in row[1] and "GOOGLE_API_KEY" in row[1]


def test_a_key_the_provider_rejects_degrades_the_doctor_and_is_named(tmp_path, monkeypatch):
    """GOOGLE_API_KEY non-empty and not a template, but Google answers 401."""
    from friday import credentials

    def provider(name, value):
        if name == "GOOGLE_API_KEY":
            return credentials.Verdict(credentials.REJECTED, "rejected (HTTP 401)")
        return credentials.Verdict(credentials.VALID, "accepted")

    env = {**_REAL_LOOKING, **_provider_env()}
    rows = _doctor_rows(tmp_path, monkeypatch, env, provider_probe=provider,
                        livekit_probe=_accept_livekit)
    state, detail = rows["provider credentials"]
    assert state == L.DEGRADED
    assert "rejected: GOOGLE_API_KEY" in detail
    assert env["GOOGLE_API_KEY"] not in detail
    assert rows["livekit credentials"][0] == L.PASS


def test_a_refused_livekit_key_pair_degrades_the_doctor(tmp_path, monkeypatch):
    from friday import credentials

    def refuse(url, key, secret):
        return credentials.Verdict(credentials.REJECTED, "rejected (HTTP 401)")

    state, detail = _doctor_rows(tmp_path, monkeypatch, {**_REAL_LOOKING, **_provider_env()},
                                 livekit_probe=refuse)["livekit credentials"]
    assert state == L.DEGRADED
    assert "rejected: LIVEKIT_API_KEY, LIVEKIT_API_SECRET" in detail


def test_credentials_the_providers_accept_pass_and_say_so(tmp_path, monkeypatch):
    rows = _doctor_rows(tmp_path, monkeypatch, {**_REAL_LOOKING, **_provider_env()},
                        **_accepting())
    assert rows["livekit credentials"][0] == L.PASS
    assert "accepted" in rows["livekit credentials"][1]
    assert rows["provider credentials"][0] == L.PASS
    assert "valid: " in rows["provider credentials"][1]


def test_without_live_the_doctor_makes_no_network_call_and_claims_no_validity(tmp_path, monkeypatch):
    from friday import netguard

    def no_network(*args, **kwargs):
        raise AssertionError("a static doctor must not reach the network")

    monkeypatch.setattr(netguard, "fetch", no_network)
    rows = _doctor_rows(tmp_path, monkeypatch, {**_REAL_LOOKING, **_provider_env()})
    assert "unverified: " in rows["provider credentials"][1]
    assert "valid: " not in rows["provider credentials"][1]


def _accept_livekit(url, key, secret):
    from friday import credentials

    return credentials.Verdict(credentials.VALID, "accepted")


def _accepting() -> dict:
    from friday import credentials

    return {"provider_probe": lambda name, value: credentials.Verdict(credentials.VALID, "accepted"),
            "livekit_probe": _accept_livekit}


# --- the 2026-10-04 exe defects: voice credentials, setup, first run ---------

def _voice_row(tmp_path, monkeypatch, env: dict[str, str], **probes):
    base = {**_REAL_LOOKING, **_provider_env()}
    return _doctor_rows(tmp_path, monkeypatch, {**base, **env}, **probes)["voice"]


def test_a_refused_llm_key_fails_the_voice_row_naming_the_variable_and_the_reason(tmp_path, monkeypatch):
    """The doctor reported voice llm PASS while GOOGLE_API_KEY was refused (401)."""
    from friday import credentials

    def provider(name, value):
        if name == "GOOGLE_API_KEY":
            return credentials.Verdict(
                credentials.REJECTED, "GOOGLE_API_KEY was rejected by the provider (HTTP 401, UNAUTHENTICATED)")
        return credentials.Verdict(credentials.VALID, "accepted")

    monkeypatch.setenv("LLM_BACKEND", "google")
    state, detail = _voice_row(tmp_path, monkeypatch, {}, provider_probe=provider,
                               livekit_probe=_accept_livekit)
    assert state == L.FAIL
    assert "GOOGLE_API_KEY" in detail and "UNAUTHENTICATED" in detail
    assert _provider_env()["GOOGLE_API_KEY"] not in detail


def test_a_gemini_key_in_the_wrong_format_fails_the_voice_row_before_any_call(tmp_path, monkeypatch):
    from friday import netguard

    def no_network(*args, **kwargs):
        raise AssertionError("a wrong-format key must not reach the provider")

    monkeypatch.setattr(netguard, "fetch", no_network)
    monkeypatch.setenv("LLM_BACKEND", "google")
    wrong = "AQ." + "Ab3dE5gH7jK9mN1pQ3rS5tU7vW9yZ1aB" * 3
    state, detail = _voice_row(tmp_path, monkeypatch, {"GOOGLE_API_KEY": wrong},
                               **_accepting())
    assert state == L.FAIL
    assert "GOOGLE_API_KEY" in detail and "AIza" in detail
    assert wrong not in detail


def test_a_template_llm_key_fails_the_voice_row(tmp_path, monkeypatch):
    monkeypatch.setenv("LLM_BACKEND", "google")
    state, detail = _voice_row(
        tmp_path, monkeypatch, {"GOOGLE_API_KEY": _template_env()["GOOGLE_API_KEY"]}, **_accepting())
    assert state == L.FAIL and "GOOGLE_API_KEY" in detail and "template" in detail


def test_an_unset_llm_key_degrades_the_voice_row_and_an_accepted_one_does_not(monkeypatch):
    from friday import credentials

    monkeypatch.setenv("LLM_BACKEND", "google")
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    _, state, detail = L.voice_check({})
    assert state == L.DEGRADED and "missing GOOGLE_API_KEY" in detail

    monkeypatch.setenv("GOOGLE_API_KEY", _provider_env()["GOOGLE_API_KEY"])
    _, state, detail = L.voice_check({"GOOGLE_API_KEY": credentials.Verdict(credentials.VALID, "accepted")})
    assert "llm google/" in detail and "GOOGLE_API_KEY" not in detail
    assert state != L.FAIL


def test_a_template_llm_key_with_a_signed_in_cli_brain_degrades_and_names_it(tmp_path, monkeypatch):
    # FRIDAY.exe 2026-10-05: the voice row FAILed on the template GOOGLE_API_KEY while
    # the agent was answering through the signed-in Claude CLI.
    monkeypatch.setenv("LLM_BACKEND", "google")
    monkeypatch.setattr(L, "cli_brain", lambda: "claude-cli")
    state, detail = _voice_row(
        tmp_path, monkeypatch, {"GOOGLE_API_KEY": _template_env()["GOOGLE_API_KEY"]}, **_accepting())
    assert state == L.DEGRADED
    assert "GOOGLE_API_KEY" in detail and "answering through claude-cli (no tool calls)" in detail


def test_cli_brain_needs_a_signed_in_backend(monkeypatch):
    from friday import cli_model_provider as C

    class Out(C.ClaudeCodeBackend):
        def entry(self):
            return self._row(False, "signed out")

    class In(C.ClaudeCodeBackend):
        def entry(self):
            return self._row(True, "signed in")

    monkeypatch.setattr(C, "discover_backends", lambda env=None: (Out(),))
    assert L.cli_brain() is None
    monkeypatch.setattr(C, "discover_backends", lambda env=None: (Out(), In()))
    assert L.cli_brain() == "claude-cli"


def test_an_unanswered_interview_is_a_setup_step_not_a_degradation(tmp_path, monkeypatch):
    from friday import onboarding

    monkeypatch.setattr(onboarding.Onboarding, "progress", lambda self: {
        "answered": 0, "total": 16, "essential_remaining": 5, "useful_now": False})
    name, state, detail = L.onboarding_check(tmp_path)
    assert name == "onboarding" and state == L.PASS
    assert "0/16 answered" in detail and "setup step" in detail and "friday.onboarding next" in detail


def test_an_onboarded_start_is_silent_and_never_opens_the_interview(tmp_path, monkeypatch):
    from friday import onboarding

    marker = onboarding.state_root_marker(tmp_path)
    marker.parent.mkdir(parents=True)
    marker.write_text('{"completed_at": 1.0}', encoding="utf-8")

    def refuse():
        raise AssertionError("an onboarded install opened the interview")

    monkeypatch.setattr(onboarding, "Onboarding", refuse)
    assert L.onboarding_at_startup(tmp_path) == []


def test_a_first_start_that_cannot_load_the_interview_says_so(tmp_path, monkeypatch):
    from friday import onboarding

    def broken():
        raise RuntimeError("store locked")

    monkeypatch.setattr(onboarding, "Onboarding", broken)
    (line,) = L.onboarding_at_startup(tmp_path)
    assert "RuntimeError" in line and "store locked" in line and "friday.onboarding status" in line


class _Ran:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode, self.stdout, self.stderr = returncode, stdout, stderr


def _hermes_profile_world(monkeypatch, tmp_path, *, exists_after: bool):
    """A Hermes whose `profile create` makes (or fails to make) the directory."""
    from friday import hermes_bridge

    home = tmp_path / "profiles" / "friday"
    monkeypatch.delenv(hermes_bridge.ENV_PROFILE, raising=False)
    monkeypatch.delenv(hermes_bridge.ENV_PROFILE_HOME, raising=False)
    monkeypatch.setattr(hermes_bridge, "profile_home", lambda profile: str(home) if home.is_dir() else "")
    commands: list[list[str]] = []

    def runner(command, **kwargs):
        commands.append(command)
        assert kwargs["timeout"] > 0                       # bounded
        if exists_after:
            home.mkdir(parents=True)
        return _Ran(0 if exists_after else 1, stderr="boom: profile exists\nlast line")

    return commands, runner, home


def test_first_run_creates_the_friday_hermes_profile_when_it_is_missing(tmp_path, monkeypatch):
    commands, runner, home = _hermes_profile_world(monkeypatch, tmp_path, exists_after=True)
    said: list[str] = []
    note = L.ensure_hermes_profile("C:/hermes/hermes.exe", runner=runner, announce=said.append)
    assert commands == [["C:/hermes/hermes.exe", "profile", "create", "friday", "--clone"]]
    assert "created 'friday'" in note and home.is_dir()
    assert said and "first run" in said[0]
    # the second start finds it and does nothing
    assert L.ensure_hermes_profile("C:/hermes/hermes.exe", runner=runner) == ""
    assert len(commands) == 1


def test_a_failed_profile_creation_is_reported_with_the_manual_command_and_does_not_raise(
        tmp_path, monkeypatch):
    commands, runner, _ = _hermes_profile_world(monkeypatch, tmp_path, exists_after=False)
    note = L.ensure_hermes_profile("hermes", runner=runner)
    assert "was not created" in note and "last line" in note
    assert "hermes profile create friday --clone" in note

    def hangs(command, **kwargs):
        raise L.subprocess.TimeoutExpired(command, kwargs["timeout"])

    assert "not created within" in L.ensure_hermes_profile("hermes", runner=hangs, timeout=3)


def test_no_hermes_or_an_operator_pinned_profile_creates_nothing(tmp_path, monkeypatch):
    from friday import hermes_bridge

    commands, runner, _ = _hermes_profile_world(monkeypatch, tmp_path, exists_after=True)
    assert L.ensure_hermes_profile(None, runner=runner) == ""
    monkeypatch.setenv(hermes_bridge.ENV_PROFILE_HOME, str(tmp_path / "mine"))
    assert L.ensure_hermes_profile("hermes", runner=runner) == ""
    monkeypatch.delenv(hermes_bridge.ENV_PROFILE_HOME)
    monkeypatch.setenv(hermes_bridge.ENV_PROFILE, "../escape --flag")
    assert "not a valid profile name" in L.ensure_hermes_profile("hermes", runner=runner)
    assert commands == []


def test_the_doctor_reports_the_hermes_profile_state(tmp_path, monkeypatch):
    _, runner, home = _hermes_profile_world(monkeypatch, tmp_path, exists_after=True)
    assert L.hermes_profile_check(None)[1:] == (L.PASS, "not applicable: hermes is not installed")
    name, state, detail = L.hermes_profile_check("hermes")
    assert name == "hermes profile" and state == L.DEGRADED
    assert "hermes profile create friday --clone" in detail
    home.mkdir(parents=True)
    state, detail = L.hermes_profile_check("hermes")[1:]
    assert state == L.PASS and str(home) in detail


def test_the_launcher_registers_skills_and_creates_the_hermes_profile_at_startup(
        tmp_path, monkeypatch, capsys):
    calls: list[str] = []
    _, out = _run_all_roles(
        tmp_path, monkeypatch, capsys, {},
        skills=lambda: calls.append("skills") or "skills: registered 1 from disk (friday-operate)",
        hermes=lambda binary, **kw: calls.append("hermes") or "hermes profile: created 'friday'")
    assert calls == ["skills", "hermes"]
    assert "skills: registered 1 from disk (friday-operate)" in out
    assert "hermes profile: created 'friday'" in out


# --- the launcher and the voice worker --------------------------------------

def _run_all_roles(tmp_path, monkeypatch, capsys, env: dict[str, str], *,
                   skills=lambda: "", hermes=lambda binary, **kw: "") -> tuple[list[str], str]:
    """Run L.run_all against fakes; returns (roles it started, its output).
    The skill registration and the Hermes profile are this machine's, so they
    are fakes too."""
    for name in L.REQUIRED_FOR_AGENT:
        monkeypatch.delenv(name, raising=False)
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    started: list[str] = []
    monkeypatch.setattr(L, "app_root", lambda: tmp_path)
    monkeypatch.setattr(L, "state_root", lambda: tmp_path / "state")
    monkeypatch.setattr(L, "adopt_legacy_state", lambda: [])
    monkeypatch.setattr(L, "ensure_state_dirs", lambda root=None: [])
    monkeypatch.setattr(L, "load_env", lambda: None)
    monkeypatch.setattr(L, "register_skills_at_startup", skills)
    monkeypatch.setattr(L, "ensure_hermes_profile", hermes)
    monkeypatch.setattr(L, "port_open", lambda host, port, timeout=0.5: True)   # attach, start no server/ui
    monkeypatch.setattr(L, "start_role",
                        lambda role, extra, logs_dir, jobs: started.append(role))

    def stop(_seconds):
        raise KeyboardInterrupt

    monkeypatch.setattr(L.time, "sleep", stop)
    assert L.run_all() == 0
    return started, capsys.readouterr().out


def test_the_launcher_does_not_start_the_voice_worker_on_template_livekit_credentials(
        tmp_path, monkeypatch, capsys):
    template = _template_env()
    started, out = _run_all_roles(tmp_path, monkeypatch, capsys,
                                  {name: template[name] for name in L.REQUIRED_FOR_AGENT})
    assert "agent" not in started
    assert out.count("not starting the voice worker") == 1
    assert "placeholder" in out and "LIVEKIT_URL" in out
    assert "browser voice" in out
    assert template["LIVEKIT_API_SECRET"] not in out


def test_the_launcher_does_not_start_the_voice_worker_without_livekit_credentials(
        tmp_path, monkeypatch, capsys):
    started, out = _run_all_roles(tmp_path, monkeypatch, capsys, {})
    assert "agent" not in started
    assert "missing" in out


def test_the_launcher_does_not_start_the_voice_worker_on_a_refused_key_pair(
        tmp_path, monkeypatch, capsys):
    from friday import credentials

    monkeypatch.setattr(credentials, "probe_livekit",
                        lambda url, key, secret: credentials.Verdict(credentials.REJECTED, "401"))
    started, out = _run_all_roles(tmp_path, monkeypatch, capsys, _REAL_LOOKING)
    assert "agent" not in started
    assert "rejected: LIVEKIT_API_KEY, LIVEKIT_API_SECRET" in out


def test_the_launcher_starts_the_voice_worker_when_livekit_accepts_the_credentials(
        tmp_path, monkeypatch, capsys):
    from friday import credentials

    monkeypatch.setattr(credentials, "probe_livekit", _accept_livekit)
    started, out = _run_all_roles(tmp_path, monkeypatch, capsys, _REAL_LOOKING)
    assert started == ["agent"]
    assert "not starting the voice worker" not in out


def test_an_unreachable_livekit_does_not_keep_the_voice_worker_off(tmp_path, monkeypatch, capsys):
    """A network blip at launch is the worker's own retry loop's business."""
    from friday import credentials

    monkeypatch.setattr(credentials, "probe_livekit",
                        lambda url, key, secret: credentials.Verdict(credentials.UNVERIFIED, "timed out"))
    started, _ = _run_all_roles(tmp_path, monkeypatch, capsys, _REAL_LOOKING)
    assert started == ["agent"]


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
    with pytest.raises(SystemExit):
        L.build_parser().parse_args(["--stop", "--emergency-stop"])



def test_a_frozen_friday_uses_the_browsers_playwright_installed_for_the_user(tmp_path, monkeypatch):
    # Chrome test of FRIDAY.exe, 2026-10-05: "browser open - unavailable",
    # BROWSER_BINARY_MISSING, while chromium-1217 sat in %LOCALAPPDATA%\\ms-playwright.
    (tmp_path / "ms-playwright" / "chromium-1217").mkdir(parents=True)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.delenv("PLAYWRIGHT_BROWSERS_PATH", raising=False)
    assert L.use_installed_browsers(is_frozen=False) == ""          # a source run is Playwright's own default
    assert "PLAYWRIGHT_BROWSERS_PATH" not in os.environ
    assert L.use_installed_browsers(is_frozen=True) == str(tmp_path / "ms-playwright")
    assert os.environ["PLAYWRIGHT_BROWSERS_PATH"] == str(tmp_path / "ms-playwright")


def test_an_explicit_browsers_path_or_a_missing_folder_is_left_alone(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))                # no ms-playwright here
    monkeypatch.delenv("PLAYWRIGHT_BROWSERS_PATH", raising=False)
    assert L.use_installed_browsers(is_frozen=True) == ""
    assert "PLAYWRIGHT_BROWSERS_PATH" not in os.environ
    (tmp_path / "ms-playwright").mkdir()
    monkeypatch.setenv("PLAYWRIGHT_BROWSERS_PATH", "D:/my-browsers")
    assert L.use_installed_browsers(is_frozen=True) == ""
    assert os.environ["PLAYWRIGHT_BROWSERS_PATH"] == "D:/my-browsers"



def test_bypass_login_is_the_bypass_face_switch_by_the_name_the_owner_asked_for():
    for flag in ("--bypass-login", "--bypass-face"):
        assert L.build_parser().parse_args([flag]).bypass_face is True, flag
