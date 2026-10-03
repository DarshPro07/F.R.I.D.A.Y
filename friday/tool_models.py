"""
The two small local models behind tool retrieval (PRD v7 §19.3, CTX-002).

    embedder   BAAI/bge-small-en-v1.5 (MIT). Dense retrieval: a query and each
               tool's L1 text become unit vectors; cosine ranks them.
    reranker   cross-encoder/ms-marco-MiniLM-L6-v2 (Apache-2.0). Reads the
               query with one candidate at a time and scores the pair; it
               reorders the few candidates the first stage kept.

Both run on onnxruntime, CPU only, and nothing here is a hosted API. Each is a
capability-fabric provider (`fabric_adapters/tool_embedder.py`,
`tool_reranker.py`) pinned to a Hub revision and a file sha256; this module is
the part both adapters and the retriever share so the pin lives in one place.

`provision()` is the only code that touches the network. `load()` reads only
what is already on disk, verifies the sha256 of every file against the pin, and
raises `ModelUnavailable` when anything is absent or altered. Callers fall back
to the model-free retrievers; a missing model never breaks boot
(NON_NEGOTIABLE 15). Weights are untrusted until their hash matches
(NON_NEGOTIABLE 6).
"""
from __future__ import annotations

import hashlib
import os
import threading
from dataclasses import dataclass
from pathlib import Path

import numpy as np

QUERY_PREFIX = "Represent this sentence for searching relevant passages: "

_TOKENIZER_SHA = "d241a60d5e8f04cc1b2b3e9ef7a4921b27bf526d9f6050ab90f9267a1f9e5c66"


@dataclass(frozen=True)
class ModelPin:
    """A Hub repo at one revision, and the sha256 of each file we load."""

    id: str
    repo: str
    revision: str
    license: str
    files: tuple[tuple[str, str], ...]       # (path in repo, sha256)
    max_length: int
    kind: str = "embedder"                   # "embedder" | "reranker"
    dim: int = 384                           # embedding width (embedders)

    @property
    def model_file(self) -> str:
        return self.files[0][0]


EMBEDDER = ModelPin(
    id="tool_embedder",
    repo="BAAI/bge-small-en-v1.5",
    revision="5c38ec7c405ec4b44b94cc5a9bb96e735b38267a",
    license="MIT",
    files=(("onnx/model.onnx",
            "828e1496d7fabb79cfa4dcd84fa38625c0d3d21da474a00f08db0f559940cf35"),
           ("tokenizer.json", _TOKENIZER_SHA)),
    max_length=256,
)
RERANKER = ModelPin(
    id="tool_reranker",
    repo="cross-encoder/ms-marco-MiniLM-L6-v2",
    revision="233902d25c440f23af6f7d6e94d2946bac0bee0a",
    license="Apache-2.0",
    files=(("onnx/model.onnx",
            "5d3e70fd0c9ff14b9b5169a51e957b7a9c74897afd0a35ce4bd318150c1d4d4a"),
           ("tokenizer.json", _TOKENIZER_SHA)),
    max_length=512,
    kind="reranker",
)
# Measured against EMBEDDER on FRIDAY's benchmark (CTX-002); pinned so the
# comparison is reproducible, not because it is the default.
EMBEDDER_BASE = ModelPin(
    id="tool_embedder_base",
    repo="BAAI/bge-base-en-v1.5",
    revision="a5beb1e3e68b9ab74eb54cfd186867f64f240e1a",
    license="MIT",
    files=(("onnx/model.onnx",
            "9bc579acdba21c253c62a9bf866891355a63ffa3442b52c8a37d75b2ccb91848"),
           ("tokenizer.json", _TOKENIZER_SHA)),
    max_length=256,
    dim=768,
)
PINS = {pin.id: pin for pin in (EMBEDDER, EMBEDDER_BASE, RERANKER)}


class ModelUnavailable(RuntimeError):
    """The model files are absent, altered or unusable. Retrieval falls back."""


def models_dir() -> Path:
    """FRIDAY_MODELS_DIR, else <state>/cache/models."""
    override = os.environ.get("FRIDAY_MODELS_DIR", "").strip()
    if override:
        return Path(override)
    from friday import config
    return Path(config.CACHE_DIR) / "models"


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def _download(pin: ModelPin, filename: str, *, offline: bool) -> Path:
    from huggingface_hub import hf_hub_download
    root = models_dir()
    if not offline:
        root.mkdir(parents=True, exist_ok=True)
    try:
        return Path(hf_hub_download(
            pin.repo, filename, revision=pin.revision, cache_dir=str(root),
            local_files_only=offline))
    except Exception as exc:                                  # noqa: BLE001
        raise ModelUnavailable(f"{pin.id}: {filename} not available: {exc}") from exc


