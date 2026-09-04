#!/usr/bin/env python3
"""
Generate third_party/UPSTREAM_LOCK.json from the pinned clones themselves.

Run:  .venv/Scripts/python.exe scripts/upstream_lock.py
      .venv/Scripts/python.exe scripts/upstream_lock.py --check

NON_NEGOTIABLE 6 says every upstream is untrusted until audited and pinned,
and `fabric.Provider` enforces the pin half of that at construction. This
script is the other half: it reads the commit, the tag and the actual LICENSE
text out of each clone under `third_party/upstream/`, so the lock is derived
evidence rather than a table somebody typed once and stopped updating.

Two things it does that a hand-written table does not:

**It reads the license from the clone, not from a policy document.**
`00_governance/LICENSE_AND_DISTRIBUTION_POLICY.md` is guidance and says so
itself - "the executing agent must re-read the LICENSE from the pinned local
clone". Doing that caught a real error: the policy lists AnythingLLM as MIT,
and the clone carries an AGPL-3.0 licence inside `open-computer/`.

**It scans for nested licenses.** A repository-level MIT does not make every
vendored subdirectory MIT. `enterprise/`, `ee/`, bundled skills and vendored
grammars each carry their own terms, and those are exactly the ones that get
missed - so they are recorded per-clone instead of trusted per-repo.

`--check` re-derives everything and exits non-zero if the file on disk has
drifted from the clones, which is the form CI wants.
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
LOCK = ROOT / "third_party" / "UPSTREAM_LOCK.json"
TEMPLATE = (ROOT / "Friday Stark Demo Main" / "06_schemas"
            / "UPSTREAM_LOCK_TEMPLATE.json")

#: Filenames that carry license text, in the order we prefer them.
LICENSE_NAMES = ("LICENSE", "LICENSE.md", "LICENSE.txt", "COPYING", "LICENCE")

#: License text -> SPDX-ish label. Ordered: the AGPL preamble also contains
#: "GNU GENERAL PUBLIC LICENSE", so the more specific pattern must win.
SIGNATURES = (
    ("AGPL-3.0", r"GNU AFFERO GENERAL PUBLIC LICENSE"),
    ("GPL-3.0", r"GNU GENERAL PUBLIC LICENSE"),
    ("Apache-2.0", r"Apache License"),
    ("BSD-3-Clause", r"BSD 3-Clause"),
    ("BSD-2-Clause", r"BSD 2-Clause"),
    ("PolyForm", r"PolyForm"),
    ("MPL-2.0", r"Mozilla Public License"),
    # Last: the MIT grant text, which several other licenses do not contain.
    ("MIT", r"Permission is hereby granted, free of charge"),
)

#: Directories whose own license is noise rather than signal - vendored
#: parser grammars and CI config, not a separately licensed component.
NESTED_NOISE = re.compile(r"(node_modules|\.git|/vendored/|\.github)")


def _git(clone: pathlib.Path, *arguments: str) -> str:
    result = subprocess.run(["git", "-C", str(clone), *arguments],
                            capture_output=True, text=True, timeout=60)
    return result.stdout.strip() if result.returncode == 0 else ""


def identify(text: str) -> str:
    for label, pattern in SIGNATURES:
        if re.search(pattern, text, re.IGNORECASE):
            return label
    return "UNIDENTIFIED"


def license_of(path: pathlib.Path) -> tuple[str, str]:
    """(label, filename) for a directory's own license file."""
    for name in LICENSE_NAMES:
        candidate = path / name
        if candidate.is_file():
            head = candidate.read_text(encoding="utf-8", errors="replace")[:4000]
            return identify(head), name
    return "NONE", ""


def nested_licenses(clone: pathlib.Path) -> list[dict]:
    """Separately licensed subdirectories, which are the ones that bite."""
    found = []
    for candidate in clone.rglob("LICENSE*"):
        if not candidate.is_file() or candidate.parent == clone:
            continue
        relative = candidate.relative_to(clone).as_posix()
        if NESTED_NOISE.search("/" + relative):
            continue
        head = candidate.read_text(encoding="utf-8", errors="replace")[:4000]
        found.append({"path": relative, "license": identify(head)})
    return sorted(found, key=lambda row: row["path"])[:20]


#: The audit output for upstreams that postdate the build pack's template.
NEW_SET = ROOT / "docs" / "integrations" / "NEW_UPSTREAM_SET.json"

