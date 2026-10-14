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
                  split is never read by a default run.

For each request: its schema characters (the starting surface plus the one
search result the model receives) against every tool's full schema, and
whether that one search found the capability the request needed. The staged
router is measured as ranking quality (Success@k, MRR, latency, peak RAM) and as
a routing policy (how many requests it routes directly, how often that is right,
how many it would hand to a chooser, and whether the chooser's candidates cover
the needed tool). No chooser is called unless --chooser cli is given: a chooser is a
paid model call, so that run needs --sample N (a hard cap on calls), reserves each
call against the build kernel's day spend lease and writes its cost to the day ledger.

The sealed holdout is opened only by --split holdout --chooser cli --sample N
--frozen <a development result> --i-am-promoting, with the chooser and thresholds the
development run froze; an existing --out is never overwritten.
"""
from __future__ import annotations

import argparse
import asyncio
import itertools
import json
import math
import os
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import build_kernel  # noqa: E402

DEVELOPMENT = ROOT / "data" / "corpus" / "development.jsonl"
CALIBRATION = ROOT / "data" / "corpus" / "calibration.jsonl"
HOLDOUT = ROOT / "data" / "corpus" / "holdout.jsonl"
REQ = "CTX-002"
CHOOSER_CAP_USD = 0.05        # one chooser call's budget: reserved before it, and the CLI's own cap
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


def _rows(path: Path = DEVELOPMENT) -> list[dict]:
    from label_adjudication import adjudicate, load_overlay

    # Calibration is not in the overlay: the same rule labels it here, in memory only.
    calibration = path == CALIBRATION
    overlay = {} if calibration else load_overlay()
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        row = json.loads(line)
        extra = (adjudicate(row) if calibration else overlay.get(row["id"])) or {}
        row["also_correct"] = sorted({*(row.get("also_correct") or []), *extra.get("also_correct", [])})
        row["clarify"] = bool(extra.get("clarify"))
        rows.append(row)
    return rows


def _case_rows(router, path: Path = DEVELOPMENT) -> list[dict]:
    active = set(router.active_names())
    return [row for row in _rows(path)
            if not row.get("turns") and row["expect"] in router.all_tools
            and row["expect"] not in active]


def _development_cases(router, path: Path = DEVELOPMENT) -> list[tuple[str, set[str]]]:
    return [(row["text"], {row["expect"], *row["also_correct"]}) for row in _case_rows(router, path)]


def _abstain_texts() -> list[str]:
    return [row["text"] for row in _rows() if row["expect"] == "ABSTAIN" and not row.get("turns")]


def _summary(report: dict) -> dict:
    return {key: value for key, value in report.items() if key != "rows"}


def _peak_rss_mb() -> float:
    import psutil

    info = psutil.Process().memory_info()
    return round(getattr(info, "peak_wset", info.rss) / 2**20, 1)


def _staged(router, *, embedder, revision: str, cards: bool, chooser=None, thresholds=None):
    from friday import tool_models, tool_router

    names, texts, descriptions = router.staged_inputs(cards=cards)
    extra = {"thresholds": thresholds} if thresholds is not None else {}
    return tool_router.StagedRouter(
        names, texts,
        shipped=lambda q: [m["capability"] for m in router.legacy_search(q, limit=20)],
        embedder=embedder, cache_dir=tool_models.models_dir() / "vectors", revision=revision,
        descriptions=descriptions, chooser=chooser, **extra)


class ChooserStopped(Exception):
    """The sample cap or the day's spend cap refused a chooser call."""


