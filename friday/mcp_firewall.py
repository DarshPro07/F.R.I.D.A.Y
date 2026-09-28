"""
The MCP firewall (PRD v7 MCP-001; build pack section 54).

An MCP server FRIDAY does not run itself is pinned the first time it is
seen: its identity (the URL FRIDAY reached it at) and, for every tool, a
hash of its schema and of its description, each description scanned for
text that tries to instruct the model. From then on the server stays ACTIVE
only while it offers exactly what was pinned. A tool changed, added or
removed, or a description rewritten, is a change nobody asked for - the
"rug pull" - and moves the server to SUSPENDED: its tools are withheld, and
it stays suspended even if it changes back, until the owner revalidates it.
Revalidation re-pins what the server then offers, and scans it again first.

FRIDAY's own tool server on this machine (a loopback URL) is first-party
code whose tools change with every update, so it is not pinned; a server
reached anywhere else - the cloud-to-PC path included - is.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import time
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

logger = logging.getLogger("friday.mcp_firewall")

ACTIVE = "ACTIVE"
SUSPENDED = "SUSPENDED"
#: The owner asked for the next connection to be pinned afresh.
REVALIDATING = "REVALIDATING"

_LOOPBACK = {"127.0.0.1", "localhost", "::1"}


@dataclass(frozen=True)
class Verdict:
    state: str
    tools: tuple = ()
    reason: str = ""


def _info(tool):
    return getattr(tool, "info", None)


def _name(tool) -> str:
    return str(getattr(_info(tool), "name", "") or "")


def _raw(tool) -> dict:
    raw = getattr(_info(tool), "raw_schema", None) or {}
    return raw if isinstance(raw, dict) else {}


def _sha(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode("utf-8")).hexdigest()


def fingerprint(tool) -> dict:
    """What is pinned for one tool: its schema and its description, hashed."""
    raw = _raw(tool)
    return {"schema": _sha(raw.get("parameters") or {}),
            "description": _sha(raw.get("description") or "")}


def _flagged(tools) -> list[str]:
    """Tools whose description tries to instruct the model, with why."""
    from friday.skills.lint import FAIL, scan_unsafe

    found = []
    for tool in tools:
        hits = [f.detail for f in scan_unsafe(str(_raw(tool).get("description") or ""),
                                                 file=_name(tool))
                if f.level == FAIL]
        if hits:
            found.append(f"{_name(tool)}: {'; '.join(hits)}")
    return found


def _changes(pinned: dict, current: dict) -> list[str]:
    changes = []
    for name in sorted(set(pinned) | set(current)):
        if name not in current:
            changes.append(f"{name}: removed")
        elif name not in pinned:
            changes.append(f"{name}: added")
        else:
            changes += [f"{name}: {part}" for part in ("schema", "description")
                        if pinned[name].get(part) != current[name].get(part)]
    return changes


class Firewall:
    """The pins, kept in one JSON file so they outlive the process."""

    def __init__(self, path) -> None:
        self.path = Path(path)

    def _load(self) -> dict:
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {}

    def _save(self, pins: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        staging = self.path.with_suffix(self.path.suffix + ".tmp")
        staging.write_text(json.dumps(pins, indent=1, sort_keys=True), encoding="utf-8")
        os.replace(staging, self.path)

    def status(self, server: str) -> dict:
        return dict(self._load().get(server) or {})

    def admit(self, server: str, tools) -> Verdict:
        """The tools FRIDAY may use from `server` now, and why not if none."""
        tools = list(tools)
        pins = self._load()
        entry = dict(pins.get(server) or {})
        current = {_name(tool): fingerprint(tool) for tool in tools}
        state = entry.get("state")
        if state in (None, REVALIDATING):
            flagged = _flagged(tools)
            if flagged:
                reason = "tool descriptions try to instruct the model: " + " | ".join(flagged)
                return self._suspend(pins, server, entry, reason)
            entry.update(state=ACTIVE, tools=current, pinned_at=time.time(), reason="")
            pins[server] = entry
            self._save(pins)
            logger.info("mcp.pinned server=%s tools=%d", server, len(current))
            return Verdict(ACTIVE, tuple(tools))
        if state == SUSPENDED:
            return Verdict(SUSPENDED, (), entry.get("reason") or "suspended")
        changes = _changes(entry.get("tools") or {}, current)
        if changes:
            return self._suspend(pins, server, entry,
                                 "its tools changed since they were pinned: " + "; ".join(changes))
        return Verdict(ACTIVE, tuple(tools))

    def _suspend(self, pins: dict, server: str, entry: dict, reason: str) -> Verdict:
        entry.update(state=SUSPENDED, reason=reason, suspended_at=time.time())
        pins[server] = entry
        self._save(pins)
        logger.warning("mcp.suspended server=%s reason=%s", server, reason)
        return Verdict(SUSPENDED, (), reason)

    def revalidate(self, server: str, *, by: str) -> None:
        """The owner's decision: pin what the server offers at its next
        connection, after scanning it again."""
        pins = self._load()
        entry = dict(pins.get(server) or {})
        entry.update(state=REVALIDATING, revalidated_by=by, revalidated_at=time.time())
        pins[server] = entry
        self._save(pins)
        logger.info("mcp.revalidate server=%s by=%s", server, by)


def is_first_party(url: str) -> bool:
    """FRIDAY's own tool server on this machine."""
    return (urlparse(url).hostname or "").lower() in _LOOPBACK


def default() -> Firewall:
    from friday.config import DATA_DIR

    return Firewall(Path(DATA_DIR) / "mcp_pins.json")


def admitted_tools(url: str, tools, *, firewall: Firewall | None = None) -> tuple[list, str]:
    """(the tools FRIDAY may use from the server at `url`, why none if suspended)."""
    tools = list(tools)
    if is_first_party(url):
        return tools, ""
    verdict = (firewall or default()).admit(url, tools)
    return list(verdict.tools), (verdict.reason if verdict.state == SUSPENDED else "")


def main(argv: list[str] | None = None) -> int:
    """`python -m friday.mcp_firewall status|revalidate <url> [--by NAME]`."""
    import argparse

    parser = argparse.ArgumentParser(prog="python -m friday.mcp_firewall")
    parser.add_argument("action", choices=("status", "revalidate"))
    parser.add_argument("url")
    parser.add_argument("--by", default="owner")
    args = parser.parse_args(argv)
    firewall = default()
    if args.action == "revalidate":
        firewall.revalidate(args.url, by=args.by)
    print(json.dumps(firewall.status(args.url), indent=1, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
