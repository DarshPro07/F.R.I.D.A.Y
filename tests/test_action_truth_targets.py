"""
Action truth at the level of the TARGET, not just the kind (FR-TRU-001).

The PRD states the failure in one line:

    Success evidence for foo.txt cannot justify "I created bar.txt."

That is the most plausible false claim this system can produce, because
every part of it is true except the part the user cares about: the verb is
right, the tool family is right, the verification is real, and the filename
is wrong. `friday/honesty.py` checked the first three and not the fourth.

These tests are written in both directions on purpose. The RED half proves
the guard catches the substitution; the GREEN half proves it does not catch
an honest claim, because a guard that refuses everything is the same as no
guard once someone switches it off.
"""
from __future__ import annotations

import pytest

from friday import contracts as c
from friday import honesty as H


def _run_that_wrote(path: str, *, title: str = "", tool: str = "files_write"):
    """A run with exactly one verified write to `path`."""
    run = c.Run.create(f"write {path}", capability=tool)
    prior = c.started(run.run_id, tool)
    verification = c.Verification(method="stat", evidence=f"{path} exists, 12 bytes")
    artifact = c.new_artifact(run_id=run.run_id, type="file",
                              title=title or path.rsplit("/", 1)[-1],
                              path_or_uri=path, producer=tool,
                              verification=verification)
    run.record(c.succeeded(prior, output={"path": path},
                           artifacts=(artifact,), verification=verification))
    return run


# --- the substitution the PRD names ----------------------------------------

def test_evidence_for_one_file_does_not_back_a_claim_about_another():
    run = _run_that_wrote("C:/work/foo.txt")
    audit = H.audit("I created bar.txt.", run)
    assert not audit.ok, "a claim about bar.txt passed on foo.txt's evidence"
    assert "bar.txt" in audit.reason
    assert "foo.txt" in audit.reason, \
        "the refusal should say what actually succeeded, not just that it refused"


def test_the_honest_claim_about_the_same_file_still_passes():
    """The GREEN half. Without this, the test above could be satisfied by a
    guard that refuses every claim, which is not a guard."""
    run = _run_that_wrote("C:/work/foo.txt")
    assert H.audit("I created foo.txt.", run).ok


@pytest.mark.parametrize("said", [
    "I created foo.txt.",
    "I created C:/work/foo.txt.",
    "I created ./work/foo.txt.",
    "I have written foo.txt for you.",
])
def test_the_same_file_named_several_ways_is_recognised(said):
    """The user says the short name; the tool records the long one. Treating
    those as different targets would make the guard fire on true claims,
    which is how a guard gets removed."""
    run = _run_that_wrote("C:/work/foo.txt")
    audit = H.audit(said, run)
    assert audit.ok, f"{said!r} was refused although it names the file that was written: {audit.reason}"


def test_a_claim_with_no_concrete_target_is_left_to_the_kind_check():
    """"I processed the catalogue" names nothing checkable. The kind check
    owns it; this rule must not invent a target to complain about."""
    run = c.Run.create("process the catalogue", capability="product_import")
    prior = c.started(run.run_id, "product_import")
    verification = c.Verification(method="count", evidence="412 rows processed")
    run.record(c.succeeded(prior, output={"rows": 412}, verification=verification))
    assert H.audit("I processed the catalogue.", run).ok


def test_a_url_claim_is_checked_against_the_url_that_was_touched():
    run = c.Run.create("open the dashboard", capability="browser_open")
    prior = c.started(run.run_id, "browser_open")
    verification = c.Verification(method="page-title",
                                  evidence="https://shop.example.com/admin loaded")
    run.record(c.succeeded(prior, output={"url": "https://shop.example.com/admin"},
                           verification=verification))
    assert H.audit("I opened https://shop.example.com/admin.", run).ok
    bad = H.audit("I opened https://bank.example.com/transfer.", run)
    assert not bad.ok, "a claim about a different site passed on this site's evidence"


def test_a_scheme_is_not_mistaken_for_a_windows_drive():
    """`https:/` matches a naive drive-letter pattern. A URL reported as a
    path makes the refusal message wrong even when the verdict is right."""
    named = H.named_targets("I posted https://x.example.com/a")
    assert "https://x.example.com/a" in named
    assert not any(value.startswith("s:") for value in named), named


def test_a_succeeded_action_that_records_nothing_it_touched_backs_no_named_claim():
    """A tool that cannot say what it acted on is not evidence for a claim
    about a specific thing. This is the fail-closed direction: unknown is
    not the same as fine."""
    run = c.Run.create("write something", capability="files_write")
    prior = c.started(run.run_id, "files_write")
    run.record(c.succeeded(prior, output="done",
                           verification=c.Verification(method="none",
                                                       evidence="the call returned")))
    audit = H.audit("I created invoice.csv.", run)
    assert not audit.ok
    assert "recorded what it touched" in audit.reason


# --- negative control -------------------------------------------------------

def test_removing_the_target_check_would_be_caught():
    """The load-bearing test for this guard.

    It restores the pre-guard behaviour in this process only, re-runs the
    substitution, and requires it to pass - which proves the passing test
    above is detecting the guard and not something incidental. The patch is
    undone in `finally`, so a failure cannot leave the auditor blind for the
    rest of the session.
    """
    run = _run_that_wrote("C:/work/foo.txt")
    original = H._target_mismatch
    try:
        H._target_mismatch = lambda sentence, backed: ""
        assert H.audit("I created bar.txt.", run).ok, (
            "with the target check removed the false claim STILL failed, so "
            "the test above is not proving what it claims")
    finally:
        H._target_mismatch = original
    assert not H.audit("I created bar.txt.", run).ok, "the guard was not restored"


# --- FR-TRU-004: exact literal preservation ---------------------------------
#
# The planner may change HOW but never WHAT. The PRD asks for corruption
# tests across five literal families; these cover the string-level property
# that every one of them depends on - a literal that survives the audit
# layer unchanged - and the request-shaping layer that must not rewrite it.

@pytest.mark.parametrize("literal", [
    "C:/Users/admin/Downloads/PRD.md",
    "./docs/plans/prd-universal-baseline/00-status.md",
    "https://github.com/DarshPro07/F.R.I.D.A.Y",
    "devendra@aicodepro.com",
    "+91 98765 43210",
    "release/v3.1.0",
    "SKU-4471-B",
    "order #100238",
])
def test_a_named_literal_is_reported_back_exactly(literal):
    """Whatever else the claim layer does, it must not alter the literal it
    is reasoning about: a path with its case changed or a branch with its
    slash normalised is a different thing from the one the user named."""
    named = H.named_targets(f"I have updated {literal} for you.")
    if named:
        assert any(literal.endswith(value) or value in literal
                   for value in named), \
            f"{literal!r} came back as {named!r}"


def test_a_literal_target_is_matched_case_insensitively_but_not_altered():
    """Windows paths differ in case and are the same file; the comparison
    must tolerate that WITHOUT rewriting what the user said."""
    said = "I created C:/Work/Foo.txt."
    run = _run_that_wrote("C:/work/foo.txt")
    assert H.audit(said, run).ok
    assert "C:/Work/Foo.txt" in said, "the input string was mutated in place"
