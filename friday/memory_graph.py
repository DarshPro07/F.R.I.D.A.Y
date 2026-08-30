"""
friday/memory_graph.py -- the shared memory as a graph, built from what is real.

The 3D view in the HUD renders THIS. Nothing here is decorative: every node is
a fact, an entity or a conflict that exists in the store, and every edge is a
relation the data actually carries.

Sources (all read-only, all already canonical):
  memories        subject paths ("user.goals.exam") become a hierarchy: the
                  namespace is a hub, each path segment a node, the leaf a fact.
                  Superseded rows are kept as version history on the node, so
                  a fact that changed over time shows its age and its churn
                  (the temporal-graph idea, without a second database).
  contradictions  two values that disagree about one subject. They are never
                  resolved silently in the store, so here they are first-class
                  nodes wired to the subject they dispute.
  GBrain ledger   facts Hermes/Friday wrote to the shared brain, attached to
                  the entity that owns them (friday, hermes, project-*).

Clustering keeps the graph renderable: paths deeper than MAX_DEPTH collapse
into their ancestor and count as its facts, so a namespace with 200 leaves
becomes a readable cluster rather than a hairball (chart guidance: >500 nodes
needs clustering before rendering; we cap well under it).
"""
from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path

MAX_DEPTH = 3           # user.goals.exam.chapter -> user.goals.exam (+1 fact)
MAX_NODES = 480         # hard ceiling for the 3D view
NODE_TYPES = ("hub", "topic", "fact", "brain", "entity", "conflict")


def _conn():
    from friday import ui_server as U
    return U._connect(), U._rows


def _ledger():
    try:
        from friday.brain import SharedBrainAdapter
        p = Path(SharedBrainAdapter()._ledger_path())
        if not p.exists():
            return []
        out = []
        for line in p.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line:
                try:
                    out.append(json.loads(line))
                except ValueError:
                    pass
        return out
    except Exception:  # noqa: BLE001
        return []


def build():
    conn, rows = _conn()
    try:
        facts = rows(conn, "SELECT id, subject, value, kind, scope, source, "
                           "confidence, created_at, superseded FROM memories "
                           "ORDER BY id")
        conflicts = rows(conn, "SELECT id, subject, existing_value, new_value, "
                               "resolution, created_at FROM contradictions "
                               "ORDER BY id DESC LIMIT 60")
    finally:
        if conn is not None:
            conn.close()

    nodes, links = {}, []
    seen_links = set()

    def link(a, b, kind):
        key = (a, b, kind)
        if a in nodes and b in nodes and key not in seen_links:
            seen_links.add(key)
            links.append({"source": a, "target": b, "kind": kind})

    def node(nid, **fields):
        n = nodes.get(nid)
        if n is None:
            n = nodes[nid] = {"id": nid, "facts": 0, "versions": 0, **fields}
        return n

    # ---- memories -> hierarchy -------------------------------------------
    history = defaultdict(int)
    latest = {}
    for f in facts:
        subj = (f.get("subject") or "").strip()
        if not subj:
            continue
        if f.get("superseded"):
            history[subj] += 1
            continue
        latest[subj] = f
    for subj, f in latest.items():
        parts = [p for p in subj.split(".") if p]
        ns = parts[0]
        hub = node("hub:" + ns, label=ns, type="hub", group=ns, depth=0)
        parent = hub["id"]
        path = ns
        depth = 0
        for seg in parts[1:MAX_DEPTH - 1]:
            depth += 1
            path = path + "." + seg
            nid = "topic:" + path
            node(nid, label=seg, type="topic", group=ns, depth=depth)
            link(parent, nid, "child")
            parent = nid
        # the leaf: collapse anything deeper into the ancestor as a fact
        leaf_label = ".".join(parts[MAX_DEPTH - 1:]) if len(parts) >= MAX_DEPTH else parts[-1]
        fid = "fact:" + subj
        fn = node(fid, label=leaf_label, type="fact", group=ns,
                  depth=min(len(parts), MAX_DEPTH),
                  value=(f.get("value") or "")[:240], kind=f.get("kind"),
                  scope=f.get("scope"), confidence=f.get("confidence"),
                  at=f.get("created_at"), source=(f.get("source") or "")[:120])
        fn["versions"] = history.get(subj, 0)
        link(parent, fid, "child")
        # ancestors accumulate fact counts (drives node size / cluster weight)
        for i in range(1, min(len(parts), MAX_DEPTH) + 1):
            anc = ("hub:" + parts[0]) if i == 1 else "topic:" + ".".join(parts[:i])
            if anc in nodes:
                nodes[anc]["facts"] += 1

    # ---- contradictions -> conflict nodes wired to the disputed subject ---
    for c in conflicts:
        subj = c.get("subject") or ""
        fid = "fact:" + subj
        if fid not in nodes:
            continue
        cid = "conflict:%s" % c.get("id")
        node(cid, label="conflict", type="conflict", group=subj.split(".")[0],
             existing=(c.get("existing_value") or "")[:160],
             proposed=(c.get("new_value") or "")[:160],
             resolution=c.get("resolution") or "pending",
             at=c.get("created_at"))
        link(fid, cid, "contradicts")
        nodes[fid]["contested"] = (c.get("resolution") or "pending") == "pending"

    # ---- GBrain ledger -> brain facts on their entity ---------------------
    for e in _ledger():
        ent = (e.get("entity") or "friday").strip() or "friday"
        eid = "entity:" + ent
        node(eid, label=ent, type="entity", group="brain", depth=0)
        bid = "brain:%s" % (e.get("fact_id") or abs(hash(e.get("fact") or "")) % 10**8)
        node(bid, label=(e.get("fact") or "")[:48], type="brain", group="brain",
             depth=1, value=(e.get("fact") or "")[:240],
             source=(e.get("provenance") or "")[:120], at=e.get("recorded_at"))
        link(eid, bid, "wrote")
        nodes[eid]["facts"] += 1

    # ---- ceiling: keep hubs/topics/entities/conflicts, trim oldest facts --
    if len(nodes) > MAX_NODES:
        keep = {k for k, v in nodes.items() if v["type"] != "fact"}
        leaves = sorted((v for v in nodes.values() if v["type"] == "fact"),
                        key=lambda v: v.get("at") or "", reverse=True)
        for v in leaves[:MAX_NODES - len(keep)]:
            keep.add(v["id"])
        nodes = {k: v for k, v in nodes.items() if k in keep}
        links = [l for l in links if l["source"] in nodes and l["target"] in nodes]

    by_type = defaultdict(int)
    for v in nodes.values():
        by_type[v["type"]] += 1
    return {
        "nodes": list(nodes.values()),
        "links": links,
        "stats": {
            "nodes": len(nodes), "links": len(links),
            "by_type": dict(by_type),
            "facts_active": len(latest),
            "facts_superseded": sum(history.values()),
            "conflicts_open": sum(1 for c in conflicts
                                  if (c.get("resolution") or "pending") == "pending"),
            "groups": sorted({v.get("group") for v in nodes.values() if v.get("group")}),
        },
    }


def adjacency(limit=400):
    """Keyboard/screen-reader fallback: the graph as a plain relationship list."""
    g = build()
    label = {n["id"]: n["label"] for n in g["nodes"]}
    return [{"from": label.get(l["source"], l["source"]),
             "kind": l["kind"],
             "to": label.get(l["target"], l["target"])}
            for l in g["links"][:limit]]