#: Where implementation found the template's proposed mode to be wrong.
#:
#: The template recorded an intent written before anyone had read Friday's
#: own source. Slice work sometimes contradicts it, and the honest record is
#: the revision plus the evidence - not a quietly edited template.
REVISED = {
    "browser-use": (
        "REFERENCE_ONLY",
        "Wholly duplicative. friday/toolsets/web.py:429 already starts "
        "Playwright, and browser_automate already drives it with Gemini's "
        "computer-use model, gated on sensitive_domains.refusal() at every "
        "entry point. Installing browser-use would add a second browser "
        "driver (cdp-use) and a second reasoning loop (its own openai, "
        "anthropic, groq and ollama clients) - NON_NEGOTIABLE 11 twice over - "
        "plus posthog telemetry and exact pins (httpx==0.28.1, "
        "pydantic==2.12.5) that collide with Friday's. It would also weaken a "
        "deliberate safety property: Friday halts and returns PARTIAL when the "
        "computer-use model raises require_confirmation, where its donor "
        "auto-acknowledged. Worth borrowing as a pattern, not an install: "
        "semantic element refs from the accessibility tree, which Friday's "
        "screenshot-and-coordinate loop does not have."),
    "openhands": (
        "REFERENCE_ONLY",
        "The brief describes an SDK, a CLI and an agent-server. None of that "
        "is at the pinned commit. `OpenHands/openhands` at d104ffdc33e7 is "
        "`@openhands/agent-canvas` 1.15.0 - an Electron application whose own "
        "README calls it 'the self-hosted developer control center for coding "
        "agents', running OpenHands, Claude Code, Codex, Gemini or any "
        "ACP-compatible agent. There is no Python package and no headless "
        "server to drive. It is not a worker Friday could hand a task to; it "
        "is a competing control layer, and NON_NEGOTIABLE 1 says Friday is "
        "the single user-facing control layer. It also drives Claude Code, "
        "which friday/executors/claude_code.py already drives directly. The "
        "PolyForm `enterprise/` concern the brief raises does not arise "
        "either - that directory does not exist at this commit. Worth noting "
        "as a pattern: ACP as a common protocol for talking to coding agents, "
        "which is the shape executor_router already approximates."),
    "cline": (
        "OPTIONAL_WORKER",
        "@cline/cli 3.0.60, Apache-2.0, a genuine headless coding CLI with "
        "its own worktrees and checkpoints. Declared in "
        "friday/executor_router.KNOWN for discovery, deliberately with "
        "buildable=False and no builder, which is the rule this codebase "
        "already applies to opencode and codex: it is not installed on this "
        "machine, and untestable code is worse than absent code. When it is "
        "installed, choose() will still not pick it until "
        "friday.evaluation has enough decided attempts to prefer it over "
        "Claude Code on measurement rather than novelty. Hermes remains "
        "mandatory for serious agentic execution per NON_NEGOTIABLE 2; this "
        "is an additional executor, never a replacement."),
    "open-notebook": (
        "SIDECAR",
        "Built 2026-09-03 as a remote HTTP helper (fabric_adapters/"
        "open_notebook_research.py, family research) against an instance the "
        "owner runs at OPEN_NOTEBOOK_URL: notebooks, notebook and ask are open "
        "reads, add_source is a write kept out of the spoken surface. Friday does "
        "not host it (no Docker here) and stores none of its answers as facts, so "
        "GBrain stays the one memory; the 2026-08 verdict that its pipeline "
        "duplicates toolsets/research.py still holds for the crawl half - what is "
        "used is the notebook the owner already keeps there, asked by voice. "
        "Honest 'unreachable, set OPEN_NOTEBOOK_URL' until an instance exists."),
    "anythingllm": (
        "SIDECAR",
        "Built 2026-09-03 as a remote HTTP helper (fabric_adapters/"
        "anythingllm_research.py, family research): Friday asks an instance the "
        "owner runs at ANYTHINGLLM_URL - workspaces, ask, documents, all reads - "
        "and says 'unreachable, set ANYTHINGLLM_URL' until one exists. It is a "
        "helper that answers questions over the owner's own document workspaces, "
        "not a second memory: nothing it returns is stored as a Friday fact "
        "(NON_NEGOTIABLE 11 holds). Earlier verdict (REFERENCE_ONLY, 2026-08) "
        "stands on the licence point: the root is MIT but open-computer/ is "
        "AGPL-3.0, so only the HTTP surface is used and nothing is imported."),
    "agent-reach": (
        "CLI",
        "First revised to REFERENCE_ONLY (2026-08): its capability routing is "
        "what fabric.py already is, `skill` rewrites the host agent's config, "
        "and eight of fifteen channels need per-site cookies. That note ended "
        "'worth revisiting narrowly for `transcribe` (Whisper via Groq/OpenAI), "
        "a clean verb and a capability Friday genuinely lacks' - and this is "
        "that revisit (2026-09-02): friday/fabric_adapters/"
        "agent_reach_transcribe.py exposes `transcribe` and `doctor` ONLY, as "
        "a subprocess of the clone's own venv, with the Groq key delivered as "
        "environment through the secret broker. setup/install/skill/configure "
        "are not reachable."),
    "vane": (
        "REFERENCE_ONLY",
        "A Next.js application with its own container, database and SearxNG "
        "backend, offering search plus answer generation. Friday already has "
        "web_search, web_answer, web_news and web_deep_research, and the "
        "brief warns in its own words to avoid duplicating answer-generation "
        "model calls when Friday can synthesise. Standing up an application "
        "to replace capabilities Friday has is not proportionate. The pattern "
        "worth taking is SearxNG-backed metasearch: Friday's web_search "
        "currently regex-scrapes DuckDuckGo result HTML, which is fragile by "
        "construction. That is a change to Friday's own search, not an "
        "install."),
    "pipecat": (
        "REFERENCE_ONLY",
        "A voice/multimodal framework, and Friday's voice transport is LiveKit "
        "- agent_friday.py is a LiveKit AgentSession with Sarvam STT, Gemini "
        "LLM and OpenAI TTS, live-tested end to end. The brief says do not "
        "replace LiveKit without evidence, and there is none: swapping the "
        "whole voice path for a second framework is not an integration, it is "
        "a rewrite. Worth reading for its pipeline processors, interrupt "
        "handling and turn-detection patterns; a specific processor could "
        "later be wrapped as an adapter if it beats the current path on a "
        "measured turn. BSD-2-Clause, so that stays open."),
    "openmontage": (
        "SIDECAR",
        "AGPL-3.0, so the fabric refuses any importing mode. Built 2026-09-03 as "
        "a remote HTTP helper (fabric_adapters/openmontage_media.py, family "
        "media): at the pin its Backlot server (backlot/server.py, FastAPI, port "
        "4750) exposes a small project-board API - projects and project {id}, "
        "both reads, no auth, no write route - which Friday asks at "
        "OPENMONTAGE_URL. The render pipeline itself is not driven. Its own "
        ".claude/skills carry separate manim licences (recorded in the "
        "nested-licence scan). Honest 'unreachable, set OPENMONTAGE_URL' until "
        "the owner runs it."),
    "postiz": (
        "SIDECAR",
        "AGPL-3.0, isolated by the same rule. Built 2026-09-03 as a remote HTTP "
        "helper (fabric_adapters/postiz_social.py, family social) against an "
        "instance the owner runs at POSTIZ_API_URL with the postiz_api_key "
        "secret: integrations, queue and status are open reads; schedule is a "
        "write behind the social.publish permission, so NON_NEGOTIABLE 13's "
        "human confirmation before an external publish still applies (answered "
        "in advance only by the owner's own full-autonomy switch). Honest "
        "'unreachable, set POSTIZ_API_URL' until an instance exists."),
    "strix": (
        "CLI",
        "strix-agent 1.5.3, 'Open-source AI Hackers for your apps' - a full "
        "autonomous security agent with its own LLM loop (openai-agents, "
        "litellm) and a Docker runtime. First revised to REFERENCE_ONLY on "
        "2026-08 because the only executing modes imported code into Friday's "
        "process, which would have been a second agent brain (NON_NEGOTIABLE "
        "11); the note asked for 'a future restricted, authorised sidecar with "
        "its own scope policy'. That is what FABRIC-CLI-01 now provides: "
        "revised again 2026-09-02 to CLI as friday/fabric_adapters/"
        "strix_pentest.py - a one-shot subprocess (process boundary, no "
        "import, no shared model loop), gated by security.authorized_scope "
        "before activation, with --target/--instruction each a single argv "
        "element. security_skills still covers the knowledge side."),
    "openworker": (
        "CLI",
        "MIT, `openworker <skill> --cwd <dir>` is a genuine headless coding "
        "coworker that runs one skill and exits. Registered 2026-09-02 as "
        "friday/fabric_adapters/openworker_cli.py in the `coding` family as an "
        "OPTIONAL worker beside Hermes, which remains the mandatory engine "
        "(NON_NEGOTIABLE 2). `run` needs coding.workspace_write; `plan` is "
        "read-only; the upstream's bypass-approvals mode is not exposed."),
    "crewai": (
        "REFERENCE_ONLY",
        "A multi-agent orchestration framework, and Friday already owns "
        "orchestration: planner.py, objectives.py, the ContinuousTaskExecutor "
        "and HermesAgency. The brief is explicit - do not use CrewAI as the "
        "global controller. Its crews-vs-flows distinction (exploratory "
        "collaboration vs deterministic workflow) is a useful lens for "
        "HermesAgency, but as code it would be a second orchestrator competing "
        "for the turn. MIT, so the patterns are free to read."),
    "agenticseek": (
        "CLI",
        "GPL-3.0, so an importing mode is refused outright - and until "
        "FABRIC-CLI-01 there was no isolated executing mode at all, which is "
        "why it was REFERENCE_ONLY. Revised 2026-09-02 to CLI as "
        "friday/fabric_adapters/agenticseek_cli.py: a subprocess is a process "
        "boundary, so the copyleft invariant holds by construction "
        "(Provider.__post_init__ still refuses ADAPTER for it). Gated behind "
        "orchestration.local_agent; it runs its own local model and browser."),
    "maxun": (
        "SIDECAR",
        "AGPL-3.0, so the fabric would refuse any importing mode regardless. "
        "Mode normalised from the template's ISOLATED_SIDECAR to the fabric's "
        "vocabulary. Built 2026-09-03 as a remote HTTP helper "
        "(fabric_adapters/maxun_scraping.py, family scraping): Friday asks an "
        "instance the owner runs at MAXUN_API_URL with the maxun_api_key secret "
        "- robots, runs and results are open reads, run_robot is a write behind "
        "scraping.run. Friday still does not host its five services or its "
        "browser (the one-browser rule stands; Scrapling covers one-shot "
        "extraction); the robots the owner already keeps there become askable. "
        "Honest 'unreachable, set MAXUN_API_URL' until an instance exists."),
    "auto-company": (
        "SKILL",
        "Demoted to REFERENCE_ONLY on 2026-08-31 as 'a whole control layer'. "
        "That is true of its runner and false of its content: fourteen "
        "executive playbooks and ~36 business skills are markdown, exactly "
        "what agency-agents already contributes as role_recipes. Promoted "
        "2026-09-02 to SKILL as friday/fabric_adapters/company_playbooks.py "
        "(roles family): one playbook or skill per call, no bulk read, the "
        "Claude Code agent loop never run. This is the HR/operations "
        "assistant pack the owner asked for."),
    "awesome-harness-engineering": (
        "SKILL",
        "'A reading list, not software' was the REFERENCE_ONLY reason, and it "
        "missed the four templates: AGENTS.md, PLAN.md, IMPLEMENT.md and "
        "HARNESS_CHECKLIST.md. Promoted 2026-09-02 to SKILL as friday/"
        "fabric_adapters/harness_templates.py (orchestration family) so the "
        "self-build loop can read the checklist as acceptance criteria. The "
        "reading list is not an operation. CC0-1.0."),
    "agents-team": (
        "SKILL",
        "REFERENCE_ONLY was the audit's first-pass read of the plugin as a "
        "whole (scaffold.py/lint.py is a second control layer, never run - "
        "NON_NEGOTIABLE 1). Revised 2026-09-03: the same audit that ruled "
        "out the generator found eight archetype templates, thirteen team "
        "rules and four SKILL.md briefs underneath it that are plain "
        "markdown, exactly what role_recipes already contributes. Promoted "
        "to SKILL as friday/fabric_adapters/agents_team_pack.py (roles "
        "family): archetype/rule/skill each read one file, allowlisted "
        "against a fixed tuple. MIT."),
    "awesome-claude-code-subagents": (
        "SKILL",
        "Confirmed SKILL on first audit: 158 Claude Code subagent briefs "
        "across ten categories (01-core-development .. 10-research-analysis), "
        "each frontmatter-only (name, description, tools, model) - no code, "
        "no generator, nothing this pack could run even by mistake. "
        "Implemented as friday/fabric_adapters/claude_subagents.py (roles "
        "family): catalogue/search/category return names only, recipe "
        "reads one brief allowlisted against the same index catalogue "
        "builds. MIT (VoltAgent)."),
}


