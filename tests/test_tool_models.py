"""CTX-002: the pinned local models - provisioning, verification, fallback."""
from __future__ import annotations

import pytest

from friday import fabric, tool_models


@pytest.fixture(autouse=True)
def _fresh(monkeypatch, tmp_path):
    monkeypatch.setenv("FRIDAY_MODELS_DIR", str(tmp_path))
    tool_models.unload()
    yield
    tool_models.unload()


def test_models_dir_honours_the_override_else_the_cache(monkeypatch, tmp_path):
    assert tool_models.models_dir() == tmp_path
    monkeypatch.delenv("FRIDAY_MODELS_DIR")
    from friday import config
    assert tool_models.models_dir() == config.CACHE_DIR / "models"


def test_absent_files_mean_unavailable_not_a_crash():
    with pytest.raises(tool_models.ModelUnavailable):
        tool_models.load(tool_models.EMBEDDER.id)
    assert tool_models.embedder() is None and tool_models.reranker() is None


def test_a_file_whose_hash_does_not_match_the_pin_is_refused(monkeypatch, tmp_path):
    bad = tmp_path / "model.onnx"
    bad.write_bytes(b"not the pinned weights")
    monkeypatch.setattr(tool_models, "_download", lambda pin, name, *, offline: bad)
    with pytest.raises(tool_models.ModelUnavailable, match="does not match the pin"):
        tool_models.load(tool_models.RERANKER.id)


def test_both_models_are_pinned_providers_in_the_fabric():
    for pin in tool_models.PINS.values():
        provider = fabric.get(pin.id)
        assert provider.upstream == pin.repo and provider.commit == pin.revision
        assert provider.license_mode == fabric.PERMISSIVE
        assert provider.integration_mode == fabric.ADAPTER
        assert "provision" in provider.operations


def test_an_unprovisioned_model_activates_as_unavailable_and_boot_survives():
    for pin in tool_models.PINS.values():
        assert fabric.activate(pin.id).state == fabric.UNAVAILABLE
        fabric.deactivate(pin.id)


def _have_models() -> bool:
    return tool_models.embedder() is not None and tool_models.reranker() is not None


def test_the_real_models_rank_a_paraphrase(monkeypatch):
    monkeypatch.delenv("FRIDAY_MODELS_DIR")
    tool_models.unload()
    if not _have_models():
        pytest.skip("pinned model files are not provisioned under FRIDAY_MODELS_DIR "
                    "or <cache>/models")
    embedder, reranker = tool_models.embedder(), tool_models.reranker()
    vectors = embedder.embed(["play a song", "delete a document"])
    query = embedder.embed(["put on some music"], query=True)[0]
    assert float(vectors[0] @ query) > float(vectors[1] @ query)
    scores = reranker.score("put on some music", ["play a song", "delete a document"])
    assert scores[0] > scores[1]
