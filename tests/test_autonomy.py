"""
Autonomy: a gate with no key is a hang, not a safety feature.

The failure this pins: browser.automate was ASK-gated, there was deliberately
no self-approval tool, so in conversation there was no way to say yes. The
agent asked "shall I proceed?", the user said "yes", and it asked again. Four
times.
"""

from __future__ import annotations

import inspect

import pytest

from friday import policy as p
from friday.toolsets import system as S


def test_full_is_the_default():
    assert p.PolicyEngine().autonomy == p.FULL


def test_full_autonomy_never_asks_for_anything():
    """The whole point. Nothing may stop and ask when there is no way to answer."""
    engine = p.PolicyEngine(autonomy=p.FULL)
    assert not engine.asks_for_anything
    for tool_id in p.TOOL_CATEGORIES:
        assert engine.decide(tool_id).decision != p.ASK, tool_id


@pytest.mark.parametrize("tool_id", [
    "browser.automate",   # the one that actually hung
    "apps.close",
    "clipboard.write",
    "files.write",
    "files.create",
    "files.edit",
    "files.copy",
    "files.move",
    "memory.forget",
])
def test_previously_gated_tools_now_run(tool_id):
    assert p.PolicyEngine(autonomy=p.FULL).decide(tool_id).decision == p.AUTO


def test_refusals_are_not_relaxed_by_autonomy():
    """DENY is a refusal, not a question. Full autonomy does not touch it."""
    engine = p.PolicyEngine(autonomy=p.FULL)
    assert engine.decide("secrets.read").decision == p.DENY
    with pytest.raises(p.PolicyError):
        engine.approve_for_session("secrets.read")


def test_unknown_tools_still_do_not_silently_run():
    """
    Autonomy relaxes declared questions. It does not make an undeclared tool
    automatic - that would mean 'unaudited' quietly became 'allowed'.
    """
    verdict = p.PolicyEngine(autonomy=p.FULL).decide("some.tool.nobody.declared")
    assert verdict.decision == p.ASK
    assert "unaudited" in verdict.reason


def test_guarded_mode_still_works_for_anyone_who_wants_it():
    engine = p.PolicyEngine(autonomy=p.GUARDED)
    assert engine.asks_for_anything
    assert engine.decide("files.write").decision == p.ASK
    assert engine.decide("browser.automate").decision == p.ASK
    assert engine.decide("system.get_info").decision == p.AUTO


def test_unknown_autonomy_mode_is_rejected():
    with pytest.raises(ValueError, match="unknown autonomy mode"):
        p.PolicyEngine(autonomy="yolo")


def test_explicit_overrides_still_win():
    """A user who wants one thing gated can still gate it."""
    engine = p.PolicyEngine(autonomy=p.FULL, overrides={p.FILE_WRITE: p.ASK})
    assert engine.decide("files.write").decision == p.ASK
    assert engine.decide("apps.close").decision == p.AUTO


def test_authorization_still_takes_only_a_tool_id():
    """Autonomy changed the answers, not who is allowed to ask."""
    assert list(inspect.signature(p.PolicyEngine.decide).parameters) == \
        ["self", "tool_id"]


def test_a_gated_tool_reports_a_reason_a_human_can_act_on():
    """
    If something IS gated (guarded mode), the result must say what to change -
    not just refuse. The old message told the user to approve, which they had
    no way to do.
    """
    from friday import contracts as c

    run = c.Run.create("close spotify", capability="system")
    engine = p.PolicyEngine(autonomy=p.GUARDED)
    result = S.apps_close(run, "spotify", engine=engine)
    assert S.needs_approval(result)
    assert "GRACEFUL_PROCESS_CLOSE" in result.error


def test_tools_run_without_approval_under_the_default_engine():
    """End to end: the default engine lets a previously gated tool through."""
    from friday import contracts as c

    run = c.Run.create("clipboard", capability="system")
    result = S.clipboard_write(run, "ada-autonomy-check")
    assert not S.needs_approval(result), result.error
    assert result.status in ("succeeded", "partial", "failed")


