#!/usr/bin/env python3
"""
Regenerate FRIDAY's engineering harness for Claude Code and for Hermes (HARN-001).

Run:  .venv/Scripts/python.exe scripts/harness_sync.py --claude-out DIR --hermes-out DIR
      .venv/Scripts/python.exe scripts/harness_sync.py --claude-out DIR --hermes-out DIR --check

The source is the repo's `.claude/` (rules, agents, skills, hooks). Both outputs
come from it in one pass, so editing a rule once reaches both tools. The
directories are named by the caller; the live `.claude/`, `~/.claude` and the
Hermes install and home (profiles, config, auth) are refused unless `--allow-live` is given. See
friday/harness_sync.py for how each part lands in the Hermes profile.

Exit codes: 0 in sync / written, 1 drift found by --check, 2 refused or bad source.
"""
from __future__ import annotations

import argparse
import os
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent


def main(argv: list[str] | None = None) -> int:
    sys.path.insert(0, str(ROOT))
    from friday import harness_sync as H
    from friday import hermes_bridge

    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--source", default=str(ROOT), help="repo root holding .claude/ (default: this repo)")
    ap.add_argument("--claude-out", required=True, help="directory for the Claude Code layout")
    ap.add_argument("--hermes-out", required=True, help="directory for the Hermes profile layout")
    ap.add_argument("--mcp-url", default=H.DEFAULT_MCP_URL, help="FRIDAY MCP server URL for the Hermes profile")
    ap.add_argument("--mcp-transport", default="auto", choices=H.MCP_TRANSPORTS,
                    help="auto = sse when the URL ends in /sse (Hermes picks SSE only from transport: sse)")
    ap.add_argument("--hook-bash", default=None,
                    help=f"bash that runs the generated hooks (default: ${H.HOOK_BASH_ENV}, else Git for Windows bash / bash on PATH)")
    ap.add_argument("--check", action="store_true", help="report drift instead of writing")
    ap.add_argument("--allow-live", action="store_true", help="permit writing to a live location")
    args = ap.parse_args(argv)

    try:
        if args.check:
            drift = H.check(args.source, args.claude_out, args.hermes_out, mcp_url=args.mcp_url,
                             mcp_transport=args.mcp_transport, hook_bash=args.hook_bash)
            for item in drift:
                print(f"DRIFT {item}")
            print("in sync" if not drift else f"{len(drift)} file(s) out of sync")
            return 1 if drift else 0
        found = hermes_bridge.locate()
        profile_home = os.getenv(hermes_bridge.ENV_PROFILE_HOME, "").strip()
        result = H.render(args.source, args.claude_out, args.hermes_out, mcp_url=args.mcp_url,
                          mcp_transport=args.mcp_transport,
                          hermes_root=found["root"] if found else None,
                          protect=[profile_home] if profile_home else [],
                          allow_live=args.allow_live, hook_bash=args.hook_bash)
    except (H.RefusedTarget, H.HarnessSourceError) as exc:
        print(f"harness_sync: {exc}", file=sys.stderr)
        return 2
    print(f"wrote {len(result.written)}, removed {len(result.removed)}, "
          f"skipped {len(result.skipped)} (no Hermes twin)")
    for item in result.skipped:
        print(f"  skipped: {item}")
    for item in result.unavailable:
        print(f"  hook not emitted: {item}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
