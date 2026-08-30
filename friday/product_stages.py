"""
The stages a catalogue actually goes through.

Separate from `products.py` on purpose: the engine there knows about graphs,
retries, quarantine and provenance, and knows nothing about SKUs. A second
kind of feed - suppliers, media, anything - is a new module here rather than
an edit to the engine.

Nothing in this file reaches the network. Image URLs are validated at row
level and fetched, if at all, through `friday.netguard`, which resolves again
at the moment it connects.
"""

from __future__ import annotations

import re

from friday import products as P



TITLE_FIELDS = ("title", "name", "product_name")
PRICE_FIELDS = ("price", "cost", "amount")
IMAGE_FIELDS = ("image", "image_url", "picture", "photo")
DESCRIPTION_FIELDS = ("description", "body", "details", "body_html")

_MONEY = re.compile(r"[^\d.\-]")


def _first(row: dict, names) -> str:
    for name in names:
        value = str(row.get(name, "") or "").strip()
        if value:
            return value
    return ""


def validate(record, context):
    row = record.source_row
    if not record.product_key:
        raise P.Quarantine("no sku, id or handle - nothing to identify it by")
    title = _first(row, TITLE_FIELDS)
    if not title:
        raise P.Quarantine("no title")

    raw_price = _first(row, PRICE_FIELDS)
    if raw_price:
        cleaned = _MONEY.sub("", raw_price)
        try:
            price = float(cleaned)
        except ValueError as exc:
            raise P.Quarantine(f"price {raw_price!r} is not a number") from exc
        if price < 0:
            raise P.Quarantine(f"price {price} is negative")
        record.set("price", price, source="source.price", method=P.DIRECT)

    record.set("sku", record.product_key, source="source.sku", method=P.DIRECT)
    record.set("title_raw", title, source="source.title", method=P.DIRECT)


def normalize(record, context):
    """Whitespace and casing only. Anything cleverer belongs to enrichment."""
    title = " ".join(record.value("title_raw", "").split())
    record.set("title", title, source="title_raw", method=P.DERIVED)
    description = _first(record.source_row, DESCRIPTION_FIELDS)
    if description:
        record.set("description", " ".join(description.split()),
                   source="source.description", method=P.DERIVED)


def images(record, context):
    """
    Validate the image URL. Refusal here is a *row* problem, not a batch one.

    Passing this is not permission to fetch: whatever downloads the image goes
    through netguard, which resolves again at connect time and revalidates
    every redirect.
    """
    from friday import netguard

    url = _first(record.source_row, IMAGE_FIELDS)
    if not url:
        raise ValueError("no image url on this row")
    try:
        verdict = netguard.check(url)
    except netguard.UrlRefused as exc:
        raise ValueError(str(exc)) from exc      # a bad row; retrying will not help
    if verdict["verdict"] == netguard.UNRESOLVED:
        # The host is syntactically fine and did not resolve *just now*. That
        # is a transient condition, and the distinction matters: this comes
        # back on the next attempt, while a metadata-endpoint URL never will.
        raise P.Retryable(f"{verdict['host']!r} did not resolve")
    record.set("image", verdict["url"], source="source.image", method=P.DIRECT)


def process_image(record, context):
    record.set("thumbnail", f"{record.value('image')}#thumb",
               source="image", method=P.DERIVED)


def enrich(record, context):
    """
    Attributes read out of the description.

    Deliberately a keyword pass rather than a model call: an honest cheap
    method beats an expensive one whose confidence is invented. When this does
    become a model call, `method` becomes llm_extract and the confidence comes
    from the model - the provenance record is already shaped for it.
    """
    text = f"{record.value('title', '')} {record.value('description', '')}".lower()
    materials = [word for word in ("cotton", "wool", "leather", "silk", "denim",
                                   "polyester", "linen") if word in text]
    if materials:
        record.set("material", materials[0], source="description",
                   method=P.DERIVED, confidence=0.6)


def classify(record, context):
    text = f"{record.value('title', '')} {record.value('description', '')}".lower()
    table = {"apparel": ("shirt", "dress", "jacket", "trouser", "sock"),
             "footwear": ("shoe", "boot", "sneaker", "sandal"),
             "accessory": ("bag", "belt", "hat", "scarf", "watch")}
    for category, words in table.items():
        if any(word in text for word in words):
            record.set("category", category, source=["title", "description"],
                       method=P.DERIVED, confidence=0.7)
            return
    record.set("category", "uncategorised", source=["title", "description"],
               method=P.DERIVED, confidence=0.2)


def generate(record, context):
    """
    The copy. This is the stage that fails when a model is unavailable, and
    its failure must not be confused with the catalogue failing.
    """
    title = record.value("title", "")
    category = record.value("category", "")
    if not title:
        raise ValueError("nothing to write about")
    record.set("seo_title", f"{title} | {category.title()}",
               source=["title", "category"], method=P.GENERATED)


def export(record, context):
    context.setdefault("exported", []).append(record.product_key)


def build() -> P.Pipeline:
    """
    The graph.

    `generate` needs only the text branch, so a missing or refused image costs
    the image branch and the export that needs it - and nothing else.
    """
    return P.Pipeline([
        P.Stage("validate", validate),
        P.Stage("normalize", normalize, needs=("validate",)),
        P.Stage("images", images, needs=("normalize",)),
        P.Stage("process_image", process_image, needs=("images",)),
        P.Stage("enrich", enrich, needs=("normalize",), retries=1),
        P.Stage("classify", classify, needs=("enrich",)),
        P.Stage("generate", generate, needs=("classify",), retries=1),
        P.Stage("export", export, needs=("generate", "process_image")),
    ])
