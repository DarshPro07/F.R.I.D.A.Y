"""UCW-008: a person describes a process, the (injected) drafting model proposes a
workflow definition, and it is offered for activation only when it validates
through UCW-002, names only registered roles and available capabilities, and
passes a dry run with stub agents. A need with no role or capability is a typed
refusal naming it; nothing is activated without the person's confirmation."""
from __future__ import annotations

import asyncio
import copy
import json
import unittest.mock as mock
from datetime import datetime, timedelta, timezone

import pytest

from friday import confirmation as C
from friday.ultracode import definition as D
from friday.ultracode import workflow_author as W

from tests.test_ultracode_definition import build_capability

ROLES = frozenset({"scout", "reviewer", "builder", "tester", "judge"})
CAPS = frozenset({"pytest.run"})
DESCRIPTION = "scan and review the repo, build a patch, run its tests and have it judged"


def fake_draft(payload):
    """A drafting model stand-in: records what it was asked, returns `payload`."""
    seen: list = []

    async def draft(description, context):
        seen.append((description, context))
        return payload

    draft.seen = seen
    return draft


def author(payload, *, roles=ROLES, capabilities=CAPS, description=DESCRIPTION):
    draft = fake_draft(payload)
    result = asyncio.run(W.author_workflow(description, draft=draft, roles=roles,
                                           capabilities=capabilities))
    return result, draft


def test_a_valid_draft_is_offered_after_validation_and_a_stub_dry_run():
    result, draft = author(json.dumps(build_capability()))
    assert result.status == W.OFFERED and result.offerable and not result.gaps
    assert result.definition == D.parse_definition(build_capability())
    assert result.dry_run == {"scan": "DONE", "review": "DONE", "build": "DONE",
                              "test": "DONE", "verdict": "DONE"}
    description, context = draft.seen[0]
    assert description == DESCRIPTION
    assert context["roles"] == sorted(ROLES) and context["capabilities"] == sorted(CAPS)


def test_the_drafting_model_is_told_what_it_may_use_and_never_a_model_name():
    _, draft = author(build_capability())
    context = draft.seen[0][1]
    assert set(context) >= {"roles", "capabilities", "tiers", "rules"}
    assert any("model" in rule for rule in context["rules"])


def test_a_step_with_an_unregistered_role_is_a_typed_refusal_naming_it():
    raw = build_capability()
    raw["steps"][0]["role"] = "archivist"
    result, _ = author(raw)
    assert result.status == W.REFUSED and not result.offerable and result.definition is None
    assert result.kind == W.GAP
    assert [(g.kind, g.name, g.step) for g in result.gaps] == [("role", "archivist", "scan")]
    assert "archivist" in result.reason and "scan" in result.reason


def test_an_unavailable_capability_is_a_typed_refusal_naming_it():
    raw = build_capability()
    raw["steps"][3]["executor"] = "deploy.prod"
    result, _ = author(raw)
    assert result.status == W.REFUSED and result.kind == W.GAP
    assert [(g.kind, g.name, g.step) for g in result.gaps] == [("capability", "deploy.prod", "test")]


def test_every_gap_is_named_not_just_the_first():
    raw = build_capability()
    raw["steps"][0]["role"] = "archivist"
    raw["steps"][2]["role"] = "deployer"
    raw["steps"][3]["executor"] = "deploy.prod"
    result, _ = author(raw)
    assert [(g.kind, g.name) for g in result.gaps] == [
        ("role", "archivist"), ("role", "deployer"), ("capability", "deploy.prod")]


def test_a_need_the_model_reports_instead_of_inventing_a_step_is_refused_by_name():
    result, _ = author({"gaps": ["a role that can file taxes", {"need": "bank transfer"}]})
    assert result.status == W.REFUSED and result.kind == W.GAP and result.definition is None
    assert [(g.kind, g.name) for g in result.gaps] == [
        ("requested", "a role that can file taxes"), ("requested", "bank transfer")]


def test_a_draft_that_names_a_model_is_refused_by_the_definition_schema():
    raw = build_capability()
    raw["steps"][0]["model"] = "gpt-5"
    result, _ = author(raw)
    assert result.status == W.REFUSED and result.kind == W.INVALID
    assert result.field == "steps[0].model" and not result.gaps


def test_a_cyclic_or_malformed_draft_is_refused_with_the_failing_field():
    raw = build_capability()
    raw["steps"][0]["depends_on"] = ["verdict"]
    result, _ = author(raw)
    assert result.kind == W.INVALID and result.field.startswith("steps[")
    bad, _ = author("not json at all")
    assert bad.kind == W.INVALID and bad.field == "$"
    odd, _ = author(42)
    assert odd.kind == W.INVALID and odd.field == "$"


def test_a_definition_whose_dry_run_fails_is_refused_not_offered():
    real = D.run_definition

    async def broken(compiled, **kw):
        out = await real(compiled, **kw)
        out["build"] = D.StepOutcome("build", "INVALID", field="steps[2].outputs",
                                     reason="stub result broke the schema")
        return out

    with mock.patch.object(W.D, "run_definition", broken):
        result, _ = author(build_capability())
    assert result.status == W.REFUSED and result.kind == W.DRY_RUN
    assert result.field == "steps[2].outputs" and result.dry_run["build"] == "INVALID"
    assert not result.offerable


def test_the_dry_run_calls_no_real_model_and_no_real_executor():
    calls: list = []
    real = D.run_definition

    async def spy(compiled, *, agent, executors, resolutions=None):
        async def wrapped(step, handed, model):
            calls.append(model)
            return await agent(step, handed, model)
        return await real(compiled, agent=wrapped, executors=executors, resolutions=resolutions)

    with mock.patch.object(W.D, "run_definition", spy):
        result, _ = author(build_capability())
    assert result.offerable and calls and set(calls) == {None}


