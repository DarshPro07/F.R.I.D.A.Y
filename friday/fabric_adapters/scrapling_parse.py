"""
Scrapling: structured extraction from HTML, and selectors that survive a redesign.

Friday already turns a page into prose - `web_crawl` and `web_fetch` run
trafilatura and hand back the article. That answers "what does this page say".
It cannot answer "give me the price, the SKU and the rating from each card on
this page", and it certainly cannot keep answering it after the site ships a
new layout. That gap is this provider's entire reason to exist, and it is why
this is not a second copy of the research toolset.

## It parses. It does not fetch.

Scrapling ships fetchers - `requests`, `curl_cffi`, and a Playwright browser
behind the `fetchers` extra. None of them are installed and none are used.
Friday declares only the parsing core, whose whole dependency list is `lxml`,
`cssselect`, `orjson`, `tld` and `w3lib`, several of which trafilatura already
pulled in.

Two reasons, and the second is the one that matters:

**A second fetcher would be a second browser.** NON_NEGOTIABLE 11 forbids
duplicate browsers, and `browser-use` is the deliberate choice for that job.

**A fetcher here would route around Friday's own network policy.** Egress goes
through `netguard` and `sensitive_domains`; a provider that opened its own
sockets would be outside both, and nothing would report it. So `html` is a
required argument. The caller fetches with Friday's own capability and passes
the markup in. That is a slightly less convenient API in exchange for one
network policy instead of two.

## Adaptive relocation, and where it writes

`adaptive=True` is the feature that distinguishes this from any lxml wrapper:
Scrapling fingerprints the element a selector matched, so when the class name
changes next month it can find the same element again. The fingerprints live
in a SQLite file, and by default upstream chooses that path itself. Here it is
pinned under `config.DATA_DIR`, which resolves from the project root rather
than the working directory - the same rule `friday/config.py` documents at
length after an automation wrote its database into System32. `data/*.sqlite3`
is already ignored by git.

Relocation is opt-in per call and off by default: it is a write, and a write
should be asked for.
"""

from __future__ import annotations

from friday import fabric

#: Installed by the `web` extra: `uv sync --extra web`. Absent is a health
#: state, not an import error - the import happens inside the call.
PACKAGE = "scrapling"
VERSION = "0.4.15"

#: Where relocation fingerprints go. Under Friday's data directory, which
#: resolves from the project root rather than the caller's cwd.
STORAGE_NAME = "scrapling_selectors.db"

OPERATIONS = ("parse", "fields", "similar", "by_text")

#: A page can contain thousands of matching nodes. Returning all of them turns
#: a cheap deterministic extraction into a context problem.
MAX_RESULTS = 200


def _storage_file() -> str:
    from friday import config

    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    return str(config.DATA_DIR / STORAGE_NAME)


def _selector(html: str, *, url: str = "", adaptive: bool = False):
    from scrapling import Selector

    if not html:
        raise fabric.FabricError(
            "scrapling parses markup it is given; pass `html`. Fetch with "
            "Friday's own web_fetch so egress stays inside netguard.")
    if adaptive:
        return Selector(html, url=url, adaptive=True,
                        storage_args={"storage_file": _storage_file()})
    return Selector(html, url=url)


def _render(element) -> dict:
    """One element, flattened to what a caller can actually use."""
    try:
        attributes = dict(element.attrib)
    except Exception:                                        # noqa: BLE001
        attributes = {}
    return {"text": (element.text or "").strip(),
            "tag": getattr(element, "tag", ""),
            "attributes": attributes}


def _query(selector, expression: str, kind: str, adaptive: bool):
    if kind == "css":
        return selector.css(expression, adaptive=adaptive)
    if kind == "xpath":
        return selector.xpath(expression, adaptive=adaptive)
    raise fabric.FabricError(f"kind must be 'css' or 'xpath', not {kind!r}")


def start():
    """Prove the parser imports here, so a missing extra fails at activation."""
    try:
        import scrapling                                     # noqa: F401
    except ImportError as exc:
        raise FileNotFoundError(
            f"{PACKAGE} is not installed; `uv sync --extra web`") from exc
    return {"package": PACKAGE}


def stop(handle) -> None:
    """A parser holds nothing."""


def health(handle) -> dict:
    try:
        import scrapling
    except ImportError:
        return {"state": fabric.UNAVAILABLE,
                "detail": f"{PACKAGE} not installed; uv sync --extra web"}
    installed = getattr(scrapling, "__version__", "?")
    if installed != VERSION:
        # Still usable, but the audit was performed against one version and
        # the selector semantics are what changes between them.
        return {"state": fabric.DEGRADED,
                "detail": f"{PACKAGE} {installed} installed, {VERSION} audited"}
    return {"state": fabric.READY, "detail": f"{PACKAGE} {installed}, parse-only"}


def call(operation: str, handle, **arguments):
    html = arguments.get("html") or ""
    url = arguments.get("url") or ""
    adaptive = bool(arguments.get("adaptive"))
    limit = min(int(arguments.get("limit") or MAX_RESULTS), MAX_RESULTS)

    if operation == "parse":
        expression = (arguments.get("selector") or "").strip()
        if not expression:
            raise fabric.FabricError("parse needs a `selector`")
        found = _query(_selector(html, url=url, adaptive=adaptive),
                       expression, arguments.get("kind") or "css", adaptive)
        return [_render(e) for e in found[:limit]]

    if operation == "fields":
        # The shape an actual extraction wants: one page, several named
        # selectors, one dict back.
        fields = arguments.get("fields") or {}
        if not isinstance(fields, dict) or not fields:
            raise fabric.FabricError("fields needs {name: selector}")
        selector = _selector(html, url=url, adaptive=adaptive)
        kind = arguments.get("kind") or "css"
        out: dict[str, list[str]] = {}
        for name, expression in fields.items():
            found = _query(selector, str(expression), kind, adaptive)
            out[str(name)] = [(e.text or "").strip() for e in found[:limit]]
        return out

    if operation == "similar":
        expression = (arguments.get("selector") or "").strip()
        if not expression:
            raise fabric.FabricError("similar needs a `selector` to anchor on")
        found = _query(_selector(html, url=url), expression,
                       arguments.get("kind") or "css", False)
        if not found:
            return []
        return [_render(e) for e in found[0].find_similar()[:limit]]

    if operation == "by_text":
        text = (arguments.get("text") or "").strip()
        if not text:
            raise fabric.FabricError("by_text needs `text`")
        found = _selector(html, url=url).find_by_text(
            text, first_match=False, partial=bool(arguments.get("partial")))
        return [_render(e) for e in (found or [])[:limit]]

    raise fabric.FabricError(f"{PACKAGE} has no operation {operation!r}")


DESCRIPTOR = fabric.Provider(
    id="scrapling_parse",
    family="scraping",
    upstream="scrapling",
    operations=OPERATIONS,
    risk="low",
    license_mode=fabric.PERMISSIVE,
    integration_mode=fabric.ADAPTER,
    cost_class="free",
    model_required=False,
    commit="458e2a2ac909b3235747ebcdb312b93a1080a10a",
    version=VERSION,
    owns_process=False,
    notes=(
        "BSD-3-Clause. Parsing core only - the `fetchers` extra (requests, "
        "curl_cffi, Playwright) is deliberately not installed, so this opens "
        "no sockets and starts no browser. `html` is required and fetching "
        "stays with Friday's web_fetch, which keeps egress inside netguard. "
        "Adaptive relocation is opt-in per call; its fingerprint database is "
        "pinned under config.DATA_DIR."
    ),
)
