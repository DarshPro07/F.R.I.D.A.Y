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


def test_sandbox_is_detected_by_its_executable(tmp_path):
    assert not clean.sandbox_available(str(tmp_path))

    (tmp_path / "System32").mkdir()
    (tmp_path / "System32" / "WindowsSandbox.exe").write_bytes(b"")

    assert clean.sandbox_available(str(tmp_path))
