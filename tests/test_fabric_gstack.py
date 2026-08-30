"""
gstack as methodology, and the discipline that keeps it methodology.

The risk with a 61-workflow pack is not that it fails to load. It is that it
loads too well: that it becomes a second orchestrator, that its browser rules
end up next to Friday's, or that answering "review this" costs a hundred
thousand tokens of procedure nobody read. These tests are mostly about those
three, not about whether markdown can be opened.
"""

from __future__ import annotations

import pytest

from friday import fabric
from friday.fabric_adapters import gstack_process as gs

cloned = pytest.mark.skipif(
    not (gs._skillpack.pack_root("gstack") / "review" / "SKILL.md").is_file(),
    reason="gstack not cloned to third_party/upstream")


@pytest.fixture(autouse=True)
def clean():
    fabric.reload()
    yield
    fabric.reload()


# --- descriptor ------------------------------------------------------------


def test_it_executes_no_upstream_code():
    """
    gstack ships a compiled browser binary and a bun toolchain. Declaring
    SKILL is the promise that none of it runs; `imported` is how an auditor
    checks the promise.
    """
    provider = fabric.get("gstack_process")
    assert provider.integration_mode == fabric.SKILL
    assert provider.imported is False
    assert provider.model_required is False
    assert provider.cost_class == "free"


def test_it_is_pinned_to_the_audited_commit():
    import json
    import pathlib

    lock = json.loads(
        (pathlib.Path(__file__).resolve().parent.parent
         / "third_party" / "UPSTREAM_LOCK.json").read_text(encoding="utf-8"))
    assert fabric.get("gstack_process").commit == lock["gstack"]["commit"]


def test_it_falls_back_to_the_other_role_pack():
    assert "role_recipes" in fabric.get("gstack_process").fallbacks


# --- it must not become a second orchestrator ------------------------------


@cloned
def test_the_browser_workflows_are_withheld():
    """
    Friday has one browser policy. gstack documents overriding the host's
    browser rules and drives its own Chromium daemon; offering those
    procedures would put a second policy in front of the same model.
    """
    offered = gs.call("catalogue", None)
    for workflow in ("browse", "connect-chrome", "open-gstack-browser",
                     "scrape", "browser-skills"):
        assert workflow not in offered, f"{workflow} must not be offered"


@cloned
def test_host_setup_and_self_upgrade_are_withheld():
    offered = gs.call("catalogue", None)
    for workflow in ("gstack-upgrade", "setup-gbrain", "sync-gbrain",
                     "setup-browser-cookies", "codex"):
        assert workflow not in offered


@cloned
def test_every_exclusion_carries_a_reason():
    """An exclusion without a reason gets deleted by the next maintainer."""
    for workflow, reason in gs.call("withheld", None).items():
        assert len(reason) > 10, f"{workflow} is excluded without a reason"


@cloned
def test_reading_a_withheld_workflow_says_why_rather_than_reading_it():
    with pytest.raises(fabric.FabricError, match="browser policy"):
        gs.call("skill", None, name="browse")


# --- laziness --------------------------------------------------------------


@cloned
def test_the_catalogue_is_metadata_not_procedures():
    """
    The catalogue is what a router needs to choose. If it ever starts
    returning the procedures themselves, every routing decision starts
    costing the whole pack.
    """
    catalogue = gs.call("catalogue", None)
    assert catalogue
    for name, entry in catalogue.items():
        assert set(entry) == {"description", "triggers", "path"}
        assert len(entry["description"]) < 400, f"{name} description is a body"


@cloned
def test_one_skill_is_read_at_a_time_and_nothing_is_cached():
    """
    Statelessness is the 'unload after use' property: there is no module-level
    store holding a procedure after the call that needed it returned.
    """
    body = gs.call("skill", None, name="review")
    assert "review" in body.lower()
    assert not any(isinstance(value, str) and len(value) > 5000
                   for value in vars(gs).values()), "a procedure was cached"


@cloned
def test_there_is_no_bulk_read_operation():
    assert "all" not in fabric.get("gstack_process").operations
    assert "everything" not in fabric.get("gstack_process").operations