def template_entries() -> dict:
    """
    The upstreams to lock: the build pack's 21 plus anything audited since.

    The lock is an audit record, so a repository that has been cloned, pinned
    and licence-reviewed belongs in it whether or not it predates the template.
    Nothing here implies installation.
    """
    entries = json.loads(TEMPLATE.read_text(encoding="utf-8"))
    if NEW_SET.is_file():
        for name, audited in json.loads(
                NEW_SET.read_text(encoding="utf-8")).items():
            entries[name] = {
                "url": audited.get("repository", ""),
                "commit": "FILL_AFTER_CLONE",
                "version": "DISCOVER",
                "license": audited.get("license", ""),
                "integration_mode": audited.get("proposed_mode", ""),
                "role": audited.get("reason", "")[:120],
                "upstream_tests": "NOT_RUN",
                "patches": [],
                "added_by": "new_upstream_set.py",
                "vendoring_blockers": audited.get("vendoring_blockers", []),
            }
    return entries


def registered_providers() -> dict:
    """Upstream name -> its descriptor, for anything that has an adapter."""
    try:
        sys.path.insert(0, str(ROOT))
        from friday import fabric
        return {p.upstream: p for p in fabric.registry().values() if p.upstream}
    except Exception:                                        # noqa: BLE001
        # The lock must still generate on a tree where Friday cannot import.
        return {}


