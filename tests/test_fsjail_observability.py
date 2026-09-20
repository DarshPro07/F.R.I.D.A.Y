"""AT-10 / AT-11 — the jail is diagnosable and the boundary is real.

AT-10: every decision leaves one INFO line on `friday.fsjail` with raw ->
expanded -> resolved -> matched root -> decision/reason and the roots; a
refusal keeps the path (never the content) and a typed reason.
AT-11: containment on Windows: a sibling directory that shares a root's name
as a prefix (`Desktop-old`), `..` traversal, case variants, `%USERPROFILE%`
/ `$HOME` spellings, and a symlink escaping a root.

Negative controls: the log plant proves file CONTENT never reaches the log;
a path just inside the root is allowed (the refusals are not blanket).
"""
from __future__ import annotations

import logging
import os
from pathlib import Path

import pytest

from friday import fsjail as J


@pytest.fixture
def roots(tmp_path):
    root = tmp_path / "Desktop"
    root.mkdir()
    (tmp_path / "Desktop-old").mkdir()          # sibling sharing the prefix
    (tmp_path / "Desktop2").mkdir()
    return tmp_path, root


@pytest.fixture
def jail(roots):
    _, root = roots
    return J.FileJail(roots=(root,))


def _refusal(jail, raw) -> J.JailError:
    with pytest.raises(J.JailError) as info:
        jail.resolve(raw)
    return info.value


# --------------------------------------------------------------------------
# AT-11 containment
# --------------------------------------------------------------------------

def test_inside_root_is_allowed_and_matched_root_is_traced(jail, roots, caplog):
    _, root = roots
    caplog.set_level(logging.INFO, logger="friday.fsjail")
    out = jail.resolve(str(root / "jarvis-test.txt"))
    assert out == (root / "jarvis-test.txt").resolve()
    line = [r.getMessage() for r in caplog.records if "fsjail allow" in r.getMessage()][-1]
    assert "decision='allowed'" in line and f"matched_root={str(root.resolve())!r}" in line


def test_sibling_with_root_as_prefix_is_outside(jail, roots):
    tmp, _ = roots
    err = _refusal(jail, str(tmp / "Desktop-old" / "x.txt"))
    assert err.reason == "outside_roots"
    err = _refusal(jail, str(tmp / "Desktop2" / "x.txt"))
    assert err.reason == "outside_roots"


def test_dotdot_traversal_out_of_root_is_outside(jail, roots):
    _, root = roots
    err = _refusal(jail, str(root / ".." / "Desktop-old" / "x.txt"))
    assert err.reason == "outside_roots"
    assert err.trace["resolved"].endswith(os.path.join("Desktop-old", "x.txt"))


@pytest.mark.skipif(os.name != "nt", reason="case-insensitive containment is a Windows property")
def test_case_variant_of_root_is_inside_on_windows(jail, roots):
    _, root = roots
    variant = str(root).replace("Desktop", "DESKTOP")
    out = jail.resolve(variant + os.sep + "x.txt")
    assert out.parent.resolve() == root.resolve()


def test_userprofile_and_home_spellings_expand_before_containment(jail, roots, monkeypatch):
    tmp, root = roots
    monkeypatch.setenv("USERPROFILE", str(tmp))
    monkeypatch.setenv("HOME", str(tmp))
    out = jail.resolve(r"%USERPROFILE%\Desktop\note.txt" if os.name == "nt" else "$HOME/Desktop/note.txt")
    assert out == (root / "note.txt").resolve()
    err = _refusal(jail, r"%USERPROFILE%\Desktop-old\note.txt" if os.name == "nt" else "$HOME/Desktop-old/note.txt")
    assert err.reason == "outside_roots"
    assert "Desktop-old" in err.trace["expanded"]        # the expansion is what was judged


def test_symlink_inside_root_pointing_out_is_refused(jail, roots):
    tmp, root = roots
    outside = tmp / "elsewhere"
    outside.mkdir()
    link = root / "escape"
    try:
        os.symlink(outside, link, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("symlink creation not permitted here")
    err = _refusal(jail, str(link / "x.txt"))
    assert err.reason == "outside_roots"


# --------------------------------------------------------------------------
# AT-10 observability
# --------------------------------------------------------------------------

def test_refusal_log_line_carries_every_stage_and_no_content(jail, roots, caplog):
    tmp, _ = roots
    caplog.set_level(logging.INFO, logger="friday.fsjail")
    secret = "THE-CONTENT-THAT-MUST-NOT-BE-LOGGED"
    target = tmp / "Desktop-old" / "x.txt"
    target.write_text(secret, encoding="utf-8")
    err = _refusal(jail, str(target))
    line = [r.getMessage() for r in caplog.records if "fsjail refuse" in r.getMessage()][-1]
    for key in ("decision='refused'", "reason='outside_roots'", "raw=", "expanded=", "resolved=", "roots="):
        assert key in line, line
    assert secret not in line                               # negative control: no content
    assert err.trace["decision"] == "refused"
    assert set(err.trace) >= {"raw", "expanded", "resolved", "roots", "reason"}


def test_every_reason_is_typed_and_distinct(jail, tmp_path):
    seen = {}
    seen["empty"] = _refusal(jail, "   ").reason
    seen["null_byte"] = _refusal(jail, "a\x00b").reason
    if os.name == "nt":
        seen["ads_colon"] = _refusal(jail, r"notes.txt:hidden").reason
    seen["unc_or_device"] = _refusal(jail, r"\\server\share\x").reason
    seen["denylisted"] = _refusal(jail, str(jail.roots[0] / ".env")).reason
    seen["outside_roots"] = _refusal(jail, str(tmp_path / "Desktop-old" / "x")).reason
    for want, got in seen.items():
        assert got == want, (want, got)
    assert set(seen) <= set(J.REASONS)


def test_files_toolset_refusal_keeps_the_path_and_reason(roots, monkeypatch):
    from friday import contracts as c
    from friday.toolsets import files as F
    tmp, root = roots
    monkeypatch.setattr(F, "_jail", J.FileJail(roots=(root,)))
    run = c.Run.create("files", capability="files_read")
    started = c.started(run.run_id, "files_read")
    path, failed = F._safe(run, started, str(tmp / "Desktop-old" / "x.txt"))
    assert path is None and failed is not None
    assert "path refused (outside_roots)" in failed.error
    assert "Desktop-old" in failed.error                    # the path survives into the result
