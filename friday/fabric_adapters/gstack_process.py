"""
gstack: engineering review methodology, as text Friday reads when it applies.

Sixty-one markdown workflows - pre-landing review, CEO and eng-manager plan
review, designer's-eye QA, retro, ship, security officer mode, destructive-
command guardrails. Friday has 163 capabilities and essentially none of this:
a check for review/QA/audit/retro/ship names in `friday.capabilities` returns
`connector_verify` and `workbench_preview`, neither of which is a review
procedure. So the overlap is nil and the value is real.

## Why SKILL and not a worker

gstack ships a compiled browser CLI, a bun toolchain and per-workflow `bin/`
directories. None of that is used here. `integration_mode=SKILL` means no
upstream code is executed - the integration is "read this procedure when the
work actually calls for it", which is the only part Friday is missing. Friday
already owns planning (`planner.py`), verification (`evaluation.py`,
`honesty.py`) and orchestration (HermesAgency); gstack contributes the
*methodology* those run, not a second runtime.

## Routing, so the operator never types a slash command

Each SKILL.md carries a `triggers:` list in its frontmatter - "review this
pr", "code review", "check my diff". That is a routing table the upstream
already maintains, so `route` matches a plain request against it and returns
ranked skill names. "Friday, review this feature" resolves to `review`
without anyone learning `/plan-ceo-review` existed.

## What is deliberately not offered

`DENIED` excludes three groups, and the exclusions are the interesting part:

**Browser workflows.** gstack drives a headless Chromium daemon with ~70
commands of its own and BROWSER.md documents overriding the host's browser
rules. Friday has `browser_capability.py` and a policy engine that decides
what a browser may touch; importing a second set of browser instructions
would put two policies in one process, and the weaker one would win by
accident. The upstream brief calls this out by name - reconcile, do not copy.

**gstack's own installation and upgrade.** Skills that tell an agent to run
`gstack-upgrade`, edit `~/.claude`, or set up cookies are host management,
not methodology.

**iOS device workflows.** They need real hardware and a debug bridge.

Everything excluded stays readable in `third_party/upstream/gstack`; it is
simply not in the catalogue, and `skill` will not read a path outside it.

## One honest caveat

The included procedures still mention gstack's own slash commands in prose
(`/scrape`, `/skillify`). They are inert - nothing here executes them - but a
model reading a procedure may try. `notes` records it, and the text is
advisory rather than a script Friday follows.
"""

from __future__ import annotations

import re

from friday import fabric
from friday.fabric_adapters import _skillpack

UPSTREAM = "gstack"

#: Workflow directories excluded from the catalogue, and why. Kept as data so
#: the reason survives next to the exclusion rather than in a commit message.
DENIED = {
    # Friday's browser policy is the only browser policy.
    "browse": "Friday owns browser policy (browser_capability.py)",
    "browser-skills": "Friday owns browser policy",
    "connect-chrome": "Friday owns browser policy",
    "open-gstack-browser": "Friday owns browser policy",
    "pair-agent": "Friday owns browser policy",
    "scrape": "Friday owns browser policy; scraping family has its own providers",
    "skillify": "codifies gstack browser flows into Playwright scripts",
    "setup-browser-cookies": "writes browser credentials",
    "extension": "installs a browser extension; Friday owns browser policy",
    # Host management, not methodology.
    "gstack": "manages the gstack installation itself, not the work",
    "gstack-upgrade": "self-upgrade of an upstream",
    "setup-deploy": "configures a deploy target on the host machine",
    "setup-gbrain": "host setup; Friday has its own memory",
    "sync-gbrain": "host setup; Friday has its own memory",
    "supabase": "provisions a third-party database for the host project",
    "hosts": "edits the machine's host configuration",
    "codex": "wraps a different agent CLI",
    "claude": "host-specific configuration",
    # Needs real hardware.
    "ios-clean": "needs an iOS device",
    "ios-design-review": "needs an iOS device",
    "ios-fix": "needs an iOS device",
    "ios-qa": "needs an iOS device",
    "ios-sync": "needs an iOS device",
}

#: The review specialists, as a named set. They live one level down and are
#: checklists rather than workflows, so they get their own operation.
SPECIALIST_DIR = "review/specialists"

#: Frontmatter is parsed with a regex rather than a YAML dependency: three
#: scalar fields and one list, from a file the pack generates itself.
_FRONTMATTER = re.compile(r"\A---\r?\n(.*?)\r?\n---", re.S)
_FIELD = re.compile(r"^(name|description|version):\s*(.+?)\s*$", re.M)
_TRIGGERS = re.compile(r"^triggers:\s*$(.*?)(?=^\S|\Z)", re.M | re.S)
_ITEM = re.compile(r"^\s+-\s+(.+?)\s*$", re.M)


def _frontmatter(text: str) -> dict:
    found = _FRONTMATTER.search(text)
    if not found:
        return {}
    block = found.group(1)
    meta = {key: value.strip('"\'') for key, value in _FIELD.findall(block)}
    triggers = _TRIGGERS.search(block)
    meta["triggers"] = _ITEM.findall(triggers.group(1)) if triggers else []
    return meta


