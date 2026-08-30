"""
Mem0 as a FEED into Friday's one shared memory -- never a second brain.

What Mem0 sells: auto-extracting durable facts about the user from every
conversation into a persistent profile. Friday already does this natively in
friday/profile.py (Gemini extraction into `memories`/`observations`), so this
adapter is deliberately narrow: when the `mem0` package is installed it offers
`extract` (run Mem0's extractor over a turn and return CANDIDATE facts), and
those candidates go through the same admission + contradiction path every
other fact does. It stores nothing itself. Non-negotiable #11 (one memory)
holds because the store of record does not change.

State is honest: without the package the provider is UNAVAILABLE, not broken.
"""
from __future__ import annotations

import importlib.util

from friday import fabric

UPSTREAM = "https://github.com/mem0ai/mem0"


def _installed():
    return importlib.util.find_spec("mem0") is not None


def start(**_):
    return {"installed": _installed()}


def stop(handle):
    return None


def health(handle):
    ok = _installed()
    return {"status": "READY" if ok else "UNAVAILABLE",
            "detail": "mem0 package importable" if ok else
                      "pip install mem0ai (not in the live venv yet); "
                      "clone is pinned under third_party/upstream/mem0"}


def call(operation, handle, **arguments):
    if operation == "status":
        return health(handle)
    if not _installed():
        raise fabric.FabricError("mem0 is not installed; provider is UNAVAILABLE")
    if operation == "extract":
        text = (arguments.get("text") or "").strip()
        if not text:
            raise fabric.FabricError("extract needs text")
        # Mem0's extractor over a single turn, returning candidates only.
        from mem0 import Memory  # noqa: WPS433
        mem = Memory()
        out = mem.add(text, user_id=arguments.get("user_id", "owner"),
                      infer=True)
        results = out.get("results", out) if isinstance(out, dict) else out
        return {"candidates": [r.get("memory") for r in results if r.get("memory")],
                "note": "candidates only; admitted through friday.profile"}
    raise fabric.FabricError("unknown operation %r" % operation)


DESCRIPTOR = fabric.Provider(
    id="mem0_memory",
    family="memory",
    upstream=UPSTREAM,
    operations=("status", "extract"),
    risk="low",
    license_mode=fabric.PERMISSIVE,
    integration_mode=fabric.ADAPTER,
    fallbacks=(),
    cost_class="moderate",
    model_required=True,
    commit="19cb89aff472325c707f64b2f34ae6afdbf7faf7",
    version="pinned-clone",
    notes=(
        "Apache-2.0. Feed only: extracts candidate user facts from a turn and "
        "hands them to friday.profile for admission. Stores nothing itself, so "
        "the one-memory rule holds. UNAVAILABLE until `mem0ai` is installed."
    ),
)
