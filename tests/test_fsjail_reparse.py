"""
The jail against real reparse points, created on the real filesystem.

An absolute root is necessary and not sufficient. A Windows junction is an
alias to another directory - possibly on another volume - and a path under one
is syntactically inside the jail while accessing something outside it. Junctions
are the alias that matters most here because creating one needs **no
administrator rights**, unlike a symlink, so anything that can write next to
the workspace can attempt this.

Measured on this interpreter (3.11, Windows) rather than assumed:

    Path.resolve() follows a junction               yes
    ... including for a path that does not exist    yes
    ... including nested junctions                  yes
    Path.is_symlink() on a junction                 FALSE   <- the trap
    st_file_attributes & REPARSE_POINT              True

So containment via a resolved path already refuses these, and the tests below
prove it rather than trusting it. The one case that would otherwise *succeed*
is the root itself being a junction, because resolving it silently moves the
whole boundary - that fails closed now.

WHAT THIS IS NOT. fsjail is a capability guard, not a security boundary
against hostile arbitrary Python. Anything holding `os`, `pathlib`, `ctypes`
or the Win32 APIs simply does not call it. There is also a TOCTOU window: a
path validated as inside can have a junction swapped underneath it before it
is opened. Both are why Forge gives generated code a restricted capability
API instead of an interpreter, and why the jail is one layer rather than the
answer.
"""

from __future__ import annotations

import os
import subprocess

import pytest

from friday.fsjail import FileJail, JailError, is_reparse_point

pytestmark = pytest.mark.skipif(os.name != "nt",
                                reason="junctions are a Windows construct")


def junction(link, target) -> bool:
    """Create a directory junction. No administrator rights required."""
    return subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(link), str(target)],
        capture_output=True, text=True).returncode == 0


def symlink(link, target) -> bool:
    """Create a directory symlink. Needs admin or Developer Mode; may fail."""
    try:
        link.symlink_to(target, target_is_directory=True)
        return True
    except (OSError, NotImplementedError):
        return False


@pytest.fixture
def world(tmp_path):
    """A jail, and a secret sitting outside it."""
    root = tmp_path / "jail"
    (root / "inner").mkdir(parents=True)
    (root / "inner" / "real.txt").write_text("allowed", encoding="utf-8")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.txt").write_text("leaked", encoding="utf-8")
    return type("World", (), {
        "root": root, "outside": outside, "jail": FileJail((root,))})()


def refused(jail, path) -> bool:
    try:
        jail.resolve(str(path))
    except JailError:
        return True
    return False


# ---------------------------------------------------------------------------
# 1-2: the classics
# ---------------------------------------------------------------------------


def test_dotdot_escape_is_refused(world):
    assert refused(world.jail, world.root / ".." / "outside" / "secret.txt")


def test_an_absolute_path_outside_is_refused(world):
    assert refused(world.jail, world.outside / "secret.txt")


def test_a_real_file_inside_is_allowed(world):
    """Gate 10. A jail that refuses everything is not evidence of anything."""
    got = world.jail.resolve(str(world.root / "inner" / "real.txt"))
    assert got.read_text(encoding="utf-8") == "allowed"


# ---------------------------------------------------------------------------
# 3-7: reparse points
# ---------------------------------------------------------------------------


def test_a_junction_pointing_out_of_the_jail_is_refused(world):
    """Gate 4. The one that needs no privileges to attempt."""
    link = world.root / "escape"
    assert junction(link, world.outside), "could not create the junction"
    assert is_reparse_point(link), "the junction was not detected as one"
    assert not link.is_symlink(), "is_symlink now works; the trap has changed"
    assert refused(world.jail, link / "secret.txt")


def test_detection_survives_the_python_312_upgrade(tmp_path):
    """
    `Path.is_junction()` arrived in 3.12 and this runtime is 3.11, so the
    attribute check is what runs today. Whichever is available must reach the
    same answer, or the upgrade quietly changes the security behaviour.
    """
    from pathlib import Path

    real, link = tmp_path / "real", tmp_path / "link"
    real.mkdir()
    assert junction(link, real)

    assert is_reparse_point(link) is True
    assert is_reparse_point(real) is False
    if hasattr(Path, "is_junction"):
        assert link.is_junction() is True, \
            "is_junction disagrees with the attribute check"


