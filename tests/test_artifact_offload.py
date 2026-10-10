"""
CTX-005: a large tool payload lands in an artifact and the next prompt gets a
reference to it, not the payload.

The acceptance is about the *subsequent prompt*, so the last test renders one
the way `cli_model_provider` does - a tool result folded into the transcript -
and looks for a sentinel that only ever existed in the raw payload.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from friday import config
from friday import contracts as c
from friday.cli_model_provider import render_prompt
from friday.fsjail import FileJail
from friday.policy import FULL, PolicyEngine
from friday.toolsets import artifacts as A
from friday.toolsets import files as F
from friday.toolsets import research as R

SENTINEL = "zzsentinelzz-ctx005-raw-payload"

#: The sentinel sits past the preview budget and well before the end, so
#: neither the preview nor a budget clip can be mistaken for the offload.
BODY = ("quantum widget calibration notes. " * 50
        + SENTINEL + " further calibration detail. " * 200)


def engine() -> PolicyEngine:
    return PolicyEngine(autonomy=FULL)


@pytest.fixture
def store(tmp_path, monkeypatch):
    """A disposable artifacts directory; the live data/ tree is never touched."""
    out = tmp_path / "artifacts"
    monkeypatch.setattr(config, "ARTIFACTS_DIR", out)
    return out


# ---------------------------------------------------------------------------
# the helper
# ---------------------------------------------------------------------------


def test_a_small_payload_stays_inline(store):
    off = A.offload("short enough to read in place", label="note")
    assert off.offloaded is False
    assert off.text == "short enough to read in place"
    assert off.fields("text") == {"text": "short enough to read in place",
                                  "digest": off.digest, "offloaded": False}
    assert not store.exists()


def test_a_large_payload_becomes_a_file(store):
    off = A.offload(BODY, label="https://example.com/page", suffix=".md")
    assert off.offloaded is True
    assert off.chars == len(BODY)
    assert SENTINEL not in off.text
    assert len(off.text) < len(BODY)
    written = Path(off.artifact_path)
    assert written.parent == store.resolve()
    assert written.read_text(encoding="utf-8") == BODY


def test_the_same_payload_is_written_once(store):
    first = A.offload(BODY, label="page", suffix=".md")
    second = A.offload(BODY, label="page", suffix=".md")
    assert first.artifact_path == second.artifact_path
    assert len(list(store.iterdir())) == 1


def test_an_unconfigured_store_keeps_the_payload(monkeypatch):
    monkeypatch.setattr(A, "artifacts_dir", lambda: None)
    off = A.offload(BODY, label="page")
    assert off.offloaded is False
    assert off.text == BODY
    assert "no artifacts directory" in off.inline_reason


def test_an_explicit_no_store_is_not_the_default_store(store):
    """`base=None` is a caller saying it has no store; it must not fall back to
    Friday's own directory, which is what leaving `base` out means."""
    off = A.offload(BODY, label="page", base=None)
    assert off.offloaded is False
    assert off.text == BODY
    assert "no artifacts directory" in off.inline_reason
    assert not store.exists()


# ---------------------------------------------------------------------------
# files_read
# ---------------------------------------------------------------------------


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    root = tmp_path / "work"
    root.mkdir()
    out = tmp_path / "work-artifacts"
    F.reset_jail(FileJail(roots=(root,)))
    monkeypatch.setattr(F, "ARTIFACTS_DIR", out)
    yield root
    F.reset_jail(None)


def read(path, **kw) -> c.ActionResult:
    return F.files_read(c.Run.create("read a file", capability="files"),
                        str(path), engine=engine(), **kw)


def test_files_read_keeps_a_small_file_inline(workspace):
    target = workspace / "note.txt"
    target.write_text("two lines\nof text\n", encoding="utf-8", newline="\n")
    result = read(target)
    assert result.status == c.SUCCEEDED
    assert result.output["text"] == "two lines\nof text\n"
    assert result.output["offloaded"] is False
    assert "artifact_path" not in result.output


def test_files_read_without_a_store_still_honours_the_inline_cap(workspace, monkeypatch):
    """No artifacts directory means the text stays inline - but never more of
    it than `max_chars`, which is the cap the caller asked for."""
    monkeypatch.setattr(F, "ARTIFACTS_DIR", None)
    target = workspace / "big.md"
    target.write_text(BODY, encoding="utf-8")
    result = read(target, max_chars=500)
    assert result.status == c.SUCCEEDED
    assert result.output["offloaded"] is False
    assert len(result.output["text"]) == 500
    assert result.output["truncated"] is True
    assert result.output["chars"] == len(BODY)
    assert "no artifacts directory" in result.output["inline_reason"]


def test_files_read_offloads_a_file_over_the_inline_cap(workspace):
    target = workspace / "big.md"
    target.write_text(BODY, encoding="utf-8")
    result = read(target, max_chars=500)
    assert result.status == c.SUCCEEDED
    assert result.output["offloaded"] is True
    assert result.output["chars"] == len(BODY)
    assert SENTINEL not in json.dumps(result.to_dict(), default=str)
    assert Path(result.output["artifact_path"]).read_text(encoding="utf-8") == BODY


# ---------------------------------------------------------------------------
# research
# ---------------------------------------------------------------------------


QUESTION = "quantum widget calibration"


