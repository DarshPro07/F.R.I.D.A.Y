"""
Measure progressive tool disclosure and the staged router (PRD v7 §19.3, §82, CTX-002).

Run:  .venv-verify/Scripts/python.exe scripts/bench_tool_disclosure.py --out <file.json>
      ... --embedder tool_embedder_base --models-dir <dir>   one model, in its own process
      ... --thresholds                                         choose the direct-route thresholds

Two request sets over the full registered catalogue:

    intents       each long-tail capability's own first intent example. A
                  regression set: the router is tuned on these words, so a hit
                  here says nothing about language it was not tuned on.
    development   the long-tail rows of data/corpus/development.jsonl, text the
                  router's intent examples were not written from, with the labels
                  adjudicated by docs/plans/ctx-002/label_overlay.json (the
                  corpus files keep their original labels). The locked holdout
                  split is never read here.

For each request: its schema characters (the starting surface plus the one
search result the model receives) against every tool's full schema, and
whether that one search found the capability the request needed. The staged
router is measured as ranking quality (Success@k, MRR, latency, peak RAM) and as
a routing policy (how many requests it routes directly, how often that is right,
how many it would hand to a chooser, and whether the chooser's candidates cover
the needed tool). No chooser is called: a chooser is a paid model call and the
benchmark makes none, so chooser accuracy is not measured here.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

DEVELOPMENT = ROOT / "data" / "corpus" / "development.jsonl"
KS = (1, 3, 5, 10, 20)
TARGET_PRECISION = 0.95       # of requests routed directly, the share that must be right


class _Listed:
    """An MCP tool as FastMCP lists it, in the shape the router loads."""

    def __init__(self, tool):
        self.info = type("Info", (), {
            "name": tool.name,
            "raw_schema": {"name": tool.name, "description": tool.description or "",
                           "parameters": tool.inputSchema},
        })()


def _router():
    from mcp.server.fastmcp import FastMCP

    from friday import capability_router as R
    from friday.tools import register_all_tools

    server = FastMCP(name="bench")
    register_all_tools(server)
    router = R.Router()
    router.load([_Listed(tool) for tool in asyncio.run(server.list_tools())])
    return router


def _rows() -> list[dict]:
    from label_adjudication import load_overlay

    overlay = load_overlay()
    rows = []
    for line in DEVELOPMENT.read_text(encoding="utf-8").splitlines():
        row = json.loads(line)
        extra = overlay.get(row["id"], {})
        row["also_correct"] = sorted({*(row.get("also_correct") or []), *extra.get("also_correct", [])})
        row["clarify"] = bool(extra.get("clarify"))
        rows.append(row)
    return rows


def _development_cases(router) -> list[tuple[str, set[str]]]:
    active = set(router.active_names())
    return [(row["text"], {row["expect"], *row["also_correct"]}) for row in _rows()
            if not row.get("turns") and row["expect"] in router.all_tools
            and row["expect"] not in active]


def _abstain_texts() -> list[str]:
    return [row["text"] for row in _rows() if row["expect"] == "ABSTAIN" and not row.get("turns")]


def _summary(report: dict) -> dict:
    return {key: value for key, value in report.items() if key != "rows"}


def _peak_rss_mb() -> float:
    import psutil

    info = psutil.Process().memory_info()
    return round(getattr(info, "peak_wset", info.rss) / 2**20, 1)


def _staged(router, *, embedder, revision: str, cards: bool):
    from friday import capabilities, capability_router as R, tool_models, tool_router

    names = sorted(router.all_tools)

    def text(name):
        meta = capabilities.by_id(name)
        return tool_router.tool_text(
            name, router._schema_of(name).get("description", ""),
            meta.intent_examples if meta else (), R.GROUP_PURPOSE.get(R.group_of(name) or "", ""),
            cards=cards)

    return tool_router.StagedRouter(
        names, [text(n) for n in names],
        shipped=lambda q: [m["capability"] for m in router.search(q, limit=20)],
        embedder=embedder, cache_dir=tool_models.models_dir() / "vectors", revision=revision,
        descriptions={n: router._schema_of(n).get("description", "") for n in names})


def _measure(rank, cases) -> tuple[dict, list]:
    """Success@k, MRR and ms per request of `rank(text) -> (ranked names, agree, margin)`."""
    hits = {k: 0 for k in KS}
    reciprocal, rows = 0.0, []
    started = time.perf_counter()
    for text, ok in cases:
        ranked, agree, margin = rank(text)
        rows.append((ranked, agree, margin, ok))
    elapsed = time.perf_counter() - started
    for ranked, _, _, ok in rows:
        for k in KS:
            hits[k] += bool(set(ranked[:k]) & ok)
        position = next((i for i, name in enumerate(ranked) if name in ok), None)
        reciprocal += 1.0 / (position + 1) if position is not None else 0.0
    n = len(cases)
    return ({**{f"success_at_{k}": round(hits[k] / n, 4) for k in KS},
             "mrr": round(reciprocal / n, 4), "ms_per_request": round(1000 * elapsed / n, 2)}, rows)


def resolve_embedder(embedder_id: str):
    """The pinned embedder named on the command line, or None for 'none'.

    A named model that cannot be loaded is an error: the run would otherwise
    measure the model-free hybrid and label it as the model's result."""
    from friday import tool_models

    if embedder_id == "none":
        return None
    if embedder_id not in tool_models.PINS:
        raise SystemExit(f"unknown embedder {embedder_id!r}; pins: {sorted(tool_models.PINS)}")
    try:
        return tool_models.load(embedder_id)
    except tool_models.ModelUnavailable as exc:
        raise SystemExit(f"embedder {embedder_id} is not available ({exc}); "
                         "pass --models-dir or set FRIDAY_MODELS_DIR, or --embedder none")


