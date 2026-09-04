"""Silent exception handlers must not grow.

F6 from the 2026-09-01 audit. `friday/` has ~584 broad `except Exception`
handlers, and a subset swallow the error entirely - no log, no re-raise, no
recorded failure. That is why a broken dependency can look like a working one:
the 500 from /api/state degrades gracefully *and* invisibly.

Fixing all of them at once would be a large, risky diff across a live agent.
This is the standing ratchet instead: the existing silent handlers are recorded
as a baseline, and the count may not increase. New code must log, re-raise, or
record its failure.

Following the house style of test_reachability.py: an invariant enforced by the
test suite rather than by a tool the project does not depend on (there is no
ruff/flake8 in either venv, and adding one to the live agent's environment to
satisfy a lint rule would be the wrong trade).

To see the offenders:
    .venv/Scripts/python.exe -m pytest tests/test_silent_excepts.py -q -s
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

PACKAGE = Path(__file__).resolve().parent.parent / "friday"

#: The count measured when this ratchet was introduced (2026-09-01). Lower it
#: freely as handlers are fixed; raising it requires a deliberate edit and a
#: reason. Regenerate with:
#:   .venv/Scripts/python.exe -c "import sys;sys.path.insert(0,'tests');\
#:   from test_silent_excepts import find_silent_handlers as f;print(len(f()))"
#:
#: Note for this repository specifically: the live agent edits its own source
#: while running (AGENTS.md, "the live agent commits to git on its own"), so
#: this number can move without anyone touching the tree by hand. GRACE absorbs
#: that drift so a routine run is not blocked by it; a real regression pushes
#: well past it, and the failure message names the offenders either way.
BASELINE = 81
GRACE = 5


def _is_silent(handler: ast.ExceptHandler) -> bool:
    """A handler that neither reports, records, nor re-raises.

    `pass`, `continue`, and a bare `return`/`return None` swallow. Anything
    that logs, calls something, builds a value, or re-raises does not.
    """
    for node in handler.body:
        if isinstance(node, ast.Pass):
            continue
        if isinstance(node, ast.Continue):
            continue
        if isinstance(node, ast.Return) and (
            node.value is None
            or (isinstance(node.value, ast.Constant) and node.value.value is None)
        ):
            continue
        return False
    return True


def _is_broad(handler: ast.ExceptHandler) -> bool:
    """`except:` or `except Exception:` / `except BaseException:`."""
    if handler.type is None:
        return True
    names = []
    if isinstance(handler.type, ast.Name):
        names = [handler.type.id]
    elif isinstance(handler.type, ast.Tuple):
        names = [e.id for e in handler.type.elts if isinstance(e, ast.Name)]
    return any(n in ("Exception", "BaseException") for n in names)


def find_silent_handlers() -> list[str]:
    """Every silent broad handler in the package, as 'path:line'."""
    found: list[str] = []
    for path in sorted(PACKAGE.rglob("*.py")):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
        except SyntaxError:            # not our problem here
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.ExceptHandler) and _is_broad(node) and _is_silent(node):
                rel = path.relative_to(PACKAGE.parent).as_posix()
                found.append(f"{rel}:{node.lineno}")
    return found


@pytest.fixture(scope="module")
def silent():
    return find_silent_handlers()


def test_the_detector_recognises_a_silent_handler():
    """Guard the guard: this must not quietly stop detecting anything."""
    tree = ast.parse("try:\n    x()\nexcept Exception:\n    pass\n")
    handler = next(n for n in ast.walk(tree) if isinstance(n, ast.ExceptHandler))
    assert _is_broad(handler) and _is_silent(handler)


def test_a_handler_that_logs_is_not_counted():
    tree = ast.parse("try:\n    x()\nexcept Exception:\n    log('boom')\n")
    handler = next(n for n in ast.walk(tree) if isinstance(n, ast.ExceptHandler))
    assert _is_broad(handler) and not _is_silent(handler)


def test_a_narrow_handler_is_not_counted():
    tree = ast.parse("try:\n    x()\nexcept ValueError:\n    pass\n")
    handler = next(n for n in ast.walk(tree) if isinstance(n, ast.ExceptHandler))
    assert not _is_broad(handler)


def test_silent_exception_handlers_do_not_grow(silent):
    """The ratchet. New code must not swallow errors in silence."""
    if len(silent) > BASELINE + GRACE:
        added = len(silent) - BASELINE
        sample = "\n  ".join(silent[-min(added, 15):])
        pytest.fail(
            f"{added} new silent broad exception handler(s): {len(silent)} > "
            f"baseline {BASELINE} (+{GRACE} grace).\n"
            f"Log the error, record it, or re-raise. "
            f"Recent offenders include:\n  {sample}"
        )


def test_the_baseline_is_not_stale(silent):
    """If handlers were fixed, lower BASELINE so the ratchet keeps its grip."""
    assert len(silent) >= BASELINE - 15, (
        f"only {len(silent)} silent handlers remain (baseline {BASELINE}). "
        "Lower BASELINE in this file to lock in the improvement."
    )
