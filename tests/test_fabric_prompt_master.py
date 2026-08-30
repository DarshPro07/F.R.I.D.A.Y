"""
prompt-master as a Friday fabric skill.

A single skill with references loaded on demand, in the `writing` family beside
no_ai_slop. The assertions that matter: it executes no upstream code, it is
pinned, its reference catalogue is the read allowlist, and it coexists with the
other writing provider rather than replacing it.
"""

from __future__ import annotations

import pytest

from friday import fabric
from friday.fabric_adapters import prompt_skill as pm

cloned = pytest.mark.skipif(
    not (pm._skillpack.pack_root(pm.UPSTREAM) / "SKILL.md").is_file(),
    reason="prompt-master not cloned")


@pytest.fixture(autouse=True)
def clean():
    fabric.reload()
    yield
    fabric.reload()


def test_it_is_a_writing_skill_pinned_and_codeless():
    import json
    import pathlib

    provider = fabric.get("prompt_master")
    assert provider.family == "writing"
    assert provider.integration_mode == fabric.SKILL
    assert provider.imported is False
    assert provider.model_required is False
    lock = json.loads(
        (pathlib.Path(__file__).resolve().parent.parent
         / "third_party" / "UPSTREAM_LOCK.json").read_text(encoding="utf-8"))
    assert provider.commit == lock[pm.UPSTREAM]["commit"]


def test_it_coexists_with_no_ai_slop_rather_than_replacing_it():
    ids = {p.id for p in fabric.by_family("writing")}
    assert {"prompt_master", "no_ai_slop"} <= ids


@cloned
def test_the_instructions_load():
    body = pm.call("instructions", None)
    assert "prompt" in body.lower() and len(body) > 1000


@cloned
def test_the_references_are_named_and_loadable():
    refs = pm.call("references", None)
    assert refs and all(r.endswith(".md") for r in refs)
    body = pm.call("reference", None, name=refs[0])
    assert isinstance(body, str) and body


@cloned
def test_a_reference_outside_the_catalogue_is_refused():
    with pytest.raises(fabric.FabricError, match="reference"):
        pm.call("reference", None, name="../../../../.env")


def test_reference_without_a_name_is_refused():
    with pytest.raises(fabric.FabricError, match="name"):
        pm.call("reference", None, name="")


def test_an_unknown_operation_is_named():
    with pytest.raises(fabric.FabricError, match="no operation"):
        pm.call("generate", None)


@cloned
def test_it_is_reachable_through_the_capability_bridge():
    """End to end: capability_use('writing','instructions') reaches it."""
    from friday.tools import fabric_control

    captured = {}
    fabric_control.register(type("R", (), {
        "tool": lambda self, *a, **k: (lambda fn: captured.setdefault(fn.__name__, fn) or fn)
        if not (a and callable(a[0])) else (captured.setdefault(a[0].__name__, a[0]) or a[0])})())
    fabric.reload()
    out = captured["capability_use"]("writing", "instructions", None)
    assert out["status"] == "succeeded", out
    assert "prompt" in str(out["output"]).lower()
