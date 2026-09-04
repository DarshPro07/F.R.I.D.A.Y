"""
friday/org.py -- the one-person company: You -> Friday -> Hermes -> teams.

Nothing in the org is typed by hand. Teams are the divisions of the pinned
agency-agents upstream (divisions.json + each agent's front matter: name,
description, colour), so the roster, its colours and its size come from data
and change when the upstream does. Live status comes from the same probes the
rest of Friday trusts.

Escalation is a chain with three honest tiers:

    friday   can it be done with a tool Friday has?   -> do it
    hermes   otherwise, is Hermes up to execute?       -> delegate
    you      otherwise                                 -> "Sir, should I build this?"

`route()` DECIDES the tier and says why; it never executes. Dispatch stays
with friday.control (gated) and the objective engine, so this module can
never become a second orchestrator.

`assemble(goal)` proposes a team for a goal by scoring every agent in every
division against the goal text. It is a proposal the owner sees, not a
spawn: dynamic sub-agents are created by the objective engine when the
proposal is approved.
"""
from __future__ import annotations

import json
import re
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
UPSTREAM = ROOT / "third_party" / "upstream" / "agency-agents"
#: VoltAgent's 158 subagent briefs, ten category folders standing in for
#: divisions.json (this upstream ships no metadata file of its own).
VOLT_UPSTREAM = (ROOT / "third_party" / "upstream" /
                 "awesome-claude-code-subagents" / "categories")
_VOLT_DEFAULT_COLOR = "#888"

_CACHE = {"at": 0.0, "divisions": None}
_LOCK = threading.Lock()
_FM = re.compile(r"^---\s*\n(.*?)\n---", re.S)


def _frontmatter(text):
    m = _FM.match(text)
    out = {}
    if not m:
        return out
    for line in m.group(1).splitlines():
        if ":" in line:
            k, v = line.split(":", 1)
            out[k.strip()] = v.strip().strip('"').strip("'")
    return out


def _load_agency_divisions():
    """Every agency-agents division with its agents. [] if it is absent."""
    meta_path = UPSTREAM / "divisions.json"
    if not meta_path.exists():
        return []
    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8")).get("divisions") or {}
    except ValueError:
        meta = {}
    out = []
    for key, m in meta.items():
        d = UPSTREAM / key
        agents = []
        if d.is_dir():
            for f in sorted(d.glob("*.md")):
                if f.name.upper().startswith("README"):
                    continue
                try:
                    fm = _frontmatter(f.read_text(encoding="utf-8", errors="replace"))
                except OSError:
                    continue
                if not fm.get("name"):
                    continue
                agents.append({"id": f.stem, "name": fm["name"],
                               "description": (fm.get("description") or "")[:160],
                               "color": fm.get("color") or m.get("color") or ""})
        out.append({"id": key, "label": m.get("label") or key.title(),
                    "color": m.get("color") or "#888", "icon": m.get("icon") or "",
                    "agents": agents, "size": len(agents)})
    out.sort(key=lambda x: -x["size"])
    return out


def _load_voltagent_divisions():
    """VoltAgent's category folders as divisions. [] if the clone is absent.

    No divisions.json here -- the category directory name is the id, the
    same name with its numeric prefix stripped and title-cased is the label
    (ponytail: one acronym reads oddly title-cased, e.g. "Data Ai" for
    05-data-ai; not worth a lookup table for one folder), and every agent
    comes from frontmatter alone (name, description -- no per-agent colour
    in this upstream, so the division default applies to all of them).
    """
    if not VOLT_UPSTREAM.is_dir():
        return []
    out = []
    for d in sorted(VOLT_UPSTREAM.iterdir()):
        if not d.is_dir():
            continue
        agents = []
        for f in sorted(d.glob("*.md")):
            if f.name.upper().startswith("README"):
                continue
            try:
                fm = _frontmatter(f.read_text(encoding="utf-8", errors="replace"))
            except OSError:
                continue
            if not fm.get("name"):
                continue
            agents.append({"id": f.stem, "name": fm["name"],
                           "description": (fm.get("description") or "")[:160],
                           "color": _VOLT_DEFAULT_COLOR})
        label = re.sub(r"^\d+-", "", d.name).replace("-", " ").title()
        out.append({"id": d.name, "label": label, "color": _VOLT_DEFAULT_COLOR,
                    "icon": "", "agents": agents, "size": len(agents)})
    out.sort(key=lambda x: -x["size"])
    return out


def _load_divisions():
    """Every division with its agents: agency-agents first, then VoltAgent
    categories. Each upstream is independent -- one being absent never
    blocks the other (a provider being unavailable is not a crash)."""
    return _load_agency_divisions() + _load_voltagent_divisions()


