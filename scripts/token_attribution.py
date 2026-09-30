"""Attribute a Claude Code session's tokens to their sources.

Usage: python scripts/token_attribution.py TRANSCRIPT [--since ISO] [--until ISO] [--json OUT]

Usage totals are exact (from message.usage). Attribution sizes are in
characters, and tokens are APPROXIMATED as chars/4.

Claude Code writes one transcript line per content block, each repeating the
same message.usage; assistant entries are therefore de-duplicated by
message.id (the last usage seen wins) so a turn is counted once.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

USAGE_FIELDS = (
    "input_tokens",
    "cache_creation_input_tokens",
    "cache_read_input_tokens",
    "output_tokens",
)
KNOWN_TOOLS = ("Bash", "Read", "Grep", "Glob", "Edit", "Write", "Agent")
CHARS_PER_TOKEN = 4


def parse_ts(value):
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def load_entries(path, since=None, until=None):
    """Yield transcript entries, skipping bad lines and out-of-window ones."""
    with Path(path).open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(entry, dict):
                continue
            ts = parse_ts(entry.get("timestamp"))
            if ts is not None:
                if since and ts < since:
                    continue
                if until and ts > until:
                    continue
            yield entry


def result_text(content):
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(
            b.get("text", "") for b in content
            if isinstance(b, dict) and b.get("type") == "text"
        )
    return ""


def tool_group(name):
    return name if name in KNOWN_TOOLS else "other"


def hint_for(tool_input):
    if not isinstance(tool_input, dict):
        return ""
    for key in ("command", "file_path", "pattern", "path", "prompt", "description"):
        if tool_input.get(key):
            return " ".join(str(tool_input[key]).split())[:80]
    return ""


def aggregate(entries):
    usage_by_id = {}
    stamps = []
    tool_uses = {}  # id -> (name, hint)
    results = []  # (tool, chars, hint)
    assistant_text = 0
    assistant_tool_input = 0
    user_text = 0
    seen_blocks = set()
    anon = 0

    for e in entries:
        ts = parse_ts(e.get("timestamp"))
        if ts:
            stamps.append(ts)
        msg = e.get("message")
        if not isinstance(msg, dict):
            continue
        content = msg.get("content")
        etype = e.get("type")
        if etype == "assistant":
            usage = msg.get("usage")
            if isinstance(usage, dict):
                mid = msg.get("id")
                if not mid:
                    anon += 1
                    mid = f"anon-{anon}"
                usage_by_id[mid] = usage
            if isinstance(content, list):
                for b in content:
                    if not isinstance(b, dict):
                        continue
                    kind = b.get("type")
                    if kind == "tool_use":
                        bid = b.get("id")
                        if bid in seen_blocks:
                            continue
                        seen_blocks.add(bid)
                        tool_uses[bid] = (b.get("name", "?"), hint_for(b.get("input")))
                        assistant_tool_input += len(json.dumps(b.get("input", {})))
                    elif kind == "text":
                        assistant_text += len(b.get("text", ""))
        elif etype == "user":
            if isinstance(content, str):
                user_text += len(content)
            elif isinstance(content, list):
                for b in content:
                    if not isinstance(b, dict):
                        continue
                    if b.get("type") == "tool_result":
                        name, hint = tool_uses.get(b.get("tool_use_id"), ("?", ""))
                        results.append((name, len(result_text(b.get("content"))), hint))
                    elif b.get("type") == "text":
                        user_text += len(b.get("text", ""))

    totals = {f: 0 for f in USAGE_FIELDS}
    for usage in usage_by_id.values():
        for f in USAGE_FIELDS:
            totals[f] += int(usage.get(f) or 0)
    total_tokens = sum(totals.values())
    span = (max(stamps) - min(stamps)).total_seconds() if len(stamps) > 1 else 0.0
    per_hour = total_tokens / (span / 3600) if span > 0 else None

    by_tool = {}
    for name, chars, _ in results:
        g = by_tool.setdefault(tool_group(name), {"results": 0, "chars": 0})
        g["results"] += 1
        g["chars"] += chars
    top = sorted(results, key=lambda r: r[1], reverse=True)[:10]
    return {
        "turns": len(usage_by_id),
        "usage": totals,
        "total_tokens": total_tokens,
        "wall_seconds": span,
        "tokens_per_hour": per_hour,
        "attribution_chars": {
            "tool_results_by_tool": by_tool,
            "assistant_text": assistant_text,
            "assistant_tool_use_input": assistant_tool_input,
            "user_text": user_text,
        },
        "top_tool_results": [
            {"tool": t, "chars": c, "hint": h} for t, c, h in top
        ],
        "note": "attribution tokens are approximated as chars/4",
    }


def sources(report):
    """Flat (label, chars) list of every attributed source, largest first."""
    a = report["attribution_chars"]
    items = [(f"tool_result:{t}", v["chars"]) for t, v in a["tool_results_by_tool"].items()]
    items += [
        ("assistant_text", a["assistant_text"]),
        ("assistant_tool_use_input", a["assistant_tool_use_input"]),
        ("user_text", a["user_text"]),
    ]
    return sorted(items, key=lambda i: i[1], reverse=True)


def summarize(report):
    u = report["usage"]
    hours = report["wall_seconds"] / 3600
    tph = report["tokens_per_hour"]
    lines = [
        f"turns={report['turns']} span={hours:.2f}h "
        f"tokens/hour={'n/a' if tph is None else f'{tph:,.0f}'}",
        f"usage: input={u['input_tokens']:,} cache_create={u['cache_creation_input_tokens']:,} "
        f"cache_read={u['cache_read_input_tokens']:,} output={u['output_tokens']:,} "
        f"total={report['total_tokens']:,}",
        "context growth by source (chars; tokens ~ chars/4, APPROXIMATE):",
    ]
    all_items = sources(report)
    grand = sum(c for _, c in all_items) or 1
    for label, chars in all_items[:6]:
        lines.append(
            f"  {label:<26}{chars:>11,} chars ~{chars // CHARS_PER_TOKEN:>9,} tok {chars / grand:6.1%}"
        )
    lines.append("top tool results:")
    for r in report["top_tool_results"][:10]:
        lines.append(f"  {r['tool']:<6}{r['chars']:>9,}  {r['hint']}")
    return "\n".join(lines[:20])


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("transcript")
    p.add_argument("--since")
    p.add_argument("--until")
    p.add_argument("--json", dest="json_out")
    args = p.parse_args(argv)
    since, until = parse_ts(args.since), parse_ts(args.until)
    if (args.since and since is None) or (args.until and until is None):
        p.error("--since/--until must be ISO 8601")
    report = aggregate(load_entries(args.transcript, since, until))
    print(summarize(report))
    if args.json_out:
        Path(args.json_out).write_text(json.dumps(report, indent=2), encoding="utf-8", newline="\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
