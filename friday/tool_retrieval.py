"""
Retrievers for progressive disclosure (PRD v7 §19.3, CTX-002, Amendment A5).

Three ways to rank a catalogue against a request, all local and model-free so a
voice turn pays no network or model latency:

    bm25     lexical: term frequency against a document's length, rare words
             weigh more
    dense    a hashed character-n-gram vector, cosine similarity. It is not a
             learned embedding; it is the local stand-in that tolerates the
             inflections and typos BM25 misses ("playing" ~ "play")
    hybrid   reciprocal-rank fusion of the two

`Disclosure` applies the same three levels to anything that is not a tool
(skills, memory entries, documents): L0 a one-line catalogue entry, L1 a
snippet found by search, L2 the full body, fetched only on request.
"""
from __future__ import annotations

import hashlib
import math
import re
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

_TOKEN = re.compile(r"[a-z0-9]+")
_STOP = frozenset("""
a an and are as at be by can could do for from has have i in is it me my of on or
our please she that the then there they this to us was we what when which will
with would you your
""".split())
_DIM = 1024


def tokens(text: str) -> list[str]:
    """Content words, lowercased; underscores and punctuation split words."""
    return [t for t in _TOKEN.findall((text or "").lower()) if t not in _STOP and len(t) > 1]


def _stem(word: str) -> str:
    for suffix in ("ing", "ies", "ed", "es", "s"):
        if word.endswith(suffix) and len(word) - len(suffix) >= 3:
            return word[: -len(suffix)] + ("y" if suffix == "ies" else "")
    return word


def stems(text: str) -> list[str]:
    return [_stem(t) for t in tokens(text)]


class BM25:
    """Okapi BM25 over pre-tokenised documents."""

    def __init__(self, documents: list[list[str]], *, k1: float = 1.4, b: float = 0.6):
        self.k1, self.b = k1, b
        self.documents = [self._counts(doc) for doc in documents]
        self.lengths = [len(doc) for doc in documents]
        self.average = (sum(self.lengths) / len(self.lengths)) if self.lengths else 0.0
        frequency: dict[str, int] = {}
        for doc in self.documents:
            for term in doc:
                frequency[term] = frequency.get(term, 0) + 1
        count = len(documents)
        self.idf = {term: math.log(1 + (count - n + 0.5) / (n + 0.5))
                    for term, n in frequency.items()}

    @staticmethod
    def _counts(doc: list[str]) -> dict[str, int]:
        counts: dict[str, int] = {}
        for term in doc:
            counts[term] = counts.get(term, 0) + 1
        return counts

    def scores(self, query: list[str]) -> list[float]:
        out = []
        for doc, length in zip(self.documents, self.lengths):
            norm = self.k1 * (1 - self.b + self.b * length / self.average) if self.average else self.k1
            score = 0.0
            for term in set(query):
                tf = doc.get(term)
                if tf:
                    score += self.idf.get(term, 0.0) * tf * (self.k1 + 1) / (tf + norm)
            out.append(score)
        return out


def _vector(text: str) -> np.ndarray:
    """Hashed character 3-grams of the stemmed words, L2-normalised."""
    vec = np.zeros(_DIM, dtype=np.float32)
    for word in stems(text):
        padded = f"^{word}$"
        for i in range(max(1, len(padded) - 2)):
            gram = padded[i:i + 3]
            # Python's hash() is salted per process; a stable hash keeps the
            # benchmark reproducible.
            h = 2166136261
            for ch in gram:
                h = ((h ^ ord(ch)) * 16777619) & 0xFFFFFFFF
            vec[h % _DIM] += 1.0
    norm = float(np.linalg.norm(vec))
    return vec / norm if norm else vec


@dataclass
class Index:
    """A searchable catalogue: ids with the text each is found by."""

    ids: list[str]
    texts: list[str]
    _bm25: BM25 = field(init=False, repr=False)
    _matrix: np.ndarray = field(init=False, repr=False)

    def __post_init__(self):
        self._bm25 = BM25([stems(text) for text in self.texts])
        self._matrix = (np.stack([_vector(text) for text in self.texts])
                        if self.texts else np.zeros((0, _DIM), dtype=np.float32))

    def _rank(self, scores) -> list[int]:
        return [i for i in sorted(range(len(self.ids)), key=lambda i: (-scores[i], self.ids[i]))
                if scores[i] > 0]

    def lexical(self, query: str) -> list[str]:
        return [self.ids[i] for i in self._rank(self._bm25.scores(stems(query)))]

    def dense(self, query: str) -> list[str]:
        if not len(self.ids):
            return []
        return [self.ids[i] for i in self._rank((self._matrix @ _vector(query)).tolist())]

    def hybrid(self, query: str, *, k: int = 20) -> list[str]:
        """Reciprocal-rank fusion; k damps the head so neither list dominates."""
        fused: dict[str, float] = {}
        for ranking in (self.lexical(query), self.dense(query)):
            for rank, name in enumerate(ranking):
                fused[name] = fused.get(name, 0.0) + 1.0 / (k + rank + 1)
        return sorted(fused, key=lambda name: (-fused[name], name))


