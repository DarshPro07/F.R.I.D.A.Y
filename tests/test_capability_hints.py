"""
CAP-004: every capability declares the six consequence hints, and a
consequential one with no confirmation path is refused by the registry check.

`side_effect` says what a call touches. The hints say what it costs if the
call was a mistake, so policy and verification read a declaration instead of
guessing from a tool name.
"""
from __future__ import annotations
from dataclasses import fields, replace
import json
import pytest
from friday import capabilities as C
from friday import capability_runtime as R

#: Declared AUTO by policy, so a capability gated under it has no yes to ask.
AUTO_TOOL = "get_world_news"
#: Declared above AUTO by policy, so a capability gated under it asks.
ASKED_TOOL = "power_shutdown"

ALL_SIX = dict(read_only=False, consequential=True, reversible=False,
               idempotent=False, external_side_effect=True,
               requires_confirmation=True)


def _cap(**over) -> C.Capability:
    base = dict(id="x_hint", description="d", execution_scope="user_device",
                side_effect="external_action", risk="HIGH",
                policy_tool_id=ASKED_TOOL, **ALL_SIX)
    base.update(over)
    return C.Capability(**base)


def test_the_record_declares_the_six_hints_undeclared_by_default():
    declared = {f.name: f.default for f in fields(C.Capability)}
    for name in C.HINTS:
        assert declared[name] is None, f"{name} must default to 'not declared'"
    assert len(C.HINTS) == 6


def test_every_registered_capability_declares_all_six():
    assert C._ALL, "the registry must not be empty"
    for cap in C._ALL:
        for name in C.HINTS:
            assert isinstance(getattr(cap, name), bool), \
                f"{cap.id}.{name} is undeclared"
    assert C.registry_problems() == []


def test_a_capability_that_leaves_a_hint_out_fails_the_registry_check():
    bare = C.Capability(id="x_bare", description="d",
                        execution_scope="network", side_effect="read")
    problems = C.registry_problems([bare])
    assert len(problems) == 1 and "x_bare" in problems[0]
    assert "undeclared" in problems[0]

    one_short = replace(_cap(), idempotent=None)
    assert "idempotent" in C.registry_problems([one_short])[0]


def test_a_consequential_capability_with_no_confirmation_path_is_refused():
    # Declared consequential, but policy gates it AUTO: nothing asks a person.
    ungated = _cap(policy_tool_id=AUTO_TOOL, requires_confirmation=False)
    problems = C.registry_problems([ungated])
    assert problems == ["x_hint: consequential with no confirmation path"]

    # The same capability with a path through policy passes.
    assert C.registry_problems([_cap()]) == []


def test_a_declared_confirmation_must_match_what_policy_does():
    claims_a_yes = _cap(policy_tool_id=AUTO_TOOL, consequential=False,
                        requires_confirmation=True)
    claims_none = _cap(consequential=False, requires_confirmation=False)
    for cap in (claims_a_yes, claims_none):
        problems = C.registry_problems([cap])
        assert len(problems) == 1 and "requires_confirmation" in problems[0]


@pytest.mark.parametrize("bad", [
    {"side_effect": "write", "read_only": True, "consequential": False,
     "external_side_effect": False},
    {"side_effect": "read", "read_only": False, "consequential": False,
     "external_side_effect": False},
    {"external_side_effect": False},
    {"side_effect": "write"},
    {"side_effect": "read", "read_only": True, "external_side_effect": False},
])
def test_a_hint_that_contradicts_the_side_effect_is_refused(bad):
    with pytest.raises(ValueError, match="x_hint"):
        _cap(**bad)


def test_derived_hints_follow_side_effect_risk_and_policy():
    news = C.get(AUTO_TOOL)
    assert (news.read_only, news.consequential, news.external_side_effect,
            news.requires_confirmation) == (True, False, False, False)
    assert news.idempotent is True

    off = C.get(ASKED_TOOL)
    assert (off.read_only, off.consequential, off.external_side_effect,
            off.reversible, off.requires_confirmation) == (
                False, True, True, False, True)

    # a repeat of "make a reminder" is a second reminder; "bring forward" is not
    assert C.get("reminders_create").idempotent is False
    assert C.get("windows_focus").idempotent is True
    # destroying something is declared unrecoverable even where risk is LOW
    assert C.get("memory_forget").reversible is False


def test_every_consequential_capability_asks_a_person():
    consequential = [cap for cap in C._ALL if cap.consequential]
    assert consequential, "the registry must mark something consequential"
    assert [cap.id for cap in consequential if not cap.requires_approval] == []


def test_the_hints_reach_the_manifest_and_stay_json():
    rows = C.as_dicts()
    for row in rows:
        assert set(C.HINTS) <= set(row)
    assert json.dumps(rows)


def test_a_changed_hint_changes_the_capability_fingerprint(monkeypatch):
    before = R.fingerprint(AUTO_TOOL)
    cap = C.get(AUTO_TOOL)
    monkeypatch.setitem(C.CAPABILITIES, AUTO_TOOL,
                        replace(cap, reversible=not cap.reversible))
    assert R.fingerprint(AUTO_TOOL) != before, \
        "a capability whose consequence changed must not run on an old plan"