def _page(url: str = "https://example.com/a") -> dict:
    return {"ok": True, "url": url, "title": "Calibration", "markdown": BODY,
            "chars": len(BODY), "truncated": False, "engine": "test"}


def _search_result(run: c.Run) -> c.ActionResult:
    return c.succeeded(
        c.started(run.run_id, "web.search"),
        output={"provider": "test",
                "results": [{"url": "https://example.com/a",
                             "title": "Calibration"}]},
        verification=c.Verification(method="stub_search", evidence="1 result"))


@pytest.fixture
def no_network(monkeypatch):
    async def fake_search(run, question, **kw):
        return _search_result(run)

    async def fake_crawl(urls, **kw):
        return [_page(u) for u in urls]

    monkeypatch.setattr(R, "web_search", fake_search)
    monkeypatch.setattr(R, "crawl_all", fake_crawl)


def deep() -> c.ActionResult:
    return asyncio.run(R.web_deep_research(
        c.Run.create(QUESTION, capability="research"), QUESTION, engine=engine()))


def test_deep_research_offloads_every_source_on_the_fresh_path(
        store, no_network, monkeypatch):
    from friday import research_cache

    monkeypatch.setattr(research_cache, "lookup", lambda *a, **k: None)
    saved = []
    monkeypatch.setattr(research_cache, "save", lambda artifact: (saved.append(artifact), artifact)[1])

    result = deep()
    assert result.status in (c.SUCCEEDED, c.PARTIAL)
    source = result.output["sources"][0]
    assert source["offloaded"] is True
    assert source["chars"] == len(BODY)
    assert SENTINEL not in json.dumps(result.to_dict(), default=str)
    # The corpus is still measured, and the cache still holds the whole text.
    assert result.output["corpus_chars"] == len(BODY)
    assert SENTINEL in saved[0].facts[0]


def test_deep_research_offloads_every_source_on_the_cache_hit_path(
        store, monkeypatch):
    from friday import research_cache

    artifact = research_cache.Artifact.create(
        QUESTION, sources=[{"url": "https://example.com/a", "title": "Calibration"}],
        facts=[BODY], confidence=1.0)
    monkeypatch.setattr(research_cache, "lookup", lambda *a, **k: artifact)

    result = deep()
    assert result.status == c.SUCCEEDED
    assert result.output["cached"] is True
    assert result.output["sources"][0]["offloaded"] is True
    assert SENTINEL not in json.dumps(result.to_dict(), default=str)


def test_web_crawl_offloads_every_page(store, no_network):
    result = asyncio.run(R.web_crawl(
        c.Run.create("read a page", capability="research"),
        "https://example.com/a", engine=engine()))
    assert result.status == c.SUCCEEDED
    page = result.output["pages"][0]
    assert page["offloaded"] is True
    # The page's own measured size survives; only the body moved.
    assert page["chars"] == len(BODY)
    assert SENTINEL not in json.dumps(result.to_dict(), default=str)


# ---------------------------------------------------------------------------
# a consumer that needs the whole page still gets it
# ---------------------------------------------------------------------------


def test_recall_returns_the_head_of_the_offloaded_text(store):
    item = {"url": "https://example.com/a",
            **A.offload(BODY, label="page", suffix=".md").fields("markdown")}
    assert SENTINEL not in item["markdown"]
    assert A.recall(item, "markdown", limit=len(BODY)) == BODY
    assert A.recall(item, "markdown", limit=100) == BODY[:100]


def test_recall_leaves_an_inline_item_alone(store):
    item = {"markdown": "short page"}
    assert A.recall(item, "markdown", limit=100) == "short page"


def test_recall_will_not_read_outside_the_artifact_store(store, tmp_path):
    stray = tmp_path / "elsewhere.md"
    stray.write_text("not an artifact", encoding="utf-8")
    item = {"markdown": "preview", "offloaded": True, "artifact_path": str(stray)}
    assert A.recall(item, "markdown", limit=100) == "preview"


def test_a_claim_check_reads_the_page_not_its_preview(store):
    """`product._readable` hands sources to the model that is checking a claim;
    offloading must not shrink that reading to the preview."""
    from friday import product

    output = {"sources": [{
        "url": "https://example.com/a", "title": "Calibration",
        **A.offload(BODY, label="page", suffix=".md").fields("markdown")}]}
    # The sentinel sits past the preview but inside the per-source reading.
    assert A.preview_budget() < BODY.index(SENTINEL) < product._PER_SOURCE
    assert SENTINEL in product._readable(output)


# ---------------------------------------------------------------------------
# the acceptance: the subsequent prompt
# ---------------------------------------------------------------------------


def test_the_next_prompt_carries_the_reference_not_the_payload(
        store, no_network, monkeypatch):
    from friday import research_cache

    monkeypatch.setattr(research_cache, "lookup", lambda *a, **k: None)
    monkeypatch.setattr(research_cache, "save", lambda artifact: artifact)

    result = deep()
    tool_message = json.dumps(result.to_dict(), default=str)
    prompt = render_prompt([
        {"role": "system", "content": "You are Friday."},
        {"role": "user", "content": QUESTION},
        {"role": "assistant", "content": "Researching."},
        {"role": "user", "content": tool_message},
    ])
    assert SENTINEL not in prompt
    # The reference survives the round trip (the path is JSON-escaped inside
    # the tool message, so the file name is what is comparable here).
    assert Path(result.output["sources"][0]["artifact_path"]).name in prompt