def derive() -> dict:
    template = template_entries()
    implemented = registered_providers()
    locked: dict[str, dict] = {}

    for name, entry in template.items():
        clone = UPSTREAM / name
        row = dict(entry)
        if not clone.is_dir():
            row["commit"] = ""
            row["status"] = "NOT_CLONED"
            locked[name] = row
            continue

        declared = row.get("license", "")
        label, filename = license_of(clone)
        nested = nested_licenses(clone)

        row["commit"] = _git(clone, "rev-parse", "HEAD")
        row["version"] = _git(clone, "describe", "--tags", "--always")
        row["license_verified"] = label
        row["license_file"] = filename
        row["license_declared"] = declared
        row["nested_licenses"] = nested
        row["status"] = "CLONED"

        if name in REVISED:
            mode, why = REVISED[name]
            row["integration_mode_proposed"] = row.get("integration_mode", "")
            row["integration_mode"] = mode
            row["revision_reason"] = why

        # An upstream that actually has an adapter states its own mode in the
        # descriptor, in the fabric's vocabulary. Reading it back beats
        # keeping the template's intent ("CORE_WEB_ADAPTER") next to a
        # descriptor that says ADAPTER and letting the two drift.
        if name in implemented:
            row.setdefault("integration_mode_proposed",
                           row.get("integration_mode", ""))
            row["integration_mode"] = implemented[name].integration_mode
            row["integration_mode_source"] = "descriptor"
            row["provider_id"] = implemented[name].id

        # The two disagreements worth surfacing loudly rather than storing.
        copyleft_nested = [n for n in nested
                           if n["license"] in ("AGPL-3.0", "GPL-3.0")]
        if copyleft_nested and label not in ("AGPL-3.0", "GPL-3.0"):
            row["license_warning"] = (
                f"root is {label} but {len(copyleft_nested)} nested "
                f"component(s) are copyleft: "
                + ", ".join(n["path"] for n in copyleft_nested))
        locked[name] = row

    return locked


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true",
                        help="fail if the lock has drifted from the clones")
    arguments = parser.parse_args()

    derived = derive()
    rendered = json.dumps(derived, indent=2, sort_keys=True) + "\n"

    if arguments.check:
        if not LOCK.exists():
            print(f"missing {LOCK}")
            return 1
        if LOCK.read_text(encoding="utf-8") != rendered:
            print(f"{LOCK} has drifted from the clones; re-run without --check")
            return 1
        print(f"{LOCK} matches the clones")
        return 0

    LOCK.write_text(rendered, encoding="utf-8")
    cloned = [n for n, r in derived.items() if r["status"] == "CLONED"]
    warned = {n: r["license_warning"] for n, r in derived.items()
              if r.get("license_warning")}
    print(f"wrote {LOCK}  ({len(cloned)} cloned, {len(derived)} total)")
    for name, warning in warned.items():
        print(f"  LICENSE WARNING  {name}: {warning}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
