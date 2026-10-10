"""CAP-FORGE-007: the install and update path (integration_versions.update)
runs forge_journeys.complete() before it admits an integration. An install
whose journey does not pass, or cannot be observed, is never admitted; a
failed journey after an update rolls back to the last known-good version.

Fixture capabilities live in tmp dirs. What a capability does is decided by
the files in its INSTALLED version directory, so the tests show the journey
runs against the installed copy and not against some stand-in."""
from __future__ import annotations

from pathlib import Path

import pytest

from friday import forge_journeys as fj
from friday import integration_versions as iv


def _ok(path: Path) -> iv.Check:
    return iv.Check(True, "ok")


def _source(tmp_path: Path, label: str, behaviour: str) -> Path:
    src = tmp_path / "src" / f"{label}-{behaviour}"
    src.mkdir(parents=True)
    (src / "behaviour.txt").write_text(behaviour, encoding="utf-8")
    return src


def _file_capability(installed: Path):
    """A filesystem capability whose honesty is read from its installed copy."""
    behaviour = (installed / "behaviour.txt").read_text(encoding="utf-8")

    def call(operation, **args):
        assert operation == "write"
        if behaviour == "honest":
            Path(args["path"]).write_text(args["content"], encoding="utf-8")
        elif behaviour == "crash":
            raise RuntimeError("driver exploded")
        return {"ok": True, "written": args["content"]}   # liar: claims success

    return call


FILES = iv.JourneySpec("filesystem", _file_capability)


@pytest.fixture
def versions(tmp_path):
    return iv.IntegrationVersions(tmp_path / "integrations")


def _install(versions, tmp_path, version, behaviour, journey=FILES):
    return versions.update("acme", version, _source(tmp_path, version, behaviour),
                          run_tests=_ok, health_check=_ok, journey=journey)


def test_an_install_with_a_passing_observed_journey_is_installted(versions, tmp_path):
    result = _install(versions, tmp_path, "1.0.0", "honest")
    assert result.status == iv.PROMOTED, result.reason
    assert versions.known_good("acme") == "1.0.0"
    assert versions.current("acme").name == "1.0.0"
    promoted = versions._load("acme")["history"][-1]
    assert promoted["event"] == "promoted"
    assert "observed" in promoted["reason"]       # the evidence is kept


def test_the_journey_runs_against_the_installed_copy(versions, tmp_path):
    seen = []

    def call_for(installed):
        seen.append(installed)
        return _file_capability(installed)

    _install(versions, tmp_path, "1.0.0", "honest",
           iv.JourneySpec("filesystem", call_for))
    assert seen == [versions.current("acme")]


def test_a_lying_install_that_passes_tests_and_health_is_not_installted(
        versions, tmp_path):
    result = _install(versions, tmp_path, "1.0.0", "liar")
    assert result.status == iv.ROLLED_BACK
    assert result.stage == "journey"
    assert "post-state" in result.reason
    assert result.current is None
    assert versions.current("acme") is None
    assert versions.known_good("acme") is None
    assert versions.is_bad("acme", "1.0.0")


def test_a_failed_journey_after_an_update_rolls_back_to_known_good(
        versions, tmp_path):
    _install(versions, tmp_path, "1.0.0", "honest")
    result = _install(versions, tmp_path, "2.0.0", "liar")
    assert result.status == iv.ROLLED_BACK and result.stage == "journey"
    assert result.current == "1.0.0"
    assert versions.current("acme").name == "1.0.0"
    assert versions.known_good("acme") == "1.0.0"
    assert versions.is_bad("acme", "2.0.0")
    retry = _install(versions, tmp_path, "2.0.0", "honest")
    assert retry.status == iv.REJECTED and retry.stage == "known_bad"


def test_a_capability_that_raises_during_the_journey_is_rolled_back(
        versions, tmp_path):
    _install(versions, tmp_path, "1.0.0", "honest")
    result = _install(versions, tmp_path, "1.1.0", "crash")
    assert result.status == iv.ROLLED_BACK and result.stage == "journey"
    assert "driver exploded" in result.reason
    assert versions.current("acme").name == "1.0.0"


