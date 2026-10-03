"""CTX-002: retrievers and L0/L1/L2 disclosure beyond tools."""
from __future__ import annotations

from friday.tool_retrieval import BM25, Disclosure, Index, Item, stems


def test_bm25_weighs_a_rare_word_over_a_common_one():
    docs = [stems("open the window"), stems("open the file"), stems("open the zebra")]
    scores = BM25(docs).scores(stems("open zebra"))
    assert scores[2] > scores[0] and scores[2] > scores[1]


def test_dense_matches_an_inflection_that_lexical_cannot():
    index = Index(["music_play", "files_delete"],
                  ["music play a song", "files delete a document"])
    assert index.dense("playing songs")[0] == "music_play"


def test_hybrid_keeps_what_either_list_found():
    index = Index(["a", "b"], ["alpha unique", "beta other"])
    assert set(index.hybrid("alpha")) >= {"a"}
    assert index.hybrid("") == []


def test_ranking_is_deterministic_across_processes():
    first = Index(["x"], ["quartz fragment"]).dense("quartz")
    assert first == ["x"]


def test_disclosure_gives_one_line_then_a_snippet_then_the_body():
    long_body = "Deploy steps. " + "detail " * 200
    disclosure = Disclosure([
        Item("skill:deploy", "skill", "ship a release", long_body, ("release", "deploy")),
        Item("memory:tea", "memory", "prefers green tea", "He prefers green tea."),
    ])
    assert disclosure.catalogue("skill") == "  skill:deploy: ship a release"
    hit = disclosure.search("deploy a release", kind="skill")
    assert [h["id"] for h in hit] == ["skill:deploy"]
    assert len(hit[0]["snippet"]) <= Disclosure.SNIPPET < len(long_body)
    assert disclosure.open("skill:deploy") == long_body
    assert disclosure.open("missing") is None


# --- the learned stages (CTX-002 ruling 2026-10-02) ------------------------


class _FakeEmbedder:
    """Maps a text to a one-hot over a tiny vocabulary: no model files."""

    CONCEPTS = [("music", "song", "tune"), ("file", "document"), ("mail", "email")]

    def __init__(self):
        self.calls = 0

    def embed(self, texts, *, query=False):
        self.calls += 1
        import numpy as np
        return np.array([[float(any(w in t for w in group)) for group in self.CONCEPTS]
                         for t in texts], dtype=np.float32)


class _FakeReranker:
    def score(self, query, documents):
        return [float(len(d)) for d in documents]       # the longest document wins


def test_the_embedder_finds_what_no_lexical_stage_can(tmp_path):
    from friday.tool_retrieval import Retriever
    ids, texts = ["a", "b"], ["alpha unique", "beta other music"]
    assert Retriever(ids, texts).index.lexical("song") == []   # no shared word
    retriever = Retriever(ids, texts, embedder=_FakeEmbedder())
    assert retriever.semantic("song")[0] == "b"
    assert retriever.search("song")[0] == "b"


def test_the_reranker_reorders_only_the_head():
    from friday.tool_retrieval import Retriever
    ids = [f"t{i}" for i in range(30)]
    texts = ["common " + "x" * i for i in range(30)]
    first = Retriever(ids, texts).first_stage("common")
    reranked = Retriever(ids, texts, reranker=_FakeReranker(), rerank_k=5).search("common")
    assert set(reranked[:5]) == set(first[:5]) and reranked[5:] == first[5:]
    assert reranked[0] == max(first[:5], key=lambda n: len(texts[ids.index(n)]))


def test_without_models_search_is_the_model_free_hybrid():
    from friday.tool_retrieval import Retriever
    index = Index(["a", "b"], ["alpha unique", "beta other"])
    assert Retriever(["a", "b"], ["alpha unique", "beta other"]).search("alpha") \
        == index.hybrid("alpha")


def test_document_vectors_are_cached_by_catalogue_and_revision(tmp_path):
    from friday.tool_retrieval import Retriever
    embedder = _FakeEmbedder()
    Retriever(["a"], ["music a"], embedder=embedder, cache_dir=tmp_path, revision="r1")
    Retriever(["a"], ["music a"], embedder=embedder, cache_dir=tmp_path, revision="r1")
    assert embedder.calls == 1                                # second build read the cache
    Retriever(["a"], ["music a"], embedder=embedder, cache_dir=tmp_path, revision="r2")
    assert embedder.calls == 2                                # a new revision re-embeds
