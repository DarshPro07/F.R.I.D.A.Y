"""
awesome-claude-code-subagents (VoltAgent, MIT): 158 Claude Code subagent
briefs across ten categories, each frontmatter-only (name, description,
tools, model). No code, no generator, nothing to run -- exactly the
role_recipes shape, one upstream folder later.

`catalogue` maps every category to its agent names -- cheap, and what a
router needs to pick one. `search` matches a query against name and
description and returns names only. `category` scopes `catalogue` to one
folder. `recipe` reads exactly one brief, allowlisted against the same index
`catalogue` builds, so a name that was never in it (or a `../` path) is
refused the same way `role_recipes.recipe` refuses one -- there is no other
disk access in this module.
"""

from __future__ import annotations

import functools
import re

from friday import fabric
from friday.fabric_adapters import _skillpack

UPSTREAM = "awesome-claude-code-subagents"
CATEGORIES_DIR = "categories"

#: The ten category folders, spelled out so `category` can validate without
#: a directory walk.
CATEGORIES = ("01-core-development", "02-language-specialists",
              "03-infrastructure", "04-quality-security", "05-data-ai",
              "06-developer-experience", "07-specialized-domains",
              "08-business-product", "09-meta-orchestration",
              "10-research-analysis")

_FM = re.compile(r"^---\s*\n(.*?)\n---", re.S)


def _frontmatter(text: str) -> dict:
    m = _FM.match(text)
    out: dict = {}
    if not m:
        return out
    for line in m.group(1).splitlines():
        if ":" in line:
            k, v = line.split(":", 1)
            out[k.strip()] = v.strip().strip('"').strip("'")
    return out


@functools.lru_cache(maxsize=1)
def _index() -> dict[str, list[dict]]:
    """category -> [{"name", "description", "path"}, ...]. Frontmatter only;
    `_index.cache_clear()` after a tree change in a test."""
    root = _skillpack.pack_root(UPSTREAM) / CATEGORIES_DIR
    out: dict[str, list[dict]] = {}
    for category in CATEGORIES:
        entries = []
        cat_dir = root / category
        if cat_dir.is_dir():
            for f in sorted(cat_dir.glob("*.md")):
                if f.name.upper().startswith("README"):
                    continue
                try:
                    fm = _frontmatter(f.read_text(encoding="utf-8", errors="replace"))
                except OSError:
                    continue
                if not fm.get("name"):
                    continue
                entries.append({"name": fm["name"],
                                "description": (fm.get("description") or "")[:200],
                                "path": f"{CATEGORIES_DIR}/{category}/{f.name}"})
        out[category] = entries
    return out


def health(handle) -> dict:
    return _skillpack.health(
        UPSTREAM, "README.md",
        f"{CATEGORIES_DIR}/01-core-development/backend-developer.md",
        f"{CATEGORIES_DIR}/02-language-specialists/python-pro.md")


def call(operation: str, handle, **arguments):
    if operation == "agents":
        return {cat: [e["name"] for e in entries]
                for cat, entries in _index().items()}
    if operation == "agent_category":
        name = (arguments.get("name") or "").strip()
        if name not in CATEGORIES:
            raise fabric.FabricError(
                f"unknown category {name!r}; known: {list(CATEGORIES)}")
        return [e["name"] for e in _index().get(name, [])]
    if operation == "find_agent":
        query = (arguments.get("query") or "").strip().lower()
        if not query:
            raise fabric.FabricError("search needs a `query`")
        words = set(re.findall(r"[a-z0-9]{3,}", query))
        hits = set()
        for entries in _index().values():
            for e in entries:
                haystack = f"{e['name']} {e['description']}".lower()
                if query in e["name"] or words & set(re.findall(r"[a-z0-9]{3,}", haystack)):
                    hits.add(e["name"])
        return sorted(hits)
    if operation == "agent":
        path = (arguments.get("path") or arguments.get("name") or "").strip()
        if not path:
            raise fabric.FabricError("agent needs a path or name from agents/find_agent")
        # The index is the allowlist, exactly as in role_recipes.recipe:
        # without this, `path` is an arbitrary read under third_party.
        entries = [e for es in _index().values() for e in es]
        allowed = {e["path"] for e in entries}
        if path not in allowed:
            # find_agent returns names, so a bare name ("scrum-master") must
            # resolve here; the model cannot know the category prefix. Only a
            # unique catalogue match resolves - anything else, traversal
            # included, is refused with the same message.
            key = path.lower().removesuffix(".md")
            match = [e["path"] for e in entries
                     if e["name"].lower() == key
                     or e["path"].rsplit("/", 1)[-1].removesuffix(".md") == key]
            if len(match) != 1:
                raise fabric.FabricError(f"{path!r} is not in this pack's catalogue")
            path = match[0]
        return _skillpack.read(UPSTREAM, path)
    raise fabric.FabricError(f"{UPSTREAM} has no operation {operation!r}")


DESCRIPTOR = fabric.Provider(
    id="claude_subagents",
    family="roles",
    upstream=UPSTREAM,
    operations=("agents", "find_agent", "agent", "agent_category"),
    risk="low",
    license_mode=fabric.PERMISSIVE,
    integration_mode=fabric.SKILL,
    open_operations=("agents", "find_agent", "agent", "agent_category"),
    cost_class="free",
    model_required=False,
    commit="009544a05267426b3896c77230177967f99f6360",
    fallbacks=("role_recipes",),
    notes=("MIT (VoltAgent). 158 subagent briefs across 10 categories, "
           "frontmatter-only (name/description/tools/model). catalogue, "
           "search and category are names only; recipe reads one brief, "
           "allowlisted against the index -- no bulk read, no upstream "
           "code executed."),
)
