#!/usr/bin/env python3
"""
Compute REQUESTED - ALREADY_STAGED and audit whatever is left over.

Run:  .venv/Scripts/python.exe scripts/new_upstream_set.py --plan
      .venv/Scripts/python.exe scripts/new_upstream_set.py --audit

The set difference is computed rather than recalled. A list of eight
repositories typed from memory is a list nobody can check; deriving it from
the requested URLs and the directories actually present under
`third_party/upstream/` means a normalisation mistake shows up as a wrong
count instead of as a quietly missing audit.

`--plan` prints the difference and the clone commands. `--audit` reads the
clones and writes `docs/integrations/NEW_UPSTREAM_SET.json`.

Nothing here installs dependencies or runs an upstream's setup script. A
README is untrusted text until a person has read it.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import re
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
UPSTREAM = ROOT / "third_party" / "upstream"
OUT = ROOT / "docs" / "integrations" / "NEW_UPSTREAM_SET.json"

#: Every repository the operator asked for, verbatim.
REQUESTED = (
    # Added after the original set, and cloned before they were listed here --
    # which is exactly what test_no_upstream_was_staged_without_being_requested
    # caught. All three are in use and permissively licensed:
    #   graphiti  Apache-2.0, tier-4 relations   (friday/fabric_adapters/graphiti_memory.py)
    #   mem0      Apache-2.0, tier-1 preferences (friday/fabric_adapters/mem0_memory.py)
    #   ultron    MIT, the control room's core   (ui/orb.js is a port of it)
    "https://github.com/getzep/graphiti",
    "https://github.com/mem0ai/mem0",
    "https://github.com/SAGAR-TAMANG/ultron-by-sagar-builds",
    # Three the owner asked for on 2026-08-31. All MIT (auto-company declares MIT
    # in package.json; it has no LICENSE file, so the audit will show it
    # UNIDENTIFIED and the decision below records why it is safe). All three are
    # whole applications / orchestration patterns, not components -- reference,
    # not import, so none touches the single-control-layer rule.
    "https://github.com/kunchenguid/firstmate",
    "https://github.com/maxmiksa/auto-company",
    "https://github.com/andrewyng/openworker",
    # Three app-builders the owner sent on 2026-08-31. All whole applications
    # with their own UIs and heavy external deps (WebContainer's commercial
    # licence, Firecrawl + E2B paid APIs, Supabase + CodeSandbox), so all three
    # are reference, never wired -- Friday delivers their capability natively
    # (Hermes builds; the browser + scrapling clone a site's UI). See DECISIONS.
    "https://github.com/stackblitz-labs/bolt.diy",
    "https://github.com/firecrawl/open-lovable",
    "https://github.com/onlook-dev/onlook",
    "https://github.com/ultrafunkamsterdam/nodriver",
    "https://github.com/OpenHands/openhands",
    "https://github.com/getmaxun/maxun",
    "https://github.com/browser-use/browser-use",
    "https://github.com/Panniantong/agent-reach",
    "https://github.com/trailhq/Graft",
    "https://github.com/msitarzewski/agency-agents",
    "https://github.com/DeusData/codebase-memory-mcp",
    "https://github.com/calesthio/OpenMontage",
    "https://github.com/lfnovo/open-notebook",
    "https://github.com/petergyang/no-ai-slop",
    "https://github.com/ayghri/i-have-adhd",
    "https://github.com/usestrix/strix",
    "https://github.com/ItzCrazyKns/Vane",
    "https://github.com/Fosowl/agenticSeek",
    "https://github.com/d4vinci/Scrapling",
    "https://github.com/garrytan/gstack",
    "https://github.com/mintplex-labs/anything-llm",
    "https://github.com/pipecat-ai/pipecat",
    "https://github.com/gitroomhq/postiz-app",
    "https://github.com/crewaiinc/crewai",
    "https://github.com/cline/cline",
    "https://github.com/volcengine/OpenViking",
    "https://github.com/rohitg00/agentmemory",
    "https://github.com/cathrynlavery/diagram-design",
    "https://github.com/k-dense-ai/scientific-agent-skills",
    "https://github.com/ai-boost/awesome-harness-engineering",
    "https://github.com/mukul975/anthropic-cybersecurity-skills",
    "https://github.com/chaitanyagiri/munder-difflin",
    "https://github.com/different-ai/openwork",
    "https://github.com/nidhinjs/prompt-master",
    "https://github.com/nexu-io/open-design",
    # Three the owner sent on 2026-09-02 with the product-trading brief.
    #   OpenMausBot  Apache-2.0 (enterprise/ carved out), a multi-bot chat
    #                app - a second control layer; its two skills are read.
    #   medusa       MIT core (ENTERPRISE-LICENSE.md carved out), headless
    #                commerce - the trading backend, reached over admin REST.
    #   Smartstore   AGPL-3.0, .NET commerce - HTTP client to its OData API
    #                only, never linked.
    "https://github.com/milind-soni/OpenMausBot",
    "https://github.com/medusajs/medusa",
    "https://github.com/smartstore/Smartstore",
    # Two the owner sent on 2026-09-03 for the "roles" family (jarvis-agentic-
    # team S4). Both MIT, both markdown-only content behind a control layer
    # that Friday never runs -- see DECISIONS below.
    #   agents-team                     a Claude Code plugin (scaffold.py /
    #                                    lint.py); its templates/rules/skills
    #                                    are read as friday/fabric_adapters/
    #                                    agents_team_pack.py.
    #   awesome-claude-code-subagents   158 agent briefs, ten categories,
    #                                    frontmatter only; read as friday/
    #                                    fabric_adapters/claude_subagents.py.
    "https://github.com/fadymondy/agents-team",
    "https://github.com/VoltAgent/awesome-claude-code-subagents",
)

#: The build pack named two directories differently from their repositories.
#: Spelled out rather than pattern-matched, because a rule that strips "-app"
#: or removes hyphens would also silently merge two genuinely distinct repos.
ALIASES = {"anything-llm": "anythingllm", "postiz-app": "postiz",
           "ultron-by-sagar-builds": "ultron"}

#: How many the operator and the build pack agree should be left over. The
#: script asserts this rather than trusting the arithmetic.
#: graphiti, mem0 and ultron were added after the build pack was written,
#: so they are 'new to the pack' too -- 10 + 3 + 3 (firstmate, auto-company, openworker, then bolt.diy, open-lovable, onlook -- all added 2026-08-31),
#: then + 2 (agents-team, awesome-claude-code-subagents -- 2026-09-03).
EXPECTED_NEW = 25


def slug(url: str) -> str:
    name = url.rstrip("/").rsplit("/", 1)[-1].removesuffix(".git").lower()
    return ALIASES.get(name, name)


def staged() -> set[str]:
    if not UPSTREAM.is_dir():
        return set()
    return {p.name.lower() for p in UPSTREAM.iterdir() if p.is_dir()}


def build_pack() -> set[str]:
    """
    The upstreams the build pack already covered.

    Taken from the lock template's keys rather than from what happens to be
    on disk: once the new eight are cloned, "not yet staged" stops being able
    to name them, and the audit would silently become a no-op. The build pack
    is a fixed historical set, so it is the stable half of the subtraction.
    """
    template = ROOT / "Friday Stark Demo Main" / "06_schemas" / \
        "UPSTREAM_LOCK_TEMPLATE.json"
    keys = set(json.loads(template.read_text(encoding="utf-8")))
    briefs = {p.stem.lower() for p in
              (ROOT / "Friday Stark Demo Main" / "02_upstreams").glob("*.md")}
    return {k.lower() for k in keys} | briefs


def difference() -> tuple[list[str], set[str], set[str]]:
    """(requested-but-not-in-the-build-pack, staged, staged-but-unrequested)."""
    want = {slug(u): u for u in REQUESTED}
    new = [want[s] for s in sorted(want) if s not in build_pack()]
    have = staged()
    return new, have, have - set(want)


# --- audit -----------------------------------------------------------------

LICENSE_NAMES = ("LICENSE", "LICENSE.md", "LICENSE.txt", "COPYING", "LICENCE",
                 "LICENSE-MIT", "NOTICE")

SIGNATURES = (
    ("AGPL-3.0", r"GNU AFFERO GENERAL PUBLIC LICENSE"),
    ("GPL-3.0", r"GNU GENERAL PUBLIC LICENSE"),
    ("LGPL", r"GNU LESSER GENERAL PUBLIC"),
    ("Apache-2.0", r"Apache License"),
    ("BSD-3-Clause", r"BSD 3-Clause"),
    ("BSD-2-Clause", r"BSD 2-Clause"),
    ("MPL-2.0", r"Mozilla Public License"),
    ("PolyForm", r"PolyForm"),
    ("CC0-1.0", r"CC0 1\.0 Universal"),
    ("CC-BY-NC", r"Attribution[- ]NonCommercial|CC[- ]BY[- ]NC"),
    ("CC-BY", r"Creative Commons Attribution"),
    ("Elastic-2.0", r"Elastic License"),
    ("BUSL", r"Business Source License"),
    # A root file that carves out a subtree. Must be checked before the EE
    # signature: OpenWork's root LICENSE grants MIT to everything *except*
    # /ee, and naming the whole repository after the carve-out would be as
    # wrong as ignoring it. The subtree scan reports ee/LICENSE separately.
    ("SPLIT_MIT_PLUS_RESTRICTED_SUBTREE",
     r"Portions of this software are licensed as follows"),
    # Source-available: readable, but production use is gated on a paid
    # subscription. Not open source, and not safe to vendor.
    ("SOURCE_AVAILABLE_EE", r"Enterprise Edition \(EE\) License|"
                            r"may only\s+be used in production if you"),
    # Anthropic Skill terms forbid extraction, copying and derivative works.
    # A hard blocker for vendoring, so it must not be confused with the MIT
    # licence at the repository root.
    ("Anthropic-proprietary", r"ADDITIONAL RESTRICTIONS|"
                              r"Anthropic, PBC\. All rights reserved"),
    ("MIT", r"Permission is hereby granted, free of charge"),
)

#: Extensions that mean "source file that happens to be called notice", not
#: "licence". `ee/.../ui/notice.tsx` is a React component.
CODE_SUFFIXES = {".tsx", ".ts", ".js", ".jsx", ".py", ".go", ".rs", ".java",
                 ".c", ".h", ".cpp", ".css", ".html", ".vue", ".svelte"}

#: Subtrees whose separate licence is the point of the scan.
SEPARATE_TREES = re.compile(r"(^|/)(ee|enterprise|pro|cloud|assets?|skills?)(/|$)")

#: Vendored dependencies and CI config: real licences, but not decisions.
NOISE = re.compile(r"(node_modules|\.git/|/vendor/|/vendored/|\.github/|"
                   r"site-packages|/dist/|/build/)")

MANIFESTS = ("package.json", "pyproject.toml", "requirements.txt", "go.mod",
             "Cargo.toml", "setup.py", "pnpm-lock.yaml", "uv.lock")

#: Proposed mode per new upstream, with the reason. These are judgements made
#: after reading each clone's licence and README, so they are recorded rather
#: than derived - but they are recorded here, next to the evidence, instead of
#: in prose someone has to trust. Nothing is IMPLEMENTED at this phase.
#:
#: Mode vocabulary is `fabric.INTEGRATION_MODES` plus REJECTED.
DECISIONS = {
    "nodriver": (
        "REFERENCE_ONLY",
        "AGPL-3.0, so fabric.Provider refuses any importing mode outright -- it "
        "could only ever be an isolated sidecar. More to the point, its whole "
        "purpose is defeating Captcha, Cloudflare and anti-bot systems, which is "
        "detection-evasion Friday will not do; her browsing goes through the "
        "gated Playwright path inside netguard. Pinned only as a record of the "
        "direct-CDP automation technique, never wired."),
    "bolt.diy": (
        "REFERENCE_ONLY",
        "MIT, but WebContainer -- the tech it is built on -- needs a commercial "
        "licence for for-profit production use, and it is a whole standalone app "
        "(Electron/browser) with its own agent loop and UI. Building an app from "
        "a prompt is already Friday delegating to Hermes, so this is pinned as a "
        "reference for its streaming full-stack build patterns, not imported."),
    "open-lovable": (
        "REFERENCE_ONLY",
        "MIT, but it mandates paid Firecrawl scraping and a paid E2B/Vercel "
        "sandbox, and is a full Next.js app with its own UI. Its value -- read a "
        "site's UI into clean data -- Friday now does natively via its gated "
        "browser and scrapling (friday/ui_browser.study_url), inside netguard "
        "with no paid services. Pinned as reference for the clone pipeline."),
    "onlook": (
        "REFERENCE_ONLY",
        "Apache-2.0. A web-based visual editor that needs Supabase, the "
        "CodeSandbox SDK and Docker to run -- a whole hosted application, not a "
        "component. Visual editing is not a backend capability Friday can host, "
        "so it is pinned as a reference for its AST-safe code-editing patterns."),
    "firstmate": (
        "REFERENCE_ONLY",
        "MIT. An 'agent distro' -- a portable directory of instructions, skills, "
        "policies and state conventions for running a crew of agents. It is an "
        "orchestration convention, which is Friday's own job (NON_NEGOTIABLE 1), "
        "so it is pinned as a pattern to learn from; its skills/ directory could "
        "later feed the roles family through an adapter if that proves worth it."),
    "auto-company": (
        "REFERENCE_ONLY",
        "MIT (declared in package.json; no LICENSE file, so the audit reads it as "
        "UNIDENTIFIED). A fully autonomous AI 'company' of 14 agents that ideate, "
        "build, deploy and market, powered by Claude Code. It is a whole control "
        "layer, so it is reference only: Friday is the single orchestrator "
        "(NON_NEGOTIABLE 1, 11), and its value is the company workflow as a "
        "blueprint, not code to import."),
    "openworker": (
        "REFERENCE_ONLY",
        "MIT. A self-updating desktop AI coworker that runs on the machine with "
        "your own model key. It is a complete rival coworker application, not a "
        "component, so wiring it would duplicate Friday herself; pinned as "
        "reference for its task-execution and self-update patterns."),
    "graphiti": (
        "ADAPTER",
        "Apache-2.0, permissive, so an importing mode is allowed. It is the "
        "temporal-relations tier of the owner's four-tier memory design and "
        "friday/fabric_adapters/graphiti_memory.py already targets it. The "
        "adapter reports UNAVAILABLE until graphiti-core is installed, which "
        "is the required behaviour for an absent optional upstream."),
    "mem0": (
        "ADAPTER",
        "Apache-2.0, permissive. Supplies the preferences tier of the same "
        "four-tier memory design, behind friday/fabric_adapters/mem0_memory.py. "
        "Reports UNAVAILABLE until mem0ai is installed. GBrain stays canonical: "
        "this is a feed into the one memory, never a second one."),
    "ultron": (
        "REFERENCE_ONLY",
        "MIT. The control room's core is a hand-port of its orbScene and hand "
        "tracker into ui/orb.js, not a runtime import, so nothing links to the "
        "clone. It is pinned as the provenance record for that port and for the "
        "amber palette DESIGN.md commits to."),
    "openviking": (
        "SIDECAR",
        "AGPL-3.0. Copyleft, so fabric.Provider will refuse any importing "
        "mode outright. Its value is the hierarchical L0/L1/L2 context model, "
        "which can inform MemoryFabric as a pattern without linking the code. "
        "GBrain remains canonical memory."),
    "agentmemory": (
        "MCP",
        "Apache-2.0, permissive. Exposes memory over MCP/HTTP, so it fits the "
        "existing gateway without importing. Only worth enabling if it beats "
        "friday/store.py on coding-session recall - measure before adopting, "
        "per NON_NEGOTIABLE 11 (no duplicate memories)."),
    "diagram-design": (
        "SKILL",
        "MIT, prompt/recipe content rather than a runtime. Belongs in the "
        "presentation family beside adhd_mode as a lazily loaded skill."),
    "awesome-harness-engineering": (
        "REFERENCE_ONLY",
        "CC0-1.0 public domain. A curated list and templates, not runnable "
        "software. Use as the checklist for docs/architecture/HARNESS_AUDIT.md; "
        "there is nothing to install, so a descriptor would be theatre."),
    "anthropic-cybersecurity-skills": (
        "SKILL",
        "Apache-2.0 at the root and in all 25 inspected skill subtrees - "
        "consistent, no nested trap. Defensive content only, and must stay "
        "behind the authorised-scope policy that governs the security family."),
    "scientific-agent-skills": (
        "SKILL",
        "MIT at the root, but skills/docx, skills/pdf, skills/pptx and "
        "skills/xlsx carry Anthropic proprietary terms that forbid extraction, "
        "copying and derivative works. Those four are NOT vendorable. Any "
        "skill adapter must allowlist the MIT skills and exclude those."),
    "munder-difflin": (
        "REFERENCE_ONLY",
        "MIT source. The value is architectural - mailbox, worker routing, PTY "
        "workers, task ledger - and Friday already owns the layer above it in "
        "HermesAgency. Read for patterns; do not import. Bundled illustration "
        "assets are MIT with an attribution request, and are irrelevant to "
        "Friday either way."),
    "open-design": (
        "SKILL",
        "Apache-2.0, a design library (162 skills, 153 design systems, 114 "
        "templates incl. html slide decks) - Claude-Design-style mock-up, "
        "presentation and UI generation. Wired as a fabric SKILL provider "
        "(presentation family, beside diagram_design): Friday reads the design "
        "method and generates the artifact with workbench_write (its own model) "
        "or hermes_delegate. Its own agent-runner product is not run (a second "
        "control layer). 35 templates carry their own licence; the adapter "
        "gates each and fails closed."),
    "prompt-master": (
        "SKILL",
        "MIT, a Claude Code skill (SKILL.md + references/) that turns a rough "
        "idea into a paste-ready prompt for a named AI tool - a capability "
        "Friday lacked. Wired as a fabric SKILL provider (writing family, "
        "beside no_ai_slop) and installed as a native Claude Code skill. "
        "Markdown only; no code runs."),
    "openwork": (
        "REFERENCE_ONLY",
        "MIT core, but ee/ is the OpenWork Enterprise Edition licence: "
        "source-available, production use gated on a paid subscription above "
        "five users. ee/ is not vendorable. The core's search_capabilities / "
        "execute_capability MCP model is worth comparing against fabric.py, "
        "which already provides discovery, routing and execution - so this is "
        "a comparison, not a second fabric."),
    "openmausbot": (
        "SKILL",
        "Apache-2.0 at the root (relicensed from MIT per NOTICE); enterprise/ "
        "carries its own all-rights-reserved licence and is excluded. An "
        "Electron chat app that hosts a roster of Claude/Codex bots - a "
        "second control layer (NON_NEGOTIABLE 1), so the app is never run. "
        "Its two skills (phone-harness for authorised-USB Android control, "
        "create-verification-skill for writing launch/verify recipes) are "
        "read on demand as a SKILL pack."),
    "medusa": (
        "SIDECAR",
        "MIT core; ENTERPRISE-LICENSE.md carves out enterprise materials, "
        "which are never touched. Medusa v2 is the product-trading backend "
        "behind the commerce family: friday/fabric_adapters/medusa_commerce.py "
        "is an HTTP client to a store the operator runs (MEDUSA_BACKEND_URL) "
        "over /admin/* with secret-key Basic auth. The clone excludes www/ "
        "(docs) by sparse checkout because its paths exceed Windows MAX_PATH. "
        "Payments and refunds are not operations."),
    "smartstore": (
        "SIDECAR",
        "AGPL-3.0, so the fabric refuses any importing mode. "
        "friday/fabric_adapters/smartstore_commerce.py is an HTTP client to "
        "the operator's store over its OData v4 Web API with "
        "PublicKey:SecretKey Basic auth; no upstream code is linked. It is "
        "the commerce family's fallback behind medusa_commerce."),
    "agents-team": (
        "REFERENCE_ONLY",
        "MIT. Ships a Claude Code plugin (agents/, lib/, scaffold.py, "
        "lint.py) that generates and grades a team of agent files -- a "
        "second control layer (NON_NEGOTIABLE 1), so the plugin itself is "
        "never run by Friday. Its templates/rules/skills are plain "
        "markdown, no different from agency-agents' recipes; see "
        "UPSTREAM_LOCK.json's revision to SKILL, implemented as "
        "friday/fabric_adapters/agents_team_pack.py (roles family)."),
    "awesome-claude-code-subagents": (
        "SKILL",
        "MIT (VoltAgent). 158 Claude Code subagent briefs across ten "
        "categories, each frontmatter-only (name, description, tools, "
        "model) -- markdown with no code and no generator, the same shape "
        "as role_recipes one upstream later. Implemented as "
        "friday/fabric_adapters/claude_subagents.py (roles family): "
        "catalogue/search/category are names, recipe reads one brief."),
}


def identify(text: str) -> str:
    for label, pattern in SIGNATURES:
        if re.search(pattern, text, re.IGNORECASE):
            return label
    return "UNIDENTIFIED"


def git(clone: pathlib.Path, *arguments: str) -> str:
    result = subprocess.run(["git", "-C", str(clone), *arguments],
                            capture_output=True, text=True, timeout=120)
    return result.stdout.strip() if result.returncode == 0 else ""


def read_head(path: pathlib.Path, limit: int = 4000) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")[:limit]
    except OSError:
        return ""


def audit_one(name: str, url: str) -> dict:
    clone = UPSTREAM / name
    row: dict = {"repository": url, "directory": f"third_party/upstream/{name}"}
    if not clone.is_dir():
        return {**row, "status": "NOT_CLONED"}

    row["commit"] = git(clone, "rev-parse", "HEAD")
    row["default_branch"] = (
        git(clone, "symbolic-ref", "--short", "HEAD")
        or git(clone, "rev-parse", "--abbrev-ref", "HEAD"))
    row["describe"] = git(clone, "describe", "--tags", "--always")
    row["latest_tag"] = git(clone, "describe", "--tags", "--abbrev=0")
    row["remote"] = git(clone, "config", "--get", "remote.origin.url")
    row["last_commit_date"] = git(clone, "log", "-1", "--format=%cI")

    label, filename = "NONE", ""
    for candidate in LICENSE_NAMES:
        if (clone / candidate).is_file():
            label = identify(read_head(clone / candidate))
            filename = candidate
            break
    row["license"] = label
    row["license_file"] = filename

    nested, separate = [], []
    for candidate in clone.rglob("*"):
        if not candidate.is_file():
            continue
        if candidate.name.split(".")[0].upper() not in {
                "LICENSE", "LICENCE", "COPYING", "NOTICE"}:
            continue
        if candidate.suffix.lower() in CODE_SUFFIXES:
            continue
        relative = candidate.relative_to(clone).as_posix()
        if candidate.parent == clone or NOISE.search("/" + relative):
            continue
        entry = {"path": relative, "license": identify(read_head(candidate))}
        nested.append(entry)
        if SEPARATE_TREES.search(relative.rsplit("/", 1)[0]):
            separate.append(entry)
    row["nested_licenses"] = sorted(nested, key=lambda e: e["path"])[:25]
    row["separately_licensed_subtrees"] = sorted(
        separate, key=lambda e: e["path"])[:25]

    row["manifests"] = sorted(m for m in MANIFESTS if (clone / m).is_file())
    row["has_mcp_reference"] = bool(list(clone.glob("**/.mcp.json"))) or any(
        "mcp" in p.name.lower() for p in clone.rglob("*.py") if p.is_file()
        and not NOISE.search("/" + p.relative_to(clone).as_posix()))
    row["readme_bytes"] = (clone / "README.md").stat().st_size if (
        clone / "README.md").is_file() else 0
    row["security_doc"] = (clone / "SECURITY.md").is_file()
    row["tracked_files"] = len(git(clone, "ls-files").splitlines())

    mode, why = DECISIONS.get(name, ("", "no decision recorded"))
    row["proposed_mode"] = mode
    row["reason"] = why
    # Copyleft may never be proposed in a mode that links it into Friday.
    # Asserted here so a careless edit to DECISIONS fails the audit rather
    # than the build.
    if row["license"] in ("AGPL-3.0", "GPL-3.0") and mode in ("ADAPTER",
                                                              "BUILTIN",
                                                              "DIRECT_LIBRARY"):
        raise SystemExit(f"{name}: {row['license']} cannot be {mode}")
    blockers = [f"{e['path']} is {e['license']}"
                for e in row["separately_licensed_subtrees"]
                if e["license"] in ("Anthropic-proprietary",
                                    "SOURCE_AVAILABLE_EE", "CC-BY-NC")]
    row["vendoring_blockers"] = blockers
    row["status"] = "AUDITED_NOT_INTEGRATED"
    return row


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", action="store_true")
    parser.add_argument("--audit", action="store_true")
    arguments = parser.parse_args()

    new, have, unrequested = difference()

    print(f"requested        : {len(REQUESTED)}")
    print(f"in build pack    : {len(build_pack() & {slug(u) for u in REQUESTED})}")
    print(f"staged on disk   : {len(have)}")
    print(f"new to the pack  : {len(new)}")
    if unrequested:
        print(f"staged but not requested: {sorted(unrequested)}")
    if len(new) != EXPECTED_NEW:
        print(f"\nWARNING: expected {EXPECTED_NEW} new, computed "
              f"{len(new)}. Check ALIASES.", file=sys.stderr)

    if arguments.plan:
        print()
        for url in new:
            print(f'git clone "{url}" "third_party/upstream/{slug(url)}"')
        return 0

    if arguments.audit:
        audited = {slug(url): audit_one(slug(url), url) for url in new}
        OUT.parent.mkdir(parents=True, exist_ok=True)
        OUT.write_text(json.dumps(audited, indent=2, sort_keys=True) + "\n",
                       encoding="utf-8")
        done = [n for n, r in audited.items() if r["status"] == "AUDITED_NOT_INTEGRATED"]
        print(f"\nwrote {OUT} ({len(done)}/{len(audited)} cloned)")
        for name, row in audited.items():
            if row["status"] != "AUDITED_NOT_INTEGRATED":
                print(f"  {name:32} NOT CLONED")
                continue
            flag = ""
            if row["separately_licensed_subtrees"]:
                flag = f"  <-- {len(row['separately_licensed_subtrees'])} separate subtree(s)"
            print(f"  {name:32} {row['commit'][:12]}  {row['license']:16}{flag}")
        return 0

    parser.print_help()
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
