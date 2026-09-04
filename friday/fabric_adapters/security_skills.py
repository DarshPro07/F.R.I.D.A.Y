"""
anthropic-cybersecurity-skills: 818 defensive and offensive procedures, gated.

Detection engineering, forensics, malware analysis, compliance - and also
DPAPI credential extraction, Active Directory ACL abuse, shadow-credential
privilege escalation. The catalogue is genuinely useful to a defender and
genuinely a manual for an attacker, and the same file serves both. That is
not a reason to withhold it; it is the reason it is gated rather than open.

## Two gates, because the pack has two audiences

**Scope.** NON_NEGOTIABLE 5 and the security family exist so that Friday does
not act against systems it was not authorised to touch. Reading an offensive
procedure into context is the first step of using it, so `skill` refuses
unless the caller passes `authorized_scope` - a non-empty statement of what
engagement or owned system this is for. The adapter does not adjudicate the
scope; it refuses to proceed without one, and records it, so an authorised
red-team engagement reads exactly like itself and an idle request reads like
the mistake it is. `catalogue` and `search` are open, because knowing a
technique exists is not performing it, and a defender has to be able to look
things up.

**Risk.** The descriptor declares `risk=restricted` and a
`security.authorized_scope` permission. Those are data the policy engine reads
before this provider is ever activated; the argument check here is the second
line, for the path that reaches `call` directly.

## Metadata is free

The pack ships `index.json`: 818 name-and-description entries, generated
upstream. So `catalogue` and `search` never open a skill file - they read the
index - and only `skill`, behind the scope gate, reads a procedure. Nothing is
bulk-loaded, and the offensive 818 never sit in a prompt by accident.

## Licence

Apache-2.0 at the root and, unusually for a pack this size, in all 25 skill
subtrees the audit sampled - no nested surprise. Markdown only; no upstream
code executes.
"""

from __future__ import annotations

import functools
import json
import re

from friday import fabric
from friday.fabric_adapters import _skillpack

UPSTREAM = "anthropic-cybersecurity-skills"
SKILL_DIR = "skills"
INDEX = "index.json"


@functools.lru_cache(maxsize=1)
def _index() -> dict[str, dict]:
    """
    name -> {description, domain, path}, from the upstream-generated index.

    Reading the index instead of walking 818 directories is what keeps
    catalogue and search cheap. Cached; `_index.cache_clear()` after a tree
    change in a test.
    """
    root = _skillpack.pack_root(UPSTREAM)
    index_file = root / INDEX
    if not index_file.is_file():
        return {}
    try:
        raw = json.loads(index_file.read_text(encoding="utf-8", errors="replace"))
    except (OSError, json.JSONDecodeError):
        return {}
    found: dict[str, dict] = {}
    for entry in raw.get("skills", []):
        name = entry.get("name")
        if not name:
            continue
        found[name] = {
            "description": (entry.get("description") or "")[:400],
            "domain": entry.get("domain", ""),
            "path": f"{SKILL_DIR}/{name}/SKILL.md",
        }
    return found


def health(handle) -> dict:
    probe = _skillpack.health(UPSTREAM, INDEX)
    if probe["state"] != fabric.READY:
        return probe
    count = len(_index())
    if not count:
        return {"state": fabric.DEGRADED, "detail": "cloned, but index unread"}
    return {"state": fabric.READY,
            "detail": f"{count} skills indexed; skill reads need authorized_scope"}


def call(operation: str, handle, **arguments):
    if operation == "catalogue":
        # Open: knowing a technique exists is not performing it.
        return {name: {"description": entry["description"],
                       "domain": entry["domain"]}
                for name, entry in _index().items()}

    if operation == "search":
        query = (arguments.get("query") or "").strip().lower()
        if not query:
            raise fabric.FabricError("search needs a `query`")
        words = {w for w in re.findall(r"[a-z0-9]{3,}", query)}
        ranked = []
        for name, entry in _index().items():
            haystack = f"{name} {entry['description']}".lower()
            score = sum(1 for w in words if w in haystack)
            if query in name or name.replace("-", " ") in query:
                score += 3
            if score:
                ranked.append({"skill": name, "score": score,
                               "description": entry["description"]})
        ranked.sort(key=lambda row: (-row["score"], row["skill"]))
        return ranked[:10]

    if operation == "skill":
        # Gated: reading a procedure is the first step of using it.
        scope = (arguments.get("authorized_scope") or "").strip()
        if not scope:
            raise fabric.FabricError(
                "reading a security procedure needs authorized_scope: name the "
                "engagement or owned system this is for. catalogue and search "
                "are open; the procedure itself is not.")
        name = (arguments.get("name") or "").strip()
        index = _index()
        if name not in index:
            raise fabric.FabricError(f"{name!r} is not in the index")
        return {"authorized_scope": scope,
                "skill": name,
                "procedure": _skillpack.read(UPSTREAM, index[name]["path"])}

    raise fabric.FabricError(f"{UPSTREAM} has no operation {operation!r}")


DESCRIPTOR = fabric.Provider(
    id="security_skills",
    family="security",
    upstream=UPSTREAM,
    operations=("catalogue", "search", "skill"),
    risk="restricted",
    license_mode=fabric.PERMISSIVE,
    integration_mode=fabric.SKILL,
    permissions=("security.authorized_scope",),
    # Knowing is not doing. The notes below already said catalogue and search
    # are open; this is where that becomes enforceable rather than prose.
    open_operations=("catalogue", "search"),
    cost_class="free",
    model_required=False,
    commit="1b3f6b2286981381a5cc0566551ef3bb6bc38383",
    notes=(
        "Apache-2.0 at root and in all 25 sampled skill subtrees. 818 skills, "
        "defensive and offensive. catalogue and search read the upstream "
        "index and are open; `skill` refuses to read a procedure without an "
        "authorized_scope argument, and the descriptor declares "
        "risk=restricted plus a security.authorized_scope permission the "
        "policy engine reads before activation. Markdown only."
    ),
)