def test_an_unobservable_journey_does_not_install(versions, tmp_path):
    # A desktop driver with no observer cannot be checked by anyone else.
    spec = iv.JourneySpec("desktop_driver", lambda installed: (lambda op, **a: None))
    result = _install(versions, tmp_path, "1.0.0", "honest", spec)
    assert result.status == iv.ROLLED_BACK and result.stage == "journey"
    assert "observer" in result.reason
    assert versions.current("acme") is None


def test_a_desktop_install_is_installted_when_the_display_reads_42(
        versions, tmp_path):
    display = tmp_path / "display.txt"
    display.write_text("", encoding="utf-8")

    def call_for(installed):
        def driver(operation, **args):
            display.write_text(display.read_text(encoding="utf-8") + args["key"],
                               encoding="utf-8")
        return driver

    spec = iv.JourneySpec("desktop_driver", call_for,
                          observer=lambda workdir: display.read_text(encoding="utf-8"))
    result = _install(versions, tmp_path, "1.0.0", "honest", spec)
    assert result.status == iv.PROMOTED, result.reason
    assert display.read_text(encoding="utf-8") == fj.DESKTOP_DISPLAY


@pytest.mark.parametrize("journey", [None, "filesystem"])
def test_an_install_without_a_journey_is_refused_before_anything_is_installed(
        versions, tmp_path, journey):
    with pytest.raises(iv.VersionError, match="journey"):
        _install(versions, tmp_path, "1.0.0", "honest", journey)
    assert versions.current("acme") is None
    assert not (versions.root / "acme" / "versions").exists()


def test_a_crash_inside_the_journey_leaves_nothing_live(versions, tmp_path):
    def call_for(installed):
        raise SystemExit("killed")

    spec = iv.JourneySpec("filesystem", call_for)
    with pytest.raises(SystemExit):
        _install(versions, tmp_path, "1.0.0", "honest", spec)
    # the next load finds the pending marker and withdraws the version
    assert versions.current("acme") is None
    assert versions.is_bad("acme", "1.0.0")


def test_update_cannot_install_a_version_without_a_journey(versions, tmp_path):
    """The one public install path requires a journey; omitting it, or passing
    something that is not a JourneySpec, never reaches known_good or PROMOTED."""
    src = _source(tmp_path, "1.0.0", "honest")
    with pytest.raises((TypeError, iv.VersionError)):
        versions.update("acme", "1.0.0", src, run_tests=_ok, health_check=_ok)
    with pytest.raises(iv.VersionError, match="journey"):
        versions.update("acme", "1.0.0", src, run_tests=_ok, health_check=_ok,
                        journey="filesystem")
    assert versions.known_good("acme") is None
    assert versions.current("acme") is None
    assert not (versions.root / "acme" / "versions").exists()
    assert not hasattr(versions, "admit")      # no second, journey-less route


def test_a_rollback_only_ever_lands_on_a_version_that_passed_a_journey(
        versions, tmp_path):
    _install(versions, tmp_path, "1.0.0", "honest")
    _install(versions, tmp_path, "2.0.0", "honest")
    result = _install(versions, tmp_path, "3.0.0", "liar")
    assert result.current == "2.0.0"
    state = versions._load("acme")
    promoted = [e["version"] for e in state["history"] if e["event"] == "promoted"]
    assert state["good"] == promoted == ["1.0.0", "2.0.0"]


def test_the_promotion_keeps_what_was_expected_and_what_was_observed(
        versions, tmp_path):
    import json
    _install(versions, tmp_path, "1.0.0", "honest")
    evidence = json.loads(versions._load("acme")["history"][-1]["reason"])
    assert evidence["class"] == "filesystem"
    assert evidence["expected"] and evidence["observed"] == evidence["expected"]
