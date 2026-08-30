"""
The person answers. The model does not get to.

Two halves, and the second is the one that needs a test of its own:

    the mechanism   `Book.consume` refuses anything not APPROVED, and only
                    `Book.approve` sets that. Covered in test_confirmation.py.

    the wiring      nothing the model can call reaches `Book.approve`.

Unit tests prove a lock works. They cannot prove nobody left a key out, and
that is a different failure - one that arrives later, by someone adding a
convenient `confirm()` tool because the agent kept asking and nothing could
answer. That is exactly how it would happen, and it would look reasonable in
the diff.

So the second half is structural: every module under `friday/tools/` is parsed
and checked for any route to approval. Parsed, not grepped - a prose-versus-
behaviour test in this project has matched its own docstring five separate
times.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from friday import approval as A
from friday import confirmation as CF

TOOLS_DIR = Path(__file__).resolve().parent.parent / "friday" / "tools"

#: Names that would mean somebody wired an answer into the model's reach.
APPROVAL_VERBS = ("approve", "confirm_action", "grant", "authorize",
                  "authorise", "say_yes", "allow_action")


@pytest.fixture
def book():
    return CF.Book()


def _modules() -> list[Path]:
    return sorted(p for p in TOOLS_DIR.glob("*.py")
                  if p.name != "__init__.py")


# ---------------------------------------------------------------------------
# The wiring
# ---------------------------------------------------------------------------


def test_there_are_tool_modules_to_check():
    """A search that finds nothing passes every assertion about it."""
    assert len(_modules()) > 10, "the tool modules moved; this test is blind"


@pytest.mark.parametrize("module", _modules(), ids=lambda p: p.name)
def test_no_tool_module_can_reach_approval(module):
    """
    No call to `.approve(...)`, and no import of the approval matcher, in
    anything the model can invoke.
    """
    tree = ast.parse(module.read_text(encoding="utf-8"))

    called = {node.func.attr for node in ast.walk(tree)
              if isinstance(node, ast.Call)
              and isinstance(node.func, ast.Attribute)}
    assert "approve" not in called, f"{module.name} calls .approve()"

    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
            imported.update(f"{node.module}.{a.name}" for a in node.names)
        elif isinstance(node, ast.Import):
            imported.update(a.name for a in node.names)
    assert not any("approval" in name for name in imported), \
        f"{module.name} imports the approval matcher"


@pytest.mark.parametrize("module", _modules(), ids=lambda p: p.name)
def test_no_registered_tool_is_named_for_an_approval(module):
    """
    A tool called `approve_action` would be reachable by the model whatever
    its body did. The name is the surface, so the name is checked.
    """
    tree = ast.parse(module.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        decorated = any(
            isinstance(d, ast.Call) and isinstance(d.func, ast.Attribute)
            and d.func.attr == "tool" for d in node.decorator_list)
        if not decorated:
            continue
        lowered = node.name.lower()
        for verb in APPROVAL_VERBS:
            assert verb not in lowered, \
                f"{module.name} registers a tool named {node.name!r}"


def test_the_approval_matcher_lives_above_the_tool_boundary():
    """
    Where it lives is the guarantee. If it moved under `friday/tools/`, the
    test above would keep passing while the property it protects had gone.
    """
    from friday import approval

    where = Path(approval.__file__).resolve()
    assert where.parent.name == "friday", \
        f"the approval matcher is at {where}, inside the tool surface"


# ---------------------------------------------------------------------------
# What counts as an answer
# ---------------------------------------------------------------------------


def test_a_yes_settles_the_one_thing_waiting(book):
    pending = book.ask("run-1", "FORCE_TERMINATE", "notepad#4#1", "Force it?")
    verdict = A.answer(book, "yes", run_id="run-1")

    assert verdict.ok
    assert book.pending[pending.nonce].state == CF.APPROVED


def test_a_no_settles_it_the_other_way(book):
    pending = book.ask("run-1", "RESTART_MACHINE", "LOCAL_MACHINE", "Restart?")
    A.answer(book, "no thanks", run_id="run-1")

    assert book.pending[pending.nonce].state == CF.REFUSED


def test_a_yes_with_nothing_pending_approves_nothing(book):
    """
    Ordinary conversation is not an approval. "Yes" has to be an answer to a
    question that was asked, or it is just a word.
    """
    verdict = A.answer(book, "yes", run_id="run-1")
    assert not verdict.ok
    assert "nothing is waiting" in verdict.reason


def test_two_pending_questions_are_never_guessed_between(book):
    """
    The worst moment to pick one. Two destructive questions and a bare yes -
    asking which costs a sentence, and choosing costs whichever one they
    did not mean.
    """
    book.ask("run-1", "FORCE_TERMINATE", "chrome#12#1", "Force Chrome?")
    book.ask("run-1", "RESTART_MACHINE", "LOCAL_MACHINE", "Restart?")

    with pytest.raises(A.Ambiguous) as caught:
        A.answer(book, "yes", run_id="run-1")

    assert len(caught.value.pending) == 2
    assert all(c.state == CF.PENDING for c in caught.value.pending), \
        "something was settled while the answer was ambiguous"


def test_something_that_is_not_an_answer_settles_nothing(book):
    pending = book.ask("run-1", "SHUTDOWN", "LOCAL_MACHINE", "Shut down?")
    verdict = A.answer(book, "what does that mean", run_id="run-1")

    assert not verdict.ok
    assert book.pending[pending.nonce].state == CF.PENDING


@pytest.mark.parametrize("said", [
    "yesterday", "surely not", "okay hold on", "no idea what you mean",
    "unless you think otherwise",
])
def test_words_that_merely_contain_an_answer_are_not_one(book, said):
    """
    The reason matching is anchored to the whole utterance. "Yes" is inside
    "yesterday", "ok" inside "okay hold on", "sure" inside "surely not" - and
    a substring match here force-terminates something.
    """
    pending = book.ask("run-1", "FORCE_TERMINATE", "x#1#1", "Force it?")
    A.answer(book, said, run_id="run-1")
    assert book.pending[pending.nonce].state == CF.PENDING, \
        f"{said!r} was taken as an answer"


def test_an_answer_does_not_cross_between_runs(book):
    book.ask("run-1", "SHUTDOWN", "LOCAL_MACHINE", "Shut down?")
    verdict = A.answer(book, "yes", run_id="run-2")
    assert not verdict.ok


def test_an_expired_question_is_not_still_waiting(book):
    from datetime import timedelta

    pending = book.ask("run-1", "SHUTDOWN", "LOCAL_MACHINE", "Shut down?")
    pending.expires_at -= timedelta(seconds=120)

    assert A.awaiting(book, "run-1") == []
    verdict = A.answer(book, "yes", run_id="run-1")
    assert not verdict.ok