def retrieval_benchmark(router, cases, embedder_id: str, embedder) -> tuple[dict, dict]:
    """Success@1/3/5/10/20, MRR, latency and peak RAM; shipped router, baseline hybrid
    (no cards) and hybrid with tool cards, over the same requests."""
    from friday import tool_models, tool_router

    out = {"embedder": embedder_id if embedder else "none (hash and bm25 only)"}
    revision = tool_models.PINS[embedder_id].revision if embedder else ""

    def shipped(text):
        names = [m["capability"] for m in router.search(text, limit=20)]
        return names, 0.0, 0.0

    out["router"], _ = _measure(shipped, cases)
    kept = {}
    for label, cards in (("hybrid", False), ("hybrid+cards", True)):
        staged = _staged(router, embedder=embedder, revision=revision, cards=cards)

        def rank(text, staged=staged):
            fused, agree = staged.rank(text)
            return [n for n, _ in fused], agree, tool_router.margin_of(fused)

        out[label], kept[label] = _measure(rank, cases)
    out["peak_ram_mb"] = _peak_rss_mb()
    return out, kept


def sweep_thresholds(rows, abstain_rows) -> dict:
    """Choose (agree, margin) on development: the largest direct-routed share whose
    answers are right at least TARGET_PRECISION of the time, counting a direct route
    on an ABSTAIN request as wrong. Also the stage-2 share and the chooser's candidate
    coverage; the oracle ceiling is the share a perfect chooser would get right."""
    grid = []
    for agree in (0.5, 0.6, 0.75, 0.8, 1.0):
        for margin in (0.0, 0.05, 0.1, 0.15, 0.2, 0.3, 0.4):
            direct = right = covered = 0
            for ranked, a, m, ok in rows:
                if a >= agree and m >= margin:
                    direct += 1
                    right += bool(ranked[:1] and ranked[0] in ok)
                else:
                    covered += bool(set(ranked[:10]) & ok)
            false_confident = sum(1 for a, m in abstain_rows if a >= agree and m >= margin)
            routed = direct + false_confident
            n = len(rows)
            precision = right / routed if routed else 1.0
            grid.append({
                "agree": agree, "margin": margin,
                "direct_share": round(direct / n, 4), "direct_precision": round(precision, 4),
                "stage2_share": round((n - direct) / n, 4),
                "stage2_coverage_at_10": round(covered / (n - direct), 4) if n > direct else 1.0,
                "abstain_routed_directly": round(false_confident / len(abstain_rows), 4),
                "oracle_ceiling": round((right + covered) / n, 4)})
    ok_rows = [g for g in grid if g["direct_precision"] >= TARGET_PRECISION]
    chosen = max(ok_rows, key=lambda g: (g["direct_share"], g["agree"], g["margin"])) if ok_rows else None
    # What a looser target would buy, so the choice of 95% is visible and not hidden.
    trade_offs = {}
    for target in (0.9, 0.8, 0.7, 0.6):
        cells = [g for g in grid if g["direct_precision"] >= target and g["direct_share"] > 0]
        trade_offs[str(target)] = max(
            cells, key=lambda g: (g["direct_share"], g["agree"], g["margin"])) if cells else None
    return {"target_precision": TARGET_PRECISION, "chosen": chosen, "trade_offs": trade_offs,
            "grid": grid}


def main(argv: list[str] | None = None) -> int:
    from friday import capability_router as R, tool_models, tool_router

    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--out", required=True)
    parser.add_argument("--models-dir", help="where the pinned model files are "
                        "(default FRIDAY_MODELS_DIR, else <cache>/models)")
    parser.add_argument("--embedder", default=tool_models.EMBEDDER.id,
                        help="a pin id from friday/tool_models.py, or 'none'")
    parser.add_argument("--thresholds", action="store_true",
                        help="sweep the direct-route thresholds on development")
    args = parser.parse_args(argv)
    if args.models_dir:
        os.environ["FRIDAY_MODELS_DIR"] = args.models_dir

    embedder = resolve_embedder(args.embedder)
    router = _router()
    result = {"intents": _summary(R.disclosure_report(router, R.intent_cases(router)))}
    if DEVELOPMENT.exists():
        cases = _development_cases(router)
        report = R.disclosure_report(router, [(text, sorted(ok)[0]) for text, ok in cases])
        found = sum(1 for (text, ok) in cases
                    if any(m["capability"] in ok for m in router.search(text, limit=6)))
        retrievers, kept = retrieval_benchmark(router, cases, args.embedder, embedder)
        result["development"] = {**_summary(report), "found": found,
                                 "recall_at_6": round(found / len(cases), 4) if cases else 0.0,
                                 "requests": len(cases), "retrievers": retrievers}
        if args.thresholds:
            staged = _staged(router, embedder=embedder, cards=True,
                             revision=tool_models.PINS[args.embedder].revision if embedder else "")
            abstain = []
            for text in _abstain_texts():
                fused, agree = staged.rank(text)
                abstain.append((agree, tool_router.margin_of(fused)))
            result["thresholds"] = sweep_thresholds(kept["hybrid+cards"], abstain)
            result["thresholds"]["abstain_requests"] = len(abstain)
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(result, indent=1), encoding="utf-8")
    print(json.dumps({k: v for k, v in result.items() if k != "thresholds"}, indent=1))
    if "thresholds" in result:
        print(json.dumps(result["thresholds"]["chosen"], indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
