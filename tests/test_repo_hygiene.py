"""
Repository hygiene invariants (audit A-007 / A-017; PRD Requirement 23).

    WHEN secrets are detected in tracked code or history, THEN production
    readiness SHALL fail unless the finding is a documented deliberate fake.
    WHEN a test uses fake secrets, THEN the allowlist SHALL be specific and
    auditable.

The scanner's two exception files are the only places a finding can be
waved through, so they are pinned here: every allow-listed path must exist
and be a test or public asset, and the fingerprint ignore list must be
exactly the one rotated, documented historical key - nothing added without
this test changing in the same commit, in review.
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

#: The single historical finding gitleaks may ignore: the companion key
#: committed in the initial snapshot, untracked in d1751bb and ROTATED the
#: same day (docs/architecture/AUDIT_2026-09-05_TRIAGE.md, "Secret scan
#: result"). A history rewrite is the owner's deferred decision.
ROTATED_KEY_FINGERPRINT = "5b9cd75df2f91a457ed0248fb3179d359ff69a03:data/companion/extension_key.pem:private-key:1"


def _uncommented(path: Path) -> list[str]:
    return [line.strip() for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.lstrip().startswith("#")]


def test_the_gitleaks_ignore_list_is_exactly_the_one_rotated_key():
    entries = _uncommented(ROOT / ".gitleaksignore")
    assert entries == [ROTATED_KEY_FINGERPRINT], entries


def test_the_ignored_finding_is_a_documented_rotation_not_a_live_secret():
    """The blob in history must not be the key the bridge trusts today."""
    triage = (ROOT / "docs/architecture/AUDIT_2026-09-05_TRIAGE.md").read_text(encoding="utf-8")
    assert "extension_key.pem" in triage and "rotated" in triage.lower()
    live = ROOT / "data/companion/extension_key.pem"
    if not live.exists():
        return  # a fresh checkout has no pairing material at all; nothing to compare
    old = subprocess.run(["git", "-C", str(ROOT), "show", "5b9cd75:data/companion/extension_key.pem"],
                         capture_output=True, text=True)
    if old.returncode != 0:
        return  # shallow clone without that commit; the fingerprint pin above still holds
    assert old.stdout.strip() != live.read_text(encoding="utf-8").strip(), \
        "the ignored historical key is STILL the live companion key - rotate it, do not ignore it"


def test_the_key_file_is_not_tracked_and_is_ignored():
    tracked = subprocess.run(["git", "-C", str(ROOT), "ls-files", "data/companion"],
                             capture_output=True, text=True).stdout.split()
    assert tracked == [], tracked
    ignored = subprocess.run(["git", "-C", str(ROOT), "check-ignore", "-q", "data/companion/extension_key.pem"])
    assert ignored.returncode == 0, "data/companion/extension_key.pem is not gitignored"


def _allowlist() -> dict:
    """The [allowlist] table, parsed as TOML - not sliced by hand. A
    hand-slice on `]` stopped at the first character class inside a planted
    regex and hid it from the guard (found writing this test's red check)."""
    import tomllib
    return tomllib.loads((ROOT / ".gitleaks.toml").read_text(encoding="utf-8"))["allowlist"]


def test_every_allowlisted_path_exists_and_is_a_test_or_public_asset():
    """An allowlist entry for a path that no longer exists is a stale
    exception waiting for a real secret to land under that name."""
    paths = _allowlist()["paths"]
    assert paths, "the allowlist has no paths"
    for pattern in paths:
        path = ROOT / pattern.replace("\\.", ".")
        assert path.exists(), f"allow-listed path does not exist: {pattern}"
        assert path.parts[len(ROOT.parts)] in ("tests", "friday", "third_party"), pattern
        if path.parts[len(ROOT.parts)] == "friday":
            assert path.name == "manifest.json", f"only the MV3 public-key manifest may be allow-listed under friday/: {pattern}"


def test_the_allowlisted_regexes_are_obviously_fake():
    """The three placeholders the redaction tests feed in: an alphanumeric
    dummy and two `sk-...` shapes with a literal ellipsis in the middle.
    Anything that could match a REAL token (a full-length key shape, a
    bare prefix like `sk-`) must not be allow-listed by regex."""
    regexes = _allowlist()["regexes"]
    assert regexes
    for rx in regexes:
        literal = rx.replace("\\.", ".")
        assert literal == "abc123xyz789" or re.fullmatch(r"sk-[a-z0-9]{3}\.\.\.[0-9]{4}", literal), \
            f"allow-listed pattern is not an obvious placeholder: {rx}"