def _entries() -> dict[str, dict]:
    """
    Every offered workflow: name -> {description, triggers, path}.

    Reads only the frontmatter of each file, so the catalogue costs a few
    hundred bytes per skill rather than the whole procedure.
    """
    root = _skillpack.pack_root(UPSTREAM)
    if not root.is_dir():
        return {}
    found: dict[str, dict] = {}
    for path in sorted(root.glob("*/SKILL.md")):
        workflow = path.parent.name
        if workflow in DENIED:
            continue
        try:
            head = path.read_text(encoding="utf-8", errors="replace")[:2000]
        except OSError:
            continue
        meta = _frontmatter(head)
        if not meta:
            continue
        found[workflow] = {
            "description": meta.get("description", ""),
            "triggers": meta.get("triggers", []),
            "path": f"{workflow}/SKILL.md",
        }
    return found


def health(handle) -> dict:
    probe = _skillpack.health(UPSTREAM, "review/SKILL.md")
    if probe["state"] != fabric.READY:
        return probe
    offered = len(_entries())
    if not offered:
        return {"state": fabric.DEGRADED,
                "detail": "cloned, but no workflow frontmatter parsed"}
    return {"state": fabric.READY,
            "detail": f"{offered} workflows offered, {len(DENIED)} withheld"}


def call(operation: str, handle, **arguments):
    if operation == "catalogue":
        return _entries()

    if operation == "withheld":
        # Diagnostic: what is deliberately not on offer, and why.
        return dict(DENIED)

    if operation == "route":
        task = (arguments.get("task") or "").strip().lower()
        if not task:
            raise fabric.FabricError("route needs a task description")
        words = {w for w in re.findall(r"[a-z]{5,}", task)}
        # A separate, shorter floor for name tokens: the distinguishing part
        # of `plan-ceo-review` and `qa-only` is two or three letters, and a
        # five-letter floor throws away exactly the word that disambiguates.
        short = set(re.findall(r"[a-z]{2,}", task))
        entries = _entries()
        # How many workflow names each token appears in. "review" is in seven
        # of them and so says almost nothing about which one is meant;
        # "retro" is in one and says everything. Without this, a request for
        # a security review ranks every *-review workflow above `cso`.
        frequency: dict[str, int] = {}
        for other in entries:
            for token in set(other.split("-")):
                frequency[token] = frequency.get(token, 0) + 1

        ranked = []
        for name, entry in entries.items():
            tokens = set(name.split("-"))
            # A whole trigger phrase appearing in the request is the strongest
            # signal the upstream gives us.
            score = sum(4 for t in entry["triggers"] if t.lower() in task)
            score += sum(1 for t in entry["triggers"]
                         if words & set(re.findall(r"[a-z]{5,}", t.lower())))
            # The domain word usually lives in the description, not the
            # triggers: "security review" has to reach `cso`, whose triggers
            # never say "review".
            score += 2 * len(words & set(
                re.findall(r"[a-z]{5,}", entry["description"].lower())))
            # Every token of a multi-word name present in the request is a
            # strong signal: "ceo review ... plan" covers all of
            # `plan-ceo-review`, which a contiguous-substring test misses.
            matched = tokens & short
            score += sum(3 if frequency.get(t, 1) <= 2 else 1 for t in matched)
            if len(tokens) > 1 and matched == tokens:
                score += 4
            if score:
                ranked.append({"skill": name, "score": score,
                               "description": entry["description"]})
        ranked.sort(key=lambda row: (-row["score"], row["skill"]))
        return ranked[:5]

    if operation == "skill":
        name = (arguments.get("name") or "").strip()
        entries = _entries()
        if name not in entries:
            raise fabric.FabricError(
                f"{name!r} is not an offered gstack workflow"
                + (f" (withheld: {DENIED[name]})" if name in DENIED else ""))
        return _skillpack.read(UPSTREAM, entries[name]["path"])

    if operation == "specialists":
        return _skillpack.catalogue(UPSTREAM, SPECIALIST_DIR)

    if operation == "specialist":
        path = (arguments.get("path") or "").strip()
        # The catalogue is the allowlist. Without this, `path` is an arbitrary
        # read of anything under third_party by whoever composes the call.
        if path not in _skillpack.catalogue(UPSTREAM, SPECIALIST_DIR):
            raise fabric.FabricError(f"{path!r} is not a review specialist")
        return _skillpack.read(UPSTREAM, path)

    raise fabric.FabricError(f"{UPSTREAM} has no operation {operation!r}")


DESCRIPTOR = fabric.Provider(
    id="gstack_process",
    family="roles",
    upstream=UPSTREAM,
    operations=("catalogue", "route", "skill", "specialists", "specialist",
                "withheld"),
    risk="low",
    license_mode=fabric.PERMISSIVE,
    integration_mode=fabric.SKILL,
    cost_class="free",
    model_required=False,
    commit="a3749bfa4b0fcb33aebf875134d9f21252f23c46",
    fallbacks=("role_recipes",),
    notes=(
        "MIT. Markdown only - no upstream code is executed, and gstack's "
        "browser CLI, bun toolchain and per-workflow bin/ are unused. Browser, "
        "host-setup and iOS workflows are withheld (see `withheld`): Friday's "
        "browser policy is the only browser policy. Routing uses the upstream's "
        "own `triggers:` frontmatter, so no slash-command syntax is needed. "
        "Included procedures still mention gstack's slash commands in prose; "
        "they are inert here, and the text is advisory, not a script."
    ),
)
