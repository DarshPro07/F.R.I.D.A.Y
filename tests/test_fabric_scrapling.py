"""
Scrapling as a parser, and the boundary that keeps it one.

The interesting assertions here are negative. Scrapling can fetch pages and
drive a browser; Friday's copy must do neither, because a second fetcher is a
second egress path outside `netguard` and a second browser is the duplicate
NON_NEGOTIABLE 11 forbids. Most of this file exists to make that boundary fail
loudly if someone later adds `url=` convenience to the adapter.
"""

from __future__ import annotations

import pytest

from friday import fabric
from friday.fabric_adapters import scrapling_parse as sp

installed = pytest.mark.skipif(
    sp.health(None)["state"] == fabric.UNAVAILABLE,
    reason="scrapling not installed; uv sync --extra web")

CARDS = """
<div class="card"><h3 class="n">Widget</h3><span class="p">42</span></div>
<div class="card"><h3 class="n">Gadget</h3><span class="p">7</span></div>
<div class="card"><h3 class="n">Doohickey</h3><span class="p">13</span></div>
"""


@pytest.fixture(autouse=True)
def clean():
    fabric.reload()
    yield
    fabric.reload()


# --- descriptor ------------------------------------------------------------


def test_it_is_the_scraping_provider_and_is_pinned():
    import json
    import pathlib

    provider = fabric.get("scrapling_parse")
    assert provider.family == "scraping"
    assert provider.license_mode == fabric.PERMISSIVE
    lock = json.loads(
        (pathlib.Path(__file__).resolve().parent.parent
         / "third_party" / "UPSTREAM_LOCK.json").read_text(encoding="utf-8"))
    assert provider.commit == lock["scrapling"]["commit"]


def test_it_costs_nothing_and_needs_no_model():
    provider = fabric.get("scrapling_parse")
    assert provider.model_required is False
    assert provider.cost_class == "free"
    assert provider.secrets == ()


def test_it_holds_no_process():
    assert fabric.get("scrapling_parse").owns_process is False


# --- the boundary: it parses, it does not fetch ----------------------------


def test_no_operation_accepts_a_url_to_fetch():
    """
    `url` is metadata for relative-link resolution, never something to
    retrieve. If an operation ever starts fetching, egress leaves netguard.
    """
    with pytest.raises(fabric.FabricError, match="pass `html`"):
        sp.call("parse", None, url="https://example.com", selector="h3")


def test_every_operation_refuses_to_work_without_markup():
    for operation, extra in (("parse", {"selector": "h3"}),
                             ("fields", {"fields": {"a": "h3"}}),
                             ("similar", {"selector": "h3"}),
                             ("by_text", {"text": "x"})):
        with pytest.raises(fabric.FabricError):
            sp.call(operation, None, html="", **extra)


def test_scraplings_own_fetchers_are_not_installed():
    """
    Scrapling's `fetchers` extra pulls curl_cffi and its own browser stack.

    Deliberately *not* asserting that Playwright is absent: Playwright is
    Friday's own browser engine (friday/toolsets/web.py starts it for
    browser_automate), so an absence check there would fail the day someone
    correctly installs Friday's browser. curl_cffi is the honest marker,
    because nothing in Friday needs it and only scrapling[fetchers] brings it.
    """
    import importlib.util

    assert importlib.util.find_spec("curl_cffi") is None, (
        "scrapling[fetchers] appears to be installed - Friday fetches with "
        "its own capability so egress stays inside netguard")


def test_the_declared_extra_asks_only_for_the_core():
    """pyproject must not quietly grow scrapling[fetchers]."""
    import pathlib

    text = (pathlib.Path(__file__).resolve().parent.parent
            / "pyproject.toml").read_text(encoding="utf-8")
    assert "scrapling>=" in text
    assert "scrapling[fetchers]" not in text
    assert "scrapling[all]" not in text


# --- extraction ------------------------------------------------------------


