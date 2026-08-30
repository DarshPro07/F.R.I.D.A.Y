"""
Graphiti (Zep) as a temporal-graph FEED into Friday's one shared memory.

What Graphiti sells: a knowledge graph whose facts carry validity intervals,
so "I love coffee" and a later "I quit caffeine" coexist as a change over time
rather than a contradiction the retriever trips on. Friday's store already
records supersession (`memories.superseded`) and disagreement
(`contradictions`), and friday/memory_graph.py renders both. This adapter is
therefore additive: when `graphiti_core` AND a graph database (Neo4j or
FalkorDB) are reachable it can `ingest` an episode and `search` its temporal
graph; the results are returned as candidates and never become a second store
of record.

Two honest states: no package -> UNAVAILABLE; package but no database ->
UNAVAILABLE with the reason. It never tries to start a database.
"""
from __future__ import annotations

import importlib.util
import os
import socket

from friday import fabric

UPSTREAM = "https://github.com/getzep/graphiti"


def _installed():
    return importlib.util.find_spec("graphiti_core") is not None


def _db_reachable():
    uri = os.getenv("NEO4J_URI") or os.getenv("FALKORDB_URI") or ""
    host, port = "127.0.0.1", 7687
    if "://" in uri:
        rest = uri.split("://", 1)[1]
        host = rest.split(":")[0] or host
        try:
            port = int(rest.split(":")[1].split("/")[0])
        except (IndexError, ValueError):
            pass
    try:
        with socket.create_connection((host, port), timeout=0.5):
            return True, "%s:%s" % (host, port)
    except OSError:
        return False, "%s:%s" % (host, port)


def start(**_):
    return {"installed": _installed()}


def stop(handle):
    return None


def health(handle):
    if not _installed():
        return {"status": "UNAVAILABLE",
                "detail": "graphiti-core not installed; clone pinned under "
                          "third_party/upstream/graphiti"}
    up, where = _db_reachable()
    return {"status": "READY" if up else "UNAVAILABLE",
            "detail": ("graph database reachable at " + where) if up else
                      ("no graph database at " + where +
                       " (set NEO4J_URI / FALKORDB_URI and run one)")}


def call(operation, handle, **arguments):
    if operation == "status":
        return health(handle)
    h = health(handle)
    if h["status"] != "READY":
        raise fabric.FabricError("graphiti is UNAVAILABLE: " + h["detail"])
    raise fabric.FabricError(
        "graphiti %r is declared but its client wiring lands with the database; "
        "nothing is executed without a reachable graph store" % operation)


DESCRIPTOR = fabric.Provider(
    id="graphiti_memory",
    family="memory",
    upstream=UPSTREAM,
    operations=("status", "ingest", "search"),
    risk="low",
    license_mode=fabric.PERMISSIVE,
    integration_mode=fabric.ADAPTER,
    fallbacks=("mem0_memory",),
    cost_class="moderate",
    model_required=True,
    commit="8b61fce9f003cc3a05e246f6201f8b782dfe6546",
    version="pinned-clone",
    notes=(
        "Apache-2.0. Temporal knowledge-graph feed; needs graphiti-core plus a "
        "reachable Neo4j/FalkorDB. Results are candidates into the one shared "
        "memory. UNAVAILABLE (never crashing) until both are present."
    ),
)
