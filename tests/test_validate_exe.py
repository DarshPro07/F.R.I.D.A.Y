"""The golden-journey validator must run the exe against a scratch profile only."""

from scripts import validate_exe


def test_the_validator_drops_every_variable_that_moves_state(tmp_path):
    # The build harness exports ADA_DB / ADA_AUDIT_DB to keep data/ada.sqlite3
    # out of reach; left in the validator's environment they send the exe's
    # database outside the scratch profile and EXE-12 fails.
    env = {
        "LOCALAPPDATA": "C:/Users/someone/AppData/Local",
        "FRIDAY_HOME": str(tmp_path / "home"),
        "PORTABLE_MODE": "1",
        "ADA_DB": str(tmp_path / "home" / "ada.sqlite3"),
        "ADA_AUDIT_DB": str(tmp_path / "home" / "audit.sqlite3"),
        "PATH": "kept",
    }

    validate_exe.isolate_state(env, tmp_path / "scratch")

    assert env == {"LOCALAPPDATA": str(tmp_path / "scratch"), "PATH": "kept"}