RUN = "run-1"


def offered(payload=None):
    result, _ = author(build_capability() if payload is None else payload)
    book = C.Book()
    return result, book, W.offer(result, book, run_id=RUN)


def test_nothing_is_activated_until_the_person_approves():
    result, book, ask = offered()
    registry = D.DefinitionRegistry()
    assert not hasattr(result, "confirm_token")
    with pytest.raises(W.ActivationRefused, match="not been approved"):
        W.activate(result, registry, book=book, nonce=ask.nonce, run_id=RUN)
    with pytest.raises(W.ActivationRefused):
        W.activate(result, registry, book=book, nonce="guessed", run_id=RUN)
    with pytest.raises(W.ActivationRefused):
        W.activate(result, registry, book=book, nonce="é" * 64, run_id=RUN)
    assert registry.versions("friday.build_capability") == ()


def test_an_approved_offer_registers_the_definition_exactly_once():
    result, book, ask = offered()
    registry = D.DefinitionRegistry()
    assert (ask.action, ask.target) == (W.ACTIVATE_ACTION, "friday.build_capability@1")
    assert ask.arguments == result.definition.to_dict()
    assert book.approve(ask.nonce).ok
    W.activate(result, registry, book=book, nonce=ask.nonce, run_id=RUN)
    assert registry.get("friday.build_capability") == result.definition
    with pytest.raises(W.ActivationRefused, match="already been used"):
        W.activate(result, D.DefinitionRegistry(), book=book, nonce=ask.nonce, run_id=RUN)


def test_a_refused_offer_cannot_be_activated():
    result, book, ask = offered()
    book.refuse(ask.nonce)
    assert not book.approve(ask.nonce).ok
    with pytest.raises(W.ActivationRefused):
        W.activate(result, D.DefinitionRegistry(), book=book, nonce=ask.nonce, run_id=RUN)


def test_a_yes_for_one_definition_does_not_activate_a_changed_one():
    first, book, ask = offered()
    other = copy.deepcopy(build_capability())
    other["steps"][0]["cap"] = 1234
    changed, _ = author(other)
    assert changed.offerable and changed.definition != first.definition
    assert book.approve(ask.nonce).ok
    registry = D.DefinitionRegistry()
    with pytest.raises(W.ActivationRefused, match="ask again for this one"):
        W.activate(changed, registry, book=book, nonce=ask.nonce, run_id=RUN)
    with pytest.raises(W.ActivationRefused):
        W.activate(first, registry, book=book, nonce=ask.nonce, run_id="another-run")
    assert registry.versions("friday.build_capability") == ()


def test_an_expired_confirmation_cannot_be_spent():
    result, book, ask = offered()
    assert book.approve(ask.nonce).ok
    book.pending[ask.nonce].expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    registry = D.DefinitionRegistry()
    with pytest.raises(W.ActivationRefused, match="expired"):
        W.activate(result, registry, book=book, nonce=ask.nonce, run_id=RUN)
    assert registry.versions("friday.build_capability") == ()


def test_a_refused_draft_cannot_even_be_offered():
    raw = build_capability()
    raw["steps"][0]["role"] = "archivist"
    result, _ = author(raw)
    with pytest.raises(W.ActivationRefused):
        W.offer(result, C.Book(), run_id=RUN)


def test_a_real_draft_that_cannot_finish_the_stub_dry_run_is_refused_unpatched():
    raw = build_capability()
    raw["steps"][0]["outputs"]["properties"]["bad"] = {"type": "string", "enum": [1]}
    raw["steps"][0]["outputs"].setdefault("required", []).append("bad")
    result, _ = author(raw)
    assert result.status == W.REFUSED and result.kind == W.DRY_RUN and not result.offerable
    assert result.field == "steps[0].outputs" and "bad: expected string" in result.reason
    assert result.dry_run["scan"] == "INVALID"


def test_one_malformed_gaps_entry_does_not_lose_the_valid_needs():
    result, _ = author({"gaps": ["bank transfer", 7, {"need": "  "}, {"need": "file taxes"}]})
    assert result.kind == W.GAP and result.definition is None
    assert [g.name for g in result.gaps] == ["bank transfer", "file taxes"]
    assert "1, 2" in result.reason and "malformed" in result.reason
    all_bad, _ = author({"gaps": [7, None]})
    assert all_bad.kind == W.INVALID and all_bad.field == "gaps" and not all_bad.gaps


def test_a_refused_draft_can_never_be_activated():
    raw = build_capability()
    raw["steps"][0]["role"] = "archivist"
    result, _ = author(raw)
    with pytest.raises(W.ActivationRefused):
        W.activate(result, D.DefinitionRegistry(), book=C.Book(), nonce="anything", run_id=RUN)


def test_a_blank_description_is_rejected_before_the_model_is_asked():
    draft = fake_draft(build_capability())
    with pytest.raises(ValueError):
        asyncio.run(W.author_workflow("   ", draft=draft, roles=ROLES, capabilities=CAPS))
    assert draft.seen == []


def test_a_draft_error_propagates_rather_than_being_swallowed():
    async def draft(description, context):
        raise RuntimeError("model down")

    with pytest.raises(RuntimeError, match="model down"):
        asyncio.run(W.author_workflow(DESCRIPTION, draft=draft, roles=ROLES, capabilities=CAPS))