def divisions():
    import time
    with _LOCK:
        if _CACHE["divisions"] is None or time.time() - _CACHE["at"] > 300:
            _CACHE["divisions"] = _load_divisions()
            _CACHE["at"] = time.time()
        return _CACHE["divisions"]


# --------------------------------------------------------------------------
# live tiers
# --------------------------------------------------------------------------

def _friday_tier():
    try:
        from friday import control
        up = control.reachable()
        return {"tier": "friday", "label": "Friday", "status": "online" if up else "offline",
                "detail": "agent + %s tools" % ("164" if up else "0")}
    except Exception:  # noqa: BLE001
        return {"tier": "friday", "label": "Friday", "status": "offline", "detail": ""}


def _hermes_tier():
    try:
        from friday import ui_server as U
        h = U._connections().get("hermes", {})
        st = h.get("status") or "unavailable"
        # "checking" means nobody has probed yet in this process, which is not
        # the same as a failure -- treating it as one escalated work to the
        # owner that Hermes could have taken.
        status = {"healthy": "online", "degraded": "degraded",
                  "checking": "degraded"}.get(st, "offline")
        return {"tier": "hermes", "label": "Hermes", "status": status,
                "detail": (h.get("summary") or "")[:90]}
    except Exception:  # noqa: BLE001
        return {"tier": "hermes", "label": "Hermes", "status": "offline", "detail": ""}


def chain():
    return [_friday_tier(), _hermes_tier(),
            {"tier": "you", "label": "You", "status": "online",
             "detail": "final authority: approves, builds, decides"}]


# --------------------------------------------------------------------------
# routing decision (never executes)
# --------------------------------------------------------------------------

def _tools_for(text):
    """Friday's own tools that plausibly match the request, by name tokens."""
    try:
        from friday import capability_router as cr
        names = list(cr.CORE_TOOLS) + [n for v in cr.GROUPS.values() for n in v]
    except Exception:  # noqa: BLE001
        return []
    toks = {t for t in re.findall(r"[a-z]{4,}", text.lower())}
    hits = []
    for n in names:
        parts = set(n.lower().split("_"))
        if toks & parts:
            hits.append(n)
    return hits[:6]


def route(task):
    """Which tier should take this, and why. A decision, not an action."""
    task = (task or "").strip()
    ch = chain()
    friday, hermes = ch[0], ch[1]
    tools = _tools_for(task)
    if tools and friday["status"] == "online":
        return {"tier": "friday", "why": "Friday has tools for this",
                "tools": tools, "chain": ch}
    if hermes["status"] in ("online", "degraded"):
        return {"tier": "hermes",
                "why": ("no matching Friday tool" if not tools else "Friday is offline")
                       + "; Hermes executes",
                "tools": tools, "chain": ch}
    return {"tier": "you", "why": "neither Friday nor Hermes can take this right now",
            "ask": "Sir, should I build this for you?", "tools": tools, "chain": ch}


# --------------------------------------------------------------------------
# dynamic team proposal
# --------------------------------------------------------------------------

_STOP = {"the", "and", "for", "with", "that", "this", "from", "into", "your", "our"}


def assemble(goal, limit=6):
    """Propose a team for a goal by scoring every upstream agent against it."""
    toks = [t for t in re.findall(r"[a-z]{3,}", (goal or "").lower()) if t not in _STOP]
    scored = []
    for d in divisions():
        for a in d["agents"]:
            hay = ("%s %s %s" % (d["id"], a["name"], a["description"])).lower()
            score = sum(hay.count(t) for t in toks)
            if score:
                scored.append((score, d, a))
    scored.sort(key=lambda x: -x[0])
    team, seen = [], set()
    for score, d, a in scored:
        if a["id"] in seen:
            continue
        seen.add(a["id"])
        team.append({"agent": a["name"], "division": d["label"],
                     "color": a["color"] or d["color"], "score": score,
                     "why": a["description"][:100]})
        if len(team) >= limit:
            break
    return {"goal": goal, "team": team,
            "note": "proposal only; the objective engine spawns agents on approval"}


def _sources():
    """Which upstream(s) actually contributed divisions -- both, one, or none."""
    names = []
    if (UPSTREAM / "divisions.json").exists():
        names.append("third_party/upstream/agency-agents")
    if VOLT_UPSTREAM.is_dir():
        names.append("third_party/upstream/awesome-claude-code-subagents")
    return names


def state():
    divs = divisions()
    return {
        "principal": "You",
        "chain": chain(),
        "divisions": [{"id": d["id"], "label": d["label"], "color": d["color"],
                       "icon": d["icon"], "size": d["size"],
                       "agents": d["agents"][:8]} for d in divs],
        "agents_total": sum(d["size"] for d in divs),
        "source": ", ".join(_sources()) or "unavailable",
    }