def _verified(pin: ModelPin, *, offline: bool) -> dict[str, Path]:
    paths: dict[str, Path] = {}
    for filename, expected in pin.files:
        path = _download(pin, filename, offline=offline)
        actual = sha256_of(path)
        if actual != expected:
            raise ModelUnavailable(
                f"{pin.id}: {filename} sha256 {actual[:12]} does not match the "
                f"pin {expected[:12]}")
        paths[filename] = path
    return paths


def provision(model_id: str) -> dict[str, str]:
    """Download a pinned model into the models dir and verify every hash."""
    pin = PINS[model_id]
    return {name: str(path) for name, path in _verified(pin, offline=False).items()}


# --- runtime ---------------------------------------------------------------


class _Session:
    """An onnx session with its tokenizer. Construct through `load`."""

    def __init__(self, pin: ModelPin, paths: dict[str, Path]):
        import onnxruntime
        from tokenizers import Tokenizer

        self._dim = pin.dim
        options = onnxruntime.SessionOptions()
        options.log_severity_level = 3
        self._session = onnxruntime.InferenceSession(
            str(paths[pin.model_file]), sess_options=options,
            providers=["CPUExecutionProvider"])
        self._wanted = {i.name for i in self._session.get_inputs()}
        self._tokenizer = Tokenizer.from_file(str(paths["tokenizer.json"]))
        self._tokenizer.enable_truncation(max_length=pin.max_length)
        self._tokenizer.enable_padding()

    def _run(self, encoded) -> np.ndarray:
        feed = {
            "input_ids": np.array([e.ids for e in encoded], dtype=np.int64),
            "attention_mask": np.array([e.attention_mask for e in encoded], dtype=np.int64),
            "token_type_ids": np.array([e.type_ids for e in encoded], dtype=np.int64),
        }
        return self._session.run(None, {k: v for k, v in feed.items() if k in self._wanted})[0]


class Embedder(_Session):
    def embed(self, texts: list[str], *, query: bool = False, batch: int = 32) -> np.ndarray:
        """Unit vectors (the CLS row), one per text."""
        if not texts:
            return np.zeros((0, self._dim), dtype=np.float32)
        if query:
            texts = [QUERY_PREFIX + t for t in texts]
        out = []
        for start in range(0, len(texts), batch):
            hidden = self._run(self._tokenizer.encode_batch(texts[start:start + batch]))
            cls = hidden[:, 0, :].astype(np.float32)
            out.append(cls / np.maximum(np.linalg.norm(cls, axis=1, keepdims=True), 1e-12))
        return np.concatenate(out)


class Reranker(_Session):
    def score(self, query: str, documents: list[str]) -> list[float]:
        """Relevance logit of each document for the query; higher is better."""
        if not documents:
            return []
        logits = self._run(self._tokenizer.encode_batch([(query, d) for d in documents]))
        return [float(x) for x in np.asarray(logits).reshape(len(documents), -1)[:, 0]]


_lock = threading.Lock()
_loaded: dict[str, object] = {}
_failed: dict[str, str] = {}


def load(model_id: str):
    """The session for a pin, loaded once. Raises ModelUnavailable, also once
    remembered, so a missing file costs one hash pass and not one per request."""
    with _lock:
        if model_id in _loaded:
            return _loaded[model_id]
        if model_id in _failed:
            raise ModelUnavailable(_failed[model_id])
        pin = PINS[model_id]
        try:
            paths = _verified(pin, offline=True)
            session = (Embedder if pin.kind == "embedder" else Reranker)(pin, paths)
        except ModelUnavailable as exc:
            _failed[model_id] = str(exc)
            raise
        except Exception as exc:                              # noqa: BLE001
            _failed[model_id] = f"{model_id}: could not start: {exc}"
            raise ModelUnavailable(_failed[model_id]) from exc
        _loaded[model_id] = session
        return session


def unload() -> None:
    """Forget loaded sessions and remembered failures (tests, provisioning)."""
    with _lock:
        _loaded.clear()
        _failed.clear()


def embedder(model_id: str = EMBEDDER.id) -> Embedder | None:
    try:
        return load(model_id)
    except ModelUnavailable:
        return None


def reranker() -> Reranker | None:
    try:
        return load(RERANKER.id)
    except ModelUnavailable:
        return None
