"""
bge-small-en-v1.5 as a fabric provider: the dense half of tool retrieval.

`provision` downloads the pinned revision and verifies each file's sha256;
`embed` turns texts into unit vectors. Nothing is fetched at runtime - a
machine that has not provisioned the model reports UNAVAILABLE and tool
retrieval falls back to the model-free retrievers.

`build` makes the adapter for any embedder pin; `tool_embedder_base.py` is the
same adapter over the larger model measured against this one.
"""

from __future__ import annotations

from friday import fabric, tool_models

PIN = tool_models.EMBEDDER


def start():
    return None


def stop(handle) -> None:
    tool_models.unload()


def build(pin: tool_models.ModelPin, notes: str):
    """(health, call, DESCRIPTOR) for one embedder pin."""

    def health(handle) -> dict:
        try:
            tool_models.load(pin.id)
        except tool_models.ModelUnavailable as exc:
            return {"state": fabric.UNAVAILABLE, "detail": str(exc)}
        return {"state": fabric.READY, "detail": f"{pin.repo}@{pin.revision[:12]}"}

    def call(operation: str, handle, **arguments):
        if operation == "provision":
            return tool_models.provision(pin.id)
        if operation == "embed":
            texts = arguments.get("texts")
            if not isinstance(texts, list) or not all(isinstance(t, str) for t in texts):
                raise fabric.FabricError("embed needs `texts`: a list of strings")
            vectors = tool_models.load(pin.id).embed(texts, query=bool(arguments.get("query")))
            return vectors.tolist()
        raise fabric.FabricError(f"{pin.id} has no operation {operation!r}")

    descriptor = fabric.Provider(
        id=pin.id,
        family="search",
        upstream=pin.repo,
        operations=("provision", "embed"),
        risk="low",
        license_mode=fabric.PERMISSIVE,
        integration_mode=fabric.ADAPTER,
        cost_class="free",
        model_required=False,
        commit=pin.revision,
        version="v1.5",
        notes=notes,
    )
    return health, call, descriptor


health, call, DESCRIPTOR = build(PIN, (
    "MIT. Local ONNX embedding model on CPU for tool retrieval; weights "
    "pinned by Hub revision and file sha256, provisioned once, loaded "
    "offline. Absent or altered files report UNAVAILABLE."))
