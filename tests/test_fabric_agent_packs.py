"""
S4: two more upstreams pinned as `roles`-family SKILL packs -- agents-team's
templates/rules/skills and awesome-claude-code-subagents' 158 briefs. Same
discipline as role_recipes / company_playbooks: names are cheap, one file is
read at a time, and there is no bulk read.
"""

from __future__ import annotations

import json
import pathlib
import re

import pytest

from friday import fabric
from friday.fabric_adapters import _skillpack
from friday.fabric_adapters import agents_team_pack as at
from friday.fabric_adapters import claude_subagents as cs

ROOT = pathlib.Path(__file__).resolve().parent.parent
LOCK_PATH = ROOT / "third_party" / "UPSTREAM_LOCK.json"

cloned_cs = pytest.mark.skipif(
    not _skillpack.pack_root(cs.UPSTREAM).is_dir(), reason="not cloned")
cloned_at = pytest.mark.skipif(
    not _skillpack.pack_root(at.UPSTREAM).is_dir(), reason="not cloned")


@pytest.fixture(autouse=True)
def _clean():
    fabric.reload()
    from friday import org
    org._CACHE["divisions"] = None
    yield
    fabric.reload()
    org._CACHE["divisions"] = None


# --- descriptors -------------------------------------------------------


@pytest.mark.parametrize("module", (cs, at))
def test_descriptor_commit_matches_the_lock(module):
    lock = json.loads(LOCK_PATH.read_text(encoding="utf-8"))
    assert re.match(r"^[0-9a-f]{40}$", module.DESCRIPTOR.commit)
    assert module.DESCRIPTOR.upstream in lock, (
        f"{module.DESCRIPTOR.upstream} is not in {LOCK_PATH}")
    assert module.DESCRIPTOR.commit == lock[module.DESCRIPTOR.upstream]["commit"]
    assert module.DESCRIPTOR.family == "roles"
    assert set(module.DESCRIPTOR.open_operations) == set(module.DESCRIPTOR.operations)


# --- claude_subagents (VoltAgent) --------------------------------------


@cloned_cs
def test_catalogue_covers_all_categories_and_is_non_empty():
    catalogue = cs.call("agents", None)
    assert set(catalogue) == set(cs.CATEGORIES)
    assert sum(len(names) for names in catalogue.values()) > 100


@cloned_cs
def test_search_finds_python_pro():
    hits = cs.call("find_agent", None, query="python")
    assert "python-pro" in hits


@cloned_cs
def test_recipe_reads_one_file_and_refuses_traversal_and_unknown_names():
    path = "categories/02-language-specialists/python-pro.md"
    text = cs.call("agent", None, path=path)
    assert "python-pro" in text
    with pytest.raises(fabric.FabricError, match="catalogue"):
        cs.call("agent", None, path="../../../.env")
    with pytest.raises(fabric.FabricError, match="catalogue"):
        cs.call("agent", None, path="categories/01-core-development/nope.md")


@cloned_cs
def test_category_operation_is_scoped_and_validated():
    names = cs.call("agent_category", None, name="02-language-specialists")
    assert "python-pro" in names
    with pytest.raises(fabric.FabricError):
        cs.call("agent_category", None, name="not-a-category")


# --- agents_team_pack ----------------------------------------------------


@cloned_at
def test_archetypes_and_rules_are_non_empty():
    assert len(at.call("archetypes", None)) == 8
    assert len(at.call("rules", None)) == 13


@cloned_at
def test_archetype_reads_one_file_and_refuses_traversal_and_unknown_names():
    text = at.call("archetype", None, name="orchestrator")
    assert "{{" in text  # still a template; read verbatim
    with pytest.raises(fabric.FabricError, match="unknown"):
        at.call("archetype", None, name="../../../.env")
    with pytest.raises(fabric.FabricError, match="unknown"):
        at.call("archetype", None, name="nope")


@cloned_at
def test_rule_and_skill_read_one_file_each():
    rule = at.call("rule", None, name="01-plan-first")
    assert "Plan-First" in rule
    skill = at.call("skill", None, name="team-gen")
    assert skill
    with pytest.raises(fabric.FabricError, match="unknown"):
        at.call("skill", None, name="../../../.env")


# --- absent clone --------------------------------------------------------


@pytest.mark.parametrize("module", (cs, at))
def test_absent_clone_reports_unavailable_without_raising(module, monkeypatch, tmp_path):
    monkeypatch.setattr(_skillpack, "UPSTREAM", tmp_path)
    probe = module.health(None)
    assert probe["state"] == fabric.UNAVAILABLE


# --- voice_brain surface --------------------------------------------------


def test_voice_brain_surface_exposes_the_new_reads(monkeypatch):
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    import friday.voice_brain as vb

    roles = vb._surface().get("roles", set())
    assert {"agents", "find_agent", "agent", "agent_category"} <= roles
    assert {"archetypes", "archetype", "rules", "rule", "skill"} <= roles


# --- org.py divisions -----------------------------------------------------


@cloned_cs
def test_org_divisions_gain_a_voltagent_category_and_total_grows(monkeypatch):
    from friday import org

    monkeypatch.setattr(org, "VOLT_UPSTREAM",
                        org.ROOT / "third_party" / "upstream" / "does-not-exist")
    org._CACHE["divisions"] = None
    agency_total = sum(d["size"] for d in org.divisions())
    monkeypatch.undo()

    org._CACHE["divisions"] = None
    full = org.divisions()
    full_total = sum(d["size"] for d in full)

    assert full_total > agency_total
    assert any(re.match(r"^\d{2}-", d["id"]) for d in full)
    state = org.state()
    assert state["agents_total"] == full_total
    assert "third_party/upstream/awesome-claude-code-subagents" in state["source"]


@cloned_cs
def test_agent_resolves_a_bare_name_to_its_catalogue_path():
    """find_agent returns names; the model must be able to read one by name."""
    text = cs.call("agent", None, path="scrum-master")
    assert "scrum" in text.lower()
    assert cs.call("agent", None, name="python-pro.md") == cs.call(
        "agent", None, path="categories/02-language-specialists/python-pro.md")
    with pytest.raises(fabric.FabricError, match="catalogue"):
        cs.call("agent", None, path="../scrum-master")