class LedgeredChooser:
    """A paid chooser held to a hard call cap and to the build kernel's day spend lease.

    Before each call it reserves `cap_usd` against the day budget; after it, the cost the
    call reported goes into the invocation ledger and the reservation is released. A call
    that reported no cost (a timeout) is recorded at the reserved cap: the spend may have
    happened. A refused call sets `stopped`, so the measurement ends instead of counting the
    router's fallback as the chooser's answer."""

    def __init__(self, inner, *, sample: int, cap_usd: float, runs_dir: Path | None = None,
                 req: str = REQ):
        self.inner, self.sample, self.cap_usd, self.req = inner, sample, cap_usd, req
        self.runs_dir = runs_dir or build_kernel.RUNS_DIR
        self.calls, self.spent_usd, self.stopped = 0, 0.0, None
        self._reported = None
        inner.on_cost = self._on_cost

    def _on_cost(self, usd: float) -> None:
        self._reported = usd

    def choose(self, query: str, cards: list[str]) -> dict:
        if self.calls >= self.sample:
            self.stopped = f"sample cap of {self.sample} chooser calls reached"
            raise ChooserStopped(self.stopped)
        attempt_id = f"bench-{uuid.uuid4()}"
        try:
            build_kernel.spend_guard(attempt_id, "bench", self.req, "CHOOSER", self.cap_usd,
                                     self.runs_dir)
        except build_kernel.SpendCapReached as exc:
            self.stopped = str(exc)
            raise ChooserStopped(self.stopped) from exc
        self.calls += 1
        self._reported = None
        try:
            return self.inner.choose(query, cards)
        finally:
            cost = self._reported if self._reported is not None else self.cap_usd
            self.spent_usd += cost
            build_kernel.ledger_append({
                "attempt_id": attempt_id, "req": self.req, "kind": "CHOOSER",
                "model": self.inner.model, "cost_usd": float(cost),
                "at": datetime.now(timezone.utc).isoformat(timespec="seconds")}, self.runs_dir)
            build_kernel.spend_release(attempt_id, self.runs_dir)


def chooser_benchmark(staged, cases, chooser: LedgeredChooser, *, sample: int) -> dict:
    """Success@1/3 of the stage-2 chooser over a deterministic sample of `cases`: the
    chosen tool first, then the other candidates, as the staged search hands them to the
    model. A request the chooser declined counts as a miss."""
    from friday import tool_router
    from friday.tool_router_rollout import query_id

    picked = sorted(cases, key=lambda case: query_id(case[0]))[:sample]
    hit1 = hit3 = answered = 0
    for text, ok in picked:
        route = staged.route(text)
        if chooser.stopped:
            break
        answered += 1
        names = list(route.candidates)
        if route.decision == tool_router.TOOL and route.tool_id:
            names = [route.tool_id, *(n for n in names if n != route.tool_id)]
        hit1 += bool(names[:1] and names[0] in ok and route.decision == tool_router.TOOL)
        hit3 += bool(set(names[:3]) & ok)
    return {"requests": answered, "success_at_1": round(hit1 / answered, 4) if answered else None,
            "success_at_3": round(hit3 / answered, 4) if answered else None,
            "chooser_calls": chooser.calls, "spent_usd": round(chooser.spent_usd, 4),
            "stopped": chooser.stopped}


def frozen_run(path: str | None) -> dict:
    """The development result a holdout run is measured against: the chooser and the
    thresholds fixed on dev. Without one the holdout is not opened."""
    if not path:
        raise SystemExit("the holdout is measured once, against a chooser and thresholds fixed on "
                         "development: pass --frozen <development result.json>")
    try:
        run = json.loads(Path(path).read_text(encoding="utf-8")).get("chooser_run") or {}
    except (OSError, ValueError) as exc:
        raise SystemExit(f"--frozen {path} is not a readable development result: {exc}")
    thresholds = run.get("thresholds") or {}
    if (run.get("split") != "development" or not run.get("chooser")
            or not isinstance(thresholds.get("agree"), (int, float))
            or not isinstance(thresholds.get("margin"), (int, float))):
        raise SystemExit(f"--frozen {path} has no development chooser_run naming the chooser and "
                         "its thresholds")
    return run


def _measure(rank, cases) -> tuple[dict, list]:
    """Success@k, MRR and ms per request of `rank(text) -> (ranked names, agree, margin)`."""
    rows = []
    started = time.perf_counter()
    for text, ok in cases:
        ranked, agree, margin = rank(text)
        rows.append((ranked, agree, margin, ok))
    elapsed = time.perf_counter() - started
    return ({**_measure_ranked([(ranked, ok) for ranked, _, _, ok in rows]),
             "ms_per_request": round(1000 * elapsed / len(cases), 2)}, rows)