class Retriever:
    """
    The shipped ranking: lexical and learned-dense search fused, then a
    cross-encoder reorders the head.

    `embedder` and `reranker` are `tool_models` sessions (or any object with
    `embed(texts, query=)` / `score(query, docs)`); either may be None, and
    then that stage is skipped - with neither, this is `Index.hybrid`. So a
    machine without the model files still ranks, a little worse, and boots.
    Document vectors are cached on disk under a key made of the model
    revision and the catalogue text, so a restart re-embeds nothing.
    """

    RERANK_K = 20

    def __init__(self, ids: list[str], texts: list[str], *, embedder=None, reranker=None,
                 cache_dir: Path | None = None, revision: str = "", rerank_k: int = RERANK_K):
        self.index = Index(ids, texts)
        self.ids, self.texts = ids, texts
        self.embedder, self.reranker = embedder, reranker
        self.rerank_k = min(rerank_k, self.RERANK_K)
        self._vectors = self._document_vectors(cache_dir, revision) if embedder else None

    def _document_vectors(self, cache_dir: Path | None, revision: str) -> np.ndarray:
        path = None
        if cache_dir is not None:
            key = hashlib.sha256("\n".join([revision, *self.ids, *self.texts]).encode()).hexdigest()
            path = Path(cache_dir) / f"tool_vectors_{key[:24]}.npy"
            if path.is_file():
                try:
                    cached = np.load(path)
                    if cached.shape[0] == len(self.ids):
                        return cached
                except (OSError, ValueError):
                    pass            # unreadable cache: recompute and overwrite
        vectors = self.embedder.embed(self.texts)
        if path is not None:
            path.parent.mkdir(parents=True, exist_ok=True)
            np.save(path, vectors)
        return vectors

    def semantic(self, query: str) -> list[str]:
        if self._vectors is None or not len(self.ids):
            return []
        scores = self._vectors @ self.embedder.embed([query], query=True)[0]
        order = sorted(range(len(self.ids)), key=lambda i: (-float(scores[i]), self.ids[i]))
        return [self.ids[i] for i in order]

    def components(self, query: str) -> dict[str, list[str]]:
        """Each first-stage ranking on its own: bm25, ngram and, with a model, bge."""
        out = {"bm25": self.index.lexical(query), "ngram": self.index.dense(query)}
        if self._vectors is not None:
            out["bge"] = self.semantic(query)
        return out

    def first_stage(self, query: str, *, k: int = 20,
                    parts: dict[str, list[str]] | None = None) -> list[str]:
        """Reciprocal-rank fusion of BM25, hashed n-gram and learned-dense."""
        fused: dict[str, float] = {}
        for ranking in (parts or self.components(query)).values():
            for rank, name in enumerate(ranking):
                fused[name] = fused.get(name, 0.0) + 1.0 / (k + rank + 1)
        return sorted(fused, key=lambda name: (-fused[name], name))

    def search(self, query: str) -> list[str]:
        ranked = self.first_stage(query)
        if self.reranker is None or not ranked:
            return ranked
        head, tail = ranked[: self.rerank_k], ranked[self.rerank_k:]
        text = dict(zip(self.ids, self.texts))
        scores = self.reranker.score(query, [text[name] for name in head])
        order = sorted(range(len(head)), key=lambda i: (-scores[i], i))
        return [head[i] for i in order] + tail


# ---------------------------------------------------------------------------
# L0 / L1 / L2 for things that are not tools
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Item:
    """A skill, a memory entry or a document."""

    id: str
    kind: str          # "skill" | "memory" | "document"
    summary: str       # L0: one line
    body: str          # L2: everything
    keywords: tuple[str, ...] = ()


class Disclosure:
    """
    L0 `catalogue()` - one line per item, always in the prompt.
    L1 `search()`     - id, kind, a snippet: enough to decide, not to use.
    L2 `open()`       - the body, only for an id the model asked for.
    """

    SNIPPET = 160

    def __init__(self, items: list[Item]):
        self.items = {item.id: item for item in items}
        self._index = Index(
            [item.id for item in items],
            [f"{item.id} {item.summary} {' '.join(item.keywords)}" for item in items])

    def catalogue(self, kind: str | None = None) -> str:
        return "\n".join(f"  {item.id}: {item.summary}" for item in sorted(
            self.items.values(), key=lambda i: i.id) if kind in (None, item.kind))

    def search(self, query: str, *, limit: int = 5, kind: str | None = None) -> list[dict]:
        found = [name for name in self._index.hybrid(query)
                 if kind in (None, self.items[name].kind)]
        return [{"id": name, "kind": self.items[name].kind,
                 "snippet": " ".join(self.items[name].body.split())[: self.SNIPPET]}
                for name in found[:limit]]

    def open(self, item_id: str) -> str | None:
        item = self.items.get(item_id)
        return item.body if item else None
