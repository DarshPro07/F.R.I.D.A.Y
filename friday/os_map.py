"""
friday/os_map.py -- the Agentic OS map: You -> Jarvis -> capability domains.

The architecture diagram, generated from what this machine ACTUALLY has rather
than drawn by hand: columns are domains, cards are real MCP capability groups
and real fabric providers, and every count comes from the live registry.

Cadence marks how a card runs, which is the honest distinction:
    on_demand  -- you or Jarvis invoke it
    always_on  -- core, always active in the tool surface
    scheduled  -- an automation/routine fires it
    isolated   -- a sidecar engine, deliberately out of process
"""
from __future__ import annotations

ON_DEMAND, ALWAYS_ON, SCHEDULED, ISOLATED = (
    "on_demand", "always_on", "scheduled", "isolated")

#: domain -> the capability_router groups that belong to it. Anything not
#: listed lands in OPS / CUSTOM, so a new group is never silently invisible.
DOMAINS = (
    ("FOUNDATIONS", "always on", ("memory_extra", "brain", "objectives",
                                  "capabilities", "profile")),
    ("PRODUCTIVITY", "the machine", ("files", "windows", "computer", "hardware",
                                     "processes", "power", "brightness",
                                     "audio", "music")),
    ("RESEARCH", "find out", ("research", "news", "browser", "youtube",
                              "documents", "vision")),
    ("CONTENT", "make things", ("workbench", "products")),
    ("COMMUNITY", "reach people", ("connectors", "identity", "reminders")),
    ("AGENCY", "the team", ("executor", "hermes", "operations")),
    ("OPS", "keep it honest", ("automations", "operating_policy",
                               "browser_policy", "utils")),
)

#: fabric family -> the domain column its providers appear in
FAMILY_DOMAIN = {
    "code_intelligence": "AGENCY",
    "roles": "AGENCY",
    "research": "RESEARCH",
    "scraping": "RESEARCH",
    "presentation": "CONTENT",
    "writing": "CONTENT",
    "security": "OPS",
    "diagnostic": "OPS",
}


#: provider id -> what that capability DOES, in the owner's language.
#:
#: A provider id is an implementation detail and the control room is a product
#: surface, so it should read "Read a web page", not "scrapling_parse". This is
#: naming, not concealment: attribution is a licence obligation and lives in
#: THIRD_PARTY_NOTICES.md and third_party/UPSTREAM_LOCK.json, which is where a
#: licence expects to find it.
#:
#: An unknown id falls back to its own de-underscored name rather than being
#: dropped, so a newly added provider shows up immediately instead of silently
#: disappearing from the map.
CAPABILITY_NAMES = {
    "scrapling_parse": "Read a web page",
    "adhd_mode": "Focus and task shaping",
    "no_ai_slop": "Plain-writing check",
    "graft": "Repository grafting",
    "gstack_process": "Workflow library",
    "codebase_memory": "Codebase recall",
    "open_design": "Design systems",
    "role_recipes": "Agent role library",
    "science_skills": "Scientific method skills",
    "security_skills": "Security review skills",
    "diagram_design": "Diagrams",
    "prompt_master": "Prompt craft",
    "graphiti_memory": "Temporal relations",
    "mem0_memory": "Preference memory",
    "dummy": "Self-test",
    "dummy_backup": "Self-test fallback",
}


def capability_label(provider_id: str) -> str:
    """A human name for a provider, falling back to the id made readable."""
    return CAPABILITY_NAMES.get(
        provider_id, provider_id.replace("_", " ").strip().capitalize())


def _groups():
    try:
        from friday import capability_router as cr
        return dict(cr.GROUPS), len(cr.CORE_TOOLS)
    except Exception:  # noqa: BLE001
        return {}, 0


def _providers():
    """(family -> [{id, state, mode}]) from the live fabric registry."""
    out = {}
    try:
        from friday import fabric
        for pid, p in fabric.registry().items():
            try:
                state = fabric.state(pid)
            except Exception:  # noqa: BLE001
                state = "registered"
            out.setdefault(getattr(p, "family", "?"), []).append(
                {"id": pid, "state": state,
                 "mode": getattr(p, "integration_mode", "")})
    except Exception:  # noqa: BLE001
        pass
    return out


def _engines():
    try:
        from friday import harness as H
        return H.availability()
    except Exception:  # noqa: BLE001
        return []


def build():
    groups, core_count = _groups()
    provs = _providers()
    claimed = set()
    columns = []

    for name, subtitle, keys in DOMAINS:
        cards = []
        for k in keys:
            if k in groups:
                claimed.add(k)
                cards.append({"label": k, "meta": "%d tools" % len(groups[k]),
                              "cadence": ALWAYS_ON if name == "FOUNDATIONS"
                              else ON_DEMAND, "kind": "tools"})
        for fam, items in provs.items():
            if FAMILY_DOMAIN.get(fam) != name:
                continue
            for p in items:
                cards.append({"label": capability_label(p["id"]), "meta": fam,
                              "cadence": ISOLATED if p["mode"] in
                              ("SIDECAR", "sidecar") else ON_DEMAND,
                              "kind": "skill", "state": p["state"]})
        columns.append({"name": name, "subtitle": subtitle, "cards": cards})

    leftovers = [{"label": k, "meta": "%d tools" % len(v), "cadence": ON_DEMAND,
                  "kind": "tools"} for k, v in sorted(groups.items())
                 if k not in claimed]
    columns.append({"name": "OPS / CUSTOM", "subtitle": "everything else",
                    "cards": leftovers})

    skills = sum(len(v) for v in provs.values())
    return {
        "conductor": {"name": "JARVIS", "role": "the conductor",
                      "principal": "You / Client"},
        "columns": columns,
        "status": {
            "live": True,
            "tools": core_count + sum(len(v) for v in groups.values()),
            "core_tools": core_count,
            "groups": len(groups),
            "skills": skills,
            "families": len(provs),
            "engines": _engines(),
        },
    }
