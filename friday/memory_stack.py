"""
friday/memory_stack.py -- the four-tier memory, assembled into one context.

The owner's blueprint, made concrete on what Friday already has:

  Tier 1  PREFERENCES  (the Mem0 role)   what you like, want and rule out --
          extracted automatically from conversation by friday/profile.py into
          `memories` (scopes: preferences, wants, goals), plus the Mem0 feed
          adapter when it is installed.
  Tier 2  SPECS        (the Obsidian role) human-owned markdown: the vault
          (`vault/`, or a real Obsidian vault via FRIDAY_VAULT). Read by task:
          pages whose title or body match the request.
  Tier 3  RULES        (the GBrain role)   git-versioned rules of engagement
          for Hermes and sub-agents: the brain ledger
          (docs/knowledge/brain_ledger.jsonl) and AGENTS.md.
  Tier 4  RELATIONS    (the Graphiti role) how things connect over time: the
          neighbourhood of the request in friday/memory_graph, with open
          contradictions surfaced, plus the Graphiti feed when reachable.

`aggregate(task)` returns the four tiers and ONE prompt block under a token
budget (directive 3.6: default 2,000 tokens injected per model call), and
records which entries were injected. `log_result()` is the "sync & log" step:
a markdown summary written back into the vault so the owner reviews work on
his desktop. None of this is a new store -- every tier reads an existing one.
"""
from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BUDGET_TOKENS = int(os.getenv("FRIDAY_MEMORY_BUDGET", "2000"))
_STOP = {"the", "and", "for", "with", "that", "this", "from", "into", "your",
         "you", "tell", "please", "about", "what", "have", "does", "make"}


def _tokens(text):
    return [t for t in re.findall(r"[a-z0-9]{3,}", (text or "").lower()) if t not in _STOP]