# ---------------------------------------------------------------------------
# CONFIRM: the tier autonomy does not grant
# ---------------------------------------------------------------------------


def test_full_autonomy_turns_ask_into_yes_but_not_confirm():
    """
    FULL exists because a gate with no key is a hang, not a safety feature -
    the agent asked "shall I proceed?" four times and nothing could say yes.
    That reasoning holds for the volume and for opening an app. It does not
    hold for shutting the machine down, which loses unsaved work in every
    application at once and kills Friday mid-sentence.
    """
    full = p.resolve_policy(p.FULL)
    assert full[p.APP_CLOSE] == p.AUTO, "FULL stopped granting ordinary asks"
    assert full[p.FILE_WRITE] == p.AUTO
    assert full[p.POWER_ACTION] == p.CONFIRM, \
        "autonomy granted a power action on its own"


def test_confirm_is_not_deny():
    """
    DENY means never and cannot be granted. CONFIRM means a person has to say
    yes - which they are allowed to do, one action at a time.

    The mechanism moved. It used to be `approve_for_session`, which made one
    yes cover every later call to that tool for the rest of the session -
    "confirmed power actions for the next ten minutes", which is precisely
    what the CONFIRM tier exists to rule out. A confirmation authorises one
    action, on one target, with one set of arguments, once. A set of tool ids
    cannot express that, so it no longer tries.
    """
    from friday import confirmation as CF

    assert p.POWER_ACTION not in p.NON_APPROVABLE, "CONFIRM is not DENY"

    engine = p.PolicyEngine()
    p.TOOL_CATEGORIES.setdefault("power.test_only", p.POWER_ACTION)
    try:
        assert not engine.decide("power.test_only").allowed
        with pytest.raises(p.PolicyError) as caught:
            engine.approve_for_session("power.test_only")
        assert "one action at a time" in str(caught.value)
    finally:
        p.TOOL_CATEGORIES.pop("power.test_only", None)

    # And here is how it IS granted: bound to this action, spent once.
    book = CF.Book()
    pending = book.ask("run-1", "RESTART_MACHINE", "LOCAL_MACHINE", "Restart?")
    book.approve(pending.nonce)
    assert book.consume(pending.nonce, run_id="run-1",
                        action="RESTART_MACHINE",
                        target="LOCAL_MACHINE").ok
    assert not book.consume(pending.nonce, run_id="run-1",
                            action="RESTART_MACHINE",
                            target="LOCAL_MACHINE").ok, "it was reusable"


def test_a_session_approval_still_settles_an_ordinary_ask():
    """The tier below is untouched: ASK is what session approval is for."""
    engine = p.PolicyEngine(autonomy=p.GUARDED)
    assert not engine.decide("apps.close").allowed
    engine.approve_for_session("apps.close")
    assert engine.decide("apps.close").allowed


def test_a_confirm_verdict_says_which_kind_of_no_it_is():
    engine = p.PolicyEngine()
    p.TOOL_CATEGORIES.setdefault("power.test_only", p.POWER_ACTION)
    try:
        verdict = engine.decide("power.test_only")
        assert verdict.needs_approval and verdict.needs_confirmation
        assert not verdict.denied
        assert "say yes" in verdict.reason
    finally:
        p.TOOL_CATEGORIES.pop("power.test_only", None)


def test_guarded_mode_is_unchanged_by_the_new_tier():
    guarded = p.resolve_policy(p.GUARDED)
    assert guarded[p.POWER_ACTION] == p.CONFIRM
    assert guarded[p.FILE_WRITE] == p.ASK


def test_every_decision_a_category_declares_is_a_known_one():
    for category, decision in p.DEFAULT_POLICY.items():
        assert decision in p.DECISIONS, f"{category}: {decision}"
