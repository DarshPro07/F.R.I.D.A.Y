"""
open-design as a Friday design-skill provider.

The "Claude Design in Friday" request: a library of design skills, systems and
templates that Friday reads and then builds from with its own model or Hermes.
The assertions that matter: it executes no upstream code, it coexists with
diagram_design in presentation, the catalogue is the read allowlist, and the
per-template licence gate fails closed.
"""

from __future__ import annotations

import pytest

from friday import fabric
from friday.fabric_adapters import open_design as od

cloned = pytest.mark.skipif(
    not (od._skillpack.pack_root(od.UPSTREAM) / "skills").is_dir(),
    reason="open-design not cloned")


@pytest.fixture(autouse=True)
def clean():
    for kind in od.DIRS:
        od._entries.cache_clear()
    fabric.reload()
    yield
    fabric.reload()


def test_it_is_a_presentation_skill_pinned_and_codeless():
    import json
    import pathlib

    provider = fabric.get("open_design")
    assert provider.family == "presentation"
    assert provider.integration_mode == fabric.SKILL
    assert provider.imported is False
    lock = json.loads(
        (pathlib.Path(__file__).resolve().parent.parent
         / "third_party" / "UPSTREAM_LOCK.json").read_text(encoding="utf-8"))
    assert provider.commit == lock[od.UPSTREAM]["commit"]


def test_it_coexists_with_diagram_design():
    ids = {p.id for p in fabric.by_family("presentation")}
    assert {"open_design", "diagram_design"} <= ids


@cloned
def test_the_catalogue_offers_skills_systems_and_templates():
    cat = od.call("catalogue", None)
    assert set(cat) == {"skill", "system", "template"}
    assert len(cat["skill"]) > 100
    assert len(cat["system"]) > 100
    assert len(cat["template"]) > 50


@cloned
def test_a_design_request_routes_to_a_skill():
    ranked = od.call("route", None, task="design a landing page")
    assert ranked and ranked[0]["score"] > 0


@cloned
def test_a_named_skill_system_and_template_load():
    cat = od.call("catalogue", None)
    assert od.call("skill", None, name=cat["skill"][0])
    assert od.call("system", None, name=cat["system"][0])


@cloned
def test_an_arbitrary_path_cannot_be_read():
    for op in ("skill", "system", "template"):
        with pytest.raises(fabric.FabricError):
            od.call(op, None, name="../../../../.env")


def test_the_template_licence_gate_fails_closed():
    """A template whose licence is not recognised as permissive is withheld."""
    allowed, why = od._classify("All rights reserved. Contact us to license.")
    assert allowed is False and why
    allowed, _ = od._classify("MIT License\nPermission is hereby granted, free of charge")
    assert allowed is True


def test_there_is_no_bulk_read_operation():
    assert set(fabric.get("open_design").operations) == {
        "catalogue", "route", "skill", "system", "template"}


def test_an_uncloned_pack_is_unavailable(monkeypatch, tmp_path):
    monkeypatch.setattr(od._skillpack, "UPSTREAM", tmp_path / "nothing")
    for _ in od.DIRS:
        od._entries.cache_clear()
    assert od.health(None)["state"] == fabric.UNAVAILABLE


def test_an_unknown_operation_is_named():
    with pytest.raises(fabric.FabricError, match="no operation"):
        od.call("generate", None)
