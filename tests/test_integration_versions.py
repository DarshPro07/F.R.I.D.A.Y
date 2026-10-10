"""CAP-FORGE-010: updates keep a last-known-good integration and roll back
automatically. Every test injects the test runner and the health check; no
real repository, no network."""
import json
from pathlib import Path

import pytest

from friday import integration_versions as iv


def _source(tmp_path: Path, label: str, body: str = "x = 1\n") -> Path:
    src = tmp_path / "src" / label
    src.mkdir(parents=True)
    (src / "mod.py").write_text(body, encoding="utf-8")
    return src


def _ok(path: Path) -> iv.Check:
    return iv.Check(True, "ok")


def _fail(path: Path) -> iv.Check:
    return iv.Check(False, "3 tests failed")


@pytest.fixture
def versions(tmp_path):
    return iv.IntegrationVersions(tmp_path / "integrations")


def _promote(versions, tmp_path, version, label=None):
    return versions.update("acme", version, _source(tmp_path, label or version),
                           run_tests=_ok, health_check=_ok)


def test_first_good_update_becomes_current_and_known_good(versions, tmp_path):
    result = _promote(versions, tmp_path, "1.0.0")
    assert result.status == iv.PROMOTED
    assert versions.known_good("acme") == "1.0.0"
    assert (versions.current("acme") / "mod.py").read_text() == "x = 1\n"


def test_new_version_is_installed_beside_the_old_one(versions, tmp_path):
    _promote(versions, tmp_path, "1.0.0")
    old_dir = versions.current("acme")
    _promote(versions, tmp_path, "1.1.0")
    assert old_dir.exists()
    assert versions.current("acme") != old_dir
    assert versions.known_good("acme") == "1.1.0"


def test_the_pointer_does_not_move_until_tests_pass(versions, tmp_path):
    _promote(versions, tmp_path, "1.0.0")
    seen = []

    def runner(path):
        seen.append(versions.known_good("acme"))
        seen.append(versions.current("acme").name)
        return iv.Check(True, "ok")

    versions.update("acme", "1.1.0", _source(tmp_path, "b"),
                    run_tests=runner, health_check=_ok)
    assert seen == ["1.0.0", "1.0.0"]


def test_failed_tests_roll_back_and_mark_the_version_bad(versions, tmp_path):
    _promote(versions, tmp_path, "1.0.0")
    result = versions.update("acme", "1.1.0", _source(tmp_path, "b"),
                             run_tests=_fail, health_check=_ok)
    assert result.status == iv.ROLLED_BACK
    assert result.stage == "tests"
    assert "3 tests failed" in result.reason
    assert versions.current("acme").name == "1.0.0"
    assert versions.known_good("acme") == "1.0.0"
    assert versions.is_bad("acme", "1.1.0")
    assert "3 tests failed" in versions.bad_versions("acme")["1.1.0"]["reason"]


def test_failed_health_check_rolls_back_after_the_switch(versions, tmp_path):
    _promote(versions, tmp_path, "1.0.0")
    switched = []

    def health(path):
        switched.append(versions.current("acme").name)
        return iv.Check(False, "port 8080 not answering")

    result = versions.update("acme", "1.1.0", _source(tmp_path, "b"),
                             run_tests=_ok, health_check=health)
    assert switched == ["1.1.0"]
    assert result.status == iv.ROLLED_BACK
    assert result.stage == "health"
    assert versions.current("acme").name == "1.0.0"
    assert versions.known_good("acme") == "1.0.0"
    assert versions.is_bad("acme", "1.1.0")


def test_a_bad_version_is_not_retried(versions, tmp_path):
    _promote(versions, tmp_path, "1.0.0")
    versions.update("acme", "1.1.0", _source(tmp_path, "b"),
                    run_tests=_fail, health_check=_ok)
    calls = []

    def runner(path):
        calls.append(path)
        return iv.Check(True, "ok")

    result = versions.update("acme", "1.1.0", _source(tmp_path, "c"),
                             run_tests=runner, health_check=_ok)
    assert result.status == iv.REJECTED
    assert result.stage == "known_bad"
    assert calls == []
    assert versions.current("acme").name == "1.0.0"


