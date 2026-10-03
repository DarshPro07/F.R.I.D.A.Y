"""
bge-base-en-v1.5 as a fabric provider: the larger embedder CTX-002 measured
against bge-small on FRIDAY's benchmark. It is pinned so the comparison can be
reproduced, not because it is the default (docs/plans/ctx-002/PLAN.md).
"""

from __future__ import annotations

from friday import tool_models
from friday.fabric_adapters.tool_embedder import build, start, stop  # noqa: F401

PIN = tool_models.EMBEDDER_BASE

health, call, DESCRIPTOR = build(PIN, (
    "MIT. Local ONNX embedding model on CPU, three times the size of "
    "tool_embedder; benchmarked against it and not promoted. Weights pinned by "
    "Hub revision and file sha256, loaded offline; absent or altered files "
    "report UNAVAILABLE."))
