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
)

#: The build pack named two directories differently from their repositories.
#: Spelled out rather than pattern-matched, because a rule that strips "-app"
#: or removes hyphens would also silently merge two genuinely distinct repos.
ALIASES = {"anything-llm": "anythingllm", "postiz-app": "postiz"}

#: How many the operator and the build pack agree should be left over. The
#: script asserts this rather than trusting the arithmetic.
EXPECTED_NEW = 10


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
