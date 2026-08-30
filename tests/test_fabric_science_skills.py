"""
The licence gate on scientific-agent-skills.

Almost everything here is about four directories. The repository is MIT, and
`skills/docx`, `skills/pdf`, `skills/pptx` and `skills/xlsx` are not: they
carry Anthropic terms forbidding extraction, copying and derivative works.
Reading one into a prompt is the extraction those terms prohibit, so the
adapter must refuse - and must keep refusing when someone adds a fifth.
"""

from __future__ import annotations

import pytest

from friday import fabric
from friday.fabric_adapters import science_skills as sk

cloned = pytest.mark.skipif(
    not (sk._skillpack.pack_root(sk.UPSTREAM) / "skills").is_dir(),
    reason="scientific-agent-skills not cloned")

#: The four the audit found. Named here as an expectation, while the adapter
#: itself derives them - if upstream relicenses one, this test should fail and
#: make somebody look, rather than the adapter silently offering it.
RESTRICTED = {"docx", "pdf", "pptx", "xlsx"}


@pytest.fixture(autouse=True)
def clean():
    sk._blocked.cache_clear()
    fabric.reload()
    yield
    sk._blocked.cache_clear()
    fabric.reload()


# --- the gate --------------------------------------------------------------


@cloned
def test_the_anthropic_licensed_skills_are_blocked():
    assert RESTRICTED <= set(sk.call("blocked", None))


@cloned
def test_a_blocked_skill_is_not_in_the_catalogue():
    assert not (RESTRICTED & set(sk.call("catalogue", None)))


@cloned
def test_reading_a_blocked_skill_is_refused_with_the_licence_reason():
    with pytest.raises(fabric.FabricError, match="may not be read"):
        sk.call("skill", None, name="pdf")


@cloned
def test_every_block_states_which_file_and_why():
    for name, reason in sk.call("blocked", None).items():
        assert "LICENSE" in reason or "LICENCE" in reason, name
        assert len(reason) > 20, name


@cloned
def test_a_permissively_licensed_skill_is_still_offered():
    """
    The gate must key on what the licence says, not on the presence of a
    licence file. `pacsomatic` ships its own MIT LICENSE and is fine.
    """
    assert "pacsomatic" not in sk.call("blocked", None)
    assert "pacsomatic" in sk.call("catalogue", None)


@cloned
def test_most_of_the_pack_survives_the_gate():
    """A gate that blocked everything would also pass the tests above."""
    offered = sk.call("catalogue", None)
    assert len(offered) > 100
    assert len(sk.call("blocked", None)) < 10


# --- fail closed -----------------------------------------------------------


def test_an_unrecognised_licence_blocks_rather_than_allows():
    allowed, why = sk._classify("All rights reserved. Ask us nicely.")
    assert allowed is False
    assert "not recognised" in why


def test_a_non_commercial_licence_is_blocked():
    allowed, why = sk._classify("Attribution-NonCommercial 4.0 International")
    assert allowed is False
    assert "non-commercial" in why


def test_a_source_available_licence_is_blocked():
    allowed, _ = sk._classify("The Foo Enterprise Edition (EE) License")
    assert allowed is False


def test_anthropic_terms_are_named_specifically_not_just_refused():
    """The operator deserves the actual reason, not 'unrecognised'."""
    allowed, why = sk._classify(
        "© 2025 Anthropic, PBC. All rights reserved.\n"
        "ADDITIONAL RESTRICTIONS: users may not extract these materials")
    assert allowed is False
    assert "Anthropic" in why


@pytest.mark.parametrize("text", [
    "Permission is hereby granted, free of charge, to any person",
    "Apache License\nVersion 2.0",
    "BSD 3-Clause License",
    "CC0 1.0 Universal",
])
def test_recognised_permissive_licences_are_allowed(text):
    allowed, _ = sk._classify(text)
    assert allowed is True


@cloned
def test_a_newly_restricted_skill_would_be_excluded_without_a_code_change(
        monkeypatch, tmp_path):
    """
    The reason the gate is computed rather than a list of four names: upstream
    adding a fifth must not require anyone to notice.
    """
    pack = tmp_path / sk.UPSTREAM
    skills = pack / "skills"
    (skills / "brandnew").mkdir(parents=True)
    (skills / "brandnew" / "SKILL.md").write_text("---\nname: brandnew\n---\n",
                                                  encoding="utf-8")
    (skills / "brandnew" / "LICENSE.txt").write_text(
        "ADDITIONAL RESTRICTIONS: you may not copy these materials",
        encoding="utf-8")
    (skills / "ordinary").mkdir(parents=True)
    (skills / "ordinary" / "SKILL.md").write_text("---\nname: ordinary\n---\n",
                                                  encoding="utf-8")
    (pack / "README.md").write_text("x", encoding="utf-8")

    monkeypatch.setattr(sk._skillpack, "UPSTREAM", tmp_path)
    sk._blocked.cache_clear()
    assert "brandnew" in sk.call("blocked", None)
    assert "ordinary" in sk.call("catalogue", None)


# --- laziness --------------------------------------------------------------


@cloned
def test_the_catalogue_is_names_and_descriptions_not_procedures():
    for name, entry in sk.call("catalogue", None).items():
        assert set(entry) == {"description", "path"}
        assert len(entry["description"]) <= 300, name


def test_there_is_no_bulk_read_operation():
    operations = set(fabric.get("science_skills").operations)
    assert operations == {"catalogue", "search", "skill", "blocked"}


@cloned
def test_search_narrows_a_hundred_and_fifty_skills_to_a_shortlist():
    found = sk.call("search", None, query="single cell rna")
    assert 0 < len(found) <= 10
    assert {"scanpy", "anndata"} & {row["skill"] for row in found}


def test_search_without_a_query_is_refused():
    with pytest.raises(fabric.FabricError, match="query"):
        sk.call("search", None, query="")


# --- descriptor ------------------------------------------------------------


def test_it_executes_no_upstream_code_and_is_pinned():
    import json
    import pathlib

    provider = fabric.get("science_skills")
    assert provider.integration_mode == fabric.SKILL
    assert provider.imported is False
    lock = json.loads(
        (pathlib.Path(__file__).resolve().parent.parent
         / "third_party" / "UPSTREAM_LOCK.json").read_text(encoding="utf-8"))
    assert provider.commit == lock[sk.UPSTREAM]["commit"]


def test_an_uncloned_pack_is_unavailable_rather_than_a_broken_boot(
        monkeypatch, tmp_path):
    monkeypatch.setattr(sk._skillpack, "UPSTREAM", tmp_path / "nothing")
    sk._blocked.cache_clear()
    assert sk.health(None)["state"] == fabric.UNAVAILABLE
    assert sk.call("catalogue", None) == {}


def test_an_unknown_operation_is_named():
    with pytest.raises(fabric.FabricError, match="no operation"):
        sk.call("synthesise", None)
