"""
Browser / computer-control harness -- engine selection, one manager.

The owner wants "all of these, chosen per need": Friday's own headed browser
for most work, browser-use when a task needs ChatGPT-browser-class autonomy,
and OpenHands / agenticSeek only for heavy computer-use -- kept isolated
because they are second control layers (agenticSeek is GPL). Friday stays the
manager (non-negotiable #1): this module only PICKS an engine and reports what
is installed. It never becomes a rival orchestrator and it drives nothing yet.

Each engine reports its REAL state:
  ready       -- importable now, can be driven
  clone_only  -- pinned upstream present under third_party/, not installed
  unavailable -- neither
"""
from __future__ import annotations

import importlib.util
from dataclasses import dataclass
from pathlib import Path

_UPSTREAM = Path(__file__).resolve().parent.parent / "third_party" / "upstream"


@dataclass(frozen=True)
class Engine:
    id: str
    title: str
    role: str            # what it is FOR
    kind: str            # builtin | worker | sidecar
    module: str = ""     # python import probe ("" -> not a python import)
    clone: str = ""      # third_party/upstream/<clone> (pinned upstream)
    isolated: bool = False
    note: str = ""


ENGINES = (
    Engine(id="headed_playwright", title="Friday headed browser",
           role="default browser: clicks Friday plans, through its own policy "
                "layer (sensitive-domain block, netguard, persistent profile)",
           kind="builtin", module="playwright"),
    Engine(id="browser_use", title="browser-use",
           role="autonomous multi-step browsing (ChatGPT-browser-class)",
           kind="worker", module="browser_use", clone="browser-use"),
    Engine(id="openhands", title="OpenHands",
           role="full computer-use for heavy PC tasks",
           kind="sidecar", clone="openhands", isolated=True,
           note="second control layer -> isolated sidecar, never in-process"),
    Engine(id="agenticseek", title="agenticSeek",
           role="local computer-use, fully offline",
           kind="sidecar", clone="agenticseek", isolated=True,
           note="GPL-3.0 -> isolated sidecar only, never imported"),
)

_BY_ID = {e.id: e for e in ENGINES}


def _installed(module: str) -> bool:
    if not module:
        return False
    try:
        return importlib.util.find_spec(module) is not None
    except (ImportError, ValueError):
        return False


def _clone_present(clone: str) -> bool:
    return bool(clone) and (_UPSTREAM / clone).exists()


def status(engine: Engine) -> str:
    if engine.module and _installed(engine.module):
        return "ready"
    if _clone_present(engine.clone):
        return "clone_only"
    return "unavailable"


def availability() -> list:
    return [{"id": e.id, "title": e.title, "role": e.role, "kind": e.kind,
             "isolated": e.isolated, "status": status(e), "note": e.note}
            for e in ENGINES]


# Task hint -> preferred engine order. The manager picks per need; the first
# READY engine in the chain wins, else it names the intent and the honest
# fallback (Friday's own headed browser, once Playwright is installed).
_CHAINS = {
    "browse": ("headed_playwright", "browser_use"),
    "autonomous_browse": ("browser_use", "headed_playwright"),
    "computer_use": ("openhands", "agenticseek", "headed_playwright"),
    "scrape": ("headed_playwright", "browser_use"),
}


def select(task_hint: str = "browse") -> dict:
    chain = _CHAINS.get(task_hint, _CHAINS["browse"])
    for eid in chain:
        if status(_BY_ID[eid]) == "ready":
            return {"task_hint": task_hint, "chosen": eid, "reason": "ready",
                    "chain": list(chain)}
    head = chain[0]
    return {"task_hint": task_hint, "chosen": None, "intended": head,
            "intended_status": status(_BY_ID[head]),
            "fallback": "headed_playwright",
            "reason": "no engine installed yet; install to enable",
            "chain": list(chain)}
