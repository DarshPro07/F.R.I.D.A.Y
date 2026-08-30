"""
The markdown-only providers, and the two rules that make them safe.

They install nothing and start nothing, so most of what could go wrong here is
about *how much* they hand a model and *which file* they are willing to read.
Both are enforced in code and asserted here.
"""

from __future__ import annotations

import pytest

from friday import contracts as c
from friday import fabric
from friday.fabric_adapters import _skillpack

PACKS = ("no_ai_slop", "adhd_mode", "role_recipes")

cloned = pytest.mark.skipif(
    not (_skillpack.UPSTREAM / "agency-agents").is_dir(),
    reason="upstream packs not cloned; run scripts/fabric_upstreams.py clone")


@pytest.fixture(autouse=True)
def clean():
    fabric.reload()
    yield
    fabric.reload()


# --- descriptors -----------------------------------------------------------


@pytest.mark.parametrize("provider_id", PACKS)
def test_a_skill_pack_executes_no_upstream_code(provider_id):
    """
    SKILL mode is the claim that nothing from this upstream runs. If one of
    these ever declared ADAPTER it would be linking markdown-adjacent code into
    Friday's process, and the licence and security review would both be wrong.
    """
    provider = fabric.get(provider_id)
    assert provider.integration_mode == fabric.SKILL
    assert provider.imported is False
    assert provider.model_required is False
    assert provider.cost_class == "free"


@pytest.mark.parametrize("provider_id", PACKS)
def test_a_skill_pack_is_pinned(provider_id):
    assert len(fabric.get(provider_id).commit) == 40


def test_the_packs_cover_three_distinct_families():
    families = {fabric.get(p).family for p in PACKS}
    assert families == {"writing", "presentation", "roles"}


# --- the token rule --------------------------------------------------------


@cloned
def test_the_catalogue_is_names_only_and_far_cheaper_than_the_recipes():
    """
    318 recipes must never arrive together. The catalogue is what a router
    chooses from, and it has to be small enough to be worth having.
    """
    names = fabric.call("role_recipes", "catalogue").output
    assert len(names) > 100, "the pack should be large; that is the point"
    one = fabric.call("role_recipes", "recipe", path=names[0]).output
    assert len("\n".join(names)) < len(one) * len(names) / 10, \
        "the catalogue is not meaningfully cheaper than reading everything"


@cloned
def test_there_is_no_bulk_read_operation():
    """The absent feature is the feature."""
    operations = set(fabric.get("role_recipes").operations)
    assert not (operations & {"all", "everything", "load_all", "read_all"})


def test_a_single_file_is_capped(monkeypatch, tmp_path):
    """A recipe longer than the cap is a document to consult, not context."""
    pack = tmp_path / "huge-pack"
    pack.mkdir()
    (pack / "big.md").write_text("x" * (_skillpack.MAX_CHARS + 500),
                                 encoding="utf-8")
    monkeypatch.setattr(_skillpack, "UPSTREAM", tmp_path)
    text = _skillpack.read("huge-pack", "big.md")
    assert len(text) < _skillpack.MAX_CHARS + 200
    assert "truncated" in text


def test_nothing_is_read_at_import():
    """
    Lazy means lazy. Importing the adapter must not touch the pack, or the
    saving is spent at startup for every session that never asks.
    """
    import importlib

    calls = []
    original = _skillpack.read
    _skillpack.read = lambda *a, **k: calls.append(a) or ""
    try:
        importlib.reload(
            importlib.import_module("friday.fabric_adapters.role_recipes"))
        assert calls == []
    finally:
        _skillpack.read = original


# --- the read rule ---------------------------------------------------------


@cloned
def test_a_path_outside_the_catalogue_is_refused():
    """
    `recipe` takes a path from a model. Without the catalogue allowlist that is
    an arbitrary read of anything under third_party - or, with enough `..`, of
    the .env file two directories up.
    """
    result = fabric.call("role_recipes", "recipe", path="../../../.env")
    assert result.status == c.FAILED
    assert "not in this pack's catalogue" in result.error


@cloned
def test_an_absolute_path_is_refused():
    result = fabric.call("role_recipes", "recipe", path="C:/Windows/win.ini")
    assert result.status == c.FAILED


@cloned
def test_an_unknown_division_names_the_known_ones():
    result = fabric.call("role_recipes", "catalogue", division="nonsense")
    assert result.status == c.FAILED
    assert "known:" in result.error


# --- health honesty --------------------------------------------------------


def test_a_pack_that_was_never_cloned_is_unavailable(monkeypatch, tmp_path):
    monkeypatch.setattr(_skillpack, "UPSTREAM", tmp_path)
    assert _skillpack.health("no-ai-slop", "x.md")["state"] == fabric.UNAVAILABLE


def test_a_cloned_pack_missing_its_entry_file_is_degraded(monkeypatch, tmp_path):
    """
    Present but wrong is a different fact from absent, and routing around it
    needs to know which.
    """
    (tmp_path / "half-pack").mkdir()
    monkeypatch.setattr(_skillpack, "UPSTREAM", tmp_path)
    probe = _skillpack.health("half-pack", "skills/thing/SKILL.md")
    assert probe["state"] == fabric.DEGRADED
    assert "missing" in probe["detail"]


@cloned
def test_the_real_packs_report_ready():
    for provider_id in PACKS:
        assert fabric.activate(provider_id).state == fabric.READY
