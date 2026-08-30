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
        "REFERENCE_ONLY",
        "A NotebookLM-style research application, v1.14.0, needing SurrealDB "
        "and its own container. Friday already has both halves it would "
        "provide. The pipeline: toolsets/research.py does crawl_all, dedupe, "
        "relevance and build_corpus against a token budget, behind "
        "web_answer, web_crawl and web_deep_research. The workspace: "
        "memory_remember/recall/search store facts with provenance, and "
        "project_record_decision, project_context, projects_list and "
        "project_resume already give a durable per-project surface - which is "
        "what a notebook is. Standing up a database and an application to "
        "restate that is not proportionate, and the brief says plainly not to "
        "replace GBrain. The pattern worth keeping is its explicit "
        "source/note/citation vocabulary, which is clearer than Friday's "
        "fact-with-provenance and could shape the research store."),
    "anythingllm": (
        "REFERENCE_ONLY",
        "The sequence admits this one only if measurable capability remains "
        "after Open Notebook, and none does: research pipeline, corpus "
        "building, durable project memory and workspace isolation are all "
        "present, so a second document/RAG application would duplicate "
        "storage and answer generation both. Licensing independently argues "
        "for caution - the root is MIT but open-computer/ is AGPL-3.0, which "
        "the build pack's own policy misses, so any future use must treat "
        "that subtree as copyleft regardless of the root."),
    "agent-reach": (
        "REFERENCE_ONLY",
        "The pattern is already built. The reason to want Agent-Reach was its "
        "capability routing - platform, preferred backend, fallback, doctor - "
        "and friday/fabric.py already is that: families, candidates(), "
        "select(), declared fallbacks, health() and report(). Meanwhile its "
        "CLI exposes no search, read or fetch verb at all; the subcommands are "
        "setup, install, configure, doctor, uninstall, skill, format, "
        "transcribe, check-update, watch and version. Retrieval reaches an "
        "agent by `skill` registration writing skill files into the host "
        "agent's config - the same surprise Friday refuses for `graft init` "
        "and codebase-memory `install`. Eight of its fifteen channels need "
        "per-platform cookies or tokens, several for sites whose terms forbid "
        "automated access. Worth revisiting narrowly for `transcribe` (Whisper "
        "via Groq/OpenAI), which is a clean verb and a capability Friday "
        "genuinely lacks."),
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
        "AGPL-3.0, so the fabric refuses any importing mode. A media/video "
        "production studio; deferred rather than built because it is a heavy "
        "service and Friday has no media pipeline for it to slot beside yet. "
        "Its own .claude/skills carry separate manim licences (recorded in the "
        "nested-licence scan). Revisit when a real video-production objective "
        "exists to justify the sidecar."),
    "postiz": (
        "SIDECAR",
        "AGPL-3.0, isolated by the same rule. A full social-scheduling "
        "application with its own database and worker. Deferred: Friday has no "
        "social-publishing objective today, and NON_NEGOTIABLE 13 already "
        "requires human confirmation before any external publish, so the value "
        "of a scheduler is bounded until there is content to schedule. Revisit "
        "as an isolated service if social publishing becomes a real need."),
    "strix": (
        "REFERENCE_ONLY",
        "strix-agent 1.5.3, 'Open-source AI Hackers for your apps' - a full "
        "autonomous security agent with its own LLM loop (openai-agents, "
        "litellm) and a Docker runtime. Friday genuinely lacks active security "
        "scanning, which is the one place a remaining upstream addresses a real "
        "gap - but installing Strix is a second agent brain and a second model "
        "loop, the NON_NEGOTIABLE 11 problem browser-use and OpenHands already "
        "hit. security_skills (implemented, Apache-2.0, scope-gated) covers the "
        "knowledge side. Active scanning belongs in a future restricted, "
        "authorised sidecar with its own scope policy, not an in-process "
        "adapter - so this is REFERENCE_ONLY now, with the gap named."),
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
        "REFERENCE_ONLY",
        "GPL-3.0, so an importing mode is refused outright. A local-first "
        "autonomous assistant; its value is the local-model, zero-API-key "
        "pattern for a privacy-sensitive user, which is a configuration "
        "question for Friday's own provider selection rather than a component "
        "to vendor. If a local worker is ever wanted it belongs behind the "
        "executor_router as an OPTIONAL_WORKER like Cline, not copied in."),
    "maxun": (
        "SIDECAR",
        "AGPL-3.0, so the fabric would refuse any importing mode regardless. "
        "Mode normalised from the template's ISOLATED_SIDECAR to the fabric's "
        "vocabulary. Deferred rather than built: docker-compose stands up "
        "postgres, minio, a backend, a frontend and its own browser service - "
        "a third browser alongside Friday's Playwright and anything else - "
        "for scheduled scraping robots. Revisit when a real recurring "
        "extraction job exists to justify five services; Scrapling covers "
        "one-shot structured extraction today."),
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
