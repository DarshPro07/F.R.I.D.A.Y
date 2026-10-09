"""ULTRA-002: a stage result is cached by what produced it.

The key is the sha256 of the spec id, stage id, input refs, model and output
schema, so a changed input, model or schema is a miss and the same stage over
the same inputs is not dispatched twice. Results live in the store (never
`data/ada.sqlite3` directly) under the owning workspace: an agent's output can
quote private material, so a workspace never reads another's hit.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Callable

from friday import workspace


def result_key(*, spec_id: str, stage_id: str, input_refs, model: str, schema: dict) -> str:
    """Stable across processes: canonical JSON, sorted keys, ref order kept."""
    canonical = json.dumps(
        {"spec": spec_id, "stage": stage_id, "inputs": list(input_refs),
         "model": model, "schema": schema},
        sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class Fetched:
    result: dict
    hit: bool


class ResultCache:
    def __init__(self, store, workspace_id: str | None = None) -> None:
        self._store = store
        self._workspace = workspace_id or workspace.current()

    def get(self, key: str) -> dict | None:
        return self._store.ultracode_result_get(self._workspace, key)

    def put(self, key: str, result: dict) -> None:
        self._store.ultracode_result_put(self._workspace, key, result)

    def get_or_run(self, key: str, dispatch: Callable[[], dict]) -> Fetched:
        """A hit returns the stored result without calling `dispatch`."""
        cached = self.get(key)
        if cached is not None:
            return Fetched(cached, hit=True)
        result = dispatch()
        self.put(key, result)
        return Fetched(result, hit=False)
