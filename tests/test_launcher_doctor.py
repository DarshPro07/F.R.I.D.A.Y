"""
The doctor's later sections (skills, voice, security, Hermes version) and
the debug variant of the launcher.
"""
from __future__ import annotations

import sys

import friday.config  # noqa: F401  (.env loaded before the doctor reads names)
from friday import launcher as L


def test_doctor_reports_skills_voice_and_security_sections(tmp_path, monkeypatch):
    monkeypatch.setenv("ADA_DB", str(tmp_path / "doctor.sqlite3"))
    # This machine's .env may hold a refused key, which is a voice FAIL by design.
    for name in (*L.REQUIRED_FOR_AGENT, *L.provider_credential_names()):
        monkeypatch.delenv(name, raising=False)
    root = tmp_path / "root"
    (root / "ui").mkdir(parents=True)
    (root / "ui" / "index.html").write_text("", encoding="utf-8")
    by_name = {name: (state, detail) for name, state, detail in L.doctor_checks(root=root, env_path=None)}
    assert by_name["skills"][0] in (L.PASS, L.DEGRADED)           # never FAIL from source
    assert "on disk" in by_name["skills"][1] and "registered" in by_name["skills"][1]
    assert by_name["voice"][0] in (L.PASS, L.DEGRADED)
    assert "stt" in by_name["voice"][1] and "tts" in by_name["voice"][1]
    assert by_name["security"][0] == L.PASS
    assert "kernel modules present" in by_name["security"][1]
    assert "hermes" in by_name


def test_skills_check_degrades_when_skills_are_on_disk_but_unregistered(tmp_path, monkeypatch):
    monkeypatch.setenv("ADA_DB", str(tmp_path / "doctor.sqlite3"))
    name, state, detail = L.skills_check(tmp_path)[0]
    assert name == "skills"
    # the repository ships friday-operate (built-in) and friday-windows-exe (project)
    assert state == L.DEGRADED and "unregistered" in detail and "friday-operate" in detail


def test_startup_registers_the_skills_on_disk_so_the_doctor_finds_none_unregistered(tmp_path, monkeypatch):
    """The 2026-10-04 defect: friday-operate was on disk and never registered."""
    monkeypatch.setenv("ADA_DB", str(tmp_path / "doctor.sqlite3"))
    assert "friday-operate" in L.skills_check(tmp_path)[0][2]          # unregistered before
    note = L.register_skills_at_startup()
    assert note.startswith("skills: registered") and "friday-operate" in note
    name, state, detail = L.skills_check(tmp_path)[0]
    assert "unregistered" not in detail
    assert L.register_skills_at_startup() == ""                         # nothing left to do


def test_registering_at_startup_leaves_an_already_registered_skill_untouched(tmp_path):
    from friday.skills import cli, files

    services = cli.Services(str(tmp_path / "skills.sqlite3"), files.tier_roots())
    first = cli.register_unregistered(services)
    assert first and all(state for _, _, state in first)
    before = {record.name: (record.state, record.tier) for record in services.registry.list()}
    assert cli.register_unregistered(services) == []
    assert {record.name: (record.state, record.tier) for record in services.registry.list()} == before


def test_a_registry_that_cannot_open_is_reported_not_raised(monkeypatch):
    from friday.skills import cli

    def broken(*args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(cli, "Services", broken)
    note = L.register_skills_at_startup()
    assert "could not register" in note and "OSError" in note and "sync" in note


def test_debug_mode_is_read_from_the_executable_name_or_the_environment(monkeypatch):
    monkeypatch.delenv("FRIDAY_DEBUG", raising=False)
    monkeypatch.setattr(sys, "executable", "C:/apps/FRIDAY/FRIDAY-Debug.exe")
    assert L.debug_mode()
    monkeypatch.setattr(sys, "executable", "C:/apps/FRIDAY/FRIDAY.exe")
    assert not L.debug_mode()
    monkeypatch.setenv("FRIDAY_DEBUG", "1")
    assert L.debug_mode()


def test_hermes_version_never_raises(tmp_path):
    assert "unknown" in L.hermes_version(str(tmp_path / "absent-hermes.exe"))


def test_security_check_flags_credentials_inside_a_packaged_application(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(tmp_path / "dist" / "FRIDAY.exe"))
    monkeypatch.setattr(L, "app_root", lambda: tmp_path / "dist" / "_internal")
    monkeypatch.delenv("PORTABLE_MODE", raising=False)
    (tmp_path / "dist" / "_internal").mkdir(parents=True)
    inside = tmp_path / "dist" / "_internal" / ".env"
    inside.write_text("X=1\n", encoding="utf-8")
    name, state, detail = L.security_check(tmp_path, inside)
    assert state == L.DEGRADED and "inside the application directory" in detail
    outside = tmp_path / "profile" / "config" / ".env"
    outside.parent.mkdir(parents=True)
    outside.write_text("X=1\n", encoding="utf-8")
    assert L.security_check(tmp_path, outside)[1] == L.PASS
