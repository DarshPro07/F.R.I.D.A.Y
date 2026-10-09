"""PROMPT-001: compile a model call's instructions from ranked layers.

Layers are ordered by rank (0 is highest, e.g. the worker contract). A line in
a lower layer that contradicts a kept higher line is dropped and reported, a
line already kept is dropped as a duplicate, and the result is scored for
health before the call: the share of the prompt-plus-tool-schema tokens that
is not tool schemas nothing uses. Below `min_health` those schemas are pruned.

A contradiction is the same instruction with opposite polarity ("never X" vs
"always X" / plain "X"). That is all it detects; semantic contradictions are
out of scope.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass

from friday.memory_stack import _approx_tokens, _tokens

_NEGATION = re.compile(r"\b(never|do not|don't|must not|should not|shall not)\b", re.I)
_MARKERS = {"never", "always", "not", "must", "should", "shall", "don"}
_SAME_INSTRUCTION = 0.8  # Jaccard overlap of the content words


@dataclass(frozen=True)
class Layer:
    name: str
    rank: int  # lower outranks higher
    text: str


def _core(line: str) -> frozenset[str]:
    return frozenset(t for t in _tokens(line) if t not in _MARKERS)


def _normal(line: str) -> str:
    return re.sub(r"\s+", " ", line.strip().lower()).rstrip(".!")


def _contradicts(a: str, b: str) -> bool:
    if bool(_NEGATION.search(a)) == bool(_NEGATION.search(b)):
        return False
    ca, cb = _core(a), _core(b)
    return bool(ca and cb) and len(ca & cb) / len(ca | cb) >= _SAME_INSTRUCTION


def _schema_tokens(schema: dict) -> int:
    return _approx_tokens(json.dumps(schema, sort_keys=True))


def _health(prompt_tokens: int, schemas: list[dict], unused: set[str]) -> float:
    total = prompt_tokens + sum(_schema_tokens(s) for s in schemas)
    wasted = sum(_schema_tokens(s) for s in schemas if s["name"] in unused)
    return (total - wasted) / total if total else 1.0


def compile_instructions(layers, tool_schemas=(), used_tools=(), min_health=0.5):
    """Return the prompt, the conflicts and duplicates found, and its health."""
    kept: list[tuple[str, str]] = []  # (layer name, line)
    conflicts: list[dict] = []
    duplicates = 0
    for layer in sorted(layers, key=lambda layer: layer.rank):
        for line in (ln.strip() for ln in layer.text.splitlines()):
            if not line:
                continue
            if any(_normal(line) == _normal(k) for _, k in kept):
                duplicates += 1
                continue
            clash = next(((n, k) for n, k in kept if _contradicts(line, k)), None)
            if clash:
                conflicts.append({"kept_layer": clash[0], "kept": clash[1],
                                  "dropped_layer": layer.name, "dropped": line})
                continue
            kept.append((layer.name, line))

    prompt = "\n".join(line for _, line in kept)
    schemas = list(tool_schemas)
    unused = {s["name"] for s in schemas} - set(used_tools)
    prompt_tokens = _approx_tokens(prompt)
    health = _health(prompt_tokens, schemas, unused)
    pruned: list[str] = []
    if health < min_health:
        pruned = [s["name"] for s in schemas if s["name"] in unused]
        schemas = [s for s in schemas if s["name"] not in unused]
        health = _health(prompt_tokens, schemas, set())
    return {"prompt": prompt, "conflicts": conflicts,
            "duplicates_removed": duplicates, "health": health,
            "unused_tools": sorted(unused), "pruned_tools": pruned,
            "tool_schemas": schemas}