def test_a_raising_runner_is_a_failed_update_with_the_reason_recorded(versions, tmp_path):
    _promote(versions, tmp_path, "1.0.0")

    def boom(path):
        raise RuntimeError("runner crashed")

    result = versions.update("acme", "1.1.0", _source(tmp_path, "b"),
                             run_tests=boom, health_check=_ok)
    assert result.status == iv.ROLLED_BACK
    assert "RuntimeError" in result.reason and "runner crashed" in result.reason
    assert versions.current("acme").name == "1.0.0"
    assert versions.is_bad("acme", "1.1.0")


def test_first_install_failing_leaves_no_current_version(versions, tmp_path):
    result = versions.update("acme", "1.0.0", _source(tmp_path, "a"),
                             run_tests=_fail, health_check=_ok)
    assert result.status == iv.ROLLED_BACK
    assert versions.current("acme") is None
    assert versions.known_good("acme") is None


def test_explicit_rollback_returns_to_the_previous_good_version(versions, tmp_path):
    _promote(versions, tmp_path, "1.0.0")
    _promote(versions, tmp_path, "1.1.0")
    assert versions.rollback("acme", "users report errors") == "1.0.0"
    assert versions.current("acme").name == "1.0.0"
    assert versions.known_good("acme") == "1.0.0"
    assert versions.is_bad("acme", "1.1.0")


def test_a_rising_failure_rate_rolls_back_without_the_person_acting(versions, tmp_path):
    _promote(versions, tmp_path, "1.0.0")
    _promote(versions, tmp_path, "1.1.0")
    rolled = [versions.observe("acme", "1.1.0", ok, window=10, min_samples=5,
                               max_failure_rate=0.5)
              for ok in (True, False, False, False, False)]
    assert rolled == [False, False, False, False, True]
    assert versions.current("acme").name == "1.0.0"
    assert versions.is_bad("acme", "1.1.0")
    assert "failure rate" in versions.bad_versions("acme")["1.1.0"]["reason"]


def test_a_healthy_version_is_not_rolled_back(versions, tmp_path):
    _promote(versions, tmp_path, "1.0.0")
    _promote(versions, tmp_path, "1.1.0")
    rolled = [versions.observe("acme", "1.1.0", ok, window=10, min_samples=5,
                               max_failure_rate=0.5)
              for ok in (True, True, False, True, True, True)]
    assert not any(rolled)
    assert versions.current("acme").name == "1.1.0"


def test_state_survives_a_new_instance(versions, tmp_path):
    _promote(versions, tmp_path, "1.0.0")
    versions.update("acme", "1.1.0", _source(tmp_path, "b"),
                    run_tests=_fail, health_check=_ok)
    again = iv.IntegrationVersions(tmp_path / "integrations")
    assert again.known_good("acme") == "1.0.0"
    assert again.is_bad("acme", "1.1.0")


def test_a_failed_update_leaves_the_known_good_files_untouched(versions, tmp_path):
    _promote(versions, tmp_path, "1.0.0")
    good = versions.current("acme")
    before = (good / "mod.py").read_bytes()
    versions.update("acme", "1.1.0", _source(tmp_path, "b", "x = 2\n"),
                    run_tests=_fail, health_check=_ok)
    assert (good / "mod.py").read_bytes() == before


@pytest.mark.parametrize("bad", ["", "..", "../x", "a/b", "a\\b", ".hidden", "v 1"])
def test_unsafe_names_and_versions_are_refused(versions, tmp_path, bad):
    with pytest.raises(iv.VersionError):
        versions.update(bad, "1.0.0", _source(tmp_path, "a"),
                        run_tests=_ok, health_check=_ok)
    with pytest.raises(iv.VersionError):
        versions.update("acme", bad, _source(tmp_path, "b"),
                        run_tests=_ok, health_check=_ok)


def test_reinstalling_an_existing_version_is_refused(versions, tmp_path):
    _promote(versions, tmp_path, "1.0.0")
    with pytest.raises(iv.VersionError):
        versions.update("acme", "1.0.0", _source(tmp_path, "b"),
                        run_tests=_ok, health_check=_ok)


def test_a_missing_source_is_refused_and_leaves_no_trace(versions, tmp_path):
    with pytest.raises(iv.VersionError):
        versions.update("acme", "1.0.0", tmp_path / "nope",
                        run_tests=_ok, health_check=_ok)
    assert versions.current("acme") is None
    assert not (tmp_path / "integrations" / "acme" / "versions" / "1.0.0").exists()


