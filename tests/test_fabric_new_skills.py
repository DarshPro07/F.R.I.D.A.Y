"""
The security and diagram skill packs.

security_skills is the one with teeth: 818 procedures, many offensive, so the
question is not whether it loads but whether reading an attack procedure is
gated. diagram_design is an ordinary skill and is here mostly to assert it
does not accidentally grow a second file-read escape.
"""

from __future__ import annotations

import pytest

from friday import fabric
from friday.fabric_adapters import diagram_skill as dg
from friday.fabric_adapters import security_skills as ss

sec_cloned = pytest.mark.skipif(
    not (ss._skillpack.pack_root(ss.UPSTREAM) / "index.json").is_file(),
    reason="anthropic-cybersecurity-skills not cloned")
dg_cloned = pytest.mark.skipif(
    not (dg._skillpack.pack_root(dg.UPSTREAM) / "skills").is_dir(),
    reason="diagram-design not cloned")


@pytest.fixture(autouse=True)
def clean():
    ss._index.cache_clear()
    fabric.reload()
    yield
    ss._index.cache_clear()
    fabric.reload()


# --- security_skills: the scope gate ---------------------------------------


def test_security_declares_restricted_risk_and_a_scope_permission():
    provider = fabric.get("security_skills")
    assert provider.family == "security"
    assert provider.risk == "restricted"
    assert "security.authorized_scope" in provider.permissions


@sec_cloned
def test_reading_a_procedure_without_a_scope_is_refused():
    with pytest.raises(fabric.FabricError, match="authorized_scope"):
        ss.call("skill", None, name="abusing-dpapi-for-credential-access")


@sec_cloned
def test_a_blank_scope_is_not_a_scope():
    with pytest.raises(fabric.FabricError, match="authorized_scope"):
        ss.call("skill", None, name="abusing-dpapi-for-credential-access",
                authorized_scope="   ")


@sec_cloned
def test_a_procedure_read_with_a_scope_records_the_scope():
    result = ss.call("skill", None,
                     name="abusing-dpapi-for-credential-access",
                     authorized_scope="engagement RT-2026-014, owned lab")
    assert result["authorized_scope"] == "engagement RT-2026-014, owned lab"
    assert result["skill"] == "abusing-dpapi-for-credential-access"
    assert len(result["procedure"]) > 200


@sec_cloned
def test_catalogue_and_search_are_open_because_knowing_is_not_doing():
    """A defender must be able to look up what a technique is."""
    assert len(ss.call("catalogue", None)) > 500
    found = ss.call("search", None, query="detect credential dumping")
    assert 0 < len(found) <= 10


@sec_cloned
def test_search_reaches_defensive_skills_by_description():
    names = {row["skill"] for row in
             ss.call("search", None, query="windows event log analysis")}
    assert any("event-log" in n or "splunk" in n for n in names)


@sec_cloned
def test_the_catalogue_carries_no_procedure_text():
    """catalogue reads the index, never a SKILL.md. Descriptions, not bodies."""
    for name, entry in ss.call("catalogue", None).items():
        assert set(entry) == {"description", "domain"}
        assert len(entry["description"]) <= 400


def test_security_has_no_bulk_read_operation():
    assert set(fabric.get("security_skills").operations) == {
        "catalogue", "search", "skill"}


def test_security_is_pinned_to_the_audited_commit():
    import json
    import pathlib

    lock = json.loads(
        (pathlib.Path(__file__).resolve().parent.parent
         / "third_party" / "UPSTREAM_LOCK.json").read_text(encoding="utf-8"))
    assert fabric.get("security_skills").commit == lock[ss.UPSTREAM]["commit"]


def test_security_uncloned_is_unavailable(monkeypatch, tmp_path):
    monkeypatch.setattr(ss._skillpack, "UPSTREAM", tmp_path / "nothing")
    ss._index.cache_clear()
    assert ss.health(None)["state"] == fabric.UNAVAILABLE


# --- diagram_design --------------------------------------------------------


def test_diagram_is_a_presentation_skill_pinned_and_codeless():
    import json
    import pathlib

    provider = fabric.get("diagram_design")
    assert provider.family == "presentation"
    assert provider.integration_mode == fabric.SKILL
    assert provider.imported is False
    lock = json.loads(
        (pathlib.Path(__file__).resolve().parent.parent
         / "third_party" / "UPSTREAM_LOCK.json").read_text(encoding="utf-8"))
    assert provider.commit == lock[dg.UPSTREAM]["commit"]


@dg_cloned
def test_the_instructions_load_and_the_references_are_named():
    assert len(dg.call("instructions", None)) > 1000
    references = dg.call("references", None)
    assert references and all(name.endswith(".md") for name in references)


@dg_cloned
def test_a_reference_outside_the_catalogue_is_refused():
    """The reference catalogue is the read allowlist."""
    with pytest.raises(fabric.FabricError, match="reference"):
        dg.call("reference", None, name="../../../../.env")


@dg_cloned
def test_a_named_reference_loads():
    references = dg.call("references", None)
    body = dg.call("reference", None, name=references[0])
    assert isinstance(body, str) and body


def test_diagram_unknown_operation_is_named():
    with pytest.raises(fabric.FabricError, match="no operation"):
        dg.call("render", None)


# --- both keep the family invariants ---------------------------------------


def test_the_new_providers_do_not_shadow_an_existing_capability():
    from friday import capabilities

    for provider_id, adapter in (("security_skills", ss),
                                 ("diagram_design", dg)):
        provider = fabric.get(provider_id)
        # A provider id must not collide with a capability id.
        assert provider.id not in capabilities.CAPABILITIES


def test_each_new_family_is_a_declared_fabric_family():
    for provider_id in ("security_skills", "diagram_design"):
        assert fabric.get(provider_id).family in fabric.FAMILIES
