"""The clean-machine validator must accept only the first-run doctor failure."""

import json
import subprocess
import sys
from pathlib import Path

from scripts import validate_exe_clean as clean

PASSING = "  PASS     core imports  ok\n"


class _Stack:
    """Stands in for the launcher process CLEAN-04 starts: alive until stopped."""

    def poll(self):
        return None

    def wait(self, timeout=None):
        return 0

    def kill(self):
        pass


def _run_main(monkeypatch, tmp_path, capsys, version_line, head):
    """Drive main() against a stub bundle whose --version answers `version_line`;
    returns (exit code, stdout). Only the process/copy boundary is replaced, so
    the wiring between the version answer and the printed verdict is real."""
    dist, base = tmp_path / "dist", tmp_path / "base"
    dist.mkdir()
    base.mkdir()
    (dist / "FRIDAY.exe").write_bytes(b"")
    database = base / "profile" / "LocalAppData" / "FRIDAY" / "data" / "ada.sqlite3"

    def fake_run(argv, **kwargs):
        flag = argv[1]
        out = {"--version": version_line, "--status": "no Friday processes"}.get(flag, "")
        if flag == "--paths":
            out = json.dumps({"state_root": str(Path(kwargs["env"]["LOCALAPPDATA"]) / "FRIDAY")})
        elif flag == "--doctor":
            out = PASSING
            database.parent.mkdir(parents=True)
            database.write_bytes(b"")
        return subprocess.CompletedProcess(argv, 0, stdout=out, stderr="")

    monkeypatch.setattr(sys, "argv", ["validate_exe_clean.py", "--dist", str(dist)])
    monkeypatch.setattr(clean, "ROOT", tmp_path)
    monkeypatch.setattr(clean, "RESULTS", [])
    monkeypatch.setattr(clean, "repo_head", lambda: head)
    monkeypatch.setattr(clean, "port_open", lambda port: False)
    monkeypatch.setattr(clean, "http_status", lambda url: 200)
    monkeypatch.setattr(clean, "wait_for", lambda probe, timeout, process=None: True)
    monkeypatch.setattr(clean.tempfile, "mkdtemp", lambda **kwargs: str(base))
    monkeypatch.setattr(clean.shutil, "copytree", lambda src, dst, **kwargs: Path(dst).mkdir())
    monkeypatch.setattr(clean.subprocess, "run", fake_run)
    monkeypatch.setattr(clean.subprocess, "Popen", lambda *args, **kwargs: _Stack())

    code = clean.main()
    return code, capsys.readouterr().out


def test_missing_livekit_credentials_are_the_expected_first_run_failure():
    out = PASSING + "  FAIL     livekit credentials  LIVEKIT_URL missing - the voice worker cannot register\n"

    ok, failed = clean.doctor_verdict(2, out)

    assert ok
    assert len(failed) == 1


def test_any_other_failing_check_fails_the_journey():
    out = PASSING + "  FAIL     state directories  cannot create vault\n"

    ok, failed = clean.doctor_verdict(2, out)

    assert not ok
    assert failed == ["FAIL     state directories  cannot create vault"]


def test_a_failure_that_only_mentions_credentials_in_its_detail_is_not_excused():
    out = PASSING + "  FAIL     voice  worker crashed before livekit credentials were read\n"

    ok, _ = clean.doctor_verdict(2, out)

    assert not ok


def test_a_doctor_that_never_imported_the_core_fails_the_journey():
    out = "  FAIL     core imports  No module named 'friday'\n"

    ok, _ = clean.doctor_verdict(2, out)

    assert not ok


def test_a_doctor_that_printed_nothing_fails_the_journey():
    ok, failed = clean.doctor_verdict(124, "")

    assert not ok
    assert failed == []


def test_a_dist_stamped_at_another_commit_is_called_stale():
    note = clean.stale_dist_note("FRIDAY 0.1.0 (commit 344d62a, dev)", "8feeb03")

    assert note and "344d62a" in note and "8feeb03" in note


def test_a_dist_built_from_a_dirty_tree_is_called_stale_even_at_head():
    note = clean.stale_dist_note("FRIDAY 0.1.0 (commit 8feeb03+dirty, dev)", "8feeb03")

    assert note and "dirty" in note


def test_a_dist_at_head_with_a_clean_tree_is_current():
    assert clean.stale_dist_note("FRIDAY 0.1.0 (commit 8feeb03, dev)", "8feeb03") is None


def test_abbreviations_of_different_length_still_match():
    assert clean.stale_dist_note("FRIDAY 0.1.0 (commit 8feeb03, dev)", "8feeb03a1c") is None


def test_a_version_line_without_a_commit_stamp_cannot_be_called_current():
    assert clean.stale_dist_note("FRIDAY 0.1.0", "8feeb03")


def test_an_unknown_checkout_is_not_judged():
    assert clean.stale_dist_note("FRIDAY 0.1.0 (commit 344d62a, dev)", "") is None


def test_a_stale_dist_is_called_stale_on_the_verdict_line_too(monkeypatch, tmp_path, capsys):
    code, out = _run_main(monkeypatch, tmp_path, capsys, "FRIDAY 0.1.0 (commit 344d62a+dirty, dev)", "4966ddc")

    assert out.count("STALE_DIST") == 2
    verdict = [line for line in out.splitlines() if "clean-machine journeys passed" in line]
    assert len(verdict) == 1 and "STALE_DIST" in verdict[0] and "344d62a" in verdict[0]
    assert code == 0, "a stale build is flagged, it does not fail the journeys"


def test_a_current_dist_carries_no_stale_note(monkeypatch, tmp_path, capsys):
    code, out = _run_main(monkeypatch, tmp_path, capsys, "FRIDAY 0.1.0 (commit 4966ddc, dev)", "4966ddc")

    assert "STALE_DIST" not in out
    assert "6/6 clean-machine journeys passed" in out
    assert code == 0


def test_sandbox_is_detected_by_its_executable(tmp_path):
    assert not clean.sandbox_available(str(tmp_path))

    (tmp_path / "System32").mkdir()
    (tmp_path / "System32" / "WindowsSandbox.exe").write_bytes(b"")

    assert clean.sandbox_available(str(tmp_path))