def test_the_state_file_records_the_history(versions, tmp_path):
    _promote(versions, tmp_path, "1.0.0")
    versions.update("acme", "1.1.0", _source(tmp_path, "b"),
                    run_tests=_fail, health_check=_ok)
    state = json.loads((tmp_path / "integrations" / "acme" / "state.json")
                       .read_text(encoding="utf-8"))
    assert [h["event"] for h in state["history"]] == ["promoted", "rolled_back"]


def test_a_partial_suffixed_version_is_not_clobbered_by_staging(versions, tmp_path):
    # "1.0.0.partial" is a valid version string; staging for "1.0.0" must not
    # share its directory.
    _promote(versions, tmp_path, "1.0.0.partial", "a")
    live = versions.current("acme")
    before = (live / "mod.py").read_bytes()
    result = versions.update("acme", "1.0.0", _source(tmp_path, "b", "x = 9\n"),
                             run_tests=_fail, health_check=_ok)
    assert result.status == iv.ROLLED_BACK
    assert live.exists() and (live / "mod.py").read_bytes() == before
    assert versions.current("acme") == live
    # and a successful update beside it leaves it intact too
    _promote(versions, tmp_path, "1.0.1", "c")
    assert live.exists() and (live / "mod.py").read_bytes() == before


def test_a_crash_during_the_health_check_is_rolled_back_on_restart(versions, tmp_path):
    _promote(versions, tmp_path, "1.0.0")

    def dies(path):
        raise SystemExit("power loss")

    with pytest.raises(SystemExit):
        versions.update("acme", "1.1.0", _source(tmp_path, "b"),
                        run_tests=_ok, health_check=dies)
    fresh = iv.IntegrationVersions(tmp_path / "integrations")
    assert fresh.current("acme").name == "1.0.0"
    assert fresh.known_good("acme") == "1.0.0"
    assert fresh.is_bad("acme", "1.1.0")
    assert "interrupted" in fresh.bad_versions("acme")["1.1.0"]["reason"]
    state = json.loads((tmp_path / "integrations" / "acme" / "state.json")
                       .read_text(encoding="utf-8"))
    assert state.get("pending") is None


def test_a_crash_during_the_tests_marks_the_version_bad_on_restart(versions, tmp_path):
    _promote(versions, tmp_path, "1.0.0")

    def dies(path):
        raise KeyboardInterrupt()

    with pytest.raises(KeyboardInterrupt):
        versions.update("acme", "1.1.0", _source(tmp_path, "b"),
                        run_tests=dies, health_check=_ok)
    fresh = iv.IntegrationVersions(tmp_path / "integrations")
    assert fresh.current("acme").name == "1.0.0"
    assert fresh.is_bad("acme", "1.1.0")


def test_a_crashed_first_install_leaves_no_current_version(versions, tmp_path):
    def dies(path):
        raise SystemExit("killed")

    with pytest.raises(SystemExit):
        versions.update("acme", "1.0.0", _source(tmp_path, "a"),
                        run_tests=_ok, health_check=dies)
    fresh = iv.IntegrationVersions(tmp_path / "integrations")
    assert fresh.current("acme") is None
    assert fresh.known_good("acme") is None


def test_a_stale_outcome_from_the_old_version_is_ignored(versions, tmp_path):
    _promote(versions, tmp_path, "1.0.0")
    _promote(versions, tmp_path, "1.1.0")
    rolled = [versions.observe("acme", "1.0.0", False, window=10, min_samples=3,
                               max_failure_rate=0.5) for _ in range(6)]
    assert not any(rolled)
    assert versions.current("acme").name == "1.1.0"
    assert not versions.is_bad("acme", "1.1.0")


def test_names_and_versions_are_case_insensitive(versions, tmp_path):
    versions.update("Acme", "V1", _source(tmp_path, "a"),
                    run_tests=_ok, health_check=_ok)
    versions.update("acme", "V2", _source(tmp_path, "b"),
                    run_tests=_fail, health_check=_ok)
    result = versions.update("ACME", "v2", _source(tmp_path, "c"),
                             run_tests=_ok, health_check=_ok)
    assert result.status == iv.REJECTED and result.stage == "known_bad"
    assert versions.is_bad("acme", "V2")
    with pytest.raises(iv.VersionError):
        versions.update("acme", "v1", _source(tmp_path, "d"),
                        run_tests=_ok, health_check=_ok)
    assert versions.current("ACME").name == "v1"
