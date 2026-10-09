"""The clean-machine validator must accept only the first-run doctor failure."""

from scripts import validate_exe_clean as clean

PASSING = "  PASS     core imports  ok\n"


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


def test_sandbox_is_detected_by_its_executable(tmp_path):
    assert not clean.sandbox_available(str(tmp_path))

    (tmp_path / "System32").mkdir()
    (tmp_path / "System32" / "WindowsSandbox.exe").write_bytes(b"")

    assert clean.sandbox_available(str(tmp_path))