@installed
def test_named_fields_come_back_as_a_dict_of_lists():
    """The shape a real extraction wants, and the one web_crawl cannot give."""
    out = sp.call("fields", None, html=CARDS,
                  fields={"name": "h3.n", "price": "span.p"})
    assert out == {"name": ["Widget", "Gadget", "Doohickey"],
                   "price": ["42", "7", "13"]}


@installed
def test_a_css_selector_returns_text_tag_and_attributes():
    found = sp.call("parse", None, html=CARDS, selector="h3.n")
    assert [e["text"] for e in found] == ["Widget", "Gadget", "Doohickey"]
    assert found[0]["tag"] == "h3"
    assert found[0]["attributes"]["class"] == "n"


@installed
def test_xpath_is_available_as_well_as_css():
    found = sp.call("parse", None, html=CARDS, kind="xpath",
                    selector='//span[@class="p"]')
    assert [e["text"] for e in found] == ["42", "7", "13"]


@installed
def test_an_unknown_selector_kind_is_refused_rather_than_guessed():
    with pytest.raises(fabric.FabricError, match="css.*xpath|xpath"):
        sp.call("parse", None, html=CARDS, kind="jquery", selector="h3")


@installed
def test_finding_by_text_returns_the_matching_element():
    found = sp.call("by_text", None, html=CARDS, text="Gadget")
    assert [e["text"] for e in found] == ["Gadget"]


@installed
def test_similar_elements_are_found_from_one_example():
    """The adaptive half: one card, then the ones shaped like it."""
    found = sp.call("similar", None, html=CARDS, selector="div.card")
    assert len(found) >= 1


@installed
def test_a_selector_that_matches_nothing_is_empty_not_an_error():
    assert sp.call("parse", None, html=CARDS, selector="h9.nope") == []
    assert sp.call("similar", None, html=CARDS, selector="h9.nope") == []


@installed
def test_results_are_capped_so_a_large_page_cannot_flood_the_context():
    many = "".join(f"<p class=x>{n}</p>" for n in range(sp.MAX_RESULTS + 50))
    assert len(sp.call("parse", None, html=many, selector="p.x")) == sp.MAX_RESULTS
    assert len(sp.call("parse", None, html=many, selector="p.x", limit=5)) == 5


# --- writes stay where Friday decides --------------------------------------


def test_the_relocation_database_lives_under_the_project_data_directory():
    """
    Upstream picks its own path for the fingerprint store. Friday pins it
    under config.DATA_DIR, which resolves from the project root rather than
    the caller's cwd - the defect friday/config.py documents at length.
    """
    from friday import config

    assert sp._storage_file().startswith(str(config.DATA_DIR))
    assert sp._storage_file().endswith(".db")


def test_relocation_is_off_unless_asked_for(monkeypatch):
    """A write should be requested, not defaulted into."""
    seen = {}

    class FakeSelector:
        def __init__(self, content=None, **kwargs):
            seen.update(kwargs)

        def css(self, *a, **k):
            return []

    monkeypatch.setitem(__import__("sys").modules, "scrapling",
                        type("m", (), {"Selector": FakeSelector,
                                       "__version__": sp.VERSION}))
    sp.call("parse", None, html="<p></p>", selector="p")
    assert "storage_args" not in seen
    assert seen.get("adaptive") in (None, False)


# --- absence ---------------------------------------------------------------


def test_a_missing_package_is_unavailable_not_an_import_error(monkeypatch):
    import builtins

    real = builtins.__import__

    def refuse(name, *args, **kwargs):
        if name == "scrapling":
            raise ImportError("no scrapling")
        return real(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", refuse)
    assert sp.health(None)["state"] == fabric.UNAVAILABLE
    with pytest.raises(FileNotFoundError, match="--extra web"):
        sp.start()


def test_an_unknown_operation_is_named():
    with pytest.raises(fabric.FabricError, match="no operation"):
        sp.call("exfiltrate", None, html="<p/>")