def _measure_ranked(rows) -> dict:
    """Success@k and MRR of (ranked names, correct names) pairs."""
    hits = {k: 0 for k in KS}
    reciprocal = 0.0
    for ranked, ok in rows:
        for k in KS:
            hits[k] += bool(set(ranked[:k]) & ok)
        position = next((i for i, name in enumerate(ranked) if name in ok), None)
        reciprocal += 1.0 / (position + 1) if position is not None else 0.0
    n = len(rows)
    return {**{f"success_at_{k}": round(hits[k] / n, 4) for k in KS}, "mrr": round(reciprocal / n, 4)}


COMPONENTS = ("shipped", "bm25", "ngram", "bge")
BASELINE_CONFIG = {"kind": "today_two_level", "weights": None, "rrf_k": 10}   # tool_router.RRF_K


def sweep_candidates() -> list[dict]:
    """Every flat weighted fusion of COMPONENTS (weights in {0,1,2,3}, not all zero, no two
    that differ only by scale) at each RRF k, and today's two-level ranking."""
    weightings = sorted({tuple(v // math.gcd(*raw) for v in raw)
                         for raw in itertools.product(range(4), repeat=len(COMPONENTS)) if any(raw)})
    return [{"kind": "flat_weighted", "weights": list(w), "rrf_k": k}
            for w in weightings for k in (5, 10, 30, 60)] + [dict(BASELINE_CONFIG)]


def _rank_with_config(rankings: dict, config: dict) -> list[str]:
    from friday import tool_router

    if config["kind"] == "today_two_level":
        return rankings["hybrid+cards"]
    # shortcut: each ranker is cut to its top 50 to keep the sweep fast; deepen it if a miss bucket shows the answer sits lower.
    lists = [rankings.get(name, [])[:50] for name in COMPONENTS]
    return [name for name, _ in tool_router.fuse(lists, k=config["rrf_k"], weights=config["weights"])]


def fit_weighted_fuse(records) -> dict:
    """The one config chosen from calibration records alone, by S@1 + S@3. Ties go to fewer
    non-zero weights, then the lexicographically first (weights, k, kind)."""
    candidates = sweep_candidates()
    best = None
    for config in candidates:
        metrics = _measure_ranked([(_rank_with_config(r["rankings"], config), r["ok"]) for r in records])
        weights = config["weights"] or [1] * len(COMPONENTS)
        key = (-round(metrics["success_at_1"] + metrics["success_at_3"], 4),
               sum(1 for w in weights if w), weights, config["rrf_k"], config["kind"])
        if best is None or key < best[0]:
            best = (key, config, metrics)
    return {"fitted_on": "calibration", "candidates": len(candidates), "config": best[1],
            "metrics": best[2]}


def ablation_report(router, rows, embedder_id: str, embedder) -> tuple[dict, list[dict]]:
    """Lexical (shipped, bm25, ngram), semantic (bge) and hybrid recall rows over corpus `rows`,
    and per-request records of every ranking (no request text) for the sweep and taxonomy."""
    from friday import tool_models, tool_router

    revision = tool_models.PINS[embedder_id].revision if embedder else ""
    plain = _staged(router, embedder=embedder, revision=revision, cards=False)
    cards = _staged(router, embedder=embedder, revision=revision, cards=True)
    records = []
    for row in rows:
        text = row["text"]
        shipped = [m["capability"] for m in router.legacy_search(text, limit=20)]
        parts = cards.retriever.components(text)       # once per request; every row reuses it
        records.append({
            "ok": {row["expect"], *row["also_correct"]}, "expect": row["expect"],
            "category": row.get("category", "UNKNOWN"),
            "rankings": {
                "shipped": shipped, **parts,
                "hybrid": [n for n, _ in tool_router.fuse([shipped, plain.retriever.first_stage(text)])],
                "hybrid+cards": [n for n, _ in tool_router.fuse(
                    [shipped, cards.retriever.first_stage(text, parts=parts)])]}})
    recall = {"index": "bm25, ngram and bge rank the tool-card index, as hybrid+cards does"}
    for name in ("shipped", "bm25", "ngram", "bge", "hybrid", "hybrid+cards"):
        recall[name] = (_measure_ranked([(r["rankings"][name], r["ok"]) for r in records])
                        if name != "bge" or embedder is not None else {"unavailable": "no embedder"})
    return recall, records


def failure_taxonomy(records, config: dict) -> dict:
    """Where the right tool sat when it was not first, by miss bucket and corpus category, and
    the most frequent (expected, wrongly first) pairs. Tool ids and counts only."""
    buckets = dict.fromkeys(("rank_2", "rank_3", "rank_4_10", "rank_11_20", "absent"), 0)
    categories: dict[str, int] = {}
    pairs: dict[tuple[str, str], int] = {}
    for r in records:
        ranked = _rank_with_config(r["rankings"], config)[:20]
        position = next((i for i, name in enumerate(ranked) if name in r["ok"]), None)
        if position == 0:
            continue
        bucket = ("absent" if position is None else "rank_2" if position == 1 else
                  "rank_3" if position == 2 else "rank_4_10" if position < 10 else "rank_11_20")
        buckets[bucket] += 1
        categories[r["category"]] = categories.get(r["category"], 0) + 1
        pair = (r["expect"], ranked[0] if ranked else "<nothing ranked>")
        pairs[pair] = pairs.get(pair, 0) + 1
    return {"misses": sum(buckets.values()), "miss_buckets": buckets,
            "misses_by_category": dict(sorted(categories.items())),
            "top_confusions": [{"expected": e, "wrongly_first": w, "count": c} for (e, w), c in
                               sorted(pairs.items(), key=lambda item: (-item[1], item[0]))[:15]]}


def cost_block(router, cases) -> dict:
    """What the shipped search's payload costs at each result limit, kept apart from recall."""
    from friday import capability_router as R

    one_label = [(text, sorted(ok)[0]) for text, ok in cases]
    out = {"label": "payload shape of the shipped search"}
    for k in (1, 3, 5, 10):
        summary = _summary(R.disclosure_report(router, one_label, limit=k))
        out[f"k={k}"] = {key: summary[key] for key in (
            "starting_chars", "baseline_chars", "starting_reduction", "worst_reduction")}
    return out


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


def _reranked(staged, reranker, head: int):
    """Rank of `staged` with the cross-encoder reordering its first `head` names."""
    from friday import tool_router

    text = dict(zip(staged.retriever.ids, staged.retriever.texts))

    def rank(query):
        fused, agree = staged.rank(query)
        names = [n for n, _ in fused]
        top, tail = names[:head], names[head:]
        scores = reranker.score(query, [text[n] for n in top]) if top else []
        order = sorted(range(len(top)), key=lambda i: (-scores[i], i))
        return [top[i] for i in order] + tail, agree, tool_router.margin_of(fused)

    return rank


def retrieval_benchmark(router, cases, embedder_id: str, embedder, reranker=None,
                        rerank_sample: int = 0) -> tuple[dict, dict]:
    """Success@1/3/5/10/20, MRR, latency and peak RAM; shipped router, baseline hybrid
    (no cards), hybrid with tool cards and, given a reranker, hybrid with cards
    reordered by it, over the same requests (the first `rerank_sample` of them, if set,
    for the reranker row and its no-reranker twin)."""
    from friday import tool_models, tool_router

    out = {"embedder": embedder_id if embedder else "none (hash and bm25 only)"}
    revision = tool_models.PINS[embedder_id].revision if embedder else ""

    def shipped(text):
        names = [m["capability"] for m in router.legacy_search(text, limit=20)]
        return names, 0.0, 0.0

    out["router"], _ = _measure(shipped, cases)
    kept = {}
    for label, cards in (("hybrid", False), ("hybrid+cards", True)):
        staged = _staged(router, embedder=embedder, revision=revision, cards=cards)

        def rank(text, staged=staged):
            fused, agree = staged.rank(text)
            return [n for n, _ in fused], agree, tool_router.margin_of(fused)

        out[label], kept[label] = _measure(rank, cases)
        if reranker is not None and cards:
            # The cross-encoder reads 20 documents per request; a sample bounds that cost, and
            # the same requests are measured without it so the two rows stay comparable.
            subset = cases[:rerank_sample] if rerank_sample else cases
            if rerank_sample:
                out["hybrid+cards (reranker sample)"], _ = _measure(rank, subset)
            out["hybrid+cards+reranker"], _ = _measure(_reranked(staged, reranker, 20), subset)
            out["hybrid+cards+reranker"]["requests"] = len(subset)
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


def _chooser_run(args, router, embedder, swept: dict | None, frozen: dict | None) -> dict:
    """Measure the CLI chooser on `args.split`. On development the thresholds are the sweep's
    choice when this run swept, else the shipped defaults; on the holdout they are the frozen
    development run's, never recomputed."""
    from friday import tool_models, tool_router

    cell = frozen["thresholds"] if frozen else ((swept or {}).get("chosen") or {})
    thresholds = (tool_router.Thresholds(agree=cell["agree"], margin=cell["margin"]) if cell
                  else tool_router.Thresholds())
    cli = tool_router.CliChooser(model=args.chooser_model, max_budget_usd=CHOOSER_CAP_USD)
    chooser = LedgeredChooser(cli, sample=args.sample, cap_usd=CHOOSER_CAP_USD)
    staged = _staged(router, embedder=embedder, cards=True, chooser=chooser, thresholds=thresholds,
                     revision=tool_models.PINS[args.embedder].revision if embedder else "")
    cases = _development_cases(router, HOLDOUT if args.split == "holdout" else DEVELOPMENT)
    return {"split": args.split, "chooser": f"cli:{args.chooser_model}", "embedder": args.embedder,
            "thresholds": {"agree": thresholds.agree, "margin": thresholds.margin},
            "sample": args.sample, **chooser_benchmark(staged, cases, chooser, sample=args.sample)}


def _check_chooser_args(args) -> dict | None:
    """Refuse a paid or sealed run that is not set up to be bounded and honest, before any
    model loads. Returns the frozen development run for a holdout measurement."""
    if getattr(args, "ablate", False) and args.chooser == "cli":
        raise SystemExit("--ablate is offline: use --chooser none")
    if getattr(args, "ablate", False) and args.split == "holdout":
        raise SystemExit("--ablate is development-only; the holdout stays sealed")
    if args.chooser == "cli" and args.sample < 1:
        raise SystemExit("--chooser cli is a paid call per request: pass --sample N (N >= 1), "
                         "the hard cap on chooser calls")
    if args.split == "development":
        return None
    if args.chooser != "cli" or not args.i_am_promoting:
        raise SystemExit("the holdout is sealed: it is measured once, with --chooser cli --sample N "
                         "--frozen <development result.json> --i-am-promoting")
    frozen = frozen_run(args.frozen)
    if frozen["chooser"] != f"cli:{args.chooser_model}":
        raise SystemExit(f"the frozen development run used {frozen['chooser']}, not "
                         f"cli:{args.chooser_model}: the chooser is fixed before the holdout")
    if Path(args.out).exists():
        raise SystemExit(f"{args.out} exists: a holdout result is never overwritten")
    return frozen


def main(argv: list[str] | None = None) -> int:
    from friday import capability_router as R, tool_models, tool_router

    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--out", required=True)
    parser.add_argument("--chooser", choices=("none", "cli"), default="none",
                        help="cli measures stage 2 through `claude -p` (paid; needs --sample)")
    parser.add_argument("--chooser-model", default="haiku")
    parser.add_argument("--sample", type=int, default=0,
                        help="hard cap on chooser calls, and on requests measured with it")
    parser.add_argument("--split", choices=("development", "holdout"), default="development")
    parser.add_argument("--frozen", help="the development result whose chooser and thresholds "
                        "a holdout run is measured with")
    parser.add_argument("--i-am-promoting", action="store_true", help="required to open the holdout")
    parser.add_argument("--models-dir", help="where the pinned model files are "
                        "(default FRIDAY_MODELS_DIR, else <cache>/models)")
    parser.add_argument("--embedder", default=tool_models.EMBEDDER.id,
                        help="a pin id from friday/tool_models.py, or 'none'")
    parser.add_argument("--reranker", action="store_true",
                        help="also measure the pinned cross-encoder reordering the hybrid+cards head")
    parser.add_argument("--rerank-sample", type=int, default=0,
                        help="measure the reranker on the first N requests only (it reads 20 "
                        "documents per request), beside the same N without it")
    parser.add_argument("--thresholds", action="store_true",
                        help="sweep the direct-route thresholds on development")
    parser.add_argument("--ablate", action="store_true",
                        help="offline CTX-002 recall ablation on calibration and development")
    args = parser.parse_args(argv)
    frozen = _check_chooser_args(args)
    if args.ablate and Path(args.out).exists():
        raise SystemExit(f"{args.out} exists: an ablation result is never overwritten")
    if args.models_dir:
        os.environ["FRIDAY_MODELS_DIR"] = args.models_dir

    embedder = resolve_embedder(args.embedder)
    reranker = resolve_embedder(tool_models.RERANKER.id) if args.reranker else None
    router = _router()
    if args.split == "holdout":
        result = {"chooser_run": _chooser_run(args, router, embedder, None, frozen)}
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(json.dumps(result, indent=1), encoding="utf-8")
        print(json.dumps(result, indent=1))
        return 0
    if args.ablate:
        dev_recall, dev = ablation_report(router, _case_rows(router, DEVELOPMENT), args.embedder, embedder)
        cal_recall, cal = ablation_report(router, _case_rows(router, CALIBRATION), args.embedder, embedder)
        fitted = fit_weighted_fuse(cal)
        frozen_dev = _measure_ranked([(_rank_with_config(r["rankings"], fitted["config"]), r["ok"])
                                      for r in dev])
        cal_delta = round(fitted["metrics"]["success_at_1"] - cal_recall["hybrid+cards"]["success_at_1"], 4)
        dev_delta = round(frozen_dev["success_at_1"] - dev_recall["hybrid+cards"]["success_at_1"], 4)
        result = {
            "recall": {
                "splits": "fitted on calibration only; development reported once; holdout not opened",
                "pre_registered_rule": ("keep the avenue only if development S@1 >= the same-run "
                                        "hybrid+cards baseline + 0.03 and the calibration delta has "
                                        "the same sign; otherwise record no gain and close it"),
                "calibration": {"requests": len(cal), "rows": cal_recall, "fitted": fitted},
                "development": {"requests": len(dev), "rows": dev_recall, "frozen_config": frozen_dev},
                "success_at_1_delta": {"calibration": cal_delta, "development": dev_delta},
                "verdict": "keep" if dev_delta >= 0.03 and cal_delta > 0 else "no gain: avenue closed",
                "failure_taxonomy": {"baseline": failure_taxonomy(dev, BASELINE_CONFIG),
                                     "frozen_config": failure_taxonomy(dev, fitted["config"])},
            },
            "cost": cost_block(router, _development_cases(router, DEVELOPMENT)),
        }
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(json.dumps(result, indent=1), encoding="utf-8")
        print(json.dumps(result, indent=1))
        return 0
    result = {"intents": _summary(R.disclosure_report(router, R.intent_cases(router)))}
    if DEVELOPMENT.exists():
        cases = _development_cases(router)
        report = R.disclosure_report(router, [(text, sorted(ok)[0]) for text, ok in cases])
        found = sum(1 for (text, ok) in cases
                    if any(m["capability"] in ok for m in router.legacy_search(text, limit=6)))
        retrievers, kept = retrieval_benchmark(router, cases, args.embedder, embedder, reranker,
                                            args.rerank_sample)
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
        if args.chooser == "cli":
            result["chooser_run"] = _chooser_run(args, router, embedder, result.get("thresholds"), None)
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(result, indent=1), encoding="utf-8")
    print(json.dumps({k: v for k, v in result.items() if k != "thresholds"}, indent=1))
    if "thresholds" in result:
        print(json.dumps(result["thresholds"]["chosen"], indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
