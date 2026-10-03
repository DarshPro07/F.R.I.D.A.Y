"""
ms-marco-MiniLM-L6-v2 as a fabric provider: the reranker for tool retrieval.

`provision` downloads the pinned revision and verifies each file's sha256;
`rerank` scores a query against a few candidate texts. Absent or altered files
report UNAVAILABLE and retrieval keeps the first-stage order.
"""

from __future__ import annotations

from friday import fabric, tool_models

PIN = tool_models.RERANKER


def start():
    return None


def stop(handle) -> None:
    tool_models.unload()


def health(handle) -> dict:
    try:
        tool_models.load(PIN.id)
    except tool_models.ModelUnavailable as exc:
        return {"state": fabric.UNAVAILABLE, "detail": str(exc)}
    return {"state": fabric.READY, "detail": f"{PIN.repo}@{PIN.revision[:12]}"}


def call(operation: str, handle, **arguments):
    if operation == "provision":
        return tool_models.provision(PIN.id)
    if operation == "rerank":
        query, documents = arguments.get("query"), arguments.get("documents")
        if not isinstance(query, str) or not isinstance(documents, list) \
                or not all(isinstance(d, str) for d in documents):
            raise fabric.FabricError("rerank needs `query` and `documents`: strings")
        return tool_models.load(PIN.id).score(query, documents)
    raise fabric.FabricError(f"{PIN.id} has no operation {operation!r}")


DESCRIPTOR = fabric.Provider(
    id=PIN.id,
    family="search",
    upstream=PIN.repo,
    operations=("provision", "rerank"),
    risk="low",
    license_mode=fabric.PERMISSIVE,
    integration_mode=fabric.ADAPTER,
    cost_class="free",
    model_required=False,
    commit=PIN.revision,
    version="L6-v2",
    notes=(
        "Apache-2.0. Local ONNX cross-encoder on CPU that reorders the top "
        "candidates of a tool search; weights pinned by Hub revision and file "
        "sha256, provisioned once, loaded offline."
    ),
)