def test_a_symlink_pointing_out_of_the_jail_is_refused(world):
    """Gate 3. Skipped rather than faked where the OS will not allow one."""
    link = world.root / "slink"
    if not symlink(link, world.outside):
        pytest.skip("symlink creation needs admin or Developer Mode")
    assert refused(world.jail, link / "secret.txt")


def test_a_nested_junction_is_refused(world):
    """Gate 6. Depth is not a way around resolution."""
    link = world.root / "inner" / "deep"
    assert junction(link, world.outside)
    assert refused(world.jail, link / "secret.txt")


def test_a_path_that_does_not_exist_yet_under_a_junction_is_refused(world):
    """
    Gate 7. The dangerous one for writes: nothing exists to stat, so a check
    that gave up on nonexistent paths would let a create land outside.
    """
    link = world.root / "escape"
    assert junction(link, world.outside)
    assert refused(world.jail, link / "newdir" / "newfile.txt")


def test_a_junction_to_another_volume_is_refused(world, tmp_path):
    """Gate 5. Junctions may cross local volumes."""
    other = None
    for drive in ("C:\\", "D:\\", "E:\\"):
        if os.path.exists(drive) and not str(world.root).upper().startswith(drive):
            other = drive
            break
    if other is None:
        pytest.skip("no second local volume to point at")
    link = world.root / "volume"
    if not junction(link, other):
        pytest.skip(f"could not create a junction to {other}")
    assert refused(world.jail, link / "Windows" / "win.ini")


def test_a_junction_pointing_back_inside_the_jail_is_still_allowed(world):
    """
    The other half of gate 4: resolution must not refuse everything that is a
    link. A junction whose target is inside the jail is not an escape.
    """
    link = world.root / "loop"
    assert junction(link, world.root / "inner")
    got = world.jail.resolve(str(link / "real.txt"))
    assert got.read_text(encoding="utf-8") == "allowed"


# ---------------------------------------------------------------------------
# 8: the root itself
# ---------------------------------------------------------------------------


def test_a_jail_root_that_is_a_reparse_point_fails_closed(tmp_path):
    """
    Gate 8, and the only case that would otherwise succeed silently: resolving
    a junction root relocates the entire boundary to its target, and every
    later containment check passes against the new location.
    """
    real = tmp_path / "real_root"
    real.mkdir()
    fake = tmp_path / "jail"
    assert junction(fake, real), "could not create the junction"

    with pytest.raises(JailError, match="reparse point"):
        FileJail((fake,))


def test_a_root_that_is_an_ordinary_directory_starts_normally(tmp_path):
    root = tmp_path / "ordinary"
    root.mkdir()
    assert FileJail((root,)).roots == (root.resolve(),)


# ---------------------------------------------------------------------------
# 9: normalisation, and the string-prefix trap
# ---------------------------------------------------------------------------


def test_case_variations_reach_the_same_verdict(world):
    """Windows paths are case-insensitive; the answer must not depend on it."""
    target = world.root / "inner" / "real.txt"
    for variant in (str(target), str(target).upper(), str(target).lower()):
        assert world.jail.resolve(variant)
    for variant in (str(world.outside / "secret.txt").upper(),
                    str(world.outside / "secret.txt").lower()):
        assert refused(world.jail, variant)


def test_a_sibling_whose_name_starts_with_the_root_is_refused(tmp_path):
    """
    The reason containment is `is_relative_to` and never `startswith`:

        root      ...\\workspace
        candidate ...\\workspace2\\secret.txt

    is not inside the root, and passes a naive string prefix comparison.
    """
    root = tmp_path / "workspace"
    root.mkdir()
    sibling = tmp_path / "workspace2"
    sibling.mkdir()
    (sibling / "secret.txt").write_text("leaked", encoding="utf-8")

    jail = FileJail((root,))
    assert str(sibling).startswith(str(root)), "the trap is not set up correctly"
    assert refused(jail, sibling / "secret.txt")


def test_forward_and_back_slashes_reach_the_same_verdict(world):
    target = world.root / "inner" / "real.txt"
    assert world.jail.resolve(str(target).replace("\\", "/"))
    assert refused(world.jail, str(world.outside / "secret.txt").replace("\\", "/"))