def _approx_tokens(s):
    return max(1, len(s) // 4)


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ---------------------------------------------------------------------------
# tier 1: preferences (Mem0 role)
# ---------------------------------------------------------------------------

def preferences(task="", limit=12, project_scope=""):
    from friday import ui_server as U
    conn = U._connect()
    try:
        # FR-017: a project-scoped preference is only visible inside that
        # project. `project_scope` '' (a request about nothing in particular)
        # sees only the global ones - never another project's.
        rows = U._rows(conn, "SELECT subject, value, scope, confidence, created_at, "
                             "project_scope FROM memories WHERE superseded=0 AND scope IN "
                             "('preferences','wants','goals','identity') "
                             "AND (project_scope='' OR project_scope=?) "
                             "ORDER BY id DESC LIMIT 200", (project_scope,))
        if not rows and conn is not None:
            # A database from before the project_scope column existed.
            rows = U._rows(conn, "SELECT subject, value, scope, confidence, created_at "
                                 "FROM memories WHERE superseded=0 AND scope IN "
                                 "('preferences','wants','goals','identity') "
                                 "ORDER BY id DESC LIMIT 200")
    finally:
        if conn is not None:
            conn.close()
    toks = set(_tokens(task))
    scored = []
    for r in rows:
        hay = ("%s %s" % (r.get("subject"), r.get("value"))).lower()
        score = sum(1 for t in toks if t in hay)
        scored.append((score, r))
    scored.sort(key=lambda x: (-x[0], x[1].get("created_at") or ""), reverse=False)
    scored.sort(key=lambda x: -x[0])
    out = [{"subject": r["subject"], "value": (r.get("value") or "")[:160],
            "scope": r.get("scope"), "confidence": r.get("confidence"),
            "matched": s > 0} for s, r in scored[:limit]]
    feed = "unavailable"
    try:
        from friday.fabric_adapters import mem0_memory
        feed = mem0_memory.health(None)["status"]
    except Exception:  # noqa: BLE001
        pass
    return {"items": out, "source": "memories (profile.py extraction)", "mem0_feed": feed}


# ---------------------------------------------------------------------------
# tier 2: specs (Obsidian role)
# ---------------------------------------------------------------------------

def specs(task="", limit=4, excerpt=600):
    from friday import vault as V
    root = V.VAULT
    if not root.exists():
        return {"items": [], "source": str(root), "note": "vault not synced yet"}
    toks = _tokens(task)
    hits = []
    for p in root.rglob("*.md"):
        try:
            text = p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        low = text.lower()
        name = p.stem.lower()
        score = sum(low.count(t) for t in toks) + 3 * sum(1 for t in toks if t in name)
        if score:
            hits.append((score, p, text))
    hits.sort(key=lambda x: -x[0])
    items = []
    for score, p, text in hits[:limit]:
        body = re.sub(r"^---.*?---\s*", "", text, count=1, flags=re.S)
        items.append({"path": str(p.relative_to(root)).replace("\\", "/"),
                      "score": score, "excerpt": body[:excerpt].strip(),
                      "hand_edited": "generated-by: friday.vault" not in text})
    return {"items": items, "source": str(root)}


# ---------------------------------------------------------------------------
# tier 3: rules (GBrain role)
# ---------------------------------------------------------------------------

def rules(task="", limit=8):
    items = []
    try:
        from friday.brain import SharedBrainAdapter
        p = Path(SharedBrainAdapter()._ledger_path())
        if p.exists():
            seen = set()
            for line in p.read_text(encoding="utf-8").splitlines():
                try:
                    e = json.loads(line)
                except ValueError:
                    continue
                fact = (e.get("fact") or "").strip()
                if fact and fact not in seen:
                    seen.add(fact)
                    items.append({"rule": fact[:200], "entity": e.get("entity") or "friday",
                                  "provenance": (e.get("provenance") or "")[:80]})
    except Exception:  # noqa: BLE001
        pass
    agents_md = ROOT / "AGENTS.md"
    if agents_md.exists():
        try:
            for ln in agents_md.read_text(encoding="utf-8", errors="replace").splitlines():
                ln = ln.strip()
                if ln.startswith(("- ", "* ")) and len(ln) > 12:
                    items.append({"rule": ln[2:][:200], "entity": "repo", "provenance": "AGENTS.md"})
        except OSError:
            pass
    toks = set(_tokens(task))
    items.sort(key=lambda r: -sum(1 for t in toks if t in r["rule"].lower()))
    return {"items": items[:limit], "source": "brain ledger (git) + AGENTS.md",
            "total": len(items)}


# ---------------------------------------------------------------------------
# tier 4: relations (Graphiti role)
# ---------------------------------------------------------------------------

def relations(task="", limit=12):
    from friday import memory_graph as G
    g = G.build()
    label = {n["id"]: n for n in g["nodes"]}
    toks = set(_tokens(task))
    seed = {n["id"] for n in g["nodes"]
            if any(t in (n.get("label", "") + " " + n.get("value", "")).lower() for t in toks)}
    edges = []
    for l in g["links"]:
        if l["source"] in seed or l["target"] in seed:
            a, b = label.get(l["source"]), label.get(l["target"])
            if a and b:
                edges.append({"from": a["label"], "kind": l["kind"], "to": b["label"],
                              "at": b.get("at") or a.get("at") or ""})
    edges.sort(key=lambda e: e["at"], reverse=True)
    conflicts = [{"subject": n["group"], "existing": n.get("existing"), "proposed": n.get("proposed")}
                 for n in g["nodes"] if n["type"] == "conflict"
                 and any(t in (n.get("existing", "") + n.get("proposed", "")).lower() for t in toks)]
    feed = "unavailable"
    try:
        from friday.fabric_adapters import graphiti_memory
        feed = graphiti_memory.health(None)["status"]
    except Exception:  # noqa: BLE001
        pass
    return {"items": edges[:limit], "conflicts": conflicts[:4], "seeds": len(seed),
            "source": "memory_graph (temporal: superseded + contradictions)",
            "graphiti_feed": feed}


# ---------------------------------------------------------------------------
# aggregate: one context block under a budget
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# tier 5: episodes -- what was actually said, across sessions
# ---------------------------------------------------------------------------

#: How many past turns travel with every request. The owner asked for 20-30
#: chats of continuity; 30 turns of a spoken conversation is roughly 1-2k
#: characters, which the budget in `aggregate` trims further if it has to.
EPISODE_TURNS = int(os.getenv("FRIDAY_EPISODE_TURNS", "30"))

#: The most of the context budget the raw transcript may take.
EPISODE_BUDGET_SHARE = float(os.getenv("FRIDAY_EPISODE_SHARE", "0.45"))


def _store():
    from friday.toolsets.memory import store
    return store()


def episodes(task="", limit=None):
    """
    Recent conversation turns, oldest first.

    The four tiers above are all *derived* memory - preferences extracted from
    talk, specs written by hand, rules committed to git, edges inferred. None
    of them is the talk itself, which is why a new session could greet the
    owner as a stranger while every tier reported healthy. This is the raw
    record, and it is deliberately not scoped to the current conversation:
    continuity that ends at a session boundary is the bug, not the feature.
    """
    limit = limit or EPISODE_TURNS
    try:
        rows = _store().recent_messages(limit=limit)
    except Exception:  # noqa: BLE001
        return {"tier": "episodes", "items": []}
    return {"tier": "episodes", "items": rows}


# ---------------------------------------------------------------------------
# tier 6: contacts -- the people a request is about
# ---------------------------------------------------------------------------

def contacts(task="", limit=5):
    """Contacts this request plausibly names. Empty for a request about no one."""
    try:
        rows = _store().find_contacts(task, limit=limit)
    except Exception:  # noqa: BLE001
        return {"tier": "contacts", "items": []}
    return {"tier": "contacts", "items": rows}


# ---------------------------------------------------------------------------
# tier 7: hermes outcomes -- what the last delegations actually did
# ---------------------------------------------------------------------------

def hermes_outcomes(task="", limit=3):
    """Recent terminal Hermes work runs (friday.hermes_bridge.on_terminal).

    Not a new table: `project_decisions` already exists and, unlike
    episodes, already survives `include_episodes=False` - the one thing a
    follow-up Hermes bundle needs. "What did the last delegation do" is a
    recency question, not a keyword match, so this is unscored like
    episodes rather than ranked like specs/preferences.
    """
    try:
        rows = _store().decisions("hermes")
    except Exception as exc:  # noqa: BLE001
        import logging
        logging.getLogger("friday.memory").warning(
            "outcomes tier unavailable (%s): 'what did Hermes do' will answer nothing", exc)
        return {"tier": "outcomes", "items": [], "error": str(exc)[:120]}
    return {"tier": "outcomes", "items": rows[:limit]}


def aggregate(task, budget_tokens=None, include_episodes=True, project_scope=""):
    """The task-scoped slice of every tier, within `budget_tokens`.

    `include_episodes=False` is the delegation shape. The episode tier is
    the last thirty spoken turns, and it takes EPISODE_BUDGET_SHARE of the
    budget by design so the voice brain keeps continuity - but a Hermes
    sub-agent handed a coding goal has no use for "he: we would be," and
    measured, that share crowded out 6 of 8 rules, all 4 specs and all 12
    relations from a 600-token bundle. What Friday knows that a worker
    needs is the rules, specs, relations and preferences; what was said is
    already in the goal.

    `project_scope` (FR-017) names the project the request is about; a
    preference recorded for another project is excluded BEFORE anything
    reaches a prompt. The token accounting returned here is the context
    telemetry FR-020 asks for: budget, tokens used, items per tier.
    """
    budget = budget_tokens or BUDGET_TOKENS
    t1, t2, t3, t4 = (preferences(task, project_scope=project_scope), specs(task),
                      rules(task), relations(task))
    t5 = episodes(task) if include_episodes else {"items": []}
    t6 = contacts(task)
    t7 = hermes_outcomes(task)
    used, lines, injected = 0, [], {"preferences": 0, "specs": 0, "rules": 0,
                                    "relations": 0, "episodes": 0, "contacts": 0,
                                    "outcomes": 0}

    def take(section, text, key):
        nonlocal used
        cost = _approx_tokens(text)
        if used + cost > budget:
            return False
        lines.append(text)
        used += cost
        injected[key] += 1
        return True

    # Ordered before the derived tiers on purpose. `take` stops at the budget,
    # so whatever is appended last is what gets dropped - and the thing that
    # must never be dropped is what he actually said last time.
    if t6["items"]:
        lines.append("PEOPLE HE MENTIONED:")
        for c in t6["items"]:
            detail = ", ".join(x for x in (
                c.get("relation"), c.get("phone"), c.get("email"),
                (c.get("notes") or "")[:80]) if x)
            if not take("contact", "- %s: %s" % (c["name"], detail or "no details on file"),
                        "contacts"):
                break
    if t5["items"]:
        # Thirty turns can be 1,800 tokens on their own, which under the 900
        # the voice brain asks for would leave nothing for preferences, rules
        # or specs - continuity bought by making Friday forget everything she
        # was taught. So the transcript gets a share, not the lot: the newest
        # turns are kept and the oldest fall off first.
        room = max(1, int(budget * EPISODE_BUDGET_SHARE))
        kept, spent = [], 0
        for m in reversed(t5["items"]):
            line = "- %s: %s" % ("he" if m.get("role") == "user" else "you",
                                 (m.get("content") or "")[:240].replace(chr(10), " "))
            cost = _approx_tokens(line)
            if spent + cost > room:
                break
            kept.append(line)
            spent += cost
        if kept:
            lines.append("WHAT WAS SAID BEFORE (most recent last):")
            for line in reversed(kept):
                if not take("episode", line, "episodes"):
                    break
    if t7["items"]:
        # High priority, same reasoning as contacts/episodes above: this is
        # the one thing a Hermes follow-up bundle needs (include_episodes
        # =False skips episodes entirely, so this must not depend on it),
        # and it is also what "what did Hermes just do?" needs live.
        lines.append("RECENT HERMES OUTCOMES:")
        for d in t7["items"]:
            if not take("outcome", "- %s" % d["decision"][:300], "outcomes"):
                break
    if t1["items"]:
        lines.append("YOUR PREFERENCES AND RULES:")
        for it in t1["items"]:
            if not take("pref", "- %s: %s" % (it["subject"].split(".", 1)[-1], it["value"]), "preferences"):
                break
    if t3["items"]:
        lines.append("RULES OF ENGAGEMENT (git-tracked):")
        for it in t3["items"]:
            if not take("rule", "- %s" % it["rule"], "rules"):
                break
    if t2["items"]:
        lines.append("RELEVANT SPECS FROM THE VAULT:")
        for it in t2["items"]:
            if not take("spec", "- [%s] %s" % (it["path"], it["excerpt"][:300].replace("\n", " ")), "specs"):
                break
    if t4["items"] or t4["conflicts"]:
        lines.append("HOW THINGS CONNECT:")
        for e in t4["items"]:
            if not take("rel", "- %s -%s-> %s" % (e["from"], e["kind"], e["to"]), "relations"):
                break
        for c in t4["conflicts"]:
            take("rel", "- OPEN CONTRADICTION on %s: '%s' vs '%s'" % (c["subject"], c["existing"], c["proposed"]), "relations")
    return {"task": task, "budget_tokens": budget, "tokens_used": used, "injected": injected,
            "tiers": {"preferences": t1, "specs": t2, "rules": t3, "relations": t4,
                      "episodes": t5, "contacts": t6, "outcomes": t7},
            "prompt": "\n".join(lines)}


def overview():
    """Counts per tier for the Memory view. Cheap; no task scoring."""
    from friday import ui_server as U
    conn = U._connect()
    try:
        pref = U._rows(conn, "SELECT COUNT(*) AS n FROM memories WHERE superseded=0 AND "
                             "scope IN ('preferences','wants','goals','identity')")
    finally:
        if conn is not None:
            conn.close()
    from friday import vault as V
    pages = sum(1 for _ in V.VAULT.rglob("*.md")) if V.VAULT.exists() else 0
    r = rules("")
    from friday import memory_graph as G
    st = G.build()["stats"]
    feeds = {}
    for name, mod in (("mem0", "mem0_memory"), ("graphiti", "graphiti_memory")):
        try:
            m = __import__("friday.fabric_adapters." + mod, fromlist=["health"])
            feeds[name] = m.health(None)["status"]
        except Exception:  # noqa: BLE001
            feeds[name] = "unavailable"
    return {"tiers": [
        {"tier": 1, "name": "Preferences", "role": "Mem0", "count": (pref[0]["n"] if pref else 0),
         "source": "profile.py extraction into memories", "feed": feeds["mem0"]},
        {"tier": 2, "name": "Specs", "role": "Obsidian", "count": pages,
         "source": str(V.VAULT), "feed": "vault"},
        {"tier": 3, "name": "Rules", "role": "GBrain", "count": r["total"],
         "source": "brain ledger (git) + AGENTS.md", "feed": "git"},
        {"tier": 4, "name": "Relations", "role": "Graphiti", "count": st["links"],
         "source": "memory_graph, %d open contradictions" % st["conflicts_open"], "feed": feeds["graphiti"]},
    ], "budget_tokens": BUDGET_TOKENS}


# ---------------------------------------------------------------------------
# sync & log: write the outcome back into the vault
# ---------------------------------------------------------------------------

def log_result(task, summary, tier_usage=None):
    from friday import vault as V
    day = _now()[:10]
    slug = re.sub(r"[^a-z0-9]+", "-", (task or "task").lower()).strip("-")[:40] or "task"
    rel = "outputs/logs/%s_%s.md" % (day, slug)
    body = ["# %s" % (task or "Task"), "", "- when: %s" % _now(),
            "- context injected: %s" % json.dumps(tier_usage or {}), "", "## Summary", "", summary or "(no summary)"]
    report = {"written": [], "skipped_edited": [], "unchanged": 0}
    V._write(rel, task or "Task", "memory_stack.log_result", "\n".join(body), report)
    return {"path": rel, "written": bool(report["written"]), "unchanged": report["unchanged"] > 0}
