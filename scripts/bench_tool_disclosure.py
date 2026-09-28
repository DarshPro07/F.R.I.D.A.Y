"""
Measure progressive tool disclosure against the all-tools baseline (PRD v7
§19.3, §82, CTX-002).

Run:  .venv-verify/Scripts/python.exe scripts/bench_tool_disclosure.py --out <file.json>

Two request sets over the full registered catalogue:

    intents       each long-tail capability's own first intent example. A
                  regression set: the router is tuned on these words, so a hit
                  here says nothing about language it was not tuned on.
    development   the long-tail rows of data/corpus/development.jsonl, text the
                  router's intent examples were not written from. The locked
                  holdout split is never read here.

For each request: its schema characters (the starting surface plus the one
search result the model receives) against every tool's full schema, and
whether that one search found the capability the request needed.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

DEVELOPMENT = ROOT / "data" / "corpus" / "development.jsonl"


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


def _development_cases(router) -> list[tuple[str, set[str]]]:
    active = set(router.active_names())
    cases = []
    for line in DEVELOPMENT.read_text(encoding="utf-8").splitlines():
        row = json.loads(line)
        if row.get("turns") or row["expect"] not in router.all_tools or row["expect"] in active:
            continue
        cases.append((row["text"], {row["expect"], *(row.get("also_correct") or [])}))
    return cases


def _summary(report: dict) -> dict:
    return {key: value for key, value in report.items() if key != "rows"}


def main(argv: list[str] | None = None) -> int:
    from friday import capability_router as R

    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--out", required=True)
    args = parser.parse_args(argv)

    router = _router()
    intents = R.disclosure_report(router, R.intent_cases(router))
    result = {"intents": _summary(intents)}
    if DEVELOPMENT.exists():
        cases = _development_cases(router)
        report = R.disclosure_report(router, [(text, sorted(ok)[0]) for text, ok in cases])
        found = sum(1 for (text, ok) in cases
                    if any(m["capability"] in ok for m in router.search(text, limit=6)))
        result["development"] = {**_summary(report), "found": found,
                                 "recall_at_6": round(found / len(cases), 4) if cases else 0.0}
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(result, indent=1), encoding="utf-8")
    print(json.dumps(result, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