@cloned
def test_a_procedure_is_size_capped():
    from friday.fabric_adapters import _skillpack

    assert len(gs.call("skill", None, name="review")) <= _skillpack.MAX_CHARS + 200


# --- routing, so nobody types a slash command ------------------------------


@cloned
@pytest.mark.parametrize("request_text, expected", [
    ("i want a ceo review of my plan", "plan-ceo-review"),
    ("engineering manager review of the plan", "plan-eng-review"),
    ("run a weekly engineering retrospective", "retro"),
    ("qa test this web app and fix the bugs", "qa"),
    ("be careful with destructive commands", "careful"),
    ("generate documentation for this feature", "document-generate"),
])
def test_a_plain_request_routes_to_the_right_workflow(request_text, expected):
    ranked = gs.call("route", None, task=request_text)
    assert ranked, f"nothing matched {request_text!r}"
    assert ranked[0]["skill"] == expected


@cloned
def test_a_security_review_reaches_a_workflow_that_covers_security():
    """
    Deliberately not asserting one name. gstack's `review` ships
    review/specialists/security.md, and `cso` is a dedicated security mode -
    both are correct answers, so the test asserts the shortlist is useful
    rather than pinning a preference the upstream does not express.
    """
    names = {row["skill"] for row in gs.call("route", None,
                                             task="do a security review")}
    assert names & {"review", "cso"}


@cloned
def test_routing_needs_no_slash_command_syntax():
    """The operator says what they want; Friday picks the workflow."""
    assert gs.call("route", None, task="review this feature before landing")[0]["skill"] == "review"


def test_routing_without_a_task_is_refused():
    with pytest.raises(fabric.FabricError, match="task"):
        gs.call("route", None, task="")


# --- it must not read outside its own catalogue ----------------------------


@cloned
def test_an_arbitrary_path_cannot_be_read_through_the_skill_operation():
    with pytest.raises(fabric.FabricError):
        gs.call("skill", None, name="../../../.env")


@cloned
def test_a_specialist_path_outside_the_specialist_directory_is_refused():
    with pytest.raises(fabric.FabricError, match="specialist"):
        gs.call("specialist", None, path="review/SKILL.md")


@cloned
def test_the_review_specialists_are_offered():
    paths = gs.call("specialists", None)
    assert any("security" in p for p in paths)
    assert all(p.startswith("review/specialists/") for p in paths)


# --- it must not silently replace what Friday already has ------------------


def test_it_does_not_duplicate_an_existing_friday_capability():
    """
    NON_NEGOTIABLE 11. gstack is methodology; Friday's 163 capabilities are
    actions. If a gstack workflow name ever becomes a capability id, one of
    them is redundant and the operator cannot tell which ran.
    """
    from friday import capabilities

    if not (gs._skillpack.pack_root("gstack")).is_dir():
        pytest.skip("gstack not cloned")
    overlap = set(gs.call("catalogue", None)) & set(capabilities.CAPABILITIES)
    assert not overlap, f"gstack workflow shadows a capability: {overlap}"


def test_the_other_skill_packs_are_still_registered():
    """Adding a pack must not displace the packs that were already there."""
    registry = fabric.registry()
    for provider in ("role_recipes", "no_ai_slop", "adhd_mode"):
        assert provider in registry


def test_two_role_providers_coexist_rather_than_one_overwriting_the_other():
    ids = {p.id for p in fabric.by_family("roles")}
    assert {"role_recipes", "gstack_process"} <= ids


# --- absence is a state, not a failure -------------------------------------


def test_an_uncloned_pack_is_unavailable_rather_than_a_broken_boot(monkeypatch,
                                                                   tmp_path):
    monkeypatch.setattr(gs._skillpack, "UPSTREAM", tmp_path / "nothing")
    probe = gs.health(None)
    assert probe["state"] == fabric.UNAVAILABLE
    assert "not cloned" in probe["detail"]


def test_an_uncloned_pack_returns_an_empty_catalogue_rather_than_raising(
        monkeypatch, tmp_path):
    monkeypatch.setattr(gs._skillpack, "UPSTREAM", tmp_path / "nothing")
    assert gs.call("catalogue", None) == {}


def test_an_unknown_operation_is_named():
    with pytest.raises(fabric.FabricError, match="no operation"):
        gs.call("obliterate", None)
